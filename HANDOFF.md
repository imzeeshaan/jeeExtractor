# JEE Extractor — Handoff / Context Document

**Purpose of this doc**: this is a context dump for another AI/engineer joining this project. It covers what exists, exactly how it works, what's been verified, what's broken/out of scope, and the architecture we're currently discussing for the next phase (a template-based extraction system). Read this fully before proposing changes — several "obvious" bugs described below were already found and fixed this session, with the specific evidence that proved them.

---

## 0. Where this lives (important — don't confuse with a sibling project)

Working directory: `/Users/asmadzeeshankhan/Projects/Projects/RnD/HealthCare/` — **not a git repo**.

This directory actually contains two *unrelated* projects side by side:

1. **MediBill Analytics** (root-level files: `index.html`, `command.html`, `data.js`, `commandData.js`, `lib/vendor/`, `start.sh`, `PLAN.md`, `README.md`) — a static healthcare billing dashboard (Chart.js + Three.js). **Not relevant to this handoff.** Ignore it.
2. **`jee-extractor-app/`** — the actual subject of this document. Everything below refers to this subdirectory.

---

## 1. What the app does

A Streamlit POC that takes a JEE (Indian engineering entrance exam) question-paper PDF and extracts every question into structured data: the question stem (text + cropped image), its 4 multiple-choice options (text + cropped image + any embedded diagram), and the matching answer-key value — then lets a human page through them with selectable checkboxes and export everything (JSON + Markdown + all image crops) as a zip.

It is explicitly a POC, not a production pipeline. The parsing approach is **deterministic, geometry-based PDF text/layout parsing** (via PyMuPDF) — there is no OCR and no AI/LLM in the extraction path today.

---

## 2. File structure

```
jee-extractor-app/
├── app.py                 Streamlit UI — upload/select a PDF, preview pages, trigger
│                           extraction, render results, handle export/download
├── extractor.py            All PDF-parsing logic. This is the file that matters.
├── requirements.txt         streamlit, pymupdf, pillow
├── .gitignore
├── sample_data/             5 bundled JEE Main 2012 sample PDFs (see §4)
│   ├── sample_jee_2012_may7.pdf
│   ├── sample_jee_2012_may12.pdf
│   ├── sample_jee_2012_may19.pdf
│   ├── sample_jee_2012_may26.pdf
│   └── sample_jee_2012_offline.pdf
└── HANDOFF.md               this file
```

No build step. Run with `streamlit run app.py` from inside `jee-extractor-app/`.

---

## 3. Data model — the output shape

`parse_pdf(pdf_path, out_dir) -> (questions, notes)`

```jsonc
{
  "question_number": 12,
  "question_type": "mcq" | "numerical",   // added this session, see §6.3
  "stem_text": "Q12. ...",                 // full stem text, reassembled across a page break if needed
  "stem_snippet": "images/q12_stem.png",   // visual crop of the stem region (source of truth for the UI)
  "stem_images": ["images/q12_stem_diagram_1.png", ...],  // isolated embedded diagrams found in the stem
  "options": [                             // empty list if question_type == "numerical"
    {"label": "1", "text": "(1) 5.32 cm", "snippet": "images/q12_opt1.png", "images": [...]},
    ... "2", "3", "4"
  ],
  "answer": "2"   // or "10", "-2.7", "23.03" for numerical questions — string, may be multi-char, may be None
}
```

`notes` is a flat list of human-readable strings logged during parsing — page-wrap events, missing markers, image-count mismatches, numerical-question detections. Surfaced in the UI as a collapsible panel. This is the app's only current "diagnostics" mechanism — see §7 for why that's insufficient going forward.

---

## 4. The source documents — what's actually in `~/Downloads/jeeqs/2012/`

The user pointed me at a Downloads folder with question-paper PDFs to test against. It turned out to hold **10 distinct PDFs**, not the 5 originally bundled as `sample_data/`. Full breakdown (this classification is the key finding of this session — read it before assuming "JEE PDF" means one format):

