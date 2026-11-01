"""Web application server for dtc-ads-video-ai-generator.

FastAPI backend that wraps the avb pipeline into a web product:
script in -> video out -> conversational edit -> export for any platform.

Run:  python -m web.server   (or  uvicorn web.server:app --port 8787)
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import re
import shutil
import threading
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

# --- avb imports -----------------------------------------------------------
from avb.config import (
    Config, load_config, load_dotenv, config_set, env_path, find_config_path,
    write_env, save_config,
)
from avb.parse import parse_script, parse_script_llm
from avb.brand import apply_brand_to_project, DEFAULT_BRAND_KEY
from avb.brief import generate_scripts_detailed
from avb.pipeline import Pipeline
from avb.edit import apply_plan
from avb.llm_edit import plan_edit_llm
from avb.edit import plan_edit
from avb.provider_specs import (
    VIDEO_SPECS, VOICE_SPECS, LLM_SPECS,
    spec_for, env_status, mask,
)
from web.history_db import (
    record_render as db_record_render,
    list_renders as db_list_renders,
    get_render as db_get_render,
    record_script as db_record_script,
    list_scripts as db_list_scripts,
    get_script as db_get_script,
    select_script as db_select_script,
    init as _init_history,
    migrate_json as _migrate_history,
    upsert_brand_kit as db_upsert_brand_kit,
    get_brand_kit as db_get_brand_kit,
    list_brand_kits as db_list_brand_kits,
    create_variant_set as db_create_variant_set,
    get_variant_set as db_get_variant_set,
    list_variant_sets as db_list_variant_sets,
    aggregate_cost as db_aggregate_cost,
    find_active_renders as db_find_active_renders,
    set_render_status as db_set_render_status,
)

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Remember the main loop so worker threads can schedule broadcasts, and
    re-queue any render that was 'running' when we last stopped (crash
    recovery)."""
    global _main_loop
    _main_loop = asyncio.get_running_loop()
    _recover_active_renders(_main_loop)
    yield


def _recover_active_renders(loop) -> list[str]:
    """Re-queue renders that were 'running' when the server last stopped.

    A crash / restart leaves them with no live in-memory job, so they would
    sit forever as 'running' in history. We flip each to 'interrupted' (a
    terminal state, so a *future* restart won't re-recover it) and launch a
    resume that re-runs the film. The content-addressed cache makes that
    cheap: any scene that already finished on disk is reused, not re-billed,
    so recovering a half-done render costs only the scenes that never landed.
    """
    recovered = []
    for rec in db_find_active_renders():
        rid = rec["render_id"]
        db_set_render_status(rid, "interrupted")
        new_job = _new_job("render")
        loop.run_in_executor(None, _run_resume, new_job, rid, None, None)
        recovered.append(rid)
    return recovered


app = FastAPI(title="AI Video Builder", version="0.3.0", lifespan=lifespan)

STATIC = Path(__file__).parent / "static"
# Directory that finished MP4s are served from. Resolved once at import time so
# the traversal check in serve_video() cannot be defeated by a relative parent
# that changes with the process cwd.
VIDEO_DIR = Path("demo/out/web").resolve()
# React build output (web/frontend -> web/static/dist). If it is missing we
# fall back to the hand-written legacy page, so the server always boots.
DIST = STATIC / "dist"
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

# ---------------------------------------------------------------------------
# Product reference images (three-view uploads)
# ---------------------------------------------------------------------------
# Uploaded product photos that steer video generation (MiniMax H3 v2
# reference-to-video keeps the product identity across scenes). Saved under
# .avb_web/uploads and served back for thumbnail previews.
UPLOAD_DIR = Path(".avb_web/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/api/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")

# Vendor limits: MiniMax H3 v2 allows <= 9 reference images, each <= 30 MB,
# body <= 64 MB total. 10 MB / 9 images keeps us far inside all three.
MAX_UPLOADS = 9
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# job_id -> resolved reference image paths, so edit re-renders inherit them.
_ref_images_by_job: dict[str, list[str]] = {}

# job_id -> render flags (voice/captions), so conversational edit re-renders
# inherit the same audio/text layers the user last picked.
_render_flags_by_job: dict[str, dict] = {}

_ALLOWED_IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)


def _upload_ext(raw: bytes, fallback: str) -> str:
    """Extension from magic bytes first, filename second (bg.png taught us
    vendors and users alike hand over video bytes wearing an image name)."""
    for magic, ext in _MAGIC:
        if raw.startswith(magic):
            return ext
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return ".webp"
    return fallback if fallback in _ALLOWED_IMG_EXT else ""


@app.post("/api/upload")
async def upload_images(files: list[UploadFile] = File(...)):
    """Store product reference images; returns {path, name, url} per file.

    `path` is what the render request sends back; `url` is for the <img>
    thumbnail. Magic-byte validated: anything that is not a real image
    (e.g. a video renamed .png) is rejected here rather than detonating
    three layers downstream.
    """
    if len(files) > MAX_UPLOADS:
        raise HTTPException(400, f"一次最多上传 {MAX_UPLOADS} 张参考图")
    out = []
    for f in files:
        raw = await f.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(400, f"{f.filename or 'image'} 超过 10MB 限制")
        ext = _upload_ext(raw, (Path(f.filename or "").suffix or "").lower())
        if not ext:
            raise HTTPException(
                400,
                f"{f.filename or 'image'} 不是可识别的图片（支持 PNG/JPG/WEBP/GIF/BMP）",
            )
        name = f"{uuid.uuid4().hex[:12]}{ext}"
        (UPLOAD_DIR / name).write_bytes(raw)
        out.append({
            "path": str(UPLOAD_DIR / name),
            "name": f.filename or name,
            "url": f"/api/uploads/{name}",
            "size": len(raw),
        })
    return {"files": out}


def _resolve_reference_images(paths: list[str]) -> list[str]:
    """Keep only real image files that live inside UPLOAD_DIR.

    The containment check matters: a forged reference path would otherwise
    get base64-encoded straight into an outbound vendor request.
    """
    root = UPLOAD_DIR.resolve()
    out = []
    for p in paths or []:
        try:
            rp = Path(p).resolve()
        except (OSError, ValueError):
            continue
        if rp.parent == root and rp.is_file() and rp.suffix.lower() in _ALLOWED_IMG_EXT:
            out.append(str(rp))
    return out

# ---------------------------------------------------------------------------
# Job manager
# ---------------------------------------------------------------------------
_jobs: dict[str, dict] = {}
_ws_clients: dict[str, list[WebSocket]] = {}
# Live Project IR per job. Each render also persists its IR as project_json
# (see history_db), so a render can be resumed after a restart via
# Project.from_json(). We still keep the object in memory for the in-session
# edit/retry flow, which is both simpler and exact for a local single-process
# app. _state_lock serialises the cross-thread mutations of the shared
# job/project maps; the single-flight guard below enforces "one task at a time".
_projects: dict[str, object] = {}
_state_lock: threading.Lock = threading.Lock()


class JobProgress(BaseModel):
    job_id: str
    stage: str
    progress: float  # 0.0 - 1.0
    message: str
    done: bool = False
    error: str | None = None
    video_url: str | None = None
    cost: float | None = None


# The pipeline runs in a worker thread (via run_in_executor), but WebSocket
# clients live on the main asyncio loop. Cross-thread notification therefore
# has to go through run_coroutine_threadsafe against the loop captured at
# startup -- calling asyncio.get_event_loop() inside the worker raises
# "There is no current event loop in thread", which used to kill every job
# at the very first progress update.
_main_loop: asyncio.AbstractEventLoop | None = None


async def _broadcast(job_id: str, data: dict):
    for ws in list(_ws_clients.get(job_id, [])):
        try:
            await ws.send_json(data)
        except Exception:
            pass


def _update_job(job_id: str, **kw):
    # Called from worker threads (ticker + completion callbacks) for the same
    # job, so the update+snapshot must be atomic against concurrent callers.
    with _state_lock:
        _jobs[job_id].update(kw)
        snapshot = dict(_jobs[job_id])
    if _main_loop is None or _main_loop.is_closed():
        return
    try:
        asyncio.run_coroutine_threadsafe(_broadcast(job_id, snapshot), _main_loop)
    except RuntimeError:
        pass  # loop gone; pollers still see the state via /api/jobs/{id}


