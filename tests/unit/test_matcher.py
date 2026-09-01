"""
Phase 4: score_against_template's exact weighted arithmetic, term by term,
using hand-crafted Fingerprint instances (matching the existing
hand-crafted-fixture test style).
"""
from datetime import datetime, timezone

from models.templates import TemplateVersion, MatchSignature
from templates.fingerprint import Fingerprint
from templates.matcher import score_against_template, match_templates

_SIG = MatchSignature(
    requires_text_layer=True,
    branding_strings=["MathonGo", "Question Paper"],
    question_marker_pattern=r"Q(\d+)\.$",
    option_marker_pattern=r"\((\d)\)",
    answer_key_marker="ANSWER KEY",
    min_question_marker_hits=10,
)


def _template_version():
    return TemplateVersion(
        template_id="jee_main_mathongo", version=1, kind="deterministic_text", status="validated",
        adapter_ref="legacy_mathongo_adapter", match_signature=_SIG,
        baseline_score=1.0, run_count=8, created_at=datetime.now(timezone.utc),
    )


def _perfect_fingerprint():
    return Fingerprint(
        requires_text_layer=True, page_count=15,
        branding_strings_found=["MathonGo", "Question Paper"],
        question_marker_hits=90, option_marker_hits=300, answer_key_marker_found=True,
    )


def test_perfect_match_scores_1_0():
    score, reasons = score_against_template(_perfect_fingerprint(), _template_version())
    assert score == 1.0


def test_no_text_layer_loses_text_layer_weight_only():
    fp = _perfect_fingerprint()
    fp.requires_text_layer = False
    score, reasons = score_against_template(fp, _template_version())
    assert score == 1.0 - 0.25
    assert any("mismatch" in r for r in reasons)


def test_missing_one_branding_string_loses_half_branding_weight():
    fp = _perfect_fingerprint()
    fp.branding_strings_found = ["MathonGo"]  # only 1 of 2 expected
    score, reasons = score_against_template(fp, _template_version())
    assert abs(score - (1.0 - (0.30 * 0.5))) < 1e-9


def test_question_marker_hits_below_floor_loses_that_weight():
    fp = _perfect_fingerprint()
    fp.question_marker_hits = 3  # below min_question_marker_hits=10
    score, reasons = score_against_template(fp, _template_version())
    assert score == 1.0 - 0.20


def test_no_option_markers_loses_that_weight():
    fp = _perfect_fingerprint()
    fp.option_marker_hits = 0
    score, reasons = score_against_template(fp, _template_version())
    assert score == 1.0 - 0.15


def test_no_answer_key_marker_loses_that_weight():
    fp = _perfect_fingerprint()
    fp.answer_key_marker_found = False
    score, reasons = score_against_template(fp, _template_version())
    assert abs(score - (1.0 - 0.10)) < 1e-9


def test_completely_unlike_fingerprint_scores_zero():
    fp = Fingerprint(
        requires_text_layer=False, page_count=29,
        branding_strings_found=[], question_marker_hits=0, option_marker_hits=0,
        answer_key_marker_found=False,
    )
    score, reasons = score_against_template(fp, _template_version())
    assert score == 0.0
    assert len(reasons) == 5  # every term reports a miss reason


def test_match_templates_sorts_descending():
    good_fp = _perfect_fingerprint()
    bad_tv = _template_version()
    bad_tv.baseline_score = None  # irrelevant to scoring, just a distinct instance
    results = match_templates(good_fp, [_template_version(), _template_version()])
    assert len(results) == 2
    assert results[0].fingerprint_score >= results[1].fingerprint_score
