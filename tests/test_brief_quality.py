"""The script writer must know what the product IS.

Three separate failures used to hide behind "the copy reads generic":

1. The LLM path failed silently (``except: pass``) and the user got the
   offline templates without being told.
2. The offline templates were one generic "problem-solving gadget" script, so
   a bottle of baijiu got "每天都在被同一个问题折磨？".
3. The brief had no field for what the product physically is, so the model
   had nothing to tell a baijiu apart from a sleep lamp.

The rules locked in here: the fallback must be reported, the fallback must
match the product family, and the traits field must reach both the script
writer and the storyboard's visual prompts.
"""

import pytest

from avb.brief import (
    _detect_family,
    _generate_heuristic,
    _split_traits,
    _strip_bullet,
    generate_scripts_detailed,
)
from avb.parse import _product_anchor, parse_script


# --- traits parsing -------------------------------------------------------

def test_strip_bullet_keeps_leading_digits_that_are_part_of_the_fact():
    """`lstrip("0123456789")` ate the numbers off real product facts."""
    assert _strip_bullet("53度酱香") == "53度酱香"
    assert _strip_bullet("500ml 大容量") == "500ml 大容量"
    assert _strip_bullet("1. 53度酱香") == "53度酱香"
    assert _strip_bullet("• 无线充电") == "无线充电"
    assert _strip_bullet("- 纯棉") == "纯棉"


def test_split_traits_handles_every_shape_users_type():
    assert _split_traits("53度酱香\n赤水河核心产区") == ["53度酱香", "赤水河核心产区"]
    assert _split_traits("纯粮固态, 五年陈酿") == ["纯粮固态", "五年陈酿"]
    assert _split_traits("1. 53度\n2. 五年陈酿") == ["53度", "五年陈酿"]


# --- product family detection --------------------------------------------

@pytest.mark.parametrize(
    "category,traits,product,expected",
    [
        ("酒水", "53度酱香", "茅台", "酒水"),
        (None, "酱香型白酒", "飞天茅台", "酒水"),
        ("3C数码", "蓝牙5.4", "无线耳机", "3C"),
        ("美妆", "烟酰胺", "精华液", "美妆"),
        ("食品", "非油炸", "坚果", "食品"),
        (None, None, "纯棉T恤", "服饰"),
        (None, None, None, "通用"),
    ],
)
def test_detect_family(category, traits, product, expected):
    assert _detect_family(category, traits, product) == expected


def test_baijiu_never_gets_the_gadget_template():
    """The regression: a bottle of baijiu must not be told it has a problem
    to solve. That line belonged to a sleep lamp."""
    d = _generate_heuristic(
        "茅台", [], "dr_vsl", language="zh",
        traits="53度酱香\n赤水河核心产区\n五年陈酿",
        category="酒水",
    )
    text = "\n".join(s.narration for s in d)
    assert "每天都在被同一个问题折磨" not in text
    assert "大多方案只是掩耳盗铃" not in text
    assert "这正是 茅台 存在的理由" in text


def test_heuristic_uses_the_user_specific_traits():
    d = _generate_heuristic(
        "茅台", [], "dr_vsl", language="zh",
        traits="53度酱香\n赤水河核心产区\n五年陈酿",
        category="酒水",
    )
    text = "\n".join(s.narration for s in d)
    assert "53度酱香" in text
    # No English filler leaking into a Chinese script.
    assert "its key benefit" not in text


def test_english_fallback_has_no_chinese():
    d = _generate_heuristic("Lumo lamp", ["sleeps faster"], "dr_vsl", language="en")
    text = "\n".join(s.narration for s in d)
    assert "它的核心卖点" not in text
    assert "every" in text.lower()


# --- the silent fallback must be reported ---------------------------------

def test_fallback_reason_is_reported_when_llm_is_off():
    drafts, reason = generate_scripts_detailed(
        product="茅台", selling_points=[], use_llm=False, language="zh",
    )
    assert len(drafts) == 4
    assert reason, "the user must be told the model was not used"


def test_fallback_reason_is_reported_when_the_model_fails(monkeypatch):
    import avb.brief as brief

    def boom(_brief):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(brief, "_generate_llm", boom)
    drafts, reason = generate_scripts_detailed(
        product="茅台", selling_points=[], use_llm=True, language="zh",
    )
    assert len(drafts) == 4
    assert "model exploded" in reason


def test_no_reason_means_the_model_wrote_them(monkeypatch):
    import avb.brief as brief

    monkeypatch.setattr(
        brief, "_generate_llm",
        lambda _b: [brief.ScriptDraft(type=t, title=t, narration="x") for t in brief.SCRIPT_TYPE_KEYS],
    )
    drafts, reason = generate_scripts_detailed(product="x", use_llm=True)
    assert reason == ""
    assert len(drafts) == 4


# --- product anchor in the storyboard prompts ----------------------------

def test_product_anchor_leads_every_visual_prompt():
    p = parse_script(
        "开瓶那一下，酱香就出来了。点下方链接，今天试试。",
        style="dr_vsl",
        product="茅台",
        product_traits="53度酱香\n赤水河核心产区\n红标陶瓷瓶",
    )
    assert p.scenes
    for s in p.scenes:
        assert s.visual.prompt.startswith("Subject on screen:")
        assert "茅台" in s.visual.prompt
        assert "53度酱香" in s.visual.prompt


def test_product_anchor_is_empty_without_traits():
    assert _product_anchor("", "") == ""
    assert _product_anchor("茅台", "") == "茅台"


def test_anchor_deduplicates_and_caps():
    a = _product_anchor("茅台", "53度酱香, 53度酱香, 五年陈酿, 五年陈酿, 核心产区, 陶瓷瓶, 红标")
    assert a.count("53度酱香") == 1
    assert len(a) <= 220
