"""
Deterministic, zero-cost provider. Every automated test in this codebase
uses ONLY this provider (or a hand-written fake) — the real
OpenAICompatibleVisionProvider is never invoked by pytest, by design (see
scripts/run_vision_experiment.py's docstring for the one exception).
"""
from models.vision import LayoutRegion, LayoutResponse, RegionBBox, TranscriptionResult


class MockVisionProvider:
    def detect_layout(self, page_image_path: str, page_number: int, context: dict) -> LayoutResponse:
        # A question_number region (hinted with the page number, since the
        # mock has no real numbering signal) followed by one whole-page
        # "unknown" content region, so downstream cropping/grouping code has
        # REAL regions (real bboxes, real crop-able coordinates, and a real
        # question to attribute them to) to exercise — not a hardcoded fake
        # bbox unrelated to the actual image. This proves the pipeline's
        # WIRING (crop extraction, ContentBlock construction, persistence)
        # end-to-end; it says nothing about detection QUALITY.
        number_region = LayoutRegion(
            region_id=f"p{page_number}_qnum",
            region_type="question_number",
            bbox=RegionBBox(x0=0.02, y0=0.02, x1=0.15, y1=0.06),
            question_number_hint=str(page_number),
            option_label_hint=None,
            contains_visual=False,
            confidence=0.5,
        )
        content_region = LayoutRegion(
            region_id=f"p{page_number}_r1",
            region_type="unknown",
            bbox=RegionBBox(x0=0.05, y0=0.08, x1=0.95, y1=0.95),
            question_number_hint=None,
            option_label_hint=None,
            contains_visual=True,
            confidence=0.5,
        )
        return LayoutResponse(
            page_number=page_number,
            regions=[number_region, content_region],
            reading_order=[number_region.region_id, content_region.region_id],
            warnings=["mock provider: one question_number region + one whole-page unknown region"],
            served_by="mock",
        )

    def transcribe_region(self, crop_image_path: str, context: dict) -> TranscriptionResult:
        # Returns unreadable=True/text=None (not a fabricated transcription
        # string) deliberately — a mock that fabricated plausible-looking
        # text would let a wiring bug (did the caller actually invoke the
        # provider and persist its result) hide behind a mock that happens
        # to look like success.
        return TranscriptionResult(
            text=None, unreadable=True,
            warnings=["mock provider: transcription not attempted"],
            served_by="mock",
        )
