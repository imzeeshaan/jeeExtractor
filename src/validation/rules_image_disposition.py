"""
Image-disposition rule (spec §16.1).

Decision: does NOT implement the full 5-category classifier
(stem|option|shared_context|header_footer_branding|intentionally_excluded|
unresolved). Confirmed by reading extractor.py: this template's header
(_header_bottom_y matching "JEE Main \\d{4}"/"Question Paper"/"MathonGo") and
footer ("Join the Most Relevant Test Series") are detected purely as TEXT
spans, never embedded raster images — so no image has ever needed a
header_footer_branding/intentionally_excluded disposition for this template,
and all 8 verified papers already hit exact image-count equality with zero
exclusions (HANDOFF.md §6.2/§8.1). Building the full classifier now would be
speculative code for a case with no evidence it's needed.

Reuses extraction.metrics's existing whole-document counting functions
unchanged — this rule is just that same proven invariant, now emitting a
persisted ValidationIssue instead of only a metrics dict.

TODO: when a second publisher/template is onboarded (Phase 4), a real image
whose count-conservation still failed here would need the full classifier to
distinguish "legitimately excluded logo" from "an actual bug" — not needed
today.
"""
import uuid

from extraction.metrics import count_embedded_images_in_question_range, count_assigned_images
from models.validation import ValidationIssue


def check_image_conservation(questions, legacy_questions, doc, document_id) -> list[ValidationIssue]:
    total = count_embedded_images_in_question_range(doc)
    assigned = count_assigned_images(legacy_questions)
    if total == assigned:
        return []
    return [ValidationIssue(
        issue_id=str(uuid.uuid4()),
        document_id=document_id,
        rule_code="IMG_CONSERVATION_MISMATCH",
        severity="error",
        message=(
            f"{total} embedded image(s) found in the question-page range but only "
            f"{assigned} assigned to a question's stem/option images — a real extraction "
            f"bug is likely (see HANDOFF.md §6.2 for the exact bug class this catches)."
        ),
    )]
