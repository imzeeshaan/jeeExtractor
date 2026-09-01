"""
Answer-key coverage rules (spec §16.2). 100% coverage is a hard requirement
for automatic approval, not a threshold — enforced here per-question via
ANSWER_MISSING (blocking), so a single unmatched answer is never silently
averaged away in an aggregate percentage.

ANSWER_DUPLICATE (duplicate entries on the answer-key page itself) is
deliberately NOT implemented: parse_pdf's answer regex builds a plain
{question_number: answer} dict, and a duplicate key in the source text
already silently overwrites (last-write-wins) before this layer ever sees
the data — the information needed to detect it doesn't survive extraction.
Documented as a known gap rather than faked.
"""
import uuid

from models.validation import ValidationIssue


def _is_float_parseable(value: str) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def check_answer_coverage(questions, document_id) -> list[ValidationIssue]:
    issues = []
    for q in questions:
        if q.answer is None:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4()),
                document_id=document_id,
                question_id=q.question_id,
                field_path="answer",
                rule_code="ANSWER_MISSING",
                severity="blocking",
                message=f"Question {q.number} has no matched answer-key entry.",
            ))
            continue

        if q.question_type == "numerical":
            if not _is_float_parseable(q.answer):
                issues.append(ValidationIssue(
                    issue_id=str(uuid.uuid4()),
                    document_id=document_id,
                    question_id=q.question_id,
                    field_path="answer",
                    rule_code="ANSWER_UNSUPPORTED_FORMAT",
                    severity="warning",
                    message=f"Question {q.number} is numerical but its answer {q.answer!r} isn't a parseable number.",
                ))
        else:
            option_labels = {o.label for o in q.options}
            if isinstance(q.answer, str) and q.answer not in option_labels:
                issues.append(ValidationIssue(
                    issue_id=str(uuid.uuid4()),
                    document_id=document_id,
                    question_id=q.question_id,
                    field_path="answer",
                    rule_code="ANSWER_POINTS_TO_MISSING_OPTION",
                    severity="error",
                    message=(
                        f"Question {q.number}'s answer {q.answer!r} doesn't match any of its "
                        f"option labels {sorted(option_labels)}."
                    ),
                ))
    return issues
