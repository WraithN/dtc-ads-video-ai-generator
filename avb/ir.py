"""SceneGraph IR -- the single source of truth for a video project.

Design rule that drives everything else in this codebase:

    The IR is the ONLY thing that is ever edited.

Not the pixels, not the mp4, not the audio. Pixels are a pure function
of the IR (plus a cache of generated assets). This is what makes:
  - conversational editing possible (patch JSON, not video)
  - partial re-rendering possible (hash-diff to find invalidated scenes)
  - versioning/rollback possible (the IR is tiny, so snapshots are free)

If you find yourself wanting to edit a rendered file, you are in the
wrong layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from typing import Any, Literal

from .hashing import short_hash

# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------

Aspect = Literal["9:16", "4:5", "1:1", "16:9"]

# Direct-response beats. The parser tags each scene with one of these;
# styles and visual selection both key off it.
Beat = Literal["hook", "problem", "agitate", "mechanism", "proof", "offer", "cta"]

VisualKind = Literal["ai_clip", "stock", "still", "graphic", "solid"]
Motion = Literal["static", "zoom_in", "zoom_out", "pan_left", "pan_right", "punch"]
CaptionMode = Literal["kinetic", "karaoke", "static", "none"]
Transition = Literal["cut", "flash", "whip", "zoom_blur", "dissolve"]

ASPECTS: dict[Aspect, tuple[int, int]] = {
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
}

# Resolution presets scale the ASPECTS baseline (1080p-class). The composer
# renders at project.width x project.height, so picking 720p here literally
# renders smaller frames rather than downscaling after the fact.
RES_SCALE: dict[str, float] = {
    "540p": 0.5,
    "720p": 2 / 3,
    "1080p": 1.0,
    "1440p": 1.5,
    "4k": 2.0,
}

# --------------------------------------------------------------------------
# Nodes
# --------------------------------------------------------------------------


@dataclass
class Visual:
    """What is on screen for this scene."""

    prompt: str = ""
    kind: VisualKind = "still"
    motion: Motion = "zoom_in"
    style: str = "dr_vsl"
    seed: int = 0
    palette: list[str] | None = None
    # --- cinematography (authored by the planner, editable in the UI) -----
    # These three are the "shot description" the user asked for: what the
    # camera does (shot), where it sits (angle), and what is in frame
    # (elements). `prompt` is the composed image/video-generation string;
    # these are kept separately so the UI can show and edit them on their
    # own and recompose `prompt` without losing the structure.
    shot: str = ""  # e.g. "close-up", "wide tracking", "over-the-shoulder"
    angle: str = ""  # e.g. "eye-level", "low-angle", "top-down", "dutch"
    elements: list[str] | None = None  # on-screen subjects / props

    def semantic(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "kind": self.kind,
            "motion": self.motion,
            "style": self.style,
            "seed": self.seed,
            "palette": self.palette,
            "shot": self.shot,
            "angle": self.angle,
            "elements": self.elements,
        }


@dataclass
class Voiceover:
    """Spoken line for this scene."""

    text: str = ""
    voice: str = "adam"
    speed: float = 1.0
    energy: float = 1.0  # 0..1 -- drives both TTS prosody and caption aggressiveness

    def semantic(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "voice": self.voice,
            "speed": round(self.speed, 4),
            "energy": round(self.energy, 4),
        }


@dataclass
class CaptionStyle:
    mode: CaptionMode = "kinetic"
    position: Literal["bottom", "center", "top"] = "bottom"
    highlight: str = "#FFE500"
    scale_pop: float = 0.18

    def semantic(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "position": self.position,
            "highlight": self.highlight,
            "scale_pop": round(self.scale_pop, 4),
        }


@dataclass
class Sfx:
    name: str = "whoosh"
    at: float = 0.0  # seconds from scene start

    def semantic(self) -> dict[str, Any]:
        return {"name": self.name, "at": round(self.at, 4)}


@dataclass
class Scene:
    """One shot. The atomic unit of caching and of conversational editing."""

    id: str = ""
    index: int = 0
    beat: Beat = "problem"
    vo: Voiceover = field(default_factory=Voiceover)
    visual: Visual = field(default_factory=Visual)
    caption: CaptionStyle = field(default_factory=CaptionStyle)
    sfx: list[Sfx] = field(default_factory=list)
    transition: Transition = "cut"
    interrupt: float = 0.0  # 0..1 pattern-interrupt strength
    duration: float | None = None  # explicit override; else derived from VO

    # -- hashing -----------------------------------------------------------
    def semantic(self) -> dict[str, Any]:
        """The fields whose change forces a re-generate.

        Note `duration` is deliberately excluded: it is normally derived
        from the VO, so including it would create a false invalidation
        loop. Explicit overrides are handled by the patch layer.
        """
        return {
            "beat": self.beat,
            "vo": self.vo.semantic(),
            "visual": self.visual.semantic(),
            "caption": self.caption.semantic(),
            "sfx": [s.semantic() for s in self.sfx],
            "transition": self.transition,
            "interrupt": round(self.interrupt, 4),
        }

    def content_hash(self) -> str:
        return short_hash(self.semantic())

    def vo_hash(self) -> str:
        return short_hash(self.vo.semantic())

    def visual_hash(self) -> str:
        return short_hash(self.visual.semantic())


@dataclass
class Music:
    track: str = "pulse_drive"
    volume: float = 0.20
    duck_under_vo: bool = True
    bpm: int = 128


@dataclass
class BrandKit:
    """Brand constraints baked into the IR so every render of the same
    product looks like one coherent campaign.

    This is the "brand constraint layer": instead of hoping batch renders
    stay on-style, the colours, font, logo watermark and fixed CTA card are
    stored on the Project and enforced by the compositor. Empty / None fields
    mean "inherit the style default", so a minimal brand kit is a no-op.
    """

    name: str = "BRAND"
    primary: str = "#FF3B30"
    accent: str = "#FFE500"
    ink: str = "#0B0B0F"
    paper: str = "#FFFFFF"  # light surface (cards, CTA subline text)
    logo_path: str = ""  # absolute path to a logo image; burned as a watermark
    logo_opacity: float = 0.85
    font_key: str = ""  # brand font (system font key); "" => style default
    cta_text: str = ""  # fixed CTA card headline (e.g. "Shop now — 20% off")
    cta_sub: str = ""  # fixed CTA card subline
    watermark: bool = True  # show the logo watermark
    # unified caption styling -- empty / None means "inherit per-scene style"
    caption_mode: CaptionMode = "kinetic"
    caption_position: Literal["bottom", "center", "top"] = "bottom"
    caption_highlight: str = ""
    caption_scale_pop: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "primary": self.primary,
            "accent": self.accent,
            "ink": self.ink,
            "paper": self.paper,
            "logo_path": self.logo_path,
            "logo_opacity": round(self.logo_opacity, 4),
            "font_key": self.font_key,
            "cta_text": self.cta_text,
            "cta_sub": self.cta_sub,
            "watermark": self.watermark,
            "caption_mode": self.caption_mode,
            "caption_position": self.caption_position,
            "caption_highlight": self.caption_highlight,
            "caption_scale_pop": self.caption_scale_pop,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "BrandKit":
        return BrandKit(**_pick(BrandKit, d or {}))


@dataclass
class Project:
    """A whole ad. Serializable, diffable, versionable."""

    id: str = "proj"
    name: str = "untitled"
    script: str = ""
    aspect: Aspect = "9:16"
    resolution: str = "1080p"
    fps: int = 30
    style: str = "dr_vsl"  # project default; scenes may override
    scenes: list[Scene] = field(default_factory=list)
    music: Music = field(default_factory=Music)
    brand: BrandKit = field(default_factory=BrandKit)

    # -- geometry ----------------------------------------------------------
    @property
    def width(self) -> int:
        return int(ASPECTS[self.aspect][0] * RES_SCALE.get(self.resolution, 1.0))

    @property
    def height(self) -> int:
        return int(ASPECTS[self.aspect][1] * RES_SCALE.get(self.resolution, 1.0))

    # -- serialization -----------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "script": self.script,
            "aspect": self.aspect,
            "fps": self.fps,
            "style": self.style,
            "resolution": self.resolution,
            "brand": self.brand.to_dict(),
            "music": {
                "track": self.music.track,
                "volume": self.music.volume,
                "duck_under_vo": self.music.duck_under_vo,
                "bpm": self.music.bpm,
            },
            "scenes": [
                {
                    "id": s.id,
                    "index": s.index,
                    "beat": s.beat,
                    "transition": s.transition,
                    "interrupt": s.interrupt,
                    "duration": s.duration,
                    "vo": s.vo.semantic(),
                    "visual": s.visual.semantic(),
                    "caption": s.caption.semantic(),
                    "sfx": [x.semantic() for x in s.sfx],
                }
                for s in self.scenes
            ],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "Project":
        p = Project(
            id=d.get("id", "proj"),
            name=d.get("name", "untitled"),
            script=d.get("script", ""),
            aspect=d.get("aspect", "9:16"),
            fps=d.get("fps", 30),
            style=d.get("style", "dr_vsl"),
            resolution=d.get("resolution", "1080p"),
        )
        # Tolerate schema drift: a Project saved by a newer build may carry
        # fields the current dataclasses do not yet know about. Pick only the
        # fields each node declares, so an unknown key is ignored rather than
        # raising `TypeError: unexpected keyword argument` on resume. This is
        # what keeps the project_json column in the history DB forward-safe.
        p.brand = BrandKit.from_dict(d.get("brand") or {})
        p.music = Music(**_pick(Music, d.get("music") or {}))

        scenes: list[Scene] = []
        for i, s in enumerate(d.get("scenes", [])):
            vo = Voiceover(**_pick(Voiceover, s.get("vo") or {}))
            vis_raw = s.get("visual") or {}
            vis = Visual(**_pick(Visual, vis_raw))
            cap = CaptionStyle(**_pick(CaptionStyle, s.get("caption") or {}))
            scenes.append(
                Scene(
                    id=s.get("id", f"s{i+1:03d}"),
                    index=s.get("index", i),
                    beat=s.get("beat", "problem"),
                    vo=vo,
                    visual=vis,
                    caption=cap,
                    sfx=[Sfx(**_pick(Sfx, x)) for x in (s.get("sfx") or [])],
                    transition=s.get("transition", "cut"),
                    interrupt=s.get("interrupt", 0.0),
                    duration=s.get("duration"),
                )
            )
        p.scenes = scenes
        return p

    @staticmethod
    def from_json(text: str) -> "Project":
        return Project.from_dict(json.loads(text))


def _pick(cls: type, d: dict[str, Any]) -> dict[str, Any]:
    """Select only the fields declared by `cls` from `d`.

    Used by Project.from_dict so deserialization is resilient to schema drift:
    unknown keys are dropped (forward-compat) and missing keys fall back to the
    dataclass defaults. Empty/None input yields an empty dict (all defaults).
    """
    if not d:
        return {}
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}

    # -- diffing -----------------------------------------------------------
    def scene_map(self) -> dict[str, Scene]:
        return {s.id: s for s in self.scenes}

    def diff(self, other: "Project") -> dict[str, Any]:
        """Compare two IR versions and report exactly what must be rebuilt."""
        a, b = self.scene_map(), other.scene_map()
        added = [sid for sid in b if sid not in a]
        removed = [sid for sid in a if sid not in b]

        vo_changed: list[str] = []
        visual_changed: list[str] = []
        meta_changed: list[str] = []

        for sid in b:
            if sid not in a:
                continue
            sa, sb = a[sid], b[sid]
            if sa.vo_hash() != sb.vo_hash():
                vo_changed.append(sid)
            if sa.visual_hash() != sb.visual_hash():
                visual_changed.append(sid)
            if (
                sa.transition != sb.transition
                or round(sa.interrupt, 4) != round(sb.interrupt, 4)
                or sa.duration != sb.duration
                or sa.caption.semantic() != sb.caption.semantic()
            ):
                meta_changed.append(sid)

        invalidated = sorted(set(vo_changed) | set(visual_changed) | set(meta_changed))
        return {
            "added": sorted(added),
            "removed": sorted(removed),
            "vo_changed": sorted(vo_changed),
            "visual_changed": sorted(visual_changed),
            "meta_changed": sorted(meta_changed),
            "invalidated": invalidated,
            "cached": sorted(set(b) - set(invalidated)),
        }
