"""
Pure coordinate-conversion functions — no I/O, no fitz.Document access.
"""
from models.common import BoundingBox


def pdf_rect_to_normalized(rect, page_width_pt, page_height_pt, rotation=0):
    """rect = (x0, y0, x1, y1) in PDF points, top-left origin (PyMuPDF's own
    convention). rotation is the page's declared rotation in degrees
    (0/90/180/270); PyMuPDF's page.rect is already reported in the page's
    UNROTATED coordinate space in most PDFs we've seen, so this function
    remaps x/y for a rotated page rather than assuming rect is already
    rotation-adjusted.

    NOTE: none of the 8 fixture papers used in this project are rotated, so
    the rotation != 0 branch below is NOT exercised by any test in this
    codebase yet. TODO: no rotated fixture available — validate against a
    real rotated PDF before relying on this branch.
    """
    x0, y0, x1, y1 = rect
    if rotation == 0:
        nx0, ny0, nx1, ny1 = x0, y0, x1, y1
        w, h = page_width_pt, page_height_pt
    elif rotation == 90:
        nx0, ny0, nx1, ny1 = y0, page_width_pt - x1, y1, page_width_pt - x0
        w, h = page_height_pt, page_width_pt
    elif rotation == 180:
        nx0, ny0, nx1, ny1 = page_width_pt - x1, page_height_pt - y1, page_width_pt - x0, page_height_pt - y0
        w, h = page_width_pt, page_height_pt
    elif rotation == 270:
        nx0, ny0, nx1, ny1 = page_height_pt - y1, x0, page_height_pt - y0, x1
        w, h = page_height_pt, page_width_pt
    else:
        raise ValueError(f"unsupported rotation: {rotation}")

    return BoundingBox(
        x0=max(0.0, min(nx0 / w, 1.0)),
        y0=max(0.0, min(ny0 / h, 1.0)),
        x1=max(0.0, min(nx1 / w, 1.0)),
        y1=max(0.0, min(ny1 / h, 1.0)),
    )


def normalized_to_pixel(bbox: BoundingBox, image_width_px, image_height_px):
    """Inverse of pdf_rect_to_normalized, targeting a rendered PNG's pixel
    grid — used by a future review UI to draw a bbox overlay on the page
    image. Returns (x0, y0, x1, y1) in pixels."""
    return (
        bbox.x0 * image_width_px,
        bbox.y0 * image_height_px,
        bbox.x1 * image_width_px,
        bbox.y1 * image_height_px,
    )


def pdf_points_to_pixels(rect, dpi):
    """rect in PDF points -> pixels at the given render dpi (72 points/inch)."""
    scale = dpi / 72.0
    x0, y0, x1, y1 = rect
    return (x0 * scale, y0 * scale, x1 * scale, y1 * scale)
