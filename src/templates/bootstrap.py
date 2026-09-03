"""
Registers the one real template this system has — idempotent, called once
at each entry point's startup (review_app.py, scripts/run_adapter.py),
immediately after their existing init_db(engine) call, same pattern already
used for that call.

Starts status="validated" (not "candidate"/"draft") with baseline_score=1.0,
run_count=8: this exact adapter has already cleared 8/8 clean runs across 8
real documents (test_adapter_integration.py's
test_validation_finds_zero_error_or_blocking_issues_on_verified_clean_papers)
— far more rigorous than the lifecycle's own stated promotion bar of "3
clean runs across 3 different documents" (HANDOFF.md §8.4). Starting it at
candidate/draft would be theater, not honesty — it would pretend a fresh,
unproven template is being evaluated when the historical evidence already
exists in the committed test suite.
"""
from datetime import datetime, timezone

from db.repositories import TemplateRepository
from models.templates import Template, TemplateVersion, MatchSignature

MATHONGO_TEMPLATE_ID = "jee_main_mathongo"
MATHONGO_VERSION = 1


def ensure_default_templates_registered(session) -> None:
    repo = TemplateRepository(session)
    if repo.get_version(MATHONGO_TEMPLATE_ID, MATHONGO_VERSION) is not None:
        return

    now = datetime.now(timezone.utc)
    repo.register(
        Template(
            template_id=MATHONGO_TEMPLATE_ID,
            name="JEE Main (MathonGo)",
            document_family="jee_main",
            created_at=now,
        ),
        TemplateVersion(
            template_id=MATHONGO_TEMPLATE_ID,
            version=MATHONGO_VERSION,
            kind="deterministic_text",
            status="validated",
            adapter_ref="legacy_mathongo_adapter",
            match_signature=MatchSignature(
                requires_text_layer=True,
                branding_strings=["MathonGo", "Question Paper"],
                question_marker_pattern=r"Q(\d+)\.$",
                option_marker_pattern=r"\((\d)\)",
                answer_key_marker="ANSWER KEY",
                min_question_marker_hits=10,
            ),
            baseline_score=1.0,
            run_count=8,
            created_at=now,
        ),
    )


# Phase 5: the vision-layout template, for scanned documents with no text
# layer (e.g. JEE Advanced). Registered as status="draft" — NOT "validated"
# like MathonGo above — because zero proven runs exist for it, unlike
# MathonGo's bootstrapped 8. min_question_marker_hits=0 and
# requires_text_layer=False make its match_signature the honest inverse of
# a text-based template's, scored by matcher.py's separate
# _score_vision_template formula.
VISION_TEMPLATE_ID = "jee_advanced_vision_layout"
VISION_TEMPLATE_VERSION = 1


def ensure_vision_layout_template_registered(session) -> None:
    repo = TemplateRepository(session)
    if repo.get_version(VISION_TEMPLATE_ID, VISION_TEMPLATE_VERSION) is not None:
        return

    now = datetime.now(timezone.utc)
    repo.register(
        Template(
            template_id=VISION_TEMPLATE_ID,
            name="JEE Advanced (vision layout)",
            document_family="jee_advanced",
            created_at=now,
        ),
        TemplateVersion(
            template_id=VISION_TEMPLATE_ID,
            version=VISION_TEMPLATE_VERSION,
            kind="vision_layout",
            status="draft",
            adapter_ref="vision_layout_adapter",
            match_signature=MatchSignature(
                requires_text_layer=False,
                branding_strings=[],
                question_marker_pattern="",
                option_marker_pattern="",
                answer_key_marker="",
                min_question_marker_hits=0,
            ),
            baseline_score=None,
            run_count=0,
            created_at=now,
        ),
    )
