"""Frame-by-frame compositor.

Renders each scene as a sequence of RGB frames:

    background (+ affine motion)
      -> graphic overlay (animated, per beat)
      -> legibility scrim
      -> kinetic captions (word-level highlight, driven by VO timings)
      -> brand chrome + progress bar
      -> pattern interrupt (flash / punch / shake)

The frame loop is deliberately dumb and synchronous. In production this
would fan out across a render farm (Remotion Lambda, or plain ffmpeg
workers), but keeping it serial makes the cache behaviour observable --
which is the thing this demo is actually meant to demonstrate.
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..assets.image import hex2rgb
from ..ir import CaptionStyle, Project, Scene
from ..styles import StyleSpec, get_style, resolve_font, scale_for_canvas

# --------------------------------------------------------------------------
# Fonts
# --------------------------------------------------------------------------

_FONT_CACHE: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}
_CACHE_LOCK = threading.Lock()


def load_font(spec: StyleSpec, size: int, font_key: str | None = None) -> ImageFont.FreeTypeFont | None:
    key = (font_key or spec.font_key, size)
    with _CACHE_LOCK:
        hit = _FONT_CACHE.get(key)
        if hit is not None:
            return hit
    path = resolve_font(font_key or spec.font_key) or resolve_font("arialbd")
    if not path:
        return None
    try:
        f = ImageFont.truetype(path, size)
    except Exception:
        return None
    with _CACHE_LOCK:
        _FONT_CACHE[key] = f
    return f


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------


def text_with_stroke(
    d: ImageDraw.ImageDraw,
    xy: tuple[float, float],
    text: str,
    font: ImageFont.FreeTypeFont | None,
    fill: tuple[int, int, int],
    stroke_fill: tuple[int, int, int],
    stroke_width: int,
    anchor: str = "mm",
) -> None:
    """Draw text with an outline in a single call.

    The outline is not decoration -- it is what keeps captions readable over
    busy B-roll, which matters because most Meta impressions arrive muted.

    PIL supports stroke natively (C-level, one draw). The obvious alternative
    -- stamping the text at 25 offsets to fake an outline -- costs roughly
    25x the time per word per frame, which at 30fps is the difference between
    a 40-second render and a 3-minute one.
    """
    if font is None:
        d.text(xy, text, fill=fill, anchor=anchor)
        return
    d.text(
        xy,
        text,
        font=font,
        fill=fill,
        anchor=anchor,
        stroke_width=stroke_width,
        stroke_fill=stroke_fill,
    )


def text_size(d: ImageDraw.ImageDraw, text: str, font) -> tuple[float, float]:
    if font is None:
        return (len(text) * 8.0, 16.0)
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    return (r - l, b - t)


# --------------------------------------------------------------------------
# Easing
# --------------------------------------------------------------------------


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else (1.0 if x > 1 else x)


def ease_out_cubic(t: float) -> float:
    t = clamp01(t)
    return 1.0 - (1.0 - t) ** 3


def ease_out_back(t: float, s: float = 1.9) -> float:
    t = clamp01(t)
    return 1.0 + (s + 1.0) * (t - 1.0) ** 3 + s * (t - 1.0) ** 2


def ease_out_elastic(t: float) -> float:
    t = clamp01(t)
    if t == 0 or t == 1:
        return t
    p = 0.42
    return float(2.0 ** (-9.0 * t) * math.sin((t - p / 4.0) * (2 * math.pi) / p) + 1.0)


# --------------------------------------------------------------------------
# Motion
# --------------------------------------------------------------------------


def motion_window(motion: str, t: float, interrupt: float) -> tuple[float, float, float]:
    """Return (zoom, center_x_frac, center_y_frac) for a moment in a scene."""
    punch_boost = 1.0 + 0.55 * interrupt

    if motion == "static":
        return (1.02, 0.5, 0.5)
    if motion == "zoom_in":
        return (1.02 + 0.16 * t * punch_boost, 0.5, 0.5)
    if motion == "zoom_out":
        return (1.18 * punch_boost - 0.16 * t, 0.5, 0.5)
    if motion == "pan_left":
        return (1.12 * punch_boost, 0.34 + 0.32 * t, 0.5)
    if motion == "pan_right":
        return (1.12 * punch_boost, 0.66 - 0.32 * t, 0.5)
    # punch: snap in hard, settle back
    z = 1.02 + (0.20 * punch_boost) * ease_out_cubic(min(1.0, t / 0.30)) - 0.035 * max(0.0, t - 0.30)
    return (z, 0.5, 0.5)


def apply_motion(bg: Image.Image, motion: str, t: float, W: int, H: int, interrupt: float, shake: float = 0.0) -> Image.Image:
    z, cx, cy = motion_window(motion, t, interrupt)
    z = max(1.0, z)
    cw, ch = W / z, H / z
    iw, ih = bg.size
    x0 = (iw - cw) * cx
    y0 = (ih - ch) * cy
    x0 = max(0.0, min(iw - cw, x0))
    y0 = max(0.0, min(ih - ch, y0))
    if shake:
        x0 += shake * math.sin(t * 90.0) * W * 0.012
        y0 += shake * math.cos(t * 77.0) * H * 0.006
        x0 = max(0.0, min(iw - cw, x0))
        y0 = max(0.0, min(ih - ch, y0))
    return bg.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize((W, H), Image.BILINEAR)


# --------------------------------------------------------------------------
# Captions
# --------------------------------------------------------------------------


def chunk_words(words: list[dict], per_chunk: int = 3) -> list[list[dict]]:
    """Group words into caption cards.

    Three words per card is the TikTok/Reels default for a reason: it is
    about as much as someone reads in one fixation without pausing the
    scroll. Longer sentences get skipped.
    """
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        cur.append(w)
        tail = w["word"].rstrip()
        done = len(cur) >= per_chunk
        if tail.endswith((",", ".", "!", "?", ";", ":")):
            done = True
        if done:
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def draw_caption(
    frame: Image.Image,
    chunks: list[list[dict]],
    t: float,
    spec: StyleSpec,
    cap: "CaptionStyle",
    W: int,
    H: int,
    energy: float,
    font_key: str | None = None,
) -> None:
    if not chunks:
        return

    # find the card whose time span contains t
    active_i = 0
    for i, ch in enumerate(chunks):
        s = ch[0]["start"]
        e = ch[-1]["end"] + 0.10
        if s <= t < e:
            active_i = i
            break
        if t >= e:
            active_i = min(i + 1, len(chunks) - 1)

    chunk = chunks[active_i]
    cs = chunk[0]["start"]
    ce = chunk[-1]["end"] + 0.10
    local = clamp01((t - cs) / max(1e-6, ce - cs))

    font_size = scale_for_canvas(spec, "", W, H)
    font = load_font(spec, font_size, font_key)
    small = load_font(spec, max(20, int(font_size * 0.92)), font_key)

    d = ImageDraw.Draw(frame)

    # current word
    wi = 0
    for i, w in enumerate(chunk):
        if w["start"] <= t < w["end"]:
            wi = i
            break
        if t >= w["end"]:
            wi = min(i + 1, len(chunk) - 1)

    def upper(s: str) -> str:
        return s.upper() if spec.uppercase else s

    # measure and wrap to the safe width. A single-line layout is what makes
    # long sentences bleed off the frame, which is the difference between
    # "looked fine in the preview" and "readable on a phone at arm's length".
    max_w = W * 0.90
    gap = font_size * 0.30
    widths = [text_size(d, upper(w["word"]), font)[0] for w in chunk]

    # Fit pass: if one word alone overruns the safe area, shrink until it
    # fits. Wrapping cannot help a single long token.
    widest = max(widths) if widths else 0.0
    if widest > max_w:
        font_size = max(12, int(font_size * (max_w / widest)))
        font = load_font(spec, font_size)
        gap = font_size * 0.30
        widths = [text_size(d, upper(w["word"]), font)[0] for w in chunk]

    lines: list[list[int]] = [[]]
    line_w = 0.0
    for i, w in enumerate(widths):
        add = w if not lines[-1] else w + gap
        if lines[-1] and line_w + add > max_w:
            lines.append([i])
            line_w = w
        else:
            lines[-1].append(i)
            line_w += add

    def line_width(idx: list[int]) -> float:
        return sum(widths[i] for i in idx) + gap * (len(idx) - 1)

    line_h = font_size * 1.34
    block_h = line_h * len(lines)

    if cap.position == "center":
        base_y = H * 0.50
    elif cap.position == "top":
        base_y = H * 0.20
    else:
        base_y = H * 0.72

    # card entry: slide up + fade
    enter = ease_out_cubic(local / 0.22)
    base_y += (1.0 - enter) * H * 0.035
    # vertical centre the wrapped block on base_y
    y0 = base_y - block_h / 2.0 + line_h / 2.0

    fill = hex2rgb(spec.text_color)
    stroke = hex2rgb(spec.stroke_color)
    hl = hex2rgb(spec.highlight)

    for li, idx in enumerate(lines):
        x = (W - line_width(idx)) / 2.0
        y = y0 + li * line_h
        for i in idx:
            w = chunk[i]
            is_active = i == wi
            wp = clamp01((t - w["start"]) / max(1e-6, w["end"] - w["start"]))
            # pop: overshoot then settle
            pop = 1.0 + cap.scale_pop * ease_out_elastic(min(1.0, wp * 1.6)) * (0.6 + 0.4 * energy)
            size = max(12, int(font_size * (pop if is_active else 1.0)))
            f = load_font(spec, size, font_key) or font
            cx = x + widths[i] / 2
            x += widths[i] + gap
            col = hl if is_active else fill
            text_with_stroke(
                d,
                (cx, y),
                upper(w["word"]),
                f,
                fill=col,
                stroke_fill=stroke,
                stroke_width=spec.stroke_width,
                anchor="mm",
            )


# --------------------------------------------------------------------------
# Chrome
# --------------------------------------------------------------------------


def draw_chrome(
    frame: Image.Image,
    project: Project,
    spec: StyleSpec,
    W: int,
    H: int,
    global_t: float,
    total: float,
    scene: Scene,
    font_key: str | None = None,
) -> None:
    d = ImageDraw.Draw(frame)
    pad = int(W * 0.055)
    brand_font_key = font_key or (project.brand.font_key or None)

    # brand mark (uses the brand font when one is set)
    small = load_font(spec, max(18, int(W * 0.038)), brand_font_key)
    if small:
        text_with_stroke(
            d,
            (pad, pad + int(W * 0.03)),
            project.brand.name[:22],
            small,
            fill=hex2rgb(spec.text_color),
            stroke_fill=hex2rgb(spec.stroke_color),
            stroke_width=max(4, spec.stroke_width // 2),
            anchor="lm",
        )

    # logo watermark -- burned in every scene so batch renders stay branded
    if project.brand.watermark and project.brand.logo_path:
        _draw_logo_watermark(d, frame, project.brand, W, H)

    # progress bar
    bh = max(4, int(H * 0.006))
    y = H - bh - int(H * 0.045)
    d.rectangle([pad, y, W - pad, y + bh], fill=(255, 255, 255, 70))
    prog = clamp01(global_t / max(1e-6, total))
    d.rounded_rectangle([pad, y, pad + (W - 2 * pad) * prog, y + bh], radius=bh // 2, fill=hex2rgb(project.brand.accent))

    # fixed CTA card -- identical across every variant of the same product
    if scene.beat == "cta" and project.brand.cta_text:
        _draw_cta_card(d, project.brand, W, H, brand_font_key, spec)

    # lower third for presenter styles
    if "lower_third" in spec.overlays:
        lh = int(H * 0.115)
        ly = H * 0.80
        d.rectangle([0, ly, W * 0.72, ly + lh], fill=hex2rgb(project.brand.ink))
        d.rectangle([0, ly, int(W * 0.008), ly + lh], fill=hex2rgb(project.brand.primary))
        if small:
            d.text((pad, ly + lh * 0.36), "Dr. Elena Voss", fill=(255, 255, 255), anchor="lm")
            d.text((pad, ly + lh * 0.72), "Veterinary Nutrition Lead", fill=(190, 200, 210), anchor="lm")


def _draw_logo_watermark(d: ImageDraw.ImageDraw, frame: Image.Image, brand, W: int, H: int) -> None:
    path = Path(brand.logo_path)
    if not path.is_file():
        return
    try:
        logo = Image.open(path).convert("RGBA")
    except Exception:
        return
    # cap the logo to ~16% of the short edge so it never crowds the frame
    target = int(min(W, H) * 0.16)
    scale = target / max(1, max(logo.width, logo.height))
    lw, lh = max(1, int(logo.width * scale)), max(1, int(logo.height * scale))
    logo = logo.resize((lw, lh), Image.LANCZOS)
    if brand.logo_opacity < 1.0:
        alpha = logo.split()[-1].point(lambda a: int(a * max(0.0, min(1.0, brand.logo_opacity))))
        logo.putalpha(alpha)
    # top-right, inset by the same pad as the brand mark
    margin = int(W * 0.055)
    x = W - lw - margin
    y = margin + int(W * 0.01)
    frame.paste(logo, (x, y), logo)


def _draw_cta_card(d: ImageDraw.ImageDraw, brand, W: int, H: int, font_key: str | None, spec: StyleSpec) -> None:
    pad = int(W * 0.055)
    cw = W - 2 * pad
    ch = int(H * 0.16)
    cx0, cy0 = pad, H - ch - int(H * 0.04)
    radius = int(ch * 0.16)
    d.rounded_rectangle([cx0, cy0, cx0 + cw, cy0 + ch], radius=radius, fill=hex2rgb(brand.primary))
    # accent rail on the left edge of the card
    d.rectangle([cx0, cy0, cx0 + int(W * 0.012), cy0 + ch], fill=hex2rgb(brand.accent))

    font = load_font(spec, max(28, int(H * 0.05)), font_key)
    sub_font = load_font(spec, max(20, int(H * 0.032)), font_key)
    if font:
        d.text((cx0 + int(W * 0.05), cy0 + ch * 0.30), brand.cta_text[:40],
               fill=hex2rgb(brand.paper), anchor="lm", font=font)
    if brand.cta_sub and sub_font:
        d.text((cx0 + int(W * 0.05), cy0 + ch * 0.70), brand.cta_sub[:60],
               fill=hex2rgb(brand.accent), anchor="lm", font=sub_font)


# Scene rendering
# --------------------------------------------------------------------------


@dataclass
class SceneAssets:
    bg: Path | None = None
    vo: Path | None = None
    vo_words: list[dict] = None  # type: ignore[assignment]
    vo_duration: float = 0.0


def render_scene_frames(
    scene: Scene,
    assets: SceneAssets,
    project: Project,
    W: int,
    H: int,
    fps: int,
    global_start: float,
    total: float,
    draft: bool = False,
    with_captions: bool = True,
) -> Iterator[np.ndarray]:
    spec = get_style(scene.visual.style or project.style)

    bg_img = None
    if assets.bg and assets.bg.is_file():
        # The pipeline normalises bg to a real image, but stay defensive: a
        # stale/corrupt file here used to kill the whole render with
        # "cannot identify image file" instead of degrading to a flat card.
        try:
            bg_img = Image.open(assets.bg).convert("RGB")
            # upscale slightly so we always have room for zoom/pan
            bg_img = bg_img.resize((int(W * 1.28), int(H * 1.28)), Image.BILINEAR)
        except Exception:
            bg_img = None
    if bg_img is None:
        bg_img = Image.new("RGB", (int(W * 1.28), int(H * 1.28)), hex2rgb(project.brand.ink))

    duration = scene.duration if scene.duration is not None else (assets.vo_duration + 0.22)
    n_frames = max(1, int(round(duration * fps)))

    chunks = chunk_words(assets.vo_words or [])
    rng = np.random.default_rng(scene.visual.seed or 1)

    scale = 0.5 if draft else 1.0
    out_w, out_h = int(W * scale), int(H * scale)

    for i in range(n_frames):
        t = i / fps
        frame = apply_motion(
            bg_img,
            scene.visual.motion,
            t / max(1e-6, duration),
            out_w,
            out_h,
            scene.interrupt,
            shake=(0.6 if scene.interrupt >= 0.85 else 0.0),
        )

        gfx = graphic_lookup(scene.beat)
        if gfx and scene.visual.kind == "graphic":
            g = ImageDraw.Draw(frame)
            gfx(g, out_w, out_h, clamp01(t / max(1e-6, duration)), spec.palette, caption_labels(scene))

        # legibility scrim + kinetic captions -- both ride the word-level
        # VO timings. Skip the whole block when captions are turned off (or
        # there are no words, e.g. a no-voiceover render), so we don't paint
        # a dark band with nothing on it.
        if with_captions and (assets.vo_words or []):
            scrim(frame, out_w, out_h, scene.caption.position)
            draw_caption(frame, chunks, t, spec, scene.caption, out_w, out_h, scene.vo.energy,
                         font_key=project.brand.font_key or None)
        draw_chrome(frame, project, spec, out_w, out_h, global_start + t, total, scene,
                    font_key=project.brand.font_key or None)

        apply_interrupt_mutable(frame, scene, t, out_w, out_h, rng)

        yield np.asarray(frame, dtype=np.uint8)


def graphic_lookup(beat: str):
    from ..assets.image import _GRAPHICS

    return _GRAPHICS.get(beat)


def caption_labels(scene: Scene) -> list[str]:
    from ..parse import keywords

    raw = keywords(scene.vo.text, limit=4)
    return [w.strip().title() for w in raw.split(",") if w.strip()]


_SCRIM_CACHE: dict[tuple, Image.Image] = {}


def scrim_mask(W: int, H: int, position: str) -> Image.Image:
    """Cached luminance mask: transparent -> black -> transparent."""
    key = (W, H, position)
    with _CACHE_LOCK:
        hit = _SCRIM_CACHE.get(key)
        if hit is not None:
            return hit

    if position == "center":
        top, bot = H * 0.36, H * 0.66
    elif position == "top":
        top, bot = H * 0.06, H * 0.36
    else:
        top, bot = H * 0.58, H * 0.92

    ys = np.arange(H, dtype=np.float32)
    f = np.clip((ys - top) / max(1e-6, bot - top), 0.0, 1.0)
    a = (118.0 * np.sin(math.pi * f)).astype(np.uint8)
    m = Image.fromarray(np.repeat(a[:, None], W, axis=1), "L")
    with _CACHE_LOCK:
        if len(_SCRIM_CACHE) < 64:
            _SCRIM_CACHE[key] = m
    return m


def scrim(frame: Image.Image, W: int, H: int, position: str) -> None:
    """Darken behind the caption block so text survives any B-roll."""
    frame.paste((0, 0, 0), (0, 0), scrim_mask(W, H, position))


def apply_interrupt_mutable(frame: Image.Image, scene: Scene, t: float, W: int, H: int, rng: np.random.Generator) -> None:
    """In-place variant of apply_interrupt (frame is reused per iteration)."""
    strength = scene.interrupt
    if strength < 0.35:
        return
    if strength >= 0.6 and t * 30 < 2.2:
        frame.paste(Image.new("RGB", (W, H), (255, 255, 255)), (0, 0))
        return
    if strength >= 0.7:
        if int(t * 30) % 11 == 0 and rng.random() < 0.30:
            bh = max(2, int(H * 0.05))
            y0 = int(rng.uniform(0, max(1, H - bh)))
            frame.paste(Image.new("RGB", (W, bh), (255, 255, 255)), (0, y0))
