"""
Accuracy fix: DeepSeek's bounding boxes are sometimes drawn too tight,
cutting off content that extends above a normal text baseline (radicals,
superscripts, stacked-fraction numerators) — confirmed live on multiple
real questions this session. Every detected region is padded by a small
margin before cropping (more on top, where clipping was observed) as a
deterministic accuracy improvement.
"""
from extraction.vision_layout_adapter import (
    _BBOX_PAD_BOTTOM, _BBOX_PAD_SIDES, _BBOX_PAD_TOP, _pad_and_build_bbox,
)
from models.vision import RegionBBox


def test_pads_more_on_top_than_other_sides():
    region_bbox = RegionBBox(x0=0.3, y0=0.3, x1=0.5, y1=0.5)
    bbox = _pad_and_build_bbox(region_bbox)
    assert bbox.x0 == 0.3 - _BBOX_PAD_SIDES
    assert bbox.y0 == 0.3 - _BBOX_PAD_TOP
    assert bbox.x1 == 0.5 + _BBOX_PAD_SIDES
    assert bbox.y1 == 0.5 + _BBOX_PAD_BOTTOM
    assert _BBOX_PAD_TOP > _BBOX_PAD_BOTTOM > 0
    assert _BBOX_PAD_TOP > _BBOX_PAD_SIDES > 0


def test_clamps_to_page_bounds_near_edges():
    region_bbox = RegionBBox(x0=0.001, y0=0.001, x1=0.999, y1=0.999)
    bbox = _pad_and_build_bbox(region_bbox)
    assert bbox.x0 == 0.0
    assert bbox.y0 == 0.0
    assert bbox.x1 == 1.0
    assert bbox.y1 == 1.0


def test_padded_box_still_valid_and_wider_than_original():
    region_bbox = RegionBBox(x0=0.2, y0=0.2, x1=0.25, y1=0.22)
    bbox = _pad_and_build_bbox(region_bbox)
    assert bbox.x0 < region_bbox.x0
    assert bbox.y0 < region_bbox.y0
    assert bbox.x1 > region_bbox.x1
    assert bbox.y1 > region_bbox.y1
    assert bbox.x0 < bbox.x1
    assert bbox.y0 < bbox.y1


# Second real bug found live this session: on a page with tightly-packed
# option rows (~3.6% of page height apart), the fixed 2% top padding above
# ate over half the gap to the neighboring option above it, bleeding that
# neighbor's text into the crop (reported by the user as "horribly
# cropped" on Q4's options). Padding must never eat past the midpoint to
# a genuine neighboring region on the same page.
def test_padding_clamps_to_midpoint_when_neighbor_is_close_above():
    # Neighbor directly above, gap of only 0.01 (1%) -- less than
    # _BBOX_PAD_TOP (0.02), so top padding must clamp to half the gap.
    region_bbox = RegionBBox(x0=0.1, y0=0.20, x1=0.9, y1=0.30)
    neighbor_above = RegionBBox(x0=0.1, y0=0.15, x1=0.9, y1=0.19)
    bbox = _pad_and_build_bbox(region_bbox, [region_bbox, neighbor_above])
    assert bbox.y0 == 0.20 - 0.005  # (0.20 - 0.19) / 2
    assert bbox.y0 > neighbor_above.y1  # never crosses into the neighbor


def test_padding_clamps_to_midpoint_when_neighbor_is_close_below():
    region_bbox = RegionBBox(x0=0.1, y0=0.20, x1=0.9, y1=0.30)
    neighbor_below = RegionBBox(x0=0.1, y0=0.31, x1=0.9, y1=0.40)
    bbox = _pad_and_build_bbox(region_bbox, [region_bbox, neighbor_below])
    assert bbox.y1 == 0.30 + 0.005  # (0.31 - 0.30) / 2
    assert bbox.y1 < neighbor_below.y0


def test_padding_unaffected_when_neighbor_is_far_away():
    region_bbox = RegionBBox(x0=0.1, y0=0.20, x1=0.9, y1=0.30)
    distant_neighbor = RegionBBox(x0=0.1, y0=0.80, x1=0.9, y1=0.90)
    bbox = _pad_and_build_bbox(region_bbox, [region_bbox, distant_neighbor])
    assert bbox.y0 == 0.20 - _BBOX_PAD_TOP
    assert bbox.y1 == 0.30 + _BBOX_PAD_BOTTOM


def test_padding_unaffected_by_non_overlapping_column_neighbor():
    # A region in a different horizontal column (no horizontal overlap)
    # must not constrain vertical padding at all.
    region_bbox = RegionBBox(x0=0.1, y0=0.20, x1=0.4, y1=0.30)
    other_column = RegionBBox(x0=0.5, y0=0.19, x1=0.9, y1=0.31)
    bbox = _pad_and_build_bbox(region_bbox, [region_bbox, other_column])
    assert bbox.y0 == 0.20 - _BBOX_PAD_TOP
    assert bbox.y1 == 0.30 + _BBOX_PAD_BOTTOM


def test_padding_with_no_neighbor_list_behaves_like_before():
    region_bbox = RegionBBox(x0=0.3, y0=0.3, x1=0.5, y1=0.5)
    bbox = _pad_and_build_bbox(region_bbox)
    assert bbox.y0 == 0.3 - _BBOX_PAD_TOP
    assert bbox.y1 == 0.5 + _BBOX_PAD_BOTTOM


# Third real bug found live this session (after the first collision-aware
# clamp still bled): the vision model's own RAW region boxes can already
# overlap a neighbor slightly (confirmed live on JEE ADV 2007-1.pdf's Q4
# options, ~0.2-0.3% of page height each) -- a clamp that only reduces
# ADDED padding when there's a genuine non-overlapping gap does nothing in
# that case, so full fixed padding still got added on top of an already-
# overlapping raw box, making the bleed worse rather than better. The
# fix computes each final edge as the midpoint between this region's own
# raw edge and the qualifying neighbor's facing edge, which naturally
# trims an already-overlapping raw box back to a clean split.
def test_padding_trims_raw_overlap_between_three_stacked_neighbors():
    opt_a = RegionBBox(x0=0.05, y0=0.1010, x1=0.95, y1=0.1370)
    opt_b = RegionBBox(x0=0.05, y0=0.1350, x1=0.95, y1=0.1720)
    opt_c = RegionBBox(x0=0.05, y0=0.1690, x1=0.95, y1=0.2050)
    all_boxes = [opt_a, opt_b, opt_c]
    a_padded = _pad_and_build_bbox(opt_a, all_boxes)
    b_padded = _pad_and_build_bbox(opt_b, all_boxes)
    c_padded = _pad_and_build_bbox(opt_c, all_boxes)
    assert a_padded.y1 <= b_padded.y0
    assert b_padded.y1 <= c_padded.y0
