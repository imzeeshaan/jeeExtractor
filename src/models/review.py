"""
ReviewAction (spec §20.3, adapted to what this codebase can actually
correct today — see HANDOFF.md / the approved Phase 3 plan for the full
reasoning on why "reassign visual"/"split"/"merge"/"attach passage" are
deliberately not in this enum: they all need bbox-boundary editing UI,
which the spec itself permits skipping for a Streamlit-only stack).

Every correction records both the previous and new value — corrections
never overwrite evidence silently; the original extraction and every
ReviewAction stay in the DB regardless of a question's current status.
"""
from typing import Literal, Optional

from pydantic import BaseModel

ReviewActionType = Literal[
    "edit_stem_text", "edit_option_text", "change_question_type",
    "mark_block_status", "approve_question", "reject_question",
]


class ReviewAction(BaseModel):
    action_id: str
    document_id: str
    question_id: str
    action_type: ReviewActionType
    field_path: str
    previous_value: Optional[str] = None
    new_value: Optional[str] = None
    reason: Optional[str] = None
    actor: Optional[str] = None
    source_page: Optional[int] = None
    source_bbox_json: Optional[str] = None
    is_template_example: bool = False
    created_at: str