# ---------------------------------------------------------------------------
# Background generation jobs (scripts / plan)
#
# LLM script-writing and scene planning are single blocking calls that take
# 10-60s. Handing the user a spinner with no feedback felt frozen; these wrap
# the work in the same job machinery as renders, with a *smoothed* progress
# ticker: real progress is unknowable inside one LLM call, so we creep toward
# 92% and let the completing worker set the truth (100% or error). The
# "fake" numbers only ever move forward and never claim completion early --
# they exist to answer "is it still working?", not to mislead.
# ---------------------------------------------------------------------------


def _new_job(kind: str, message: str = "Job queued") -> str:
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = {
        "job_id": job_id,
        "kind": kind,
        "stage": "queued",
        "progress": 0.02,
        "message": message,
        "done": False,
        "error": None,
        "video_url": None,
        "cost": None,
        "stats": None,
        "result": None,
    }
    return job_id


def _active_job_id() -> str | None:
    """Return the id of any job that has not finished yet, else None."""
    for jid, job in _jobs.items():
        if not job.get("done"):
            return jid
    return None


def _require_no_active_job():
    """Enforce the "one task at a time" invariant.

    A render and a generation (or two renders) running together would both hit
    the worker thread pool and mutate the shared _projects/_plans maps keyed by
    plan id, corrupting each other's state. The UI serialises steps, but the
    API must defend itself: a second start while one is active is rejected.
    """
    active = _active_job_id()
    if active is not None:
        raise HTTPException(
            409,
            "另一个任务正在运行（%s），请等待它完成后再开始新任务。" % active,
        )


def _ticker(job_id: str, stop: threading.Event, phases: list[tuple[str, str]], cap: float = 0.92):
    """Smooth progress + rotating phase messages while real work runs."""
    t0 = time.time()
    progress = 0.03
    i = 0
    while not stop.wait(2.0):
        progress = min(cap, progress + (cap - progress) * 0.10 + 0.008)
        phase, fallback = phases[min(i // 3, len(phases) - 1)]
        _update_job(
            job_id,
            stage=phase,
            progress=round(progress, 3),
            message=fallback,
            stats={"phase": phase, "elapsed": round(time.time() - t0, 1)},
        )
        i += 1


def _finish_job(job_id: str, result: dict):
    stop = _tickers.pop(job_id, None)
    if stop:
        stop.set()
    _update_job(job_id, stage="done", progress=1.0, message="Complete",
                done=True, result=result)


def _fail_job(job_id: str, err: str):
    stop = _tickers.pop(job_id, None)
    if stop:
        stop.set()
    _update_job(job_id, stage="error", progress=0, done=True, error=err)


_tickers: dict[str, threading.Event] = {}


# ---------------------------------------------------------------------------
# Render history
#
# Every render (initial or a retry-from-failure) is recorded so the user can
# revisit past videos, see which scenes degraded, and resume a partial run
# without re-spending money on the scenes that already succeeded. The record
# is persisted to a SQLite store (.avb_web/history.db) so history survives a
# server restart -- the output mp4s are already on disk, so losing the index
# on restart would be silly.
# ---------------------------------------------------------------------------

def _record_render(*, render_id: str, job_id: str, plan_id: str, name: str,
                   config: dict, project: object | None, report: object | None,
                   status: str, parent_id: str | None = None,
                   scenes: list[dict] | None = None,
                   warnings: list[dict] | None = None) -> dict:
    """Persist one render (or retry) to the SQLite history store.

    `scenes` / `warnings` may be passed explicitly (the hard-failure path,
    where there is no RunReport yet but the pipeline has already logged which
    scenes degraded). When omitted they are derived from `report`.
    """
    if scenes is None:
        scenes = []
        if project is not None and report is not None:
            bad = {w.get("scene_id"): w for w in (getattr(report, "warnings", []) or [])}
            for s in getattr(project, "scenes", []):
                w = bad.get(s.id)
                if w:
                    st = "skipped" if w.get("skipped") else "degraded"
                else:
                    st = "ok"
                scenes.append({"id": s.id, "index": s.index, "status": st})
    if warnings is None:
        warnings = [
            {"scene_index": w.get("index"), "error": w.get("error"),
             "scope": w.get("scope"), "skipped": w.get("skipped")}
            for w in (getattr(report, "warnings", []) or [])
        ] if report else []
    video_url = None
    if report is not None and getattr(report, "video", None):
        video_url = f"/api/video/{Path(report.video).name}"
    rec = {
        "render_id": render_id,
        "job_id": job_id,
        "plan_id": plan_id,
        "name": name,
        "created_at": time.time(),
        "status": status,  # done | partial | failed
        "video_url": video_url,
        "video_path": str(getattr(report, "video", "")) if report else None,
        "config": config,
        "scenes": scenes,
        "cost": float(getattr(report, "cost_usd", 0.0) or 0.0) if report else 0.0,
        "total_duration": float(getattr(report, "total_duration", 0.0) or 0.0) if report else 0.0,
        "warnings": warnings,
        "parent_id": parent_id,
        "kind": "retry" if parent_id else "render",
        # Full Project IR serialised so the render can be resumed after a
        # server restart, when the in-memory project dict is long gone. The
        # IR round-trips losslessly through to_json()/from_json().
        "project_json": project.to_json() if project is not None else None,
    }
    db_record_render(rec)
    return rec


def _record_script(*, script_id: str, job_id: str, product: str,
                   category: str | None, audience: str | None, tone: str | None,
                   language: str, drafts: list[dict],
                   traits: str = "",
                   selected_type: str | None = None,
                   selected_narration: str | None = None) -> dict:
    """Persist one script generation to the SQLite history store."""
    rec = {
        "script_id": script_id,
        "job_id": job_id,
        "product": product,
        "category": category,
        "audience": audience,
        "tone": tone,
        "language": language,
        "traits": traits,
        "created_at": time.time(),
        "drafts": drafts,
        "selected_type": selected_type,
        "selected_narration": selected_narration,
    }
    db_record_script(rec)
    return rec


def _video_progress_cb(job_id: str, n_total: int):
    """Build the per-scene progress callback shared by render + resume."""

    def _cb(info: dict):
        idx = int(info.get("scene_index") or 0)
        phase = info.get("phase", "")
        if phase == "scene_done":
            frac = idx / max(1, n_total)
            _update_job(job_id, stage="rendering",
                        progress=round(0.30 + 0.65 * frac, 3),
                        message=f"Scene {idx}/{n_total} done",
                        stats={"phase": "scene_done", "scene_index": idx,
                               "scene_total": n_total, "cached": info.get("cached")})
            return
        frac = (idx - 1) / max(1, n_total) if idx > 1 else 0.0
        stats = {"phase": phase}
        for k in ("prompt_chars", "task_id", "status", "elapsed", "received_mb",
                  "provider", "scene_index", "scene_total", "scene_id", "model"):
            if info.get(k) is not None:
                stats[k] = info.get(k)
        _update_job(job_id, stage="rendering",
                    progress=round(0.30 + 0.65 * frac, 3), stats=stats)

    return _cb


def _start_ticker(job_id: str, phases: list[tuple[str, str]]):
    stop = threading.Event()
    _tickers[job_id] = stop
    threading.Thread(target=_ticker, args=(job_id, stop, phases), daemon=True).start()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class GenerateRequest(BaseModel):
    script: str
    name: str = "untitled"
    style: str = "dr_vsl"
    template: str = "pain_hook"
    aspect: str = "9:16"
    fps: int = 30
    draft: bool = False
    use_llm: bool = True
    video_provider: str | None = None  # override
    voice_provider: str | None = None
    out_name: str | None = None
    resolution: str = "1080p"
    product: str = ""
    product_traits: str = ""
    brand: dict | None = None
    brand_product: str | None = None


class EditRequest(BaseModel):
    job_id: str | None = None
    project_json: str | None = None  # serialized IR
    instruction: str
    use_llm: bool = True
    # False -> only report the plan (preview what would change).
    # True  -> apply it, invalidate, and re-render just the stale scenes.
    apply: bool = False
    draft: bool = False
    out_name: str | None = None


class ConfigUpdate(BaseModel):
    key: str  # dotted: "llm.model", "video.provider"
    value: str


class SettingsUpdate(BaseModel):
    """Provider selection + credentials from the settings page.

    `keys` is env-var -> value (secrets). Empty values are ignored, so the
    form can be submitted without retyping (and without wiping) a key that
    is already set.
    """
    video_provider: str | None = None
    voice_provider: str | None = None
    llm_provider: str | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    video_model: str | None = None
    voice_model: str | None = None
    keys: dict[str, str] = {}


# ---------------------------------------------------------------------------
# Routes: frontend
# ---------------------------------------------------------------------------
@app.api_route("/", methods=["GET", "HEAD"])
async def index():
    # Starlette does not answer HEAD for a GET-only route, and the IDE's
    # preview panel probes with HEAD -- without this it concluded nothing was
    # listening on a perfectly healthy server.
    if DIST.is_dir():
        return FileResponse(str(DIST / "index.html"))
    return FileResponse(str(STATIC / "index.html"))


@app.get("/legacy")
async def legacy():
    """The pre-React single-file UI, kept as a fallback."""
    return FileResponse(str(STATIC / "index.html"))


# ---------------------------------------------------------------------------
# Routes: config
# ---------------------------------------------------------------------------
@app.get("/api/config")
async def get_config():
    cfg = load_config()
    return {
        "llm": {
            "provider": cfg.llm.provider,
            "model": cfg.llm.model,
            "base_url": cfg.llm.base_url,
            "api_key_set": bool(cfg.llm.api_key),
            "max_tokens": cfg.llm.max_tokens,
        },
        "voice": {
            "provider": cfg.voice.provider,
            "voice": cfg.voice.voice,
            "speed": cfg.voice.speed,
            "energy": cfg.voice.energy,
        },
        "video": {
            "provider": cfg.video.provider,
            "model": cfg.video.model,
            "max_usd_per_run": cfg.video.max_usd_per_run,
        },
        "render": {
            "width": cfg.render.width,
            "height": cfg.render.height,
            "fps": cfg.render.fps,
            "crf": cfg.render.crf,
        },
    }


@app.post("/api/config")
async def set_config(req: ConfigUpdate):
    """Write a key into avb.toml (persists across restarts)."""
    from avb.config import config_set, find_config_path
    path = find_config_path() or Path("avb.toml")
    try:
        changed, msg = config_set(path, req.key, req.value)
        if not changed:
            raise HTTPException(400, msg)
        return {"ok": True, "message": msg}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, str(e))


# ---------------------------------------------------------------------------
# Routes: provider settings (the page that was missing)
# ---------------------------------------------------------------------------
# Non-secret choices go to avb.toml; secrets go to .env. Both are written
# line-by-line so existing comments survive, and os.environ is updated in
# process so a save takes effect without restarting the server.
_LLM_PRESETS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-flash",
                 "api_key_env": "DEEPSEEK_API_KEY"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
               "api_key_env": "OPENAI_API_KEY"},
    "moonshot": {"base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k",
                 "api_key_env": "MOONSHOT_API_KEY"},
    "zhipu": {"base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4.6",
               "api_key_env": "ZHIPU_API_KEY"},
    "qwen": {"base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
             "api_key_env": "BAILIAN_API_KEY"},
    # "compat" deliberately has no preset: the whole point is that the user
    # types the base URL, so auto-filling it would defeat the field.
}


