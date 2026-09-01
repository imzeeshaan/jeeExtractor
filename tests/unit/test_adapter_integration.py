"""
Phase 1: end-to-end integration test for LegacyMathonGoAdapter over all 8
fixture papers — isolated temp DB/dirs per test, never touching data/app.db.
"""
import json
import os
import uuid
from datetime import datetime, timezone

import pytest

from db.repositories import DocumentRepository, QuestionRepository
from db.session import get_engine, init_db, make_session_factory, session_scope
from extraction.legacy_mathongo_adapter import LegacyMathonGoAdapter
from models.documents import Document
from models.questions import question_to_legacy_dict

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "pdfs")
GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "..", "golden")

PAPER_IDS = [
    "2012_may07", "2012_may12", "2012_may19", "2012_may26", "2012_offline",
    "2019_jan09", "2019_jan12", "2020_jan07",
]


@pytest.fixture
def session_factory():
    engine = get_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)


@pytest.mark.parametrize("paper_id", PAPER_IDS)
def test_adapter_produces_valid_canonical_models_with_full_metrics(paper_id, tmp_path, session_factory):
    pdf_path = os.path.join(FIXTURES_DIR, f"{paper_id}.pdf")
    out_dir = tmp_path / "crops"
    out_dir.mkdir()

    document = Document(
        document_id=str(uuid.uuid4()),
        filename=f"{paper_id}.pdf",
        sha256="0" * 64,
        page_count=1,
        file_size_bytes=1,
        uploaded_at=datetime.now(timezone.utc),
        storage_path=str(pdf_path),
    )

    result = LegacyMathonGoAdapter().run(document, str(pdf_path), str(out_dir))

    # every canonical model already validated on construction (Pydantic) —
    # the assertion here is just that we got a non-empty, sane result
    assert len(result.questions) > 0
    assert result.metrics["answer_coverage"] == 1.0, (
        f"{paper_id}: adapter metrics answer_coverage != 1.0: {result.metrics}"
    )
    assert result.metrics["images_conserved"] is True, (
        f"{paper_id}: adapter metrics image conservation violated: {result.metrics}"
    )

    with session_scope(session_factory) as session:
        DocumentRepository(session).create(document)
        QuestionRepository(session).bulk_save(result.questions)

    with session_scope(session_factory) as session:
        persisted = QuestionRepository(session).to_canonical(document.document_id)

    assert len(persisted) == len(result.questions)
    assert {q.number for q in persisted} == {q.number for q in result.questions}


@pytest.mark.parametrize("paper_id", PAPER_IDS)
def test_legacy_round_trip_matches_golden_questions_json(paper_id, tmp_path):
    """The canonical layer must not lose anything the legacy UI/export needs:
    question_to_legacy_dict(adapter_output) should match the Phase 0 golden
    questions.json on every field the legacy shape carries."""
    pdf_path = os.path.join(FIXTURES_DIR, f"{paper_id}.pdf")
    out_dir = tmp_path / "crops"
    out_dir.mkdir()

    document = Document(
        document_id=str(uuid.uuid4()),
        filename=f"{paper_id}.pdf",
        sha256="0" * 64,
        page_count=1,
        file_size_bytes=1,
        uploaded_at=datetime.now(timezone.utc),
        storage_path=str(pdf_path),
    )
    result = LegacyMathonGoAdapter().run(document, str(pdf_path), str(out_dir))

    with open(os.path.join(GOLDEN_DIR, paper_id, "questions.json")) as f:
        golden_questions = {q["question_number"]: q for q in json.load(f)}

    for canonical_q in result.questions:
        back = question_to_legacy_dict(canonical_q)
        golden = golden_questions[back["question_number"]]

        assert back["question_type"] == golden["question_type"], (
            f"{paper_id} Q{back['question_number']}: question_type mismatch"
        )
        assert back["stem_text"] == golden["stem_text"], (
            f"{paper_id} Q{back['question_number']}: stem_text mismatch"
        )
        assert back["answer"] == golden["answer"], (
            f"{paper_id} Q{back['question_number']}: answer mismatch"
        )
        assert [o["text"] for o in back["options"]] == [o["text"] for o in golden["options"]], (
            f"{paper_id} Q{back['question_number']}: option text mismatch"
        )
