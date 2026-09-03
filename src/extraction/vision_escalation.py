"""
Validation-driven per-question escalation from the primary vision provider
(DeepSeek) to the fallback provider (OpenAI) — the actual cost/accuracy
design the user asked for: DeepSeek runs on every page (cheap), and only the
specific questions whose OUTPUT looks wrong get individually re-run through
OpenAI, not a blanket retry-the-whole-call-on-error path (that narrower
error-only case is providers.fallback_vision.FallbackVisionProvider, which
composes with this rather than replacing it).

Escalation criteria (per the user's explicit list — "low confidence, missing
options, unclear image-to-question mapping, or answer-key mismatches"):

- Low detection confidence: reads DeepSeek's own per-region self-reported
  LayoutRegion.confidence (via ContentBlock.confidence, already stored per
  block) — a real, independent signal, NOT the same thing as the document-
  level 0.6 confidence cap every vision question gets from
  compute_question_confidence, which would trivially flag everything if
  used here.
- Missing options: len(question.options) == 0 — a real, cheap, honest
  check. Deliberately narrower than OPT_COUNT_UNEXPECTED/
  OPT_PARTIAL_MARKER_SET, which stay suppressed for question_type=="unknown"
  (see rules_option_consistency.py) since "expected exactly N options" isn't
  knowable for a document type this system doesn't classify — "zero options
  at all" is unambiguous.
- Unclear image-to-question mapping: VisionLayoutAdapterResult already
  tracks dropped/unattributed regions per page (regions seen before any
  question_number, or a hallucinated reading_order id) — any question on a
  page carrying such a warning is flagged.
- Answer-key mismatch: NOT IMPLEMENTED. No answer-key mapping is attempted
  for vision-derived questions this phase (rules_answer_coverage.py skips
  extraction_mode=="vision_layout" entirely) — there is no signal to check a
  mismatch against yet. Documented here as a known gap, not faked as an
  always-false check.
"""
_ESCALATION_REGION_CONFIDENCE_THRESHOLD = 0.6


def _min_block_confidence(question) -> float:
    confidences = [b.confidence for b in question.stem_blocks]
    for opt in question.options:
        confidences.extend(b.confidence for b in opt.blocks)
    return min(confidences) if confidences else 0.0


def select_questions_for_escalation(questions: list, dropped_region_warnings: dict,
                                     question_ids_with_clipped_crops: set = None) -> list:
    """Returns (question, reason) pairs for every question meeting at least
    one real criterion. dropped_region_warnings is
    VisionLayoutAdapterResult.dropped_region_warnings (page_number -> list
    of warning strings). question_ids_with_clipped_crops (optional) is the
    set of question_ids that had a rules_geometry.GEOM_CROP_EDGE_CLIPPED
    issue fire on any of their blocks — a real, live-observed failure mode
    (bounding boxes cut off content, most severely for math notation with
    radicals/superscripts) that a confidence check alone can't catch, since
    the model still reports high confidence on a clipped-but-plausible-
    looking crop."""
    question_ids_with_clipped_crops = question_ids_with_clipped_crops or set()
    flagged = []
    for q in questions:
        if _min_block_confidence(q) < _ESCALATION_REGION_CONFIDENCE_THRESHOLD:
            flagged.append((q, "low detection confidence"))
        elif len(q.options) == 0:
            flagged.append((q, "no options detected"))
        elif q.page_start in dropped_region_warnings or q.page_end in dropped_region_warnings:
            flagged.append((q, "unclear image-to-question region mapping on this page"))
        elif q.question_id in question_ids_with_clipped_crops:
            flagged.append((q, "crop appears clipped (edge-ink-density check)"))
    return flagged


def escalate_question(question, page, fallback_provider, reason: str, out_dir: str):
    """Re-runs fallback_provider.detect_layout on the question's page,
    passing the primary provider's own regions as context so the fallback
    can verify/correct rather than detect blind. Rebuilds this question's
    stem_blocks/options from the fallback's response ONLY (does not merge
    two providers' regions for one question — ambiguous provenance is worse
    than a clean replacement), re-cropping fresh image files from the page
    (never reuses the primary provider's crop files, which may be exactly
    the badly-placed regions that triggered escalation). Returns the
    updated question in place."""
    import os
    import uuid

    from extraction.vision_layout_adapter import _crop_region, _pad_and_build_bbox
    from models.common import SourceEvidence
    from models.questions import ContentBlock, Option

    prior_layout = [
        {"region_id": b.block_id, "confidence": b.confidence, "content_type": b.content_type}
        for b in question.stem_blocks
    ]
    context = {"template_rules": None, "prior_layout": prior_layout, "escalation_reason": reason}
    layout = fallback_provider.detect_layout(page.rendered_path, page.page_number, context)

    images_dir = os.path.join(out_dir, "images")
    os.makedirs(images_dir, exist_ok=True)

    new_stem_blocks = []
    new_options = []
    for region in layout.regions:
        if region.region_type in ("header", "footer", "subject_heading", "section_heading",
                                   "instructions", "answer_key", "page_number", "question_number"):
            continue
        # Real bug fixed here: a page frequently has MULTIPLE questions, and
        # the fallback provider's response for the whole page includes every
        # question's regions, not just the one being escalated. The
        # original code took every non-skipped region unconditionally,
        # which silently merged a NEIGHBORING question's content into the
        # one being escalated (confirmed live: escalating Q10 on a page
        # shared with Q11 left both questions containing each other's
        # stems/options). Only keep a region whose own question_number_hint
        # matches the question actually being escalated — confirmed live
        # (DeepSeek and OpenAI both) that this hint is reliably set on every
        # real content region, so a region with no hint, or a hint for a
        # different question, is dropped rather than guessed into place.
        if region.question_number_hint != question.number:
            continue
        try:
            bbox = _pad_and_build_bbox(region.bbox, [r.bbox for r in layout.regions])
        except Exception:
            continue

        crop_name = f"{question.question_id}_escalated_p{page.page_number}_{region.region_id}.png"
        crop_path = os.path.join(images_dir, crop_name)
        _crop_region(page.rendered_path, bbox, crop_path)

        evidence = SourceEvidence(
            page_number=page.page_number, bbox=bbox, extraction_method="vision_layout",
            provider_name="openai_escalation",
        )
        block = ContentBlock(
            block_id=str(uuid.uuid4()),
            content_type="image" if region.contains_visual else "text",
            text=None,
            clean_crop_path=os.path.relpath(crop_path, out_dir),
            evidence=evidence,
            confidence=region.confidence,
            status="needs_review",
        )
        if region.region_type in ("question_stem", "diagram", "equation", "table", "shared_passage", "unknown"):
            new_stem_blocks.append(block)
        elif region.region_type in ("option_label", "option_content"):
            label = region.option_label_hint or "?"
            opt = next((o for o in new_options if o.label == label), None)
            if opt is None:
                opt = Option(option_id=str(uuid.uuid4()), label=label, blocks=[],
                             evidence=evidence, confidence=region.confidence, review_required=True)
                new_options.append(opt)
            opt.blocks.append(block)

    if new_stem_blocks:
        question.stem_blocks = new_stem_blocks
    if new_options:
        question.options = new_options
    return question
