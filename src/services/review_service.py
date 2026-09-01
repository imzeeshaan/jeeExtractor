"""
All Phase 3 decision logic — no Streamlit import anywhere in this file, so
it's fully unit-testable (pytest can't drive Streamlit's script-rerun model,
so the UI files in src/ui/ stay thin wrappers around these functions).

Read-only functions take an already-open `session` (caller manages it).
Mutating functions take a `session_factory` and manage their own atomic
transaction via db.session.session_scope — an edit and its ReviewAction
audit record always commit together, never one without the other.
"""
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

from db.repositories import (
    QuestionRepository, ValidationIssueRepository, ReviewActionRepository,
)
from db.session import session_scope
from models.review import ReviewAction
from validation import rules_option_consistency, rules_stem_completeness, rules_geometry
from validation.confidence import compute_question_confidence

_REVIEWED_STATUSES = ("approved", "rejected")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_review_queue(session, document_id: str, include_reviewed: bool = False) -> list[dict]:
    """Confidence-routed queue: (1) any error/blocking issue first,
    (2) confidence ascending, (3) question number ascending. Approved/
    rejected questions are hidden unless include_reviewed=True."""
    questions = QuestionRepository(session).to_canonical(document_id)
    issue_repo = ValidationIssueRepository(session)

    rows = []
    for q in questions:
        if not include_reviewed and q.status in _REVIEWED_STATUSES:
            continue
        issues = issue_repo.list_for_question(q.question_id)
        has_error_or_blocking = any(i.severity in ("error", "blocking") for i in issues)
        rows.append({
            "question_id": q.question_id,
            "number": q.number,
            "question_type": q.question_type,
            "status": q.status,
            "confidence": q.confidence,
            "issue_count": len(issues),
            "has_error_or_blocking": has_error_or_blocking,
        })

    def sort_key(row):
        number_key = int(row["number"]) if row["number"].isdigit() else row["number"]
        return (not row["has_error_or_blocking"], row["confidence"], number_key)

    rows.sort(key=sort_key)
    return rows


def get_question_detail(session_factory, question_id: str) -> Optional[dict]:
    with session_scope(session_factory) as session:
        question = QuestionRepository(session).get(question_id)
        if question is None:
            return None
        issues = ValidationIssueRepository(session).list_for_question(question_id)
        actions = ReviewActionRepository(session).list_for_question(question_id)
        return {"question": question, "issues": issues, "review_actions": actions}


def _crops_root(config, document_id: str) -> str:
    """Reconstructs the same path extraction.ingest.ingest_and_extract used
    when it ran the adapter — config.crops_dir/{document_id} — since that
    path isn't itself persisted anywhere queryable."""
    return os.path.join(str(config.crops_dir), document_id)


def recheck_question(session_factory, config, question_id: str) -> None:
    """Re-runs the three per-question validation rule modules (option
    consistency, stem completeness, geometry — the ones validation.engine
    already runs per-question, not the whole-document ones like image
    conservation/answer coverage/sequence, which need the full question list
    and are unaffected by a single-question text/type edit) against the
    question's CURRENT state, replaces its ValidationIssue rows, and
    recomputes confidence. This is the honest, scoped-down "field-level
    reprocessing" — see the approved Phase 3 plan for why a fake
    re-extraction button was rejected."""
    with session_scope(session_factory) as session:
        q_repo = QuestionRepository(session)
        question = q_repo.get(question_id)
        if question is None:
            return
        issue_repo = ValidationIssueRepository(session)
        document_level_issues = [
            i for i in issue_repo.list_for_document(question.document_id) if i.question_id is None
        ]

        crops_root = _crops_root(config, question.document_id)
        new_issues = []
        new_issues += rules_option_consistency.check_option_consistency(question, question.document_id)
        new_issues += rules_stem_completeness.check_stem_completeness(question, question.document_id)
        new_issues += rules_geometry.check_geometry(question, crops_root, question.document_id)

        issue_repo.replace_for_question(question_id, new_issues)
        q_repo.update_validation_issue_ids(question_id, [i.issue_id for i in new_issues])
        new_confidence = compute_question_confidence(question, new_issues, document_level_issues)
        q_repo.update_confidence(question_id, new_confidence)


def _record_action(session, document_id, question_id, action_type, field_path,
                    previous_value, new_value, actor=None, reason=None):
    ReviewActionRepository(session).record(ReviewAction(
        action_id=str(uuid.uuid4()),
        document_id=document_id,
        question_id=question_id,
        action_type=action_type,
        field_path=field_path,
        previous_value=previous_value,
        new_value=new_value,
        reason=reason,
        actor=actor,
        created_at=_now_iso(),
    ))


def edit_stem_text(session_factory, config, question_id: str, block_id: str, new_text: str,
                    actor: str = None, reason: str = None) -> None:
    with session_scope(session_factory) as session:
        q_repo = QuestionRepository(session)
        previous = q_repo.update_block_text(block_id, new_text)
        question = q_repo.get(question_id)
        _record_action(session, question.document_id, question_id, "edit_stem_text",
                        f"stem_blocks.{block_id}.text", previous, new_text, actor, reason)
    recheck_question(session_factory, config, question_id)


def edit_option_text(session_factory, config, question_id: str, block_id: str, new_text: str,
                      actor: str = None, reason: str = None) -> None:
    with session_scope(session_factory) as session:
        q_repo = QuestionRepository(session)
        previous = q_repo.update_block_text(block_id, new_text)
        question = q_repo.get(question_id)
        _record_action(session, question.document_id, question_id, "edit_option_text",
                        f"options.blocks.{block_id}.text", previous, new_text, actor, reason)
    recheck_question(session_factory, config, question_id)


def change_question_type(session_factory, config, question_id: str, new_type: str,
                          actor: str = None, reason: str = None) -> None:
    with session_scope(session_factory) as session:
        q_repo = QuestionRepository(session)
        previous = q_repo.update_question_type(question_id, new_type)
        question = q_repo.get(question_id)
        _record_action(session, question.document_id, question_id, "change_question_type",
                        "question_type", previous, new_type, actor, reason)
    recheck_question(session_factory, config, question_id)


def mark_block_status(session_factory, question_id: str, block_id: str, new_status: str,
                       actor: str = None, reason: str = None) -> None:
    with session_scope(session_factory) as session:
        q_repo = QuestionRepository(session)
        previous = q_repo.update_block_status(block_id, new_status)
        question = q_repo.get(question_id)
        _record_action(session, question.document_id, question_id, "mark_block_status",
                        f"blocks.{block_id}.status", previous, new_status, actor, reason)


def approve_question(session_factory, question_id: str, actor: str = None, reason: str = None) -> None:
    with session_scope(session_factory) as session:
        q_repo = QuestionRepository(session)
        previous = q_repo.update_question_status(question_id, "approved")
        question = q_repo.get(question_id)
        _record_action(session, question.document_id, question_id, "approve_question",
                        "status", previous, "approved", actor, reason)


def reject_question(session_factory, question_id: str, actor: str = None, reason: str = None) -> None:
    with session_scope(session_factory) as session:
        q_repo = QuestionRepository(session)
        previous = q_repo.update_question_status(question_id, "rejected")
        question = q_repo.get(question_id)
        _record_action(session, question.document_id, question_id, "reject_question",
                        "status", previous, "rejected", actor, reason)
