"""
The canonical Question/Option/ContentBlock models (spec §7), plus the two
required backward-compatibility conversion functions (spec §7.7) that prove
the canonical layer can round-trip to/from the exact shape app.py already
relies on. template_id/template_version/validation_issue_ids stay as plain
scalar fields on purpose — no src/models/templates.py or
src/models/validation.py exist yet (deliberately deferred to Phase 2/4, see
the approved plan).
"""
import uuid
from typing import Literal, Optional, Union

from pydantic import BaseModel

from models.common import SourceEvidence

ContentType = Literal[
    "text", "equation", "image", "mixed", "chemical_structure", "table", "unknown"
]
ContentStatus = Literal["extracted", "unreadable", "needs_review", "approved"]
QuestionType = Literal[
    "single_correct", "multiple_correct", "numerical", "integer",
    "true_false", "assertion_reason", "statement_based",
    "match_columns", "passage_based", "unknown",
]
ExtractionMode = Literal["deterministic_text", "hybrid_repair", "vision_layout"]
QuestionStatus = Literal["draft", "needs_review", "approved", "rejected"]


class ContentBlock(BaseModel):
    block_id: str
    content_type: ContentType
    text: Optional[str] = None
    latex: Optional[str] = None
    clean_crop_path: Optional[str] = None
    audit_crop_path: Optional[str] = None
    evidence: SourceEvidence
    confidence: float
    status: ContentStatus


class Option(BaseModel):
    option_id: str
    label: str
    blocks: list[ContentBlock]
    evidence: SourceEvidence
    confidence: float
    review_required: bool = False


class Question(BaseModel):
    question_id: str
    document_id: str
    number: str
    subject: Optional[str] = None
    section: Optional[str] = None
    question_type: QuestionType
    shared_context_id: Optional[str] = None
    stem_blocks: list[ContentBlock]
    options: list[Option]
    answer: Optional[Union[str, list[str], float]] = None
    answer_evidence: Optional[SourceEvidence] = None
    page_start: int
    page_end: int
    extraction_mode: ExtractionMode
    confidence: float
    status: QuestionStatus
    validation_issue_ids: list[str] = []
    template_id: Optional[str] = None
    template_version: Optional[int] = None


# Legacy question_type only ever has these two values today (see
# extractor.py) — this is intentionally the narrow subset relevant during
# migration, not the full canonical QuestionType space.
_LEGACY_QUESTION_TYPES = {"mcq", "numerical"}

# Map legacy question_type -> canonical question_type. "mcq" is ambiguous in
# the canonical taxonomy (could be single_correct or multiple_correct); the
# legacy extractor has never distinguished them (every MCQ question in the
# 8 verified papers is single-answer), so single_correct is the correct,
# information-preserving default here, not a guess.
_LEGACY_TO_CANONICAL_TYPE = {
    "mcq": "single_correct",
    "numerical": "numerical",
}
_CANONICAL_TO_LEGACY_TYPE = {v: k for k, v in _LEGACY_TO_CANONICAL_TYPE.items()}


def question_to_legacy_dict(q: Question) -> dict:
    """Inverse of legacy_dict_to_question: emit the exact shape app.py and
    the current export code already consume. Lossy by design — drops
    confidence/evidence/status/template identity, which the legacy UI never
    had and doesn't need."""
    stem_text = " ".join(b.text for b in q.stem_blocks if b.text).strip()
    # legacy_dict_to_question always puts the stitched stem snippet as the
    # FIRST stem block's clean_crop_path and any isolated diagram crops as
    # additional blocks, in that order — so recovering "first = snippet,
    # rest = diagram images" here is exact, not a guess, for anything that
    # went through that conversion. (A canonical Question built some other
    # way, e.g. later phases with vision-derived stems, isn't guaranteed to
    # follow this ordering — this function's contract is specifically the
    # round-trip inverse of legacy_dict_to_question.)
    stem_snippet = None
    stem_images = []
    for b in q.stem_blocks:
        if b.clean_crop_path and stem_snippet is None:
            stem_snippet = b.clean_crop_path
        elif b.clean_crop_path:
            stem_images.append(b.clean_crop_path)

    options = []
    for opt in q.options:
        text = " ".join(b.text for b in opt.blocks if b.text).strip()
        snippet = None
        images = []
        for b in opt.blocks:
            if b.clean_crop_path and snippet is None:
                snippet = b.clean_crop_path
            elif b.clean_crop_path:
                images.append(b.clean_crop_path)
        options.append({
            "label": opt.label,
            "text": text,
            "snippet": snippet,
            "images": images,
        })

    answer = q.answer if isinstance(q.answer, (str, type(None))) else str(q.answer)

    return {
        "question_number": int(q.number),
        "question_type": _CANONICAL_TO_LEGACY_TYPE.get(q.question_type, "mcq"),
        "stem_text": stem_text,
        "stem_snippet": stem_snippet,
        "stem_images": stem_images,
        "options": options,
        "answer": answer,
    }


