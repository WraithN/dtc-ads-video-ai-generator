"""The storyboard must never eat the script.

Regression cover for the bug where a short target runtime silently truncated
the tail of the ad copy: `shots[:max_scenes]` dropped the last scenes, so the
CTA — the part that sells the click — never made it into the storyboard.

The rule now: the scene budget is a *cost* knob. Overflowing scenes are
MERGED (every spoken word survives), and if the model still drops a line the
script text is stitched back in.
"""

import pytest

from avb.parse import (
    _ensure_full_coverage,
    _merge_shots_to_cap,
    _norm_text,
    _scene_cap,
    parse_script,
)

SCRIPT = """不是等什么大日子，
兄弟局的酒你未必舍得开。
可这瓶 Moutai，
愣是塞进了你日日盯着的酒柜。
开一次，兄弟少一次；
不开，又对不起这场面。
直到那天他掏出这瓶——
封膜还在，柜门一开，
整个屋子都静了。
不是酒变了，是你变了。
点下方链接，别让这瓶酒等下一个敢开它的人。"""


def _scene_cap_never_below_three():
    assert _scene_cap(5, 20) == 3
    assert _scene_cap(30, 8) == 8
    assert _scene_cap(30, 40) == 8  # 30s / 4s per scene
    assert _scene_cap(None, 12) == 12
    assert _scene_cap(0, 12) == 12


def test_merge_keeps_every_word():
    shots = [{"text": f"line {i}", "beat": "hook"} for i in range(9)]
    merged = _merge_shots_to_cap(shots, 4)
    assert len(merged) == 4
    # Every word survives; the merge only inserts a separator between them.
    assert _norm_text(" ".join(m["text"] for m in merged)) == _norm_text(
        " ".join(s["text"] for s in shots)
    )


def test_heuristic_parser_keeps_the_cta_under_a_short_target():
    """A 20s target must not throw the last lines of the script away."""
    p = parse_script(SCRIPT, style="dr_vsl", target_seconds=20)
    assert p.scenes
    spoken = _norm_text(" ".join(s.vo.text for s in p.scenes))
    # The final CTA has to survive the scene budget.
    assert _norm_text("点下方链接") in spoken
    assert _norm_text("不是酒变了，是你变了") in spoken
    assert _norm_text("整个屋子都静了") in spoken


def test_heuristic_parser_never_drops_a_line():
    p = parse_script(SCRIPT, style="dr_vsl", target_seconds=45)
    spoken = _norm_text(" ".join(s.vo.text for s in p.scenes))
    for line in SCRIPT.splitlines():
        assert _norm_text(line) in spoken, f"lost line: {line}"


def test_coverage_net_stitches_back_a_dropped_line():
    """The safety net: a model that drops the CTA gets it put back."""
    shots = [
        {"text": "不是等什么大日子，", "beat": "hook"},
        {"text": "兄弟局的酒你未必舍得开。", "beat": "hook"},
    ]
    fixed = _ensure_full_coverage(SCRIPT, shots)
    assert len(fixed) > len(shots)
    spoken = _norm_text(" ".join(s["text"] for s in fixed))
    assert _norm_text("点下方链接") in spoken


def test_coverage_net_is_a_noop_when_nothing_is_missing():
    shots = [{"text": line, "beat": "hook"} for line in SCRIPT.splitlines()]
    assert _ensure_full_coverage(SCRIPT, shots) == shots


@pytest.mark.parametrize("target", [5, 20, 30, 60])
def test_no_script_line_is_ever_dropped(target):
    p = parse_script(SCRIPT, style="dr_vsl", target_seconds=target)
    spoken = _norm_text(" ".join(s.vo.text for s in p.scenes))
    for line in SCRIPT.splitlines():
        assert _norm_text(line) in spoken, f"lost line at target={target}: {line}"


def test_heuristic_parser_accepts_the_llm_kwargs():
    """The LLM parser forwards the same kwargs when it falls back.

    `script_language` used to blow up the fallback with
    "parse_script() got an unexpected keyword argument" -- which turned a
    transient model outage into a failed job instead of a degraded storyboard.
    """
    p = parse_script(
        "Hook line here. Body line explaining the benefit.",
        style="dr_vsl",
        target_seconds=20,
        script_language="zh",
    )
    assert p.scenes
