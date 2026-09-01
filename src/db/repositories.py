"""
Repository pattern: these are the ONLY modules (besides schema.py/session.py
itself) that import the SQLAlchemy ORM row classes. Everything else (the
adapter, ingestion service, scripts, tests) sees only Pydantic models and
repository method calls, so a future DB swap stays realistic.
"""
import json
from datetime import datetime, timezone

from db.schema import (
    DocumentRow, PageRow, ProcessingJobRow, StageRunRow,
    QuestionRow, OptionRow, ContentBlockRow, AssetRow, AnswerRow,
)
from models.common import SourceEvidence
from models.documents import Document, Page, ProcessingJob, StageRun
from models.questions import Question, Option, ContentBlock


def _iso_now():
    return datetime.now(timezone.utc).isoformat()


class DocumentRepository:
    def __init__(self, session):
        self.session = session

    def create(self, document: Document) -> None:
        row = DocumentRow(
            id=document.document_id,
            filename=document.filename,
            sha256=document.sha256,
            page_count=document.page_count,
            file_size_bytes=document.file_size_bytes,
            storage_path=document.storage_path,
            exam=document.exam,
            year=document.year,
            shift=document.shift,
            publisher=document.publisher,
            source_note=document.source_note,
            uploaded_at=document.uploaded_at.isoformat(),
        )
        self.session.add(row)
        self.session.flush()

    def get_by_sha256(self, sha256: str):
        row = self.session.query(DocumentRow).filter_by(sha256=sha256).one_or_none()
        return self._to_model(row) if row else None

    def get(self, document_id: str):
        row = self.session.query(DocumentRow).filter_by(id=document_id).one_or_none()
        return self._to_model(row) if row else None

    def list_all(self):
        return [self._to_model(r) for r in self.session.query(DocumentRow).all()]

    @staticmethod
    def _to_model(row: DocumentRow) -> Document:
        return Document(
            document_id=row.id,
            filename=row.filename,
            sha256=row.sha256,
            page_count=row.page_count,
            file_size_bytes=row.file_size_bytes,
            uploaded_at=row.uploaded_at,
            exam=row.exam,
            year=row.year,
            shift=row.shift,
            publisher=row.publisher,
            source_note=row.source_note,
            storage_path=row.storage_path,
        )


class PageRepository:
    def __init__(self, session):
        self.session = session

    def bulk_create(self, pages: list[Page]) -> None:
        for p in pages:
            self.session.add(PageRow(
                id=p.page_id,
                document_id=p.document_id,
                page_number=p.page_number,
                width_pt=p.width_pt,
                height_pt=p.height_pt,
                rotation=p.rotation,
                page_type=p.page_type,
                char_count=p.char_count,
                text_bbox_coverage=p.text_bbox_coverage,
                image_coverage=p.image_coverage,
                has_full_page_image=p.has_full_page_image,
                drawing_count=p.drawing_count,
                rendered_path=p.rendered_path,
                render_hash=p.render_hash,
            ))
        self.session.flush()

    def list_for_document(self, document_id: str) -> list[Page]:
        rows = (
            self.session.query(PageRow)
            .filter_by(document_id=document_id)
            .order_by(PageRow.page_number)
            .all()
        )
        return [
            Page(
                page_id=r.id, document_id=r.document_id, page_number=r.page_number,
                width_pt=r.width_pt, height_pt=r.height_pt, rotation=r.rotation,
                page_type=r.page_type, char_count=r.char_count,
                text_bbox_coverage=r.text_bbox_coverage, image_coverage=r.image_coverage,
                has_full_page_image=r.has_full_page_image, drawing_count=r.drawing_count,
                rendered_path=r.rendered_path, render_hash=r.render_hash,
            )
            for r in rows
        ]


