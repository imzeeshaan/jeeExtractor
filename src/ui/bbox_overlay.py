"""
Draws one READ-ONLY rectangle on a rendered page PNG, using an evidence
bbox already stored in the DB. This is not a new rendering subsystem and
not drag-resize editing (both explicitly out of scope per the spec) — it's
a ~15-line PIL overlay proving to a reviewer exactly where a piece of
evidence came from.
"""
from PIL import Image, ImageDraw

from pdf.coordinates import normalized_to_pixel

OVERLAY_COLOR = (255, 60, 60)
OVERLAY_WIDTH = 4


def draw_bbox_overlay(rendered_page_path: str, bbox) -> Image.Image:
    """bbox is a models.common.BoundingBox (normalized 0-1). Returns a new
    PIL Image — the source file on disk is never modified."""
    image = Image.open(rendered_page_path).convert("RGB")
    x0, y0, x1, y1 = normalized_to_pixel(bbox, image.width, image.height)
    draw = ImageDraw.Draw(image)
    draw.rectangle([x0, y0, x1, y1], outline=OVERLAY_COLOR, width=OVERLAY_WIDTH)
    return image
