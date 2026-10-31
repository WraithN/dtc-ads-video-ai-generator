"""SQLite-backed history store for generated videos and scripts.

Replaces the old `.avb_web/renders.json` single-file registry. SQLite gives
concurrent-safe writes, querying and a clean split between the two kinds of
history we keep:

  * ``renders`` -- every video render (and every resume), newest first.
  * ``scripts`` -- every brief -> 4 ad-script generation.

A plain ``threading.Lock`` serialises writes; reads may run concurrently.
WAL mode keeps the per-write cost tiny so recording history on the hot
render path stays cheap.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

_DB_PATH = Path(".avb_web/history.db")
_LOCK = threading.Lock()


def _conn() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init() -> None:
    """Create the tables if they do not exist."""
    with _LOCK, _conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS renders (
                render_id TEXT PRIMARY KEY,
                job_id    TEXT,
                plan_id   TEXT,
                name      TEXT,
                created_at REAL,
                status    TEXT,
                video_url TEXT,
                video_path TEXT,
                config    TEXT,
                scenes    TEXT,
                cost      REAL,
                total_duration REAL,
                warnings  TEXT,
                parent_id TEXT,
                kind      TEXT,
                project_json TEXT
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS scripts (
                script_id          TEXT PRIMARY KEY,
                job_id             TEXT,
                product            TEXT,
                category           TEXT,
                audience           TEXT,
                tone               TEXT,
                language           TEXT,
                created_at         REAL,
                drafts             TEXT,
                selected_type      TEXT,
                selected_narration TEXT,
                traits             TEXT
            )"""
        )
        # Additive migration: `traits` (what the product physically is) was
        # added after the first release. SQLite has no IF NOT EXISTS for
        # columns, so check the pragma first.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(scripts)").fetchall()}
        if "traits" not in cols:
            conn.execute("ALTER TABLE scripts ADD COLUMN traits TEXT")

        # Additive migration: `project_json` (the full Project IR, so a render
        # can be resumed after a server restart) was added after launch.
        # SQLite has no IF NOT EXISTS for columns, so check the pragma first.
        cols_r = {r[1] for r in conn.execute("PRAGMA table_info(renders)").fetchall()}
        if "project_json" not in cols_r:
            conn.execute("ALTER TABLE renders ADD COLUMN project_json TEXT")


def _load_json(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


# ---------------------------------------------------------------------------
# Renders
# ---------------------------------------------------------------------------

def record_render(rec: dict) -> dict:
    """Insert or replace a render history row. Returns the stored record."""
    with _LOCK, _conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO renders
               (render_id, job_id, plan_id, name, created_at, status,
                video_url, video_path, config, scenes, cost, total_duration,
                warnings, parent_id, kind, project_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec["render_id"],
                rec.get("job_id"),
                rec.get("plan_id"),
                rec.get("name"),
                rec.get("created_at"),
                rec.get("status"),
                rec.get("video_url"),
                rec.get("video_path"),
                json.dumps(rec.get("config") or {}, ensure_ascii=False),
                json.dumps(rec.get("scenes") or [], ensure_ascii=False),
                float(rec.get("cost") or 0.0),
                float(rec.get("total_duration") or 0.0),
                json.dumps(rec.get("warnings") or [], ensure_ascii=False),
                rec.get("parent_id"),
                rec.get("kind"),
                rec.get("project_json"),
            ),
        )
    return rec


def list_renders() -> list[dict]:
    with _LOCK, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM renders ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_render(r) for r in rows]


def get_render(render_id: str) -> dict | None:
    with _LOCK, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM renders WHERE render_id = ?", (render_id,)
        ).fetchone()
    return _row_to_render(row) if row else None


def _row_to_render(row: sqlite3.Row) -> dict:
    return {
        "render_id": row["render_id"],
        "job_id": row["job_id"],
        "plan_id": row["plan_id"],
        "name": row["name"],
        "created_at": row["created_at"],
        "status": row["status"],
        "video_url": row["video_url"],
        "video_path": row["video_path"],
        "config": _load_json(row["config"], {}),
        "scenes": _load_json(row["scenes"], []),
        "cost": row["cost"],
        "total_duration": row["total_duration"],
        "warnings": _load_json(row["warnings"], []),
        "parent_id": row["parent_id"],
        "kind": row["kind"] or ("retry" if row["parent_id"] else "render"),
        "project_json": row["project_json"],
    }


