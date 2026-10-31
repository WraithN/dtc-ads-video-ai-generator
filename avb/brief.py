"""Brief -> multiple ad script variants.

This is the *top* of the creative funnel, above the parser. Where
``parse_script`` turns a single finished script into shots, this module turns
a product brief (name + selling points + style) into several *competing*
script concepts the user can pick between and edit.

Four frameworks are offered. They are intentionally different plays, not
four flavours of the same line:

  pain_solution   Problem-Agitate-Solution. The classic DR hook: name the
                  pain, make it worse, reveal the mechanism, drop the offer.
  grass_planting  Soft lifestyle recommendation. "I've been using this for
                  a month and..." — warm, never salesy, UGC energy.
  review_proof    Demonstration / before-after with proof and data. Builds
                  credibility for a claim-driven product.
  story_drama     A 15-30s narrative with a character and a payoff beat.

An LLM produces the four when configured; a deterministic template path
covers the offline / no-key case so the feature always works.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field

# --------------------------------------------------------------------------
# Frameworks
# --------------------------------------------------------------------------

SCRIPT_TYPES: list[dict] = [
    {
        "key": "pain_solution",
        "label": "Pain → Solution",
        "accent": "#FF3B30",
        "desc": "Open on a relatable pain point, agitate it, reveal the mechanism, close with the offer + CTA.",
    },
    {
        "key": "grass_planting",
        "label": "Grass-planting / Soft sell",
        "accent": "#A8FF60",
        "desc": "A warm 'I've been using this' recommendation. Lifestyle, never salesy, UGC energy.",
    },
    {
        "key": "review_proof",
        "label": "Review / Proof",
        "accent": "#4CC9F0",
        "desc": "A demonstration or before-after with proof and data — for claim-driven products.",
    },
    {
        "key": "story_drama",
        "label": "Story / Drama",
        "accent": "#FFD400",
        "desc": "A tiny 15-30s narrative with a character and a payoff moment.",
    },
]

SCRIPT_TYPE_KEYS = [t["key"] for t in SCRIPT_TYPES]


@dataclass
class ScriptDraft:
    """One generated ad script."""

    type: str
    title: str
    narration: str  # full, editable voiceover (one sentence per line)
    rationale: str = ""
    outline: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Brief sanitisation
# --------------------------------------------------------------------------


def _strip_bullet(s: str) -> str:
    """Remove a leading list marker — but only a real one.

    `lstrip("•-*0123456789. ")` looked harmless and ate the leading digits of
    perfectly good facts: "53度酱香" became "度酱香" and "500ml" became "ml".
    A marker is a digit run followed by a separator AND a space; "53度" is
    neither.
    """
    s = (s or "").strip()
    s = re.sub(r"^[-*•]\s*", "", s)
    s = re.sub(r"^\d{1,2}[.)、,，]\s*", "", s)
    return s.strip()


def _clean_points(points: list[str] | str | None) -> list[str]:
    if not points:
        return []
    if isinstance(points, str):
        points = points.splitlines()
    out: list[str] = []
    for p in points:
        p = _strip_bullet(p)
        if p:
            out.append(p)
    return out[:8]


_VARIATION_ANGLES = [
    "Lead with the outcome the customer dreams about, then reveal the product as the bridge.",
    "Open with a bold, slightly contrarian claim that stops the scroll.",
    "Start mid-scene in the customer's daily struggle, no preamble.",
    "Use a 'you think X, but actually Y' reframe as the hook.",
    "Open with a concrete sensory detail, then expand to the benefit.",
    "Lead with a question the customer is already asking in their head.",
]


def _assemble_brief(product: str, points: list[str], style: str,
                    audience: str | None, tone: str | None,
                    category: str | None = None,
                    variation: int = 0,
                    language: str = "en",
                    traits: str = "") -> str:
    lines = [f"Product: {product or 'a DTC product'}"]
    # Product traits come first: they are what lets the model know the
    # category (a bottle of baijiu vs a sleep lamp) before it writes a single
    # line. Without them the model falls back to generic filler.
    if traits.strip():
        lines.append("Product traits (what this product physically is):")
        lines += [f"  - {t}" for t in _split_traits(traits)]
    if points:
        lines.append("Selling points:")
        lines += [f"  - {p}" for p in points]
    if style:
        lines.append(f"Visual style: {style}")
    if category:
        lines.append(f"Category: {category}")
    if audience:
        lines.append(f"Target audience: {audience}")
    if tone:
        lines.append(f"Tone: {tone}")
    # The script language is an explicit ask, not something the model should
    # infer: a Chinese user generating for the home market wants 中文 copy, not
    # English with Chinese brand names baked in.
    if language:
        lines.append(f"Script language: {language}")
    if variation:
        n = len(_VARIATION_ANGLES)
        angle = _VARIATION_ANGLES[(variation - 1) % n]
        lines.append(
            f"\nCreative variation #{variation}: make this batch feel genuinely "
            f"fresh and different from a default pass. Hook instruction: {angle}"
        )
    return "\n".join(lines)


def _split_traits(traits: str) -> list[str]:
    """Break a free-text trait blob into individual facts.

    Users type traits in every shape imaginable: one long sentence, a slash
    list, a bulleted block. Splitting on the usual separators keeps the
    brief readable for the model.
    """
    parts = re.split(r"[\n\r]+|[,，;；/、]+|\s{2,}", traits or "")
    out: list[str] = []
    for p in parts:
        p = _strip_bullet(p)
        if p:
            out.append(p)
    return out[:8]



# --------------------------------------------------------------------------
# LLM path
# --------------------------------------------------------------------------

_SYSTEM = """You are a senior short-form video ad copywriter for DTC brands.
Given a product brief, write FOUR complete ad scripts, one in each framework:

