"""
Phase 0 regression suite: freezes the CURRENT, UNMODIFIED behavior of
extractor.parse_pdf() against the 8 real JEE Main papers verified by hand
this session (see HANDOFF.md). This file must never import anything from
extractor.py other than parse_pdf/_find_answer_key_page, and must not be
affected by any change made in src/ during Phase 1.
"""
import os

import fitz
import pytest

from extractor import parse_pdf
from extraction.metrics import (
    count_embedded_images_in_question_range,
    count_assigned_images,
    answer_coverage,
    numerical_question_numbers,
    is_contiguous_numbering,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "pdfs")

# Exact expected values, re-confirmed by direct execution before this suite was
# written (see the approved plan) — do not "round" or approximate these.
EXPECTED = {
    "2012_may07":   {"question_count": 90, "numerical_count": 0,  "images_total": 13, "images_assigned": 13},
    "2012_may12":   {"question_count": 90, "numerical_count": 0,  "images_total": 24, "images_assigned": 24},
    "2012_may19":   {"question_count": 90, "numerical_count": 0,  "images_total": 20, "images_assigned": 20},
    "2012_may26":   {"question_count": 90, "numerical_count": 0,  "images_total": 19, "images_assigned": 19},
    "2012_offline": {"question_count": 90, "numerical_count": 0,  "images_total": 26, "images_assigned": 26},
    "2019_jan09":   {"question_count": 90, "numerical_count": 0,  "images_total": 43, "images_assigned": 43},
    "2019_jan12":   {"question_count": 90, "numerical_count": 0,  "images_total": 66, "images_assigned": 66},
    "2020_jan07":   {"question_count": 75, "numerical_count": 15, "images_total": 22, "images_assigned": 22},
}


@pytest.fixture(scope="module")
def parsed(tmp_path_factory):
    """Parse every fixture once per test session (parsing + image cropping
    is not free) and cache results keyed by paper id."""
    cache = {}

    def _get(paper_id):
        if paper_id not in cache:
            pdf_path = os.path.join(FIXTURES_DIR, f"{paper_id}.pdf")
            out_dir = tmp_path_factory.mktemp(f"parsed_{paper_id}")
            questions, notes = parse_pdf(pdf_path, str(out_dir))
            doc = fitz.open(pdf_path)
            cache[paper_id] = (questions, notes, doc)
        return cache[paper_id]

    yield _get

    for questions, notes, doc in cache.values():
        doc.close()


@pytest.mark.parametrize("paper_id", sorted(EXPECTED.keys()))
def test_question_count(parsed, paper_id):
    questions, notes, doc = parsed(paper_id)
    assert len(questions) == EXPECTED[paper_id]["question_count"]


@pytest.mark.parametrize("paper_id", sorted(EXPECTED.keys()))
def test_contiguous_question_numbers(parsed, paper_id):
    questions, notes, doc = parsed(paper_id)
    assert is_contiguous_numbering(questions), (
        f"{paper_id}: question numbers are not a contiguous 1..N sequence — "
        f"got {sorted(q['question_number'] for q in questions)}"
    )


@pytest.mark.parametrize("paper_id", sorted(EXPECTED.keys()))
def test_answer_coverage_is_complete(parsed, paper_id):
    """100% answer coverage is a hard requirement, not a threshold (HANDOFF §6.4/§8.1)."""
    questions, notes, doc = parsed(paper_id)
    coverage = answer_coverage(questions)
    missing = [q["question_number"] for q in questions if q.get("answer") is None]
    assert coverage == 1.0, f"{paper_id}: answer coverage {coverage:.3f}, missing questions: {missing}"


@pytest.mark.parametrize("paper_id", sorted(EXPECTED.keys()))
def test_numerical_question_count(parsed, paper_id):
    questions, notes, doc = parsed(paper_id)
    actual = len(numerical_question_numbers(questions))
    assert actual == EXPECTED[paper_id]["numerical_count"]


@pytest.mark.parametrize("paper_id", sorted(EXPECTED.keys()))
def test_whole_document_image_conservation(parsed, paper_id):
    """The invariant that caught every real cross-page bug this session:
    every embedded raster image in the question-page range must be assigned
    to exactly one question's stem_images or option images, whole-document."""
    questions, notes, doc = parsed(paper_id)
    total = count_embedded_images_in_question_range(doc)
    assigned = count_assigned_images(questions)
    expected = EXPECTED[paper_id]

    assert total == expected["images_total"], (
        f"{paper_id}: expected {expected['images_total']} embedded images in the "
        f"question-page range, found {total} — either the fixture changed or the "
        f"counting method drifted, investigate before touching the assigned-count assertion"
    )
    assert assigned == expected["images_assigned"], (
        f"{paper_id}: expected {expected['images_assigned']} images assigned to "
        f"questions, got {assigned}"
    )
    assert total == assigned, (
        f"{paper_id}: image conservation violated — {total} embedded vs {assigned} "
        f"assigned (delta {total - assigned}); see HANDOFF §6.2 for the exact bug "
        f"class this catches"
    )
