"""Motion footage providers.

Every provider here is a *hosted* model: Veo 3.1, Kling 3.0, Seedance,
Hailuo, Wan, CogVideoX, Jimeng. There is deliberately no offline stand-in --
a fake clip would produce a video that looks finished and isn't, which is
worse than failing loudly. When nothing is configured, `build_video_provider`
raises `ProviderUnavailable` naming the exact env vars to fill in.

All of them share the same call shape and are selected by env var. Note that
every one of them is *asynchronous*: POST -> job id -> poll -> download. The
orchestrator owns the queue; providers must stay pure.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import os
import re
import sys
import threading
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path

import requests

from ..hashing import short_hash
from .base import Cache, Cost, Provider

__all__ = [
    "VeoVideo", "KlingVideo",
    "BailianVideo", "MiniMaxVideo", "ZhipuVideo", "JimengVideo", "SeedanceVideo",
    "VideoResult", "build_video_provider", "ProviderUnavailable",
    "image_data_uri",
]

_MAGIC_MIME = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
)


def image_data_uri(path: str | Path) -> str:
    """Local image file -> `data:<mime>;base64,...` for vendor APIs.

    MiniMax H3 v2 accepts reference images as public URLs or base64 data
    URIs; a local web app has no public host, so data URI it is (bodies stay
    well under the 64 MB cap for a handful of product photos).
    """
    raw = Path(path).read_bytes()
    mime = "image/webp"
    for magic, m in _MAGIC_MIME:
        if raw.startswith(magic):
            mime = m
            break
    else:
        if raw[:4] in (b"RIFF",) and raw[8:12] == b"WEBP":
            mime = "image/webp"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _sniff_mime(path: str | Path) -> str:
    """MIME type only (for APIs that split base64 from its content type)."""
    raw = Path(path).read_bytes()[:12]
    for magic, m in _MAGIC_MIME:
        if raw.startswith(magic):
            return m
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


class ProviderUnavailable(RuntimeError):
    """Raised when no video provider has credentials.

    Carries `missing` so callers (CLI doctor, web settings) can render a
    precise "set these variables" list instead of a bare failure.
    """

    def __init__(self, message: str, missing: dict[str, tuple[str, ...]] | None = None):
        super().__init__(message)
        self.missing = missing or {}


# --- Failure classification -------------------------------------------------
#
# A generation call can fail in ways that need opposite handling. Burning a
# retry on "insufficient balance" wastes minutes and produces the same error;
# giving up immediately on a 503 throws away a render that would have
# succeeded eight seconds later. So every failure is sorted into one of three
# buckets before anyone decides what to do with it.

# Will never succeed on retry: the account is out of money, the key is wrong,
# the request itself is unacceptable. Surface at once and tell the human to
# change something outside the code.
_ACCOUNT_MARKERS = (
    "insufficient balance", "insufficient_balance", "balance",
    "quota", "exceeded", "unauthorized", "forbidden",
    "invalid api key", "invalid_api_key", "api key", "authentication",
    "permission", "billing", "payment", "access denied", "1008",
)

# The request was rejected for something about *this* prompt/image -- other
# scenes are unaffected, so the run should continue with a placeholder.
_SCENE_MARKERS = (
    "content policy", "content_policy", "sensitive", "moderation",
    "flagged", "invalid request", "invalid_request", "invalid parameter",
    "invalid_param", "parameter", "unsupported", "too long", "not found",
)

# Worth trying again: transport hiccups and vendor-side load. Everything not
# clearly marked permanent lands here too -- a one-off blip should not kill a
# render, and a genuinely broken account still surfaces after the retries.
_TRANSIENT_MARKERS = (
    "timeout", "timed out", "rate limit", "rate_limit", "too many request",
    "busy", "unavailable", "try again", "retry", "internal error",
    "internal_error", "overload", "server error", "server_error",
    "temporarily", "connection", "reset by peer", "deadline",
    "429", "500", "502", "503", "504",
)

# Rate limiting is transient even though vendor phrasing ("rate limit
# exceeded") borrows words from the account list below. Checked before the
# account markers so a 429 stays retryable instead of aborting the run.
_RATELIMIT_MARKERS = (
    "rate limit", "rate_limit", "too many request", "429", "throttl",
)


class VideoGenError(RuntimeError):
    """A vendor failure, tagged with how the pipeline should react to it.

    `retryable` -- automatic retry could plausibly help.
    `scope` -- "account" aborts the whole run (every scene would fail the
    same way); "scene" lets the pipeline fall back to a placeholder backdrop
    for this one shot and keep rendering the rest.
    """

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
        scope: str = "scene",
        code: str | None = None,
    ):
        super().__init__(message)
        self.retryable = retryable
        self.scope = scope
        self.code = code


def classify_video_error(exc: BaseException) -> VideoGenError:
    """Sort an arbitrary provider failure into the buckets above.

    Idempotent: an already-classified VideoGenError comes back unchanged, so
    callers can wrap this around any exception without double-wrapping.
    """
    if isinstance(exc, VideoGenError):
        return exc
    msg = str(exc)
    low = msg.lower()
    code: str | None = None
    m = re.search(r"['\"]code['\"]\s*:\s*['\"]?(\w+)", msg)
    if m:
        code = m.group(1)

    if code == "1008":
        return VideoGenError(msg, retryable=False, scope="account", code=code)
    if any(s in low for s in _RATELIMIT_MARKERS):
        return VideoGenError(msg, retryable=True, scope="scene", code=code)
    if any(s in low for s in _ACCOUNT_MARKERS):
        return VideoGenError(msg, retryable=False, scope="account", code=code)
    if isinstance(exc, (TimeoutError, ConnectionError, requests.RequestException)):
        return VideoGenError(msg, retryable=True, scope="scene", code=code)
    if any(s in low for s in _TRANSIENT_MARKERS):
        return VideoGenError(msg, retryable=True, scope="scene", code=code)
    if any(s in low for s in _SCENE_MARKERS):
        return VideoGenError(msg, retryable=False, scope="scene", code=code)
    return VideoGenError(msg, retryable=True, scope="scene", code=code)


@dataclass
class VideoResult:
    path: Path
    duration: float
    cached: bool
    cost: Cost


class _AsyncVideoBase(Provider):
    """Shared plumbing for the asynchronous hosted video APIs.

    Every provider in this family is: submit -> poll -> download. There is no
    synchronous path that hands back an mp4, so don't design for one.
    """

    kind = "video"
    price_per_sec = 0.10
    submit_url = ""
    env_key = ""
    # Whether _submit can consume reference images. Providers that cannot
    # (see subclasses) silently drop them, but the caller deserves a warning
    # because "uploaded three views and got a text-only ad" looks like a bug.
    supports_reference_images = False
    # Model revision: env override wins, class default otherwise. Vendors rev
    # these identifiers constantly, so a stale id is the commonest failure --
    # being able to set it without a code change is the point.
    model_env = ""
    default_model = ""

    def __init__(self) -> None:
        self.api_key = os.environ.get(self.env_key, "")

    def _report(self, **info) -> None:
        # Thread-local progress (see report_progress / progress_scope below).
        # generate() binds a per-call callback into thread-local state, so
        # concurrent generate() calls on the same provider instance each report
        # to their own observer instead of racing on a shared attribute.
        report_progress(provider=self.name, **info)

    @property
    def model(self) -> str:
        return (os.environ.get(self.model_env, "") or self.default_model).strip()

    def available(self) -> bool:
        return bool(self.api_key)

    def price(self, seconds: float = 0.0, **_: object) -> float:
        return round(seconds * self.price_per_sec, 6)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _submit(
        self, prompt: str, seconds: float, seed: int,
        reference_images: list[Path] | None = None,
    ) -> str:
        """Submit one generation job, return the vendor task id.

        `reference_images` are validated local paths (product three-views
        etc.). Subclasses that support them consume them; the rest ignore.
        """
        raise NotImplementedError

    def _poll(self, job_id: str, timeout: float = 600.0) -> str:
        raise NotImplementedError

    # Attempts per step for retryable failures. Generous enough to ride out a
    # vendor blip, tight enough that a dead account is obvious in under a
    # minute rather than after eight scenes times three tries each.
    max_attempts = 3
    # Exponential backoff between retries. Retry N waits
    # min(backoff_max, backoff_base * 2 ** (N - 1)) seconds: 2s, 4s, 8s, ...
    # capped at 30s. A billed API hates long stalls, so the base is small and
    # the cap keeps a dead account from sleeping for minutes across many
    # scenes; exponential (not linear) means a brief vendor hiccup is absorbed
    # quickly while a longer outage still backs off instead of hammering.
    backoff_base = 2.0
    backoff_max = 30.0

    def _pause_for(self, attempt: int) -> float:
        """Seconds to wait before retry attempt N (1-indexed)."""
        return min(self.backoff_max, self.backoff_base * (2 ** (attempt - 1)))

    def generate(
        self, prompt: str, style: str, seed: int, seconds: float, w: int, h: int,
        cache: Cache, reference_images: list[Path] | None = None,
    ) -> VideoResult:
        refs = [Path(p) for p in (reference_images or []) if Path(p).is_file()]
        # Include the model id: two MiniMax models (v1 Hailuo vs v2 H3) share
        # `self.name` but produce different footage, so a key without the model
        # would serve a stale clip when the user switches tiers. Reference
        # images hash their *bytes*: re-uploading a changed three-view set must
        # invalidate the cache, but re-rendering the same images must not.
        key = short_hash({
            "p": self.name, "m": self.model, "prompt": prompt, "style": style,
            "seed": seed, "sec": round(seconds, 2), "w": w, "h": h,
            "refs": [
                hashlib.sha256(p.read_bytes()).hexdigest()[:16]
                for p in refs
            ] if refs else None,
        })
        hit = cache.get("video", key, ".mp4")
        if hit:
            self._report(phase="cache")
            return VideoResult(path=hit, duration=seconds, cached=True, cost=Cost(self.name, "video", 0.0))

        if refs and not self.supports_reference_images:
            print(
                f"  !! {self.name} does not support reference images; "
                f"ignoring {len(refs)} uploaded image(s) (text-to-video only)"
            )
        t0 = time.time()

        # Submit + poll are retried together, as one attempt. A submit that
        # never produced a task id cost nothing, so re-running it is safe; a
        # vendor-declared job failure is classified first so a dead balance
        # fails immediately instead of buying three identical errors.
        url = ""
        for attempt in range(1, self.max_attempts + 1):
            try:
                self._report(phase="submitting", prompt_chars=len(prompt),
                             model=self.model, ref_images=len(refs), attempt=attempt)
                job = self._submit(prompt, seconds, seed, refs)
                self._report(phase="submitted", task_id=str(job), attempt=attempt)
                url = self._poll(job)
                break
            except Exception as e:  # noqa: BLE001 - classified just below
                err = classify_video_error(e)
                if not err.retryable or attempt >= self.max_attempts:
                    self._report(phase="failed", attempt=attempt, error=str(err)[:300])
                    raise err from e
                pause = self._pause_for(attempt)
                self._report(phase="retrying", attempt=attempt,
                             wait_s=pause, error=str(err)[:200])
                print(f"  !! {self.name}: {err} -- retrying "
                      f"({attempt}/{self.max_attempts - 1}) in {pause:.0f}s",
                      file=sys.stderr)
                time.sleep(pause)

        # Download retries in place against the same URL. Deliberately NOT
        # folded into the loop above: the job already exists and is already
        # billed, so re-submitting to fix a flaky download would pay twice.
        self._report(phase="downloading", received_mb=0.0)
        tmp = cache.root / "video" / f"tmp_{os.getpid()}_{key}.mp4"
        tmp.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, self.max_attempts + 1):
            try:
                received = 0
                last_report = 0
                with requests.get(url, stream=True, timeout=300) as r:
                    r.raise_for_status()
                    with open(tmp, "wb") as f:
                        for chunk in r.iter_content(1 << 20):
                            if chunk:
                                f.write(chunk)
                                received += len(chunk)
                                # Throttled: ~every 4 MiB, enough for a progress
                                # line without flooding the websocket.
                                if received - last_report >= (4 << 20):
                                    last_report = received
                                    self._report(phase="downloading",
                                                 received_mb=round(received / 1e6, 1))
                break
            except Exception as e:  # noqa: BLE001 - classified just below
                err = classify_video_error(e)
                if not err.retryable or attempt >= self.max_attempts:
                    self._report(phase="failed", attempt=attempt, error=str(err)[:300])
                    raise err from e
                pause = self._pause_for(attempt)
                self._report(phase="retrying_download", attempt=attempt, wait_s=pause)
                print(f"  !! {self.name}: download failed ({err}); "
                      f"retrying in {pause:.0f}s", file=sys.stderr)
                time.sleep(pause)
        self._report(phase="downloaded", received_mb=round(received / 1e6, 1))
        final = cache.put("video", key, ".mp4", tmp)
        self._report(phase="done_scene_asset", elapsed=round(time.time() - t0, 1))
        return VideoResult(path=final, duration=seconds, cached=False, cost=Cost(self.name, "video", self.price(seconds), seconds))


class VeoVideo(_AsyncVideoBase):
    """Google Veo 3.1. Native audio, longest clips, SynthID watermark
    (cannot be disabled -- plan your disclosure flow around it)."""

    name = "veo-3.1"
    env_key = "GOOGLE_API_KEY"
    price_per_sec = 0.40  # 1080p standard
    model_env = "VEO_MODEL"
    default_model = "veo-3.1-generate-001"
    supports_reference_images = True  # first reference image = conditioning frame
    # The model id is part of the path, not the body -- so it is templated
    # per request rather than baked into `submit_url`.
    submit_url = "https://generativelanguage.googleapis.com/v1beta/models"

    def _submit(self, prompt: str, seconds: float, seed: int,
                reference_images: list[Path] | None = None) -> str:
        payload = {
            "instances": [{"prompt": prompt}],
            "parameters": {
                "aspectRatio": "9:16",
                "durationSeconds": int(max(4, min(60, seconds))),
                "personGeneration": "allow_adult",
                "sampleCount": 1,
            },
        }
        # Veo 3.1 image-to-video rides the same predictLongRunning endpoint:
        # attach the first reference image as the conditioning frame.
        if reference_images:
            payload["instances"][0]["image"] = {
                "bytesBase64Encoded": base64.b64encode(
                    Path(reference_images[0]).read_bytes()
                ).decode("ascii"),
                "mimeType": _sniff_mime(reference_images[0]),
            }
        url = f"{self.submit_url}/{self.model}:predictLongRunning?key={self.api_key}"
        r = requests.post(url, json=payload, timeout=120)
        r.raise_for_status()
        return r.json()["name"]

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/{job_id}?key={self.api_key}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            d = r.json()
            if d.get("done"):
                return d["response"]["generatedVideos"][0]["video"]["uri"]
            time.sleep(5)
        raise TimeoutError(f"Veo job {job_id} timed out")


class KlingVideo(_AsyncVideoBase):
    """Kuaishou Kling 3.0. Best video-per-dollar; multi-shot generation."""

    name = "kling-3.0"
    env_key = "KLING_API_KEY"
    price_per_sec = 0.10
    model_env = "KLING_MODEL"
    default_model = "kling-v3"
    submit_url = "https://api.klingai.com/v1/videos/text2video"

    def _submit(self, prompt: str, seconds: float, seed: int,
                reference_images: list[Path] | None = None) -> str:
        # Kling's image2video is a separate endpoint (/videos/image2video with
        # its own poll path); until that adapter exists the text2video path
        # stays reference-free (base class warns the user).
        payload = {
            "model_name": self.model,
            "prompt": prompt,
            "duration": "5" if seconds <= 5 else "10",
            "aspect_ratio": "9:16",
            "seed": seed % 100000,
        }
        r = requests.post(self.submit_url, json=payload, headers=self._headers(), timeout=120)
        r.raise_for_status()
        return r.json()["data"]["task_id"]

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        url = f"https://api.klingai.com/v1/videos/text2video/{job_id}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = requests.get(url, headers=self._headers(), timeout=60)
            r.raise_for_status()
            d = r.json()["data"]
            if d["task_status"] == "succeed":
                return d["task_result"]["videos"][0]["url"]
            if d["task_status"] == "failed":
                raise RuntimeError(d.get("task_status_msg", "Kling generation failed"))
            time.sleep(5)
        raise TimeoutError(f"Kling job {job_id} timed out")




# --------------------------------------------------------------------------
# Domestic providers (Alibaba Bailian / MiniMax / Vidu / Zhipu)
# --------------------------------------------------------------------------
#
# All four are submit -> poll -> download, same shape as Veo/Kling, so they
# inherit _AsyncVideoBase. Auth differs per vendor and is normalised in
# `_headers`/`_submit` only. Prices below are list-rate USD/sec equivalents
# as of 2026-10 and are the *API* rates, not consumer membership credits --
# the two pools do not mix on any of these platforms.


class BailianVideo(_AsyncVideoBase):
    """Alibaba Cloud Bailian / Tongyi Wanxiang (Wan).

    The interesting one for a portfolio: Wan is the only major family in the
    domestic top-5 that open-sourced its weights (Apache 2.0, 1.3B and 14B).
    So this adapter covers both the hosted API and, in principle, a local
    Wan2.x checkpoint behind the same interface -- which is the whole point
    of the provider layer.
    """

    name = "bailian-wan"
    env_key = "BAILIAN_API_KEY"
    model_env = "BAILIAN_MODEL"
    default_model = "wan2.5-t2v-preview"
    price_per_sec = 0.08  # 720p list-rate equivalent
    submit_url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/video-generation/video-synthesis"

    def _headers(self) -> dict[str, str]:
        # DashScope uses a plain API-Key header, not Bearer.
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json",
                "X-DashScope-Async": "enable"}

    def _submit(self, prompt: str, seconds: float, seed: int,
                reference_images: list[Path] | None = None) -> str:
        payload = {
            "model": self.model,
            "input": {"prompt": prompt},
            "parameters": {
                "size": "720*1280",
                "duration": int(max(3, min(15, seconds))),
            },
        }
        r = requests.post(self.submit_url, json=payload, headers=self._headers(), timeout=120)
        r.raise_for_status()
        return r.json()["output"]["task_id"]

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{job_id}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = requests.get(url, headers=self._headers(), timeout=60)
            r.raise_for_status()
            d = r.json().get("output") or {}
            if d.get("task_status") == "SUCCEEDED":
                return d["video_url"]
            if d.get("task_status") in ("FAILED", "CANCELED"):
                raise RuntimeError(d.get("message", "Bailian generation failed"))
            time.sleep(6)
        raise TimeoutError(f"Bailian job {job_id} timed out")


class MiniMaxVideo(_AsyncVideoBase):
    """MiniMax Hailuo / H3 video generation.

    MiniMax ships *two* incompatible video APIs under the same credentials:

      v1  (Hailuo-02 / Hailuo-2.3 / T2V-01 / T2V-01-Director):
          POST /v1/video_generation -> task_id
          GET  /v1/query/video_generation?task_id= -> status + file_id
          GET  /v1/files/retrieve?file_id=         -> {file:{download_url}}

      v2  (H3 / H3-Highspeed / H3-Lite):
          POST /v2/video_generation -> task_id   (body uses `content`, not `prompt`)
          GET  /v2/query/video_generation?task_id= -> items[]; the matching
               item's `content.url` IS the mp4 (no separate retrieve step)

    The model id selects the API: anything starting with `MiniMax-H3` uses the
    v2 shape; everything else uses v1. Both share the same host root, so the
    only difference is the /v1 vs /v2 path segment.

    Host differs by account region: intl `api.minimax.io`, mainland
    `api.minimaxi.com`. Override the whole base with MINIMAX_BASE_URL. A
    mainland account key is rejected by the intl host with "invalid api key",
    which is the single commonest reason MiniMax "doesn't work".
    """

    name = "minimax-hailuo"
    env_key = "MINIMAX_API_KEY"
    model_env = "MINIMAX_MODEL"
    default_model = "MiniMax-Hailuo-02"
    price_per_sec = 0.045  # 768P list rate
    default_base = "https://api.minimax.io/v1"
    # v2 H3 supports reference-to-video (up to 9 role=reference_image items);
    # v1 only takes a single first_frame_image.
    supports_reference_images = True
    # Model-id prefixes that live on the v2 API (different request/response shape).
    _V2_PREFIXES = ("MiniMax-H3",)

    def __init__(self) -> None:
        self.api_key = os.environ.get(self.env_key, "")
        self.base = (os.environ.get("MINIMAX_BASE_URL", "") or self.default_base).rstrip("/")
        # v2 hangs off the host root, not under /v1.
        self.host_root = re.sub(r"/v\d+$", "", self.base)
        # Group-billed accounts need this; personal keys do not.
        self.group_id = os.environ.get("MINIMAX_GROUP_ID", "")

    @property
    def _use_v2(self) -> bool:
        return any(self.model.startswith(p) for p in self._V2_PREFIXES)

    def _headers(self) -> dict[str, str]:
        h = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.group_id:
            h["GroupId"] = self.group_id
        return h

    def _submit(self, prompt: str, seconds: float, seed: int,
                reference_images: list[Path] | None = None) -> str:
        refs = [Path(p) for p in (reference_images or []) if Path(p).is_file()]
        if self._use_v2:
            # v2 uses `content` (OpenAI-style) not `prompt`, and requires both
            # `ratio` and `resolution`. 1080P is rejected by H3-Lite, so 768P
            # is the safe denominator.
            #
            # Reference-to-video: text + up to 9 reference_image items is how
            # a product's three-view set keeps its identity across shots.
            # first_frame/last_frame and reference_* are mutually exclusive,
            # so uploaded images always go in as references, never as frames.
            content: list[dict] = [{"type": "text", "text": prompt}]
            content += [
                {
                    "type": "image_url",
                    "image_url": {"url": image_data_uri(p)},
                    "role": "reference_image",
                }
                for p in refs[:9]
            ]
            payload = {
                "model": self.model,
                "content": content,
                "ratio": "9:16",
                "resolution": "768P",
                "duration": 6 if seconds <= 6 else 10,
            }
            r = requests.post(
                f"{self.host_root}/v2/video_generation", json=payload, headers=self._headers(), timeout=120
            )
            r.raise_for_status()
            d = r.json()
            # v2 success returns {task_id: ...} directly; errors are
            # {"type":"error","error":{...}}.
            if d.get("type") == "error":
                raise RuntimeError(
                    f"MiniMax submit failed: {(d.get('error') or {}).get('message', d)}"
                )
            task_id = d.get("task_id")
            if not task_id:
                raise RuntimeError(f"MiniMax returned no task_id: {d}")
            return str(task_id)

        payload = {
            "model": self.model,
            "prompt": prompt,
            # 6 or 10, nothing else; 1080P would force 6 and cost 2x, so the
            # resolution stays at the default 768P.
            "duration": 6 if seconds <= 6 else 10,
            "resolution": "768P",
            # The pipeline already writes cinematic prompts; letting the
            # vendor rewrite them makes scene diffs non-deterministic.
            "prompt_optimizer": False,
        }
        if refs:
            # v1 has no reference-to-video mode; the best it can do is treat
            # the first view as the opening frame.
            payload["first_frame_image"] = image_data_uri(refs[0])
        r = requests.post(f"{self.base}/video_generation", json=payload, headers=self._headers(), timeout=120)
        r.raise_for_status()
        d = r.json()
        base_resp = d.get("base_resp") or {}
        if base_resp.get("status_code"):
            raise RuntimeError(f"MiniMax submit failed: {base_resp.get('status_msg', d)}")
        task_id = d.get("task_id")
        if not task_id:
            raise RuntimeError(f"MiniMax returned no task_id: {d}")
        return str(task_id)

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        t0 = time.time()
        if self._use_v2:
            url = f"{self.host_root}/v2/query/video_generation?task_id={job_id}"
            deadline = time.time() + timeout
            while time.time() < deadline:
                r = requests.get(url, headers=self._headers(), timeout=60)
                r.raise_for_status()
                items = r.json().get("items", [])
                # The query returns a list (the task_id filter is not always
                # honoured); find ours by id and keep polling if it is not on
                # this page yet.
                item = next((x for x in items if str(x.get("id")) == str(job_id)), None)
                if item is not None:
                    status = str(item.get("status", "")).lower()
                    self._report(phase="generating", task_id=str(job_id),
                                 status=status, elapsed=round(time.time() - t0, 1))
                    if status in ("succeeded", "success"):
                        url_out = (item.get("content") or {}).get("url")
                        if not url_out:
                            raise RuntimeError(f"MiniMax v2 finished but returned no content.url: {item}")
                        return url_out
                    if status in ("failed", "fail"):
                        raise RuntimeError(f"MiniMax v2 generation failed: {item}")
                else:
                    self._report(phase="generating", task_id=str(job_id),
                                 status="queued", elapsed=round(time.time() - t0, 1))
                time.sleep(6)
            raise TimeoutError(f"MiniMax v2 job {job_id} timed out")

        url = f"{self.base}/query/video_generation?task_id={job_id}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = requests.get(url, headers=self._headers(), timeout=60)
            r.raise_for_status()
            d = r.json()
            status = str(d.get("status", "")).lower()
            self._report(phase="generating", task_id=str(job_id),
                         status=status or "queued", elapsed=round(time.time() - t0, 1))
            if status == "success":
                file_id = d.get("file_id")
                if not file_id:
                    raise RuntimeError(f"MiniMax finished but returned no file_id: {d}")
                return self._retrieve(file_id)
            if status == "fail":
                raise RuntimeError(
                    (d.get("base_resp") or {}).get("status_msg") or f"MiniMax generation failed: {d}"
                )
            time.sleep(6)
        raise TimeoutError(f"MiniMax job {job_id} timed out")

    def _retrieve(self, file_id: str) -> str:
        """file_id -> the actual mp4 URL (v1 only; v2 carries the url directly)."""
        r = requests.get(
            f"{self.base}/files/retrieve?file_id={file_id}", headers=self._headers(), timeout=60
        )
        r.raise_for_status()
        d = r.json()
        url = (d.get("file") or {}).get("download_url") or d.get("download_url")
        if not url:
            raise RuntimeError(f"MiniMax retrieve returned no download_url: {d}")
        return url


class ZhipuVideo(_AsyncVideoBase):
    """Zhipu CogVideoX (open weights) via the hosted BigModel API.

    CogVideoX-5B is one of the few video models you can actually self-host
    on a 24GB card. The hosted API is here so the pipeline works without
    owning a GPU; the local server path is the same interface.
    """

    name = "zhipu-cogvideox"
    env_key = "ZHIPU_API_KEY"
    model_env = "ZHIPU_MODEL"
    default_model = "cogvideox-2"
    price_per_sec = 0.07
    submit_url = "https://open.bigmodel.cn/api/paas/v4/videos/generations"

    def _submit(self, prompt: str, seconds: float, seed: int,
                reference_images: list[Path] | None = None) -> str:
        payload = {"model": self.model, "prompt": prompt}
        r = requests.post(self.submit_url, json=payload, headers=self._headers(), timeout=120)
        r.raise_for_status()
        return r.json()["id"]

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        url = f"https://open.bigmodel.cn/api/paas/v4/async-result/{job_id}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = requests.get(url, headers=self._headers(), timeout=60)
            r.raise_for_status()
            d = r.json()
            if d.get("task_status") == "SUCCESS":
                return d["video_result"][0]["url"]
            if d.get("task_status") == "FAIL":
                raise RuntimeError("CogVideoX generation failed")
            time.sleep(6)
        raise TimeoutError(f"CogVideoX job {job_id} timed out")


# --------------------------------------------------------------------------
# Jimeng (ByteDance / Volcengine)
# --------------------------------------------------------------------------
#
# Two things make this adapter different from the Bearer-token family above:
#
# 1. Auth is the Volcengine HMAC-SHA256 request signature (AK/SK), not a
#    bearer token. It is AWS-SigV4-shaped: derive a signing key through
#    date/region/service, then sign a canonical request. Implemented below
#    because "use the official SDK" is not an option for a dependency-light
#    pipeline, and the algorithm is ~30 lines of hmac/hashlib.
# 2. The result URL expires in about an hour. The base class downloads
#    immediately after polling, which is the correct shape -- but if you ever
#    change generate() to return URLs instead of local files, you will lose
#    clips silently. Jimeng also caps concurrency at 1, so fan it out through
#    the orchestrator's queue rather than threads.

_REGION = "cn-north-1"
_SERVICE = "cv"


def _volc_sign(secret_key: str, date_short: str) -> bytes:
    """Derive the Volcengine signing key: date -> region -> service -> 'request'."""
    import hashlib
    import hmac

    def _h(key: bytes | str, msg: str) -> bytes:
        return hmac.new(key if isinstance(key, bytes) else key.encode(), msg.encode(), hashlib.sha256).digest()

    k_date = _h(secret_key, date_short)
    k_region = _h(k_date, _REGION)
    k_service = _h(k_region, _SERVICE)
    return _h(k_service, "request")


def _volc_authorization(
    method: str,
    query: dict[str, str],
    body: bytes,
    ak: str,
    sk: str,
    now: datetime | None = None,
) -> dict[str, str]:
    """Build the `Authorization` header and supporting headers for visual.volcengineapi.com."""
    import hashlib
    import hmac

    now = now or datetime.utcnow()
    x_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_short = now.strftime("%Y%m%d")

    payload_hash = hashlib.sha256(body).hexdigest()
    # Query string must be sorted by key, URI-encoded, exactly as sent.
    canonical_qs = "&".join(f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(query.items()))
    headers = {
        "content-type": "application/json",
        "host": "visual.volcengineapi.com",
        "x-content-sha256": payload_hash,
        "x-date": x_date,
    }
    signed_headers = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{headers[k]}\n" for k in sorted(headers))
    canonical_request = "\n".join(
        [method, "/", canonical_qs, canonical_headers, signed_headers, payload_hash]
    )
    scope = f"{date_short}/{_REGION}/{_SERVICE}/request"
    string_to_sign = "\n".join(
        ["HMAC-SHA256", x_date, scope, hashlib.sha256(canonical_request.encode()).hexdigest()]
    )
    signing_key = _volc_sign(sk, date_short)
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    headers["authorization"] = (
        f"HMAC-SHA256 Credential={ak}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers


def _uri_encode(s: str) -> str:
    from urllib.parse import quote

    return quote(str(s), safe="-_.~")


class JimengVideo(_AsyncVideoBase):
    """ByteDance Jimeng (即梦) via the Volcengine visual API.

    Text-to-video and image-to-video from the same model family that powers
    the consumer Jimeng product. Two practical notes that bite people:

    - Auth is the Volcengine AK/SK signature, so this needs
      `VOLC_ACCESS_KEY` + `VOLC_SECRET_KEY` (not a single token).
    - Result URLs expire in ~1 hour and concurrency is capped at 1. The
      pipeline downloads the clip the moment polling succeeds, so expiry is
      not a problem in normal operation -- but do not cache URLs.

    `req_key` selects the model generation; `jimeng_vgfm_t2v_l20` is the
    S2.0 Pro text-to-video endpoint. Override via `JIMENG_REQ_KEY` when
    ByteDance moves the identifier (they version these aggressively).
    """

    name = "jimeng"
    price_per_sec = 0.05
    submit_url = "https://visual.volcengineapi.com"
    env_key = "VOLC_ACCESS_KEY"
    env_key_secret = "VOLC_SECRET_KEY"
    # req_key *is* the model selector on this API.
    model_env = "JIMENG_REQ_KEY"
    default_model = "jimeng_vgfm_t2v_l20"

    def __init__(self) -> None:
        self.api_key = os.environ.get(self.env_key, "")
        self.secret_key = os.environ.get(self.env_key_secret, "")

    @property
    def req_key(self) -> str:
        return self.model

    def available(self) -> bool:
        return bool(self.api_key and self.secret_key)

    def _call(self, action: str, payload: dict) -> dict:
        import json as _json

        body = _json.dumps(payload, separators=(",", ":")).encode()
        query = {"Action": action, "Version": "2022-08-31"}
        headers = _volc_authorization("POST", query, body, self.api_key, self.secret_key)
        # Sign over the canonical query; send it on the URL too.
        qs = "&".join(f"{k}={v}" for k, v in sorted(query.items()))
        r = requests.post(f"{self.submit_url}?{qs}", data=body, headers=headers, timeout=120)
        r.raise_for_status()
        return r.json()

    def _submit(self, prompt: str, seconds: float, seed: int,
                reference_images: list[Path] | None = None) -> str:
        payload = {
            "req_key": self.req_key,
            "prompt": prompt[:150],  # S2.0 Pro documents a 150-char cap
            "seed": int(seed) if seed and seed > 0 else -1,
            "aspect_ratio": "9:16",
        }
        d = self._call("CVSync2AsyncSubmitTask", payload)
        code = d.get("code", d.get("status"))
        if code not in (10000, 0, "10000"):
            raise RuntimeError(f"Jimeng submit failed: {d.get('message', d)}")
        task_id = (d.get("data") or {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"Jimeng returned no task_id: {d}")
        return task_id

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            d = self._call("CVSync2AsyncGetResult", {"req_key": self.req_key, "task_id": job_id})
            data = d.get("data") or {}
            status = str(data.get("status", "")).lower()
            if status in ("done", "success", "succeeded", "10000"):
                url = data.get("video_url") or data.get("resp_data")
                if isinstance(url, dict):
                    url = url.get("video_url")
                if not url:
                    # Some generations return JSON-in-string under resp_data.
                    if isinstance(data.get("resp_data"), str) and "video_url" in data["resp_data"]:
                        import json as _json

                        url = _json.loads(data["resp_data"]).get("video_url")
                if not url:
                    raise RuntimeError(f"Jimeng done but no video_url: {data}")
                return url
            if status in ("not_found", "expired", "failed", "error", "cancelled"):
                raise RuntimeError(f"Jimeng job {job_id} ended as {status}: {data}")
            time.sleep(8)
        raise TimeoutError(f"Jimeng job {job_id} timed out")



# --------------------------------------------------------------------------
# Seedance (ByteDance / Volcengine Ark)
# --------------------------------------------------------------------------
#
# Seedance is the video-generation family behind Doubao (豆包) on Volcengine
# Ark. Two things make it different from the sibling Jimeng adapter:
#
# 1. Auth is a plain Bearer API key -- not the AK/SK HMAC-SHA256 signature
#    that Jimeng needs. Key comes from the Ark console. So this adapter is
#    ~15 lines of submit/poll and nothing else.
# 2. Billing is per-token (15 CNY/M tokens for pro, 4.2 for fast), not
#    per-second. `price_per_sec` below is a rough USD equivalent purely so
#    the shared cost ledger has something to report.
#
# Model tiers: "doubao-seedance-2-0-pro" (quality) and
# "doubao-seedance-2-0-mini" (fast/cheap). Override via SEEDANCE_MODEL --
# ByteDance revs these identifiers often and a stale id is the single most
# common failure mode here.
#
# Submit -> poll -> download, so it inherits _AsyncVideoBase unchanged.


class SeedanceVideo(_AsyncVideoBase):
    """ByteDance Seedance via the Volcengine Ark API.

    Text-to-video and image-to-video. Newer and cleaner than Jimeng: plain
    Bearer auth, JSON in/out, status strings 'queued' / 'running' /
    'succeeded' / 'failed'. The returned video URL is a pre-signed link valid
    for 24h, and the base class downloads it immediately, so expiry is not a
    concern in normal operation.
    """

    name = "seedance"
    env_key = "SEEDANCE_API_KEY"
    model_env = "SEEDANCE_MODEL"
    default_model = "doubao-seedance-2-0-pro"
    price_per_sec = 0.05  # rough USD equivalent of token billing
    submit_url = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
    supports_reference_images = True  # first reference image = first frame

    def _submit(self, prompt: str, seconds: float, seed: int,
                reference_images: list[Path] | None = None) -> str:
        content: list[dict] = [{"type": "text", "text": prompt[:600]}]
        if reference_images:
            content.append({
                "type": "image_url",
                "image_url": {"url": image_data_uri(reference_images[0])},
                "role": "first_frame",
            })
        payload = {
            "model": self.model,
            "content": content,
            "ratio": "9:16",  # vertical ad default
            "duration": int(max(2, min(12, seconds))),
            "watermark": False,
        }
        r = requests.post(
            self.submit_url,
            headers=self._headers(),
            json=payload,
            timeout=120,
        )
        r.raise_for_status()
        d = r.json()
        task_id = d.get("id")
        if not task_id:
            raise RuntimeError(f"Seedance returned no task id: {d}")
        return task_id

    def _poll(self, job_id: str, timeout: float = 900.0) -> str:
        url = f"{self.submit_url}/{job_id}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = requests.get(url, headers=self._headers(), timeout=60)
            r.raise_for_status()
            d = r.json()
            status = str(d.get("status", "")).lower()
            if status == "succeeded":
                # Ark nests the link under content.video_url (or data.video_url
                # depending on the model revision); accept either.
                content = d.get("content") or d.get("data") or {}
                if isinstance(content, list):
                    content = content[0] if content else {}
                url_out = content.get("video_url") if isinstance(content, dict) else None
                if not url_out:
                    url_out = d.get("video_url")
                if not url_out:
                    raise RuntimeError(f"Seedance succeeded but no video_url: {d}")
                return url_out
            if status in ("failed", "cancelled", "expired"):
                raise RuntimeError(f"Seedance job {job_id} ended as {status}: {d}")
            time.sleep(5)
        raise TimeoutError(f"Seedance job {job_id} timed out")


# --------------------------------------------------------------------------
# Provider selection
# --------------------------------------------------------------------------

_REGISTRY: dict[str, type] = {
    "veo": VeoVideo,
    "kling": KlingVideo,
    "bailian": BailianVideo,
    "wan": BailianVideo,
    "minimax": MiniMaxVideo,
    "hailuo": MiniMaxVideo,
    "zhipu": ZhipuVideo,
    "cogvideox": ZhipuVideo,
    "jimeng": JimengVideo,
    "dreamina": JimengVideo,
    "seedance": SeedanceVideo,
}

# Probe order when the caller says `auto`. Roughly: cheapest-good first.
_DEFAULT_ORDER = ("seedance", "kling", "jimeng", "veo", "bailian", "minimax", "zhipu")


def required_envs(key: str) -> tuple[str, ...]:
    """Env vars a provider needs before it can be used."""
    cls = _REGISTRY.get(key)
    if cls is None:
        return ()
    out: list[str] = []
    for attr in ("env_key", "env_key_secret"):
        v = getattr(cls, attr, "")
        if v:
            out.append(v)
    return tuple(out)


def build_video_provider(prefer: str | None = None) -> Provider:
    """Pick the first provider that actually has credentials.

    `AVB_VIDEO_PROVIDER` (or `prefer`) forces a specific one; otherwise we
    walk `_DEFAULT_ORDER`. There is no offline fallback: if nothing is
    configured we raise `ProviderUnavailable` listing what to set, because
    silently producing fake footage would ship a broken ad.

    Falling back across *configured* vendors is still the point of the
    abstraction -- when a vendor kills an API (see: Sora 2, Sept 2026) the
    pipeline keeps running on the next one.
    """
    want = (prefer or os.environ.get("AVB_VIDEO_PROVIDER") or "auto").strip().lower()
    order: list[str] = [] if want in ("", "auto") else [want]
    order += [k for k in _DEFAULT_ORDER if k not in order]

    missing: dict[str, tuple[str, ...]] = {}
    for key in order:
        cls = _REGISTRY.get(key)
        if not cls:
            continue
        inst = cls()
        if inst.available():
            if want not in ("", "auto") and key != want:
                # An explicit pick that could not be honoured is worth
                # shouting about: the user asked for Kling and is getting
                # Seedance. Better to keep rendering than to fail, but never
                # do it silently.
                print(
                    f"  !! video provider '{want}' is not configured "
                    f"(needs {', '.join(required_envs(want))}); using '{key}' instead"
                )
            return inst
        missing[key] = required_envs(key)

    if want not in ("", "auto") and want in missing:
        env_list = ", ".join(missing[want]) or "(none)"
        known = ", ".join(sorted(_REGISTRY))
        raise ProviderUnavailable(
            f"video provider '{want}' is not configured. Set: {env_list}. "
            f"Known providers: {known}",
            missing,
        )

    env_list = "; ".join(f"{k}: {', '.join(v) or '(none)'}" for k, v in missing.items())
    raise ProviderUnavailable(
        f"No video provider configured -- set at least one of: {env_list}",
        missing,
    )


# ---------------------------------------------------------------------------
# Thread-local progress plumbing
# ---------------------------------------------------------------------------
# generate() can run concurrently on several worker threads (one per scene in
# Pipeline.synth). A shared self.progress_cb attribute would let scene B's
# reports clobber scene A's observer, so the callback lives in thread-local
# state for the duration of each generate() call instead.

_tls = threading.local()


@contextlib.contextmanager
def progress_scope(cb):
    """Temporarily bind the calling thread's progress callback for generate()."""
    prev = getattr(_tls, "progress_cb", None)
    _tls.progress_cb = cb
    try:
        yield
    finally:
        _tls.progress_cb = prev


def report_progress(**info) -> None:
    """Invoke the calling thread's bound progress callback, if any (best-effort).

    The callback is stored in thread-local state by progress_scope, so concurrent
    generate() calls each report to their own observer instead of racing on a
    shared attribute.
    """
    cb = getattr(_tls, "progress_cb", None)
    if cb is None:
        return
    try:
        cb(info)
    except Exception:  # noqa: BLE001 - progress must never kill a render
        pass
