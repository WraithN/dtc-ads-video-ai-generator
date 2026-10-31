"""Tests for the SQLite history store (web/history_db.py).

These exercise the additive schema migration and the project_json round-trip
that makes a render resumable after a server restart.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import web.history_db as hdb


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(hdb, "_DB_PATH", tmp_path / "history.db")
    hdb.init()
    yield hdb


def _render_rec(render_id: str = "r1", **over) -> dict:
    rec = {
        "render_id": render_id,
        "job_id": "j1",
        "plan_id": "p1",
        "name": "test",
        "created_at": 123.0,
        "status": "done",
        "video_url": "/api/video/x.mp4",
        "video_path": "demo/out/web/x.mp4",
        "config": {"a": 1},
        "scenes": [{"id": "s1", "status": "ok"}],
        "cost": 0.5,
        "total_duration": 9.0,
        "warnings": [],
        "parent_id": None,
        "kind": "render",
        "project_json": json.dumps({"id": "p1", "scenes": [{"id": "s1"}]}),
    }
    rec.update(over)
    return rec


def test_record_and_get_roundtrip(db):
    db.record_render(_render_rec())
    row = db.get_render("r1")
    assert row is not None
    assert row["status"] == "done"
    assert row["config"] == {"a": 1}
    assert row["scenes"] == [{"id": "s1", "status": "ok"}]
    # The persisted IR is what lets resume rebuild the Project after restart.
    assert row["project_json"] == json.dumps({"id": "p1", "scenes": [{"id": "s1"}]})


def test_list_renders_newest_first(db):
    db.record_render(_render_rec("r2", created_at=100.0))
    db.record_render(_render_rec("r3", created_at=200.0))
    rows = db.list_renders()
    assert [r["render_id"] for r in rows] == ["r3", "r2"]


def test_get_missing_returns_none(db):
    assert db.get_render("nope") is None


def test_migration_adds_project_json_column(tmp_path):
    # Build a DB with the pre-project_json schema, then run init() and confirm
    # the ALTER migration adds the column so old installs keep working.
    old_path = tmp_path / "old.db"
    conn = sqlite3.connect(str(old_path))
    conn.execute(
        """CREATE TABLE renders (
            render_id TEXT PRIMARY KEY, job_id TEXT, plan_id TEXT, name TEXT,
            created_at REAL, status TEXT, video_url TEXT, video_path TEXT,
            config TEXT, scenes TEXT, cost REAL, total_duration REAL,
            warnings TEXT, parent_id TEXT, kind TEXT)"""
    )
    conn.close()

    hdb._DB_PATH = old_path
    hdb.init()  # must ALTER the table to add project_json
    with sqlite3.connect(str(old_path)) as c:
        cols = {r[1] for r in c.execute("PRAGMA table_info(renders)").fetchall()}
    assert "project_json" in cols

    # And a write/read on the migrated table must succeed.
    hdb.record_render(_render_rec("migrated"))
    assert hdb.get_render("migrated")["project_json"] is not None


def test_script_select(db):
    db.record_script(
        {
            "script_id": "s1",
            "job_id": "j",
            "product": "Lumo 助眠灯",
            "category": "小家电",
            "audience": "夜班族",
            "tone": "安心",
            "language": "zh",
            "created_at": 1.0,
            "drafts": [{"type": "pain_solution", "title": "t"}],
            "traits": "53度酱香型白酒",
        }
    )
    assert db.get_script("s1")["product"] == "Lumo 助眠灯"
    db.select_script("s1", "pain_solution", "narration text")
    assert db.get_script("s1")["selected_type"] == "pain_solution"
