"""Tests for P4 cost aggregation (GET /api/cost + history_db.aggregate_cost).

Spend is rolled up across recorded renders: a grand total, a per-provider
breakdown (provider taken from each render's stored config), and a per-day
series. These let the user see where the billed-API dollars went.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import web.history_db as hdb
import web.server as server


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(hdb, "_DB_PATH", tmp_path / "history.db")
    hdb.init()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(hdb, "_DB_PATH", tmp_path / "history.db")
    hdb.init()
    return TestClient(server.app)


def _record(cost: float, provider: str, age_days: int = 0) -> None:
    hdb.record_render({
        "render_id": f"r-{cost}-{provider}-{age_days}",
        "job_id": "j",
        "plan_id": "p",
        "name": "test",
        "created_at": time.time() - age_days * 86400.0,
        "status": "done",
        "config": {"video_provider": provider},
        "cost": cost,
    })


def test_aggregate_cost_empty(db):
    agg = hdb.aggregate_cost()
    assert agg["total"] == 0.0
    assert agg["count"] == 0
    assert agg["by_provider"] == {}
    assert agg["by_day"] == []


def test_aggregate_cost_totals_and_provider_breakdown(db):
    _record(1.0, "kling")
    _record(2.5, "kling")
    _record(4.0, "veo")
    agg = hdb.aggregate_cost()
    assert agg["total"] == pytest.approx(7.5)
    assert agg["count"] == 3
    # provider breakdown sums correctly and is sorted by spend desc
    assert agg["by_provider"]["kling"] == pytest.approx(3.5)
    assert agg["by_provider"]["veo"] == pytest.approx(4.0)
    assert list(agg["by_provider"].keys())[0] == "veo"


def test_aggregate_cost_groups_by_day(db):
    _record(1.0, "kling", age_days=0)
    _record(2.0, "kling", age_days=0)
    _record(5.0, "veo", age_days=3)
    agg = hdb.aggregate_cost()
    today = agg["by_day"][0]["day"]
    todays = [d for d in agg["by_day"] if d["day"] == today]
    assert todays[0]["cost"] == pytest.approx(3.0)
    assert todays[0]["renders"] == 2


def test_cost_endpoint(client):
    _record(1.5, "kling")
    _record(3.5, "veo")
    r = client.get("/api/cost")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == pytest.approx(5.0)
    assert body["count"] == 2
    assert body["by_provider"]["kling"] == pytest.approx(1.5)
    assert body["by_provider"]["veo"] == pytest.approx(3.5)
