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

Phase 5 follow-up ("Concurrent + Incremental Vision Processing"): the
deterministic path is untouched (a single fast, atomic call+commit, exactly
as before — test_ingest_template_matching.py etc. depend on this staying
byte-identical). The vision-layout path is restructured into four
separately-committed phases so results become visible in the database as
soon as each page finishes, instead of only at the very end:
  Phase A — setup + template match (fast, no external API calls; shared by
            both paths).
  Phase B — per-page vision extraction, concurrent dispatch via
            VisionLayoutAdapter's on_page_result callback; each page's
            questions are validated (per-question rules only) and
            committed immediately.
  Phase C — validation-driven escalation to the fallback provider,
            concurrent dispatch; each escalated question's updated blocks
            are committed immediately.
  Phase D — whole-document-only rules (just rules_sequence — everything
            else is already per-question or already skipped for vision),
            final confidence pass, template lifecycle update, job status.
`on_document_created(document_id, job_id)` fires right after Phase A
commits — this is what lets a caller (e.g. a background thread from the
Upload page) get the id back almost immediately, long before extraction
finishes. `progress_callback(stage, completed, total, detail)` fires
during Phase B/C.
"""
import hashlib
import os
import shutil
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from extraction import vision_escalation
from extraction.vision_layout_adapter import VisionLayoutAdapterResult
from services import review_service
from templates.fingerprint import compute_fingerprint
from templates.matcher import match_templates
from templates.registry import build_escalation_provider, get_adapter
from templates.lifecycle import compute_validation_score, update_baseline_and_check_demotion
from validation import rules_geometry, rules_option_consistency, rules_sequence, rules_stem_completeness
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
                        publisher: str = None, allow_duplicate: bool = False,
                        on_document_created=None, progress_callback=None):
    """Returns the final result (LegacyAdapterResult / VisionLayoutAdapterResult
    / UnmatchedDocumentResult) once everything completes — existing callers
    (tests, scripts/run_adapter.py, scripts/run_vision_experiment.py) keep
    working unchanged if they just call this and use the return value.
    Raises DuplicateDocumentError if this exact PDF was already ingested and
    allow_duplicate is False (raised before any phase commits, so no
    on_document_created call happens in that case)."""
    _verify_pdf_magic_bytes(pdf_path)
    sha256 = _sha256_of_file(pdf_path)

    # ---------- Phase A: setup + template match (fast, no external API calls) ----------
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
        # every validated/monitored/draft template. A match is only trusted
        # once its adapter has actually been RUN and validated — the
        # fingerprint score alone only decides whether it's worth trying. ---
        match_stage_started_at = datetime.now(timezone.utc)
        fp = compute_fingerprint(storage_path)
        candidates = template_repo.list_matchable()
        match_results = match_templates(fp, candidates)
        best = match_results[0] if match_results else None

        unmatched = best is None or best.fingerprint_score < MATCH_AUTO_RUN_THRESHOLD
        matched_tv = None
        if unmatched:
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
        else:
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
    # Phase A committed.

    if on_document_created:
        on_document_created(document_id, job_id)

    if unmatched:
        return UnmatchedDocumentResult(document_id=document_id, candidates=match_results)

    extraction_out_dir = os.path.join(str(config.crops_dir), document_id)
    os.makedirs(extraction_out_dir, exist_ok=True)

    if matched_tv.kind != "vision_layout":
        # Deterministic path: a single fast, atomic call+commit, unchanged
        # from before this follow-up — no incremental persistence needed,
        # and this keeps the well-tested existing behavior byte-identical.
        return _run_deterministic_path(
            config, session_factory, document, job, matched_tv, pages, extraction_out_dir,
        )

    return _run_vision_path(
        config, session_factory, document, job, matched_tv, pages, extraction_out_dir, progress_callback,
    )


def _run_deterministic_path(config, session_factory, document, job, matched_tv, pages, extraction_out_dir):
    document_id = document.document_id
    job_id = job.job_id
    with session_scope(session_factory) as session:
        job_repo = JobRepository(session)
        template_repo = TemplateRepository(session)

        try:
            adapter = get_adapter(matched_tv.adapter_ref, config)
            result = adapter.run(document, document.storage_path, extraction_out_dir,
                                  template_id=matched_tv.template_id, template_version=matched_tv.version,
                                  pages=pages)
        except Exception as exc:
            job_repo.update_status(job_id, "failed", error=str(exc))
            raise

        validation_started_at = datetime.now(timezone.utc)
        fitz_doc = fitz.open(document.storage_path)
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


def _run_vision_path(config, session_factory, document, job, matched_tv, pages, extraction_out_dir,
                      progress_callback):
    document_id = document.document_id
    job_id = job.job_id
    pages_by_number = {p.page_number: p for p in pages}
    all_questions = []
    dropped_region_warnings = {}
    question_ids_with_clipped_crops = set()

    try:
        adapter = get_adapter(matched_tv.adapter_ref, config)
    except Exception as exc:
        with session_scope(session_factory) as session:
            JobRepository(session).update_status(job_id, "failed", error=str(exc))
        raise

    def _on_page_result(page, page_questions, page_warnings, completed, total):
        # Phase B: per-page validation (only the per-question rule modules
        # recheck_question already reuses — the whole-document-only rule
        # runs once at the end, in Phase D) + immediate commit, so this
        # page's questions are visible to any other DB reader right away.
        if page_warnings:
            dropped_region_warnings[page.page_number] = page_warnings
        if page_questions:
            page_issues = []
            for q in page_questions:
                q_issues = (
                    rules_option_consistency.check_option_consistency(q, document_id)
                    + rules_stem_completeness.check_stem_completeness(q, document_id)
                    + rules_geometry.check_geometry(q, extraction_out_dir, document_id)
                )
                q.validation_issue_ids = [i.issue_id for i in q_issues]
                q.confidence = compute_question_confidence(q, q_issues, document_level_issues=[])
                page_issues.extend(q_issues)
                if any(i.rule_code == "GEOM_CROP_EDGE_CLIPPED" for i in q_issues):
                    question_ids_with_clipped_crops.add(q.question_id)
            with session_scope(session_factory) as session:
                QuestionRepository(session).bulk_save(page_questions)
                ValidationIssueRepository(session).bulk_save(page_issues)
                JobRepository(session).update_progress(
                    job_id, "layout_detection", completed, total, f"page {page.page_number}",
                )
            all_questions.extend(page_questions)
        else:
            with session_scope(session_factory) as session:
                JobRepository(session).update_progress(
                    job_id, "layout_detection", completed, total, f"page {page.page_number}",
                )
        if progress_callback:
            progress_callback("layout_detection", completed, total, f"page {page.page_number}")

    try:
        result = adapter.run(document, document.storage_path, extraction_out_dir,
                              template_id=matched_tv.template_id, template_version=matched_tv.version,
                              pages=pages, on_page_result=_on_page_result, concurrency=config.vision_concurrency)
    except Exception as exc:
        with session_scope(session_factory) as session:
            JobRepository(session).update_status(job_id, "failed", error=str(exc))
        raise

    # ---------- Phase C: validation-driven escalation (concurrent) ----------
    flagged = vision_escalation.select_questions_for_escalation(
        all_questions, dropped_region_warnings, question_ids_with_clipped_crops,
    )
    escalated_reasons = {}
    skipped_reasons = {}

    if flagged:
        try:
            fallback_provider = build_escalation_provider(config)
        except Exception as exc:
            fallback_provider = None
            for q, reason in flagged:
                skipped_reasons[q.question_id] = f"{reason} (no fallback provider available: {exc})"

        if fallback_provider is not None:
            def _escalate_one(q, reason):
                page = pages_by_number.get(q.page_start)
                if page is None or not page.rendered_path:
                    return q, reason, "no rendered page available", False
                try:
                    vision_escalation.escalate_question(q, page, fallback_provider, reason, extraction_out_dir)
                    return q, reason, None, True
                except Exception as exc:
                    return q, reason, str(exc), False

            with ThreadPoolExecutor(max_workers=config.vision_concurrency) as executor:
                futures = {executor.submit(_escalate_one, q, reason): q for q, reason in flagged}
                completed = 0
                for future in as_completed(futures):
                    completed += 1
                    q, reason, error, ok = future.result()
                    if ok:
                        # Persist the escalated blocks, then reuse the
                        # EXISTING recheck_question (review_service) to
                        # re-run per-question rules + recompute confidence —
                        # not reinvented here.
                        with session_scope(session_factory) as session:
                            QuestionRepository(session).replace_blocks_for_question(q)
                        review_service.recheck_question(session_factory, config, q.question_id)
                        escalated_reasons[q.question_id] = reason
                    else:
                        skipped_reasons[q.question_id] = f"{reason} (escalation failed: {error})"
                    with session_scope(session_factory) as session:
                        JobRepository(session).update_progress(
                            job_id, "escalation", completed, len(flagged), f"Q{q.number}",
                        )
                    if progress_callback:
                        progress_callback("escalation", completed, len(flagged), f"Q{q.number}")

    escalation_metrics = {
        "escalated_count": len(escalated_reasons),
        "skipped_count": len(skipped_reasons),
        "total_questions": len(all_questions),
        "reasons": escalated_reasons,
        "skipped_reasons": skipped_reasons,
    }

    # ---------- Phase D: finalize ----------
    with session_scope(session_factory) as session:
        # rules_sequence is the only rule left that genuinely needs the
        # FULL question list (duplicate/gap detection) — every other rule
        # is already per-question (ran in Phase B/C) or already skipped for
        # vision_layout (rules_image_disposition/rules_answer_coverage).
        doc_level_issues = rules_sequence.check_question_sequence(all_questions, document_id)
        issue_repo = ValidationIssueRepository(session)
        if doc_level_issues:
            issue_repo.bulk_save(doc_level_issues)

        all_issues = issue_repo.list_for_document(document_id)  # now complete: per-question + doc-level

        q_repo = QuestionRepository(session)
        for q in all_questions:
            q_issues = [i for i in all_issues if i.question_id == q.question_id]
            q_repo.update_confidence(q.question_id, compute_question_confidence(q, q_issues, doc_level_issues))

        trust_tier = compute_document_trust_tier(all_questions, all_issues)

        job_repo = JobRepository(session)
        job_repo.create_stage_run(StageRun(
            stage_run_id=str(uuid.uuid4()), job_id=job_id, stage_name="vision_layout_extraction",
            status="succeeded", metrics=result.metrics,
            started_at=job.started_at, completed_at=datetime.now(timezone.utc),
        ))
        job_repo.create_stage_run(StageRun(
            stage_run_id=str(uuid.uuid4()), job_id=job_id, stage_name="validate_questions",
            status="succeeded", metrics={**summarize(all_issues), "trust_tier": trust_tier},
            started_at=job.started_at, completed_at=datetime.now(timezone.utc),
        ))
        job_repo.create_stage_run(StageRun(
            stage_run_id=str(uuid.uuid4()), job_id=job_id, stage_name="vision_escalation",
            status="succeeded", metrics=escalation_metrics,
            started_at=job.started_at, completed_at=datetime.now(timezone.utc),
        ))

        template_repo = TemplateRepository(session)
        validation_score = compute_validation_score(all_questions, all_issues)
        new_baseline, new_status = update_baseline_and_check_demotion(matched_tv, validation_score)
        template_repo.update_status(matched_tv.template_id, matched_tv.version, new_status)
        template_repo.update_baseline_and_run_count(
            matched_tv.template_id, matched_tv.version, new_baseline, matched_tv.run_count + 1,
        )

        job_repo.update_status(job_id, "succeeded")

        return VisionLayoutAdapterResult(
            document_id=document_id, questions=all_questions, metrics=result.metrics,
            issues=all_issues, legacy_questions=None, dropped_region_warnings=dropped_region_warnings,
        )
