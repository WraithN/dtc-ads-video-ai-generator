"""Direct-response ad templates.

DTC ads win or lose on structure, not on free-form generation. A *template*
is a fixed narrative skeleton -- the beat sequence plus per-beat
cinematography defaults (shot / angle / motion / visual kind / caption
position). Applying one reassigns scene beats deterministically and stamps
the structural defaults onto the IR, so the same product always comes out
with the same shape whether the parser ran through the LLM or the heuristic
path.

This is deliberately IR-only: it never touches pixels. It composes with
everything downstream (content-addressed cache, conversational edit, partial
re-render) because it just rewrites ``Scene.beat`` / ``Visual`` fields before
synthesis.

``flexible`` is an explicit no-op so existing free-generation behaviour is
preserved when the user picks it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ir import Beat

# Beats recognised by the parser.
_VALID_BEATS = {"hook", "problem", "agitate", "mechanism", "proof", "offer", "cta"}


@dataclass
class BeatCinematography:
    """Structural defaults for one beat within a template.

    An empty field means "leave whatever the parser already assigned" -- a
    template only pins the things it cares about, so it never fights the
    product anchor or the user's chosen visual style.
    """

    shot: str = ""
    angle: str = ""
    motion: str = ""
    kind: str = ""  # visual kind override (ai_clip | stock | still | graphic | solid)
    caption_position: str = ""  # bottom | center | top
    interrupt: float | None = None


@dataclass
class TemplateSpec:
    key: str
    label: str
    description: str
    beats: list[Beat]  # canonical narrative skeleton (hook-first, cta-last)
    recommended_style: str = "dr_vsl"
    per_beat: dict[str, BeatCinematography] = field(default_factory=dict)

    def cinematography(self, beat: str) -> BeatCinematography:
        return self.per_beat.get(beat, BeatCinematography())


# --------------------------------------------------------------------------
# Registry
#
# Five fixed DTC structures. Each beats list is ordered hook-first / cta-last.
# The per-beat cinematography is what makes the structure *look* the part:
# a UGC talking-head reads as selfie close-ups, a comparison read as split
# screens, a product-hero as macro inserts. The recommended_style is a hint
# the UI can prefill, not a hard override of the user's look choice.
# --------------------------------------------------------------------------

TEMPLATES: dict[str, TemplateSpec] = {
    "flexible": TemplateSpec(
        key="flexible",
        label="灵活（自由结构）",
        description="不套模板，由脚本或模型自由决定分镜结构。",
        beats=[],
        recommended_style="dr_vsl",
    ),
    "pain_hook": TemplateSpec(
        key="pain_hook",
        label="痛点钩子",
        description="强钩子开场 → 痛点 → 放大 → 机制 → 证明 → 优惠 → 行动。",
        beats=["hook", "problem", "agitate", "mechanism", "proof", "offer", "cta"],
        recommended_style="dr_vsl",
        per_beat={
            "hook": BeatCinematography(shot="extreme close-up", angle="eye-level, slight dutch tilt", motion="punch", caption_position="bottom", interrupt=0.85),
            "problem": BeatCinematography(shot="medium shot", angle="eye-level", motion="zoom_in", kind="stock"),
            "agitate": BeatCinematography(shot="handheld tracking", angle="low-angle", motion="pan_left", kind="stock"),
            "mechanism": BeatCinematography(shot="macro insert", angle="top-down macro", motion="zoom_in", kind="graphic"),
            "proof": BeatCinematography(shot="graphic overlay", angle="straight-on", motion="static", kind="graphic"),
            "offer": BeatCinematography(shot="product hero shot", angle="slight low-angle", motion="zoom_in", kind="still"),
            "cta": BeatCinematography(shot="wide hero reveal", angle="eye-level", motion="punch", caption_position="bottom", interrupt=0.9),
        },
    ),
    "ugc_talk": TemplateSpec(
        key="ugc_talk",
        label="UGC 口播",
        description="真人自拍口播：钩子 → 痛点 → 放大 → 机制 → 证明 → 行动。",
        beats=["hook", "problem", "agitate", "mechanism", "proof", "cta"],
        recommended_style="ugc",
        per_beat={
            "hook": BeatCinematography(shot="selfie close-up", angle="eye-level", motion="static", kind="ai_clip", caption_position="center"),
            "problem": BeatCinematography(shot="selfie medium", angle="eye-level", motion="zoom_in", kind="ai_clip", caption_position="center"),
            "agitate": BeatCinematography(shot="handheld selfie", angle="eye-level", motion="pan_right", kind="ai_clip", caption_position="center"),
            "mechanism": BeatCinematography(shot="selfie demo", angle="eye-level", motion="static", kind="ai_clip", caption_position="center"),
            "proof": BeatCinematography(shot="selfie before/after", angle="eye-level", motion="zoom_in", kind="ai_clip", caption_position="center"),
            "cta": BeatCinematography(shot="selfie call to action", angle="eye-level", motion="static", kind="ai_clip", caption_position="center"),
        },
    ),
    "product_hero": TemplateSpec(
        key="product_hero",
        label="产品特写",
        description="以产品特写为主：钩子特写 → 痛点 → 微距机制 → 细节证明 → 主图 → 行动。",
        beats=["hook", "problem", "mechanism", "proof", "offer", "cta"],
        recommended_style="cinematic",
        per_beat={
            "hook": BeatCinematography(shot="extreme close-up", angle="eye-level", motion="zoom_in", kind="still"),
            "problem": BeatCinematography(shot="close-up detail", angle="eye-level", motion="zoom_in", kind="still"),
            "mechanism": BeatCinematography(shot="macro insert", angle="top-down macro", motion="zoom_in", kind="still"),
            "proof": BeatCinematography(shot="detail hero", angle="low-angle", motion="static", kind="still"),
            "offer": BeatCinematography(shot="product hero shot", angle="slight low-angle", motion="zoom_in", kind="still"),
            "cta": BeatCinematography(shot="wide hero reveal", angle="eye-level", motion="zoom_out", kind="still"),
        },
    ),
    "comparison": TemplateSpec(
        key="comparison",
        label="对比类",
        description="前后对比：使用前痛点 → 放大 → 使用后机制 → 使用后证明 → 优惠 → 行动。",
        beats=["problem", "agitate", "mechanism", "proof", "offer", "cta"],
        recommended_style="dr_vsl",
        per_beat={
            "problem": BeatCinematography(shot="before split screen", angle="eye-level", motion="static", kind="still"),
            "agitate": BeatCinematography(shot="before close-up", angle="low-angle", motion="pan_left", kind="stock"),
            "mechanism": BeatCinematography(shot="after macro", angle="top-down macro", motion="zoom_in", kind="graphic"),
            "proof": BeatCinematography(shot="after split screen", angle="straight-on", motion="static", kind="still"),
            "offer": BeatCinematography(shot="product hero shot", angle="slight low-angle", motion="zoom_in", kind="still"),
            "cta": BeatCinematography(shot="wide hero reveal", angle="eye-level", motion="punch", interrupt=0.9),
        },
    ),
    "talking_expert": TemplateSpec(
        key="talking_expert",
        label="专家背书",
        description="专家口播：钩子 → 痛点 → 机制讲解 → 证据 → 行动。",
        beats=["hook", "problem", "mechanism", "proof", "cta"],
        recommended_style="talking_head",
        per_beat={
            "hook": BeatCinematography(shot="studio close-up", angle="eye-level", motion="static", kind="ai_clip", caption_position="bottom"),
            "problem": BeatCinematography(shot="studio medium", angle="eye-level", motion="static", kind="ai_clip"),
            "mechanism": BeatCinematography(shot="studio demo", angle="eye-level", motion="static", kind="graphic"),
            "proof": BeatCinematography(shot="studio graphic", angle="eye-level", motion="static", kind="graphic"),
            "cta": BeatCinematography(shot="studio call to action", angle="eye-level", motion="static", kind="ai_clip"),
        },
    ),
}

DEFAULT_TEMPLATE = "pain_hook"


def get_template(key: str) -> TemplateSpec:
    return TEMPLATES.get(key, TEMPLATES[DEFAULT_TEMPLATE])


def template_keys() -> list[dict]:
    """The menu the UI renders: key + label + description + suggested look."""
    return [
        {
            "key": t.key,
            "label": t.label,
            "description": t.description,
            "recommended_style": t.recommended_style,
        }
        for t in TEMPLATES.values()
    ]


def _assign_beats(n_scenes: int, beats: list[Beat]) -> list[Beat]:
    """Map n scenes onto a template's beat sequence deterministically.

    Scene 0 -> beats[0] (the hook), the last scene -> beats[-1] (the CTA), and
    the middle scenes are distributed across the inner beats proportionally.
    Both fewer and more scenes than the template has beats are handled: the
    inner beats simply cycle.
    """
    if not beats or n_scenes <= 0:
        return []
    if n_scenes == 1:
        return [beats[0]]
    if n_scenes == 2:
        return [beats[0], beats[-1]]
    first, *middle, last = beats
    inner = middle if middle else [first]
    out: list[Beat] = [first]
    for i in range(n_scenes - 2):
        out.append(inner[i % len(inner)])
    out.append(last)
    return out


def recompose_prompt(text: str, beat: Beat, style: str, shot: str, angle: str,
                     elements, anchor: str) -> str:
    """Rebuild a scene's visual prompt from its (possibly template-overridden)
    cinematography, re-prepending the product anchor.

    Kept here so both ``parse_script`` and ``_project_from_shots`` share the
    exact same recomposition and anchor rule.
    """
    from .parse import compose_visual_prompt

    base = compose_visual_prompt(text, beat, style, shot, angle, elements)
    if anchor and not base.lower().startswith("subject on screen"):
        base = f"Subject on screen: {anchor}. {base}"
    return base


def apply_template_to_project(project, template_key: str, anchor: str = "") -> "object":
    """Rewrite scene beats + structural defaults from a template.

    Pure IR transform: deterministic, no network, no pixels. Returns the same
    (mutated) project so it can be chained. ``flexible`` is a no-op, so a user
    who wants the old free-generation behaviour gets exactly that.
    """
    spec = get_template(template_key)
    if spec.key == "flexible" or not spec.beats:
        return project
    beats = _assign_beats(len(project.scenes), spec.beats)
    style = project.style
    for scene, beat in zip(project.scenes, beats):
        scene.beat = beat
        bc = spec.cinematography(beat)
        if bc.shot:
            scene.visual.shot = bc.shot
        if bc.angle:
            scene.visual.angle = bc.angle
        if bc.motion:
            scene.visual.motion = bc.motion
        if bc.kind:
            scene.visual.kind = bc.kind
        if bc.caption_position:
            scene.caption.position = bc.caption_position  # type: ignore[assignment]
        if bc.interrupt is not None:
            scene.interrupt = bc.interrupt
        scene.visual.prompt = recompose_prompt(
            scene.vo.text, beat, style, scene.visual.shot, scene.visual.angle,
            scene.visual.elements, anchor,
        )
    return project
