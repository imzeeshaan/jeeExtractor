"""
Upload/Ingest page — picks a fixture PDF or an uploaded one and starts
ingestion. Lists previously-ingested documents (including ones still
processing) with a jump-to-Review action.

Phase 5 follow-up (Concurrent + Incremental Vision Processing): ingestion
now runs in a background daemon thread rather than blocking this page —
clicking "Analyze document" returns almost immediately (as soon as Phase A
of ingest_and_extract commits and hands back a document_id), and actual
extraction progress/results are watched from the Review page, which shows
partial results live as they land in the database. This page no longer
shows final metrics itself (there's nothing final to show synchronously
anymore) — it just confirms ingestion started and points at Review.

Kept thin: all real logic is extraction.ingest.ingest_and_extract and
db.repositories — this file only reads widget values, spawns the
background thread, and renders results.
"""
import os
import tempfile
import threading
import time

import streamlit as st

from db.repositories import DocumentRepository, JobRepository
from db.session import session_scope
from extraction.ingest import ingest_and_extract, DuplicateDocumentError

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "tests", "fixtures", "pdfs")

_JOB_STATUS_LABELS = {
    "running": "⏳ processing", "succeeded": "✅ done",
    "failed": "❌ failed", "unmatched": "🚫 unmatched",
}


def _list_fixtures():
    if not os.path.isdir(FIXTURES_DIR):
        return []
    return sorted(f for f in os.listdir(FIXTURES_DIR) if f.endswith(".pdf"))


def _start_ingestion(session_factory, config, pdf_path, exam, year, shift, publisher, allow_duplicate):
    """Spawns ingestion in a background daemon thread and waits briefly
    (Phase A — hash/copy/render/match — is fast, no external API calls) for
    either the new document_id/job_id or an early failure to become
    available. Returns a dict: {"document_id":..., "job_id":...} once
    ingestion has genuinely started, {"duplicate_id":...} if this exact PDF
    was already ingested, {"error":...} for any other pre-Phase-A failure,
    or {} if Phase A hasn't resolved within the wait window (rare — the
    caller should tell the user to check back). Extraction itself keeps
    running in the background regardless of what this function returns —
    the daemon thread survives page navigation and browser-tab close (tied
    to the Python process, not the browser session), but not a Streamlit
    server restart."""
    result_holder = {}

    def _target():
        try:
            ingest_and_extract(
                pdf_path, config, session_factory,
                exam=exam or None, year=year or None, shift=shift or None, publisher=publisher or None,
                allow_duplicate=allow_duplicate,
                on_document_created=lambda document_id, job_id: result_holder.update(
                    document_id=document_id, job_id=job_id,
                ),
            )
        except DuplicateDocumentError as exc:
            result_holder.setdefault("duplicate_id", exc.existing_document_id)
        except Exception as exc:
            if "document_id" not in result_holder:
                # Only reachable for a pre-Phase-A failure (invalid PDF,
                # unexpected setup error) — anything after Phase A already
                # recorded itself as a "failed" ProcessingJob row, visible
                # via the status badge below instead.
                result_holder.setdefault("error", str(exc))

    threading.Thread(target=_target, daemon=True).start()
    for _ in range(50):  # up to 5s; Phase A normally resolves in well under 1s
        if result_holder:
            break
        time.sleep(0.1)
    return dict(result_holder)


def render_upload_page(session_factory, config):
    st.title("Upload / Ingest")

    source = st.radio("Choose a PDF", ["Pick a fixture", "Upload a PDF"], horizontal=True)

    pdf_path = None
    if source == "Pick a fixture":
        fixtures = _list_fixtures()
        if not fixtures:
            st.error(f"No fixture PDFs found under {FIXTURES_DIR}")
        else:
            chosen = st.selectbox("Fixture paper", fixtures)
            pdf_path = os.path.join(FIXTURES_DIR, chosen)
    else:
        uploaded = st.file_uploader("Upload a JEE exam PDF", type=["pdf"])
        if uploaded is not None:
            tmp_dir = tempfile.mkdtemp(prefix="jee_review_upload_")
            pdf_path = os.path.join(tmp_dir, uploaded.name)
            with open(pdf_path, "wb") as f:
                f.write(uploaded.getbuffer())

    col1, col2 = st.columns(2)
    with col1:
        exam = st.text_input("Exam", value="JEE Main")
        year = st.number_input("Year", min_value=2000, max_value=2100, value=2012, step=1)
    with col2:
        shift = st.text_input("Shift (optional)")
        publisher = st.text_input("Publisher (optional)", value="MathonGo")

    if pdf_path and st.button("Analyze document", type="primary"):
        st.session_state["_upload_outcome"] = _start_ingestion(
            session_factory, config, pdf_path, exam, int(year), shift, publisher, False,
        )

    outcome = st.session_state.get("_upload_outcome")
    if outcome:
        if "duplicate_id" in outcome:
            st.warning(f"Already ingested as document `{outcome['duplicate_id']}`.")
            if pdf_path and st.button("Ingest again anyway", key="allow_dup"):
                st.session_state["_upload_outcome"] = _start_ingestion(
                    session_factory, config, pdf_path, exam, int(year), shift, publisher, True,
                )
                st.rerun()
        elif "error" in outcome:
            st.error(outcome["error"])
        elif "document_id" in outcome:
            st.success(f"Ingestion started — document `{outcome['document_id']}`.")
            st.session_state["review_document_id"] = outcome["document_id"]
            st.info(
                "Switch to the **Review** page (left sidebar) any time to watch progress live — "
                "already-completed questions show up immediately, even while the rest of the "
                "document is still processing."
            )
        else:
            st.warning("Still starting — this can take a moment for a large document; check the "
                       "Review page shortly.")

    st.divider()
    st.subheader("Previously ingested documents")
    with session_scope(session_factory) as session:
        documents = DocumentRepository(session).list_all()
        job_repo = JobRepository(session)
        jobs_by_document = {doc.document_id: job_repo.get_latest_for_document(doc.document_id)
                             for doc in documents}

    if not documents:
        st.caption("None yet.")
    else:
        for doc in documents:
            cols = st.columns([3, 1, 1, 1, 1, 2])
            cols[0].write(f"**{doc.filename}**")
            cols[1].write(doc.exam or "—")
            cols[2].write(str(doc.year) if doc.year else "—")
            cols[3].write(f"{doc.page_count}p")
            job = jobs_by_document.get(doc.document_id)
            cols[4].write(_JOB_STATUS_LABELS.get(job.status if job else None, "—"))
            if cols[5].button("Go to Review", key=f"goto_{doc.document_id}"):
                st.session_state["review_document_id"] = doc.document_id
                st.info("Document selected — open the **Review** page from the sidebar.")
