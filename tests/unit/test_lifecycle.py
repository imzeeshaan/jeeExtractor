"""
Phase 4: rolling baseline formula + demotion threshold/run-count-guard,
hand-crafted TemplateVersion + score sequences.
"""
from datetime import datetime, timezone

from models.templates import TemplateVersion, MatchSignature
from templates.lifecycle import compute_validation_score, update_baseline_and_check_demotion
from models.validation import ValidationIssue


def _tv(status="validated", baseline_score=1.0, run_count=8):
    return TemplateVersion(
        template_id="jee_main_mathongo", version=1, kind="deterministic_text", status=status,
        adapter_ref="legacy_mathongo_adapter",
        match_signature=MatchSignature(
            requires_text_layer=True, branding_strings=["MathonGo"],
            question_marker_pattern=r"Q(\d+)\.$", option_marker_pattern=r"\((\d)\)",
            answer_key_marker="ANSWER KEY",
        ),
        baseline_score=baseline_score, run_count=run_count, created_at=datetime.now(timezone.utc),
    )


def _issue(severity):
    return ValidationIssue(issue_id="x", document_id="d", rule_code="X", severity=severity, message="m")


def test_compute_validation_score_clean():
    assert compute_validation_score([], []) == 1.0


def test_compute_validation_score_error_only():
    assert compute_validation_score([], [_issue("error")]) == 0.5


def test_compute_validation_score_blocking():
    assert compute_validation_score([], [_issue("blocking"), _issue("warning")]) == 0.0


def test_baseline_rolling_mean():
    tv = _tv(baseline_score=1.0, run_count=8)
    new_baseline, new_status = update_baseline_and_check_demotion(tv, 1.0)
    assert new_baseline == 1.0  # mean of 8x1.0 + 1.0 is still 1.0
    assert new_status == "validated"


def test_first_run_baseline_is_none_uses_new_score_directly():
    tv = _tv(baseline_score=None, run_count=0)
    new_baseline, new_status = update_baseline_and_check_demotion(tv, 0.8)
    assert new_baseline == 0.8


def test_demotion_triggers_when_score_drops_below_tolerance_with_enough_history():
    tv = _tv(status="validated", baseline_score=1.0, run_count=8)
    new_baseline, new_status = update_baseline_and_check_demotion(tv, 0.5)
    # 1.0 - 0.5 = 0.5 > 0.15 tolerance, run_count 8 >= 3 -> demote
    assert new_status == "monitored"
    assert new_baseline == (1.0 * 8 + 0.5) / 9


def test_no_demotion_within_tolerance():
    tv = _tv(status="validated", baseline_score=1.0, run_count=8)
    new_baseline, new_status = update_baseline_and_check_demotion(tv, 0.9)
    # 1.0 - 0.9 = 0.1 <= 0.15 tolerance -> stays validated
    assert new_status == "validated"


def test_no_demotion_before_minimum_run_count():
    tv = _tv(status="validated", baseline_score=1.0, run_count=1)
    new_baseline, new_status = update_baseline_and_check_demotion(tv, 0.0)
    # would clear the tolerance check, but run_count 1 < 3 -> guarded, no demotion
    assert new_status == "validated"


def test_monitored_stays_monitored_on_bad_score():
    tv = _tv(status="monitored", baseline_score=1.0, run_count=8)
    new_baseline, new_status = update_baseline_and_check_demotion(tv, 0.0)
    assert new_status == "monitored"
