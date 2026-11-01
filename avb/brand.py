"""Brand constraint layer -- an IR-level transform.

Applying a brand kit is the same kind of operation as applying a template:
it only mutates the SceneGraph IR, never the pixels. The compositor later
reads ``project.brand`` and the per-scene ``CaptionStyle`` (which we stamp
here) to keep every render of a product on-style.

The web layer is responsible for *storing* brand kits (per product) and
resolving which kit to use; this module only knows how to fold a brand kit
into a Project. Keeping the two apart means ``avb`` has no dependency on the
web SQLite store.
"""

from __future__ import annotations

from dataclasses import fields
from typing import Any

from .ir import BrandKit, CaptionStyle, Project

# Sentinel product key for the global default brand kit.
DEFAULT_BRAND_KEY = "__default__"


def default_brand_kit() -> BrandKit:
    """The out-of-the-box brand kit (identical to before this layer existed)."""
    return BrandKit()


def brand_kit_from_dict(d: dict[str, Any] | None) -> BrandKit:
    """Build a BrandKit from a (possibly partial) dict, filling defaults."""
    return BrandKit.from_dict(d or {})


def apply_brand_to_project(
    project: Project,
    brand: dict[str, Any] | BrandKit | None,
) -> Project:
    """Fold a brand kit into ``project`` and propagate caption defaults.

    ``brand`` may be a partial dict (only the keys the user edited) or a
    full ``BrandKit``. ``None`` is a no-op so callers can always call this.

    String fields that are empty mean "inherit the style default" and are
    skipped; ``None`` (not present) is likewise skipped. Booleans and
    numbers are applied whenever the key is present, so ``watermark: false``
    and ``logo_opacity: 0`` are honoured.
    """
    if brand is None:
        return project
    if isinstance(brand, BrandKit):
        brand = brand.to_dict()

    bk = project.brand
    declared = {f.name for f in fields(BrandKit)}
    for key in declared:
        if key not in brand:
            continue
        val = brand[key]
        if val is None:
            continue
        # Empty string on a string field = "don't override".
        if isinstance(val, str) and val == "":
            continue
        setattr(bk, key, val)

    # Propagate the unified caption style onto every scene so batch renders
    # share one subtitle look. Empty / None values are left as the scene's
    # own (style-derived) value.
    cap_mode = brand.get("caption_mode")
    cap_pos = brand.get("caption_position")
    cap_hl = brand.get("caption_highlight")
    cap_pop = brand.get("caption_scale_pop")
    if any(v for v in (cap_mode, cap_pos, cap_hl, cap_pop is not None)):
        for scene in project.scenes:
            cap: CaptionStyle = scene.caption
            if cap_mode:
                cap.mode = cap_mode
            if cap_pos:
                cap.position = cap_pos
            if cap_hl:
                cap.highlight = cap_hl
            if cap_pop is not None:
                cap.scale_pop = float(cap_pop)

    return project
