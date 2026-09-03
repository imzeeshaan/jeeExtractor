"""
Layout-detection and repair-transcription response shapes (spec §14).
Pure Pydantic — no PDF/DB/provider imports, mirroring models/validation.py's
isolation. These are the ONLY shapes a VisionProvider implementation returns;
providers never construct ContentBlock/Question directly (that conversion is
vision_layout_adapter.py's job, so a provider swap never touches persistence).
"""
from typing import Literal, Optional

from pydantic import BaseModel

RegionType = Literal[
    "header", "footer", "subject_heading", "section_heading", "instructions",
    "shared_passage", "question_number", "question_stem", "option_label",
    "option_content", "diagram", "equation", "table", "answer_key",
    "page_number", "unknown",
]


class RegionBBox(BaseModel):
    """Deliberately separate from models.common.BoundingBox — a permissive
    shape at the provider boundary. BoundingBox's own validator would raise
    on a hallucinated/malformed region; here the adapter can catch and drop
    a bad region instead of crashing the whole page."""
    x0: float
    y0: float
    x1: float
    y1: float


class LayoutRegion(BaseModel):
    region_id: str
    region_type: RegionType
    bbox: RegionBBox
    question_number_hint: Optional[str] = None
    option_label_hint: Optional[str] = None
    contains_visual: bool = False
    confidence: float


class LayoutResponse(BaseModel):
    page_number: int
    regions: list[LayoutRegion] = []
    reading_order: list[str] = []  # region_id values, in order
    warnings: list[str] = []
    # Which underlying provider actually produced this response (e.g.
    # "deepseek", "openai", "mock") — set by the provider that built the
    # object, not by whatever wrapper (e.g. FallbackVisionProvider) happened
    # to be called. Real bug this fixes: vision_layout_adapter.py used to
    # record type(self._provider).__name__ as evidence.provider_name, which
    # is ALWAYS "FallbackVisionProvider" regardless of whether the primary
    # succeeded or the call fell over to the secondary — making it
    # impossible to tell which provider actually served any given region.
    served_by: Optional[str] = None


class TranscriptionResult(BaseModel):
    text: Optional[str] = None  # None if the model marks the region unreadable
    # Populated only when the crop contains mathematical notation (fractions,
    # exponents, roots, etc.) — None otherwise, same "None means not
    # applicable" convention as text. Plain-text extraction (extractor.py)
    # can't preserve this structure since it flattens 2D layout into a
    # single line; this is a second, on-demand vision read specifically for
    # recovering it.
    latex: Optional[str] = None
    unreadable: bool = False
    warnings: list[str] = []
    served_by: Optional[str] = None  # see LayoutResponse.served_by
