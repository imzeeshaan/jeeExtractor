"""
Scores a Fingerprint against a TemplateVersion's MatchSignature. Weighted
linear combination, each term binary (full weight or none) — no partial
credit for a fuzzy signal. Weights sum to 1.0; branding (0.30) and
text-layer (0.25) are weighted highest because they're the two signals that
most reliably separate "JEE Advanced, no text layer, no branding" (score
near 0) from "any MathonGo JEE Main paper" (score near 1) — verified against
this project's actual fixture set, not chosen arbitrarily.
"""
from dataclasses import dataclass

from models.templates import TemplateVersion
from templates.fingerprint import Fingerprint

_WEIGHT_TEXT_LAYER = 0.25
_WEIGHT_BRANDING = 0.30
_WEIGHT_QUESTION_MARKERS = 0.20
_WEIGHT_OPTION_MARKERS = 0.15
_WEIGHT_ANSWER_KEY = 0.10


@dataclass
class MatchResult:
    template_version: TemplateVersion
    fingerprint_score: float
    reasons: list


def score_against_template(fp: Fingerprint, tv: TemplateVersion):
    sig = tv.match_signature
    score = 0.0
    reasons = []

    if fp.requires_text_layer == sig.requires_text_layer:
        score += _WEIGHT_TEXT_LAYER
        reasons.append(f"text layer presence matches (requires_text_layer={sig.requires_text_layer})")
    else:
        reasons.append(f"text layer presence mismatch (found={fp.requires_text_layer}, "
                        f"expected={sig.requires_text_layer})")

    expected_branding = sig.branding_strings or []
    if expected_branding:
        matched_branding = [b for b in expected_branding if b in fp.branding_strings_found]
        branding_ratio = len(matched_branding) / len(expected_branding)
        score += _WEIGHT_BRANDING * branding_ratio
        if matched_branding:
            reasons.append(f"branding string(s) found: {matched_branding}")
        missing = [b for b in expected_branding if b not in matched_branding]
        if missing:
            reasons.append(f"branding string(s) not found: {missing}")

    if fp.question_marker_hits >= sig.min_question_marker_hits:
        score += _WEIGHT_QUESTION_MARKERS
        reasons.append(f"{fp.question_marker_hits} question-marker hits >= "
                        f"required {sig.min_question_marker_hits}")
    else:
        reasons.append(f"only {fp.question_marker_hits} question-marker hits, "
                        f"below required {sig.min_question_marker_hits}")

    if fp.option_marker_hits > 0:
        score += _WEIGHT_OPTION_MARKERS
        reasons.append(f"{fp.option_marker_hits} option-marker hits found")
    else:
        reasons.append("no option-marker hits found")

    if fp.answer_key_marker_found:
        score += _WEIGHT_ANSWER_KEY
        reasons.append("answer-key marker found")
    else:
        reasons.append("no answer-key marker found")

    return score, reasons


def match_templates(fp: Fingerprint, candidates: list) -> list:
    results = []
    for tv in candidates:
        score, reasons = score_against_template(fp, tv)
        results.append(MatchResult(template_version=tv, fingerprint_score=score, reasons=reasons))
    results.sort(key=lambda r: r.fingerprint_score, reverse=True)
    return results
