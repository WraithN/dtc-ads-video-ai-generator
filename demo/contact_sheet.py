"""Build a portfolio contact sheet from the rendered demo videos.

Pulls evenly spaced frames from each finished mp4 and lays them out on a
single image. This is the artifact you actually attach to a proposal -- a
recruiter will look at one PNG far more readily than four mp4s.

    python demo/contact_sheet.py                 # defaults
    python demo/contact_sheet.py --cols 6 --w 260
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_INPUTS = [
    ("v1_dr_vsl_9x16.mp4", "DR VSL (v1)"),
    ("v2_dr_vsl_faster_intro.mp4", "v2 - faster intro"),
    ("v3_dr_vsl_comic_mechanism.mp4", "v3 - comic mechanism"),
    ("style_comic_9x16.mp4", "Comic"),
    ("style_vector_9x16.mp4", "Vector"),
]


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont | None:
    for p in (
        "C:/Windows/Fonts/ariblk.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/DejaVuSans-Bold.ttf",
    ):
        if Path(p).is_file():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    return None


def probe_duration(path: Path) -> float:
    r = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def frame_at(path: Path, t: float, w: int, h: int) -> Image.Image | None:
    tmp = path.with_name(f"._cs_{path.stem}_{abs(hash((str(path), t))) % 10**8}.png")
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{max(0.0, t):.3f}",
        "-i", str(path),
        "-frames:v", "1",
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
        str(tmp),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not tmp.is_file():
        return None
    img = Image.open(tmp).convert("RGB")
    tmp.unlink(missing_ok=True)
    return img


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default=str(ROOT / "demo/out"))
    ap.add_argument("--cols", type=int, default=5)
    ap.add_argument("--rows", type=int, default=4)
    ap.add_argument("--w", type=int, default=250)
    ap.add_argument("--out", default=str(ROOT / "demo/out/contact_sheet.png"))
    args = ap.parse_args()

    outdir = Path(args.outdir)
    tw, th = args.w, int(args.w * 16 / 9)
    label_h = 26
    pad = 8

    rows: list[tuple[str, list[Image.Image]]] = []
    for name, label in DEFAULT_INPUTS:
        p = outdir / name
        if not p.is_file():
            print(f"  skip (missing): {name}")
            continue
        dur = probe_duration(p)
        if dur <= 0:
            print(f"  skip (unreadable): {name}")
            continue
        # sample across the body of the clip, skipping the very first frame
        ts = [dur * (i + 0.5) / args.cols for i in range(args.cols)]
        frames = [f for f in (frame_at(p, t, tw, th) for t in ts) if f is not None]
        if frames:
            rows.append((f"{label}  ({dur:.1f}s)", frames))
            print(f"  {name}: {len(frames)} frames, {dur:.1f}s")

    if not rows:
        print("no input videos found")
        return 1

    sheet_w = args.cols * (tw + pad) + pad
    sheet_h = len(rows) * (th + label_h + pad) + pad + 54
    sheet = Image.new("RGB", (sheet_w, sheet_h), (17, 18, 22))
    d = ImageDraw.Draw(sheet)

    title_f = _font(22)
    lab_f = _font(13)
    if title_f:
        d.text((pad + 2, 14), "dtc-ads-video-ai-generator - one script, four styles, three conversation edits",
               fill=(238, 240, 246), font=title_f)

    y = 52
    for label, frames in rows:
        if lab_f:
            d.text((pad + 2, y), label, fill=(150, 156, 172), font=lab_f)
        y += label_h
        x = pad
        for f in frames:
            sheet.paste(f, (x, y))
            d.rectangle([x, y, x + tw, y + th], outline=(44, 46, 56), width=1)
            x += tw + pad
        y += th + pad

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(f"\nwrote {out}  ({sheet_w}x{sheet_h})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
