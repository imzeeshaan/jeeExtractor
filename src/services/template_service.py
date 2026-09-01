"""
Template Studio decision logic — no Streamlit import, mirrors
review_service.py's testability convention.
"""
from datetime import datetime, timezone

from db.repositories import TemplateRepository, JobRepository, DocumentRepository
from db.session import session_scope


def list_templates(session) -> list[dict]:
    template_repo = TemplateRepository(session)
    rows = []
    for tv in template_repo.list_all_versions():
        template = template_repo.get_template(tv.template_id)
        rows.append({
            "template_id": tv.template_id,
            "name": template.name if template else tv.template_id,
            "document_family": template.document_family if template else None,
            "version": tv.version,
            "kind": tv.kind,
            "status": tv.status,
            "adapter_ref": tv.adapter_ref,
            "match_signature": tv.match_signature,
            "baseline_score": tv.baseline_score,
            "run_count": tv.run_count,
        })
    rows.sort(key=lambda r: (r["template_id"], r["version"]))
    return rows


def get_match_history(session, template_id: str, version: int, limit: int = 20) -> list[dict]:
    """Reads existing stage_runs rows (stage_name="match_template") — no
    dedicated match-history table, consistent with how upload_page.py
    already reconstructs state from existing tables rather than adding a
    redundant one."""
    job_repo = JobRepository(session)
    doc_repo = DocumentRepository(session)

    history = []
    for stage_run in job_repo.list_stage_runs("match_template"):
        m = stage_run.metrics
        if not m.get("matched"):
            continue
        if m.get("template_id") != template_id or m.get("template_version") != version:
            continue
        job = job_repo.get_job(stage_run.job_id)
        document = doc_repo.get(job.document_id) if job else None
        history.append({
            "document_id": job.document_id if job else None,
            "filename": document.filename if document else None,
            "score": m.get("score"),
            "reasons": m.get("reasons", []),
            "completed_at": stage_run.completed_at,
        })

    epoch = datetime.min.replace(tzinfo=timezone.utc)
    history.sort(key=lambda h: h["completed_at"] or epoch, reverse=True)
    return history[:limit]


def deprecate_template_version(session_factory, template_id: str, version: int) -> None:
    with session_scope(session_factory) as session:
        TemplateRepository(session).update_status(template_id, version, "deprecated")