def _spec_payload(specs, selected: str | None = None, model_values: dict[str, str] | None = None):
    """Serialize specs for the settings page.

    `model_values` overrides the current model per spec key, for values that
    live somewhere other than the spec's env var (the LLM model lives in
    avb.toml, for instance).
    """
    out = []
    for s in specs:
        if model_values and s.key in model_values:
            current = model_values[s.key] or ""
        else:
            current = os.environ.get(s.model_env, "") if s.model_env else ""
        out.append({
            "key": s.key,
            "label": s.label,
            "kind": s.kind,
            "ready": s.ready(),
            "missing": s.missing(),
            "console": s.console,
            "notes": s.notes,
            "models": list(s.models),
            "model_env": s.model_env,
            "model_value": current,
            "envs": env_status(s),
            "selected": selected == s.key,
        })
    return out


@app.get("/api/settings")
async def get_settings():
    """Everything the settings page needs: what's available, what's missing."""
    cfg = load_config()
    from avb.assets.voice import build_voice_provider
    from avb.assets.video import build_video_provider

    try:
        video = build_video_provider(cfg.video.provider)
        video_active, video_ready = video.name, True
    except Exception as e:  # ProviderUnavailable
        video_active, video_ready = None, False
        video_error = str(e)
    else:
        video_error = ""

    voice = build_voice_provider(cfg.voice.provider)

    # `auto` means "whatever is configured"; resolve it for display so the
    # dropdown does not show a mode the user cannot reason about.
    def _resolve(kind, want):
        if want and want != "auto":
            return want
        for s in (VIDEO_SPECS if kind == "video" else VOICE_SPECS):
            if s.ready():
                return s.key
        return "auto"

    return {
        "video": {
            "selected": cfg.video.provider,
            "resolved": _resolve("video", cfg.video.provider),
            "active": video_active,
            "ready": video_ready,
            "error": video_error,
            "specs": _spec_payload(VIDEO_SPECS, _resolve("video", cfg.video.provider)),
        },
        "voice": {
            "selected": cfg.voice.provider,
            "resolved": voice.name.replace("-tts", "") if voice else "auto",
            "active": voice.name,
            "ready": voice.available(),
            "specs": _spec_payload(
                VOICE_SPECS, cfg.voice.provider,
                # The chosen voice is a config value (voice.voice), not an
                # env var, so hand it to the UI explicitly.
                {s.key: cfg.voice.voice for s in VOICE_SPECS if s.model_env},
            ),
        },
        "llm": {
            "selected": cfg.llm.provider,
            "model": cfg.llm.model,
            "base_url": cfg.llm.base_url,
            "api_key_env": cfg.llm.api_key_env,
            "api_key_set": bool(cfg.llm.api_key),
            "api_key_masked": mask(cfg.llm.api_key),
            "specs": _spec_payload(
                LLM_SPECS, cfg.llm.provider, {s.key: cfg.llm.model for s in LLM_SPECS}
            ),
        },
        "env_file": str(env_path()),
        "toml_file": str(find_config_path() or Path("avb.toml")),
    }


@app.post("/api/settings")
async def update_settings(req: SettingsUpdate):
    """Persist provider choice + credentials, then re-probe.

    Returns the same shape as GET so the UI can just re-render.
    """
    cfg_path = find_config_path() or Path("avb.toml")
    if not cfg_path.is_file():
        save_config(load_config(), cfg_path)
    written_toml: list[str] = []
    written_env: list[str] = []

    def _toml(dotted: str, value: str):
        try:
            changed, _ = config_set(cfg_path, dotted, value)
            if changed:
                written_toml.append(dotted)
        except Exception:
            pass

    if req.video_provider:
        _toml("video.provider", req.video_provider)
    if req.voice_provider:
        _toml("voice.provider", req.voice_provider)
    if req.llm_provider:
        _toml("llm.provider", req.llm_provider)
        preset = _LLM_PRESETS.get(req.llm_provider.strip().lower())
        if preset and not req.llm_base_url:
            _toml("llm.base_url", preset["base_url"])
        if preset and not req.llm_model:
            _toml("llm.model", preset["model"])
        if preset:
            _toml("llm.api_key_env", preset["api_key_env"])
    if req.llm_model:
        _toml("llm.model", req.llm_model)
    if req.llm_base_url:
        _toml("llm.base_url", req.llm_base_url)

    # Secrets -> .env, and into os.environ so they apply immediately.
    secrets: dict[str, str] = {}
    for k, v in (req.keys or {}).items():
        v = (v or "").strip()
        if not v or not re.fullmatch(r"[A-Z][A-Z0-9_]*", k or ""):
            continue  # blank = leave as-is; non-env-shaped = ignore
        secrets[k] = v

    if req.video_model:
        # per-provider model override, e.g. SEEDANCE_MODEL / MINIMAX_MODEL
        sel = (req.video_provider or load_config().video.provider or "").lower()
        spec = spec_for("video", sel)
        if spec and spec.model_env:
            secrets[spec.model_env] = req.video_model.strip()

    if req.voice_model:
        # AVB_VOICE is the one env var the voice adapters read for the voice
        # id; it also mirrors [voice].voice in avb.toml.
        sel = (req.voice_provider or load_config().voice.provider or "").lower()
        spec = spec_for("voice", sel)
        env_name = (spec.model_env if spec and spec.model_env else "AVB_VOICE")
        if req.voice_model.strip():
            secrets[env_name] = req.voice_model.strip()

    if secrets:
        write_env(secrets)
        os.environ.update(secrets)
        written_env = sorted(secrets)

    # Provider objects cache their key at construction time, so drop any
    # memoised client that would otherwise keep the old credential.
    try:
        from avb import llm as _llm
        _llm.get_client(load_config())
    except Exception:
        pass

    fresh = await get_settings()
    fresh["written"] = {"toml": written_toml, "env": written_env}
    return fresh


