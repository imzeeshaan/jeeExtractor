"""
Phase 0: exact-hash golden regression tests.

_crop()/render_pages() in extractor.py are pure fitz.Page.get_pixmap() calls —
deterministic given identical input bytes and PyMuPDF version, with no
randomness in the rendering path. We therefore use EXACT SHA-256 hashing here,
not a perceptual/fuzzy diff: a fuzzy diff would silently absorb a real
regression (e.g. a crop rectangle boundary shifted by 1pt), which defeats the
purpose of a freeze test.

Escape hatch (not implemented, documented only): if this suite is ever run on
a machine with a different PyMuPDF/MuPDF build that produces byte-different
but visually-identical PNGs, the fallback would be a per-file perceptual diff
(Pillow ImageChops.difference, small max-channel-delta tolerance) gated behind
an explicit env var such as JEE_GOLDEN_TOLERANT=1 — never the default, so a
real regression is never silently absorbed by "just being tolerant".

To regenerate goldens after a deliberate, verified change:
    python3 tests/golden/generate_golden.py [paper_id ...]
"""
import hashlib
import json
import os

import pytest

from extractor import parse_pdf

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "..", "fixtures", "pdfs")
GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "..", "golden")

PAPER_IDS = [
    "2012_may07", "2012_may12", "2012_may19", "2012_may26", "2012_offline",
    "2019_jan09", "2019_jan12", "2020_jan07",
]


def _sha256_of_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _diff_manifests(expected, actual):
    expected_keys = set(expected)
    actual_keys = set(actual)
    lines = []
    missing = sorted(expected_keys - actual_keys)
    extra = sorted(actual_keys - expected_keys)
    changed = sorted(
        k for k in (expected_keys & actual_keys) if expected[k] != actual[k]
    )
    if missing:
        lines.append(f"missing ({len(missing)}): {missing[:10]}{'...' if len(missing) > 10 else ''}")
    if extra:
        lines.append(f"extra ({len(extra)}): {extra[:10]}{'...' if len(extra) > 10 else ''}")
    if changed:
        lines.append(f"changed hash ({len(changed)}): {changed[:10]}{'...' if len(changed) > 10 else ''}")
    return "; ".join(lines) if lines else "(no differences found — unexpected)"


@pytest.mark.parametrize("paper_id", PAPER_IDS)
def test_golden_image_manifest_matches(paper_id, tmp_path):
    golden_path = os.path.join(GOLDEN_DIR, paper_id, "manifest.json")
    with open(golden_path) as f:
        expected_manifest = json.load(f)

    pdf_path = os.path.join(FIXTURES_DIR, f"{paper_id}.pdf")
    parse_pdf(pdf_path, str(tmp_path))

    images_dir = tmp_path / "images"
    actual_manifest = {}
    if images_dir.is_dir():
        for fname in sorted(os.listdir(images_dir)):
            fpath = images_dir / fname
            if fpath.is_file():
                actual_manifest[f"images/{fname}"] = _sha256_of_file(str(fpath))

    assert actual_manifest == expected_manifest, (
        f"{paper_id}: golden image manifest mismatch — {_diff_manifests(expected_manifest, actual_manifest)}"
    )


@pytest.mark.parametrize("paper_id", PAPER_IDS)
def test_golden_questions_json_matches(paper_id, tmp_path):
    golden_path = os.path.join(GOLDEN_DIR, paper_id, "questions.json")
    with open(golden_path) as f:
        expected_questions = json.load(f)

    pdf_path = os.path.join(FIXTURES_DIR, f"{paper_id}.pdf")
    actual_questions, notes = parse_pdf(pdf_path, str(tmp_path))

    # compare as parsed JSON objects (round-trip actual through json too) so
    # this is a pure data-equality check, not sensitive to key ordering/whitespace
    actual_as_json = json.loads(json.dumps(actual_questions))
    assert actual_as_json == expected_questions, f"{paper_id}: questions.json output no longer matches golden"


def test_determinism_repeated_runs_match(tmp_path):
    """Sanity check for the exact-hash strategy itself: running parse_pdf
    twice against the same input in the same process must produce identical
    output. If this test is ever flaky, the exact-hash approach above is the
    wrong tool and needs the tolerant fallback documented at the top of this
    file — investigate before touching any other test in this suite."""
    pdf_path = os.path.join(FIXTURES_DIR, "2012_may07.pdf")
    out_a = tmp_path / "run_a"
    out_b = tmp_path / "run_b"
    questions_a, _ = parse_pdf(pdf_path, str(out_a))
    questions_b, _ = parse_pdf(pdf_path, str(out_b))

    assert json.dumps(questions_a, sort_keys=True) == json.dumps(questions_b, sort_keys=True)

    for fname in sorted(os.listdir(out_a / "images")):
        hash_a = _sha256_of_file(str(out_a / "images" / fname))
        hash_b = _sha256_of_file(str(out_b / "images" / fname))
        assert hash_a == hash_b, f"non-deterministic crop output for {fname}"
