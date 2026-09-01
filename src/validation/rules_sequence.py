"""
Question sequence/count rules (spec §16.3). Whole-document — operates on the
full question list, not one question at a time.

Per the spec: "do not invent an expected count for unknown documents" — there
is no external ground truth (e.g. a declared total) to compare against, so a
numbering gap is a warning (something to look at), never blocking.
"""
import uuid

from models.validation import ValidationIssue


def check_question_sequence(questions, document_id) -> list[ValidationIssue]:
    issues = []

    seen = {}
    for q in questions:
        seen.setdefault(q.number, []).append(q)
    for number, dupes in seen.items():
        if len(dupes) > 1:
            for q in dupes:
                issues.append(ValidationIssue(
                    issue_id=str(uuid.uuid4()),
                    document_id=document_id,
                    question_id=q.question_id,
                    field_path="number",
                    rule_code="SEQ_DUPLICATE_NUMBER",
                    severity="blocking",
                    message=f"Question number {number!r} appears {len(dupes)} times in this document.",
                ))

    numeric_numbers = sorted(int(n) for n in seen if n.isdigit())
    if numeric_numbers:
        expected = set(range(numeric_numbers[0], numeric_numbers[-1] + 1))
        missing = sorted(expected - set(numeric_numbers))
        for n in missing:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4()),
                document_id=document_id,
                rule_code="SEQ_GAP",
                severity="warning",
                message=f"Question number {n} is missing from the sequence (gap between "
                        f"{numeric_numbers[0]} and {numeric_numbers[-1]}).",
            ))

    return issues
