"""
Phase 2: one test per rule_code, using hand-crafted Question/legacy-dict
objects (not full PDFs) — following test_models.py's _sample_legacy_question
pattern. Each proves the exact expected rule fires (and, where relevant,
that nothing else unexpected fires).

Note: BoundingBox rejecting an inverted/out-of-page rect at construction time
is already covered by test_models.py::test_bounding_box_rejects_invalid_range
— not duplicated here (see rules_geometry.py's module docstring for why that
makes the "bbox valid and within page" spec rule structurally redundant).
"""
import os
import uuid

import fitz
import pytest
from PIL import Image

from extractor import parse_pdf
from models.common import BoundingBox, SourceEvidence
from models.questions import ContentBlock, Option, Question, legacy_dict_to_question
from validation import (
    rules_answer_coverage,
    rules_geometry,
    rules_image_disposition,
    rules_option_consistency,
    rules_sequence,
    rules_stem_completeness,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "pdfs")
DOCUMENT_ID = "doc-test-1"


def _make_evidence(rect_key, page_number, rect=None):
    return SourceEvidence(
        page_number=page_number,
        bbox=BoundingBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0),
        extraction_method="legacy_deterministic",
    )


def _sample_legacy_question(**overrides):
    base = {
        "question_number": 5,
        "question_type": "mcq",
        "stem_text": "Sample stem text?",
        "stem_snippet": "images/q5_stem.png",
        "stem_images": [],
        "options": [
            {"label": "1", "text": "(1) A", "snippet": "images/q5_opt1.png", "images": []},
            {"label": "2", "text": "(2) B", "snippet": "images/q5_opt2.png", "images": []},
            {"label": "3", "text": "(3) C", "snippet": "images/q5_opt3.png", "images": []},
            {"label": "4", "text": "(4) D", "snippet": "images/q5_opt4.png", "images": []},
        ],
        "answer": "2",
    }
    base.update(overrides)
    return base


def _question_from_legacy(**overrides):
    legacy = _sample_legacy_question(**overrides)
    return legacy_dict_to_question(legacy, document_id=DOCUMENT_ID, page_start=1, page_end=1,
                                    make_evidence=_make_evidence)


def _rule_codes(issues):
    return {i.rule_code for i in issues}


# --- SEQ_* (rules_sequence.py) ---

def test_seq_duplicate_number():
    q1 = _question_from_legacy(question_number=12)
    q2 = _question_from_legacy(question_number=12)
    issues = rules_sequence.check_question_sequence([q1, q2], DOCUMENT_ID)
    assert "SEQ_DUPLICATE_NUMBER" in _rule_codes(issues)
    assert all(i.severity == "blocking" for i in issues if i.rule_code == "SEQ_DUPLICATE_NUMBER")
    assert len([i for i in issues if i.rule_code == "SEQ_DUPLICATE_NUMBER"]) == 2


def test_seq_gap():
    questions = [_question_from_legacy(question_number=n) for n in (1, 2, 4)]
    issues = rules_sequence.check_question_sequence(questions, DOCUMENT_ID)
    gap_issues = [i for i in issues if i.rule_code == "SEQ_GAP"]
    assert len(gap_issues) == 1
    assert gap_issues[0].severity == "warning"
    assert "3" in gap_issues[0].message


# --- ANSWER_* (rules_answer_coverage.py) ---

def test_answer_missing():
    q_ok = _question_from_legacy(question_number=1, answer="2")
    q_missing = _question_from_legacy(question_number=2, answer=None)
    issues = rules_answer_coverage.check_answer_coverage([q_ok, q_missing], DOCUMENT_ID)
    assert _rule_codes(issues) == {"ANSWER_MISSING"}
    assert issues[0].severity == "blocking"
    assert issues[0].question_id == q_missing.question_id


def test_answer_unsupported_format():
    q = _question_from_legacy(question_type="numerical", options=[], answer="not-a-number")
    issues = rules_answer_coverage.check_answer_coverage([q], DOCUMENT_ID)
    assert _rule_codes(issues) == {"ANSWER_UNSUPPORTED_FORMAT"}
    assert issues[0].severity == "warning"


