"""
Phase 4: compute_fingerprint + score_against_template, against all 8 real
known-good fixtures (must score high) and the real JEE Advanced "unknown
paper" fixture (must score low) — concrete, falsifiable assertions.
"""
import os
from datetime import datetime, timezone

import pytest

from models.templates import TemplateVersion, MatchSignature
from templates.fingerprint import compute_fingerprint
from templates.matcher import score_against_template

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "pdfs")
UNSUPPORTED_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "unsupported")

PAPER_IDS = [
    "2012_may07", "2012_may12", "2012_may19", "2012_may26", "2012_offline",
    "2019_jan09", "2019_jan12", "2020_jan07",
]


def _mathongo_template_version():
    return TemplateVersion(
        template_id="jee_main_mathongo",
        version=1,
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
        created_at=datetime.now(timezone.utc),
    )


@pytest.mark.parametrize("paper_id", PAPER_IDS)
def test_known_good_papers_score_high(paper_id):
    pdf_path = os.path.join(FIXTURES_DIR, f"{paper_id}.pdf")
    fp = compute_fingerprint(pdf_path)
    score, reasons = score_against_template(fp, _mathongo_template_version())
    assert score >= 0.85, f"{paper_id}: expected score >= 0.85, got {score:.2f} ({reasons})"


def test_unsupported_paper_scores_low():
    pdf_path = os.path.join(UNSUPPORTED_DIR, "JEE_ADV_2016-1.pdf")
    fp = compute_fingerprint(pdf_path)
    assert fp.requires_text_layer is False, "JEE ADV 2016-1 is expected to have zero extractable text"
    score, reasons = score_against_template(fp, _mathongo_template_version())
    assert score < 0.65, f"expected a low score for the unsupported paper, got {score:.2f} ({reasons})"
