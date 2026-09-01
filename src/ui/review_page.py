"""
Review page — confidence-routed queue + two-column evidence/correction view.

Kept thin: every decision (queue ordering, edits, approve/reject, recheck)
delegates to services.review_service. This file only reads widget state and
renders results, per the Phase 3 plan's testability requirement.
"""
import os

import streamlit as st

from db.repositories import DocumentRepository, PageRepository
from db.session import session_scope
from services import review_service
from ui.bbox_overlay import draw_bbox_overlay

QUESTION_TYPES = [
    "single_correct", "multiple_correct", "numerical", "integer",
    "true_false", "assertion_reason", "statement_based",
    "match_columns", "passage_based", "unknown",
]
BLOCK_STATUSES = ["extracted", "unreadable", "needs_review", "approved"]


def _resolve_asset_path(config, document_id, relative_path):
    if not relative_path:
        return None
    return os.path.join(str(config.crops_dir), document_id, relative_path)


def render_review_page(session_factory, config):
    st.title("Review")

    with session_scope(session_factory) as session:
        documents = DocumentRepository(session).list_all()
    if not documents:
        st.info("No documents ingested yet — use the Upload / Ingest page first.")
        return

    doc_by_id = {d.document_id: d for d in documents}
    default_doc_id = st.session_state.get("review_document_id", documents[0].document_id)
    if default_doc_id not in doc_by_id:
        default_doc_id = documents[0].document_id

    doc_labels = {d.document_id: f"{d.filename} ({d.document_id[:8]})" for d in documents}
    selected_doc_id = st.sidebar.selectbox(
        "Document", list(doc_labels.keys()), format_func=lambda k: doc_labels[k],
        index=list(doc_labels.keys()).index(default_doc_id),
    )
    st.session_state["review_document_id"] = selected_doc_id

    include_reviewed = st.sidebar.checkbox("Show approved/rejected too", value=False)

    with session_scope(session_factory) as session:
        queue = review_service.get_review_queue(session, selected_doc_id, include_reviewed=include_reviewed)

    st.sidebar.markdown(f"**{len(queue)} question(s) in queue**")
    if not queue:
        st.success("Nothing left to review for this document (with current filters).")
        return

    queue_labels = {
        row["question_id"]: (
            f"Q{row['number']} — conf {row['confidence']:.2f} — "
            f"{row['issue_count']} issue(s)"
            + (" ⚠️" if row["has_error_or_blocking"] else "")
            + (f" [{row['status']}]" if row["status"] in ("approved", "rejected") else "")
        )
        for row in queue
    }
    default_question_id = st.session_state.get("review_question_id", queue[0]["question_id"])
    if default_question_id not in queue_labels:
        default_question_id = queue[0]["question_id"]

    selected_question_id = st.sidebar.radio(
        "Question", list(queue_labels.keys()), format_func=lambda k: queue_labels[k],
        index=list(queue_labels.keys()).index(default_question_id),
    )
    st.session_state["review_question_id"] = selected_question_id

    detail = review_service.get_question_detail(session_factory, selected_question_id)
    if detail is None:
        st.error("Selected question no longer exists.")
        return

    question = detail["question"]
    issues = detail["issues"]
    actions = detail["review_actions"]

    left, right = st.columns([1, 1])

    with left:
        st.subheader("Evidence")
        with session_scope(session_factory) as session:
            pages = {p.page_number: p for p in PageRepository(session).list_for_document(selected_doc_id)}

        page_numbers = sorted(set([question.page_start, question.page_end]))
        chosen_page = st.selectbox("Page", page_numbers, index=0) if len(page_numbers) > 1 else page_numbers[0]
        page = pages.get(chosen_page)

        if page and page.rendered_path and os.path.exists(page.rendered_path):
            bbox = question.stem_blocks[0].evidence.bbox if question.stem_blocks else None
            if bbox is not None:
                st.image(draw_bbox_overlay(page.rendered_path, bbox), caption=f"Page {chosen_page} (evidence highlighted)")
            else:
                st.image(page.rendered_path, caption=f"Page {chosen_page}")
        else:
            st.caption("Rendered page not available.")

        st.markdown("**Crops**")
        stem_crop = _resolve_asset_path(config, selected_doc_id,
                                         question.stem_blocks[0].clean_crop_path if question.stem_blocks else None)
        if stem_crop and os.path.exists(stem_crop):
            st.image(stem_crop, caption="Stem")
        for opt in question.options:
            for block in opt.blocks:
                crop = _resolve_asset_path(config, selected_doc_id, block.clean_crop_path)
                if crop and os.path.exists(crop):
                    st.image(crop, caption=f"Option {opt.label}", width=220)

    with right:
        st.subheader(f"Q{question.number}")

        c1, c2, c3 = st.columns(3)
        c1.metric("Confidence", f"{question.confidence:.2f}")
        c2.metric("Status", question.status)
        c3.metric("Extraction", question.extraction_mode)

        new_type = st.selectbox(
            "Question type", QUESTION_TYPES,
            index=QUESTION_TYPES.index(question.question_type) if question.question_type in QUESTION_TYPES else 0,
            key=f"type_{question.question_id}",
        )
        if new_type != question.question_type and st.button("Save type", key=f"save_type_{question.question_id}"):
            review_service.change_question_type(session_factory, config, question.question_id, new_type)
            st.rerun()

        st.markdown("**Stem**")
        for block in question.stem_blocks:
            new_text = st.text_area("Stem text", value=block.text or "", key=f"stem_{block.block_id}")
            bcol1, bcol2 = st.columns(2)
            if bcol1.button("Save stem text", key=f"save_stem_{block.block_id}"):
                review_service.edit_stem_text(session_factory, config, question.question_id,
                                               block.block_id, new_text)
                st.rerun()
            new_status = bcol2.selectbox(
                "Block status", BLOCK_STATUSES,
                index=BLOCK_STATUSES.index(block.status) if block.status in BLOCK_STATUSES else 0,
                key=f"stem_status_{block.block_id}",
            )
            if new_status != block.status and bcol2.button("Save status", key=f"save_stem_status_{block.block_id}"):
                review_service.mark_block_status(session_factory, question.question_id, block.block_id, new_status)
                st.rerun()

        if question.options:
            st.markdown("**Options**")
            for opt in question.options:
                st.write(f"Option {opt.label}")
                for block in opt.blocks:
                    if block.content_type == "image":
                        st.caption("(image block — no text to edit)")
                        continue
                    new_opt_text = st.text_area("Option text", value=block.text or "",
                                                 key=f"opt_{block.block_id}", label_visibility="collapsed")
                    if st.button("Save option text", key=f"save_opt_{block.block_id}"):
                        review_service.edit_option_text(session_factory, config, question.question_id,
                                                          block.block_id, new_opt_text)
                        st.rerun()

        st.markdown(f"**Answer:** {question.answer if question.answer is not None else '—'}")

        st.markdown("**Validation issues**")
        if not issues:
            st.caption("None.")
        for issue in issues:
            st.write(f"`{issue.rule_code}` [{issue.severity}] — {issue.message}")

        st.divider()
        a1, a2 = st.columns(2)
        if a1.button("✅ Approve", type="primary", key=f"approve_{question.question_id}"):
            review_service.approve_question(session_factory, question.question_id)
            st.rerun()
        if a2.button("❌ Reject", key=f"reject_{question.question_id}"):
            review_service.reject_question(session_factory, question.question_id)
            st.rerun()

        with st.expander(f"Audit trail ({len(actions)} action(s))"):
            for action in actions:
                st.write(
                    f"{action.created_at} — **{action.action_type}** on `{action.field_path}`: "
                    f"{action.previous_value!r} → {action.new_value!r}"
                )