class JobRepository:
    def __init__(self, session):
        self.session = session

    def create_job(self, job: ProcessingJob) -> None:
        self.session.add(ProcessingJobRow(
            id=job.job_id,
            document_id=job.document_id,
            job_type=job.job_type,
            status=job.status,
            started_at=job.started_at.isoformat() if job.started_at else None,
            completed_at=job.completed_at.isoformat() if job.completed_at else None,
            error_message=job.error_message,
        ))
        self.session.flush()

    def update_status(self, job_id: str, status: str, error: str = None) -> None:
        row = self.session.query(ProcessingJobRow).filter_by(id=job_id).one()
        row.status = status
        if status == "running" and row.started_at is None:
            row.started_at = _iso_now()
        if status in ("succeeded", "failed"):
            row.completed_at = _iso_now()
        if error is not None:
            row.error_message = error
        self.session.flush()

    def create_stage_run(self, stage_run: StageRun) -> None:
        self.session.add(StageRunRow(
            id=stage_run.stage_run_id,
            job_id=stage_run.job_id,
            stage_name=stage_run.stage_name,
            status=stage_run.status,
            metrics_json=json.dumps(stage_run.metrics),
            started_at=stage_run.started_at.isoformat() if stage_run.started_at else None,
            completed_at=stage_run.completed_at.isoformat() if stage_run.completed_at else None,
        ))
        self.session.flush()