| File | Format family | Status |
|---|---|---|
| `sample_jee_2012_may7/12/19/26/offline.pdf` (5 files) | JEE Main 2012, MathonGo export | ✅ Fully supported — 90/90 questions, 100% answers, 100% images, on every paper |
| `JEE Main 2019 (09 Jan Shift 2) MathonGo.pdf` | JEE Main 2019, same MathonGo export style | ✅ Fully supported — same 90/90 clean result |
| `JEE Main 2019 (12 Jan Shift 2) MathonGo.pdf` | same | ✅ Fully supported |
| `JEE Main 2020 (07 Jan Shift 1)MathonGo.pdf` | JEE Main 2020, same export style **but introduces a new question type** | ✅ Fully supported *after this session's fixes* — see §6.3 |
| `JEE ADV 2007-1.pdf` | JEE **Advanced** (different exam, not JEE Main) | ❌ **Not supported** — scanned/image PDF, effectively no text layer (only ~487 chars of junk text like repeated "Answer" across 30 pages) |
| `JEE ADV 2016-1.pdf` | JEE Advanced | ❌ **Not supported** — zero extractable text across all 29 pages |

The two JEE Advanced files are a fundamentally different problem: no `Qn.` markers, no `(N)` option markers, nothing for a regex/geometry parser to find. They would need OCR or a vision-LLM approach — this is **out of scope for the current codebase** and was explicitly not attempted. Don't assume any "just fix the regex" approach will touch these; they have no text to regex against.

Note: several of the "new" PDFs downloaded to `jeeqs/2012` are byte-identical (MD5-verified) to the ones already in `sample_data/` — only the May-7 2012 paper is a different export of the same content (same 90 questions, same answer key, different file bytes). The 2019/2020/JEE-ADV files are genuinely new content not previously in `sample_data/`.

---

## 5. How `extractor.py` works — the core algorithm

This is the part worth understanding in depth, because the exam layout is a **print layout with no structural markup** — everything is inferred from raw text/image bounding-box positions on the page. There is no XML/HTML to walk.

### 5.1 Setup pass (`parse_pdf`, per-document)

1. **Find the answer key page**: scan every page's text for the literal string `"ANSWER KEY"` (case-insensitive). Everything from that page onward is excluded from question parsing.
2. **Parse the answer key**: regex `r'(\d+)\.\s*\(([^)]+)\)'` against that page's text → `{question_number: answer_string}`. The captured group is a *string*, not necessarily a single digit — see §6.4 for why this changed.
3. **Precompute per-page data** (`pages_info` dict, keyed by page index) so later steps can look ahead to the next page without re-parsing:
   - `q_markers`: every span matching `r'Q(\d+)\.$'` (i.e. a lone "Q12." token), sorted top-to-bottom by y-position. This is the anchor for everything — a question's content spans from its own marker's y down to the *next* marker's y (or to the page's footer/end).
   - `img_boxes`: every embedded raster image's bounding box (`page.get_images(full=True)` + `page.get_image_bbox()`).
   - `footer_y`: the y-position where a recurring ad line ("Join the Most Relevant Test Series...") starts, so it's never mistaken for question content.
   - `header_y` (`_header_bottom_y`): the y-position where the repeating page header/branding ("JEE Main 2012 ...", "Question Paper", "MathonGo", "... Previous Year Paper") ends. Added this session specifically to support stem-continuation detection (§6.2) without contaminating it with header text.

### 5.2 Per-question extraction (main loop in `parse_pdf`)

For each `Qn.` marker on a page (in order):

1. **Option marker detection** (`find_opt_markers`, a closure inside `parse_pdf`): finds every `(N)` occurrence **at the character level** (via `page.get_text("rawdict")`, walking individual glyph bounding boxes), not at the span level. This matters because a single text span can contain two markers back-to-back (e.g. a short option "(1)" immediately followed by "(2)" on the same line) — span-level matching would merge or miss them.
2. **Row/column reconstruction** (`_group_rows`, `_column_boundaries`): the 4 options are laid out in a 2-column grid, not sequential text. `_group_rows` clusters markers by y-proximity (±6pt tolerance) into rows, sorted left-to-right within each row. `_column_boundaries` then places a vertical split line just *before* the next marker's own x-position — not at the midpoint between two markers. This was a deliberate fix for two real bugs: midpoint-bisection cut through a wrapping word ("translational") and nearly touched a long "Statement 1..." option.
3. **Cross-page option wrapping**: if a question is the *last* one on its page and has zero option markers on that page, the code looks for `(N)` markers at the top of the *next* page (before that page's own first `Qn.` marker). If found, the question's options are built from that next page instead, and a note is logged.
4. **Cross-page STEM wrapping** (added this session — was previously a real bug, see §6.2): even when options wrap correctly, the *stem itself* can spill onto the next page (extra sentence + sometimes a diagram, before the wrapped options start). This is now detected and the stem crop/text/diagrams are stitched across both page fragments.
5. **Stem extraction**: crop the stem's rectangle (`_crop`, a straight `page.get_pixmap(dpi=200, clip=rect)` raster crop — this is why math formulas/diagrams render correctly in the snippet even though PyMuPDF's `get_text()` often can't extract the formula as text, see §5.3) and pull its text via `get_text("text", clip=rect)`. If there's a stem continuation on the next page, both fragments are stacked vertically into one PNG (`_stack_vertically`, via Pillow) and their texts concatenated.
6. **Diagram extraction**: any embedded raster image whose bounding box overlaps ≥50% with a rect (stem or option) — measured as intersection-area / image-area, `_image_in_rect` — is cropped out separately as its own file, in addition to being visible inside the parent snippet.
7. **Question-type detection** (added this session, §6.3): if *zero* `(N)` markers were found anywhere for a question (not "1-3 missing" — genuinely zero), it's classified `"numerical"` (a fill-in-the-blank numeric-answer question, the 2019+ JEE Main pattern) instead of `"mcq"`. No fake blank options are created; a single note is logged instead of 4 "marker not found" notes.
8. **Same-page image sanity check**: when options are on the same page as the stem (not wrapped), the code counts all embedded images physically inside the question's y-range and compares that to how many actually got assigned to stem/options. Mismatch → a note is logged. (This check does **not** run for the cross-page case — that path is validated differently, see §6.2's fix.)

