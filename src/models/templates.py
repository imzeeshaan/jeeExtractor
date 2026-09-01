"""
Template / TemplateVersion (spec §11, §18) — Phase 4's core new model.

Deliberate scope decision (see the approved Phase 4 plan): a TemplateVersion
is metadata + fingerprint rules + a pointer (adapter_ref) to which EXISTING
Python adapter class to invoke — not a generic declarative-rule interpreter
that replaces extractor.py's hardcoded parsing logic. MatchSignature only
has fields the fingerprint step can cheaply compute from a PDF; it does not
carry layout/visual_rules/validation_policy DSL fields from the spec's fuller
vision, since nothing in this phase reads them.
"""
from datetime import datetime
from typing import Optional, Literal

from pydantic import BaseModel

TemplateKind = Literal["deterministic_text", "hybrid_layout", "vision_layout"]
TemplateStatus = Literal["candidate", "draft", "validated", "monitored", "deprecated"]


class MatchSignature(BaseModel):
    requires_text_layer: bool = True
    branding_strings: list[str]
    question_marker_pattern: str
    option_marker_pattern: str
    answer_key_marker: str
    min_question_marker_hits: int = 10


class Template(BaseModel):
    template_id: str
    name: str
    document_family: str
    created_at: datetime


class TemplateVersion(BaseModel):
    template_id: str
    version: int
    kind: TemplateKind
    status: TemplateStatus
    adapter_ref: str
    match_signature: MatchSignature
    baseline_score: Optional[float] = None
    run_count: int = 0
    created_at: datetime
