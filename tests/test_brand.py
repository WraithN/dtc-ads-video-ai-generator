"""Tests for the brand constraint layer (P2).

Covers:
  - BrandKit IR serialization round-trip (forward-safe)
  - apply_brand_to_project: merge + caption unification + no-op
  - composer: fixed CTA card + logo watermark actually paint pixels
  - web endpoints: GET/PUT /api/brand round-trip (isolated DB)
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw

import pytest
from fastapi.testclient import TestClient

import web.history_db as hdb
import web.server as server
from avb.brand import apply_brand_to_project, default_brand_kit
from avb.ir import BrandKit, Project, Scene, Voiceover, Visual
from avb.parse import parse_script
from avb.render.composer import _draw_cta_card, _draw_logo_watermark, get_style


# --------------------------------------------------------------------------
# IR
# --------------------------------------------------------------------------

def _sample_project() -> Project:
    p = parse_script("Tired of tangled cables? Our magnetic charger ends it. Grab yours today.",
                     template="pain_hook", style="dr_vsl")
    return p


def test_brandkit_defaults_are_backward_compatible():
    b = default_brand_kit()
    assert b.name == "BRAND"
    assert b.primary == "#FF3B30"
    assert b.accent == "#FFE500"
    assert b.ink == "#0B0B0F"
    # new fields default to "no constraint"
    assert b.logo_path == ""
    assert b.cta_text == ""
    assert b.font_key == ""
    assert b.watermark is True


def test_brandkit_round_trip_keeps_new_fields():
    p = _sample_project()
    p.brand = BrandKit(
        name="Acme", primary="#123456", accent="#abcdef", ink="#000000",
        paper="#ffffff", logo_path="/tmp/logo.png", logo_opacity=0.5,
        font_key="impact", cta_text="Shop now", cta_sub="Free shipping",
        caption_mode="karaoke", caption_position="center",
        caption_highlight="#00ff00", caption_scale_pop=0.25,
    )
    d = json.loads(p.to_json())
    assert d["brand"]["logo_path"] == "/tmp/logo.png"
    assert d["brand"]["cta_text"] == "Shop now"
    assert d["brand"]["caption_scale_pop"] == 0.25
    p2 = Project.from_json(p.to_json())
    assert p2.brand.name == "Acme"
    assert p2.brand.font_key == "impact"
    assert p2.brand.logo_opacity == 0.5
    assert p2.brand.caption_highlight == "#00ff00"
    assert p2.brand.caption_scale_pop == 0.25


def test_empty_brand_payload_is_forward_safe():
    # An IR saved before the brand layer existed must reload with defaults.
    raw = json.dumps({"brand": {}})
    p = Project.from_json(raw)
    assert p.brand.name == "BRAND"
    assert p.brand.cta_text == ""


# --------------------------------------------------------------------------
# apply_brand_to_project
# --------------------------------------------------------------------------

def test_apply_brand_none_is_noop():
    p = _sample_project()
    before = [s.caption.mode for s in p.scenes]
    apply_brand_to_project(p, None)
    assert [s.caption.mode for s in p.scenes] == before
    assert p.brand.cta_text == ""


def test_apply_brand_partial_dict_merges():
    p = _sample_project()
    apply_brand_to_project(p, {"cta_text": "Buy now", "primary": "#0000ff"})
    assert p.brand.cta_text == "Buy now"
    assert p.brand.primary == "#0000ff"
    # unrelated fields keep their defaults
    assert p.brand.accent == "#FFE500"


def test_apply_brand_false_boolean_is_honored():
    p = _sample_project()
    apply_brand_to_project(p, {"watermark": False})
    assert p.brand.watermark is False


def test_apply_brand_object_form_works():
    p = _sample_project()
    apply_brand_to_project(p, BrandKit(cta_text="Hi", font_key="impact"))
    assert p.brand.cta_text == "Hi"
    assert p.brand.font_key == "impact"


def test_apply_brand_unifies_captions():
    p = _sample_project()
    # give scenes differing positions first
    p.scenes[0].caption.position = "top"
    p.scenes[1].caption.position = "center"
    apply_brand_to_project(p, {
        "caption_position": "bottom",
        "caption_highlight": "#123123",
        "caption_scale_pop": 0.3,
    })
    for s in p.scenes:
        assert s.caption.position == "bottom"
        assert s.caption.highlight == "#123123"
        assert s.caption.scale_pop == 0.3


def test_apply_brand_empty_strings_do_not_override():
    p = _sample_project()
    p.scenes[0].caption.position = "top"
    apply_brand_to_project(p, {"caption_position": ""})
    assert p.scenes[0].caption.position == "top"


# --------------------------------------------------------------------------
# composer: CTA card + logo watermark paint real pixels
# --------------------------------------------------------------------------

def test_composer_draws_cta_card():
    W = H = 400
    frame = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(frame)
    brand = BrandKit(primary="#FF3B30", accent="#FFE500", paper="#FFFFFF", cta_text="Shop now")
    spec = get_style("dr_vsl")
    _draw_cta_card(d, brand, W, H, None, spec)
    # sample inside the card, above the headline text -> primary fill
    px = frame.getpixel((W // 2, H - int(H * 0.16) - int(H * 0.04) + int(H * 0.16 * 0.10)))
    assert px == (255, 59, 48)


def test_composer_draws_logo_watermark(tmp_path):
    W = H = 400
    logo = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
    logo_path = tmp_path / "logo.png"
    logo.save(logo_path)
    frame = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(frame)
    brand = BrandKit(logo_path=str(logo_path), logo_opacity=1.0, watermark=True)
    _draw_logo_watermark(d, frame, brand, W, H)
    # the logo sits in the top-right corner; at least one pixel is pure red
    found_red = False
    for x in range(W - 90, W - 20):
        for y in range(20, 90):
            if frame.getpixel((x, y)) == (255, 0, 0):
                found_red = True
                break
        if found_red:
            break
    assert found_red


def test_composer_skips_logo_when_watermark_off(tmp_path):
    W = H = 400
    logo = Image.new("RGBA", (64, 64), (255, 0, 0, 255))
    logo_path = tmp_path / "logo.png"
    logo.save(logo_path)
    frame = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(frame)
    brand = BrandKit(logo_path=str(logo_path), watermark=False)
    _draw_logo_watermark(d, frame, brand, W, H)
    # no red pixels anywhere
    assert not any(
        frame.getpixel((x, y)) == (255, 0, 0)
        for x in range(W) for y in range(H)
    )


# --------------------------------------------------------------------------
# web endpoints (isolated DB)
# --------------------------------------------------------------------------

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(hdb, "_DB_PATH", tmp_path / "history.db")
    hdb.init()
    return TestClient(server.app)


def test_brand_api_round_trip(client):
    # default is empty
    r = client.get("/api/brand")
    assert r.status_code == 200
    assert r.json()["brand"] == {}

    # PUT a product kit
    payload = {"product": "acme-charger", "brand": {"name": "Acme", "cta_text": "Shop now", "primary": "#123456"}}
    r = client.put("/api/brand", json=payload)
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # GET it back
    r = client.get("/api/brand", params={"product": "acme-charger"})
    assert r.status_code == 200
    body = r.json()
    assert body["brand"]["cta_text"] == "Shop now"
    assert body["brand"]["primary"] == "#123456"

    # it also shows up in the list
    r = client.get("/api/brand/list")
    keys = [k["product_key"] for k in r.json()["kits"]]
    assert "acme-charger" in keys


def test_brand_api_default_kit(client):
    payload = {"product": "", "brand": {"cta_text": "Default CTA"}}
    r = client.put("/api/brand", json=payload)
    assert r.status_code == 200
    # GET without product falls back to the global default
    r = client.get("/api/brand")
    assert r.json()["brand"]["cta_text"] == "Default CTA"