def test_answer_points_to_missing_option():
    q = _question_from_legacy(answer="9")  # options only go up to label "4"
    issues = rules_answer_coverage.check_answer_coverage([q], DOCUMENT_ID)
    assert _rule_codes(issues) == {"ANSWER_POINTS_TO_MISSING_OPTION"}
    assert issues[0].severity == "error"


# --- OPT_* (rules_option_consistency.py) ---

def test_opt_count_unexpected_and_partial_marker_set():
    q = _question_from_legacy(options=[
        {"label": "1", "text": "(1) A", "snippet": "images/q5_opt1.png", "images": []},
        {"label": "2", "text": "(2) B", "snippet": "images/q5_opt2.png", "images": []},
    ], answer="2")
    issues = rules_option_consistency.check_option_consistency(q, DOCUMENT_ID)
    codes = _rule_codes(issues)
    assert "OPT_COUNT_UNEXPECTED" in codes
    assert "OPT_PARTIAL_MARKER_SET" in codes


def test_opt_duplicate_label():
    q = _question_from_legacy(options=[
        {"label": "1", "text": "(1) A", "snippet": "images/q5_opt1.png", "images": []},
        {"label": "2", "text": "(2) B", "snippet": "images/q5_opt2.png", "images": []},
        {"label": "2", "text": "(2) B again", "snippet": "images/q5_opt2b.png", "images": []},
        {"label": "4", "text": "(4) D", "snippet": "images/q5_opt4.png", "images": []},
    ])
    issues = rules_option_consistency.check_option_consistency(q, DOCUMENT_ID)
    assert "OPT_DUPLICATE_LABEL" in _rule_codes(issues)
    dup_issues = [i for i in issues if i.rule_code == "OPT_DUPLICATE_LABEL"]
    assert all(i.severity == "error" for i in dup_issues)


def test_opt_image_option_no_asset():
    evidence = _make_evidence("option_1", 1)
    option = Option(
        option_id=str(uuid.uuid4()),
        label="1",
        blocks=[ContentBlock(
            block_id=str(uuid.uuid4()),
            content_type="image",
            clean_crop_path=None,
            evidence=evidence,
            confidence=1.0,
            status="unreadable",
        )],
        evidence=evidence,
        confidence=1.0,
    )
    stem_block = ContentBlock(
        block_id=str(uuid.uuid4()), content_type="mixed", text="Stem.",
        evidence=evidence, confidence=1.0, status="extracted",
    )
    q = Question(
        question_id=str(uuid.uuid4()), document_id=DOCUMENT_ID, number="1",
        question_type="single_correct", stem_blocks=[stem_block], options=[option],
        answer="1", page_start=1, page_end=1, extraction_mode="deterministic_text",
        confidence=1.0, status="draft",
    )
    issues = rules_option_consistency.check_option_consistency(q, DOCUMENT_ID)
    assert "OPT_IMAGE_OPTION_NO_ASSET" in _rule_codes(issues)
    assert [i for i in issues if i.rule_code == "OPT_IMAGE_OPTION_NO_ASSET"][0].severity == "error"


# --- STEM_* (rules_stem_completeness.py) ---

def test_stem_trailing_stopword_and_no_terminal_punctuation():
    q = _question_from_legacy(stem_text="The wavelength of the")
    issues = rules_stem_completeness.check_stem_completeness(q, DOCUMENT_ID)
    codes = _rule_codes(issues)
    assert "STEM_TRAILING_STOPWORD" in codes
    assert "STEM_NO_TERMINAL_PUNCTUATION" in codes
    assert all(i.severity == "warning" for i in issues)


def test_stem_references_visual_without_image():
    q = _question_from_legacy(stem_text="The diagram is shown below.", stem_images=[])
    issues = rules_stem_completeness.check_stem_completeness(q, DOCUMENT_ID)
    assert "STEM_REFERENCES_VISUAL_WITHOUT_IMAGE" in _rule_codes(issues)


def test_stem_clean_text_has_no_completeness_issues():
    q = _question_from_legacy(stem_text="What is the speed of light in vacuum?")
    issues = rules_stem_completeness.check_stem_completeness(q, DOCUMENT_ID)
    assert issues == []


# --- GEOM_* (rules_geometry.py) ---

