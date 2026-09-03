"""
Phase 5: vision-aware skips in rules_answer_coverage / rules_option_consistency
— a vision-derived question's intentionally-unattempted answer-key mapping
and arbitrary option count must not fire fake failures.
"""
from models.common import BoundingBox, SourceEvidence
from models.questions import ContentBlock, Option, Question
from validation import rules_answer_coverage, rules_option_consistency

_BBOX = BoundingBox(x0=0.1, y0=0.1, x1=0.5, y1=0.5)
DOCUMENT_ID = "doc-vision-1"


def _evidence():
    return SourceEvidence(page_number=1, bbox=_BBOX, extraction_method="vision_layout")


def _block():
    return ContentBlock(block_id="b1", content_type="text", text=None, clean_crop_path="images/x.png",
                         evidence=_evidence(), confidence=0.5, status="needs_review")


def _vision_question(answer=None, options=None):
    return Question(
        question_id="qv1", document_id=DOCUMENT_ID, number="1", question_type="unknown",
        stem_blocks=[_block()], options=options or [], answer=answer,
        page_start=1, page_end=1, extraction_mode="vision_layout",
        confidence=0.5, status="needs_review",
    )


def test_answer_coverage_skips_vision_questions_with_no_answer():
    q = _vision_question(answer=None)
    issues = rules_answer_coverage.check_answer_coverage([q], DOCUMENT_ID)
    assert issues == []


def test_option_consistency_skips_arbitrary_option_count_for_unknown_type():
    opts = [Option(option_id=f"o{i}", label=str(i), blocks=[_block()], evidence=_evidence(),
                    confidence=0.5, review_required=False) for i in range(7)]
    q = _vision_question(options=opts)
    issues = rules_option_consistency.check_option_consistency(q, DOCUMENT_ID)
    codes = {i.rule_code for i in issues}
    assert "OPT_COUNT_UNEXPECTED" not in codes
    assert "OPT_PARTIAL_MARKER_SET" not in codes
