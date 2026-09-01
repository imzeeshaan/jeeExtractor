"""
The ValidationIssue model (spec §16.8) — supersedes the throwaway
{"question_number", "message", "severity"} dicts that
legacy_mathongo_adapter.py's _notes_to_issues() still produces (that stays
as-is, it's the adapter's own internal note-classification); this is the
real, persisted structured issue representation produced by the separate
"validate_questions" stage in extraction/ingest.py.
"""
from typing import Literal, Optional

from pydantic import BaseModel


class ValidationIssue(BaseModel):
    issue_id: str
    document_id: str
    page_number: Optional[int] = None
    question_id: Optional[str] = None
    field_path: Optional[str] = None
    rule_code: str
    severity: Literal["info", "warning", "error", "blocking"]
    message: str
    evidence_path: Optional[str] = None
    resolved: bool = False
    resolution_note: Optional[str] = None
