"""
Geometry/crop rules (spec §16.6).

Redundancy note: BoundingBox's own Pydantic validator (models/common.py)
already guarantees 0<=x0<x1<=1 / 0<=y0<y1<=1 for every persisted bbox — it is
structurally impossible for a Question in this codebase to have an
inverted/out-of-page bbox (that's exactly what caught the Q72 bug in
extractor.py itself, see HANDOFF.md §10). So "bbox valid and within page" is
NOT re-implemented here.

This module covers only what construction-time bbox validation can't catch:
the actual crop FILE's pixel content — too small, blank, or missing. This is
the one rule module that touches the filesystem (needs crops_root to resolve
a ContentBlock's clean_crop_path, which is stored relative to the
extraction output directory, e.g. "images/q12_stem.png").

NOT implemented (documented gaps, need adapter changes beyond this phase's
scope): "regions must not overlap unrelated questions" and "page continuation
must be explicit" — both need adjacent-question rects the adapter doesn't
retain post-conversion.
"""
import os
import uuid

from PIL import Image, ImageStat

from models.validation import ValidationIssue

MIN_CROP_DIMENSION_PX = 20
BLANK_CROP_STDDEV_THRESHOLD = 3.0


def _check_one_crop(crop_path, crops_root, document_id, question_id, field_path):
    full_path = os.path.join(crops_root, crop_path)
    if not os.path.isfile(full_path):
        return [ValidationIssue(
            issue_id=str(uuid.uuid4()),
            document_id=document_id,
            question_id=question_id,
            field_path=field_path,
            rule_code="GEOM_CROP_FILE_MISSING",
            severity="error",
            message=f"Crop file {crop_path!r} does not exist on disk.",
            evidence_path=crop_path,
        )]

    issues = []
    with Image.open(full_path) as img:
        if img.width < MIN_CROP_DIMENSION_PX or img.height < MIN_CROP_DIMENSION_PX:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4()),
                document_id=document_id,
                question_id=question_id,
                field_path=field_path,
                rule_code="GEOM_CROP_TOO_SMALL",
                severity="warning",
                message=f"Crop {crop_path!r} is {img.width}x{img.height}px, below the "
                        f"{MIN_CROP_DIMENSION_PX}px minimum in at least one dimension.",
                evidence_path=crop_path,
            ))

        stat = ImageStat.Stat(img.convert("L"))
        if stat.stddev[0] < BLANK_CROP_STDDEV_THRESHOLD:
            issues.append(ValidationIssue(
                issue_id=str(uuid.uuid4()),
                document_id=document_id,
                question_id=question_id,
                field_path=field_path,
                rule_code="GEOM_CROP_BLANK",
                severity="warning",
                message=f"Crop {crop_path!r} looks blank (pixel stddev {stat.stddev[0]:.2f} < "
                        f"{BLANK_CROP_STDDEV_THRESHOLD}).",
                evidence_path=crop_path,
            ))
    return issues


def check_geometry(question, crops_root, document_id) -> list[ValidationIssue]:
    if not crops_root:
        return []

    issues = []
    for idx, block in enumerate(question.stem_blocks):
        if block.clean_crop_path:
            issues += _check_one_crop(
                block.clean_crop_path, crops_root, document_id, question.question_id,
                f"stem_blocks[{idx}].clean_crop_path",
            )
    for opt in question.options:
        for idx, block in enumerate(opt.blocks):
            if block.clean_crop_path:
                issues += _check_one_crop(
                    block.clean_crop_path, crops_root, document_id, question.question_id,
                    f"options[{opt.label}].blocks[{idx}].clean_crop_path",
                )
    return issues
