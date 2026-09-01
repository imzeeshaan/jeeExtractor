"""
Ties PDF services + persistence + template matching + the matched adapter
together into one callable. This is what scripts/run_adapter.py and
review_app.py's Upload page call; app.py never calls this (per the approved
plan — the canonical layer stays purely additive/parallel to the original
single-session UI).

Phase 4: a document is fingerprinted and matched against every registered
validated/monitored template before any adapter runs. An unmatched document
returns an UnmatchedDocumentResult (not an error) with zero questions
extracted, rather than defaulting to running the one adapter that happens
to exist.
"""
import hashlib
import os
import shutil
import uuid
from datetime import datetime, timezone

import fitz

from config import AppConfig
from db.repositories import (
    DocumentRepository, PageRepository, JobRepository, QuestionRepository, AssetRepository,
    ValidationIssueRepository, TemplateRepository,
)
from db.session import session_scope
from models.documents import Document, Page, ProcessingJob, StageRun
from pdf.inspector import inspect_document
from pdf.renderer import render_document
from templates.fingerprint import compute_fingerprint
from templates.matcher import match_templates
from templates.registry import get_adapter
from templates.lifecycle import compute_validation_score, update_baseline_and_check_demotion
from validation.confidence import compute_question_confidence, compute_document_trust_tier
from validation.engine import run_validation, issues_by_question, summarize

PDF_MAGIC = b"%PDF-"
MATCH_AUTO_RUN_THRESHOLD = 0.65
# The spec's 0.65-0.84 "confirm with user" band is deliberately collapsed
# into this single auto-run threshold for Phase 4: there is no interactive
# confirmation UI anywhere in this codebase, and with exactly one registered
# template there is nothing to choose between anyway. A stated
# simplification, not a hidden one.


class InvalidPdfError(ValueError):
    pass


class DuplicateDocumentError(ValueError):
    def __init__(self, existing_document_id: str):
        super().__init__(f"document already ingested as {existing_document_id}")
        self.existing_document_id = existing_document_id


class UnmatchedDocumentResult:
    """Returned (not raised) when no registered template matched this
    document well enough to run any adapter. This is a legitimate, complete
    outcome — nothing failed — so it's a plain return value, unlike
    DuplicateDocumentError which is a pre-work decision point the caller
    must resolve before anything happens."""
    def __init__(self, document_id: str, candidates: list):
        self.document_id = document_id
        self.candidates = candidates


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _verify_pdf_magic_bytes(path: str) -> None:
    with open(path, "rb") as f:
        head = f.read(len(PDF_MAGIC))
    if head != PDF_MAGIC:
        raise InvalidPdfError(f"{path} does not start with the PDF magic bytes")


