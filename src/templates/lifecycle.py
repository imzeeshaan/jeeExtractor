"""
Template lifecycle tracking (spec §18): rolling baseline + demotion.

A simple cumulative mean is used for the baseline — not exponential decay or
anything fancier — because there are only 8 real historical runs total
across all fixtures and zero real drift signal yet to tune a decay curve
against; a mean is the least-surprising choice that isn't premature.
"""
_DEMOTION_TOLERANCE = 0.15   # score more than this far below baseline -> demote
_MIN_RUNS_BEFORE_DEMOTION = 3  # don't demote off one bad run alone


def compute_validation_score(questions, issues) -> float:
    """Coarse on purpose — reuses exactly the same severity partition
    compute_document_trust_tier already uses, collapsed to a number so it
    can be averaged: 1.0 if zero error/blocking issues, 0.5 if any error but
    no blocking, 0.0 if any blocking."""
    if any(i.severity == "blocking" for i in issues):
        return 0.0
    if any(i.severity == "error" for i in issues):
        return 0.5
    return 1.0


def update_baseline_and_check_demotion(template_version, new_score: float):
    """Returns (new_baseline, new_status). Only ever demotes validated ->
    monitored (once run_count >= _MIN_RUNS_BEFORE_DEMOTION, guarding against
    flapping status off one bad run) — monitored is already the
    review-required state per spec §18, so a monitored template that scores
    badly again simply stays monitored; there's no further demotion target
    defined this phase."""
    run_count = template_version.run_count
    baseline = template_version.baseline_score

    if baseline is None:
        new_baseline = new_score
    else:
        new_baseline = (baseline * run_count + new_score) / (run_count + 1)

    new_status = template_version.status
    if (
        template_version.status == "validated"
        and run_count >= _MIN_RUNS_BEFORE_DEMOTION
        and baseline is not None
        and new_score < baseline - _DEMOTION_TOLERANCE
    ):
        new_status = "monitored"

    return new_baseline, new_status
