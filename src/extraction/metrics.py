"""
Shared, pure metric computations over `extractor.parse_pdf` output.

These are the exact checks that found every real bug documented in HANDOFF.md
(stem truncation across a page break, dropped multi-digit/decimal answers).
Both the Phase 0 regression tests and the Phase 1 LegacyMathonGoAdapter import
this module so the two can never silently drift into two different notions of
"correct" — one hand-copied version verified by hand, one used at runtime.

No PDF/DB/Pydantic imports here on purpose: these functions take already-parsed
data (a `fitz.Document` and/or a `questions` list) and return plain values.
"""
from extractor import _find_answer_key_page


def count_embedded_images_in_question_range(doc):
    """Total embedded raster images across every page BEFORE the answer-key page
    (or the whole document if there is no answer-key page). This is the
    "total" side of the whole-document image-conservation invariant."""
    answer_page_idx = _find_answer_key_page(doc)
    page_range = range(answer_page_idx if answer_page_idx is not None else doc.page_count)
    return sum(len(doc[i].get_images(full=True)) for i in page_range)


def count_assigned_images(questions):
    """Total images assigned to any question's stem_images or any option's
    images. This is the "assigned" side of the invariant — it must equal
    count_embedded_images_in_question_range(doc) exactly, for every
    MathonGo-format paper, or a real bug is present (see HANDOFF §6.2)."""
    assigned = sum(len(q["stem_images"]) for q in questions)
    assigned += sum(len(o["images"]) for q in questions for o in q["options"])
    return assigned


def answer_coverage(questions):
    """Fraction of questions with a non-None answer. Must be exactly 1.0 for
    every MathonGo-format paper with an answer key (HANDOFF §6.4/§8.1) — this
    is a hard requirement, not a soft threshold."""
    if not questions:
        return 0.0
    matched = sum(1 for q in questions if q.get("answer") is not None)
    return matched / len(questions)


def numerical_question_numbers(questions):
    """Sorted list of question_number for every question classified as
    question_type == "numerical" (2019+ JEE Main fill-in-the-blank pattern,
    HANDOFF §6.3)."""
    return sorted(q["question_number"] for q in questions if q["question_type"] == "numerical")


def is_contiguous_numbering(questions):
    """True iff question_number values are exactly 1..N with no gaps or
    duplicates. Catches silent question drops that raw counts alone can miss
    (e.g. losing Q40 but gaining a duplicate Q41 would still pass a bare
    len() check)."""
    numbers = sorted(q["question_number"] for q in questions)
    return numbers == list(range(1, len(numbers) + 1))


def compute_all_metrics(questions, doc):
    """Convenience bundle of every metric above, in the shape both the Phase 0
    tests and the Phase 1 adapter/StageRun want to record."""
    total = count_embedded_images_in_question_range(doc)
    assigned = count_assigned_images(questions)
    return {
        "question_count": len(questions),
        "answer_coverage": answer_coverage(questions),
        "numerical_count": len(numerical_question_numbers(questions)),
        "embedded_images_total": total,
        "embedded_images_assigned": assigned,
        "images_conserved": total == assigned,
        "contiguous_numbering": is_contiguous_numbering(questions),
    }