def legacy_dict_to_question(
    d: dict,
    document_id: str,
    page_start: int,
    page_end: int,
    make_evidence,
) -> Question:
    """Convert one legacy parse_pdf() question dict into a canonical
    Question. `make_evidence(rect_key_prefix, page_number, rect=None) ->
    SourceEvidence` is a caller-supplied factory (owned by the adapter, not
    this module) since building real evidence requires page dimensions this
    function doesn't have — see legacy_mathongo_adapter.py for the concrete
    factory used at runtime. When the legacy dict carries the additive
    "stem_rect"/"stem_page"/option "rect"/"page" keys (extractor.py's
    geometry extension), those are passed through as real evidence; older
    legacy dicts without those keys (e.g. hand-built test fixtures) fall
    back to page_start/page_end with no rect, which make_evidence handles."""
    if d["question_type"] not in _LEGACY_QUESTION_TYPES:
        raise ValueError(f"unknown legacy question_type: {d['question_type']!r}")

    stem_evidence = make_evidence("stem", d.get("stem_page", page_start), rect=d.get("stem_rect"))
    stem_block = ContentBlock(
        block_id=str(uuid.uuid4()),
        content_type="mixed",
        text=d["stem_text"] or None,
        clean_crop_path=d["stem_snippet"],
        evidence=stem_evidence,
        confidence=1.0,
        status="extracted",
    )
    stem_blocks = [stem_block]
    for diagram_path in d.get("stem_images", []):
        stem_blocks.append(ContentBlock(
            block_id=str(uuid.uuid4()),
            content_type="image",
            clean_crop_path=diagram_path,
            evidence=make_evidence("stem_diagram", page_start),
            confidence=1.0,
            status="extracted",
        ))

    options = []
    for opt in d.get("options", []):
        opt_evidence = make_evidence(
            f"option_{opt['label']}", opt.get("page", page_end), rect=opt.get("rect")
        )
        blocks = [ContentBlock(
            block_id=str(uuid.uuid4()),
            content_type="mixed",
            text=opt["text"] or None,
            clean_crop_path=opt.get("snippet"),
            evidence=opt_evidence,
            confidence=1.0,
            status="extracted" if opt.get("snippet") else "unreadable",
        )]
        for img_path in opt.get("images", []):
            blocks.append(ContentBlock(
                block_id=str(uuid.uuid4()),
                content_type="image",
                clean_crop_path=img_path,
                evidence=make_evidence(f"option_{opt['label']}_diagram", page_end),
                confidence=1.0,
                status="extracted",
            ))
        options.append(Option(
            option_id=str(uuid.uuid4()),
            label=opt["label"],
            blocks=blocks,
            evidence=opt_evidence,
            confidence=1.0,
            review_required=not bool(opt.get("snippet")),
        ))

    answer_evidence = make_evidence("answer", page_end) if d.get("answer") is not None else None

    return Question(
        question_id=str(uuid.uuid4()),
        document_id=document_id,
        number=str(d["question_number"]),
        question_type=_LEGACY_TO_CANONICAL_TYPE[d["question_type"]],
        stem_blocks=stem_blocks,
        options=options,
        answer=d.get("answer"),
        answer_evidence=answer_evidence,
        page_start=page_start,
        page_end=page_end,
        extraction_mode="deterministic_text",
        confidence=1.0,
        status="draft",
        template_id="jee_main_mathongo",
        template_version=1,
    )
