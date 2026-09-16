#!/usr/bin/env python3
"""
Manual, REAL-MONEY CLI harness — the ONLY code path in this entire codebase
that calls a real vision API (DeepSeek primary, OpenAI fallback). Every
automated test uses MockVisionProvider exclusively; this script is never
imported by pytest and never run in CI.

Usage:
    DEEPSEEK_API_KEY=... OPENAI_API_KEY=... python3 scripts/run_vision_experiment.py \
        [--pdf tests/fixtures/unsupported/JEE_ADV_2016-1.pdf] \
        [--primary-model deepseek-flash] [--fallback-model gpt-5.6-terra]

Hard rule (do not relax): this script targets ONLY the one committed
JEE Advanced fixture by default — pass --pdf explicitly to override, but
there is no "run against everything" mode, given real API cost.

Model pricing referenced in comments is cached from this session's research
(2026-08-31 / 2026-09-01) and may be stale — re-verify before trusting it for
a real budget decision. DeepSeek's deepseek-flash (formerly
deepseek-v4-flash-vision-exp, which now routes here for backward
compatibility as of the V4.1-Flash release, 2026-09-10) has native vision
support built into the model rather than the earlier "-vision-exp"
experimental bolt-on.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import get_config
from db.session import get_engine, init_db, make_session_factory, session_scope
from extraction.ingest import DuplicateDocumentError, ingest_and_extract, UnmatchedDocumentResult
from templates.bootstrap import ensure_default_templates_registered, ensure_vision_layout_template_registered

_DEFAULT_PDF = os.path.join(
    os.path.dirname(__file__), "..", "tests", "fixtures", "unsupported", "JEE_ADV_2016-1.pdf",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", default=_DEFAULT_PDF)
    parser.add_argument("--primary-model", default=None)
    parser.add_argument("--fallback-model", default=None)
    parser.add_argument("--allow-duplicate", action="store_true",
                         help="Re-ingest even if this exact PDF was already ingested (e.g. by an "
                              "earlier deterministic-path test run) — this fixture is deliberately "
                              "re-run often during vision experimentation.")
    args = parser.parse_args()

    # get_config() loads .env (python-dotenv) as a side effect — load it
    # explicitly here first so the DEEPSEEK_API_KEY/OPENAI_API_KEY checks
    # below see values from .env, not just already-exported shell vars.
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"), override=False)

    if not os.environ.get("DEEPSEEK_API_KEY"):
        print("DEEPSEEK_API_KEY is not set — refusing to run (DeepSeek is the mandatory primary provider).")
        return 1
    if not os.environ.get("OPENAI_API_KEY"):
        print("NOTE: OPENAI_API_KEY is not set — the OpenAI fallback/escalation path will be "
              "unavailable; any question that would have escalated simply stays unescalated "
              "(logged in the 'vision_escalation' stage's skipped_reasons, not a crash). Continuing "
              "with DeepSeek only.")

    os.environ["JEE_VISION_PROVIDER"] = "real"
    if args.primary_model:
        os.environ["JEE_VISION_PRIMARY_MODEL"] = args.primary_model
    if args.fallback_model:
        os.environ["JEE_VISION_FALLBACK_MODEL"] = args.fallback_model

    config = get_config()
    engine = get_engine(config.db_path, echo=config.db_echo)
    init_db(engine)
    session_factory = make_session_factory(engine)
    with session_scope(session_factory) as session:
        ensure_default_templates_registered(session)
        ensure_vision_layout_template_registered(session)

    started = time.monotonic()
    try:
        result = ingest_and_extract(args.pdf, config, session_factory, exam="JEE Advanced",
                                     allow_duplicate=args.allow_duplicate)
    except DuplicateDocumentError as exc:
        print(f"Already ingested as document {exc.existing_document_id} "
              f"(pass --allow-duplicate to ingest again)")
        return 1
    elapsed = time.monotonic() - started

    if isinstance(result, UnmatchedDocumentResult):
        print("UNMATCHED — the vision template did not clear the auto-run threshold. "
              "This itself is meaningful experiment output, not a bug.")
        for c in result.candidates:
            print(f"  {c.template_version.template_id} v{c.template_version.version}: "
                  f"score={c.fingerprint_score:.2f}")
        return 0

    print(f"document_id: {result.document_id}")
    print(f"elapsed_seconds: {elapsed:.1f}")
    print(f"question_count: {result.metrics['question_count']}")
    print(f"region_count: {result.metrics['region_count']}")
    print(f"pages_with_dropped_regions: {result.metrics.get('pages_with_dropped_regions', 0)}")
    print("NOTE: per-call token usage / cost accumulation is not wired up in this script's v1 — "
          "see OpenAICompatibleVisionProvider._call for where response usage would be captured; "
          "left as a documented follow-up rather than adding untested accumulation logic to the "
          "one script whose whole point is a careful, deliberate, real-money run. Check the "
          "'vision_escalation' stage_runs row in data/app.db for how many of the total questions "
          "actually escalated to OpenAI (should be a small minority, not most of them).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
