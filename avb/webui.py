"""Local HTML configuration page.

Serves a single page on 127.0.0.1 that edits `avb.toml` and shows provider
status. Standard library only -- no Flask/FastAPI, because a config tool
that needs a dependency install to configure its dependencies is a joke.

Endpoints:
    GET  /            the page
    GET  /api/config  effective config as JSON (secrets redacted)
    POST /api/config  {"key": "llm.model", "value": "..."} -> writes avb.toml
    POST /api/test    {"kind": "llm"|"tts"} -> live connectivity probe
    GET  /api/status  provider availability

Binding is 127.0.0.1 by default. This page can write your config file, so
do not put it behind a public reverse proxy.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .config import Config, config_set, load_config

__all__ = ["serve", "make_handler"]

_REDACT_KEYS = {"api_key", "secret", "token", "password"}


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: ("***" if any(s in k.lower() for s in _REDACT_KEYS) and v else _redact(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def _status() -> dict[str, Any]:
    from .assets.voice import build_voice_provider
    from .assets.video import build_video_provider

    cfg = load_config()
    out: dict[str, Any] = {"config_loaded": True}
    try:
        v = build_voice_provider(cfg.voice.provider)
        out["voice"] = {"selected": v.name, "available": v.available()}
    except Exception as e:
        out["voice"] = {"error": str(e)}
    try:
        vid = build_video_provider(cfg.video.provider)
        out["video"] = {"selected": vid.name, "available": vid.available()}
    except Exception as e:
        out["video"] = {"error": str(e)}
    out["llm"] = {
        "provider": cfg.llm.provider,
        "model": cfg.llm.model,
        "base_url": cfg.llm.base_url,
        "key_env": cfg.llm.api_key_env,
        "has_key": bool(cfg.llm.api_key),
    }
    return out


def _probe(kind: str) -> dict[str, Any]:
    cfg = load_config()
    if kind == "llm":
        from .llm import LLMClient

        try:
            client = LLMClient(cfg.llm)
            resp = client.chat(
                [{"role": "user", "content": "Reply with the single word: ok"}],
                max_tokens=2000,
            )
            return {
                "ok": True,
                "model": resp.model,
                "latency_s": resp.latency_s,
                "content": resp.content[:80],
                "tokens": resp.total_tokens,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}
    if kind == "tts":
        from .assets.base import Cache
        from .assets.voice import build_voice_provider

        try:
            provider = build_voice_provider(cfg.voice.provider)
            cache = Cache(Path(cfg.workdir) / "cache")
            r = provider.synth("This is a connectivity test.", voice=cfg.voice.voice,
                               speed=cfg.voice.speed, energy=cfg.voice.energy, cache=cache)
            return {"ok": True, "provider": provider.name, "duration": r.duration,
                    "words": len(r.words), "path": str(r.path)}
        except Exception as e:
            return {"ok": False, "error": str(e)}
    return {"ok": False, "error": f"unknown probe kind: {kind}"}


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dtc-ads-video-ai-generator · config</title>
<style>
  :root {
    --bg: #f6f7f9; --card: #ffffff; --ink: #12161c; --muted: #5b6672;
    --line: #dde2e8; --accent: #2f6df6; --ok: #1a7f45; --bad: #b3261e;
    --mono: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--ink);
    font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    padding: 32px 20px 64px;
  }
  .wrap { max-width: 860px; margin: 0 auto; }
  h1 { font-size: 22px; margin: 0 0 4px; letter-spacing: -0.01em; }
  h2 { font-size: 15px; margin: 28px 0 10px; color: var(--muted); font-weight: 600;
       text-transform: uppercase; letter-spacing: 0.06em; }
  .sub { color: var(--muted); margin: 0 0 24px; }
  .card {
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 18px 20px; margin-bottom: 14px;
  }
  .row { display: grid; grid-template-columns: 220px 1fr auto; gap: 12px; align-items: center;
         padding: 8px 0; border-bottom: 1px solid var(--line); }
  .row:last-child { border-bottom: 0; }
  label { color: var(--muted); font-size: 13px; font-family: var(--mono); }
  input[type=text], select {
    width: 100%; padding: 8px 10px; border: 1px solid var(--line); border-radius: 8px;
    font: inherit; font-family: var(--mono); background: #fff; color: var(--ink);
  }
  input[type=text]:focus, select:focus { outline: 2px solid var(--accent); border-color: transparent; }
  button {
    padding: 8px 14px; border-radius: 8px; border: 1px solid var(--accent);
    background: var(--accent); color: #fff; font: inherit; font-size: 13px; cursor: pointer;
  }
  button.ghost { background: transparent; color: var(--accent); }
  button:disabled { opacity: 0.5; cursor: default; }
  .pill { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 12px;
          font-family: var(--mono); }
  .pill.ok { background: #e6f4ec; color: var(--ok); }
  .pill.bad { background: #fbeae9; color: var(--bad); }
  .pill.muted { background: #eef1f4; color: var(--muted); }
  .note { font-size: 13px; color: var(--muted); margin-top: 8px; }
  .toast {
    position: fixed; left: 50%; bottom: 24px; transform: translateX(-50%) translateY(20px);
    background: var(--ink); color: #fff; padding: 10px 18px; border-radius: 10px;
    opacity: 0; transition: all 0.25s; pointer-events: none; font-size: 14px;
    max-width: 90vw;
  }
  .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
  code { font-family: var(--mono); background: #eef1f4; padding: 1px 5px; border-radius: 4px;
         font-size: 13px; }
  .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
  @media (max-width: 720px) {
    .row { grid-template-columns: 1fr; gap: 6px; }
    .grid2 { grid-template-columns: 1fr; }
  }
</style>
</head>
<body>
<div class="wrap">
  <h1>dtc-ads-video-ai-generator</h1>
  <p class="sub">Configuration · writes to <code id="cfgpath">avb.toml</code> · secrets stay in <code>.env</code></p>

  <div class="card" id="status-card">
    <h2 style="margin-top:0">Provider status</h2>
    <div id="status">loading…</div>
  </div>

  <h2>LLM</h2>
  <div class="card" id="sec-llm"></div>

  <h2>Voice</h2>
  <div class="card" id="sec-voice"></div>

  <h2>Video</h2>
  <div class="card" id="sec-video"></div>

  <h2>Render</h2>
  <div class="card" id="sec-render"></div>

  <h2>Project</h2>
  <div class="card" id="sec-project"></div>

  <div class="card">
    <h2 style="margin-top:0">Connectivity</h2>
    <div class="grid2">
      <button class="ghost" onclick="probe('llm')">Test LLM</button>
      <button class="ghost" onclick="probe('tts')">Test TTS</button>
    </div>
    <div class="note" id="probe-out"></div>
  </div>
</div>

<div class="toast" id="toast"></div>

<script>
const SCHEMA = {
  llm: [
    ["provider", "select", ["deepseek","openai","moonshot","zhipu","ollama","custom"]],
    ["model", "text"],
    ["base_url", "text"],
    ["api_key_env", "text"],
    ["max_tokens", "text"],
    ["temperature", "text"],
    ["effort", "select", ["low","high","max"]],
    ["enabled", "select", ["true","false"]]
  ],
  voice: [
    ["provider", "select", ["auto","edge","formant","elevenlabs"]],
    ["voice", "text"],
    ["rate", "text"],
    ["speed", "text"],
    ["energy", "text"]
  ],
  video: [
    ["provider", "select", ["auto","seedance","kling","jimeng","veo","bailian","minimax","zhipu"]],
    ["model", "text"],
    ["clip_seconds", "text"],
    ["max_usd_per_run", "text"]
  ],
  render: [
    ["width", "text"], ["height", "text"], ["fps", "text"],
    ["crf", "text"], ["preset", "text"], ["workers", "text"]
  ],
  project: [
    ["project_name", "text"], ["style", "text"], ["aspect", "text"], ["workdir", "text"]
  ]
};

const SECTIONS = {
  llm: "sec-llm", voice: "sec-voice", video: "sec-video",
  render: "sec-render", project: "sec-project"
};

let current = {};

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(t._h);
  t._h = setTimeout(() => t.classList.remove("show"), 2600);
}

async function loadConfig() {
  const r = await fetch("/api/config");
  current = await r.json();
  for (const [sec, elId] of Object.entries(SECTIONS)) {
    const el = document.getElementById(elId);
    const rows = SCHEMA[sec] || [];
    el.innerHTML = rows.map(([key, kind, opts]) => {
      const val = sec === "project" ? (current[key] ?? "") : ((current[sec] || {})[key] ?? "");
      const dotted = sec === "project" ? key : `${sec}.${key}`;
      const input = kind === "select"
        ? `<select data-key="${dotted}">${opts.map(o =>
            `<option value="${o}" ${String(val)===o?"selected":""}>${o}</option>`).join("")}</select>`
        : `<input type="text" data-key="${dotted}" value="${String(val).replace(/"/g,'&quot;')}">`;
      return `<div class="row">
        <label>${dotted}</label>
        ${input}
        <button class="ghost" onclick="saveOne('${dotted}')">Save</button>
      </div>`;
    }).join("");
  }
}

async function saveOne(dotted) {
  const input = document.querySelector(`[data-key="${dotted}"]`);
  if (!input) return;
  const body = JSON.stringify({key: dotted, value: input.value});
  const r = await fetch("/api/config", {
    method: "POST", headers: {"Content-Type":"application/json"}, body
  });
  const out = await r.json();
  if (out.ok) { toast(`saved ${dotted} = ${out.value}`); loadStatus(); }
  else toast(`error: ${out.error || "unknown"}`);
}

async function loadStatus() {
  const r = await fetch("/api/status");
  const s = await r.json();
  const v = s.voice || {}, vd = s.video || {}, l = s.llm || {};
  const pill = (ok, txt) => `<span class="pill ${ok?"ok":"bad"}">${txt}</span>`;
  document.getElementById("status").innerHTML = `
    <div class="row"><label>voice</label>
      <div>${v.selected || v.error || "?"}</div>
      <div>${v.available !== undefined ? pill(v.available, v.available?"available":"unavailable") : pill(false,"error")}</div>
    </div>
    <div class="row"><label>video</label>
      <div>${vd.selected || vd.error || "?"}</div>
      <div>${vd.available !== undefined ? pill(vd.available, vd.available?"available":"unavailable") : pill(false,"error")}</div>
    </div>
    <div class="row"><label>llm</label>
      <div>${l.provider || "?"} / ${l.model || "?"}</div>
      <div>${pill(!!l.has_key, l.has_key?"key set":"no key ($"+(l.key_env||"")+")")}</div>
    </div>`;
}

async function probe(kind) {
  const out = document.getElementById("probe-out");
  out.textContent = `testing ${kind}…`;
  const r = await fetch("/api/test", {
    method: "POST", headers: {"Content-Type":"application/json"},
    body: JSON.stringify({kind})
  });
  const d = await r.json();
  if (d.ok) {
    out.innerHTML = `<span class="pill ok">ok</span> ` +
      (kind === "llm"
        ? `model=${d.model} · ${d.latency_s}s · ${d.tokens} tok · replied “${d.content}”`
        : `provider=${d.provider} · ${d.duration?.toFixed?.(2)}s · ${d.words} words`);
  } else {
    out.innerHTML = `<span class="pill bad">failed</span> ${d.error}`;
  }
}

loadConfig().then(loadStatus);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    config_path: Path = Path("avb.toml")

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter
        pass

    def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Any) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self._send(200, PAGE.encode("utf-8"), "text/html")
            return
        if self.path == "/api/config":
            cfg = load_config(self.config_path)
            self._json(200, _redact(cfg.to_dict()))
            return
        if self.path == "/api/status":
            self._json(200, _status())
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._json(400, {"ok": False, "error": "bad JSON"})
            return

        if self.path == "/api/config":
            key = body.get("key")
            value = body.get("value")
            if not key:
                self._json(400, {"ok": False, "error": "missing key"})
                return
            try:
                if not self.config_path.exists():
                    from .config import Config, save_config

                    save_config(Config(), self.config_path)
                ok, msg = config_set(self.config_path, key, str(value))
                self._json(200 if ok else 400, {"ok": ok, "value": msg, "error": None if ok else msg})
            except Exception as e:
                self._json(500, {"ok": False, "error": str(e)})
            return

        if self.path == "/api/test":
            self._json(200, _probe(str(body.get("kind") or "")))
            return

        self._json(404, {"ok": False, "error": "not found"})


def make_handler(config_path: Path):
    class _H(Handler):
        pass

    _H.config_path = Path(config_path)
    return _H


def serve(
    config_path: str | Path = "avb.toml",
    *,
    host: str = "127.0.0.1",
    port: int = 8787,
    open_browser: bool = True,
) -> None:
    path = Path(config_path)
    if not path.exists():
        from .config import Config, save_config

        save_config(Config(), path)
        print(f"created {path}")

    httpd = ThreadingHTTPServer((host, port), make_handler(path))
    url = f"http://{host}:{port}/"
    print(f"config UI: {url}")
    print(f"editing:   {path.resolve()}")
    print("Ctrl-C to stop.")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        httpd.server_close()