### 5.3 Why raster crops matter more than extracted text here

A recurring theme: PyMuPDF's `get_text()` frequently returns **incomplete or spatially-scrambled text** for these PDFs, because math formulas/symbols are sometimes rendered as separate text runs positioned far from their visual context in the PDF's internal block order (though still at the *correct* y-coordinate — `get_text(clip=rect)` filters by bounding box, so this generally still works), and some diagrams (e.g. certain graphs) are drawn as **vector paths** (`page.get_drawings()`), not embedded raster images — meaning `page.get_images()` never sees them and they can't be isolated as a standalone diagram file. They **do** still show up correctly in the raster `_crop()` snippet, because that's a full-fidelity pixel render of the region, not a text/object extraction. **The snippet image is the actual source of truth the app displays and the user reviews — `stem_text`/`stem_images` are best-effort supplementary extraction, not guaranteed-complete.** Don't assume `stem_text` alone is reliable for anything downstream (e.g. don't build a "search questions by text" feature on top of it without knowing this).

---

## 6. Bugs found and fixed this session (with the evidence, so they aren't "rediscovered")

All of these were found by **actually running the extractor and inspecting output** (rendering crops as images, counting embedded images vs. assigned images across the whole document, diffing against hand-checked page renders) — not by reading the code. That method is worth repeating for any future change.

### 6.1 (Pre-existing, not touched) Cross-page option wrapping — confirmed correct
Verified pixel-for-pixel against raw page renders (May-7 Q11: fraction options `ℓ/√2π`, `ℓ/√3π` wrapped from page 2 to page 3) — this was already working correctly before this session.

### 6.2 Stem-continuation-across-page-break was silently truncating text and dropping diagrams (FIXED)
**Evidence**: compared total embedded raster images per document (`page.get_images()`) against total images actually assigned to any question's `stem_images`/option `images` — found exact-count mismatches in 3 of the 5 original papers (May-7: 13 vs 12; May-12: 24 vs 21; May-19: 20 vs 18). Traced every missing image to the same root cause: when a question's stem (not just its options) continues onto the next page, the old code only extended the *option* search to the next page — the stem crop/text was still built from the origin page alone. Concretely, May-19 Q16's extracted `stem_text` ended mid-sentence: `"...The electric flux through the curved surface of the"` — missing `"hemisphere is [diagram]"` entirely.

**Fix** (now in `extractor.py`): when options are found via the cross-page lookahead, additionally check the gap between the next page's header end (`_header_bottom_y`) and where the wrapped options start. If that gap contains real text or an image (checked explicitly — not just "the gap has nonzero height", since normal page top-margin also produces a nonzero gap with nothing in it), treat it as a stem continuation: stitch the crop (`_stack_vertically`) and concatenate the text.

**Verified after fix**: image-assigned count == total embedded image count, exactly, on **all 8 text-based papers** (13/13, 24/24, 20/20, 19/19, 26/26, 43/43, 66/66, 22/22).

