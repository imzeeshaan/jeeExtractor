#!/usr/bin/env python3
"""
Manual CLI harness for exercising the Phase 1 ingestion + legacy-adapter
pipeline without the Streamlit UI. Does not touch app.py.

Usage:
    python3 scripts/run_adapter.py --pdf path/to/paper.pdf --exam "JEE Main" --year 2012 \
        [--shift "07 May Online"] [--publisher MathonGo] [--json-out out.json] [--allow-duplicate]
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config import get_config
from db.session import get_engine, init_db, make_session_factory, session_scope
from extraction.ingest import ingest_and_extract, DuplicateDocumentError, UnmatchedDocumentResult
from templates.bootstrap import ensure_default_templates_registered


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--exam", default=None)
    parser.add_argument("--year", type=int, default=None)
    parser.add_argument("--shift", default=None)
    parser.add_argument("--publisher", default=None)
    parser.add_argument("--json-out", default=None)
    parser.add_argument("--allow-duplicate", action="store_true")
    args = parser.parse_args()

    config = get_config()
    engine = get_engine(config.db_path, echo=config.db_echo)
    init_db(engine)
    session_factory = make_session_factory(engine)
    with session_scope(session_factory) as session:
        ensure_default_templates_registered(session)

    try:
        result = ingest_and_extract(
            args.pdf, config, session_factory,
            exam=args.exam, year=args.year, shift=args.shift, publisher=args.publisher,
            allow_duplicate=args.allow_duplicate,
        )
    except DuplicateDocumentError as exc:
        print(f"Already ingested as document {exc.existing_document_id} "
              f"(pass --allow-duplicate to ingest again)")
        return 1

    if isinstance(result, UnmatchedDocumentResult):
        print(f"document_id: {result.document_id}")
        print("No registered template matched this document — nothing was extracted.")
        for c in result.candidates:
            print(f"  candidate {c.template_version.template_id} v{c.template_version.version}: "
                  f"score={c.fingerprint_score:.2f}")
            for reason in c.reasons:
                print(f"    - {reason}")
        return 0

    m = result.metrics
    print(f"document_id: {result.document_id}")
    print(f"question_count: {m['question_count']}")
    print(f"answer_coverage: {m['answer_coverage']:.3f}")
    print(f"numerical_count: {m['numerical_count']}")
    print(f"images: {m['embedded_images_assigned']}/{m['embedded_images_total']} "
          f"assigned/total (conserved={m['images_conserved']})")
    print(f"contiguous_numbering: {m['contiguous_numbering']}")
    print(f"issues: {len(result.issues)}")

    if args.json_out:
        payload = [q.model_dump(mode="json") for q in result.questions]
        with open(args.json_out, "w") as f:
            json.dump(payload, f, indent=2)
        print(f"wrote {len(payload)} canonical questions to {args.json_out}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