class QuestionRepository:
    def __init__(self, session):
        self.session = session

    def bulk_save(self, questions: list[Question]) -> None:
        # Flush after each parent row (question, then each option) before
        # adding rows that reference it by raw FK column value — the ORM has
        # no relationship()-based dependency graph here (deliberately plain
        # FK columns, see schema.py), so it can't infer insert ordering from
        # object references alone; an explicit flush makes the FK exist
        # before the dependent row is inserted (SQLite enforces
        # PRAGMA foreign_keys=ON).
        for q in questions:
            self.session.add(QuestionRow(
                id=q.question_id,
                document_id=q.document_id,
                number=q.number,
                subject=q.subject,
                section=q.section,
                question_type=q.question_type,
                shared_context_id=q.shared_context_id,
                answer_json=json.dumps(q.answer),
                answer_evidence_json=q.answer_evidence.model_dump_json() if q.answer_evidence else None,
                page_start=q.page_start,
                page_end=q.page_end,
                extraction_mode=q.extraction_mode,
                confidence=q.confidence,
                status=q.status,
                validation_issue_ids_json=json.dumps(q.validation_issue_ids),
                template_id=q.template_id,
                template_version=q.template_version,
            ))
            self.session.flush()

            for seq, block in enumerate(q.stem_blocks):
                self.session.add(self._content_block_row(block, "question_stem", q.question_id, seq))

            for opt in q.options:
                self.session.add(OptionRow(
                    id=opt.option_id,
                    question_id=q.question_id,
                    label=opt.label,
                    evidence_json=opt.evidence.model_dump_json(),
                    confidence=opt.confidence,
                    review_required=opt.review_required,
                ))
                self.session.flush()
                for seq, block in enumerate(opt.blocks):
                    self.session.add(self._content_block_row(block, "option", opt.option_id, seq))

            if q.answer is not None:
                self.session.add(AnswerRow(
                    id=f"{q.question_id}-answer",
                    question_id=q.question_id,
                    raw_value=str(q.answer),
                    matched=True,
                    source_page=q.answer_evidence.page_number if q.answer_evidence else None,
                ))
        self.session.flush()

    @staticmethod
    def _content_block_row(block: ContentBlock, owner_type: str, owner_id: str, sequence: int) -> ContentBlockRow:
        return ContentBlockRow(
            id=block.block_id,
            owner_type=owner_type,
            owner_id=owner_id,
            content_type=block.content_type,
            text=block.text,
            latex=block.latex,
            clean_crop_path=block.clean_crop_path,
            audit_crop_path=block.audit_crop_path,
            evidence_json=block.evidence.model_dump_json(),
            confidence=block.confidence,
            status=block.status,
            sequence=sequence,
        )

    def list_for_document(self, document_id: str) -> list[Question]:
        return self.to_canonical(document_id)

    def to_canonical(self, document_id: str) -> list[Question]:
        q_rows = (
            self.session.query(QuestionRow)
            .filter_by(document_id=document_id)
            .order_by(QuestionRow.number)
            .all()
        )
        questions = []
        for qr in q_rows:
            stem_blocks = self._load_blocks("question_stem", qr.id)
            options = self._load_options(qr.id)
            answer_evidence = (
                SourceEvidence.model_validate_json(qr.answer_evidence_json)
                if qr.answer_evidence_json else None
            )
            questions.append(Question(
                question_id=qr.id,
                document_id=qr.document_id,
                number=qr.number,
                subject=qr.subject,
                section=qr.section,
                question_type=qr.question_type,
                shared_context_id=qr.shared_context_id,
                stem_blocks=stem_blocks,
                options=options,
                answer=json.loads(qr.answer_json) if qr.answer_json else None,
                answer_evidence=answer_evidence,
                page_start=qr.page_start,
                page_end=qr.page_end,
                extraction_mode=qr.extraction_mode,
                confidence=qr.confidence,
                status=qr.status,
                validation_issue_ids=json.loads(qr.validation_issue_ids_json or "[]"),
                template_id=qr.template_id,
                template_version=qr.template_version,
            ))
        # sort numerically where possible (question "number" is stored as a
        # string since some future question types may not be purely numeric)
        questions.sort(key=lambda q: int(q.number) if q.number.isdigit() else q.number)
        return questions

    def _load_blocks(self, owner_type: str, owner_id: str) -> list[ContentBlock]:
        rows = (
            self.session.query(ContentBlockRow)
            .filter_by(owner_type=owner_type, owner_id=owner_id)
            .order_by(ContentBlockRow.sequence)
            .all()
        )
        return [
            ContentBlock(
                block_id=r.id,
                content_type=r.content_type,
                text=r.text,
                latex=r.latex,
                clean_crop_path=r.clean_crop_path,
                audit_crop_path=r.audit_crop_path,
                evidence=SourceEvidence.model_validate_json(r.evidence_json),
                confidence=r.confidence,
                status=r.status,
            )
            for r in rows
        ]

    def _load_options(self, question_id: str) -> list[Option]:
        rows = self.session.query(OptionRow).filter_by(question_id=question_id).all()
        return [
            Option(
                option_id=r.id,
                label=r.label,
                blocks=self._load_blocks("option", r.id),
                evidence=SourceEvidence.model_validate_json(r.evidence_json),
                confidence=r.confidence,
                review_required=r.review_required,
            )
            for r in rows
        ]


class AssetRepository:
    def __init__(self, session):
        self.session = session

    def register(self, document_id: str, asset_type: str, file_path: str, mime_type: str,
                 file_size_bytes: int, sha256: str, width_px: int = None, height_px: int = None) -> str:
        import uuid
        asset_id = str(uuid.uuid4())
        self.session.add(AssetRow(
            id=asset_id,
            document_id=document_id,
            asset_type=asset_type,
            file_path=file_path,
            mime_type=mime_type,
            width_px=width_px,
            height_px=height_px,
            file_size_bytes=file_size_bytes,
            sha256=sha256,
            created_at=_iso_now(),
        ))
        self.session.flush()
        return asset_id

    def list_for_document(self, document_id: str) -> list[dict]:
        rows = self.session.query(AssetRow).filter_by(document_id=document_id).all()
        return [
            {
                "id": r.id, "asset_type": r.asset_type, "file_path": r.file_path,
                "mime_type": r.mime_type, "width_px": r.width_px, "height_px": r.height_px,
                "file_size_bytes": r.file_size_bytes, "sha256": r.sha256, "created_at": r.created_at,
            }
            for r in rows
        ]
