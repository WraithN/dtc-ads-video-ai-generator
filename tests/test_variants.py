"""Tests for variant management (P3): A/B variant sets + batch export.

Covers the SQLite store, the generation endpoint (deterministic plan-level
variants), brand consistency across the set, and the zip export.
"""

from __future__ import annotations

import csv
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import web.history_db as hdb
import web.server as server
from avb.ir import Project


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(hdb, "_DB_PATH", tmp_path / "history.db")
    hdb.init()
    return TestClient(server.app)


SCRIPT = (
    "Your afternoon crash is not about willpower. It is about NAD.\n"
    "After thirty, NAD drops. That is why you fade at three PM.\n"
    "Our liposomal NMN restores your NAD levels. One capsule.\n"
    "Forty percent off your first bottle. Tap below."
)


def test_db_variant_set_round_trip():
    hdb.init()
    members = [
        {"member_id": "m1", "idx": 0, "label": "pain_hook · dr_vsl · adam",
         "template": "pain_hook", "style": "dr_vsl", "voice": "adam",
         "framework": "pain_solution", "script": SCRIPT, "project_json": '{"scenes":[]}',
         "status": "planned"},
    ]
    s = hdb.create_variant_set("set1", "acme", "Acme set", SCRIPT, {"count": 1}, members)
    assert s["set_id"] == "set1"
    assert s["members"][0]["member_id"] == "m1"
    got = hdb.get_variant_set("set1")
    assert got["members"][0]["style"] == "dr_vsl"
    listed = hdb.list_variant_sets()
    assert any(x["set_id"] == "set1" for x in listed)
    assert listed[0]["member_count"] == 1


def test_create_variant_set_endpoint(client):
    r = client.post("/api/variant-sets", json={
        "product": "", "name": "Q4", "script": SCRIPT, "count": 5, "language": "en",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["set_id"]
    assert len(body["members"]) == 5
    # each member has a parsed scene count
    for m in body["members"]:
        assert m["scenes"] >= 1
    # templates / styles / voices cycle so variants actually differ
    tpls = {m["template"] for m in body["members"]}
    styles = {m["style"] for m in body["members"]}
    voices = {m["voice"] for m in body["members"]}
    assert len(tpls) > 1
    assert len(styles) > 1
    assert len(voices) > 1


def test_variant_set_applies_stored_brand(client):
    # store a brand kit for the product
    client.put("/api/brand", json={"product": "acme-nmn", "brand": {"cta_text": "Shop Acme"}})
    r = client.post("/api/variant-sets", json={
        "product": "acme-nmn", "name": "branded", "script": SCRIPT, "count": 3,
    })
    assert r.status_code == 200
    for m in r.json()["members"]:
        pj = Project.from_json(m["project_json"])
        assert pj.brand.cta_text == "Shop Acme"


def test_variant_set_get_and_export(client):
    created = client.post("/api/variant-sets", json={
        "product": "", "name": "export-me", "script": SCRIPT, "count": 4,
    }).json()
    sid = created["set_id"]
    r = client.get(f"/api/variant-sets/{sid}")
    assert r.status_code == 200
    assert len(r.json()["members"]) == 4

    r = client.get(f"/api/variant-sets/{sid}/export")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert "README.txt" in names
    assert "variants.csv" in names
    member_files = [n for n in names if n.startswith("variants/") and n.endswith(".json")]
    assert len(member_files) == 4
    # CSV lists all 4 rows
    reader = list(csv.DictReader(io.StringIO(zf.read("variants.csv").decode("utf-8"))))
    assert len(reader) == 4