### 6.3 Numerical Value questions (JEE Main 2020+) were misparsed as MCQ with 4 missing options (FIXED)
**Evidence**: `JEE Main 2020 (07 Jan Shift 1)` produced 60 "option marker not found" notes (15 questions × 4 labels) and only 63/75 answers matched. Manually confirmed via the answer-key page that questions 21-25, 46-49, 71-74 are genuinely a different question type — no `(1)(2)(3)(4)` at all, just a blank line for a numeric answer (`"...the kinetic energy of the particle (in J) is: ... ______________."`).

**Fix**: added `question_type` field. If zero option markers are found for a question (after the cross-page lookahead also finds nothing), it's classified `"numerical"` — `options: []`, single informational note instead of 4 false-alarm notes, stem crop covers the full question block (there's no separate "options region" to exclude). `app.py` updated to not render phantom checkboxes for these — shows a caption instead.

### 6.4 Answer-key regex silently dropped multi-digit/decimal/negative answers (FIXED)
**Evidence**: same 2020 paper's answer key contains entries like `21. (10)`, `46. (-2.7)`, `47. (10.6)`, `48. (23.03)` — Numerical Value answers. The old regex `r'(\d+)\.\s*\((\d)\)'` requires exactly one digit inside the parens, so all 12 non-single-digit answers in that paper silently returned `None` with no error/warning.

