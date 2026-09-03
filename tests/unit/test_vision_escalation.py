"""
Phase 5: validation-driven per-question escalation criteria and the
escalate_question rebuild-from-fallback-only behavior.
"""
import os

from PIL import Image

from extraction.vision_escalation import escalate_question, select_questions_for_escalation
from models.common import BoundingBox, SourceEvidence
from models.documents import Page
from models.questions import ContentBlock, Option, Question
from models.vision import LayoutRegion, LayoutResponse, RegionBBox

_BBOX = BoundingBox(x0=0.1, y0=0.1, x1=0.5, y1=0.5)


def _evidence():
    return SourceEvidence(page_number=1, bbox=_BBOX, extraction_method="vision_layout")


def _block(confidence, crop_path=None):
    return ContentBlock(block_id=f"b-{confidence}", content_type="text", text=None,
                         clean_crop_path=crop_path, evidence=_evidence(),
                         confidence=confidence, status="needs_review")


def _question(number, stem_confidence=0.9, options=None, page=1):
    return Question(
        question_id=f"q{number}", document_id="doc1", number=str(number), question_type="unknown",
        stem_blocks=[_block(stem_confidence)], options=options or [],
        page_start=page, page_end=page, extraction_mode="vision_layout",
        confidence=0.5, status="needs_review",
    )


def _option(label="1", confidence=0.9):
    return Option(option_id=f"opt-{label}", label=label, blocks=[_block(confidence)],
                   evidence=_evidence(), confidence=confidence, review_required=False)


def test_low_confidence_question_is_flagged():
    q = _question(1, stem_confidence=0.3, options=[_option()])
    flagged = select_questions_for_escalation([q], dropped_region_warnings={})
    assert len(flagged) == 1
    assert flagged[0][0].question_id == "q1"
    assert "confidence" in flagged[0][1]


def test_missing_options_question_is_flagged():
    q = _question(2, stem_confidence=0.9, options=[])
    flagged = select_questions_for_escalation([q], dropped_region_warnings={})
    assert len(flagged) == 1
    assert "options" in flagged[0][1]


def test_clipped_crop_question_is_flagged_even_with_high_confidence():
    # Real gap this closes: a clipped crop can still self-report high
    # confidence (the model doesn't know it cut off content), so the
    # confidence/options/dropped-region criteria alone would miss it.
    q = _question(6, stem_confidence=0.95, options=[_option()])
    flagged = select_questions_for_escalation(
        [q], dropped_region_warnings={}, question_ids_with_clipped_crops={q.question_id},
    )
    assert len(flagged) == 1
    assert "clipped" in flagged[0][1]


def test_clean_high_confidence_question_not_flagged_when_not_in_clipped_set():
    q = _question(7, stem_confidence=0.95, options=[_option()])
    flagged = select_questions_for_escalation(
        [q], dropped_region_warnings={}, question_ids_with_clipped_crops={"some-other-question"},
    )
    assert flagged == []


def test_dropped_region_page_flags_its_question():
    q = _question(3, stem_confidence=0.9, options=[_option()], page=2)
    flagged = select_questions_for_escalation([q], dropped_region_warnings={2: ["region dropped"]})
    assert len(flagged) == 1
    assert "mapping" in flagged[0][1]


def test_clean_question_is_not_flagged():
    q = _question(4, stem_confidence=0.9, options=[_option()])
    flagged = select_questions_for_escalation([q], dropped_region_warnings={})
    assert flagged == []


class _CannedFallbackProvider:
    def __init__(self, response):
        self._response = response

    def detect_layout(self, page_image_path, page_number, context):
        return self._response

    def transcribe_region(self, crop_image_path, context):
        raise NotImplementedError


def test_escalate_question_replaces_blocks_with_fallback_provenance(tmp_path):
    page_png = tmp_path / "page1.png"
    Image.new("RGB", (400, 600), color=(200, 200, 200)).save(page_png)
    page = Page(
        page_id="p1", document_id="doc1", page_number=1, width_pt=612, height_pt=792,
        page_type="scanned", char_count=0, text_bbox_coverage=0.0, image_coverage=1.0,
        has_full_page_image=True, drawing_count=0, rendered_path=str(page_png),
    )

    canned = LayoutResponse(
        page_number=1,
        regions=[
            LayoutRegion(region_id="r1", region_type="question_stem",
                         bbox=RegionBBox(x0=0.1, y0=0.1, x1=0.5, y1=0.3),
                         question_number_hint="5", contains_visual=False, confidence=0.95),
            LayoutRegion(region_id="r2", region_type="option_content",
                         bbox=RegionBBox(x0=0.1, y0=0.4, x1=0.5, y1=0.5),
                         question_number_hint="5", option_label_hint="1",
                         contains_visual=False, confidence=0.9),
        ],
        reading_order=["r1", "r2"], warnings=[],
    )
    fallback = _CannedFallbackProvider(canned)

    q = _question(5, stem_confidence=0.3, options=[])
    out_dir = str(tmp_path / "out")
    os.makedirs(out_dir, exist_ok=True)
    escalate_question(q, page, fallback, reason="low detection confidence", out_dir=out_dir)

    assert len(q.stem_blocks) == 1
    assert q.stem_blocks[0].evidence.provider_name == "openai_escalation"
    assert len(q.options) == 1
    assert q.options[0].label == "1"
    assert q.options[0].blocks[0].evidence.provider_name == "openai_escalation"
    # a fresh crop file was actually written, not a reused stale path
    crop_full_path = os.path.join(out_dir, q.stem_blocks[0].clean_crop_path)
    assert os.path.isfile(crop_full_path)


