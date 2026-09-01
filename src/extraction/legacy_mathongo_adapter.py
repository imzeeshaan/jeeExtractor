"""
Wraps extractor.parse_pdf() without modifying its behavior, converting its
output into the canonical Question/Option/ContentBlock models.

bbox-evidence note: extractor.py now additively returns the stem/option rects
it computes internally (in PDF points, plus 1-indexed page numbers) — see the
"stem_rect"/"stem_page"/"stem_cont_rect"/option "rect"/"page" keys. This
adapter converts those real rects to normalized BoundingBox coordinates via
PageGeometry + pdf_rect_to_normalized. If a legacy dict is ever missing these
keys (e.g. a hand-built test fixture, or a future template that doesn't
supply them), make_evidence falls back to an honest full-page bbox (0,0,1,1)
rather than fabricating a fake precise-looking box.
"""
import fitz

from extractor import parse_pdf
from extraction.metrics import compute_all_metrics
from models.common import BoundingBox, SourceEvidence
from models.documents import Document
from models.questions import Question, legacy_dict_to_question
from pdf.coordinates import pdf_rect_to_normalized

_NOTE_PATTERNS = [
    ("options continue on page", "info"),
    ("stem continues on page", "info"),
    ("option (", "warning"),  # "option (N) marker not found"
    ("no options found", "info"),  # numerical-question detection
    ("found", "warning"),  # image-count mismatch sanity-check note
]


class PageGeometry:
    """Per-document page dimensions (width/height/rotation), used both to
    convert real PDF-point rects into normalized bboxes and to build an
    honest full-page fallback bbox when no rect is available for a given
    field."""

    def __init__(self, pdf_path: str):
        doc = fitz.open(pdf_path)
        try:
            self.page_count = doc.page_count
            self._dims = {
                (i + 1): (doc[i].rect.width, doc[i].rect.height, doc[i].rotation)
                for i in range(doc.page_count)
            }
        finally:
            doc.close()

    def full_page_bbox(self) -> BoundingBox:
        return BoundingBox(x0=0.0, y0=0.0, x1=1.0, y1=1.0)

    def normalize_rect(self, rect, page_number: int) -> BoundingBox:
        width_pt, height_pt, rotation = self._dims[page_number]
        return pdf_rect_to_normalized(rect, width_pt, height_pt, rotation=rotation)


class LegacyAdapterResult:
    def __init__(self, document_id: str, questions: list[Question], metrics: dict, issues: list[dict],
                 legacy_questions: list[dict]):
        self.document_id = document_id
        self.questions = questions
        self.metrics = metrics
        self.issues = issues
        # raw parse_pdf() dicts — kept around so the Phase 2 validation engine's
        # image-disposition rule can reuse extraction.metrics's existing
        # dict-shaped functions unchanged, rather than re-deriving that shape
        # (lossily) from the canonical Question objects.
        self.legacy_questions = legacy_questions


def _classify_note(note: str) -> str:
    lowered = note.lower()
    for pattern, severity in _NOTE_PATTERNS:
        if pattern in lowered:
            return severity
    return "info"


def _notes_to_issues(notes: list[str]) -> list[dict]:
    """Lightweight, non-persisted-as-their-own-table issue dicts — NOT the
    full ValidationIssue model (spec §16.8, Phase 2 scope). Just enough that
    nothing from the legacy extractor's notes is silently dropped."""
    issues = []
    for note in notes:
        qnum = None
        if note.startswith("Q") and ":" in note:
            head = note.split(":", 1)[0]
            digits = "".join(c for c in head if c.isdigit())
            if digits:
                qnum = int(digits)
        issues.append({
            "question_number": qnum,
            "message": note,
            "severity": _classify_note(note),
        })
    return issues


class LegacyMathonGoAdapter:
    """Wraps parse_pdf() — calls it unmodified, converts its output to
    canonical models, computes metrics, and classifies notes as lightweight
    issues. Does not touch app.py's behavior or extractor.py's logic."""

    def run(self, document: Document, pdf_path: str, out_dir: str,
            template_id: str = "jee_main_mathongo", template_version: int = 1) -> LegacyAdapterResult:
        # template_id/template_version default to the historical hardcoded
        # values so every pre-Phase-4 call site keeps working unchanged;
        # Phase 4's ingest.py passes the REAL matched values through.
        legacy_questions, notes = parse_pdf(pdf_path, out_dir)

        geometry = PageGeometry(pdf_path)

        def make_evidence(_rect_key: str, page_number: int, rect=None) -> SourceEvidence:
            if rect is not None:
                bbox = geometry.normalize_rect(rect, page_number)
            else:
                # honest fallback — no fabricated precise-looking box when we
                # genuinely don't have real geometry for this field
                bbox = geometry.full_page_bbox()
            return SourceEvidence(
                page_number=page_number,
                bbox=bbox,
                extraction_method="legacy_deterministic",
            )

        canonical_questions = []
        for legacy_q in legacy_questions:
            canonical_questions.append(
                legacy_dict_to_question(
                    legacy_q,
                    document_id=document.document_id,
                    page_start=legacy_q.get("stem_page", 1),
                    page_end=legacy_q.get("stem_end_page", geometry.page_count),
                    make_evidence=make_evidence,
                    template_id=template_id,
                    template_version=template_version,
                )
            )

        doc = fitz.open(pdf_path)
        try:
            metrics = compute_all_metrics(legacy_questions, doc)
        finally:
            doc.close()

        issues = _notes_to_issues(notes)

        return LegacyAdapterResult(
            document_id=document.document_id,
            questions=canonical_questions,
            metrics=metrics,
            issues=issues,
            legacy_questions=legacy_questions,
        )
