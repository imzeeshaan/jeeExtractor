"""
SQLAlchemy ORM schema — Phase 1 + Phase 2 + Phase 3 + Phase 4 tables:
documents, pages, processing_jobs, stage_runs, questions, options,
content_blocks, assets, answers, validation_issues, review_actions,
templates, template_versions.

Deliberately NOT created here (later-phase tables, per the approved plan):
template_examples, template_evaluations, shared_contexts, exports.

Binary files (uploaded PDFs, rendered pages, crops) are never stored here —
only paths/hashes/sizes, per spec §8. evidence/metrics/answer values that are
structured are stored as JSON text (model_dump_json()/model_validate_json()
round-trip) since SQLite has no JSON type worth relying on at this scale.

content_blocks uses a polymorphic (owner_type, owner_id) pair rather than
separate FK columns per owner kind, so both stem blocks (owner_type=
"question_stem", owner_id=questions.id) and option blocks (owner_type=
"option", owner_id=options.id) live in the one table the spec names — a
modeling choice, not something the spec text spelled out explicitly.
"""
from sqlalchemy import (
    Column, String, Integer, Float, Boolean, Text, ForeignKey, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class DocumentRow(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True)
    filename = Column(String, nullable=False)
    sha256 = Column(String, nullable=False, unique=True)
    page_count = Column(Integer, nullable=False)
    file_size_bytes = Column(Integer, nullable=False)
    storage_path = Column(String, nullable=False)
    exam = Column(String)
    year = Column(Integer)
    shift = Column(String)
    publisher = Column(String)
    source_note = Column(String)
    uploaded_at = Column(String, nullable=False)  # ISO8601
    template_id = Column(String)
    template_version = Column(Integer)


class PageRow(Base):
    __tablename__ = "pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number"),)

    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    page_number = Column(Integer, nullable=False)
    width_pt = Column(Float, nullable=False)
    height_pt = Column(Float, nullable=False)
    rotation = Column(Integer, nullable=False, default=0)
    page_type = Column(String, nullable=False)  # text_based|scanned|mixed
    char_count = Column(Integer, nullable=False)
    text_bbox_coverage = Column(Float, nullable=False)
    image_coverage = Column(Float, nullable=False)
    has_full_page_image = Column(Boolean, nullable=False)
    drawing_count = Column(Integer, nullable=False)
    rendered_path = Column(String)
    render_hash = Column(String)


class ProcessingJobRow(Base):
    __tablename__ = "processing_jobs"

    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    job_type = Column(String, nullable=False)
    status = Column(String, nullable=False)  # pending|running|succeeded|failed
    started_at = Column(String)
    completed_at = Column(String)
    error_message = Column(String)


class StageRunRow(Base):
    __tablename__ = "stage_runs"

    id = Column(String, primary_key=True)
    job_id = Column(String, ForeignKey("processing_jobs.id"), nullable=False)
    stage_name = Column(String, nullable=False)
    status = Column(String, nullable=False)
    metrics_json = Column(Text, nullable=False)
    started_at = Column(String)
    completed_at = Column(String)


class QuestionRow(Base):
    __tablename__ = "questions"
    __table_args__ = (UniqueConstraint("document_id", "number"),)

    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    number = Column(String, nullable=False)
    subject = Column(String)
    section = Column(String)
    question_type = Column(String, nullable=False)
    shared_context_id = Column(String)
    answer_json = Column(Text)  # JSON-encoded: string | list[str] | float | null
    answer_evidence_json = Column(Text)
    page_start = Column(Integer, nullable=False)
    page_end = Column(Integer, nullable=False)
    extraction_mode = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)
    status = Column(String, nullable=False)
    validation_issue_ids_json = Column(Text, nullable=False, default="[]")
    template_id = Column(String)
    template_version = Column(Integer)


class OptionRow(Base):
    __tablename__ = "options"

    id = Column(String, primary_key=True)
    question_id = Column(String, ForeignKey("questions.id"), nullable=False)
    label = Column(String, nullable=False)
    evidence_json = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False)
    review_required = Column(Boolean, nullable=False, default=False)


class ContentBlockRow(Base):
    __tablename__ = "content_blocks"

    id = Column(String, primary_key=True)
    owner_type = Column(String, nullable=False)  # "question_stem" | "option"
    owner_id = Column(String, nullable=False)     # questions.id or options.id
    content_type = Column(String, nullable=False)
    text = Column(Text)
    latex = Column(Text)
    clean_crop_path = Column(String)
    audit_crop_path = Column(String)
    evidence_json = Column(Text, nullable=False)
    confidence = Column(Float, nullable=False)
    status = Column(String, nullable=False)
    sequence = Column(Integer, nullable=False, default=0)


class AssetRow(Base):
    __tablename__ = "assets"

    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    asset_type = Column(String, nullable=False)  # uploaded_pdf|rendered_page|crop|isolated_diagram
    file_path = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    width_px = Column(Integer)
    height_px = Column(Integer)
    file_size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String, nullable=False)
    created_at = Column(String, nullable=False)


class AnswerRow(Base):
    __tablename__ = "answers"

    id = Column(String, primary_key=True)
    question_id = Column(String, ForeignKey("questions.id"), nullable=False, unique=True)
    raw_value = Column(String, nullable=False)
    matched = Column(Boolean, nullable=False)
    source_page = Column(Integer)


class ValidationIssueRow(Base):
    __tablename__ = "validation_issues"

    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    page_number = Column(Integer)
    question_id = Column(String, ForeignKey("questions.id"))
    field_path = Column(String)
    rule_code = Column(String, nullable=False)
    severity = Column(String, nullable=False)  # info|warning|error|blocking
    message = Column(Text, nullable=False)
    evidence_path = Column(String)
    resolved = Column(Boolean, nullable=False, default=False)
    resolution_note = Column(String)


class ReviewActionRow(Base):
    __tablename__ = "review_actions"

    id = Column(String, primary_key=True)
    document_id = Column(String, ForeignKey("documents.id"), nullable=False)
    question_id = Column(String, ForeignKey("questions.id"), nullable=False)
    action_type = Column(String, nullable=False)
    field_path = Column(String, nullable=False)
    previous_value = Column(Text)
    new_value = Column(Text)
    reason = Column(String)
    actor = Column(String)
    source_page = Column(Integer)
    source_bbox_json = Column(Text)
    is_template_example = Column(Boolean, nullable=False, default=False)
    created_at = Column(String, nullable=False)


class TemplateRow(Base):
    __tablename__ = "templates"

    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    document_family = Column(String, nullable=False)
    created_at = Column(String, nullable=False)


class TemplateVersionRow(Base):
    __tablename__ = "template_versions"
    __table_args__ = (UniqueConstraint("template_id", "version"),)

    id = Column(String, primary_key=True)  # synthetic uuid PK
    template_id = Column(String, ForeignKey("templates.id"), nullable=False)
    version = Column(Integer, nullable=False)
    kind = Column(String, nullable=False)
    status = Column(String, nullable=False)
    adapter_ref = Column(String, nullable=False)
    match_signature_json = Column(Text, nullable=False)
    baseline_score = Column(Float)
    run_count = Column(Integer, nullable=False, default=0)
    created_at = Column(String, nullable=False)
