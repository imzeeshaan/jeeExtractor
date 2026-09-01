import fitz
import re
import os
from PIL import Image


def render_pages(pdf_path, out_dir, dpi=130):
    """Render every page of the PDF to a PNG for full-document preview. Returns list of file paths."""
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)
    paths = []
    for i, page in enumerate(doc):
        pix = page.get_pixmap(dpi=dpi)
        path = os.path.join(out_dir, f"page_{i + 1}.png")
        pix.save(path)
        paths.append(path)
    doc.close()
    return paths


def _crop(page, rect, path, pad=3, dpi=200):
    r = fitz.Rect(rect[0] - pad, rect[1] - pad, rect[2] + pad, rect[3] + pad)
    pix = page.get_pixmap(dpi=dpi, clip=r)
    pix.save(path)


def _stack_vertically(paths, out_path):
    """Combine multiple page-crop PNGs (e.g. a stem split across a page break) into one image."""
    imgs = [Image.open(p) for p in paths]
    width = max(im.width for im in imgs)
    height = sum(im.height for im in imgs)
    canvas = Image.new("RGB", (width, height), "white")
    y = 0
    for im in imgs:
        if im.width != width:
            im = im.resize((width, round(im.height * width / im.width)))
        canvas.paste(im, (0, y))
        y += im.height
    canvas.save(out_path)