# ---------------------------------------------------------------------------
# Routes: providers
# ---------------------------------------------------------------------------
@app.get("/api/templates")
async def list_templates():
    """The DTC ad-template menu (UGC 口播 / 产品特写 / 对比类 / 痛点钩子 ...)."""
    from avb.templates import template_keys

    return {"templates": template_keys(), "default": "pain_hook"}


# ---------------------------------------------------------------------------
# Routes: brand constraint layer
# ---------------------------------------------------------------------------
class BrandRequest(BaseModel):
    product: str = ""  # empty => the global default kit
    brand: dict = {}


@app.get("/api/brand")
async def get_brand(product: str = ""):
    """Return the stored brand kit for a product, or the global default.

    ``product`` is optional: when omitted (or not found) the global default
    kit is returned. Always 200 with a ``brand`` payload (possibly empty).
    """
    key = product.strip() or DEFAULT_BRAND_KEY
    row = db_get_brand_kit(key)
    if not row:
        row = db_get_brand_kit(DEFAULT_BRAND_KEY)
    return {
        "product_key": (row or {}).get("product_key", key),
        "name": (row or {}).get("name", ""),
        "brand": (row or {}).get("brand", {}),
    }


@app.put("/api/brand")
async def put_brand(req: BrandRequest):
    """Store (upsert) a brand kit for a product, or the global default.

    ``product`` empty => the global default kit. The ``brand`` dict is stored
    as-is (partial kits are fine; the IR fills the rest from defaults).
    """
    key = req.product.strip() or DEFAULT_BRAND_KEY
    db_upsert_brand_kit(key, req.brand.get("name", "") if isinstance(req.brand, dict) else "", req.brand or {})
    return {"ok": True, "product_key": key}


@app.get("/api/brand/list")
async def list_brands():
    """All stored brand kits (for the brand picker UI)."""
    return {"kits": db_list_brand_kits()}


# ---------------------------------------------------------------------------
# Routes: variant sets (A/B testing)
# ---------------------------------------------------------------------------
class VariantSetRequest(BaseModel):
    product: str = ""
    name: str = ""
    script: str
    count: int = 5
    language: str = "en"
    # Optional pools to draw combinations from; sensible defaults applied.
    styles: list[str] | None = None
    templates: list[str] | None = None
    voices: list[str] | None = None
    frameworks: list[str] | None = None


_DEFAULT_STYLES = ["dr_vsl", "ugc", "comic", "vector", "cinematic"]
_DEFAULT_TEMPLATES = ["pain_hook", "ugc_talk", "product_hero", "comparison", "talking_expert"]
_DEFAULT_VOICES = ["adam", "aria", "bella", "antoni"]
_DEFAULT_FRAMEWORKS = ["pain_solution", "review_proof", "grass_planting", "story_drama"]


@app.post("/api/variant-sets")
async def create_variant_set_endpoint(req: VariantSetRequest):
    """Generate N ad variants of one product for A/B testing.

    Each variant is a distinct SceneGraph IR produced by combining a template,
    a visual style, a voice and a brief framework. Generation is plan-level
    only (no TTS / no video), so it is cheap and runs synchronously. The
    product's stored brand kit is applied so the variants stay on-style.
    """
    from avb.parse import parse_script

    script = (req.script or "").strip()
    if not script:
        raise HTTPException(400, "请先粘贴广告脚本")
    count = max(1, min(int(req.count or 5), 20))

    styles = req.styles or _DEFAULT_STYLES
    templates = req.templates or _DEFAULT_TEMPLATES
    voices = req.voices or _DEFAULT_VOICES
    frameworks = req.frameworks or _DEFAULT_FRAMEWORKS

    set_id = uuid.uuid4().hex[:12]
    name = req.name.strip() or f"variants-{set_id}"
    members: list[dict] = []
    for i in range(count):
        tpl = templates[i % len(templates)]
        style = styles[i % len(styles)]
        voice = voices[i % len(voices)]
        fw = frameworks[i % len(frameworks)]
        label = f"{tpl} · {style} · {voice}"
        try:
            proj = parse_script(
                script, name=f"{name}-{i+1}", style=style, template=tpl,
                script_language=req.language, product=req.product,
            )
        except Exception as e:
            raise HTTPException(400, f"变体 {i+1} 解析失败：{e}")
        # Keep brand consistency across the whole set (P2 integration).
        apply_brand_to_project(proj, _resolve_brand({"brand_product": req.product} if req.product else {}))
        for s in proj.scenes:
            s.vo.voice = voice
        members.append({
            "member_id": uuid.uuid4().hex[:12],
            "idx": i,
            "label": label,
            "template": tpl,
            "style": style,
            "voice": voice,
            "framework": fw,
            "script": script,
            "project_json": proj.to_json(),
            "status": "planned",
        })

    params = {
        "count": count, "styles": styles, "templates": templates,
        "voices": voices, "frameworks": frameworks, "language": req.language,
    }
    db_create_variant_set(set_id, req.product, name, script, params, members)
    result = db_get_variant_set(set_id) or {}
    result["members"] = [
        {**m, "scenes": _member_scene_count(m)} for m in result.get("members", [])
    ]
    return result


def _member_scene_count(member: dict) -> int:
    try:
        pj = json.loads(member["project_json"] or "{}")
        return len(pj.get("scenes", []))
    except Exception:
        return 0


@app.get("/api/variant-sets")
async def list_variant_sets_endpoint():
    """All variant sets, newest first, with member counts."""
    return {"sets": db_list_variant_sets()}


@app.get("/api/variant-sets/{set_id}")
async def get_variant_set_endpoint(set_id: str):
    s = db_get_variant_set(set_id)
    if not s:
        raise HTTPException(404, "Variant set not found")
    s["members"] = [{**m, "scenes": _member_scene_count(m)} for m in s.get("members", [])]
    return s


@app.get("/api/variant-sets/{set_id}/export")
async def export_variant_set(set_id: str):
    """Download the whole set as a zip: each variant's IR + a CSV summary."""
    s = db_get_variant_set(set_id)
    if not s:
        raise HTTPException(404, "Variant set not found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", _variant_readme(s))
        zf.writestr("variants.csv", _variant_csv(s))
        for m in s.get("members", []):
            zf.writestr(
                f"variants/{m['member_id']}.json",
                m.get("project_json") or "{}",
            )
    buf.seek(0)
    headers = {"Content-Disposition": f'attachment; filename="variant-set-{set_id}.zip"'}
    return Response(buf.getvalue(), media_type="application/zip", headers=headers)


def _variant_readme(s: dict) -> str:
    lines = [
        f"Variant set: {s.get('name')}  (id {s.get('set_id')})",
        f"Product: {s.get('product') or '(none)'}",
        f"Members: {len(s.get('members', []))}",
        "",
        "Each variant is a SceneGraph IR (variants/<id>.json) produced by",
        "combining a template, a visual style, a voice and a brief framework.",
        "Import any of them back into the builder to render it.",
        "",
        "Combination legend:",
    ]
    for m in s.get("members", []):
        lines.append(f"  {m['idx']+1:02d}. {m['label']}  (framework: {m.get('framework')})")
    return "\n".join(lines)


def _variant_csv(s: dict) -> str:
    import csv

    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["idx", "label", "template", "style", "voice", "framework", "scenes", "status"])
    for m in s.get("members", []):
        w.writerow([
            m["idx"] + 1, m["label"], m["template"], m["style"],
            m["voice"], m.get("framework"), _member_scene_count(m), m.get("status"),
        ])
    return out.getvalue()


@app.get("/api/providers")
async def list_providers():
    from avb.assets.voice import build_voice_provider
    from avb.assets.video import build_video_provider, _REGISTRY as VREG

    voice = build_voice_provider()

    v_status = {}
    for name, cls in VREG.items():
        try:
            inst = cls()
            v_status[name] = {"available": inst.available(), "class": cls.__name__}
        except Exception:
            v_status[name] = {"available": False, "class": cls.__name__}

    try:
        video = build_video_provider()
        active, err = video.name, None
    except Exception as e:
        active, err = None, str(e)

    return {
        "voice": {"active": voice.name, "available": voice.available()},
        "video": {"active": active, "error": err, "providers": v_status},
    }


@app.get("/api/cost")
async def get_cost():
    """Aggregate billed-API spend across every recorded render.

    Returns total, a per-provider breakdown (provider read from each render's
    stored config) and a per-day series. Lets the user see where the video
    generation dollars went without poking the SQLite file directly.
    """
    return db_aggregate_cost()


