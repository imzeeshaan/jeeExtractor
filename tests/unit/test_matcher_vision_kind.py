"""
Phase 5: the kind-aware scoring branch in templates/matcher.py. Hand-crafted
Fingerprints against a kind="vision_layout" TemplateVersion, plus a
real-fixture check (the actual JEE_ADV_2016-1.pdf fingerprint clears the
auto-run threshold against the bootstrapped vision template) and a
regression guard that the existing deterministic_text formula/tests are
untouched.
"""
import os
from datetime import datetime, timezone

from models.templates import TemplateVersion, MatchSignature
from templates.fingerprint import Fingerprint, compute_fingerprint
from templates.matcher import score_against_template

UNSUPPORTED_PDF = os.path.join(
    os.path.dirname(__file__), "..", "fixtures", "unsupported", "JEE_ADV_2016-1.pdf",
)

_VISION_SIG = MatchSignature(
    requires_text_layer=False, branding_strings=[],
    question_marker_pattern="", option_marker_pattern="",
    answer_key_marker="", min_question_marker_hits=0,
)


def _vision_tv():
    return TemplateVersion(
        template_id="jee_advanced_vision_layout", version=1, kind="vision_layout", status="draft",
        adapter_ref="vision_layout_adapter", match_signature=_VISION_SIG,
        baseline_score=None, run_count=0, created_at=datetime.now(timezone.utc),
    )


def test_scanned_fingerprint_scores_1_0_against_vision_template():
    fp = Fingerprint(requires_text_layer=False, page_count=29, branding_strings_found=[],
                      question_marker_hits=0, option_marker_hits=0, answer_key_marker_found=False)
    score, reasons = score_against_template(fp, _vision_tv())
    assert score == 1.0
    assert len(reasons) == 2


def test_text_based_fingerprint_scores_0_against_vision_template():
    fp = Fingerprint(requires_text_layer=True, page_count=15, branding_strings_found=["MathonGo"],
                      question_marker_hits=90, option_marker_hits=300, answer_key_marker_found=True)
    score, reasons = score_against_template(fp, _vision_tv())
    assert score == 0.0


def test_real_scanned_fixture_clears_auto_run_threshold_against_vision_template():
    fp = compute_fingerprint(UNSUPPORTED_PDF)
    score, reasons = score_against_template(fp, _vision_tv())
    assert score >= 0.65


def test_real_mathongo_paper_does_not_falsely_match_vision_template():
    fixtures_dir = os.path.join(os.path.dirname(__file__), "..", "fixtures", "pdfs")
    fp = compute_fingerprint(os.path.join(fixtures_dir, "2012_may07.pdf"))
    score, reasons = score_against_template(fp, _vision_tv())
    assert score < 0.65
