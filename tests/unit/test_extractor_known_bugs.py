"""
Phase 0: dedicated regression tests for the SPECIFIC bugs found and fixed this
session (HANDOFF.md §6). Each test pins the exact behavior confirmed by direct
execution before being written here, so a future change that reintroduces one
of these bugs fails with a specific, legible message instead of just "some
aggregate count changed by 1".
"""
import os
import re

from extractor import parse_pdf

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "pdfs")


def _parse(paper_id, tmp_path):
    pdf_path = os.path.join(FIXTURES_DIR, f"{paper_id}.pdf")
    return parse_pdf(pdf_path, str(tmp_path))


def _question(questions, number):
    return next(q for q in questions if q["question_number"] == number)


def test_may07_q11_options_wrap_across_page_break(tmp_path):
    questions, notes = _parse("2012_may07", tmp_path)
    q11 = _question(questions, 11)

    assert len(q11["options"]) == 4
    texts = {o["label"]: o["text"] for o in q11["options"]}
    assert texts["1"] == "(1) ℓ √2π"
    assert texts["2"] == "(2) ℓ √3π"
    assert texts["3"] == "(3) ℓ 3π"
    assert texts["4"] == "(4) ℓ 2π"
    for label in ("1", "2", "3", "4"):
        assert texts[label], f"Q11 option {label} has empty text"

    assert any(
        "Q11: options continue on page 3" in n for n in notes
    ), "expected a cross-page option-wrap note for Q11"


def test_may19_q16_stem_continues_across_page_break_with_diagram(tmp_path):
    questions, notes = _parse("2012_may19", tmp_path)
    q16 = _question(questions, 16)

    # regression guard against the exact truncation bug from HANDOFF §6.2:
    # the stem must NOT end mid-sentence at "...surface of the"
    assert not q16["stem_text"].rstrip().endswith("surface of the")
    assert q16["stem_text"].rstrip().endswith("hemisphere is")

    assert q16["stem_images"] == ["images/q16_stem_diagram_1.png"]

    assert any("Q16: options continue on page 4" in n for n in notes)
    assert any("Q16: stem continues on page 4" in n for n in notes)


def test_may19_q20_stem_continues_across_page_break_with_diagram(tmp_path):
    questions, notes = _parse("2012_may19", tmp_path)
    q20 = _question(questions, 20)

    assert q20["stem_text"].rstrip().endswith("displacement of the connector is represented by the figure. x")
    assert q20["stem_images"] == ["images/q20_stem_diagram_1.png"]

    assert any("Q20: options continue on page 5" in n for n in notes)
    assert any("Q20: stem continues on page 5" in n for n in notes)


def test_2020_numerical_questions_have_no_options(tmp_path):
    questions, notes = _parse("2020_jan07", tmp_path)

    expected_numerical = {21, 22, 23, 24, 25, 46, 47, 48, 49, 50, 71, 72, 73, 74, 75}
    actual_numerical = {q["question_number"] for q in questions if q["question_type"] == "numerical"}
    assert actual_numerical == expected_numerical

    for qnum in expected_numerical:
        q = _question(questions, qnum)
        assert q["options"] == [], f"Q{qnum} is numerical but has non-empty options"


def test_2020_multichar_numerical_answers_are_captured(tmp_path):
    """The pre-fix regex only captured a single digit inside the parens,
    silently dropping every one of these (HANDOFF §6.4)."""
    questions, notes = _parse("2020_jan07", tmp_path)
    expected_answers = {
        21: "10", 22: "60", 23: "600", 24: "175", 25: "11",
        46: "-2.7", 47: "10.6", 48: "23.03", 49: "1.67", 50: "2",
        71: "30", 72: "5", 73: "36", 74: "18", 75: "3",
    }
    for qnum, expected_answer in expected_answers.items():
        q = _question(questions, qnum)
        assert q["answer"] == expected_answer, f"Q{qnum}: expected answer {expected_answer!r}, got {q['answer']!r}"


def test_may19_q72_stem_not_corrupted_by_embedded_parenthetical(tmp_path):
    """Found while wiring Phase 1's bbox evidence (a strict BoundingBox
    validator caught an inverted rect): Q72's stem contains the literal text
    "f(1-a)-f(1)", and the embedded "(1)" was misdetected as an option
    marker sitting above the real option row, corrupting stem_y_end into a
    degenerate near-zero-height region (empty stem_text, near-blank crop).
    Fixed by rejecting any "(N)" match whose preceding character is
    alphanumeric (i.e. embedded in a function-call-like token) in
    find_opt_markers."""
    questions, notes = _parse("2012_may19", tmp_path)
    q72 = _question(questions, 72)

    assert q72["stem_text"] != ""
    assert "f(x)" in q72["stem_text"]
    assert q72["stem_rect"][1] < q72["stem_rect"][3], "stem_rect must not be inverted/degenerate"

    texts = {o["label"]: o["text"] for o in q72["options"]}
    assert texts["1"] == "(1) −53 3"
    assert texts["2"] == "(2) 53 3"
    assert texts["3"] == "(3) −55 3"
    assert texts["4"] == "(4) 55 3"


def test_no_inverted_or_degenerate_rects_anywhere(tmp_path):
    """Whole-suite sweep for the Q72 bug class: no stem_rect or option rect
    should ever have y0 >= y1 or x0 >= x1, across every fixture."""
    for paper_id in ["2012_may07", "2012_may12", "2012_may19", "2012_may26",
                      "2012_offline", "2019_jan09", "2019_jan12", "2020_jan07"]:
        questions, notes = _parse(paper_id, tmp_path / paper_id)
        for q in questions:
            r = q["stem_rect"]
            assert r[1] < r[3] and r[0] < r[2], f"{paper_id} Q{q['question_number']}: degenerate stem_rect {r}"
            for o in q["options"]:
                r = o.get("rect")
                if r is not None:
                    assert r[1] < r[3] and r[0] < r[2], (
                        f"{paper_id} Q{q['question_number']} option {o['label']}: degenerate rect {r}"
                    )


def test_answer_regex_handles_multichar_values():
    """Narrow unit test isolating just the answer-key regex, independent of
    any PDF fixture, so this specific behavior stays pinned even if the
    fixture files ever change."""
    sample = "21. (10)\n46. (-2.7)\n47. (10.6)\n48. (23.03)\n1. (2)\n"
    pattern = r'(\d+)\.\s*\(([^)]+)\)'
    parsed = {int(m.group(1)): m.group(2).strip() for m in re.finditer(pattern, sample)}
    assert parsed == {21: "10", 46: "-2.7", 47: "10.6", 48: "23.03", 1: "2"}
