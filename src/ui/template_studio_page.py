"""
Template Studio — read-only view of registered templates plus a manual
Deprecate action. Kept thin: real logic lives in services/template_service.py.
"""
import streamlit as st

from db.session import session_scope
from services import template_service


def render_template_studio_page(session_factory, config):
    st.title("Template Studio")

    with session_scope(session_factory) as session:
        templates = template_service.list_templates(session)

    if not templates:
        st.info("No templates registered yet.")
        return

    for t in templates:
        with st.expander(f"{t['name']} — v{t['version']} [{t['status']}]", expanded=True):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Kind", t["kind"])
            c2.metric("Baseline score", f"{t['baseline_score']:.2f}" if t["baseline_score"] is not None else "—")
            c3.metric("Run count", t["run_count"])
            c4.metric("Adapter", t["adapter_ref"])

            sig = t["match_signature"]
            st.markdown("**Match signature**")
            st.json(sig.model_dump())

            with session_scope(session_factory) as session:
                history = template_service.get_match_history(session, t["template_id"], t["version"])
            st.markdown(f"**Recent match history** ({len(history)})")
            if not history:
                st.caption("No documents have matched this template yet.")
            else:
                for h in history:
                    st.write(f"- `{h['filename'] or h['document_id']}` — score {h['score']:.2f} "
                             f"({h['completed_at']})")

            if t["status"] != "deprecated":
                if st.button("Deprecate this version", key=f"deprecate_{t['template_id']}_{t['version']}"):
                    template_service.deprecate_template_version(session_factory, t["template_id"], t["version"])
                    st.rerun()