# ---------------------------------------------------------------------------
# Routes: plan -> confirm -> render
#
# Generation is deliberately split in two. `/api/plan` turns a script into
# scenes (copy + visual prompts) and stops there; `/api/render` is what
# actually spends money on TTS and video. Putting a confirmation step between
# them is the whole point: the model's first guess at scene copy is a draft,
# and regenerating eight clips because a line read wrong costs real dollars.
# ---------------------------------------------------------------------------
# A plan is a *proposal* the user has not approved yet, so it is kept apart
# from _projects (which holds IRs already promoted into a render job).
_plans: dict[str, dict] = {}


class PlanRequest(BaseModel):
    script: str
    name: str = "untitled"
    style: str = "dr_vsl"
    template: str = "pain_hook"
    use_llm: bool = True
    target_seconds: float | None = None
    # Language the script is written in. The director model is told so it
    # never switches the spoken copy into another language.
    language: str = "en"
    # What the product is. Threads a "subject on screen" anchor into every
    # visual prompt so the video model keeps the same product across shots.
    product: str = ""
    product_traits: str = ""
    # Brand constraint layer: an inline brand kit (subset of BrandKit fields)
    # and/or a stored kit keyed by product. Inline wins; brand_product loads
    # the persisted kit for that product (or the global default when omitted).
    brand: dict | None = None
    brand_product: str | None = None


class ScriptsRequest(BaseModel):
    """Product brief -> four competing ad-script concepts."""

    product: str = ""
    selling_points: list[str] | str | None = None
    style: str = "dr_vsl"
    audience: str | None = None
    tone: str | None = None
    category: str | None = None
    use_llm: bool = True
    variation: int = 0
    # Language the ad scripts should be written in. Defaults to English; the
    # UI sends the current page language so the copy matches the user's market.
    language: str = "en"
    # What the product physically IS: category, material, craft, flavour,
    # spec, price band. Selling points say what it does for the buyer; this
    # says what it IS, and it is the only way the model can tell a bottle of
    # baijiu from a sleep lamp.
    product_traits: str = ""


@app.post("/api/scripts")
async def create_scripts(req: ScriptsRequest):
    """Brief -> up to four ad-script variants. Free; no generation yet.

    Runs as a background job: LLM writing takes tens of seconds and the UI
    wants a live progress panel, not a dead spinner.
    """
    product = (req.product or "").strip()
    if not product and not req.selling_points:
        raise HTTPException(400, "请至少填写产品名称或卖点")
    params = req.model_dump()
    job_id = _new_job("scripts")
    _start_ticker(job_id, [
        ("brief", "Reading product & selling points"),
        ("drafting", "Drafting 4 script frameworks"),
        ("polishing", "Polishing tone & hooks"),
    ])

    def _run():
        try:
            drafts, fallback_reason = generate_scripts_detailed(
                product=params.get("product", ""),
                selling_points=params.get("selling_points"),
                style=params.get("style", "dr_vsl"),
                audience=params.get("audience"),
                tone=params.get("tone"),
                category=params.get("category"),
                use_llm=params.get("use_llm", True),
                variation=params.get("variation", 0),
                language=params.get("language", "en"),
                product_traits=params.get("product_traits", ""),
            )
            drafts_payload = [d.to_dict() for d in drafts]
            # Persist this generation so the user can revisit it from history.
            _record_script(
                script_id=job_id, job_id=job_id, product=product,
                category=params.get("category"), audience=params.get("audience"),
                tone=params.get("tone"), language=params.get("language", "en"),
                traits=params.get("product_traits", ""),
                drafts=drafts_payload,
            )
            _finish_job(job_id, {
                "brief": {
                    "product": product,
                    "style": params.get("style", "dr_vsl"),
                    "traits": params.get("product_traits", ""),
                },
                "scripts": drafts_payload,
                # History rows are keyed by the generating job id — the UI
                # echoes it back to mark which concept was chosen.
                "script_id": job_id,
                # Tell the user when the offline templates were used instead
                # of the model. Silently shipping template filler as if it
                # were AI copy is how a bottle of baijiu ends up selling
                # "每天都在被同一个问题折磨".
                "used_llm": not fallback_reason,
                "fallback_reason": fallback_reason,
            })
        except Exception as e:  # LLM path falls back internally; surface plainly
            _fail_job(job_id, f"脚本生成失败：{e}")

    asyncio.get_event_loop().run_in_executor(None, _run)
    return {"job_id": job_id}


class PlanScene(BaseModel):
    """A user-confirmed (or user-edited) scene, sent back on render."""

    id: str
    vo: str | None = None
    prompt: str | None = None
    shot: str | None = None
    angle: str | None = None
    elements: list[str] | None = None


class RenderRequest(BaseModel):
    plan_id: str
    scenes: list[PlanScene] = []
    name: str = "untitled"
    style: str = "dr_vsl"
    aspect: str = "9:16"
    fps: int = 30
    draft: bool = False
    video_provider: str | None = None
    voice_provider: str | None = None
    out_name: str | None = None
    # Render resolution preset. Map keys must match avb.ir.RES_SCALE so the
    # composer renders smaller/larger frames rather than downscaling.
    resolution: str = "1080p"
    # Target total length in seconds. Scenes are scaled proportionally after
    # voice synthesis; VO/caption sync is untouched.
    target_seconds: float | None = None
    # Product reference images (three-view set) uploaded via /api/upload.
    # Steer video generation so every scene keeps the same product identity.
    reference_images: list[str] = []
    # Optional audio/text layers. Off by default would be unusual, but the
    # UI lets the user skip the voiceover (silent clips) and/or the kinetic
    # captions (burned-in subtitles) per render.
    with_voice: bool = True
    with_captions: bool = True


class RenderResumeRequest(BaseModel):
    """Resume a partial/failed render from the failure point."""

    render_id: str
    out_name: str | None = None


def _scene_view(scene: object) -> dict:
    """Flatten one Scene for the confirmation UI.

    `caption` is not a separate field on purpose: kinetic captions are driven
    by the voiceover's word-level timings, so there is exactly one piece of
    text per scene and editing it moves the caption too. Exposing them as two
    independent inputs would be a lie the renderer could not honour.
    """
    return {
        "id": scene.id,
        "index": scene.index,
        "beat": scene.beat,
        "vo": scene.vo.text,
        "prompt": scene.visual.prompt,
        "motion": scene.visual.motion,
        "style": scene.visual.style or "",
        "shot": scene.visual.shot or "",
        "angle": scene.visual.angle or "",
        "elements": scene.visual.elements or [],
    }


def _apply_scene_overrides(project: object, overrides: list[dict]) -> int:
    """Write confirmed copy back onto the IR. Returns the number changed."""
    by_id = {s.id: s for s in project.scenes}
    changed = 0
    for ov in overrides or []:
        scene = by_id.get(getattr(ov, "id", None) or (ov or {}).get("id"))
        if scene is None:
            continue
        ovd = ov if isinstance(ov, dict) else {
            "vo": getattr(ov, "vo", None),
            "prompt": getattr(ov, "prompt", None),
            "shot": getattr(ov, "shot", None),
            "angle": getattr(ov, "angle", None),
            "elements": getattr(ov, "elements", None),
        }
        vo = ovd.get("vo")
        prompt = ovd.get("prompt")
        shot = ovd.get("shot")
        angle = ovd.get("angle")
        elements = ovd.get("elements")
        if vo is not None and vo != scene.vo.text:
            scene.vo.text = vo
            changed += 1
        if prompt is not None and prompt != scene.visual.prompt:
            scene.visual.prompt = prompt
            changed += 1
        if shot is not None and shot != scene.visual.shot:
            scene.visual.shot = shot
            changed += 1
        if angle is not None and angle != scene.visual.angle:
            scene.visual.angle = angle
            changed += 1
        if elements is not None:
            el = list(elements) if isinstance(elements, (list, tuple)) else None
            if el != (scene.visual.elements or []):
                scene.visual.elements = el
                changed += 1
    return changed


def _resolve_brand(params: dict) -> dict | None:
    """Pick the brand kit to apply for a request.

    Priority: inline ``brand`` dict overrides a stored kit; ``brand_product``
    loads that product's persisted kit (falling back to the global default);
    if neither references a stored kit, the global default is still applied
    when present. ``None`` means "no brand constraint" (style defaults win).
    """
    brand = params.get("brand")
    product = params.get("brand_product")
    base: dict | None = None
    if product:
        stored = db_get_brand_kit(product) or db_get_brand_kit(DEFAULT_BRAND_KEY)
        if stored and stored.get("brand"):
            base = stored["brand"]
    else:
        default = db_get_brand_kit(DEFAULT_BRAND_KEY)
        if default and default.get("brand"):
            base = default["brand"]
    if isinstance(brand, dict):
        base = {**(base or {}), **brand}
    return base or None


