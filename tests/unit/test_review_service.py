"""
Phase 3: review_service decision logic, isolated in-memory SQLite per test
(never data/app.db), matching test_repositories.py's pattern.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
from PIL import Image

from db.repositories import DocumentRepository, QuestionRepository, ValidationIssueRepository
from db.session import get_engine, init_db, make_session_factory, session_scope
from models.common import BoundingBox, SourceEvidence
from models.documents import Document
from models.questions import legacy_dict_to_question
from models.validation import ValidationIssue
from services import review_service


class FakeConfig:
    """Minimal stand-in for AppConfig — review_service only ever reads
    .crops_dir off the config it's given."""
    def __init__(self, crops_dir):
        self.crops_dir = crops_dir


@pytest.fixture
def session_factory():
    engine = get_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)


def _make_evidence(rect_key, page_number, rect=None):
    return SourceEvidence(
        page_number=page_number,
        bbox=BoundingBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0),
        extraction_method="legacy_deterministic",
    )


def _sample_legacy_question(**overrides):
    base = {
        "question_number": 5,
        "question_type": "mcq",
        "stem_text": "Sample stem text?",
        "stem_snippet": "images/q5_stem.png",
        "stem_images": [],
        "options": [
            {"label": "1", "text": "(1) A", "snippet": "images/q5_opt1.png", "images": []},
            {"label": "2", "text": "(2) B", "snippet": "images/q5_opt2.png", "images": []},
            {"label": "3", "text": "(3) C", "snippet": "images/q5_opt3.png", "images": []},
            {"label": "4", "text": "(4) D", "snippet": "images/q5_opt4.png", "images": []},
        ],
        "answer": "2",
    }
    base.update(overrides)
    return base


def _seed_document_and_questions(session_factory, questions_overrides, document_id="doc-1"):
    document = Document(
        document_id=document_id, filename="sample.pdf", sha256="a" * 64,
        page_count=1, file_size_bytes=1, uploaded_at=datetime.now(timezone.utc),
        storage_path="/tmp/sample.pdf",
    )
    canonical_questions = []
    for overrides in questions_overrides:
        legacy = _sample_legacy_question(**overrides)
        canonical_questions.append(legacy_dict_to_question(
            legacy, document_id=document_id, page_start=1, page_end=1, make_evidence=_make_evidence,
        ))
    with session_scope(session_factory) as session:
        DocumentRepository(session).create(document)
        QuestionRepository(session).bulk_save(canonical_questions)
    return document, canonical_questions


def test_get_review_queue_orders_error_first_then_confidence_ascending(session_factory):
    document, questions = _seed_document_and_questions(session_factory, [
        {"question_number": 1},
        {"question_number": 2},
        {"question_number": 3},
    ])
    q1, q2, q3 = questions

    with session_scope(session_factory) as session:
        issue_repo = ValidationIssueRepository(session)
        # q1: low confidence, warning only
        q_repo = QuestionRepository(session)
        q_repo.update_confidence(q1.question_id, 0.5)
        issue_repo.bulk_save([ValidationIssue(
            issue_id=str(uuid.uuid4()), document_id=document.document_id, question_id=q1.question_id,
            rule_code="STEM_NO_TERMINAL_PUNCTUATION", severity="warning", message="x",
        )])
        # q2: high confidence but has an ERROR issue -> must sort first regardless
        q_repo.update_confidence(q2.question_id, 0.95)
        issue_repo.bulk_save([ValidationIssue(
            issue_id=str(uuid.uuid4()), document_id=document.document_id, question_id=q2.question_id,
            rule_code="OPT_DUPLICATE_LABEL", severity="error", message="x",
        )])
        # q3: clean, highest confidence
        q_repo.update_confidence(q3.question_id, 1.0)

    with session_scope(session_factory) as session:
        queue = review_service.get_review_queue(session, document.document_id)

    assert [row["number"] for row in queue] == ["2", "1", "3"]


def test_get_review_queue_excludes_and_includes_reviewed(session_factory):
    document, questions = _seed_document_and_questions(session_factory, [
        {"question_number": 1}, {"question_number": 2},
    ])
    with session_scope(session_factory) as session:
        QuestionRepository(session).update_question_status(questions[0].question_id, "approved")

    with session_scope(session_factory) as session:
        default_queue = review_service.get_review_queue(session, document.document_id)
        full_queue = review_service.get_review_queue(session, document.document_id, include_reviewed=True)

    assert [row["number"] for row in default_queue] == ["2"]
    assert {row["number"] for row in full_queue} == {"1", "2"}


