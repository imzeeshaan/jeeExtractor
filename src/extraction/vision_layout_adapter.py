"""
Vision-layout adapter — LegacyAdapterResult-compatible (same 4 attributes:
.questions, .metrics, .issues, .legacy_questions) so ingest.py's downstream
code (validation, persistence, lifecycle-update) needs zero branching on
which adapter ran.

.legacy_questions is set to None (not a synthesized empty list) — an empty
list would claim "zero images assigned to zero total", which is false
information for a scanned page (which usually has one large embedded
image); None is the honest "this concept does not apply to this extraction
path", and validation.engine.run_validation skips check_image_conservation
entirely when legacy_questions is None.

Explicit, known gaps (documented here rather than silently half-built):
real JEE Advanced question-type classification (question_type stays
"unknown"), multi-question shared-passage linking via SharedContext, and
cross-page region continuation (a question spanning two pages is not
merged — each page's regions are attributed independently).
"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from PIL import Image

from models.common import BoundingBox, SourceEvidence
from models.questions import ContentBlock, Option, Question
from pdf.coordinates import normalized_to_pixel

_REGION_TYPES_AS_STEM = {"question_stem", "diagram", "equation", "table", "shared_passage", "unknown"}
_REGION_TYPES_SKIPPED = {
    "header", "footer", "subject_heading", "section_heading", "instructions",
    "answer_key", "page_number",
}

# Real, live-observed limitation (confirmed visually across multiple real
# questions this session): DeepSeek's bounding boxes are sometimes drawn too
# tight, most severely cutting off content that extends above a normal text
# baseline (radicals, superscripts, stacked-fraction numerators) but
# occasionally even on plain text lines. Padding every box by a small margin
# before cropping is a cheap, deterministic accuracy improvement that
# doesn't depend on the model's own precision. Padded more on top than
# elsewhere since that's specifically where clipping was observed
# (numerators/superscripts sit above the detected box, not below it).
#
# Collision-aware (added after a second real, live-observed problem): a
# blanket fixed padding bled into neighboring regions when options are
# packed tightly (confirmed live — a densely-stacked options block only
# ~3.6% of page height apart per row had its 2% top padding eat over half
# that gap, pulling the option ABOVE's tail text into each crop). Padding is
# now clamped, per side, to at most half the gap to the nearest OTHER region
# on that side that horizontally overlaps this one (i.e. a genuine
# above/below neighbor, not an unrelated region off to the side) — expands
# as much as safely possible to prevent clipping, never past the midpoint
# to a neighbor.
_BBOX_PAD_TOP = 0.02
_BBOX_PAD_BOTTOM = 0.01
_BBOX_PAD_SIDES = 0.005


def _clamp_edges_to_neighbors(region_bbox, other_boxes, x0, y0, x1, y1):
    # Real bug found live this session: the vision model's own raw region
    # boxes are sometimes not just tightly spaced but actually OVERLAPPING
    # a neighbor by a small amount (confirmed live on JEE ADV 2007-1.pdf
    # Q4's options, ~0.2-0.3% of page height each) -- a clamp that only
    # reduces ADDED padding when there's a clean, non-overlapping gap does
    # nothing in that case, so full fixed padding still got added on top
    # of an already-overlapping raw box, making the bleed worse, not
    # better. Instead, compute each final edge directly as the midpoint
    # between this region's own raw edge and the qualifying neighbor's
    # facing edge -- this naturally reduces to "half the gap" when there's
    # a real gap, and naturally TRIMS the raw box back to the true
    # midpoint when the raw boxes already overlap, splitting the
    # contested strip evenly rather than handing it all to one crop.
    for other in other_boxes:
        if other is region_bbox:
            continue
        horizontal_overlap = other.x0 < region_bbox.x1 and other.x1 > region_bbox.x0
        vertical_overlap = other.y0 < region_bbox.y1 and other.y1 > region_bbox.y0
        if horizontal_overlap:
            other_center_y = (other.y0 + other.y1) / 2
            own_center_y = (region_bbox.y0 + region_bbox.y1) / 2
            if other_center_y < own_center_y:  # neighbor above
                midpoint = (other.y1 + region_bbox.y0) / 2
                y0 = max(y0, midpoint)
            elif other_center_y > own_center_y:  # neighbor below
                midpoint = (region_bbox.y1 + other.y0) / 2
                y1 = min(y1, midpoint)
        if vertical_overlap:
            other_center_x = (other.x0 + other.x1) / 2
            own_center_x = (region_bbox.x0 + region_bbox.x1) / 2
            if other_center_x < own_center_x:  # neighbor to the left
                midpoint = (other.x1 + region_bbox.x0) / 2
                x0 = max(x0, midpoint)
            elif other_center_x > own_center_x:  # neighbor to the right
                midpoint = (region_bbox.x1 + other.x0) / 2
                x1 = min(x1, midpoint)
    return x0, y0, x1, y1


def _pad_and_build_bbox(region_bbox, other_region_boxes: list = None) -> BoundingBox:
    other_region_boxes = other_region_boxes or []
    x0 = region_bbox.x0 - _BBOX_PAD_SIDES
    y0 = region_bbox.y0 - _BBOX_PAD_TOP
    x1 = region_bbox.x1 + _BBOX_PAD_SIDES
    y1 = region_bbox.y1 + _BBOX_PAD_BOTTOM
    x0, y0, x1, y1 = _clamp_edges_to_neighbors(region_bbox, other_region_boxes, x0, y0, x1, y1)
    # Safety floor only for the degenerate case where two neighbors'
    # midpoints cross each other (e.g. a region squeezed between two very
    # close overlapping neighbors) -- fall back to the raw region rather
    # than an inverted/zero-size crop. This does NOT re-forbid trimming
    # past the raw edge on one side alone, which is the actual fix for the
    # overlapping-raw-box case above.
    if x0 >= x1:
        x0, x1 = region_bbox.x0, region_bbox.x1
    if y0 >= y1:
        y0, y1 = region_bbox.y0, region_bbox.y1
    return BoundingBox(
        x0=max(0.0, x0), y0=max(0.0, y0),
        x1=min(1.0, x1), y1=min(1.0, y1),
    )


class VisionLayoutAdapterResult:
    def __init__(self, document_id, questions, metrics, issues, legacy_questions, dropped_region_warnings):
        self.document_id = document_id
        self.questions = questions
        self.metrics = metrics
        self.issues = issues
        self.legacy_questions = legacy_questions  # always None for this adapter
        # page_number -> [warning strings], for vision_escalation's "unclear
        # image-to-question mapping" criterion.
        self.dropped_region_warnings = dropped_region_warnings


def _crop_region(page_png_path: str, bbox: BoundingBox, out_path: str) -> None:
    with Image.open(page_png_path) as img:
        x0, y0, x1, y1 = normalized_to_pixel(bbox, img.width, img.height)
        img.crop((int(x0), int(y0), int(x1), int(y1))).save(out_path)


class VisionLayoutAdapter:
    def __init__(self, provider):
        self._provider = provider

    def run(self, document, pdf_path: str, out_dir: str,
            template_id: str = None, template_version: int = None, pages: list = None,
            on_page_result=None, concurrency: int = 5) -> VisionLayoutAdapterResult:
        """on_page_result(page, page_questions, page_warnings, completed, total),
        when provided, fires ONCE PER PAGE on this method's calling thread —
        in COMPLETION order (not page-number order), immediately after that
        page's detect_layout response has been fetched AND grouped into
        Questions. This is what lets ingest.py persist each page's results
        the moment they're ready, instead of waiting for the whole
        document (see the approved "Concurrent + Incremental Vision
        Processing" plan).

        Per-page detect_layout calls are dispatched concurrently (a
        ThreadPoolExecutor of `concurrency` workers) since they're
        independent network requests with no data dependency on each other
        — confirmed: question/option grouping is entirely self-contained
        per page (a question_number region only sets attribution for
        regions on THAT SAME page), so completion order doesn't affect
        correctness. A single page's exception is caught here and recorded
        as a dropped/skipped page rather than aborting the whole run.

        One real, deliberate behavior change from the fully-sequential
        version: if DeepSeek mislabels the same question number on two
        DIFFERENT pages, they are no longer silently merged into one
        Question (each page's grouping is now independent, not shared
        across pages via one dict) — they persist as two separate rows,
        which rules_sequence.SEQ_DUPLICATE_NUMBER will correctly flag. This
        is more honest than silently merging two different questions'
        content, not a regression."""
        if not pages:
            raise ValueError("VisionLayoutAdapter.run requires pages= (list[Page] with rendered_path set)")

        images_dir = os.path.join(out_dir, "images")
        os.makedirs(images_dir, exist_ok=True)

        valid_pages = [p for p in pages if p.rendered_path]
        total = len(valid_pages)
        all_questions = []
        dropped_region_warnings = {}

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            future_to_page = {
                executor.submit(
                    self._provider.detect_layout, page.rendered_path, page.page_number,
                    {"template_rules": None, "prior_layout": None, "escalation_reason": None},
                ): page
                for page in valid_pages
            }
            completed = 0
            for future in as_completed(future_to_page):
                page = future_to_page[future]
                completed += 1
                try:
                    layout = future.result()
                    page_questions, page_warnings = self._group_page_layout(
                        page, layout, images_dir, out_dir, document, template_id, template_version,
                    )
                except Exception as exc:
                    page_questions, page_warnings = [], [f"detect_layout failed: {exc}"]

                if page_warnings:
                    dropped_region_warnings[page.page_number] = page_warnings
                all_questions.extend(page_questions)
                if on_page_result:
                    on_page_result(page, page_questions, page_warnings, completed, total)

        metrics = {
            "question_count": len(all_questions),
            "page_count": len(pages),
            "region_count": sum(
                len(q.stem_blocks) + sum(len(o.blocks) for o in q.options) for q in all_questions
            ),
            "pages_with_dropped_regions": len(dropped_region_warnings),
        }
        return VisionLayoutAdapterResult(
            document_id=document.document_id, questions=all_questions, metrics=metrics,
            issues=[], legacy_questions=None, dropped_region_warnings=dropped_region_warnings,
        )

    def _group_page_layout(self, page, layout, images_dir, out_dir, document, template_id, template_version):
        """Groups ONE page's already-fetched LayoutResponse into Questions
        — extracted from the original single-pass loop body, just scoped to
        one page instead of accumulating across all pages in a shared dict.
        Runs on the calling (main) thread only — the ThreadPoolExecutor
        workers only ever do the network call.

        Attribution: a region's OWN question_number_hint always wins when
        present, regardless of its region_type; a dedicated
        region_type=="question_number" region only updates the carried-
        forward `current_number` used as a fallback for regions with no
        hint of their own. This was a real bug, confirmed live on a real
        document (JEE ADV 2007-1.pdf): some documents number questions
        inline within the stem paragraph rather than as a separate boxed
        label, so DeepSeek never emits a region_type=="question_number"
        region at all for them — it puts the correct number directly on
        question_number_hint for the stem/option regions themselves
        instead. The original code only ever read question_number_hint off
        a region explicitly typed "question_number", so every region on
        such a page was silently dropped as "unattributed" even though the
        correct number was present the whole time — roughly half of that
        document's questions never made it into the database. This fix
        doesn't change behavior for documents that DO use a dedicated
        question_number region (confirmed: their other regions all have
        question_number_hint=None, so `region.question_number_hint or
        current_number` still resolves to the carried-forward value)."""
        questions_by_number = {}
        page_warnings = list(layout.warnings)
        current_number = None
        for region_id in layout.reading_order:
            region = next((r for r in layout.regions if r.region_id == region_id), None)
            if region is None:
                page_warnings.append(f"reading_order referenced unknown region_id {region_id!r} — dropped")
                continue
            if region.region_type in _REGION_TYPES_SKIPPED:
                continue
            if region.region_type == "question_number" and region.question_number_hint:
                current_number = region.question_number_hint
                questions_by_number.setdefault(current_number, self._new_question(
                    document, page, current_number, template_id, template_version))
                continue

            target_number = region.question_number_hint or current_number
            if target_number is None:
                page_warnings.append(
                    f"region {region.region_id!r} ({region.region_type}) seen before any "
                    "question number could be determined — dropped (unattributed)"
                )
                continue
            current_number = target_number
            questions_by_number.setdefault(target_number, self._new_question(
                document, page, target_number, template_id, template_version))

            try:
                bbox = _pad_and_build_bbox(region.bbox, [r.bbox for r in layout.regions])
            except Exception as exc:
                page_warnings.append(f"region {region.region_id!r} had an invalid bbox, dropped: {exc}")
                continue

            q = questions_by_number[current_number]
            crop_name = f"{document.document_id}_p{page.page_number}_{region.region_id}.png"
            crop_path = os.path.join(images_dir, crop_name)
            _crop_region(page.rendered_path, bbox, crop_path)

            evidence = SourceEvidence(
                page_number=page.page_number, bbox=bbox, extraction_method="vision_layout",
                # layout.served_by is the ACTUAL provider that produced this
                # response (e.g. "deepseek", "openai") -- NOT
                # type(self._provider).__name__, which is always
                # "FallbackVisionProvider" regardless of which underlying
                # provider handled the call (a real bug this replaces: it
                # made it impossible to tell DeepSeek-served regions apart
                # from OpenAI-fallback-served ones after the fact).
                provider_name=layout.served_by or type(self._provider).__name__,
            )
            block = ContentBlock(
                block_id=str(uuid.uuid4()),
                content_type="image" if region.contains_visual else "text",
                text=None,  # layout-only this phase — no transcription attempted
                clean_crop_path=os.path.relpath(crop_path, out_dir),
                evidence=evidence,
                confidence=region.confidence,
                status="needs_review",  # never "extracted" — nothing was verified
            )

            if region.region_type in _REGION_TYPES_AS_STEM:
                q.stem_blocks.append(block)
            elif region.region_type in ("option_label", "option_content"):
                label = region.option_label_hint or "?"
                opt = next((o for o in q.options if o.label == label), None)
                if opt is None:
                    opt = Option(option_id=str(uuid.uuid4()), label=label, blocks=[],
                                 evidence=evidence, confidence=region.confidence,
                                 review_required=True)
                    q.options.append(opt)
                opt.blocks.append(block)

        return list(questions_by_number.values()), page_warnings

    @staticmethod
    def _new_question(document, page, number, template_id, template_version) -> Question:
        return Question(
            question_id=str(uuid.uuid4()), document_id=document.document_id, number=number,
            # "unknown" is the honest default — nothing in this phase
            # classifies MCQ vs. multiple_correct vs. numerical vs.
            # match_columns etc. for a vision-derived question.
            question_type="unknown",
            stem_blocks=[], options=[], page_start=page.page_number, page_end=page.page_number,
            extraction_mode="vision_layout", confidence=0.5, status="needs_review",
            template_id=template_id, template_version=template_version,
        )
