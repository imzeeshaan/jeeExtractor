"""
Phase 4 exit condition, second half: "an unknown paper is identified as
unknown." Uses the real JEE Advanced PDF (confirmed zero extractable text
across all 29 pages, per HANDOFF.md §4) — not a synthetic stand-in.
"""
import json
import os

import pytest

from config import AppConfig
from db.repositories import QuestionRepository, DocumentRepository
from db.schema import ProcessingJobRow, StageRunRow
from db.session import get_engine, init_db, make_session_factory, session_scope
from extraction.ingest import ingest_and_extract, UnmatchedDocumentResult
from templates.bootstrap import ensure_default_templates_registered

UNSUPPORTED_PDF = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "unsupported", "JEE_ADV_2016-1.pdf",
)


@pytest.fixture
def session_factory():
    engine = get_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def config(tmp_path):
    c = AppConfig(
        data_dir=tmp_path, db_path=tmp_path / "app.db",
        uploads_dir=tmp_path / "uploads", rendered_pages_dir=tmp_path / "rendered",
        crops_dir=tmp_path / "crops", templates_dir=tmp_path / "templates",
        render_dpi=150,
    )
    c.ensure_directories()
    return c


def test_unmatched_paper_produces_no_questions_and_unmatched_job_status(session_factory, config):
    with session_scope(session_factory) as session:
        ensure_default_templates_registered(session)

    result = ingest_and_extract(UNSUPPORTED_PDF, config, session_factory, exam="JEE Advanced", year=2016)

    assert isinstance(result, UnmatchedDocumentResult)
    assert len(result.candidates) == 1
    assert result.candidates[0].fingerprint_score < 0.65

    with session_scope(session_factory) as session:
        document = DocumentRepository(session).get(result.document_id)
        assert document.template_id is None
        assert document.template_version is None

        job_row = session.query(ProcessingJobRow).filter_by(document_id=result.document_id).one()
        assert job_row.status == "unmatched"
        assert job_row.completed_at is not None
        assert job_row.error_message is None  # nothing errored — this is not a failure

        stage_row = session.query(StageRunRow).filter_by(job_id=job_row.id, stage_name="match_template").one()
        metrics = json.loads(stage_row.metrics_json)
        assert metrics["matched"] is False
        assert metrics["candidates"][0]["score"] < 0.65
        assert len(metrics["candidates"][0]["reasons"]) > 0

        questions = QuestionRepository(session).to_canonical(result.document_id)
        assert questions == []
