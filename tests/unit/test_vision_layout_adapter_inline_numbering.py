"""
Regression guard for a real bug hit live this session on JEE ADV 2007-1.pdf:
some documents number questions inline within the stem paragraph rather
than as a separate boxed label, so the provider never emits a
region_type=="question_number" region for them — it puts the correct
number directly on question_number_hint for the stem/option regions
themselves instead. The grouping logic must attribute those regions using
their OWN hint, not only hints carried by a dedicated question_number
region (which never exists for a page like this) — otherwise every region
on such a page is silently dropped as unattributed.
"""
from extraction.vision_layout_adapter import VisionLayoutAdapter
from models.documents import Document, Page
from models.vision import LayoutRegion, LayoutResponse, RegionBBox


def _document():
    from datetime import datetime, timezone
    return Document(
        document_id="doc1", filename="f.pdf", sha256="x", page_count=1,
        file_size_bytes=1, uploaded_at=datetime.now(timezone.utc), storage_path="/dev/null",
    )


def _page(number=1, rendered_path="dummy.png"):
    return Page(
        page_id="p1", document_id="doc1", page_number=number, width_pt=612, height_pt=792,
        page_type="scanned", char_count=0, text_bbox_coverage=0.0, image_coverage=1.0,
        has_full_page_image=True, drawing_count=0, rendered_path=rendered_path,
    )


class _InlineNumberingProvider:
    """Never emits a region_type=="question_number" region -- every region
    carries its own question_number_hint directly, matching the real
    JEE ADV 2007-1.pdf response confirmed live."""
    def detect_layout(self, page_image_path, page_number, context):
        regions = [
            LayoutRegion(region_id="q4_stem", region_type="question_stem",
                         bbox=RegionBBox(x0=0.05, y0=0.05, x1=0.9, y1=0.15),
                         question_number_hint="4", contains_visual=False, confidence=0.95),
            LayoutRegion(region_id="q4_optA", region_type="option_label",
                         bbox=RegionBBox(x0=0.1, y0=0.2, x1=0.2, y1=0.25),
                         question_number_hint="4", option_label_hint="A",
                         contains_visual=False, confidence=0.9),
            LayoutRegion(region_id="q5_stem", region_type="question_stem",
                         bbox=RegionBBox(x0=0.05, y0=0.3, x1=0.9, y1=0.4),
                         question_number_hint="5", contains_visual=False, confidence=0.95),
        ]
        return LayoutResponse(page_number=page_number, regions=regions,
                               reading_order=[r.region_id for r in regions], warnings=[])


def test_regions_attributed_by_their_own_hint_without_a_dedicated_number_region(tmp_path):
    from PIL import Image
    page_png = tmp_path / "page.png"
    Image.new("RGB", (400, 600), color=(200, 200, 200)).save(page_png)

    adapter = VisionLayoutAdapter(_InlineNumberingProvider())
    result = adapter.run(_document(), "dummy.pdf", str(tmp_path), pages=[_page(1, str(page_png))])

    numbers = {q.number for q in result.questions}
    assert numbers == {"4", "5"}, f"expected both questions attributed, got {numbers}"

    q4 = next(q for q in result.questions if q.number == "4")
    assert len(q4.stem_blocks) == 1
    assert len(q4.options) == 1 and q4.options[0].label == "A"

    q5 = next(q for q in result.questions if q.number == "5")
    assert len(q5.stem_blocks) == 1

    assert 1 not in result.dropped_region_warnings  # nothing should have been dropped as unattributed
