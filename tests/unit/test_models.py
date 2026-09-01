"""
Phase 1: BoundingBox validation edge cases + the legacy<->canonical
round-trip conversion functions.
"""
import pytest
from pydantic import ValidationError

from models.common import BoundingBox, SourceEvidence
from models.questions import question_to_legacy_dict, legacy_dict_to_question


def test_bounding_box_accepts_valid_range():
    bbox = BoundingBox(x0=0.1, y0=0.2, x1=0.9, y1=0.8)
    assert bbox.x0 == 0.1


@pytest.mark.parametrize("kwargs", [
    dict(x0=0.5, y0=0.1, x1=0.4, y1=0.9),   # x0 >= x1
    dict(x0=0.1, y0=0.9, x1=0.9, y1=0.1),   # y0 >= y1
    dict(x0=-0.1, y0=0.1, x1=0.9, y1=0.9),  # x0 < 0
    dict(x0=0.1, y0=0.1, x1=1.1, y1=0.9),   # x1 > 1
])
def test_bounding_box_rejects_invalid_range(kwargs):
    with pytest.raises((ValidationError, AssertionError)):
        BoundingBox(**kwargs)


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
        "stem_images": ["images/q5_stem_diagram_1.png"],
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


def test_legacy_to_canonical_round_trip_preserves_mcq_fields():
    legacy = _sample_legacy_question()
    canonical = legacy_dict_to_question(legacy, document_id="doc-1", page_start=3, page_end=3,
                                         make_evidence=_make_evidence)

    assert canonical.number == "5"
    assert canonical.question_type == "single_correct"
    assert len(canonical.options) == 4
    assert canonical.answer == "2"

    back = question_to_legacy_dict(canonical)
    assert back["question_number"] == 5
    assert back["question_type"] == "mcq"
    assert back["stem_text"] == "Sample stem text?"
    assert back["stem_snippet"] == "images/q5_stem.png"
    assert back["stem_images"] == ["images/q5_stem_diagram_1.png"]
    assert back["answer"] == "2"
    assert [o["label"] for o in back["options"]] == ["1", "2", "3", "4"]
    assert [o["text"] for o in back["options"]] == ["(1) A", "(2) B", "(3) C", "(4) D"]


def test_legacy_to_canonical_round_trip_preserves_numerical_type():
    legacy = _sample_legacy_question(question_type="numerical", options=[], answer="-2.7")
    canonical = legacy_dict_to_question(legacy, document_id="doc-1", page_start=4, page_end=4,
                                         make_evidence=_make_evidence)

    assert canonical.question_type == "numerical"
    assert canonical.options == []
    assert canonical.answer == "-2.7"

    back = question_to_legacy_dict(canonical)
    assert back["question_type"] == "numerical"
    assert back["options"] == []
    assert back["answer"] == "-2.7"


def test_legacy_to_canonical_rejects_unknown_question_type():
    legacy = _sample_legacy_question(question_type="match_columns")
    with pytest.raises(ValueError):
        legacy_dict_to_question(legacy, document_id="doc-1", page_start=1, page_end=1,
                                 make_evidence=_make_evidence)