- pain_solution: open on a relatable pain point, agitate it, reveal the
  mechanism, close with the offer + CTA.
- grass_planting: a soft, lifestyle "I've been using this" recommendation;
  warm and relatable, never salesy, UGC energy.
- review_proof: a demonstration / before-after review with proof and data;
  build credibility.
- story_drama: a tiny 15-30s narrative with a character and a payoff moment.

THE PRODUCT IS THE SUBJECT. Before writing, work out what this product
actually IS from the brief: its category, material, craft, flavour, spec,
price band and the occasion it belongs to. Every line must belong to THAT
product. A bottle of baijiu is written about the cork, the pour, the aroma,
the table, the second morning. A sleep lamp is written about the light, the
timer, the bedroom at night. Never carry a sentence across from another
category.

HARD RULES
- If the brief names a category, every object, sensation, verb and scene must
  fit that category. Copy that would fit any product at all is a bug:
  "每天都在被同一个问题折磨？" / "Most solutions just mask it" /
  "It just makes life a little easier" say nothing about the product and must
  never appear.
- Name the product (or its brand) at least once per script.
- Use the product traits from the brief concretely: pick at least three and
  put them into the copy as sensory or factual detail, never as a list.
- Give each script at least two concrete sensory or situational details —
  what it looks like, smells, tastes or feels like, who is using it, when
  and where.
- Do NOT invent hard facts that are not in the brief: no invented specs,
  certifications, prices, clinical results, awards or numbers. If the brief
  is thin, stay descriptive instead of making things up. Phrase soft
  promises as feelings, not as facts.
- Write for the target audience: open inside their real situation, use the
  words they would actually use. Do not read the audience label out loud.
- Hold the requested tone across every line (reassuring stays calm and
  confident; playful gets light, punchy phrasing) without breaking the
  framework's structure.
- Respect the requested visual style in tone (cinematic = restrained,
  dr_vsl = punchy, comic = playful).
- Each script is a self-contained voiceover: 8-14 short sentences, ONE idea
  per line (separate lines with \\n).
- Write the scripts in the language explicitly named in the brief's
  "Script language" field. Do NOT translate product names or brand terms
  that are already in another language; keep them verbatim. If no language is
  specified, default to English.
- Every line must be speakable in under ~3 seconds.

