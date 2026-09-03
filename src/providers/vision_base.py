"""
Protocol both mock and real vision providers implement. context's shape is
deliberately different per method — detect_layout needs page-level framing,
transcribe_region needs field-level framing — reusing one context shape for
both would either starve one call of what it needs or pollute the other with
unused keys.
"""
from typing import Protocol

from models.vision import LayoutResponse, TranscriptionResult


class VisionProvider(Protocol):
    def detect_layout(self, page_image_path: str, page_number: int, context: dict) -> LayoutResponse:
        """context keys: template_rules (dict | None), prior_layout
        (list[dict] | None — a previous provider's regions attributed to one
        question, given when this call is an ESCALATION re-run so the second
        provider can verify/correct rather than detect blind),
        escalation_reason (str | None — which criterion triggered this
        re-run, purely informational for the prompt)."""
        ...

    def transcribe_region(self, crop_image_path: str, context: dict) -> TranscriptionResult:
        """context keys: field_kind (Literal["question_stem",
        "option_content", ...] — what part of a question this crop
        represents), question_number (str | None), option_label (str | None),
        prior_text (str | None — text a prior pass produced, given to the
        model as "verify or correct this against the image" rather than
        transcribing blind)."""
        ...
