"""Tests for P4 crash recovery: durable 'running' status + auto-requeue.

A render now records a 'running' row (with its full Project IR) before it
spends any money. If the server dies mid-render that row stays 'running'; on
boot ``_recover_active_renders`` flips it to 'interrupted' (so a future
restart won't loop) and re-queues a resume that reuses the cache.
"""

from __future__ import annotations

import pytest

import web.history_db as hdb
import web.server as server


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(hdb, "_DB_PATH", tmp_path / "history.db")
    hdb.init()


@pytest.fixture
def clean_jobs():
    """Isolate the module-global job registry so recovery tests that spawn a
    real (but stubbed) job don't leak a 'running' entry into later tests."""
    saved = dict(server._jobs)
    server._jobs.clear()
    yield
    server._jobs.clear()
    server._jobs.update(saved)


def _record_running(rid: str) -> None:
    hdb.record_render({
        "render_id": rid,
        "job_id": rid,
        "plan_id": "p1",
        "name": "interrupted render",
        "created_at": 1_700_000_000.0,
        "status": "running",
        "config": {"video_provider": "kling"},
        "project_json": '{"scenes": []}',
    })


def test_find_active_renders(db):
    _record_running("r-running")
    active = hdb.find_active_renders()
    assert [r["render_id"] for r in active] == ["r-running"]
    hdb.set_render_status("r-running", "interrupted")
    assert hdb.find_active_renders() == []


def test_recover_flips_to_interrupted_and_schedules_resume(db, clean_jobs, monkeypatch):
    scheduled = []

    def fake_run_in_executor(_exec, fn, *args):
        scheduled.append((fn, args))
        fn(*args)  # actually invoke the (patched) resume so it records to `calls`
        return None

    class FakeLoop:
        def run_in_executor(self, _exec, fn, *args):
            return fake_run_in_executor(_exec, fn, *args)

    # capture the resume call instead of running the real (heavy) pipeline
    calls = []
    monkeypatch.setattr(server, "_run_resume",
                        lambda jid, src, only, out: calls.append((jid, src, only, out)))

    _record_running("r-crash")
    recovered = server._recover_active_renders(FakeLoop())

    assert recovered == ["r-crash"]
    # original is no longer 'running' -> won't be recovered again next boot
    assert hdb.find_active_renders() == []
    assert hdb.get_render("r-crash")["status"] == "interrupted"
    # a resume was scheduled as a child of the crashed render
    assert len(calls) == 1
    assert calls[0][1] == "r-crash"  # src_render_id
    assert calls[0][0].startswith("job-") or calls[0][0]  # new job id issued


def test_recover_noop_when_nothing_active(db, monkeypatch):
    scheduled = []
    monkeypatch.setattr(server, "_run_resume",
                        lambda jid, src, only, out: scheduled.append(src))
    assert server._recover_active_renders(object()) == []
    assert scheduled == []
