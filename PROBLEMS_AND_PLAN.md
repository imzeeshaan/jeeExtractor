# JEE Question Extractor — Current Problems, Plan, and Approach

## 1. Where we are today

We have a working Streamlit POC (`jee-extractor-app/`) that deterministically parses JEE Main question papers (PyMuPDF, no AI in the extraction path) into structured question/option/answer data. It's been tested and verified — not just "runs without crashing" — against 10 real papers, with 8 fully working:

- 5× JEE Main 2012, 2× JEE Main 2019, 1× JEE Main 2020 — all hit 100% on the two invariants that actually matter: every embedded image gets assigned to a question, and every answer-key entry gets matched.
- Several real bugs were found and fixed this session this way (a stem silently truncated across a page break, a numeric answer format the regex didn't handle) — always by measuring against a hard, whole-document invariant, never by eyeballing "does this look okay."

## 2. Problems we're still facing

1. **JEE Advanced papers don't work at all.** They're scanned/image PDFs with effectively no text layer — our parser has nothing to find a `Qn.` marker or `(N)` option marker against. This isn't a bug to fix; it's a different problem needing OCR or vision-based extraction.
2. **The extractor only knows one format.** It's hand-tuned to one publisher's export style (MathonGo — consistent branding strings, consistent marker conventions). A paper from a different source would likely break it, and fixing that today means editing Python code, not configuring something.
3. **No support for non-MCQ question types.** Match-the-columns, assertion-reason, paragraph-based question groups, chemistry structures — none of these exist in our current data model, which only distinguishes `mcq` vs `numerical` (and only added that distinction this session, after finding it broken).
4. **Validation today only proves internal consistency, not correctness.** Our invariants (image count, answer coverage) are real and valuable, but a wrong-yet-self-consistent extraction could still pass them. The worst bug we found this session (a truncated stem) wasn't caught by any automated check — it was caught by a human reading the output.
5. **No template system — the parser is one hardcoded script.** There's no way to detect "this is a new format," no way to store/reuse rules per format, no versioning.
6. **No review workflow, no evidence trail, no persistence.** Everything runs in-memory in one Streamlit session; there's no database, no per-field confidence, no bounding-box evidence linking an extracted field back to its source, no record of human corrections.
7. **No security/testing infrastructure.** No prompt-injection defense (relevant the moment any AI/vision step is added — document content must be treated as data, never instructions), no automated test suite, no mock-provider testability.

## 3. The plan to address it

We evaluated two paths and they turned out to be complementary rather than competing:

- **Our own design** (built through this conversation): a template-matching system — fingerprint a new document, try known templates, score them against hard invariants, and if nothing scores well enough, have AI propose a new template (declarative config, not generated code), validate it against those same invariants, and only promote it to "trusted" after several clean runs across different documents, not just one.
- **An external, more complete spec** ("Question Template Studio") that was shared with us: covers the same core idea (templates, confidence-gated trust, deterministic-first-then-vision cascade) but goes further — full data model with per-field evidence and confidence, a proper review UI, SQLite persistence with versioning and audit trail, a pluggable AI provider interface with a mock for testing, explicit prompt-injection defense, mixed/non-MCQ question types as first-class citizens, and a phased build plan with acceptance criteria.

Comparing them directly: the external spec is the better blueprint. It independently converged on the same core mechanism we designed, then covers everything ours doesn't — the data model, UI, persistence, and security work needed to actually ship this as a real tool rather than a design pattern.

## 4. How to address it — concrete next steps

1. **Treat the external spec as the source-of-truth architecture going forward**, not as an alternative to weigh against our own — it's a superset.
2. **Fold in the two invariants we've already proven matter** (whole-document image conservation, 100% answer-key coverage) as explicit, named rules in that spec's validation engine (§19.1) — the spec's rule list is broader but doesn't currently name these two specifically, and they're the ones that caught real bugs.
3. **Reuse the current `extractor.py` logic as the first working template**, not throw it away — its marker-detection, row/column reconstruction, and cross-page handling become the deterministic PDF-text extraction path (and effectively the "MathonGo JEE Main" template) inside the new system, rather than something rebuilt from scratch.
4. **Build in the phased order the spec lays out**, keeping the app runnable after each phase: foundation (repo, models, DB, upload/render) → generic extraction (layout detection, question assembly) → validation and review (rules, confidence, review queue) → template system (matching, learning, Template Studio) → export and hardening (JSON/CSV/ZIP, retries, metrics).
5. **Scope JEE Advanced (vision-only) support as an explicit, separate decision** — it needs the vision extraction cascade the spec describes, and should never be auto-trusted the way a text-based template can be, regardless of what confidence score a model reports.
6. **Keep the current app running as-is in the meantime** — it's a proven, working tool for the 8 supported papers today; nothing about starting the bigger build requires taking it offline first.
