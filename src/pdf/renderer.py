"""
Fresh, standalone page renderer for Phase 1 — deliberately NOT a wrapper
around extractor.render_pages(). That function serves a different job (fast,
ephemeral, session-scoped UI preview at 130 DPI, no hashing, no DB tracking,
written into a throwaway tempdir). This renderer produces persistent, hashed,
300-DPI-default, rotation-aware output that later phases treat as ground
truth for evidence/review — a different enough purpose that reusing/wrapping
the existing function would either waste a second render pass or require
parameterizing extractor.py for zero benefit to this phase's job. extractor.py
is not touched here.
"""
import hashlib
import os
from dataclasses import dataclass

import fitz


@dataclass
class RenderedPage:
    page_number: int  # 1-indexed
    file_path: str
    width_pt: float
    height_pt: float
    rotation: int
    sha256: str
    width_px: int
    height_px: int


def render_document(document_id: str, pdf_path: str, out_dir: str, dpi: int = 300) -> list[RenderedPage]:
    doc = fitz.open(pdf_path)
    try:
        target_dir = os.path.join(out_dir, document_id)
        os.makedirs(target_dir, exist_ok=True)

        results = []
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=dpi)
            file_path = os.path.join(target_dir, f"page_{i + 1:03d}.png")
            pix.save(file_path)

            with open(file_path, "rb") as f:
                sha256 = hashlib.sha256(f.read()).hexdigest()

            results.append(RenderedPage(
                page_number=i + 1,
                file_path=file_path,
                width_pt=page.rect.width,
                height_pt=page.rect.height,
                rotation=page.rotation,
                sha256=sha256,
                width_px=pix.width,
                height_px=pix.height,
            ))
        return results
    finally:
        doc.close()
