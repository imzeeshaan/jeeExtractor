"""
Phase 5 follow-up (Concurrent + Incremental Vision Processing): proves
VisionLayoutAdapter.run() actually dispatches per-page detect_layout calls
concurrently (not serialized), isolates a single page's failure from the
rest, and invokes on_page_result exactly once per page with correct,
monotonically increasing completed counts.
"""
import threading
import time

from extraction.vision_layout_adapter import VisionLayoutAdapter
from models.documents import Document
from models.vision import LayoutRegion, LayoutResponse, RegionBBox


def _page(page_number, rendered_path="dummy.png"):
    from models.documents import Page
    return Page(
        page_id=f"p{page_number}", document_id="doc1", page_number=page_number,
        width_pt=612, height_pt=792, page_type="scanned", char_count=0,
        text_bbox_coverage=0.0, image_coverage=1.0, has_full_page_image=True,
        drawing_count=0, rendered_path=rendered_path,
    )


def _document():
    from datetime import datetime, timezone
    return Document(
        document_id="doc1", filename="f.pdf", sha256="x", page_count=5,
        file_size_bytes=1, uploaded_at=datetime.now(timezone.utc), storage_path="/dev/null",
    )


def _simple_layout(page_number):
    region = LayoutRegion(
        region_id=f"p{page_number}_qnum", region_type="question_number",
        bbox=RegionBBox(x0=0.02, y0=0.02, x1=0.15, y1=0.06),
        question_number_hint=str(page_number), contains_visual=False, confidence=0.9,
    )
    return LayoutResponse(page_number=page_number, regions=[region], reading_order=[region.region_id])


class _ConcurrencyTrackingProvider:
    """Every call sleeps briefly so overlapping calls are observable; tracks
    the max number of calls in flight at once."""
    def __init__(self):
        self._lock = threading.Lock()
        self._in_flight = 0
        self.max_in_flight = 0

    def detect_layout(self, page_image_path, page_number, context):
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
        time.sleep(0.1)
        with self._lock:
            self._in_flight -= 1
        return _simple_layout(page_number)


def test_pages_are_dispatched_concurrently(tmp_path, monkeypatch):
    # _group_page_layout crops from the page image via PIL -- give it a real file.
    from PIL import Image
    page_png = tmp_path / "page.png"
    Image.new("RGB", (400, 600), color=(200, 200, 200)).save(page_png)

    provider = _ConcurrencyTrackingProvider()
    adapter = VisionLayoutAdapter(provider)
    pages = [_page(i, str(page_png)) for i in range(1, 6)]

    adapter.run(_document(), "dummy.pdf", str(tmp_path), pages=pages, concurrency=5)

    assert provider.max_in_flight > 1, "pages were processed one at a time, not concurrently"


class _OnePageFailsProvider:
    def detect_layout(self, page_image_path, page_number, context):
        if page_number == 3:
            raise RuntimeError("simulated API failure on page 3")
        return _simple_layout(page_number)


def test_one_page_failure_does_not_abort_the_others(tmp_path):
    from PIL import Image
    page_png = tmp_path / "page.png"
    Image.new("RGB", (400, 600), color=(200, 200, 200)).save(page_png)

    adapter = VisionLayoutAdapter(_OnePageFailsProvider())
    pages = [_page(i, str(page_png)) for i in range(1, 6)]

    result = adapter.run(_document(), "dummy.pdf", str(tmp_path), pages=pages, concurrency=5)

    succeeded_numbers = {q.number for q in result.questions}
    assert succeeded_numbers == {"1", "2", "4", "5"}
    assert 3 in result.dropped_region_warnings
    assert "detect_layout failed" in result.dropped_region_warnings[3][0]


def test_on_page_result_called_once_per_page_with_correct_counts(tmp_path):
    from PIL import Image
    page_png = tmp_path / "page.png"
    Image.new("RGB", (400, 600), color=(200, 200, 200)).save(page_png)

    adapter = VisionLayoutAdapter(_ConcurrencyTrackingProvider())
    pages = [_page(i, str(page_png)) for i in range(1, 6)]

    calls = []
    def _on_page_result(page, page_questions, page_warnings, completed, total):
        calls.append((page.page_number, completed, total))

    adapter.run(_document(), "dummy.pdf", str(tmp_path), pages=pages, concurrency=5,
                on_page_result=_on_page_result)

    assert len(calls) == 5
    assert {p for p, _, _ in calls} == {1, 2, 3, 4, 5}
    completed_values = [c for _, c, _ in calls]
    assert completed_values == sorted(completed_values)  # monotonically increasing
    assert completed_values == [1, 2, 3, 4, 5]
    assert all(total == 5 for _, _, total in calls)
