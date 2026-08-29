import streamlit as st
import tempfile
import os
import json
import shutil
import zipfile
from extractor import render_pages, parse_pdf

st.set_page_config(page_title="JEE Question Extractor", layout="wide")
st.title("JEE Question & Answer Extractor")

if "session_dir" not in st.session_state:
    st.session_state.session_dir = tempfile.mkdtemp(prefix="jee_extract_")
if "pdf_path" not in st.session_state:
    st.session_state.pdf_path = None
if "page_images" not in st.session_state:
    st.session_state.page_images = None
if "questions" not in st.session_state:
    st.session_state.questions = None
if "notes" not in st.session_state:
    st.session_state.notes = None

session_dir = st.session_state.session_dir

SAMPLE_DIR = os.path.join(os.path.dirname(__file__), "sample_data")
SAMPLE_PDFS = {
    "JEE Main 2012 (07 May Online)": "sample_jee_2012_may7.pdf",
    "JEE Main 2012 (12 May Online)": "sample_jee_2012_may12.pdf",
    "JEE Main 2012 (19 May Online)": "sample_jee_2012_may19.pdf",
    "JEE Main 2012 (26 May Online)": "sample_jee_2012_may26.pdf",
    "JEE Main 2012 (Offline)": "sample_jee_2012_offline.pdf",
}

source = st.radio("Choose a PDF", ["Upload my own PDF", "Use a sample paper"], horizontal=True)

if source == "Use a sample paper":
    sample_label = st.selectbox("Sample paper", list(SAMPLE_PDFS.keys()))
    sample_path = os.path.join(SAMPLE_DIR, SAMPLE_PDFS[sample_label])
    if st.session_state.pdf_path != sample_path:
        st.session_state.pdf_path = sample_path
        st.session_state.page_images = None
        st.session_state.questions = None
        st.session_state.notes = None
else:
    uploaded = st.file_uploader("Upload a JEE exam PDF", type=["pdf"])
    if uploaded is not None:
        pdf_path = os.path.join(session_dir, uploaded.name)
        if st.session_state.pdf_path != pdf_path:
            with open(pdf_path, "wb") as f:
                f.write(uploaded.getbuffer())
            st.session_state.pdf_path = pdf_path
            st.session_state.page_images = None
            st.session_state.questions = None
            st.session_state.notes = None
    elif st.session_state.pdf_path and os.path.dirname(st.session_state.pdf_path) == SAMPLE_DIR:
        st.session_state.pdf_path = None
        st.session_state.page_images = None
        st.session_state.questions = None
        st.session_state.notes = None

if st.session_state.pdf_path:
    st.subheader("PDF Preview")

    if st.session_state.page_images is None:
        with st.spinner("Rendering PDF pages..."):
            preview_dir = os.path.join(session_dir, "preview")
            st.session_state.page_images = render_pages(st.session_state.pdf_path, preview_dir)

    with st.expander(f"View all {len(st.session_state.page_images)} pages", expanded=True):
        for i, img_path in enumerate(st.session_state.page_images):
            st.image(img_path, caption=f"Page {i + 1}", use_container_width=True)

    st.divider()

    if st.button("Extract Questions and Answers", type="primary"):
        with st.spinner("Extracting questions, options, diagrams, and answers..."):
            out_dir = os.path.join(session_dir, "extracted")
            if os.path.exists(out_dir):
                shutil.rmtree(out_dir)
            questions, notes = parse_pdf(st.session_state.pdf_path, out_dir)
            st.session_state.questions = questions
            st.session_state.notes = notes
            st.session_state.out_dir = out_dir


def _select_option(qnum, chosen_label):
    for lbl in ["1", "2", "3", "4"]:
        key = f"q{qnum}_opt{lbl}"
        st.session_state[key] = (lbl == chosen_label)


if st.session_state.questions:
    questions = st.session_state.questions
    notes = st.session_state.notes
    out_dir = st.session_state.out_dir

    st.subheader(f"Extracted {len(questions)} Questions")

    if notes:
        with st.expander(f"{len(notes)} note(s) from extraction", expanded=False):
            for n in notes:
                st.write(f"- {n}")

    for q in questions:
        qnum = q["question_number"]
        with st.expander(f"Q{qnum}"):
            st.markdown(f"**{q['stem_text']}**")
            if q.get("stem_snippet"):
                p = os.path.join(out_dir, q["stem_snippet"])
                if os.path.exists(p):
                    st.image(p, caption="Question (visual reference)")
            for img in q.get("stem_images", []):
                p = os.path.join(out_dir, img)
                if os.path.exists(p):
                    st.image(p, caption="Question diagram", width=350)

            st.markdown("---")
            for opt in q["options"]:
                label = opt["label"]
                col1, col2 = st.columns([0.08, 0.92])
                with col1:
                    st.checkbox(
                        "",
                        key=f"q{qnum}_opt{label}",
                        on_change=_select_option,
                        args=(qnum, label),
                        label_visibility="collapsed",
                    )
                with col2:
                    st.write(f"**({label})** {opt['text']}")
                    for img in opt.get("images", []):
                        p = os.path.join(out_dir, img)
                        if os.path.exists(p):
                            st.image(p, width=220)

            if q.get("answer"):
                st.caption(f"Answer key: ({q['answer']})")

    st.divider()
    st.subheader("Download Results")

    json_path = os.path.join(out_dir, "questions.json")
    with open(json_path, "w") as f:
        json.dump(questions, f, indent=2)

    md_lines = ["# Extracted Questions\n"]
    for q in questions:
        md_lines.append(f"## Q{q['question_number']}\n")
        md_lines.append(f"{q['stem_text']}\n")
        md_lines.append(f"![stem]({q['stem_snippet']})\n")
        for img in q.get("stem_images", []):
            md_lines.append(f"**Diagram:** ![diagram]({img})\n")
        for opt in q["options"]:
            md_lines.append(f"**({opt['label']})** {opt['text']}")
            for img in opt.get("images", []):
                md_lines.append(f"  ![opt]({img})")
            md_lines.append("")
        if q["answer"]:
            md_lines.append(f"**Answer:** ({q['answer']})\n")
        md_lines.append("\n---\n")
    md_path = os.path.join(out_dir, "questions.md")
    with open(md_path, "w") as f:
        f.write("\n".join(md_lines))

    zip_path = os.path.join(session_dir, "extraction_result.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(out_dir):
            for file in files:
                full = os.path.join(root, file)
                arcname = os.path.relpath(full, out_dir)
                zf.write(full, arcname)

    with open(zip_path, "rb") as f:
        st.download_button(
            "Download all results (JSON + Markdown + images, .zip)",
            f,
            file_name="jee_extraction_result.zip",
            mime="application/zip",
        )