def _header_bottom_y(text_dict, default=55.0):
    """Find where the repeating page header/branding ends, so a stem that continues onto the
    next page can be captured starting right after it (not "JEE Main 2012 ... MathonGo" noise)."""
    y1 = 0
    for block in text_dict["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                t = span["text"].strip()
                if t and (re.match(r'^JEE Main \d{4}', t) or t in ("Question Paper", "MathonGo")
                          or "Previous Year Paper" in t):
                    y1 = max(y1, span["bbox"][3])
    return y1 + 4 if y1 > 0 else default


def _find_answer_key_page(doc):
    for i, page in enumerate(doc):
        if "ANSWER KEY" in page.get_text().upper():
            return i
    return None


def _group_rows(markers, y_tol=6):
    """Group (label, x, y) markers into rows by y-proximity, sorted top-to-bottom, then left-to-right."""
    markers = sorted(markers, key=lambda m: m[2])
    rows = []
    for m in markers:
        placed = False
        for row in rows:
            if abs(row[0][2] - m[2]) <= y_tol:
                row.append(m)
                placed = True
                break
        if not placed:
            rows.append([m])
    for row in rows:
        row.sort(key=lambda m: m[1])
    return rows


def _column_boundaries(marker_xs, margin=2.0):
    """One split line between each adjacent pair of marker x-positions, placed just before the
    next marker's own x. A real exam layout never lets one option's content overlap the next
    option's marker, so this is safe — and unlike bisecting the midpoint between two markers, it
    never cuts through a long option's overflowing text (the bug hit with "translational" wrapping
    past the column midpoint, and a "Statement 1..." option almost touching the next marker)."""
    return [marker_xs[i + 1] - margin for i in range(len(marker_xs) - 1)]


def _image_in_rect(bbox, rect, min_overlap=0.5):
    ix0, iy0, ix1, iy1 = max(bbox.x0, rect[0]), max(bbox.y0, rect[1]), min(bbox.x1, rect[2]), min(bbox.y1, rect[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return False
    inter_area = (ix1 - ix0) * (iy1 - iy0)
    bbox_area = max((bbox.x1 - bbox.x0) * (bbox.y1 - bbox.y0), 1e-6)
    return (inter_area / bbox_area) >= min_overlap


def parse_pdf(pdf_path, out_dir):
    """
    Extract every question as a stem + 4 individually-extracted options, plus the matching
    answer key entry. Returns (questions, notes).

    questions: list of dicts:
      {
        "question_number": int,
        "stem_text": str,
        "stem_snippet": "images/qN_stem.png",
        "stem_images": [ "images/qN_stem_diagram_k.png", ... ],
        "options": [
          {"label": "1", "text": str, "snippet": "images/qN_opt1.png",
           "images": ["images/qN_opt1_diagram_k.png", ...]},
          ... for "2", "3", "4"
        ],
        "answer": "2" or None
      }
    notes: list of strings flagging any case that couldn't be confidently associated
    """
    img_dir = os.path.join(out_dir, "images")
    os.makedirs(img_dir, exist_ok=True)

    doc = fitz.open(pdf_path)
    answer_page_idx = _find_answer_key_page(doc)
    question_page_range = range(0, answer_page_idx if answer_page_idx is not None else doc.page_count)

    answers = {}
    if answer_page_idx is not None:
        answer_text = doc[answer_page_idx].get_text()
        # value inside the parens is usually a single MCQ digit, but Numerical Value questions
        # (introduced in 2019+ JEE Main papers) key multi-digit/decimal/negative numbers, e.g. "(-2.7)"
        for m in re.finditer(r'(\d+)\.\s*\(([^)]+)\)', answer_text):
            answers[int(m.group(1))] = m.group(2).strip()

    questions = []
    notes = []

    # --- precompute per-page data so a question can look ahead to the next page ---
    pages_info = {}
    for pi in question_page_range:
        page = doc[pi]
        text_dict = page.get_text("dict")
        rawdict = page.get_text("rawdict")
        page_h = page.rect.height

        q_markers = []
        for block in text_dict["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    m = re.match(r'Q(\d+)\.$', span["text"].strip())
                    if m:
                        q_markers.append((int(m.group(1)), span["bbox"]))
        q_markers.sort(key=lambda t: t[1][1])

        img_boxes = []
        for img in page.get_images(full=True):
            try:
                img_boxes.append(page.get_image_bbox(img))
            except Exception:
                pass

        # footer link ("Join the Most Relevant Test Series...") sits below the last question on
        # some pages and must not be treated as part of that question's last option
        footer_y = page_h - 20
        for block in text_dict["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    if span["text"].strip().startswith("Join the Most Relevant"):
                        footer_y = min(footer_y, span["bbox"][1] - 2)

        pages_info[pi] = {
            "page": page, "text_dict": text_dict, "rawdict": rawdict, "q_markers": q_markers,
            "img_boxes": img_boxes, "footer_y": footer_y, "header_y": _header_bottom_y(text_dict),
        }

    def find_opt_markers(rawdict, y_lo, y_hi):
        """Find every '(N)' marker occurrence using character-level positions, since long options
        sometimes place two markers on the same text span (e.g. one line holding both (1) and (2)).

        A match is rejected when the character immediately before it is alphanumeric — that's the
        signature of a "(N)" appearing inside a math expression (e.g. a stem containing "f(1)"),
        not a real option marker, which is always preceded by nothing (start of a run) or a
        non-alnum character. Found via a real bug: JEE Main 2012 (19 May Online) Q72's stem is
        "... f(1-a)-f(1) ...", whose embedded "(1)" was misdetected as an option marker sitting
        above the real option row, corrupting the stem's own boundary (stem_y_end computed from
        the topmost "row" ended up above the stem's start, producing a degenerate near-empty crop
        and empty stem_text) — invisible until real bounding-box evidence exposed the inverted rect."""
        found = []
        for block in rawdict["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    chars = span.get("chars", [])
                    text = "".join(c["c"] for c in chars)
                    for m in re.finditer(r'\((\d)\)', text):
                        if m.start() > 0 and text[m.start() - 1].isalnum():
                            continue
                        cbbox = chars[m.start()]["bbox"]
                        if y_lo <= cbbox[1] <= y_hi:
                            found.append((m.group(1), cbbox[0], cbbox[1]))
        return found

    for pi in question_page_range:
        info = pages_info.get(pi)
        if not info or not info["q_markers"]:
            continue
        page, text_dict, q_markers = info["page"], info["text_dict"], info["q_markers"]
        img_boxes, footer_y = info["img_boxes"], info["footer_y"]
        block_left, block_right = 30, 565

        for idx, (qnum, qbbox) in enumerate(q_markers):
            y_start = qbbox[1] - 2
            y_end = q_markers[idx + 1][1][1] - 2 if idx + 1 < len(q_markers) else footer_y

            # --- find option markers within this block ---
            opt_markers = find_opt_markers(info["rawdict"], y_start, y_end)

            # options can wrap onto the next page (appear before that page's first question)
            opt_page, opt_text_dict, opt_img_boxes = page, text_dict, img_boxes
            opt_y_lo, opt_y_hi = y_start, y_end
            stem_cont = None  # (page, img_boxes, rect) for stem text/diagram spilling onto next page
            if not opt_markers and idx == len(q_markers) - 1 and (pi + 1) in pages_info:
                next_info = pages_info[pi + 1]
                next_q_markers = next_info["q_markers"]
                boundary = next_q_markers[0][1][1] - 2 if next_q_markers else next_info["footer_y"]
                candidate = find_opt_markers(next_info["rawdict"], 0, boundary)
                if candidate:
                    opt_markers = candidate
                    opt_page, opt_text_dict = next_info["page"], next_info["text_dict"]
                    opt_img_boxes = next_info["img_boxes"]
                    opt_y_lo, opt_y_hi = 0, boundary
                    notes.append(f"Q{qnum}: options continue on page {pi + 2} (wrapped from page {pi + 1})")

                    # the stem itself (text and/or a diagram) can also spill onto that page, in the
                    # gap between the page header and where the (wrapped) options start. Only treat
                    # it as a real continuation if that gap actually holds text or an image — a plain
                    # top margin (header ends, options start a few points later) is not a continuation.
                    row_top_next = _group_rows(candidate)[0][0][2] - 2
                    header_y_next = next_info["header_y"]
                    if row_top_next > header_y_next + 4:
                        cont_rect = (block_left, header_y_next, block_right, row_top_next)
                        has_text = bool(next_info["page"].get_text("text", clip=fitz.Rect(*cont_rect)).strip())
                        has_image = any(_image_in_rect(bb, cont_rect) for bb in next_info["img_boxes"])
                        if has_text or has_image:
                            stem_cont = (next_info["page"], next_info["img_boxes"], cont_rect)
                            notes.append(f"Q{qnum}: stem continues on page {pi + 2} (wrapped from page {pi + 1})")

            options_on_own_page = opt_page is page
            rows = _group_rows(opt_markers) if opt_markers else []
            stem_y_end = (rows[0][0][2] - 2) if (rows and options_on_own_page) else y_end

            # --- stem (text + crop + diagrams, plus any continuation on the next page) ---
            stem_rect = (block_left, y_start, block_right, stem_y_end)
            stem_parts = [(page, img_boxes, stem_rect)]
            if stem_cont is not None:
                stem_parts.append(stem_cont)

            stem_text = " ".join(
                " ".join(src_page.get_text("text", clip=fitz.Rect(*rect)).split())
                for src_page, _, rect in stem_parts
            ).strip()

            stem_snippet_name = f"q{qnum}_stem.png"
            if len(stem_parts) == 1:
                _crop(page, stem_rect, os.path.join(img_dir, stem_snippet_name))
            else:
                part_paths = []
                for k, (src_page, _, rect) in enumerate(stem_parts):
                    part_path = os.path.join(img_dir, f"q{qnum}_stem_part{k}.png")
                    _crop(src_page, rect, part_path)
                    part_paths.append(part_path)
                _stack_vertically(part_paths, os.path.join(img_dir, stem_snippet_name))
                # the per-page-fragment crops are kept on disk (not removed) so a
                # canonical-model consumer (see src/extraction/legacy_mathongo_adapter.py)
                # can build one ContentBlock per real page fragment instead of only
                # having the single stitched image to work with

            stem_images = []
            for src_page, boxes, rect in stem_parts:
                for b in [bb for bb in boxes if _image_in_rect(bb, rect)]:
                    fname = f"q{qnum}_stem_diagram_{len(stem_images) + 1}.png"
                    _crop(src_page, (b.x0, b.y0, b.x1, b.y1), os.path.join(img_dir, fname))
                    stem_images.append(f"images/{fname}")

            # --- options: build a rect per marker using row/column neighbors ---
            option_rects = {}  # label -> rect
            for r_idx, row in enumerate(rows):
                row_y_top = row[0][2] - 2
                row_y_bottom = rows[r_idx + 1][0][2] - 2 if r_idx + 1 < len(rows) else opt_y_hi
                marker_xs = [mx for (_, mx, _) in row]
                boundaries = _column_boundaries(marker_xs)
                edges = [block_left] + boundaries + [block_right]
                for c_idx, (label, mx, my) in enumerate(row):
                    option_rects[label] = (edges[c_idx], row_y_top, edges[c_idx + 1], row_y_bottom)

            # a question with no "(N)" markers at all isn't a parsing miss — it's a Numerical Value
            # question (2019+ JEE Main pattern): a fill-in-the-blank numeric answer, no MCQ options
            question_type = "mcq" if opt_markers else "numerical"

            options = []
            if question_type == "numerical":
                notes.append(f"Q{qnum}: no options found — treated as a Numerical Value question (page {pi + 1})")
            else:
                for label in ["1", "2", "3", "4"]:
                    rect = option_rects.get(label)
                    if rect is None:
                        options.append({"label": label, "text": "", "snippet": None, "images": []})
                        notes.append(f"Q{qnum}: option ({label}) marker not found (page {pi + 1})")
                        continue
                    text = " ".join(opt_page.get_text("text", clip=fitz.Rect(*rect)).split())
                    snippet_name = f"q{qnum}_opt{label}.png"
                    _crop(opt_page, rect, os.path.join(img_dir, snippet_name))
                    opt_imgs = []
                    for j, b in enumerate([bb for bb in opt_img_boxes if _image_in_rect(bb, rect)]):
                        fname = f"q{qnum}_opt{label}_diagram_{j + 1}.png"
                        _crop(opt_page, (b.x0, b.y0, b.x1, b.y1), os.path.join(img_dir, fname))
                        opt_imgs.append(f"images/{fname}")
                    options.append({
                        "label": label,
                        "text": text,
                        "snippet": f"images/{snippet_name}",
                        "images": opt_imgs,
                        # additive geometry evidence (rect in PDF points, 1-indexed page
                        # number) for callers building canonical bounding-box evidence —
                        # existing keys/values above are unchanged
                        "rect": rect,
                        "page": (pi + 1) if options_on_own_page else (pi + 2),
                    })

            # sanity check: every embedded image in the stem's own region should land in stem or an option
            # (only meaningful when options are on the same page as the stem)
            if options_on_own_page:
                block_images = [bb for bb in img_boxes if y_start <= (bb.y0 + bb.y1) / 2 <= y_end]
                assigned_count = len(stem_images) + sum(len(o["images"]) for o in options)
                if len(block_images) != assigned_count:
                    notes.append(
                        f"Q{qnum}: found {len(block_images)} embedded image(s) in this question's region but "
                        f"only assigned {assigned_count} to stem/options (page {pi + 1}) — check for overlap or gaps."
                    )

            question_dict = {
                "question_number": qnum,
                "question_type": question_type,
                "stem_text": stem_text,
                "stem_snippet": f"images/{stem_snippet_name}",
                "stem_images": stem_images,
                "options": options,
                "answer": answers.get(qnum),
                # additive geometry evidence — same rationale as the option "rect"/"page"
                # keys above; existing keys/values are unchanged
                "stem_rect": stem_rect,
                "stem_page": pi + 1,
                "stem_end_page": (pi + 2) if stem_cont is not None else (pi + 1),
            }
            if stem_cont is not None:
                question_dict["stem_cont_rect"] = stem_cont[2]
            questions.append(question_dict)

    questions.sort(key=lambda q: q["question_number"])
    doc.close()
    return questions, notes
