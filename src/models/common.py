"""
Shapes shared across the canonical data model. Per the approved plan,
BoundingBox and SourceEvidence are implemented exactly as specified;
SharedContext is a deliberate minimal stub (no passage/group semantics yet —
that's Phase 2+ territory) so Question.shared_context_id has something real
to reference without over-building.
"""
from typing import Literal, Optional

from pydantic import BaseModel, model_validator


class BoundingBox(BaseModel):
    """Normalized top-left coordinates in the range 0 to 1.
    x0, y0 = top-left corner; x1, y1 = bottom-right corner."""
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def validate_bounds(self):
        assert 0 <= self.x0 < self.x1 <= 1, f"invalid x bounds: x0={self.x0}, x1={self.x1}"
        assert 0 <= self.y0 < self.y1 <= 1, f"invalid y bounds: y0={self.y0}, y1={self.y1}"
        return self


class SourceEvidence(BaseModel):
    page_number: int
    bbox: BoundingBox
    extraction_method: Literal[
        "legacy_deterministic",
        "template_deterministic",
        "pdf_text",
        "ocr",
        "vision_layout",
        "vision_transcription",
        "user",
    ]
    source_crop_path: Optional[str] = None
    provider_name: Optional[str] = None
    provider_model: Optional[str] = None
    prompt_version: Optional[str] = None


class SharedContext(BaseModel):
    """Minimal stub — full passage/shared-diagram semantics are Phase 2+.
    Exists now only so Question.shared_context_id type-checks against
    something real; no dedicated persistence table in Phase 1."""
    shared_context_id: str
    document_id: str
