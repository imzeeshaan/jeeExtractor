"""
Upload/Ingest page — picks a fixture PDF or an uploaded one, runs
ingest_and_extract, and shows the resulting metrics/trust tier. Lists
previously-ingested documents with a jump-to-Review action.

Kept thin: all real logic is extraction.ingest.ingest_and_extract and
db.repositories — this file only reads widget values and renders results.
"""
import os
import tempfile

import streamlit as st

from db.repositories import DocumentRepository
from db.session import session_scope
from extraction.ingest import ingest_and_extract, DuplicateDocumentError, UnmatchedDocumentResult

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "tests", "fixtures", "pdfs")


def _list_fixtures():
    if not os.path.isdir(FIXTURES_DIR):
        return []
    return sorted(f for f in os.listdir(FIXTURES_DIR) if f.endswith(".pdf"))


def _run_ingest(session_factory, config, pdf_path, exam, year, shift, publisher, allow_duplicate):
    try:
        return ingest_and_extract(
            pdf_path, config, session_factory,
            exam=exam or None, year=year or None, shift=shift or None, publisher=publisher or None,
            allow_duplicate=allow_duplicate,
        )
    except DuplicateDocumentError as exc:
        st.warning(f"Already ingested as document `{exc.existing_document_id}`.")
        if st.button("Ingest again anyway", key="allow_dup"):
            return _run_ingest(session_factory, config, pdf_path, exam, year, shift, publisher, True)
        return None


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
        with st.spinner("Ingesting: inspecting, rendering, extracting, validating..."):
            result = _run_ingest(session_factory, config, pdf_path, exam, int(year), shift, publisher, False)

        if isinstance(result, UnmatchedDocumentResult):
            st.warning(f"No registered template matched this document — nothing was extracted. "
                       f"(document `{result.document_id}`)")
            for c in result.candidates:
                st.write(f"- `{c.template_version.template_id}` v{c.template_version.version}: "
                         f"score {c.fingerprint_score:.2f}")
                st.caption(" / ".join(c.reasons))
        elif result is not None:
            st.success(f"Ingested — document `{result.document_id}`")
            m = result.metrics
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Questions", m["question_count"])
            c2.metric("Answer coverage", f"{m['answer_coverage']:.0%}")
            c3.metric("Images conserved", "Yes" if m["images_conserved"] else "No")
            c4.metric("Issues found", len(result.issues))
            st.session_state["review_document_id"] = result.document_id
            st.info("Open the **Review** page (left sidebar) to review this document's questions.")

    st.divider()
    st.subheader("Previously ingested documents")
    with session_scope(session_factory) as session:
        documents = DocumentRepository(session).list_all()

    if not documents:
        st.caption("None yet.")
    else:
        for doc in documents:
            cols = st.columns([3, 1, 1, 1, 2])
            cols[0].write(f"**{doc.filename}**")
            cols[1].write(doc.exam or "—")
            cols[2].write(str(doc.year) if doc.year else "—")
            cols[3].write(f"{doc.page_count}p")
            if cols[4].button("Go to Review", key=f"goto_{doc.document_id}"):
                st.session_state["review_document_id"] = doc.document_id
                st.info("Document selected — open the **Review** page from the sidebar.")