Return ONLY JSON of the form:
{"scripts":[
  {"type":"pain_solution","title":"...","narration":"line1\\nline2\\n...",
   "rationale":"one sentence why this framework fits the product"},
  {"type":"grass_planting", ...},
  {"type":"review_proof", ...},
  {"type":"story_drama", ...}
]}"""


def generate_scripts(
    *,
    product: str = "",
    selling_points: list[str] | str | None = None,
    style: str = "",
    audience: str | None = None,
    tone: str | None = None,
    category: str | None = None,
    use_llm: bool = True,
    variation: int = 0,
    language: str = "en",
    product_traits: str = "",
) -> list[ScriptDraft]:
    """Return four script concepts (always 4, in SCRIPT_TYPE_KEYS order)."""
    drafts, _reason = generate_scripts_detailed(
        product=product,
        selling_points=selling_points,
        style=style,
        audience=audience,
        tone=tone,
        category=category,
        use_llm=use_llm,
        variation=variation,
        language=language,
        product_traits=product_traits,
    )
    return drafts


def generate_scripts_detailed(
    *,
    product: str = "",
    selling_points: list[str] | str | None = None,
    style: str = "",
    audience: str | None = None,
    tone: str | None = None,
    category: str | None = None,
    use_llm: bool = True,
    variation: int = 0,
    language: str = "en",
    product_traits: str = "",
) -> tuple[list[ScriptDraft], str]:
    """Same as `generate_scripts`, but also reports WHY the offline path was used.

    Returns (drafts, fallback_reason). An empty reason means the LLM wrote
    these scripts.

    The reason used to be swallowed by a bare `except: pass`, which is how a
    bottle of baijiu ended up with copy about "每天都在被同一个问题折磨" and
    no explanation that the AI had never been asked. Silently degrading is
    worse than failing: the user reads template filler and assumes it is what
    the model thinks of their product.
    """
    points = _clean_points(selling_points)
    brief = _assemble_brief(product, points, style, audience, tone,
                           category=category, variation=variation,
                           language=language, traits=product_traits)

    reason = ""
    if use_llm:
        try:
            drafts = _generate_llm(brief)
            if drafts:
                return _fill_order(drafts), ""
            reason = "LLM returned no usable scripts"
        except Exception as exc:  # noqa: BLE001 — the reason is surfaced to the UI
            reason = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
    else:
        reason = "LLM script writer is off"

    fallback = _generate_heuristic(product, points, style, audience=audience,
                                   tone=tone, language=language,
                                   traits=product_traits, category=category)
    return fallback, reason


def _generate_llm(brief: str) -> list[ScriptDraft]:
    from .llm import LLMError, get_client
    from .config import load_config

    cfg = load_config().llm
    client = get_client(cfg)
    if not client.available:
        raise LLMError("LLM not configured")

    data = client.chat_json(
        [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": brief},
        ],
        temperature=0.8,
        max_tokens=2400,
    )
    if not isinstance(data, dict):
        data = {"scripts": data}
    raw = data.get("scripts") or []
    if not isinstance(raw, list):
        raise ValueError("LLM did not return a scripts array")

    out: list[ScriptDraft] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        t = item.get("type", "")
        narration = str(item.get("narration", "")).strip()
        if not narration:
            continue
        out.append(
            ScriptDraft(
                type=t,
                title=str(item.get("title", "")).strip(),
                narration=narration,
                rationale=str(item.get("rationale", "")).strip(),
            )
        )
    if not out:
        raise ValueError("LLM returned no usable scripts")
    return out


def _fill_order(drafts: list[ScriptDraft]) -> list[ScriptDraft]:
    """Guarantee all four frameworks are present and in canonical order."""
    by_type = {d.type: d for d in drafts}
    ordered: list[ScriptDraft] = []
    for key in SCRIPT_TYPE_KEYS:
        if key in by_type:
            ordered.append(by_type[key])
        else:
            ordered.append(ScriptDraft(type=key, title=key, narration=""))
    return ordered


# --------------------------------------------------------------------------
# Heuristic path (no LLM, always works)
# --------------------------------------------------------------------------

# The offline templates used to be ONE "problem-solving gadget" script, which
# reads like nonsense for anything that is not a gadget — a bottle of baijiu
# got "每天都在被同一个问题折磨？". The fallback now picks its vocabulary from
# the product family, so even without an LLM the copy talks about the right
# product.
_FAMILIES: dict[str, dict[str, dict[str, str]]] = {
    "酒水": {
        "zh": {
            "pain": "喝多了一桌子，最难受的其实是第二天",
            "agitate": "头昏口干，话说不圆，场子散了还得自己收拾",
            "sensory": "开盖酱香就出来了，倒进杯里酒线细长，入口柔，咽下去有回甘",
            "scene": "酒倒下去那一下，整桌的注意力都过去了",
            "proof": "同样的酒量，第二天能不能照常上桌，差别自己知道",
            "offer": "原厂渠道，价格透明",
        },
        "en": {
            "pain": "You drank too much at one table — the worst part is the next morning",
            "agitate": "Head pounding, mouth dry, sentences not landing, and you're the one cleaning up",
            "sensory": "The cap comes off and the aroma is already there; the pour is clean, the first sip is soft with a sweet finish",
            "scene": "The pour is what does it — the whole table stops talking for a second",
            "proof": "Same amount drunk. Whether you can show up the next day is the proof.",
            "offer": "Straight from the source, price shown up front",
        },
    },
    "食品": {
        "zh": {
            "pain": "嘴馋的时候，翻遍柜子只剩一包没味道的",
            "agitate": "吃完不上头、不解馋，第二天还想再买一次",
            "sensory": "撕开包装那一声脆响，第一口下去香气就出来了",
            "scene": "下午三点垫一口，比硬扛到晚饭强",
            "proof": "空袋和回购率，比什么都实在",
            "offer": "整箱囤更划算，坏果包赔",
        },
        "en": {
            "pain": "The craving hits and the cupboard is full of flavourless packets",
            "agitate": "It doesn't hit the spot, you want more by tomorrow, and it was expensive",
            "sensory": "The crack of the pack, then the first bite and the flavour is right there",
            "scene": "Three in the afternoon, one of these beats white-knuckling it to dinner",
            "proof": "Empty bags and reorder rates don't lie",
            "offer": "Stock up on the case, replacements guaranteed",
        },
    },
    "美妆": {
        "zh": {
            "pain": "护肤品越用越贵，脸却还是那张脸",
            "agitate": "厚重的、闷痘的、什么用都没有的，一柜子都是",
            "sensory": "上脸就化开，不搓泥也不黏腻，早晚都能用",
            "scene": "洗完脸抹上，两分钟就能出门",
            "proof": "连着用了一个月，照片里看得出来",
            "offer": "首单小样先试，不合适就退",
        },
        "en": {
            "pain": "Skincare keeps getting pricier and your skin looks the same",
            "agitate": "Heavy, pore-clogging, did-nothing bottles — the shelf is full of them",
            "sensory": "It melts in on contact, no pilling, no stickiness, morning or night",
            "scene": "Clean face, one layer, out the door in two minutes",
            "proof": "A month of consistent use shows up in the photos",
            "offer": "Try the mini first — return it if it isn't for you",
        },
    },
    "3C": {
        "zh": {
            "pain": "设备买了一堆，能用上的没几个",
            "agitate": "插不上、连不上、充不满电，用一次气一次",
            "sensory": "上手就知道差别：接上就稳，一整晚不断连",
            "scene": "出门前拿上，到地方就派上用场",
            "proof": "参数不吹，第三方测出来的数自己看",
            "offer": "两年质保，坏了直接换",
        },
        "en": {
            "pain": "You've bought a drawer of gear and use maybe two of it",
            "agitate": "Doesn't plug in, won't pair, dies at 20% — every time",
            "sensory": "The difference shows the second you touch it: it connects and it stays connected",
            "scene": "Grab it on the way out and it earns its place on arrival",
            "proof": "No spec-sheet fiction — the third-party numbers are there to read",
            "offer": "Two-year warranty, swap it out if it fails",
        },
    },
    "家居": {
        "zh": {
            "pain": "家里总有一处，怎么收拾都不对",
            "agitate": "东西越买越多，空间越来越挤，想换个大件又下不去手",
            "sensory": "材质和做工都在细节里，上手那一刻就知道有没有认真做",
            "scene": "回到家的那一下，光线一落，整个角落就安静了",
            "proof": "用了半年，没有变形也没有多余的味道",
            "offer": "七天无理由，运费我们出",
        },
        "en": {
            "pain": "There's one spot in the house that never looks right",
            "agitate": "More stuff, less space, and the one big purchase you keep putting off",
            "sensory": "The details are where the craft shows — you know it the second you pick it up",
            "scene": "You get home, the light falls right, and that corner finally settles",
            "proof": "Six months in: no warping, no lingering smell",
            "offer": "Seven days, no questions, we pay the shipping",
        },
    },
    "服饰": {
        "zh": {
            "pain": "衣柜满满当当，出门还是没得穿",
            "agitate": "版型不对、肩线塌了、穿两次就变形",
            "sensory": "上身是有骨架的撑感，不是软塌塌挂着",
            "scene": "出门前不用挑，同一套就能直接见人",
            "proof": "洗过十次还没松，肩线还在原位",
            "offer": "不合适直接换，尺码表按实际数据写",
        },
        "en": {
            "pain": "The wardrobe is full and you still have nothing to wear",
            "agitate": "Wrong cut, collapsed shoulder, misshapen after two wears",
            "sensory": "It holds its shape on you instead of hanging off you",
            "scene": "No deciding in the morning — one set, straight out the door",
            "proof": "Ten washes and it still hasn't lost its shape",
            "offer": "Wrong size, send it back — the size chart is measured, not guessed",
        },
    },
    "通用": {
        "zh": {
            "pain": "这类东西你已经换过好几个了，每次都不太行",
            "agitate": "宣传写得漂亮，用起来处处将就，最后还是压在抽屉里",
            "sensory": "上手的细节一眼就知道有没有认真做",
            "scene": "第一次用就顺手，之后就离不开了",
            "proof": "不用看广告，用过的人自己会回购",
            "offer": "不满意直接退，来回运费我们承担",
        },
        "en": {
            "pain": "You've replaced this thing several times and none of them stuck",
            "agitate": "Great on the ad, mediocre in the hand, ends up at the back of a drawer",
            "sensory": "You can tell in the first second whether it was made properly",
            "scene": "It works on the first try, and then you stop buying alternatives",
            "proof": "Skip the ads — the people who own it just buy it again",
            "offer": "Not for you? Send it back, we cover the return shipping",
        },
    },
}

# Two tiers. "strong" words are unambiguous (a product called 无线耳机 is
# definitely 3C); "weak" words are single characters that only make sense in
# a product name — "灯" in 助眠灯, "鞋" in 高跟鞋 — and are weighted low so
# they can never outvote a real match ("包装" must not become 服饰 just
# because it contains 包).
_FAMILY_KEYWORDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "酒水": (
        ("酒", "白酒", "啤酒", "红酒", "洋酒", "鸡尾酒", "威士忌", "白兰地", "香槟",
         "酱香", "茅", "五粮液", "清酒", "烧酒", "干红", "起泡酒",
         "wine", "beer", "whisky", "whiskey", "baijiu", "liquor", "vodka", "sake", "cognac"),
        (),
    ),
    "食品": (
        ("零食", "食品", "生鲜", "干货", "坚果", "糖果", "茶叶", "咖啡", "饮料", "奶粉",
         "糕点", "巧克力", "香肠", "面包", "蜂蜜", "果干", "螺蛳粉", "火锅",
         "food", "snack", "coffee", "tea", "drink", "candy", "nut", "cookie", "honey"),
        (),
    ),
    "美妆": (
        ("美妆", "护肤", "化妆", "口红", "面膜", "精华", "面霜", "香水", "洗发", "沐浴",
         "乳液", "眼霜", "粉底", "防晒", "护手霜", "身体乳", "膏霜", "美容仪",
         "skincare", "serum", "cream", "lipstick", "perfume", "shampoo", "makeup", "lotion"),
        (),
    ),
    "3C": (
        ("数码", "电子", "耳机", "手机", "充电", "电脑", "相机", "音响", "键盘", "手表",
         "智能", "蓝牙", "平板", "显示器", "路由器", "游戏机", "投影", "无人机",
         "3c", "wireless", "earbuds", "headphone", "charger", "phone", "laptop", "camera", "speaker"),
        (),
    ),
    "家居": (
        ("家居", "家具", "床垫", "沙发", "灯具", "台灯", "吊灯", "落地灯", "床头灯",
         "厨具", "收纳", "卫浴", "装修", "家电", "衣柜", "餐桌", "茶几", "窗帘", "坐垫",
         "furniture", "lamp", "sofa", "bed", "kitchen", "storage", "home", "appliance", "curtain"),
        ("灯", "床"),
    ),
    "服饰": (
        ("服饰", "衣服", "T恤", "卫衣", "衬衫", "外套", "裤子", "裙", "鞋", "靴",
         "帽", "袜", "项链", "手链", "戒指", "包袋", "背包", "钱包", "内衣", "泳衣",
         "apparel", "shirt", "dress", "shoe", "jacket", "pants", "hoodie", "bag", "sock"),
        (),
    ),
}

# A weak single-character hit is enough on its own; a strong word outranks it.
_STRONG_WEIGHT = 1.0
_WEAK_WEIGHT = 0.6
_FAMILY_THRESHOLD = 0.6


def _detect_family(category: str | None, traits: str | None, product: str | None) -> str:
    """Guess the product family so the offline templates talk about the right
    thing. Matching runs over the category first, then the traits and the
    product name (a user who types "飞天茅台" as the product never picks a
    category)."""
    hay = " ".join(filter(None, [category or "", traits or "", product or ""])).lower()
    if not hay:
        return "通用"
    best, best_score = "通用", 0.0
    for fam, (strong, weak) in _FAMILY_KEYWORDS.items():
        # Both sides are lowercased: the haystack is, so the keywords must be
        # too or "T恤" never matches a lowercased "纯棉t恤".
        score = _STRONG_WEIGHT * sum(1 for w in strong if w.lower() in hay)
        score += _WEAK_WEIGHT * sum(1 for w in weak if w.lower() in hay)
        if score > best_score:
            best, best_score = fam, score
    return best if best_score >= _FAMILY_THRESHOLD else "通用"


def _bullet(points: list[str], language: str = "en") -> str:
    """The selling points as one clause — never an English phrase in a
    Chinese script."""
    if points:
        return "; ".join(points)
    return "它的核心卖点" if language.startswith("zh") else "its key benefit"


def _audience_line(audience: str | None, language: str = "en") -> str:
    """A hook line that names who this is for, or "" when unspecified.

    Kept generic on purpose: it slots into any framework without assuming
    a product category.
    """
    a = (audience or "").strip()
    if not a:
        return ""
    if language.startswith("zh"):
        return f"如果你{a}，那这一款就是为你准备的。"
    return f"If you're {a}, this one's for you."


def _generate_heuristic(product: str, points: list[str], style: str,
                        audience: str | None = None, tone: str | None = None,
                        language: str = "en",
                        traits: str = "", category: str | None = None) -> list[ScriptDraft]:
    p = (product or ("这款产品" if language.startswith("zh") else "this product")).strip()
    feats = _bullet(points, language)
    zh = language.startswith("zh")
    fam = _detect_family(category, traits, product)
    table = _FAMILIES[fam]["zh" if zh else "en"]

    # The first trait doubles as the "mechanism" line: it is the most concrete
    # fact the user gave us about the product.
    trait_list = _split_traits(traits)
    mechanism = trait_list[0] if trait_list else (points[0] if points else "")
    if zh:
        sp = mechanism or "它把该做的都做了"
    else:
        sp = mechanism or feats

    # The offline templates are fixed copy, so "tone" can only steer small
    # surface choices; the audience line is the one place a brief-only path
    # can visibly personalise the script.
    aud = _audience_line(audience, language=language)

    def _aud(suffix: str) -> str:
        return f"{aud}\n{suffix}" if aud else suffix

    if zh:
        return [
            ScriptDraft(
                type="pain_solution",
                title="痛点反转",
                narration=_aud(
                    f"{table['pain']}。\n"
                    f"{table['agitate']}。\n"
                    f"这正是 {p} 存在的理由。\n"
                    f"它的原理是{sp}。\n"
                    f"不需要复杂流程，更不花冤枉钱。\n"
                    f"就是{feats}。\n"
                    f"{table['proof']}。\n"
                    f"今天试试 {p}，{table['scene']}。\n"
                    f"{table['offer']}，点击下方趁早入手。"
                ),
                rationale="先点出痛点，再揭示机制，最后抛优惠收尾。",
            ),
            ScriptDraft(
                type="grass_planting",
                title="种草安利",
                narration=_aud(
                    f"好啦，我一定要跟你说说 {p}。\n"
                    f"用了一阵子，它已经悄悄成了我生活的一部分。\n"
                    f"最让我心动的是{sp}。\n"
                    f"{table['sensory']}。\n"
                    f"而且{feats}，毫不费力。\n"
                    f"不是什么魔法，但它真的有用。\n"
                    f"{table['proof']}。\n"
                    f"想看就点链接，自己感受一下。"
                ),
                rationale="温暖的第一人称推荐，UGC 质感，软收尾。",
            ),
            ScriptDraft(
                type="review_proof",
                title="测评证明",
                narration=_aud(
                    f"我把 {p} 拿来实测，结果出乎意料。\n"
                    f"先看卖点宣称：{sp}。\n"
                    f"我拿它和之前用的东西做了对比。\n"
                    f"{table['sensory']}。\n"
                    f"几天之内差别就很明显。\n"
                    f"数据不会骗人：{feats}。\n"
                    f"{table['proof']}。\n"
                    f"{table['offer']}，点链接看完整测评。"
                ),
                rationale="用演示和证据做前后对比。",
            ),
            ScriptDraft(
                type="story_drama",
                title="剧情故事",
                narration=(
                    f"她差点放弃——直到一个小小的改变。\n"
                    f"每天早晨都像在爬同一座 uphill。\n"
                    f"后来朋友递给她 {p}。\n"
                    f"一开始她不信：{sp}。\n"
                    f"{table['scene']}。\n"
                    f"一周不到，一切都变了。\n"
                    f"如今{feats}已经成了她的日常。\n"
                    f"有时解法比我们想得简单。\n"
                    f"通过链接找到 {p}——写下你自己的结局。"
                ),
                rationale="一条小小的角色弧线，带一个 payoff。",
            ),
        ]
    return [
        ScriptDraft(
            type="pain_solution",
            title="Pain → Solution",
            narration=_aud(
                f"{table['pain']}.\n"
                f"{table['agitate']}.\n"
                f"That's exactly why {p} exists.\n"
                f"It works by {sp}.\n"
                f"No complicated routine, no wasted money.\n"
                f"Just {feats}.\n"
                f"{table['proof']}.\n"
                f"Try {p} today — {table['scene']}.\n"
                f"{table['offer']}. Tap below before the launch price disappears."
            ),
            rationale="Names the pain, reveals the mechanism, closes with the offer.",
        ),
        ScriptDraft(
            type="grass_planting",
            title="Grass-planting / Soft sell",
            narration=_aud(
                f"Okay, I have to tell you about {p}.\n"
                f"I've been using it for a while now and it's quietly become part of my routine.\n"
                f"What I love is {sp}.\n"
                f"{table['sensory']}.\n"
                f"And {feats} — without any fuss.\n"
                f"No, it's not magic, but it actually delivers.\n"
                f"{table['proof']}.\n"
                f"Link's right there if you want to see for yourself."
            ),
            rationale="Warm first-person recommendation, UGC energy, soft close.",
        ),
        ScriptDraft(
            type="review_proof",
            title="Review / Proof",
            narration=_aud(
                f"So I put {p} to the test — here's what actually happened.\n"
                f"First, the claim: {sp}.\n"
                f"I compared it against what I was using before.\n"
                f"{table['sensory']}.\n"
                f"The difference was obvious within days.\n"
                f"The proof is in the results: {feats}.\n"
                f"{table['proof']}.\n"
                f"{table['offer']}. Check the link to see the full breakdown."
            ),
            rationale="Demonstration with proof and a before/after framing.",
        ),
        ScriptDraft(
            type="story_drama",
            title="Story / Drama",
            narration=(
                f"She almost gave up — until one small change.\n"
                f"Every morning felt like the same uphill climb.\n"
                f"Then a friend handed her {p}.\n"
                f"At first she didn't believe it: {sp}.\n"
                f"{table['scene']}.\n"
                f"By the end of the week, everything felt different.\n"
                f"Now {feats} is just her normal.\n"
                f"Sometimes the fix is simpler than we think.\n"
                f"Find {p} through the link — and write your own ending."
            ),
            rationale="A tiny character arc with a payoff beat.",
        ),
    ]
