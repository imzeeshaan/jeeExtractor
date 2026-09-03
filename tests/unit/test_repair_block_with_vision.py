"""
Phase 5: review_service.repair_block_with_vision — mirrors edit_stem_text's
atomic pattern, sourcing the new text from a vision provider instead of a
widget value.
"""
import os

import pytest
from PIL import Image

from datetime import datetime, timezone

from config import AppConfig
from db.repositories import DocumentRepository, QuestionRepository, ReviewActionRepository
from db.session import get_engine, init_db, make_session_factory, session_scope
from models.common import BoundingBox, SourceEvidence
from models.documents import Document
from models.questions import ContentBlock, Question
from services import review_service


@pytest.fixture
def session_factory():
    engine = get_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def config(tmp_path):
    c = AppConfig(
        data_dir=tmp_path, db_path=tmp_path / "app.db",
        uploads_dir=tmp_path / "uploads", rendered_pages_dir=tmp_path / "rendered",
        crops_dir=tmp_path / "crops", templates_dir=tmp_path / "templates",
    )
    c.ensure_directories()
    return c


class _MockProviderUnreadable:
    def transcribe_region(self, crop_image_path, context):
        from models.vision import TranscriptionResult
        return TranscriptionResult(text=None, unreadable=True, warnings=[])


class _MockProviderReadable:
    def transcribe_region(self, crop_image_path, context):
        from models.vision import TranscriptionResult
        return TranscriptionResult(text="transcribed text", unreadable=False, warnings=[])


class _MockProviderReadableWithLatex:
    def transcribe_region(self, crop_image_path, context):
        from models.vision import TranscriptionResult
        return TranscriptionResult(text="(3/4)^3", latex=r"\left(\frac{3}{4}\right)^3",
                                    unreadable=False, warnings=[])


def _seed_question(session_factory, config, document_id, crop_rel_path):
    crop_full = os.path.join(str(config.crops_dir), document_id, crop_rel_path)
    os.makedirs(os.path.dirname(crop_full), exist_ok=True)
    Image.new("RGB", (100, 100), color=(50, 50, 50)).save(crop_full)

    evidence = SourceEvidence(page_number=1, bbox=BoundingBox(x0=0.1, y0=0.1, x1=0.5, y1=0.5),
                               extraction_method="vision_layout")
    block = ContentBlock(block_id="block-1", content_type="text", text=None,
                          clean_crop_path=crop_rel_path, evidence=evidence,
                          confidence=0.4, status="needs_review")
    question = Question(
        question_id="question-1", document_id=document_id, number="1", question_type="unknown",
        stem_blocks=[block], options=[], page_start=1, page_end=1,
        extraction_mode="vision_layout", confidence=0.5, status="needs_review",
    )
    with session_scope(session_factory) as session:
        DocumentRepository(session).create(Document(
            document_id=document_id, filename="fixture.pdf", sha256=document_id, page_count=1,
            file_size_bytes=1, uploaded_at=datetime.now(timezone.utc), storage_path="/dev/null",
        ))
        QuestionRepository(session).bulk_save([question])
    return question


def test_repair_writes_unreadable_result_and_records_action(session_factory, config):
    document_id = "doc-1"
    _seed_question(session_factory, config, document_id, "images/q1_stem.png")

    review_service.repair_block_with_vision(
        session_factory, config, _MockProviderUnreadable(), "question-1", "block-1",
    )

    with session_scope(session_factory) as session:
        q = QuestionRepository(session).get("question-1")
        assert q.stem_blocks[0].text is None
        actions = ReviewActionRepository(session).list_for_question("question-1")
        assert len(actions) == 1
        assert actions[0].action_type == "vision_repair_transcription"


def test_repair_writes_readable_transcription(session_factory, config):
    document_id = "doc-2"
    _seed_question(session_factory, config, document_id, "images/q1_stem.png")

    review_service.repair_block_with_vision(
        session_factory, config, _MockProviderReadable(), "question-1", "block-1",
    )

    with session_scope(session_factory) as session:
        q = QuestionRepository(session).get("question-1")
        assert q.stem_blocks[0].text == "transcribed text"


def test_repair_persists_latex_alongside_text(session_factory, config):
    document_id = "doc-3"
    _seed_question(session_factory, config, document_id, "images/q1_stem.png")

    review_service.repair_block_with_vision(
        session_factory, config, _MockProviderReadableWithLatex(), "question-1", "block-1",
    )

    with session_scope(session_factory) as session:
        q = QuestionRepository(session).get("question-1")
        assert q.stem_blocks[0].text == "(3/4)^3"
        assert q.stem_blocks[0].latex == r"\left(\frac{3}{4}\right)^3"
        actions = ReviewActionRepository(session).list_for_question("question-1")
        assert len(actions) == 1
        assert actions[0].action_type == "vision_repair_transcription"
        assert "latex" in actions[0].new_value
