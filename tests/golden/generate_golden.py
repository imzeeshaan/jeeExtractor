"""
Manual, one-time-run script that generates the golden files consumed by
tests/unit/test_golden_manifest.py. This script is NEVER run automatically by
pytest — run it by hand only when you intend to accept new output as correct
(e.g. after verifying a fix is genuinely an improvement, not a regression).

Usage (from jee-extractor-app/):
    python3 tests/golden/generate_golden.py [paper_id ...]

With no arguments, regenerates goldens for all 8 fixtures. With one or more
paper_id arguments, regenerates only those (useful after deliberately changing
behavior for one paper and wanting to re-baseline just it, while leaving the
others' goldens as a check that nothing else moved).
"""
import hashlib
import json
import os
import sys
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from extractor import parse_pdf

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "pdfs")
GOLDEN_DIR = os.path.dirname(__file__)

ALL_PAPER_IDS = [
    "2012_may07", "2012_may12", "2012_may19", "2012_may26", "2012_offline",
    "2019_jan09", "2019_jan12", "2020_jan07",
]


def _sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def generate_one(paper_id):
    pdf_path = os.path.join(FIXTURES_DIR, f"{paper_id}.pdf")
    work_dir = os.path.join(GOLDEN_DIR, f"_tmp_{paper_id}")
    if os.path.exists(work_dir):
        shutil.rmtree(work_dir)
    os.makedirs(work_dir)

    questions, notes = parse_pdf(pdf_path, work_dir)

    images_dir = os.path.join(work_dir, "images")
    manifest = {}
    if os.path.isdir(images_dir):
        for fname in sorted(os.listdir(images_dir)):
            fpath = os.path.join(images_dir, fname)
            if os.path.isfile(fpath):
                manifest[f"images/{fname}"] = _sha256_of_file(fpath)

    out_dir = os.path.join(GOLDEN_DIR, paper_id)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    with open(os.path.join(out_dir, "questions.json"), "w") as f:
        json.dump(questions, f, indent=2, sort_keys=True)
    with open(os.path.join(out_dir, "notes.json"), "w") as f:
        json.dump(notes, f, indent=2)

    shutil.rmtree(work_dir)
    print(f"{paper_id}: {len(manifest)} image(s) hashed, {len(questions)} question(s) recorded")


if __name__ == "__main__":
    targets = sys.argv[1:] or ALL_PAPER_IDS
    for pid in targets:
        generate_one(pid)
