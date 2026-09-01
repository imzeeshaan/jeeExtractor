"""
Bundles every rule module into one call — mirrors extraction.metrics's
compute_all_metrics bundling role for the (separate) invariant-metrics
concern.
"""
from validation import (
    rules_answer_coverage,
    rules_geometry,
    rules_image_disposition,
    rules_option_consistency,
    rules_sequence,
    rules_stem_completeness,
)


def run_validation(questions, legacy_questions, fitz_doc, document_id, crops_root=None) -> list:
    issues = []
    issues += rules_image_disposition.check_image_conservation(questions, legacy_questions, fitz_doc, document_id)
    issues += rules_answer_coverage.check_answer_coverage(questions, document_id)
    issues += rules_sequence.check_question_sequence(questions, document_id)
    for q in questions:
        issues += rules_option_consistency.check_option_consistency(q, document_id)
        issues += rules_stem_completeness.check_stem_completeness(q, document_id)
        issues += rules_geometry.check_geometry(q, crops_root, document_id)
    return issues


def issues_by_question(issues) -> dict:
    """question_id -> [issue_id, ...]. Document-level issues (question_id is
    None) are excluded — they belong to no single question."""
    result = {}
    for issue in issues:
        if issue.question_id is not None:
            result.setdefault(issue.question_id, []).append(issue.issue_id)
    return result


def summarize(issues) -> dict:
    """Counts by severity — mirrors compute_all_metrics's dict-of-scalars shape."""
    return {
        "issue_count": len(issues),
        "blocking_count": sum(1 for i in issues if i.severity == "blocking"),
        "error_count": sum(1 for i in issues if i.severity == "error"),
        "warning_count": sum(1 for i in issues if i.severity == "warning"),
        "info_count": sum(1 for i in issues if i.severity == "info"),
    }