def test_escalate_question_does_not_pull_in_a_neighboring_questions_regions(tmp_path):
    # Regression guard for a real bug hit live this session: a page usually
    # has MULTIPLE questions, and the fallback provider's response for the
    # whole page includes every question's regions -- escalating one
    # question must not merge in a different question's stem/options just
    # because they happen to share a page.
    page_png = tmp_path / "page1.png"
    Image.new("RGB", (400, 600), color=(200, 200, 200)).save(page_png)
    page = Page(
        page_id="p1", document_id="doc1", page_number=1, width_pt=612, height_pt=792,
        page_type="scanned", char_count=0, text_bbox_coverage=0.0, image_coverage=1.0,
        has_full_page_image=True, drawing_count=0, rendered_path=str(page_png),
    )

    canned = LayoutResponse(
        page_number=1,
        regions=[
            LayoutRegion(region_id="q10_stem", region_type="question_stem",
                         bbox=RegionBBox(x0=0.1, y0=0.1, x1=0.5, y1=0.2),
                         question_number_hint="10", contains_visual=False, confidence=0.95),
            LayoutRegion(region_id="q10_optA", region_type="option_content",
                         bbox=RegionBBox(x0=0.1, y0=0.22, x1=0.5, y1=0.25),
                         question_number_hint="10", option_label_hint="A",
                         contains_visual=False, confidence=0.9),
            LayoutRegion(region_id="q11_stem", region_type="question_stem",
                         bbox=RegionBBox(x0=0.1, y0=0.3, x1=0.5, y1=0.4),
                         question_number_hint="11", contains_visual=False, confidence=0.95),
            LayoutRegion(region_id="q11_optA", region_type="option_content",
                         bbox=RegionBBox(x0=0.1, y0=0.42, x1=0.5, y1=0.45),
                         question_number_hint="11", option_label_hint="A",
                         contains_visual=False, confidence=0.9),
        ],
        reading_order=["q10_stem", "q10_optA", "q11_stem", "q11_optA"], warnings=[],
    )
    fallback = _CannedFallbackProvider(canned)

    q10 = _question(10, stem_confidence=0.3, options=[])
    out_dir = str(tmp_path / "out")
    os.makedirs(out_dir, exist_ok=True)
    escalate_question(q10, page, fallback, reason="low detection confidence", out_dir=out_dir)

    assert len(q10.stem_blocks) == 1
    assert q10.stem_blocks[0].clean_crop_path.endswith("q10_stem.png")
    assert len(q10.options) == 1
    assert q10.options[0].blocks[0].clean_crop_path.endswith("q10_optA.png")


def test_escalate_question_bbox_padding_does_not_bleed_into_tight_neighbor(tmp_path):
    # Regression guard for the second real bug hit live this session: Q4's
    # options were only ~3.6% of page height apart, and the fixed 2% top
    # padding used by escalate_question's bbox construction (a call site
    # separate from vision_layout_adapter's main pass, easy to miss when
    # fixing the main pass alone) ate over half that gap, bleeding a
    # neighboring option's text into the crop ("horribly cropped", per the
    # user). escalate_question must pass the full page region list into
    # _pad_and_build_bbox so its own crops are collision-aware too.
    page_png = tmp_path / "page1.png"
    Image.new("RGB", (400, 600), color=(200, 200, 200)).save(page_png)
    page = Page(
        page_id="p1", document_id="doc1", page_number=1, width_pt=612, height_pt=792,
        page_type="scanned", char_count=0, text_bbox_coverage=0.0, image_coverage=1.0,
        has_full_page_image=True, drawing_count=0, rendered_path=str(page_png),
    )

    canned = LayoutResponse(
        page_number=1,
        regions=[
            LayoutRegion(region_id="q4_optA", region_type="option_content",
                         bbox=RegionBBox(x0=0.1, y0=0.20, x1=0.9, y1=0.24),
                         question_number_hint="4", option_label_hint="A",
                         contains_visual=False, confidence=0.9),
            LayoutRegion(region_id="q4_optB", region_type="option_content",
                         bbox=RegionBBox(x0=0.1, y0=0.276, x1=0.9, y1=0.316),
                         question_number_hint="4", option_label_hint="B",
                         contains_visual=False, confidence=0.9),
        ],
        reading_order=["q4_optA", "q4_optB"], warnings=[],
    )
    fallback = _CannedFallbackProvider(canned)

    q4 = _question(4, stem_confidence=0.3, options=[])
    out_dir = str(tmp_path / "out")
    os.makedirs(out_dir, exist_ok=True)
    escalate_question(q4, page, fallback, reason="low detection confidence", out_dir=out_dir)

    opt_a = next(o for o in q4.options if o.label == "A")
    opt_b = next(o for o in q4.options if o.label == "B")
    # padded boxes must never cross the midpoint gap between the two options
    assert opt_a.blocks[0].evidence.bbox.y1 <= opt_b.blocks[0].evidence.bbox.y0