@app.post("/api/plan")
async def create_plan(req: PlanRequest):
    """Script -> scenes. No TTS, no video, no spend. Background job (see /api/scripts)."""
    script = (req.script or "").strip()
    if not script:
        raise HTTPException(400, "请先粘贴广告脚本")
    params = req.model_dump()
    job_id = _new_job("plan")
    _start_ticker(job_id, [
        ("parsing", "Splitting the script into scenes"),
        ("cinema", "Assigning shot / angle / elements"),
        ("polishing", "Polishing visual prompts"),
    ])

    def _run():
        try:
            if params.get("use_llm", True):
                project = parse_script_llm(
                    script, name=params["name"], style=params["style"],
                    target_seconds=params.get("target_seconds"),
                    script_language=params.get("language"),
                    product=params.get("product", ""),
                    product_traits=params.get("product_traits", ""),
                    template=params.get("template", "pain_hook"),
                )
            else:
                project = parse_script(
                    script, name=params["name"], style=params["style"],
                    target_seconds=params.get("target_seconds"),
                    script_language=params.get("language"),
                    product=params.get("product", ""),
                    product_traits=params.get("product_traits", ""),
                    template=params.get("template", "pain_hook"),
                )
            if not getattr(project, "scenes", None):
                _fail_job(job_id, "没能从脚本里解析出任何场景，请换一段文案试试")
                return
            # Brand constraint layer: fold the resolved kit into the IR so the
            # stored project (and every downstream render) stays on-style.
            apply_brand_to_project(project, _resolve_brand(params))
            plan_id = uuid.uuid4().hex[:12]
            _plans[plan_id] = {
                "project": project,
                "script": script,
                "style": params["style"],
                "name": params["name"],
            }
            # Also register under _projects so /api/edit can patch a plan that
            # has been rendered under the same id (see /api/render).
            _projects[plan_id] = project
            _finish_job(job_id, {
                "plan_id": plan_id,
                "name": params["name"],
                "style": params["style"],
                "scenes": [_scene_view(s) for s in project.scenes],
            })
        except Exception as e:  # LLM/rule parse both fall back; surface it plainly
            _fail_job(job_id, f"脚本解析失败：{e}")

    asyncio.get_event_loop().run_in_executor(None, _run)
    return {"job_id": job_id}


@app.get("/api/plan/{plan_id}")
async def get_plan(plan_id: str):
    if plan_id not in _plans:
        raise HTTPException(404, "Plan not found or expired")
    p = _plans[plan_id]
    return {
        "plan_id": plan_id,
        "name": p["name"],
        "style": p["style"],
        "scenes": [_scene_view(s) for s in p["project"].scenes],
    }


@app.post("/api/render")
async def render(req: RenderRequest):
    """Render a confirmed plan. This is the step that costs money."""
    _require_no_active_job()
    from avb.assets.video import build_video_provider

    cfg0 = load_config()
    try:
        build_video_provider(req.video_provider or cfg0.video.provider)
    except Exception as e:
        raise HTTPException(
            400,
            "尚未配置可用的视频 Provider。请到「设置」页选择一家并填入 API Key。",
        ) from e

    if req.plan_id not in _plans:
        raise HTTPException(404, "Plan not found or expired, please re-run the script step")

    job_id = _new_job("render")
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_render, job_id, req.model_dump())
    return {"job_id": job_id}


def _run_render(job_id: str, params: dict):
    """Render a confirmed plan in a worker thread."""
    pipe = None
    cfg0 = load_config()
    try:
        entry = _plans.get(params.get("plan_id", ""))
        if entry is None:
            raise RuntimeError("Plan expired before rendering started")
        project = entry["project"]

        _update_job(job_id, stage="confirming", progress=0.05,
                     message="Applying confirmed copy...")

        changed = _apply_scene_overrides(project, params.get("scenes") or [])
        if changed:
            _update_job(job_id, stage="confirming", progress=0.08,
                         message=f"Applied {changed} edit(s) to the confirmed copy")

        # Visual style is chosen at render time (confirm step), not in the
        # brief: an explicit pick overrides the project default AND every
        # per-scene style, because the user's choice is a whole-film intent.
        # An empty/absent style keeps whatever the parser assigned.
        chosen_style = (params.get("style") or "").strip()
        if chosen_style:
            project.style = chosen_style
            for s in getattr(project, "scenes", []):
                s.visual.style = chosen_style

        # Resolution is likewise a render-time whole-film intent: it drives
        # project.width/height, so every scene renders at the chosen scale.
        chosen_resolution = (params.get("resolution") or "1080p").strip()
        if chosen_resolution:
            project.resolution = chosen_resolution

        cfg = load_config()
        if params.get("video_provider"):
            cfg.video.provider = params["video_provider"]
        if params.get("voice_provider"):
            cfg.voice.provider = params["voice_provider"]

        _update_job(job_id, stage="synth", progress=0.15,
                     message=f"Rendering {len(project.scenes)} scenes...")

        out_dir = Path("demo/out/web")
        out_dir.mkdir(parents=True, exist_ok=True)
        name = params.get("name") or entry["name"] or "untitled"
        out_name = params.get("out_name") or f"{name}_{job_id[:6]}.mp4"
        out_path = out_dir / out_name

        pipe = Pipeline(workdir=".avb_web", draft=params.get("draft", False), config=cfg)

        # Keep the IR alive so /api/edit can patch it without re-parsing, and
        # so a follow-up render of the same plan reuses the cache.
        _projects[job_id] = project
        _projects[params["plan_id"]] = project

        _update_job(job_id, stage="rendering", progress=0.3, message="Rendering scenes...")
        raw_target = params.get("target_seconds")
        try:
            target = float(raw_target) if raw_target else None
        except (TypeError, ValueError):
            target = None
        if target is not None:
            target = min(60.0, max(5.0, target))

        # Live video progress: the shared callback maps scene_index into the
        # 0.30-0.95 band; scene_done events land exactly on their fraction so
        # the bar only moves forward.
        n_total = len(project.scenes)

        pipe.video_progress_cb = _video_progress_cb(job_id, n_total)
        ref_images = _resolve_reference_images(params.get("reference_images") or [])
        # Remember them so /api/edit re-renders keep steering generation with
        # the same product images.
        _ref_images_by_job[job_id] = ref_images
        # Captions are only meaningful with a voiceover; guard against a
        # client sending captions-on + voice-off.
        want_voice = bool(params.get("with_voice", True))
        want_captions = bool(params.get("with_captions", True)) and want_voice
        _render_flags_by_job[job_id] = {
            "with_voice": want_voice,
            "with_captions": want_captions,
        }
        config = {
            "style": chosen_style or project.style,
            "resolution": chosen_resolution or "1080p",
            "aspect": project.aspect,
            "video_provider": params.get("video_provider") or cfg0.video.provider,
            "target_seconds": target,
            "with_voice": want_voice,
            "with_captions": want_captions,
        }
        # Durably mark this render as 'running' BEFORE we spend money. If the
        # server dies mid-render (crash / restart / OOM), the row stays
        # 'running' and is auto-recovered on the next boot -- the cache makes
        # the re-run cheap because finished scenes are reused, not re-billed.
        _record_render(render_id=job_id, job_id=job_id, plan_id=params["plan_id"],
                       name=name, config=config, project=project, report=None,
                       status="running")
        report = pipe.run(
            project, out_path,
            target_seconds=target,
            reference_images=ref_images or None,
            with_voice=want_voice,
            with_captions=want_captions,
        )

        def _count(v):
            return len(v) if isinstance(v, (list, tuple)) else (v or 0)

        status = "partial" if getattr(report, "warnings", None) else "done"
        _record_render(render_id=job_id, job_id=job_id, plan_id=params["plan_id"],
                       name=name, config=config, project=project, report=report,
                       status=status)
        _update_job(job_id, stage="done", progress=1.0, message="Complete",
                     done=True,
                     video_url=f"/api/video/{out_path.name}",
                     video_path=str(out_path),
                     cost=getattr(report, "cost_usd", 0.0),
                     total_duration=getattr(report, "total_duration", 0.0),
                     scenes=_count(getattr(report, "scenes", 0)),
                     rendered=_count(getattr(report, "rendered", 0)),
                     reused=_count(getattr(report, "reused", 0)),
                     warnings=list(getattr(report, "warnings", []) or []))
    except Exception as e:
        # A hard failure still goes into history so the user can resume from
        # the failure point. Cache reuse means only the scenes that never
        # finished get re-submitted on retry -- not the whole film.
        bad = {}
        if pipe is not None:
            for w in getattr(pipe, "scene_warnings", []):
                bad[w.get("scene_id")] = w
        fail_scenes = []
        if project is not None:
            for s in getattr(project, "scenes", []):
                w = bad.get(s.id)
                st = ("skipped" if w.get("skipped") else "degraded") if w else "ok"
                fail_scenes.append({"id": s.id, "index": s.index, "status": st})
        fail_warnings = [
            {"scene_index": w.get("index"), "error": w.get("error"),
             "scope": w.get("scope"), "skipped": w.get("skipped")}
            for w in bad.values()
        ]
        config = {
            "style": (params.get("style") or "").strip() or "dr_vsl",
            "resolution": (params.get("resolution") or "1080p").strip(),
            "aspect": (params.get("aspect") or "9:16").strip(),
            "video_provider": params.get("video_provider") or cfg0.video.provider,
            "target_seconds": None,
            "with_voice": bool(params.get("with_voice", True)),
            "with_captions": bool(params.get("with_captions", True)),
        }
        _record_render(render_id=job_id, job_id=job_id, plan_id=params.get("plan_id", ""),
                       name=params.get("name") or "untitled", config=config,
                       project=project, report=None, status="failed",
                       scenes=fail_scenes, warnings=fail_warnings)
        _update_job(job_id, stage="error", progress=0, done=True, error=str(e))


