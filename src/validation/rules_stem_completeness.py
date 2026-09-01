"""
Stem-completeness heuristics (spec §16.5) — all warning severity, per the
spec's own framing: "this is a review heuristic, not proof of error."

STEM_TRAILING_STOPWORD is the exact signature of the real bug found and
fixed this session (HANDOFF.md §6.2: May-19 Q16's stem ended
"...surface of the" before the fix). Had this rule existed then, it would
have caught that bug automatically instead of requiring a human to notice a
sentence looked cut off.

Two spec sub-checks are NOT implemented — no current module computes the
needed signal, and faking it would be worse than omitting it:
- "sentence ends before a referenced equation/diagram" (needs semantic
  understanding of where an inline equation was extracted, not just presence)
- "next page begins with unassigned content" (needs the adapter to retain
  next-page layout info post-conversion, which it doesn't today)
"""
import re
import uuid

from models.validation import ValidationIssue

_TRAILING_STOPWORDS = {
    "the", "a", "an", "of", "in", "on", "at", "by", "for", "with",
    "and", "or", "to", "is", "are", "was",
}
_VISUAL_REFERENCE_PHRASES = ("shown below", "shown in the figure", "shown in figure", "as shown")
_TERMINAL_PUNCTUATION = (".", "?", "!", ")")


def _stem_text(question) -> str:
    return " ".join(b.text for b in question.stem_blocks if b.text).strip()


def check_stem_completeness(question, document_id) -> list[ValidationIssue]:
    issues = []
    stem_text = _stem_text(question)

    if stem_text:
        last_word = re.sub(r"[^\w]", "", stem_text.split()[-1]).lower()
        if last_word in _TRAILING_STOPWORDS:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4()),
                document_id=document_id,
                question_id=question.question_id,
                field_path="stem_blocks",
                rule_code="STEM_TRAILING_STOPWORD",
                severity="warning",
                message=f"Question {question.number}'s stem ends on the stopword {last_word!r} — "
                        f"possibly truncated across a page break.",
            ))

    if not stem_text or stem_text.rstrip()[-1] not in _TERMINAL_PUNCTUATION:
        issues.append(ValidationIssue(
            issue_id=str(uuid.uuid4()),
            document_id=document_id,
            question_id=question.question_id,
            field_path="stem_blocks",
            rule_code="STEM_NO_TERMINAL_PUNCTUATION",
            severity="warning",
            message=f"Question {question.number}'s stem doesn't end with terminal punctuation "
                    f"({'/'.join(_TERMINAL_PUNCTUATION)}).",
        ))

    lowered = stem_text.lower()
    if any(phrase in lowered for phrase in _VISUAL_REFERENCE_PHRASES):
        has_image_block = any(b.content_type == "image" for b in question.stem_blocks)
        if not has_image_block:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4()),
                document_id=document_id,
                question_id=question.question_id,
                field_path="stem_blocks",
                rule_code="STEM_REFERENCES_VISUAL_WITHOUT_IMAGE",
                severity="warning",
                message=f"Question {question.number}'s stem references a figure but has no image block.",
            ))

    return issues
