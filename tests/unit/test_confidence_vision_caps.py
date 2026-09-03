"""
Phase 5: compute_question_confidence/compute_document_trust_tier's
vision-only caps — spec §17's "vision-only extraction is never HIGH."
"""
from models.common import BoundingBox, SourceEvidence
from models.questions import ContentBlock, Question
from validation.confidence import compute_document_trust_tier, compute_question_confidence

_BBOX = BoundingBox(x0=0.1, y0=0.1, x1=0.5, y1=0.5)


def _evidence(method="vision_layout"):
    return SourceEvidence(page_number=1, bbox=_BBOX, extraction_method=method)


def _vision_question(**overrides):
    base = dict(
        question_id="q1", document_id="doc1", number="1", question_type="unknown",
        stem_blocks=[ContentBlock(block_id="b1", content_type="text", text=None,
                                   clean_crop_path="images/q1.png", evidence=_evidence(),
                                   confidence=0.9, status="needs_review")],
        options=[], answer=None, page_start=1, page_end=1,
        extraction_mode="vision_layout", confidence=0.5, status="needs_review",
    )
    base.update(overrides)
    return Question(**base)


def test_confidence_never_exceeds_vision_cap_even_with_zero_issues():
    q = _vision_question()
    score = compute_question_confidence(q, question_issues=[], document_level_issues=[])
    assert score <= 0.6


def test_confidence_still_floors_on_blocking_issue():
    class FakeIssue:
        severity = "blocking"
    q = _vision_question()
    score = compute_question_confidence(q, question_issues=[FakeIssue()], document_level_issues=[])
    assert score == 0.0


def test_trust_tier_never_high_when_any_question_is_vision_derived():
    q = _vision_question()
    tier = compute_document_trust_tier([q], all_issues=[])
    assert tier != "HIGH"
    assert tier == "MEDIUM"


def test_trust_tier_stays_none_below_the_vision_cap_on_blocking_issue():
    class FakeIssue:
        severity = "blocking"
    q = _vision_question()
    tier = compute_document_trust_tier([q], all_issues=[FakeIssue()])
    assert tier == "NONE"