def _run_resume(job_id: str, src_render_id: str, only: set[str] | None, out_name: str | None):
    """Re-render only the failed scenes of a prior render and re-assemble.

    Good scenes are served from the content-addressed cache, so resuming a
    partial run re-submits just the scenes that actually failed -- already-
    spent money is not re-spent, and an account-level top-up (after a balance
    halt) only pays for the shots that were skipped.
    """
    try:
        rec = db_get_render(src_render_id)
        if rec is None:
            raise RuntimeError("Render history record missing")
        plan_id = rec.get("plan_id", "")
        project = _projects.get(plan_id) or _projects.get(src_render_id)
        if project is None:
            # The in-memory IR is gone (server restarted since the original
            # render). Rebuild it from the JSON we persisted so resume still
            # works across restarts -- this is what makes "top up balance,
            # then resume" a real workflow rather than a same-session trick.
            pj = rec.get("project_json")
            if pj:
                from avb.ir import Project

                project = Project.from_json(pj)
        if project is None:
            raise RuntimeError("Project expired; please re-run the script step before resuming")

        cfg = load_config()
        out_dir = Path("demo/out/web")
        out_dir.mkdir(parents=True, exist_ok=True)
        name = rec.get("name") or "untitled"
        path = out_dir / (out_name or f"{name}_retry_{job_id[:6]}.mp4")

        pipe = Pipeline(workdir=".avb_web", draft=False, config=cfg)
        _projects[job_id] = project
        # Inherit the reference images + voice/caption choice from the source
        # render so the retry keeps the same product identity and audio layer.
        src_job = rec.get("job_id") or src_render_id
        ref_images = _ref_images_by_job.get(src_job, [])
        _ref_images_by_job[job_id] = ref_images
        flags = _render_flags_by_job.get(src_job, {})
        want_voice = bool(flags.get("with_voice", True))
        want_captions = bool(flags.get("with_captions", True)) and want_voice
        _render_flags_by_job[job_id] = {"with_voice": want_voice, "with_captions": want_captions}

        n_total = len(project.scenes)
        pipe.video_progress_cb = _video_progress_cb(job_id, n_total)
        # Mark running durably so a second crash during recovery is itself
        # recoverable on the next boot (its parent was already flipped to
        # 'interrupted' by _recover_active_renders, breaking the loop).
        _record_render(render_id=job_id, job_id=job_id, plan_id=plan_id, name=name,
                       config=rec.get("config", {}), project=project, report=None,
                       status="running", parent_id=src_render_id)
        report = pipe.run(project, path, only=only, reference_images=ref_images or None,
                          with_voice=want_voice, with_captions=want_captions)

        status = "partial" if getattr(report, "warnings", None) else "done"
        _record_render(render_id=job_id, job_id=job_id, plan_id=plan_id, name=name,
                       config=rec.get("config", {}), project=project, report=report,
                       status=status, parent_id=src_render_id)
        _update_job(job_id, stage="done", progress=1.0, message="Complete", done=True,
                    video_url=f"/api/video/{path.name}", video_path=str(path),
                    cost=getattr(report, "cost_usd", 0.0),
                    total_duration=getattr(report, "total_duration", 0.0),
                    scenes=len(report.scenes) if hasattr(report, "scenes") else 0,
                    rendered=len(report.rendered) if hasattr(report, "rendered") else 0,
                    reused=len(report.reused) if hasattr(report, "reused") else 0,
                    warnings=list(getattr(report, "warnings", []) or []))
    except Exception as e:
        _update_job(job_id, stage="error", progress=0, done=True, error=str(e))


@app.post("/api/render/resume")
async def resume_render(req: RenderResumeRequest):
    """Retry a partial/failed render from the failure point.

    Partial (some scenes degraded/skipped): re-run ONLY those scenes, reusing
    the cache for the good ones. Failed (hard error): re-run the whole film,
    which still reuses cache for any scenes that completed before the crash.
    """
    _require_no_active_job()
    rec = db_get_render(req.render_id)
    if rec is None:
        raise HTTPException(404, "Render history not found")
    if rec.get("status") == "done":
        raise HTTPException(400, "This render is already complete")
    if rec.get("status") == "failed":
        only = None  # full re-run, cache reuse keeps it cheap
    else:
        only = {s["id"] for s in rec.get("scenes", []) if s["status"] != "ok"}
    if not only and rec.get("status") != "failed":
        raise HTTPException(400, "Nothing to retry — all scenes are good")
    job_id = _new_job("render")
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_resume, job_id, req.render_id, only, req.out_name)
    return {"job_id": job_id}


@app.get("/api/renders")
async def list_renders():
    """All past renders, newest first."""
    return {"renders": db_list_renders()}


@app.get("/api/renders/{render_id}")
async def get_render(render_id: str):
    rec = db_get_render(render_id)
    if rec is None:
        raise HTTPException(404, "Render not found")
    return rec


@app.get("/api/scripts-history")
async def list_script_history():
    """All past script generations, newest first."""
    return {"scripts": db_list_scripts()}


class ScriptSelectRequest(BaseModel):
    """Which of a batch's four concepts the user took forward."""

    type: str
    narration: str = ""


@app.post("/api/scripts-history/{script_id}/select")
async def select_script(script_id: str, req: ScriptSelectRequest):
    """Record the chosen concept on a past script batch."""
    if db_get_script(script_id) is None:
        raise HTTPException(404, "Script history not found")
    db_select_script(script_id, req.type, req.narration)
    return {"ok": True}


@app.get("/api/scripts-history/{script_id}")
async def get_script_history(script_id: str):
    rec = db_get_script(script_id)
    if rec is None:
        raise HTTPException(404, "Script history not found")
    return rec


@app.post("/api/generate")
async def generate(req: GenerateRequest):
    _require_no_active_job()
    # Fail fast with an actionable message rather than burning a job on a
    # pipeline that cannot produce footage: there is no offline video path.
    from avb.assets.video import build_video_provider
    cfg0 = load_config()
    try:
        build_video_provider(req.video_provider or cfg0.video.provider)
    except Exception as e:
        raise HTTPException(
            400,
            "尚未配置可用的视频 Provider。请在左侧「Provider 配置」里选择一家并填入 API Key。",
        ) from e

    job_id = _new_job("generate")

    # Run in background thread (pipeline is sync)
    loop = asyncio.get_event_loop()
    loop.run_in_executor(None, _run_pipeline, job_id, req.model_dump())
    return {"job_id": job_id}


