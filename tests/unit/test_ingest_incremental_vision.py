"""
Phase 5 follow-up (Concurrent + Incremental Vision Processing): the actual
behavior being built, not just an implementation detail -- questions from
already-completed pages must be queryable from the database WHILE later
pages are still being processed, with the job's progress_json reflecting
how far along it is.
"""
import os
import threading

import pytest

import templates.registry as registry_module
from config import AppConfig
from db.repositories import DocumentRepository, JobRepository, QuestionRepository
from db.session import get_engine, init_db, make_session_factory, session_scope
from extraction.ingest import ingest_and_extract
from providers.mock_vision import MockVisionProvider
from templates.bootstrap import ensure_default_templates_registered, ensure_vision_layout_template_registered

UNSUPPORTED_PDF = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "unsupported", "JEE_ADV_2016-1.pdf",
)


@pytest.fixture
def session_factory(tmp_path):
    # A real file-backed DB, not ":memory:" -- an in-memory SQLite
    # connection is isolated per-thread by default (SingletonThreadPool),
    # so two different threads sharing one :memory: engine would each see
    # an EMPTY database, not the same one. This test genuinely exercises
    # cross-thread visibility (the whole point of the feature), so it needs
    # the same real-file + WAL-mode setup the actual app uses.
    engine = get_engine(str(tmp_path / "test.db"))
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def config(tmp_path):
    c = AppConfig(
        data_dir=tmp_path, db_path=tmp_path / "app.db",
        uploads_dir=tmp_path / "uploads", rendered_pages_dir=tmp_path / "rendered",
        crops_dir=tmp_path / "crops", templates_dir=tmp_path / "templates",
        render_dpi=100, vision_concurrency=3,
    )
    c.ensure_directories()
    return c


class _StaggeredProvider:
    """Page 1 returns immediately; every other page blocks until released --
    proves the test can observe a partial, in-progress state."""
    def __init__(self):
        self._release = threading.Event()
        self.page_1_done = threading.Event()

    def release_remaining_pages(self):
        self._release.set()

    def detect_layout(self, page_image_path, page_number, context):
        if page_number != 1:
            self._release.wait(timeout=10)
        result = MockVisionProvider().detect_layout(page_image_path, page_number, context)
        if page_number == 1:
            self.page_1_done.set()
        return result

    def transcribe_region(self, crop_image_path, context):
        return MockVisionProvider().transcribe_region(crop_image_path, context)


def test_page_1_questions_visible_while_later_pages_still_processing(session_factory, config, monkeypatch):
    provider = _StaggeredProvider()
    monkeypatch.setattr(registry_module, "build_vision_provider", lambda cfg: provider)

    with session_scope(session_factory) as session:
        ensure_default_templates_registered(session)
        ensure_vision_layout_template_registered(session)

    result_holder = {}

    def _run():
        result_holder["result"] = ingest_and_extract(
            UNSUPPORTED_PDF, config, session_factory, exam="JEE Advanced",
        )

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()

    assert provider.page_1_done.wait(timeout=15), "page 1 never completed"

    # Give the main ingestion thread a brief moment to persist page 1's
    # commit after signaling page_1_done (the event fires inside
    # detect_layout, slightly before the commit happens in ingest.py).
    import time
    document_id = None
    for _ in range(50):
        with session_scope(session_factory) as session:
            docs = DocumentRepository(session).list_all()
        if docs:
            document_id = docs[0].document_id
            with session_scope(session_factory) as session:
                questions = QuestionRepository(session).to_canonical(document_id)
                job = JobRepository(session).get_latest_for_document(document_id)
            if questions and job and job.status == "running":
                break
        time.sleep(0.1)

    assert document_id is not None, "document row was never created"
    assert questions, "no questions were visible while the document was still processing"
    assert job.status == "running", "job should still be running while pages 2+ are blocked"
    assert job.progress_json is not None

    provider.release_remaining_pages()
    thread.join(timeout=60)
    assert not thread.is_alive(), "ingestion did not finish after releasing remaining pages"

    with session_scope(session_factory) as session:
        final_job = JobRepository(session).get_latest_for_document(document_id)
        final_questions = QuestionRepository(session).to_canonical(document_id)

    assert final_job.status == "succeeded"
    assert len(final_questions) >= len(questions)  # more (or equal) pages' worth of questions now
