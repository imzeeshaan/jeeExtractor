"""
Cheap, whole-document fingerprint computation — the "don't run the adapter
yet" half of the hybrid fingerprint+validate match. Deliberately never calls
into extractor.py; every signal here is independently computable from a
plain fitz.Document, using the same literal patterns extractor.py's own
header/marker detection already uses (not new ones invented for this).
"""
import re
from dataclasses import dataclass, field

import fitz

from pdf.inspector import inspect_document, MIN_CHAR_COUNT_FOR_TEXT

_BRANDING_PATTERNS = {
    "MathonGo": lambda t: "MathonGo" in t,
    "Question Paper": lambda t: "Question Paper" in t,
    "JEE Main <year>": lambda t: bool(re.search(r"JEE Main \d{4}", t)),
}
_QUESTION_MARKER_RE = re.compile(r"Q\d+\.")
_OPTION_MARKER_RE = re.compile(r"\(\d\)")
_ANSWER_KEY_MARKER = "ANSWER KEY"


@dataclass
class Fingerprint:
    requires_text_layer: bool
    page_count: int
    branding_strings_found: list = field(default_factory=list)
    question_marker_hits: int = 0
    option_marker_hits: int = 0
    answer_key_marker_found: bool = False


def compute_fingerprint(pdf_path: str) -> Fingerprint:
    classifications = inspect_document(pdf_path)
    # a document "has a usable text layer" if any page carries real body
    # text — same MIN_CHAR_COUNT_FOR_TEXT threshold pdf.inspector already
    # uses to call a page "text_based", not a new invented one.
    requires_text_layer = any(c.char_count >= MIN_CHAR_COUNT_FOR_TEXT for c in classifications)

    branding_found = []
    question_hits = 0
    option_hits = 0
    answer_key_found = False

    doc = fitz.open(pdf_path)
    try:
        full_text_parts = []
        for page in doc:
            text = page.get_text("text")
            full_text_parts.append(text)
            question_hits += len(_QUESTION_MARKER_RE.findall(text))
            option_hits += len(_OPTION_MARKER_RE.findall(text))
        full_text = "\n".join(full_text_parts)
        for label, matcher in _BRANDING_PATTERNS.items():
            if matcher(full_text):
                branding_found.append(label)
        answer_key_found = _ANSWER_KEY_MARKER in full_text.upper()
        page_count = doc.page_count
    finally:
        doc.close()

    return Fingerprint(
        requires_text_layer=requires_text_layer,
        page_count=page_count,
        branding_strings_found=branding_found,
        question_marker_hits=question_hits,
        option_marker_hits=option_hits,
        answer_key_marker_found=answer_key_found,
    )