def test_geom_crop_too_small(tmp_path):
    img_path = tmp_path / "images"
    img_path.mkdir()
    Image.new("RGB", (5, 5), color=(10, 20, 30)).save(img_path / "tiny.png")

    evidence = _make_evidence("stem", 1)
    stem_block = ContentBlock(
        block_id=str(uuid.uuid4()), content_type="image", clean_crop_path="images/tiny.png",
        evidence=evidence, confidence=1.0, status="extracted",
    )
    q = Question(
        question_id=str(uuid.uuid4()), document_id=DOCUMENT_ID, number="1",
        question_type="numerical", stem_blocks=[stem_block], options=[],
        answer="1", page_start=1, page_end=1, extraction_mode="deterministic_text",
        confidence=1.0, status="draft",
    )
    issues = rules_geometry.check_geometry(q, str(tmp_path), DOCUMENT_ID)
    assert "GEOM_CROP_TOO_SMALL" in _rule_codes(issues)


def test_geom_crop_blank(tmp_path):
    img_path = tmp_path / "images"
    img_path.mkdir()
    Image.new("RGB", (100, 100), color=(200, 200, 200)).save(img_path / "blank.png")

    evidence = _make_evidence("stem", 1)
    stem_block = ContentBlock(
        block_id=str(uuid.uuid4()), content_type="image", clean_crop_path="images/blank.png",
        evidence=evidence, confidence=1.0, status="extracted",
    )
    q = Question(
        question_id=str(uuid.uuid4()), document_id=DOCUMENT_ID, number="1",
        question_type="numerical", stem_blocks=[stem_block], options=[],
        answer="1", page_start=1, page_end=1, extraction_mode="deterministic_text",
        confidence=1.0, status="draft",
    )
    issues = rules_geometry.check_geometry(q, str(tmp_path), DOCUMENT_ID)
    assert "GEOM_CROP_BLANK" in _rule_codes(issues)


def test_geom_crop_file_missing(tmp_path):
    evidence = _make_evidence("stem", 1)
    stem_block = ContentBlock(
        block_id=str(uuid.uuid4()), content_type="image", clean_crop_path="images/does_not_exist.png",
        evidence=evidence, confidence=1.0, status="extracted",
    )
    q = Question(
        question_id=str(uuid.uuid4()), document_id=DOCUMENT_ID, number="1",
        question_type="numerical", stem_blocks=[stem_block], options=[],
        answer="1", page_start=1, page_end=1, extraction_mode="deterministic_text",
        confidence=1.0, status="draft",
    )
    issues = rules_geometry.check_geometry(q, str(tmp_path), DOCUMENT_ID)
    assert "GEOM_CROP_FILE_MISSING" in _rule_codes(issues)
    assert [i for i in issues if i.rule_code == "GEOM_CROP_FILE_MISSING"][0].severity == "error"


def test_geom_no_crops_root_returns_no_issues():
    q = _question_from_legacy()
    assert rules_geometry.check_geometry(q, None, DOCUMENT_ID) == []


# --- IMG_CONSERVATION_MISMATCH (rules_image_disposition.py) ---

def test_img_conservation_mismatch(tmp_path):
    pdf_path = os.path.join(FIXTURES_DIR, "2012_may07.pdf")
    legacy_questions, _ = parse_pdf(pdf_path, str(tmp_path))
    assert any(q["stem_images"] for q in legacy_questions), "fixture must have at least one stem image to corrupt"

    corrupted = [dict(q) for q in legacy_questions]
    for q in corrupted:
        if q["stem_images"]:
            q["stem_images"] = []  # drop one real image's assignment, leaving it unaccounted for
            break

    doc = fitz.open(pdf_path)
    try:
        issues = rules_image_disposition.check_image_conservation([], corrupted, doc, DOCUMENT_ID)
    finally:
        doc.close()

    assert _rule_codes(issues) == {"IMG_CONSERVATION_MISMATCH"}
    assert issues[0].severity == "error"


def test_img_conservation_clean_paper_has_no_mismatch(tmp_path):
    pdf_path = os.path.join(FIXTURES_DIR, "2012_may07.pdf")
    legacy_questions, _ = parse_pdf(pdf_path, str(tmp_path))
    doc = fitz.open(pdf_path)
    try:
        issues = rules_image_disposition.check_image_conservation([], legacy_questions, doc, DOCUMENT_ID)
    finally:
        doc.close()
    assert issues == []
