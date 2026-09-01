"""
Ties PDF services + persistence + the legacy adapter together into one
callable. This is what scripts/run_adapter.py calls; app.py does not call
this in Phase 1 (per the approved plan — the canonical layer stays purely
additive/parallel to the existing UI until a later phase).
"""
import hashlib
import os
import shutil
import uuid
from datetime import datetime, timezone

import fitz

from config import AppConfig
from db.repositories import DocumentRepository, PageRepository, JobRepository, QuestionRepository, AssetRepository
from db.session import session_scope
from extraction.legacy_mathongo_adapter import LegacyMathonGoAdapter
from models.documents import Document, Page, ProcessingJob, StageRun
from pdf.inspector import inspect_document
from pdf.renderer import render_document

PDF_MAGIC = b"%PDF-"


class InvalidPdfError(ValueError):
    pass


class DuplicateDocumentError(ValueError):
    def __init__(self, existing_document_id: str):
        super().__init__(f"document already ingested as {existing_document_id}")
        self.existing_document_id = existing_document_id


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
        job_id = str(uuid.uuid4())
        job = ProcessingJob(
            job_id=job_id,
            document_id=document_id,
            job_type="legacy_mathongo_extraction",
            status="running",
            started_at=datetime.now(timezone.utc),
        )
        job_repo.create_job(job)

        extraction_out_dir = os.path.join(str(config.crops_dir), document_id)
        os.makedirs(extraction_out_dir, exist_ok=True)

        try:
            result = LegacyMathonGoAdapter().run(document, storage_path, extraction_out_dir)
        except Exception as exc:
            job_repo.update_status(job_id, "failed", error=str(exc))
            raise

        QuestionRepository(session).bulk_save(result.questions)
        job_repo.create_stage_run(StageRun(
            stage_run_id=str(uuid.uuid4()),
            job_id=job_id,
            stage_name="legacy_parse_pdf",
            status="succeeded",
            metrics=result.metrics,
            started_at=job.started_at,
            completed_at=datetime.now(timezone.utc),
        ))
        job_repo.update_status(job_id, "succeeded")

        return result
