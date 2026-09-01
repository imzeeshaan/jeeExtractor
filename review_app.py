"""
Phase 3 review UI entry point — run with:
    streamlit run review_app.py --server.port 8502

Separate from app.py (the original single-session tool, untouched, still
run via `streamlit run app.py`) — this app operates entirely against the
canonical data/app.db, reusing every repository/service built in Phase 1-3.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import streamlit as st

from config import get_config
from db.session import get_engine, init_db, make_session_factory, session_scope
from templates.bootstrap import ensure_default_templates_registered
from ui.upload_page import render_upload_page
from ui.review_page import render_review_page
from ui.template_studio_page import render_template_studio_page

st.set_page_config(page_title="JEE Review Studio", layout="wide")


@st.cache_resource
def _bootstrap():
    config = get_config()
    engine = get_engine(config.db_path, echo=config.db_echo)
    init_db(engine)
    session_factory = make_session_factory(engine)
    with session_scope(session_factory) as session:
        ensure_default_templates_registered(session)
    return session_factory, config


session_factory, config = _bootstrap()

upload_page = st.Page(lambda: render_upload_page(session_factory, config), title="Upload / Ingest",
                       icon="📥", url_path="upload", default=True)
review_page = st.Page(lambda: render_review_page(session_factory, config), title="Review",
                       icon="🔍", url_path="review")
template_studio_page = st.Page(lambda: render_template_studio_page(session_factory, config),
                                title="Template Studio", icon="🧩", url_path="template-studio")

st.navigation([upload_page, review_page, template_studio_page]).run()
