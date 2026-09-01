"""
Deterministic per-page classification (text_based / scanned / mixed).
Computed and PERSISTED only in Phase 1 — nothing routes on this
classification yet (no vision fallback exists to route to). Thresholds are
named module-level constants so they're visible/tunable, not magic numbers
buried in a conditional.

These thresholds were sanity-checked against known real data this session:
the JEE Advanced samples (2007/2016) — genuinely scanned, ~487 chars of OCR
noise across 30 pages for one, zero extractable characters across 29 pages
for the other — must classify as "scanned"; the 8 supported MathonGo papers
(each with 90 or 75 questions of real body text) must classify "text_based".
"""
from dataclasses import dataclass

import fitz

MIN_CHAR_COUNT_FOR_TEXT = 200
FULL_PAGE_IMAGE_AREA_RATIO = 0.85
HIGH_IMAGE_COVERAGE_RATIO = 0.5


@dataclass
class PageClassification:
    page_number: int
    width_pt: float
    height_pt: float
    rotation: int
    page_type: str  # text_based | scanned | mixed
    char_count: int
    text_bbox_coverage: float
    image_coverage: float
    has_full_page_image: bool
    drawing_count: int


def classify_page(page: "fitz.Page", page_number: int) -> PageClassification:
    rect = page.rect
    page_area = max(rect.width * rect.height, 1e-6)

    text = page.get_text("text")
    char_count = len(text)

    text_dict = page.get_text("dict")
    text_area = 0.0
    for block in text_dict["blocks"]:
        if block.get("type") != 0:
            continue
        bbox = block["bbox"]
        text_area += max(bbox[2] - bbox[0], 0) * max(bbox[3] - bbox[1], 0)
    text_bbox_coverage = min(text_area / page_area, 1.0)

    image_area = 0.0
    has_full_page_image = False
    for img in page.get_images(full=True):
        try:
            bbox = page.get_image_bbox(img)
        except Exception:
            continue
        area = max(bbox.x1 - bbox.x0, 0) * max(bbox.y1 - bbox.y0, 0)
        image_area += area
        if area / page_area >= FULL_PAGE_IMAGE_AREA_RATIO:
            has_full_page_image = True
    image_coverage = min(image_area / page_area, 1.0)

    drawing_count = len(page.get_drawings())

    if char_count < MIN_CHAR_COUNT_FOR_TEXT and has_full_page_image:
        page_type = "scanned"
    elif char_count >= MIN_CHAR_COUNT_FOR_TEXT and image_coverage < HIGH_IMAGE_COVERAGE_RATIO:
        page_type = "text_based"
    else:
        page_type = "mixed"

    return PageClassification(
        page_number=page_number,
        width_pt=rect.width,
        height_pt=rect.height,
        rotation=page.rotation,
        page_type=page_type,
        char_count=char_count,
        text_bbox_coverage=text_bbox_coverage,
        image_coverage=image_coverage,
        has_full_page_image=has_full_page_image,
        drawing_count=drawing_count,
    )


def inspect_document(pdf_path: str) -> list[PageClassification]:
    doc = fitz.open(pdf_path)
    try:
        return [classify_page(doc[i], i + 1) for i in range(doc.page_count)]
    finally:
        doc.close()