def ingest_and_extract(pdf_path: str, config: AppConfig, session_factory,
                        exam: str = None, year: int = None, shift: str = None,
                        publisher: str = None, allow_duplicate: bool = False):
    """Full Phase-1 pipeline: hash/verify -> store -> inspect -> render ->
    run the legacy adapter -> persist. Returns the LegacyAdapterResult.
    Raises DuplicateDocumentError if this exact PDF was already ingested and
    allow_duplicate is False."""
    _verify_pdf_magic_bytes(pdf_path)
    sha256 = _sha256_of_file(pdf_path)

    with session_scope(session_factory) as session:
        doc_repo = DocumentRepository(session)
        existing = doc_repo.get_by_sha256(sha256)
        if existing and not allow_duplicate:
            raise DuplicateDocumentError(existing.document_id)

        document_id = str(uuid.uuid4())
        filename = os.path.basename(pdf_path)
        storage_path = os.path.join(str(config.uploads_dir), f"{document_id}.pdf")
        shutil.copyfile(pdf_path, storage_path)

        fitz_doc = fitz.open(storage_path)
        try:
            page_count = fitz_doc.page_count
        finally:
            fitz_doc.close()

        document = Document(
            document_id=document_id,
            filename=filename,
            sha256=sha256,
            page_count=page_count,
            file_size_bytes=os.path.getsize(storage_path),
            uploaded_at=datetime.now(timezone.utc),
            exam=exam,
            year=year,
            shift=shift,
            publisher=publisher,
            storage_path=storage_path,
        )
        doc_repo.create(document)

        asset_repo = AssetRepository(session)
        asset_repo.register(
            document_id=document_id, asset_type="uploaded_pdf", file_path=storage_path,
            mime_type="application/pdf", file_size_bytes=document.file_size_bytes, sha256=sha256,
        )

        classifications = inspect_document(storage_path)
        rendered = render_document(document_id, storage_path, str(config.rendered_pages_dir), dpi=config.render_dpi)
        rendered_by_page = {r.page_number: r for r in rendered}

        for r in rendered:
            asset_repo.register(
                document_id=document_id, asset_type="rendered_page", file_path=r.file_path,
                mime_type="image/png", file_size_bytes=os.path.getsize(r.file_path), sha256=r.sha256,
                width_px=r.width_px, height_px=r.height_px,
            )

        pages = []
        for c in classifications:
            r = rendered_by_page.get(c.page_number)
            pages.append(Page(
                page_id=str(uuid.uuid4()),
                document_id=document_id,
                page_number=c.page_number,
                width_pt=c.width_pt,
                height_pt=c.height_pt,
                rotation=c.rotation,
                page_type=c.page_type,
                char_count=c.char_count,
                text_bbox_coverage=c.text_bbox_coverage,
                image_coverage=c.image_coverage,
                has_full_page_image=c.has_full_page_image,
                drawing_count=c.drawing_count,
                rendered_path=r.file_path if r else None,
                render_hash=r.sha256 if r else None,
            ))
        PageRepository(session).bulk_create(pages)

        job_repo = JobRepository(session)
        template_repo = TemplateRepository(session)
        job_id = str(uuid.uuid4())
        job = ProcessingJob(
            job_id=job_id,
            document_id=document_id,
            job_type="legacy_mathongo_extraction",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        job_repo.create_job(job)

        # --- template matching (Phase 4): fingerprint, then match against
        # every validated/monitored template. A match is only trusted once
        # its adapter has actually been RUN and validated (see below) — the
        # fingerprint score alone only decides whether it's worth trying. ---
        match_stage_started_at = datetime.now(timezone.utc)
        fp = compute_fingerprint(storage_path)
        candidates = template_repo.list_matchable()
        match_results = match_templates(fp, candidates)
        best = match_results[0] if match_results else None

        if best is None or best.fingerprint_score < MATCH_AUTO_RUN_THRESHOLD:
            doc_repo.update_template_match(document_id, None, None)
            job_repo.create_stage_run(StageRun(
                stage_run_id=str(uuid.uuid4()),
                job_id=job_id,
                stage_name="match_template",
                status="succeeded",
                metrics={
                    "matched": False,
                    "candidates": [
                        {
                            "template_id": r.template_version.template_id,
                            "version": r.template_version.version,
                            "score": r.fingerprint_score,
                            "reasons": r.reasons,
                        }
                        for r in match_results
                    ],
                },
                started_at=match_stage_started_at,
                completed_at=datetime.now(timezone.utc),
            ))
            job_repo.update_status(job_id, "unmatched")
            return UnmatchedDocumentResult(document_id=document_id, candidates=match_results)

        matched_tv = best.template_version
        job_repo.create_stage_run(StageRun(
            stage_run_id=str(uuid.uuid4()),
            job_id=job_id,
            stage_name="match_template",
            status="succeeded",
            metrics={
                "matched": True,
                "template_id": matched_tv.template_id,
                "template_version": matched_tv.version,
                "score": best.fingerprint_score,
                "reasons": best.reasons,
            },
            started_at=match_stage_started_at,
            completed_at=datetime.now(timezone.utc),
        ))
        doc_repo.update_template_match(document_id, matched_tv.template_id, matched_tv.version)

        extraction_out_dir = os.path.join(str(config.crops_dir), document_id)
        os.makedirs(extraction_out_dir, exist_ok=True)

        try:
            adapter = get_adapter(matched_tv.adapter_ref)
            result = adapter.run(document, storage_path, extraction_out_dir,
                                  template_id=matched_tv.template_id, template_version=matched_tv.version)
        except Exception as exc:
            job_repo.update_status(job_id, "failed", error=str(exc))
            raise

        validation_started_at = datetime.now(timezone.utc)
        fitz_doc = fitz.open(storage_path)
        try:
            issues = run_validation(
                result.questions, result.legacy_questions, fitz_doc, document_id,
                crops_root=extraction_out_dir,
            )
        finally:
            fitz_doc.close()

        issue_ids_by_question = issues_by_question(issues)
        document_level_issues = [i for i in issues if i.question_id is None]
        for q in result.questions:
            q_issues = [i for i in issues if i.question_id == q.question_id]
            q.validation_issue_ids = issue_ids_by_question.get(q.question_id, [])
            q.confidence = compute_question_confidence(q, q_issues, document_level_issues)

        QuestionRepository(session).bulk_save(result.questions)
        job_repo.create_stage_run(StageRun(
            stage_run_id=str(uuid.uuid4()),
            job_id=job_id,
            stage_name="legacy_parse_pdf",
            status="succeeded",
            metrics=result.metrics,
            started_at=job.started_at,
            completed_at=validation_started_at,
        ))

        ValidationIssueRepository(session).bulk_save(issues)
        job_repo.create_stage_run(StageRun(
            stage_run_id=str(uuid.uuid4()),
            job_id=job_id,
            stage_name="validate_questions",
            status="succeeded",
            metrics={**summarize(issues), "trust_tier": compute_document_trust_tier(result.questions, issues)},
            started_at=validation_started_at,
            completed_at=datetime.now(timezone.utc),
        ))

        # update the matched template's rolling baseline/lifecycle status
        # based on how this run actually validated
        validation_score = compute_validation_score(result.questions, issues)
        new_baseline, new_status = update_baseline_and_check_demotion(matched_tv, validation_score)
        template_repo.update_status(matched_tv.template_id, matched_tv.version, new_status)
        template_repo.update_baseline_and_run_count(
            matched_tv.template_id, matched_tv.version, new_baseline, matched_tv.run_count + 1,
        )

        job_repo.update_status(job_id, "succeeded")

        return result