def test_approve_question_transitions_status_and_records_action(session_factory):
    document, questions = _seed_document_and_questions(session_factory, [{"question_number": 1}])
    q = questions[0]

    review_service.approve_question(session_factory, q.question_id, actor="tester")

    with session_scope(session_factory) as session:
        persisted = QuestionRepository(session).get(q.question_id)
        actions = review_service.get_question_detail(session_factory, q.question_id)["review_actions"]

    assert persisted.status == "approved"
    assert len(actions) == 1
    assert actions[0].action_type == "approve_question"
    assert actions[0].previous_value == "draft"
    assert actions[0].new_value == "approved"
    assert actions[0].actor == "tester"


def test_reject_question_same_shape(session_factory):
    document, questions = _seed_document_and_questions(session_factory, [{"question_number": 1}])
    q = questions[0]

    review_service.reject_question(session_factory, q.question_id)

    detail = review_service.get_question_detail(session_factory, q.question_id)
    assert detail["question"].status == "rejected"
    assert detail["review_actions"][0].previous_value == "draft"
    assert detail["review_actions"][0].new_value == "rejected"


def test_edit_stem_text_updates_block_and_records_before_after_values(session_factory, tmp_path):
    document, questions = _seed_document_and_questions(session_factory, [{"question_number": 1}])
    q = questions[0]
    config = FakeConfig(crops_dir=tmp_path)

    review_service.edit_stem_text(
        session_factory, config, q.question_id, q.stem_blocks[0].block_id, "Fixed stem text?",
    )

    detail = review_service.get_question_detail(session_factory, q.question_id)
    assert detail["question"].stem_blocks[0].text == "Fixed stem text?"
    action = [a for a in detail["review_actions"] if a.action_type == "edit_stem_text"][0]
    assert action.previous_value == "Sample stem text?"
    assert action.new_value == "Fixed stem text?"


def test_change_question_type_updates_and_records_action(session_factory, tmp_path):
    document, questions = _seed_document_and_questions(session_factory, [{"question_number": 1}])
    q = questions[0]
    config = FakeConfig(crops_dir=tmp_path)

    review_service.change_question_type(session_factory, config, q.question_id, "multiple_correct")

    detail = review_service.get_question_detail(session_factory, q.question_id)
    assert detail["question"].question_type == "multiple_correct"
    action = detail["review_actions"][0]
    assert action.previous_value == "single_correct"
    assert action.new_value == "multiple_correct"


def test_mark_block_status_updates_and_records_action(session_factory):
    document, questions = _seed_document_and_questions(session_factory, [{"question_number": 1}])
    q = questions[0]

    review_service.mark_block_status(session_factory, q.question_id, q.stem_blocks[0].block_id, "unreadable")

    detail = review_service.get_question_detail(session_factory, q.question_id)
    assert detail["question"].stem_blocks[0].status == "unreadable"
    action = detail["review_actions"][0]
    assert action.action_type == "mark_block_status"
    assert action.previous_value == "extracted"
    assert action.new_value == "unreadable"


def test_recheck_question_replaces_issues_and_recomputes_confidence(session_factory, tmp_path):
    document, questions = _seed_document_and_questions(
        session_factory, [{"question_number": 1, "stem_text": "The wavelength of the"}],
    )
    q = questions[0]
    config = FakeConfig(crops_dir=tmp_path)

    # recheck_question's geometry rule resolves clean_crop_path against
    # crops_root on disk — create real placeholder images so GEOM_CROP_*
    # doesn't fire and pollute this test's confidence assertion.
    images_dir = tmp_path / document.document_id / "images"
    images_dir.mkdir(parents=True)
    for name in ("q5_stem.png", "q5_opt1.png", "q5_opt2.png", "q5_opt3.png", "q5_opt4.png"):
        # a checkerboard, not a solid color — a flat-color image has zero
        # pixel stddev and would itself trip GEOM_CROP_BLANK
        img = Image.new("RGB", (100, 100), color=(255, 255, 255))
        for y in range(0, 100, 10):
            for x in range(0, 100, 10):
                if (x // 10 + y // 10) % 2 == 0:
                    img.paste((0, 0, 0), (x, y, x + 10, y + 10))
        img.save(images_dir / name)

    # seed the exact stale issue a fresh ingest would have produced for this broken stem
    with session_scope(session_factory) as session:
        ValidationIssueRepository(session).bulk_save([ValidationIssue(
            issue_id=str(uuid.uuid4()), document_id=document.document_id, question_id=q.question_id,
            rule_code="STEM_TRAILING_STOPWORD", severity="warning", message="stale",
        )])
        QuestionRepository(session).update_confidence(q.question_id, 0.9)

    review_service.edit_stem_text(
        session_factory, config, q.question_id, q.stem_blocks[0].block_id,
        "What is the wavelength of visible light?",
    )

    detail = review_service.get_question_detail(session_factory, q.question_id)
    rule_codes = {i.rule_code for i in detail["issues"]}
    assert "STEM_TRAILING_STOPWORD" not in rule_codes
    assert detail["question"].confidence == 1.0
