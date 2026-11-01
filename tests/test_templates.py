"""Tests for the DTC ad-template system (P1).

The template layer is a pure-IR transform, so these tests never touch the
network or ffmpeg -- they assert the beat skeleton is assigned deterministically
and the visual prompts are re-composed to match it.
"""

from __future__ import annotations

from avb.parse import parse_script
from avb.templates import (
    TEMPLATES,
    _assign_beats,
    apply_template_to_project,
    get_template,
    template_keys,
)

_SCRIPT = (
    "Tired of the same old routine every morning.\n"
    "Most solutions just mask it and you waste money.\n"
    "This works by a simple mechanism that actually helps.\n"
    "Studies show results within weeks.\n"
    "Try it today and save big.\n"
    "Click below to get yours."
)


def test_registry_has_expected_templates():
    keys = set(TEMPLATES)
    assert "flexible" in keys
    for k in ("pain_hook", "ugc_talk", "product_hero", "comparison", "talking_expert"):
        assert k in keys, f"missing template {k}"
    # menu payload is serialisable and carries the recommended style
    menu = template_keys()
    assert any(m["key"] == "pain_hook" and m["recommended_style"] for m in menu)


def test_assign_beats_pins_hook_and_cta_and_distributes_middle():
    beats = get_template("pain_hook").beats  # 7 beats
    # 6 scenes -> hook, then 4 middles across the inner 5, then cta
    out = _assign_beats(6, beats)
    assert out[0] == "hook"
    assert out[-1] == "cta"
    assert len(out) == 6
    # inner beats all appear at least once across the middle
    assert set(out[1:-1]) == {"problem", "agitate", "mechanism", "proof"}


def test_assign_beats_cycles_when_more_scenes_than_beats():
    beats = get_template("pain_hook").beats  # 7
    out = _assign_beats(10, beats)
    assert out[0] == "hook"
    assert out[-1] == "cta"
    assert len(out) == 10
    # every inner beat must appear at least once
    inner = {"problem", "agitate", "mechanism", "proof", "offer"}
    assert inner.issubset(set(out))


def test_assign_beats_edge_cases():
    beats = get_template("pain_hook").beats
    assert _assign_beats(1, beats) == ["hook"]
    assert _assign_beats(2, beats) == ["hook", "cta"]
    # 3 scenes: hook, first inner, cta
    assert _assign_beats(3, beats) == ["hook", "problem", "cta"]
    # empty template (flexible) -> no beats assigned
    assert _assign_beats(5, []) == []


def test_parse_script_template_is_deterministic_and_structured():
    # Flexible keeps the classifier's free beats; a template pins a skeleton.
    free = parse_script(_SCRIPT, template="flexible", style="dr_vsl")
    pinned = parse_script(_SCRIPT, template="pain_hook", style="dr_vsl")
    assert len(pinned.scenes) == len(free.scenes) == 6
    beats = [s.beat for s in pinned.scenes]
    assert beats[0] == "hook"
    assert beats[-1] == "cta"
    assert beats == ["hook", "problem", "agitate", "mechanism", "proof", "cta"]
    # deterministic: same input -> identical skeleton
    again = parse_script(_SCRIPT, template="pain_hook", style="dr_vsl")
    assert [s.beat for s in again.scenes] == beats
    # template differs from the free (classifier) assignment
    assert [s.beat for s in free.scenes] != beats


def test_parse_script_template_recomposes_prompt_with_shot():
    proj = parse_script(_SCRIPT, template="product_hero", style="cinematic")
    # product_hero prescribes "macro insert" for mechanism -> the recomposed
    # prompt must carry that framing language.
    mech = next(s for s in proj.scenes if s.beat == "mechanism")
    assert "macro" in mech.visual.prompt.lower()
    # and the scene motion/kind are stamped from the template
    assert mech.visual.kind == "still"


def test_apply_template_to_project_is_pure_ir_transform():
    # applying after the fact (e.g. via an edit) yields the same structure
    proj = parse_script(_SCRIPT, template="flexible", style="dr_vsl")
    apply_template_to_project(proj, "comparison")
    beats = [s.beat for s in proj.scenes]
    assert beats[0] == "problem"  # comparison skeleton starts on problem
    assert beats[-1] == "cta"
    assert len(proj.scenes) == 6  # never drops a scene


def test_parse_script_llm_falls_back_to_templated_heuristic():
    # parse_script_llm falls back to parse_script on any LLM failure, and that
    # heuristic path applies the requested template deterministically. We test
    # the fallback target directly so the unit test never depends on network
    # access or a configured model.
    proj = parse_script(_SCRIPT, template="ugc_talk", style="ugc")
    beats = [s.beat for s in proj.scenes]
    assert beats[0] == "hook"
    assert beats[-1] == "cta"
    # ugc_talk marks caption position center on the hook beat
    assert proj.scenes[0].caption.position == "center"
