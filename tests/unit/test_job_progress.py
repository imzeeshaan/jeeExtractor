"""
Phase 5 follow-up: JobRepository.update_progress/get_latest_for_document —
the single source of truth any page/rerun queries for "how far along is
this job", decoupled from whatever thread is actually running it.
"""
import json
from datetime import datetime, timezone

import pytest

from db.repositories import DocumentRepository, JobRepository
from db.session import get_engine, init_db, make_session_factory, session_scope
from models.documents import Document, ProcessingJob


@pytest.fixture
def session_factory():
    engine = get_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)


def _seed_document_and_job(session_factory, document_id="doc1", job_id="job1"):
    with session_scope(session_factory) as session:
        DocumentRepository(session).create(Document(
            document_id=document_id, filename="f.pdf", sha256=document_id, page_count=1,
            file_size_bytes=1, uploaded_at=datetime.now(timezone.utc), storage_path="/dev/null",
        ))
        JobRepository(session).create_job(ProcessingJob(
            job_id=job_id, document_id=document_id, job_type="legacy_mathongo_extraction",
            status="running", started_at=datetime.now(timezone.utc),
        ))


def test_update_progress_round_trips(session_factory):
    _seed_document_and_job(session_factory)
    with session_scope(session_factory) as session:
        JobRepository(session).update_progress("job1", "layout_detection", 7, 30, "page 7")

    with session_scope(session_factory) as session:
        job = JobRepository(session).get_job("job1")
        progress = json.loads(job.progress_json)
        assert progress == {"stage": "layout_detection", "completed": 7, "total": 30, "detail": "page 7"}


def test_update_progress_overwrites_not_appends(session_factory):
    _seed_document_and_job(session_factory)
    with session_scope(session_factory) as session:
        repo = JobRepository(session)
        repo.update_progress("job1", "layout_detection", 7, 30, "page 7")
        repo.update_progress("job1", "layout_detection", 8, 30, "page 8")

    with session_scope(session_factory) as session:
        job = JobRepository(session).get_job("job1")
        progress = json.loads(job.progress_json)
        assert progress["completed"] == 8  # not a growing log of both updates


def test_get_latest_for_document_returns_the_job(session_factory):
    _seed_document_and_job(session_factory, document_id="doc2", job_id="job2")
    with session_scope(session_factory) as session:
        job = JobRepository(session).get_latest_for_document("doc2")
        assert job is not None
        assert job.job_id == "job2"
        assert job.status == "running"


def test_get_latest_for_document_returns_none_when_no_job_exists(session_factory):
    with session_scope(session_factory) as session:
        assert JobRepository(session).get_latest_for_document("nonexistent") is None
