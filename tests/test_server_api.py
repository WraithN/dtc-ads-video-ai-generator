"""API-level tests for web/server.py using FastAPI's TestClient.

Focuses on the two regressions flagged in review:
  - P0: /api/video must never let a filename traverse out of its directory.
  - P1: a render must be resumeable after a restart (project_json round-trip),
        and resume of a 'done' render must be refused.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import web.history_db as hdb
import web.server as server
from avb.ir import Project, Scene, Visual, Voiceover


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Isolate the history DB so these tests never touch the real .avb_web store.
    monkeypatch.setattr(hdb, "_DB_PATH", tmp_path / "history.db")
    hdb.init()
    return TestClient(server.app)


# --------------------------------------------------------------------------
# P0: path traversal
# --------------------------------------------------------------------------


def test_serve_video_blocks_path_traversal(client):
    # README lives ABOVE the video directory (repo root). If the traversal went
    # through, we would get a 200 with file contents. It must not -- the resolve
    # + parent-directory check keeps the response inside VIDEO_DIR, so the only
    # possible outcome here is 404 (file does not exist there).
    r = client.get("/api/video/..%2f..%2fREADME.md")
    assert r.status_code == 404
    assert b"# AI Video Builder" not in r.content  # never leaked the README


def test_serve_video_missing_is_404(client):
    r = client.get("/api/video/does_not_exist.mp4")
    assert r.status_code == 404


def test_serve_video_keeps_legit_dotdot_in_name(client):
    # A legitimate filename containing ".." must NOT be rejected (the old
    # substring ban would have blocked "v2..final.mp4"). There is no such file,
    # so we only assert it is not a 400 from the guard.
    r = client.get("/api/video/v2..final.mp4")
    assert r.status_code != 400


# --------------------------------------------------------------------------
# P1: resume semantics
# --------------------------------------------------------------------------


def test_resume_unknown_render_404(client):
    r = client.post("/api/render/resume", json={"render_id": "no-such-render"})
    assert r.status_code == 404


def test_resume_done_render_refused(client):
    hdb.record_render(
        {
            "render_id": "done1",
            "job_id": "j",
            "plan_id": "p",
            "name": "n",
            "created_at": 1.0,
            "status": "done",
            "video_url": None,
            "video_path": None,
            "config": {},
            "scenes": [{"id": "s1", "status": "ok"}],
            "cost": 0,
            "total_duration": 0,
            "warnings": [],
            "parent_id": None,
            "kind": "render",
            "project_json": None,
        }
    )
    r = client.post("/api/render/resume", json={"render_id": "done1"})
    assert r.status_code == 400


# --------------------------------------------------------------------------
# IR round-trip: the foundation of cross-restart resume
# --------------------------------------------------------------------------


def test_project_json_roundtrip_losssless():
    p = Project(
        name="黄河",
        script="s",
        aspect="9:16",
        resolution="1080p",
        scenes=[
            Scene(
                id="s1",
                beat="hook",
                vo=Voiceover(text="hi"),
                visual=Visual(prompt="p", style="dr_vsl", shot="close-up"),
            )
        ],
    )
    p2 = Project.from_json(p.to_json())
    assert p2.name == p.name
    assert p2.scenes[0].vo.text == "hi"
    assert p2.scenes[0].visual.shot == "close-up"
    assert p2.width == p.width


# --------------------------------------------------------------------------
# P3: shared mutable state (one task at a time, locked mutations)
# --------------------------------------------------------------------------


def test_require_no_active_job_helper(monkeypatch):
    # Isolate the shared job map so we don't trip on state from other tests.
    monkeypatch.setattr(server, "_jobs", {})
    server._require_no_active_job()  # no jobs -> fine

    server._jobs["done"] = {"done": True}
    server._require_no_active_job()  # finished jobs don't count

    server._jobs["active"] = {"done": False}
    import pytest as _pytest

    with _pytest.raises(Exception) as exc:
        server._require_no_active_job()
    # The guard raises FastAPI's HTTPException(409, ...).
    assert exc.value.status_code == 409


def test_resume_rejects_when_another_job_active(client, monkeypatch):
    # The single-flight guard must fire before any DB/provider work, so a
    # second start while one job is in flight is rejected with 409.
    monkeypatch.setattr(server, "_jobs", {"active": {"done": False}})
    r = client.post("/api/render/resume", json={"render_id": "x"})
    assert r.status_code == 409


def test_update_job_concurrent_no_corruption(monkeypatch):
    import threading

    monkeypatch.setattr(server, "_jobs", {})
    ids = [server._new_job("render") for _ in range(8)]

    def worker(jid, n):
        for i in range(200):
            server._update_job(jid, progress=i / 200, message=f"m{i}", stage="running")

    threads = [threading.Thread(target=worker, args=(jid, i)) for i, jid in enumerate(ids)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for jid in ids:
        job = server._jobs[jid]
        assert isinstance(job["progress"], float)
        assert 0.0 <= job["progress"] <= 1.0
        assert isinstance(job["message"], str)
        assert job["done"] is False
