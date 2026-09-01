"""
Confidence scoring (spec §17): "a routing score, not a scientific
probability." Uses ONLY the signals that actually exist in this codebase
today — there is no OCR, vision, template-match, or user-correction signal
anywhere yet, so this deliberately does not stub out fake scores for those.
The two real signals: (a) whether extraction produced real content vs. an
empty/fallback value, and (b) validation rule outcomes for that question.
"""

_SEVERITY_PENALTY = {"blocking": 1.00, "error": 0.30, "warning": 0.10, "info": 0.0}
_DOCUMENT_LEVEL_PENALTY = 0.05
_EMPTY_CONTENT_CAP = 0.5


def compute_question_confidence(question, question_issues, document_level_issues) -> float:
    """Starts at 1.0 (deterministic extraction, no probabilistic model in the
    loop). Subtracts the worst applicable per-question severity penalty
    (a "blocking" issue always floors the score to 0.0, satisfying the field
    policy's "<0.70 or blocking issue -> mandatory review" by construction —
    no separate blocking check needed downstream). Subtracts a smaller flat
    penalty if any document-level issue exists (a whole-document problem like
    an image-conservation mismatch casts some doubt on every question in it).
    Additionally capped at 0.5 if extraction found no real stem content or no
    answer — "extraction found nothing" is a stronger negative signal than
    any rule computed from that same emptiness could independently express."""
    score = 1.0
    if question_issues:
        score -= max(_SEVERITY_PENALTY[issue.severity] for issue in question_issues)
    if document_level_issues:
        score -= _DOCUMENT_LEVEL_PENALTY
    score = max(score, 0.0)

    stem_has_content = any(b.text or b.clean_crop_path for b in question.stem_blocks)
    if not stem_has_content or question.answer is None:
        score = min(score, _EMPTY_CONTENT_CAP)

    return round(score, 4)


def compute_document_trust_tier(questions, all_issues) -> str:
    """No template-lifecycle concept exists yet (Phase 4) -- "validated
    deterministic template" isn't something Phase 2 can check, so trust tier
    reduces to a pure function of validation-issue severity across the whole
    document, the only real signal available. HIGH is deliberately reachable
    on zero hard-rule violations alone (not withheld until a template
    lifecycle exists) -- otherwise the tier would be meaningless for the only
    template this system has today. Any "error" (not just "blocking")
    demotes below MEDIUM, since "error" always indicates a concrete,
    non-heuristic defect even when not blocking."""
    if any(issue.severity == "blocking" for issue in all_issues):
        return "NONE"
    if any(issue.severity == "error" for issue in all_issues):
        return "LOW"
    if any(issue.severity == "warning" for issue in all_issues):
        return "MEDIUM"
    return "HIGH"
