"""
Phase 5: MockVisionProvider's wiring contract — trivial, but pins the
exact shape everything else (adapter, escalation) depends on.
"""
from providers.mock_vision import MockVisionProvider


def test_detect_layout_returns_a_question_number_region_and_a_content_region():
    provider = MockVisionProvider()
    result = provider.detect_layout("dummy.png", page_number=3, context={})
    assert result.page_number == 3
    assert len(result.regions) == 2
    number_region, content_region = result.regions
    assert number_region.region_type == "question_number"
    assert number_region.question_number_hint == "3"
    assert content_region.region_type == "unknown"
    for region in result.regions:
        assert 0 <= region.bbox.x0 < region.bbox.x1 <= 1
        assert 0 <= region.bbox.y0 < region.bbox.y1 <= 1
    assert result.reading_order == [number_region.region_id, content_region.region_id]


def test_transcribe_region_reports_unreadable_not_fabricated_text():
    provider = MockVisionProvider()
    result = provider.transcribe_region("dummy_crop.png", context={"field_kind": "question_stem"})
    assert result.unreadable is True
    assert result.text is None
