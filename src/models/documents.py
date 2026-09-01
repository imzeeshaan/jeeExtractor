"""
Document-intake-side canonical models: the uploaded PDF itself, its pages
(with deterministic classification computed but not yet acted on), and the
processing job/stage-run records that track one ingestion run.
"""
from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class Document(BaseModel):
    document_id: str
    filename: str
    sha256: str
    page_count: int
    file_size_bytes: int
    uploaded_at: datetime
    exam: Optional[str] = None
    year: Optional[int] = None
    shift: Optional[str] = None
    publisher: Optional[str] = None
    source_note: Optional[str] = None
    storage_path: str
    # Set once template matching runs (Phase 4) — None until then, and
    # stays None if no template matched (an "unmatched" document).
    # Parallel to Question.template_id/template_version, and queryable
    # directly rather than only recoverable by parsing a stage_runs
    # metrics_json blob.
    template_id: Optional[str] = None
    template_version: Optional[int] = None


class Page(BaseModel):
    page_id: str
    document_id: str
    page_number: int  # 1-indexed
    width_pt: float
    height_pt: float
    rotation: int = 0
    page_type: Literal["text_based", "scanned", "mixed"]
    char_count: int
    text_bbox_coverage: float
    image_coverage: float
    has_full_page_image: bool
    drawing_count: int
    rendered_path: Optional[str] = None
    render_hash: Optional[str] = None


class ProcessingJob(BaseModel):
    job_id: str
    document_id: str
    # Deliberately narrow for Phase 1 — the only job type that exists is
    # running the legacy adapter. Extended in later phases (template
    # matching, vision fallback, etc.), not pre-built here.
    job_type: Literal["legacy_mathongo_extraction"]
    # "unmatched" (Phase 4): the job ran to completion but no template
    # matched the document well enough to run any adapter — nothing errored
    # (not "failed") and nothing was extracted (not "succeeded").
    status: Literal["pending", "running", "succeeded", "failed", "unmatched"]
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None


class StageRun(BaseModel):
    stage_run_id: str
    job_id: str
    stage_name: str
    status: Literal["pending", "running", "succeeded", "failed"]
    metrics: dict = {}
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
