"""
Option consistency rules (spec §16.4). Per-question.

EXPECTED_MCQ_OPTION_COUNT is hardcoded to 4 — this template (MathonGo JEE
Main) is always exactly 0 (numerical) or 4 (MCQ) options, the same way
extractor.py hardcodes its ["1","2","3","4"] label loop. Not a general
"any option count is fine" system; a future multi-template system would make
this configurable per template (Phase 4 concern).
"""
import uuid

from models.validation import ValidationIssue

EXPECTED_MCQ_OPTION_COUNT = 4


def check_option_consistency(question, document_id) -> list[ValidationIssue]:
    issues = []
    q = question

    labels_seen = {}
    for opt in q.options:
        labels_seen.setdefault(opt.label, []).append(opt)
    for label, dupes in labels_seen.items():
        if len(dupes) > 1:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4()),
                document_id=document_id,
                question_id=q.question_id,
                field_path="options",
                rule_code="OPT_DUPLICATE_LABEL",
                severity="error",
                message=f"Question {q.number} has {len(dupes)} options labeled {label!r}.",
            ))

    option_count = len(q.options)
    if q.question_type == "numerical":
        if option_count != 0:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4()),
                document_id=document_id,
                question_id=q.question_id,
                field_path="options",
                rule_code="OPT_COUNT_UNEXPECTED",
                severity="warning",
                message=f"Question {q.number} is numerical but has {option_count} option(s); expected 0.",
            ))
    else:
        if option_count != EXPECTED_MCQ_OPTION_COUNT:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4()),
                document_id=document_id,
                question_id=q.question_id,
                field_path="options",
                rule_code="OPT_COUNT_UNEXPECTED",
                severity="warning",
                message=(
                    f"Question {q.number} has {option_count} option(s); expected "
                    f"{EXPECTED_MCQ_OPTION_COUNT}."
                ),
            ))
        if 1 <= option_count <= EXPECTED_MCQ_OPTION_COUNT - 1:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4()),
                document_id=document_id,
                question_id=q.question_id,
                field_path="options",
                rule_code="OPT_PARTIAL_MARKER_SET",
                severity="error",
                message=(
                    f"Question {q.number} has only {option_count} of "
                    f"{EXPECTED_MCQ_OPTION_COUNT} expected options — some option markers "
                    f"were likely not found during extraction."
                ),
            ))

    for opt in q.options:
        for block in opt.blocks:
            if block.content_type == "image" and not block.clean_crop_path:
                issues.append(ValidationIssue(
                    issue_id=str(uuid.uuid4()),
                    document_id=document_id,
                    question_id=q.question_id,
                    field_path=f"options[{opt.label}].blocks",
                    rule_code="OPT_IMAGE_OPTION_NO_ASSET",
                    severity="error",
                    message=f"Question {q.number} option {opt.label} has an image-typed block with no crop asset.",
                ))

    return issues