# ---------------------------------------------------------------------------
# Scripts
# ---------------------------------------------------------------------------

def record_script(rec: dict) -> dict:
    """Insert or replace a script-generation history row."""
    with _LOCK, _conn() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO scripts
               (script_id, job_id, product, category, audience, tone,
                language, created_at, drafts, selected_type, selected_narration,
                traits)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec["script_id"],
                rec.get("job_id"),
                rec.get("product"),
                rec.get("category"),
                rec.get("audience"),
                rec.get("tone"),
                rec.get("language"),
                rec.get("created_at"),
                json.dumps(rec.get("drafts") or [], ensure_ascii=False),
                rec.get("selected_type"),
                rec.get("selected_narration"),
                rec.get("traits"),
            ),
        )
    return rec


def list_scripts() -> list[dict]:
    with _LOCK, _conn() as conn:
        rows = conn.execute(
            "SELECT * FROM scripts ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_script(r) for r in rows]


def select_script(script_id: str, type_: str, narration: str = "") -> None:
    """Record which concept in a batch the user moved forward with.

    Purely informational — it lets the history page show "used" instead of
    leaving four equally-weighted drafts. Cheap enough to be best-effort.
    """
    with _LOCK, _conn() as conn:
        conn.execute(
            "UPDATE scripts SET selected_type = ?, selected_narration = ? "
            "WHERE script_id = ?",
            (type_, narration, script_id),
        )


def get_script(script_id: str) -> dict | None:
    with _LOCK, _conn() as conn:
        row = conn.execute(
            "SELECT * FROM scripts WHERE script_id = ?", (script_id,)
        ).fetchone()
    return _row_to_script(row) if row else None


def _row_to_script(row: sqlite3.Row) -> dict:
    return {
        "script_id": row["script_id"],
        "job_id": row["job_id"],
        "product": row["product"],
        "category": row["category"],
        "audience": row["audience"],
        "tone": row["tone"],
        "language": row["language"],
        "created_at": row["created_at"],
        "traits": row["traits"] if "traits" in row.keys() else "",
        "drafts": _load_json(row["drafts"], []),
        "selected_type": row["selected_type"],
        "selected_narration": row["selected_narration"],
    }


# ---------------------------------------------------------------------------
# One-time migration of the old JSON registry
# ---------------------------------------------------------------------------

def migrate_json() -> int:
    """Import a legacy `.avb_web/renders.json` into the DB (once).

    Returns the number of rows imported. If the DB already has renders, or the
    JSON file is gone, this is a no-op.
    """
    json_path = Path(".avb_web/renders.json")
    if not json_path.is_file():
        return 0
    with _LOCK, _conn() as conn:
        existing = conn.execute("SELECT COUNT(*) AS n FROM renders").fetchone()["n"]
        if existing > 0:
            return 0
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        count = 0
        for rec in data:
            if not isinstance(rec, dict) or not rec.get("render_id"):
                continue
            conn.execute(
                """INSERT OR REPLACE INTO renders
                   (render_id, job_id, plan_id, name, created_at, status,
                    video_url, video_path, config, scenes, cost, total_duration,
                    warnings, parent_id, kind)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    rec.get("render_id"),
                    rec.get("job_id"),
                    rec.get("plan_id"),
                    rec.get("name"),
                    rec.get("created_at", 0),
                    rec.get("status"),
                    rec.get("video_url"),
                    rec.get("video_path"),
                    json.dumps(rec.get("config") or {}, ensure_ascii=False),
                    json.dumps(rec.get("scenes") or [], ensure_ascii=False),
                    float(rec.get("cost") or 0.0),
                    float(rec.get("total_duration") or 0.0),
                    json.dumps(rec.get("warnings") or [], ensure_ascii=False),
                    rec.get("parent_id"),
                    rec.get("kind") or ("retry" if rec.get("parent_id") else "render"),
                ),
            )
            count += 1
    return count

