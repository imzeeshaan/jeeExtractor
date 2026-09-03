"""
Phase 5: TemplateRepository.list_matchable()'s widening to include "draft"
status alongside "validated"/"monitored" — "candidate" stays excluded.
"""
from datetime import datetime, timezone

import pytest

from db.repositories import TemplateRepository
from db.session import get_engine, init_db, make_session_factory, session_scope
from models.templates import Template, TemplateVersion, MatchSignature
from templates.bootstrap import ensure_default_templates_registered, MATHONGO_TEMPLATE_ID, MATHONGO_VERSION


@pytest.fixture
def session_factory():
    engine = get_engine(":memory:")
    init_db(engine)
    return make_session_factory(engine)


def _sig():
    return MatchSignature(
        requires_text_layer=True, branding_strings=[], question_marker_pattern="",
        option_marker_pattern="", answer_key_marker="",
    )


def test_draft_and_validated_are_matchable_but_not_candidate(session_factory):
    with session_scope(session_factory) as session:
        ensure_default_templates_registered(session)
        repo = TemplateRepository(session)
        now = datetime.now(timezone.utc)
        repo.register(
            Template(template_id="t_candidate", name="Candidate", document_family="x", created_at=now),
            TemplateVersion(template_id="t_candidate", version=1, kind="vision_layout", status="candidate",
                             adapter_ref="vision_layout_adapter", match_signature=_sig(),
                             baseline_score=None, run_count=0, created_at=now),
        )
        repo.register(
            Template(template_id="t_draft", name="Draft", document_family="x", created_at=now),
            TemplateVersion(template_id="t_draft", version=1, kind="vision_layout", status="draft",
                             adapter_ref="vision_layout_adapter", match_signature=_sig(),
                             baseline_score=None, run_count=0, created_at=now),
        )

        matchable = TemplateRepository(session).list_matchable()
        ids = {(tv.template_id, tv.status) for tv in matchable}

        assert (MATHONGO_TEMPLATE_ID, "validated") in ids
        assert ("t_draft", "draft") in ids
        assert not any(template_id == "t_candidate" for template_id, _ in ids)
