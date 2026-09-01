"""
Phase 1: repository CRUD round-trip, against an isolated in-memory SQLite
database per test (never the real data/app.db).
"""
import uuid
from datetime import datetime, timezone

import pytest

from db.repositories import DocumentRepository, QuestionRepository
from db.session import get_engine, init_db, make_session_factory, session_scope
from models.common import BoundingBox, SourceEvidence
from models.documents import Document
from models.questions import legacy_dict_to_question


@pytest.fixture
def session_factory():
    engine = get_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)


def _sample_document():
    return Document(
        document_id=str(uuid.uuid4()),
        filename="sample.pdf",
        sha256="a" * 64,
        page_count=15,
        file_size_bytes=12345,
        uploaded_at=datetime.now(timezone.utc),
        exam="JEE Main",
        year=2012,
        storage_path="/tmp/sample.pdf",
    )


def _make_evidence(rect_key, page_number, rect=None):
    return SourceEvidence(
        page_number=page_number,
        bbox=BoundingBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0),
        extraction_method="legacy_deterministic",
    )


def test_document_create_and_get_by_sha256(session_factory):
    doc = _sample_document()
    with session_scope(session_factory) as session:
        DocumentRepository(session).create(doc)

    with session_scope(session_factory) as session:
        found = DocumentRepository(session).get_by_sha256(doc.sha256)
        assert found is not None
        assert found.document_id == doc.document_id
        assert found.filename == "sample.pdf"
        assert found.exam == "JEE Main"


def test_document_get_by_sha256_returns_none_when_absent(session_factory):
    with session_scope(session_factory) as session:
        assert DocumentRepository(session).get_by_sha256("nonexistent") is None


def test_question_bulk_save_and_to_canonical_round_trip(session_factory):
    doc = _sample_document()
    legacy = {
        "question_number": 7,
        "question_type": "mcq",
        "stem_text": "What is 2+2?",
        "stem_snippet": "images/q7_stem.png",
        "stem_images": [],
        "options": [
            {"label": "1", "text": "(1) 3", "snippet": "images/q7_opt1.png", "images": []},
            {"label": "2", "text": "(2) 4", "snippet": "images/q7_opt2.png", "images": []},
            {"label": "3", "text": "(3) 5", "snippet": "images/q7_opt3.png", "images": []},
            {"label": "4", "text": "(4) 6", "snippet": "images/q7_opt4.png", "images": []},
        ],
        "answer": "2",
    }
    canonical = legacy_dict_to_question(legacy, document_id=doc.document_id, page_start=2, page_end=2,
                                         make_evidence=_make_evidence)

    with session_scope(session_factory) as session:
        DocumentRepository(session).create(doc)
        QuestionRepository(session).bulk_save([canonical])

    with session_scope(session_factory) as session:
        loaded = QuestionRepository(session).to_canonical(doc.document_id)

    assert len(loaded) == 1
    q = loaded[0]
    assert q.number == "7"
    assert q.question_type == "single_correct"
    assert q.answer == "2"
    assert len(q.options) == 4
    assert [o.label for o in q.options] == ["1", "2", "3", "4"]
    assert q.stem_blocks[0].text == "What is 2+2?"
    assert q.options[1].blocks[0].text == "(2) 4"


def test_question_bulk_save_numerical_question_has_no_options(session_factory):
    doc = _sample_document()
    legacy = {
        "question_number": 21,
        "question_type": "numerical",
        "stem_text": "Find the value.",
        "stem_snippet": "images/q21_stem.png",
        "stem_images": [],
        "options": [],
        "answer": "10",
    }
    canonical = legacy_dict_to_question(legacy, document_id=doc.document_id, page_start=4, page_end=4,
                                         make_evidence=_make_evidence)

    with session_scope(session_factory) as session:
        DocumentRepository(session).create(doc)
        QuestionRepository(session).bulk_save([canonical])

    with session_scope(session_factory) as session:
        loaded = QuestionRepository(session).to_canonical(doc.document_id)

    assert len(loaded) == 1
    assert loaded[0].question_type == "numerical"
    assert loaded[0].options == []
    assert loaded[0].answer == "10"