def _run_pipeline(job_id: str, params: dict):
    """Execute the full pipeline in a worker thread."""
    try:
        _update_job(job_id, stage="parsing", progress=0.05, message="Parsing script...")

        cfg = load_config()
        # Override providers if requested
        if params.get("video_provider"):
            cfg.video.provider = params["video_provider"]
        if params.get("voice_provider"):
            cfg.voice.provider = params["voice_provider"]

        script = params["script"]
        name = params.get("name", "untitled")

        # Parse
        if params.get("use_llm", True):
            project = parse_script_llm(script, name=name, style=params.get("style", "dr_vsl"),
                                      template=params.get("template", "pain_hook"))
        else:
            project = parse_script(script, name=name, style=params.get("style", "dr_vsl"),
                                  template=params.get("template", "pain_hook"))

        chosen_resolution = (params.get("resolution") or "1080p").strip()
        if chosen_resolution:
            project.resolution = chosen_resolution

        # Brand constraint layer: fold the resolved kit into the IR so the
        # rendered video (and the stored project_json) stays on-style.
        apply_brand_to_project(project, _resolve_brand(params))

        _update_job(job_id, stage="synth", progress=0.15,
                     message=f"Parsed {len(project.scenes)} scenes, synthesizing voiceover...")

        # Build pipeline
        out_dir = Path("demo/out/web")
        out_dir.mkdir(parents=True, exist_ok=True)
        out_name = params.get("out_name") or f"{name}_{job_id[:6]}.mp4"
        out_path = out_dir / out_name

        pipe = Pipeline(workdir=".avb_web", draft=params.get("draft", False), config=cfg)

        # Keep the IR alive so /api/edit can patch it without re-parsing.
        _projects[job_id] = project

        _update_job(job_id, stage="rendering", progress=0.3,
                     message="Rendering scenes...")

        report = pipe.run(project, out_path)

        # RunReport.scenes is a list of timings and rendered/reused are lists
        # of scene ids -- the frontend wants counts, so normalise here rather
        # than shipping whole scene arrays over the wire.
        def _count(v):
            return len(v) if isinstance(v, (list, tuple)) else (v or 0)

        _update_job(job_id, stage="done", progress=1.0, message="Complete",
                     done=True,
                     video_url=f"/api/video/{out_path.name}",
                     cost=getattr(report, "cost_usd", 0.0),
                     total_duration=getattr(report, "total_duration", 0.0),
                     scenes=_count(getattr(report, "scenes", 0)),
                     rendered=_count(getattr(report, "rendered", 0)),
                     reused=_count(getattr(report, "reused", 0)),
                     warnings=list(getattr(report, "warnings", []) or []))

    except Exception as e:
        _update_job(job_id, stage="error", progress=0, done=True, error=str(e))


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    if job_id not in _jobs:
        raise HTTPException(404, "Job not found")
    return _jobs[job_id]


@app.websocket("/ws/jobs/{job_id}")
async def job_websocket(ws: WebSocket, job_id: str):
    await ws.accept()
    _ws_clients.setdefault(job_id, []).append(ws)
    try:
        # Send current state immediately
        if job_id in _jobs:
            await ws.send_json(_jobs[job_id])
        while True:
            await ws.receive_text()  # keep alive
    except WebSocketDisconnect:
        pass
    finally:
        if ws in _ws_clients.get(job_id, []):
            _ws_clients[job_id].remove(ws)


@app.get("/api/video/{filename}")
async def serve_video(filename: str):
    # `filename` is concatenated under VIDEO_DIR. Resolve it and confirm the
    # result still lives directly inside VIDEO_DIR before touching the FS. This
    # blocks any traversal regardless of encoding. (Starlette also refuses '/'
    # inside a path parameter, so the only names reaching here have no separator;
    # a bare ".." still resolves to a sibling of VIDEO_DIR and fails the check.)
    # Using a parent-directory comparison -- not a substring ban -- avoids
    # rejecting legitimate names like "v2..final.mp4".
    path = (VIDEO_DIR / filename).resolve()
    if path.parent != VIDEO_DIR or not path.exists():
        raise HTTPException(404, "Video not found")
    return FileResponse(str(path), media_type="video/mp4")


# ---------------------------------------------------------------------------
# Routes: conversational edit
# ---------------------------------------------------------------------------
@app.post("/api/edit")
async def edit(req: EditRequest):
    """Plan and optionally apply a conversational edit."""
    try:
        # The IR is held in memory per job (see _projects). Falling back to
        # a supplied project_json is not supported yet because avb.ir has
        # to_dict() but no from_dict().
        if req.job_id and req.job_id in _projects:
            project = _projects[req.job_id]
        else:
            raise HTTPException(400, "No project for this job (generate a video first)")

        # Build timings (simplified)
        from avb.pipeline import SceneTiming
        t = 0.0
        timings = []
        for s in project.scenes:
            dur = 3.0 if s.beat in ("hook", "cta") else 2.6
            timings.append(SceneTiming(s.id, s.index, s.beat, t, dur, t, s.vo.text))
            t += dur

        # Plan edit
        if req.use_llm:
            plan = plan_edit_llm(project, req.instruction, timings)
        else:
            plan = plan_edit(project, req.instruction, timings)

        out = {
            "intent": plan.intent,
            "scene_ids": plan.scene_ids,
            "ops": [{"path": o.path, "value": o.value} for o in plan.ops],
            "explanation": plan.explanation,
        }

        if not req.apply:
            return out

        # --- apply and re-render -----------------------------------------
        # apply_plan returns a NEW project, so the pre-edit version is still
        # intact: invalidate() diffs the two and we only re-render scenes
        # whose content hash actually moved. That is the feature the whole
        # architecture exists for.
        new_project = apply_plan(project, plan)
        diff = invalidate(project, new_project)  # Project.diff -> dict
        stale = set(diff.get("invalidated", []) or diff.get("added", []) or [])

        _projects[req.job_id] = new_project

        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {
            "job_id": job_id,
            "stage": "queued",
            "progress": 0.0,
            "message": "Re-rendering edited scenes",
            "done": False,
            "error": None,
            "video_url": None,
            "cost": None,
        }
        asyncio.get_event_loop().run_in_executor(
            None, _run_rerender, job_id, req.job_id, new_project, sorted(stale),
            req.out_name, req.draft,
        )
        out["job_id"] = job_id
        out["rebuilt"] = sorted(stale)
        return out
    except Exception as e:
        raise HTTPException(500, str(e))


def _run_rerender(job_id: str, src_job_id: str | None, project: object,
                  stale: list[str], out_name: str | None, draft: bool):
    """Re-render a project after an edit, rebuilding only the stale scenes."""
    try:
        cfg = load_config()
        out_dir = Path("demo/out/web")
        out_dir.mkdir(parents=True, exist_ok=True)
        name = getattr(project, "name", "untitled") or "untitled"
        path = out_dir / (out_name or f"{name}_edit_{job_id[:6]}.mp4")

        only = set(stale) or None
        _update_job(job_id, stage="rendering", progress=0.2,
                     message=f"Rebuilding {len(only) if only else len(project.scenes)} scene(s)...")

        pipe = Pipeline(workdir=".avb_web", draft=draft, config=cfg)
        _projects[job_id] = project
        ref_images = _ref_images_by_job.get(src_job_id, [])
        _ref_images_by_job[job_id] = ref_images
        # Inherit the voiceover/caption choice from the render this edit
        # branched off of, so a no-voice edit doesn't suddenly grow a VO.
        flags = _render_flags_by_job.get(src_job_id, {})
        want_voice = bool(flags.get("with_voice", True))
        want_captions = bool(flags.get("with_captions", True)) and want_voice
        _render_flags_by_job[job_id] = {
            "with_voice": want_voice,
            "with_captions": want_captions,
        }
        report = pipe.run(
            project, path, only=only, reference_images=ref_images or None,
            with_voice=want_voice, with_captions=want_captions,
        )

        def _count(v):
            return len(v) if isinstance(v, (list, tuple)) else (v or 0)

        _update_job(job_id, stage="done", progress=1.0, message="Complete", done=True,
                     video_url=f"/api/video/{path.name}",
                     video_path=str(path),
                     cost=getattr(report, "cost_usd", 0.0),
                     total_duration=getattr(report, "total_duration", 0.0),
                     scenes=_count(getattr(report, "scenes", 0)),
                     rendered=_count(getattr(report, "rendered", 0)),
                     reused=_count(getattr(report, "reused", 0)),
                     warnings=list(getattr(report, "warnings", []) or []))
    except Exception as e:
        _update_job(job_id, stage="error", progress=0, done=True, error=str(e))


# ---------------------------------------------------------------------------
# Routes: quick TTS preview
# ---------------------------------------------------------------------------
@app.post("/api/tts-preview")
async def tts_preview(text: str, voice: str = ""):
    """Quick TTS preview - returns audio file."""
    import tempfile
    from avb.assets.voice import build_voice_provider
    from avb.assets.base import Cache

    cache = Cache(Path(".avb_web/cache"))
    provider = build_voice_provider()
    voice = voice or "en-US-AriaNeural"

    result = provider.synth(text, voice=voice, speed=1.0, energy=1.0, cache=cache)
    return FileResponse(str(result.path), media_type="audio/mpeg")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
# Initialise the SQLite history store and migrate any legacy JSON registry so
# past videos are listed after a restart.
_init_history()
_migrate_history()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8787)