**Fix**: regex changed to `r'(\d+)\.\s*\(([^)]+)\)'`, capturing the full string inside the parens. `answer` field is now consistently a string (was already a string for MCQ, but now can be `"10"`, `"-2.7"`, etc. — **do not assume it's a single character or that `int(answer)` is safe** — it can be a decimal or negative value as text).

**Verified after fix**: 75/75 answers matched (was 63/75).

---

## 7. Known limitations (current state, as of this handoff)

1. **JEE Advanced papers are entirely unsupported** — no text layer, needs OCR or vision-LLM extraction. Not attempted. Would need a fundamentally different extraction path, not a fix to the existing one.
2. **`stem_text` is best-effort, not authoritative** — see §5.3. The visual crop is the real output; don't build features that assume the text field is complete or correctly ordered.
3. **Vector-drawn diagrams can't be isolated as standalone files** — they're visible in the parent snippet (raster crop) but never appear in `stem_images`/option `images`, because those lists only capture embedded raster images (`page.get_images()`). This wasn't flagged as a bug because the full snippet still shows them correctly — just noting it as an actual current gap in case someone wants a "download all diagrams separately" feature.
4. **Validation today is metrics-based only, and that's a known-insufficient safety net.** The image-count and answer-match checks in §6 are exact invariants (good), but they only prove internal consistency, not semantic correctness — e.g. a wrong-but-self-consistent parse could still pass every current check. The stem-truncation bug (§6.2) was *not* caught by any automated check originally — it was caught by a human reading an extracted sentence and noticing it ended mid-word. There is currently no automated "does this stem read like a complete sentence" check in the code. This is a real gap, discussed further in §8.
5. **Only tested against MathonGo-exported PDFs.** All 8 working papers share the same underlying export tool/branding (`"MathonGo"`, consistent header/footer strings, consistent `Qn.`/`(N)` marker conventions). A JEE paper from a different publisher/tool would likely need a different header/footer regex, and possibly a different option-marker convention, at minimum — untested.
6. **No automated test suite.** All verification this session was done via ad-hoc scripts run against `sample_data/` and the downloaded papers, not committed as reusable tests. If you add a test suite, the exact checks used this session (image-count invariant, answer-match-rate, per-question option-count consistency, stem non-truncation heuristic) are good candidates — see §8.1.

---

## 8. Where the conversation is heading — the next-phase design (not yet built)

The user wants to evolve this from "one hardcoded extractor tuned for MathonGo" into a **template-based system**: upload a paper → detect if a known template fits → run it; if not, have AI infer a new template, validate it, and hand it to the user. This section captures the design discussion so far, including the specific weaknesses identified and how they were addressed — **read this before proposing "let's just use AI to parse everything"; that idea was explicitly discussed and scoped down for good reasons.**

### 8.1 Core insight: turn every bug found in §6 into a permanent automatic invariant
Every real bug this session was caught by a specific, repeatable check, not vibes. These should become hard, non-negotiable gates before any template (hand-written or AI-generated) is trusted:
- **Image conservation**: `total embedded images in doc == total images assigned to some question`, exactly, whole-document. (This is literally how §6.2 and part of §6.3 were found.)
- **Answer coverage must be 100%**, not "close enough" — any unmatched answer is a specific, fixable format gap (§6.4 proves this — the fix was a one-line regex change once the gap was known).
- **Stem-truncation heuristic**: flag any `stem_text` ending on a stopword/preposition/conjunction or with no terminal punctuation — this is exactly the signature §6.2's bug had, and currently nothing in the code checks for it automatically.
- **Per-question option-count consistency**: outlier counts (not cleanly "all-4" or "all-0-and-flagged-numerical") indicate a real parsing failure, not noise.

### 8.2 Template representation
Proposed as **declarative JSON config** interpreted by a generic engine (the current `extractor.py` logic, parameterized) — explicitly **not** AI-generated Python code, to avoid "is this generated code safe to execute" risk and to keep templates diffable/auditable. Draft schema shape (not implemented yet):
```json
{
  "id": "jee_main_mathongo_v1",
  "match_signature": { "requires_text_layer": true, "branding_strings": ["MathonGo", "JEE Main"], ... },
  "layout": { "option_grid": "2-column-row-grouped", "header_detector_regex": "...", ... },
  "answer_key": { "page_detector": "ANSWER KEY", "answer_regex": "..." },
  "question_type_rule": "no_option_markers_found -> numerical"
}
```
The current `jee-extractor-app` codebase, if this is built, would effectively become the *first* template (`jee_main_mathongo_v1`) plus the generic engine that interprets it.

### 8.3 Matching strategy: hybrid fingerprint + validate
Cheap fingerprint prefilter (text layer present? branding strings hit?) narrows candidates; top candidates are then actually *run* and scored against the §8.1 invariants + historical baseline — highest score above threshold wins. Pure fingerprint-only matching was rejected as unsafe (can match a template that "looks right" but scores badly); pure run-everything was rejected as wasteful.

### 8.4 Template lifecycle: draft → validated (not create → trust)
AI proposes a template → engine runs it → must clear all §8.1 hard invariants (iterate with AI, capped at ~3 attempts, using the *specific* invariant violation as feedback) → status becomes **draft**. Draft templates require a human spot-check of a small sample on every run until they've had **3 clean runs across 3 different documents** (not the same document 3 times) → promoted to **validated** → auto-runs without human review. A validated template that later scores below its own historical baseline (e.g. publisher changed their export tool) automatically demotes back to requiring review — it doesn't coast on past reputation.

### 8.5 Confidence tiers replace a single accept/reject threshold
- **HIGH** (validated template, zero invariant violations) → auto-run.
- **MEDIUM** (matches but score dropped, or a soft heuristic tripped) → run it, surface only the flagged questions for a quick human check.
- **LOW/NONE** → AI bootstrap path (§8.4).

### 8.6 Vision-only documents (JEE Advanced, §4) get a separate template "kind"
No regex/geometry invariant can validate a vision-LLM extraction the way §8.1's checks validate a text-based one. These should **always** route to mandatory human sampling regardless of the model's self-reported confidence — never promoted to auto-run tier the way text-based templates can be.

### 8.7 Explicitly rejected/descoped ideas (so they aren't re-proposed without new information)
- **"Just use AI/vision to extract everything"** — discussed and narrowed to *only* the JEE-Advanced (no-text-layer) case. For the 8 working text-based papers, the deterministic approach is faster, free, and already proven at 100% on every hard invariant — replacing it wholesale would trade a working, verifiable system for a probabilistic one with no upside for that format family.
- **AI-generated template = generated Python code, executed directly** — rejected in favor of AI-generated *declarative config* interpreted by a fixed, already-audited engine.
- **A single confidence threshold deciding "trust or don't"** — rejected in favor of the tiered approach in §8.5, because a threshold either lets clearly-wrong-but-lucky matches through or blocks clearly-fine matches, depending on where you set it.

### 8.8 Open / not yet decided
- Nothing in §8 has been implemented — it's a design discussion only. The current codebase is still the single hardcoded `extractor.py` described in §5.
- Exact promotion criteria numbers (3 clean runs? what "meaningfully below baseline" means numerically) are placeholders from conversation, not finalized.
- Whether JEE Advanced support is in scope for v1 of the template system, or deferred, is undecided.
- No template schema, matcher, or bootstrap code exists yet — if you're picking this up, the highest-leverage first step discussed was building the §8.1 invariant-checking module first, since it's reusable regardless of how the templating/matching layer ends up designed, and it directly encodes the only bugs we've actually proven exist.

---

## 9. How to verify any future change (the method that actually found every bug above)

Don't trust "it ran without crashing" or "the note list is empty." Do this instead:
1. Run `parse_pdf` against a paper and actually **open several rendered crop PNGs as images** and read them — don't just check that a file exists.
2. Compute the image-conservation invariant (§8.1) across the **whole document**, not per-question — whole-document is what caught the cross-page bugs, per-question same-page checks alone (the pre-existing check in `extractor.py`) missed them.
3. Compute answer-match rate and require 100%, then manually inspect *why* any gap exists rather than accepting "close enough."
4. Cross-reference against the actual PDF page render (`page.get_pixmap()`) for any question flagged by the above, to confirm what the *correct* extraction should have been before trusting a fix.

---

## 10. Update: Phase 0 + Phase 1 implemented (§8 is no longer "not yet built" for these two phases)

Sections 1-9 above describe the state as of the initial handoff. Phase 0 (regression suite) and Phase 1 (canonical foundation) from the follow-up "Question Template Studio" spec have since been implemented, following the approved plan. This section is the delta — read it alongside, not instead of, sections 1-9.

### What exists now, beyond `app.py`/`extractor.py`
- `tests/fixtures/pdfs/` — all 8 verified papers, committed as self-contained fixtures (not dependent on `~/Downloads`).
- `tests/unit/test_extractor_regression.py`, `test_extractor_known_bugs.py`, `test_golden_manifest.py` — the Phase 0 suite. 63 tests, all green, against `extractor.py`/`app.py` with zero behavior changes from the original handoff **except** the one fix described below.
- `tests/golden/` — exact-hash golden manifests + `questions.json` per paper, regenerated (and re-verified green) each time a verified fix changes output.
- `src/` — the Phase 1 canonical layer: Pydantic models (`models/`), SQLAlchemy persistence (`db/`), page inspection/rendering/coordinate helpers (`pdf/`), and `extraction/legacy_mathongo_adapter.py` + `extraction/ingest.py`, which wrap `parse_pdf()` and persist its output as canonical `Question`/`Option`/`ContentBlock` rows with real bounding-box evidence. **Purely additive** — `app.py` is untouched, imports nothing from `src/`, and still runs exactly as before (`streamlit run app.py`).
- `scripts/run_adapter.py` — CLI harness to run the full ingest → adapt → persist pipeline without the UI. Verified end-to-end against all 8 fixtures: 100% answer coverage, 100% image conservation, correct row counts in `data/app.db` (705 questions across 8 documents, 2760 options, 705 answers, 129 assets).

### A ninth bug, found only because Phase 1 added real bbox evidence
Wiring real geometry into `SourceEvidence.bbox` (instead of a placeholder) surfaced a genuine pre-existing bug that no prior check caught: **JEE Main 2012 (19 May Online) Q72's stem was silently empty.**

Its stem contains the literal text `f(x) = ... ; lim f(1−α)−f(1) / (α³+3α)`. The embedded `(1)` inside `f(1)` matched the same character-level `\((\d)\)` regex used to find real option markers, at a y-position *above* the real option row — so it became the topmost "row," and `stem_y_end` (derived from that row's y) ended up *less than* `y_start`, producing a degenerate, near-zero-height stem crop and an empty `stem_text`. This was invisible to every prior check (image conservation N/A — no image in this stem; answer coverage fine — independent of stem text) and was only caught because a strict `BoundingBox` Pydantic validator (`0 <= y0 < y1 <= 1`) rejected the inverted rect outright.

**Fix** (in `find_opt_markers`, `extractor.py`): reject any `(N)` match whose preceding character is alphanumeric — that's the signature of a marker embedded in a function-call-like token (`f(1)`) rather than a real, standalone option marker, which is always preceded by nothing or a non-alnum character. Verified via a full regression sweep across all 8 papers (no other question anywhere had a similarly corrupted rect) and two new permanent tests (`test_may19_q72_stem_not_corrupted_by_embedded_parenthetical`, `test_no_inverted_or_degenerate_rects_anywhere`) in `test_extractor_known_bugs.py`.

This is the same lesson as every other bug in this document: the fix came from a stricter *invariant* (here, "a bounding box's coordinates must be ordered"), not from reading code or eyeballing output.

### Still true, unchanged
Everything in §7 (Known limitations) still applies — JEE Advanced remains unsupported, `stem_text` is still best-effort, there's still no validation-rule engine, review UI, or template matching/learning. Phase 0+1 only build the regression safety net and the data/persistence foundation those later phases need.
