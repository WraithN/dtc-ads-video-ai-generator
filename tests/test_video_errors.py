"""Failure taxonomy + retry policy for video generation.

These tests pin the behaviour added after the MiniMax 1008 (insufficient
balance) incident:
  * account-scope errors (balance / auth / quota) must NOT be retried and
    must abort the whole run;
  * transient errors (timeouts, 5xx, rate limits) get bounded backoff;
  * download retries reuse the same job URL (a re-submit would double-bill).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from avb.assets.base import Cache
from avb.assets.video import (
    VideoGenError,
    _AsyncVideoBase,
    classify_video_error,
)


# ---------------------------------------------------------------------------
# classify_video_error
# ---------------------------------------------------------------------------

def test_balance_error_is_account_scope_and_not_retryable():
    err = classify_video_error(
        RuntimeError("{'code': '1008', 'message': 'insufficient balance'}")
    )
    assert err.scope == "account"
    assert err.retryable is False
    assert err.code == "1008"


def test_auth_and_quota_errors_are_account_scope():
    for msg in (
        "401 Unauthorized: invalid api key",
        "quota exceeded for this month",
        "payment required",
        "access denied",
    ):
        err = classify_video_error(RuntimeError(msg))
        assert err.scope == "account", msg
        assert err.retryable is False, msg


def test_timeout_and_connection_errors_are_retryable():
    for exc in (
        TimeoutError("timed out"),
        ConnectionError("connection reset"),
        ConnectionResetError("peer reset"),
    ):
        err = classify_video_error(exc)
        assert err.retryable is True
        assert err.scope == "scene"


def test_rate_limit_is_retryable_even_with_exceeded_wording():
    """'rate limit exceeded' borrows 'exceeded' from the account list --
    it must stay transient, or a 429 would abort the whole render."""
    err = classify_video_error(RuntimeError("429 rate limit exceeded, retry later"))
    assert err.retryable is True
    assert err.scope == "scene"
    err = classify_video_error(RuntimeError("503 service unavailable"))
    assert err.retryable is True


def test_content_moderation_is_scene_scope_permanent():
    err = classify_video_error(RuntimeError("content policy violation: sensitive"))
    assert err.scope == "scene"
    assert err.retryable is False


def test_unknown_error_defaults_to_retryable_scene():
    err = classify_video_error(RuntimeError("something odd happened"))
    assert err.scope == "scene"
    assert err.retryable is True


def test_video_gen_error_passthrough():
    original = VideoGenError("balance", retryable=False, scope="account", code="1008")
    err = classify_video_error(original)
    assert err is original


# ---------------------------------------------------------------------------
# _AsyncVideoBase.generate retry policy (with a stub provider)
# ---------------------------------------------------------------------------

class _StubProvider(_AsyncVideoBase):
    """submit/poll are stubbed; download goes through a fake requests.get."""

    name = "stub"
    env_key = "AVB_TEST_STUB_KEY"  # never set; available() is not used here
    default_model = "stub-1"
    price_per_sec = 0.0

    def __init__(self, failures):
        super().__init__()
        self.failures = list(failures)  # exceptions raised per submit attempt
        self.attempts = 0

    def _submit(self, prompt, seconds, seed, reference_images=None):
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)
        return "task-1"

    def _poll(self, job_id, timeout=600.0):
        return "https://example.com/fake.mp4"


class _FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=1 << 20):
        yield b"fake-mp4-bytes"


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    monkeypatch.setattr("avb.assets.video.requests.get", lambda *a, **kw: _FakeResponse())


def _generate(p: _StubProvider, cache: Cache):
    return p.generate(
        prompt="hello", style="dr_vsl", seed=1, seconds=4.0, w=768, h=1344,
        cache=cache,
    )


def test_generate_retries_transient_then_succeeds(tmp_path, monkeypatch):
    p = _StubProvider([TimeoutError("blip"), ConnectionError("reset")])
    result = _generate(p, Cache(tmp_path / "cache"))
    assert p.attempts == 3  # two failures + one success
    assert result.path.is_file()
    assert result.cached is False


def test_generate_does_not_retry_account_error(tmp_path):
    p = _StubProvider([RuntimeError("{'code': '1008', 'message': 'insufficient balance'}")])
    with pytest.raises(VideoGenError) as ei:
        _generate(p, Cache(tmp_path / "cache"))
    assert ei.value.scope == "account"
    assert ei.value.retryable is False
    assert p.attempts == 1  # gave up immediately


def test_generate_gives_up_after_max_attempts(tmp_path):
    p = _StubProvider([TimeoutError("t")] * 5)
    with pytest.raises(VideoGenError) as ei:
        _generate(p, Cache(tmp_path / "cache"))
    assert ei.value.retryable is True
    assert p.attempts == 3  # max_attempts


def test_generate_caches_result(tmp_path):
    """A second identical generate must hit the cache, not the provider."""
    p = _StubProvider([])
    cache = Cache(tmp_path / "cache")
    first = _generate(p, cache)
    submits_before = p.attempts
    second = _generate(p, cache)
    assert p.attempts == submits_before  # no new submit
    assert second.cached is True
    assert second.path == first.path


def test_generate_retries_download_on_same_job(monkeypatch, tmp_path):
    """Download retries must reuse the same job URL (a re-submit double-bills)."""
    calls = {"n": 0}

    def flaky_get(*a, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("download dropped")
        return _FakeResponse()

    monkeypatch.setattr("avb.assets.video.requests.get", flaky_get)
    p = _StubProvider([])
    result = _generate(p, Cache(tmp_path / "cache"))
    assert p.attempts == 1       # submit+poll ran once -- no double billing
    assert calls["n"] == 3       # two failed downloads + one success
    assert result.path.is_file()


def test_backoff_is_exponential(monkeypatch, tmp_path):
    """Retry pauses must grow exponentially (2, 4, 8 ...) up to the cap.

    A provider that fails twice then succeeds must wait ~2s then ~4s, never
    the old fixed (2, 5) linear schedule. Captured via a spy on time.sleep.
    """
    slept = []

    def spy_sleep(s):
        slept.append(s)

    monkeypatch.setattr(time, "sleep", spy_sleep)
    monkeypatch.setattr("avb.assets.video.requests.get", lambda *a, **kw: _FakeResponse())
    p = _StubProvider([TimeoutError("blip"), ConnectionError("reset")])
    result = _generate(p, Cache(tmp_path / "cache"))
    assert p.attempts == 3  # two failures + one success
    assert result.path.is_file()
    assert slept == [2.0, 4.0], slept


def test_backoff_respects_cap(monkeypatch, tmp_path):
    """With a low cap the schedule never exceeds it (gives up after max)."""
    slept = []

    def spy_sleep(s):
        slept.append(s)

    monkeypatch.setattr(time, "sleep", spy_sleep)
    monkeypatch.setattr("avb.assets.video.requests.get", lambda *a, **kw: _FakeResponse())
    p = _StubProvider([TimeoutError("t")] * 5)
    p.backoff_base = 1.0
    p.backoff_max = 1.5
    with pytest.raises(VideoGenError):
        _generate(p, Cache(tmp_path / "cache"))
    # attempt1->1s, attempt2->1.5s (capped); gives up after max_attempts.
    assert slept == [1.0, 1.5], slept
