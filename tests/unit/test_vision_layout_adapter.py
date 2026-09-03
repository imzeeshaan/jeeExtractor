"""
Phase 5 end-to-end: the real scanned JEE Advanced fixture, run through
ingest_and_extract with the vision-layout template registered and
AppConfig.vision_provider left at its default "mock" (zero real API spend).
Proves the crop/persistence/validation/escalation-stage wiring, not
detection quality (MockVisionProvider's single whole-page region is the only
signal available).
"""
import os

import pytest

from config import AppConfig
from db.repositories import DocumentRepository, QuestionRepository
from db.schema import ProcessingJobRow, StageRunRow
from db.session import get_engine, init_db, make_session_factory, session_scope
from extraction.ingest import ingest_and_extract, UnmatchedDocumentResult
from templates.bootstrap import ensure_default_templates_registered, ensure_vision_layout_template_registered

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
        render_dpi=100,  # keep the 29-page render fast for a test
    )
    c.ensure_directories()
    return c


def test_scanned_paper_matches_vision_template_and_extracts_with_mock_provider(session_factory, config):
    assert config.vision_provider == "mock"  # zero real API spend, structurally guaranteed

    with session_scope(session_factory) as session:
        ensure_default_templates_registered(session)
        ensure_vision_layout_template_registered(session)

    result = ingest_and_extract(UNSUPPORTED_PDF, config, session_factory, exam="JEE Advanced")

    assert not isinstance(result, UnmatchedDocumentResult)
    assert result.metrics["question_count"] >= 1

    with session_scope(session_factory) as session:
        document = DocumentRepository(session).get(result.document_id)
        assert document.template_id == "jee_advanced_vision_layout"

        questions = QuestionRepository(session).to_canonical(result.document_id)
        assert len(questions) >= 1
        for q in questions:
            assert q.extraction_mode == "vision_layout"
            assert q.question_type == "unknown"
            for block in q.stem_blocks:
                assert block.text is None
                assert block.status == "needs_review"
                assert os.path.isfile(os.path.join(str(config.crops_dir), result.document_id, block.clean_crop_path))

        job_row = session.query(ProcessingJobRow).filter_by(document_id=result.document_id).one()
        assert job_row.status == "succeeded"

        validate_stage = session.query(StageRunRow).filter_by(
            job_id=job_row.id, stage_name="validate_questions").one()
        import json
        metrics = json.loads(validate_stage.metrics_json)
        assert metrics["trust_tier"] in ("MEDIUM", "LOW", "NONE")  # never HIGH — spec §17

        escalation_stage = session.query(StageRunRow).filter_by(
            job_id=job_row.id, stage_name="vision_escalation").one_or_none()
        assert escalation_stage is not None
