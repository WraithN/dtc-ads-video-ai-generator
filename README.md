# dtc-ads-video-ai-generator

**Paste an ad script. Pick a style. Get a finished, Meta-ready video. Then
revise it by talking to it.**

An end-to-end automated production pipeline: script → scene breakdown →
neural voiceover with word-level timings → visuals → kinetic captions →
music and SFX → rendered MP4. Conversational editing re-renders only what
actually changed.

---

## Why this exists: the pain points

Script-to-video tooling sounds solved until you run it as a business. The
pain points this project is built around:

- **Editing is all-or-nothing.** Most pipelines are `script → clips → stitch → mp4`. There is no layer you can edit, so "change the first 10 seconds" means regenerate everything — minutes and dollars to move one cut.
- **Every revision re-spends.** A client's "make the hook punchier" re-renders the whole 60s ad, re-paying for every clip and the voiceover you already had.
- **Vendor lock-in and silent breakage.** One model API changes or shuts down (Sora 2 closed on 2026-09-24) and the pipeline dies. Swapping usually means a rewrite.
- **Invisible cost.** Without per-run cost accounting you cannot control unit economics — the whole game for high-volume DTC.
- **Fragile renders.** A run that dies at scene 17/20 (balance halt, rate limit) restarts from zero and re-spends on the 16 scenes that were already fine.
- **Drifting product identity.** Generic AI templates make a Moutai and a bedside lamp sound identical, and the product morphs shot-to-shot because nothing anchors it.
- **Unsafe "AI editing".** LLM edits that write arbitrary fields or touch pixels can silently corrupt the output or leak files outside the project.

---

## What is real, what is not

Straight answer, because the distinction matters:

| Layer | Status |
|---|---|
| **IR + content-addressed cache + conversational editing** | real — this is the deliverable |
| **LLM** (script parse, edit routing) | **real** — DeepSeek V4 by default, any OpenAI-compatible endpoint |
| **TTS** | **real** — edge-tts neural voices, free, word-level timestamps |
| **Music / SFX** | synthesised (chord progression + DSP), not generative |
| **Visuals / footage** | procedural stills + compositor motion; real video providers (Kling, Veo, Jimeng, Wan, Hailuo, CogVideoX) implemented and selected by config |

The offline formant voice and procedural video exist so the pipeline runs
with zero credentials and zero network. Everything real plugs into the same
`Provider` interface, so swapping is a config change, not a rewrite.

See **`docs/CASE_STUDY.md`** for the Upwork portfolio case study: answers
to the five application questions, a demo walk-through script, proposal
openers, and what is real vs stand-in.

See **`docs/PROVIDERS.md`** for the 2026 TTS and video-generation landscape
(pricing, licenses, what actually runs on a laptop GPU).

---

## What this project solves

Each pain point above maps to a concrete mechanism — not a marketing claim:

| Pain point | How it is solved | Where |
|---|---|---|
| Edits are all-or-nothing | The **SceneGraph IR** sits in the middle; it is the *only* thing ever edited — never pixels, never the MP4 | `avb/ir.py` |
| Re-rendering everything | Scenes are **content-addressed**; only scenes whose hash moved are re-rendered (85–95% reuse on a typical edit) | `avb/pipeline.py`, `avb/assets/base.py` |
| Vendor lock-in | A `Provider` abstraction: swap the model by config, not code, and it fails loudly instead of shipping fake footage | `avb/assets/*` |
| Invisible cost | `CostLedger` records every provider's spend and rolls it up per run and per kind | cost accounting |
| Fragile renders | SQLite history stores the full IR snapshot, so a resume re-runs **only** the scenes that failed and serves the rest from cache | `web/history_db.py`, `web/server.py` |
| Drifting identity | Product **traits** are injected as the visual subject *and* copy context on every scene | `avb/brief.py`, `avb/parse.py` |
| Unsafe AI edits | The LLM *proposes* from a closed whitelist; the code *disposes*, and a plan that sanitises to zero ops falls back to rules | `avb/llm_edit.py` |

The deeper "why" lives in **The one architectural decision that matters** and
**Conversational editing** below. Production hardening — path-traversal-safe
video serving, single-flight jobs, tolerant IR loading — is written up in
**`docs/REVIEW.md`**.

---

## Quick start

```bash
cd dtc-ads-video-ai-generator
python -m pip install -e ".[all]"     # core + edge-tts
cp .env.example .env                  # then put your DEEPSEEK_API_KEY in it

python -m avb.cli doctor              # verify the environment
python -m avb.cli config web          # optional: configure at http://127.0.0.1:8787

python -m avb.cli render script.txt --draft --out out/draft.mp4
python -m avb.cli render script.txt --out out/final.mp4
```

Then edit by talking:

```bash
python -m avb.cli edit project.json "Spice up the hook and make the first 10 seconds punchier"
python -m avb.cli render script.txt --only s001,s002,s003,s004 --out out/v2.mp4
```

Requires Python 3.11+ and `ffmpeg` on `PATH`. Full guide: **`docs/USAGE.md`**.
Architecture: **`docs/ARCHITECTURE.md`**.

### The demo tour

```bash
python demo/run_demo.py               # 7-stage tour, full quality
python demo/run_demo.py --draft       # half res, for iteration
python demo/run_demo.py --stages 3,4  # just render + conversational edits
```

Outputs land in `demo/out/`. `--stages` splits a long run; later stages reuse
the on-disk segment cache from earlier ones, so the split is free. Stage ids:
`1` parse, `2` providers, `3` v1 render, `4` edit #1, `5` edit #2, `6` style
matrix, `7` scene map.

---

## Configure

Four-layer cascade (later wins): **defaults < `avb.toml` < env vars < CLI flags.**

Secrets never go in `avb.toml` — the file names the env var that holds each
key (`api_key_env = "DEEPSEEK_API_KEY"`), and `.env` (gitignored) feeds the
environment. So the config file is safe to commit and share.

Three equivalent front-ends:

```bash
# 1. CLI
python -m avb.cli config set llm.model deepseek-v4-pro
python -m avb.cli config set voice.voice zh-CN-XiaoxiaoNeural
python -m avb.cli config set video.provider kling

# 2. Local web UI (stdlib only, localhost)
python -m avb.cli config web

# 3. Hand-edit avb.toml — comments survive `config set`
```

| Section | Keys |
|---|---|
| `[llm]` | `provider` `model` `base_url` `api_key_env` `max_tokens` `effort` |
| `[voice]` | `provider` `voice` `rate` `speed` `energy` |
| `[video]` | `provider` `model` `clip_seconds` `max_usd_per_run` |
| `[render]` | `width` `height` `fps` `crf` `preset` `workers` |

Any OpenAI-compatible endpoint works — DeepSeek, Moonshot, Zhipu, local
vLLM / Ollama with `--jinja`.

---

## CLI

```
avb doctor                  environment and provider check
avb config init|get|set|web|list|show|path
avb parse  script.txt [--llm] [--out p.json]
avb render script.txt [--draft] [--llm] [--style X] [--only s001,s002]
avb edit   project.json "make it punchier" [--rules-only]
avb providers               what is wired up and whether it is live
avb tts "hello world"       quick voice test
avb web                     launch the browser UI
```

---

## Web app

Browser UI over the same pipeline: paste a script, pick a model, watch it
render, revise it in natural language, then export the MP4 to publish anywhere.

The **Provider settings** panel at the top of the left column is where you
configure vendors: pick Seedance / Kling / Jimeng / Veo / Wan / Hailuo /
CogVideoX, paste the key, hit save. Keys land in `.env` (gitignored) and take
effect immediately -- no restart. Nothing renders until one is configured.

```bash
python -m pip install -e ".[web]"
python -m avb.cli web                 # -> http://127.0.0.1:8787
```

| | |
|---|---|
| **Step 1** | fill the product brief (name, **traits**, selling points, category, language) → get **four** ad-script concepts (Pain→Solution / Grass-planting / Review / Story); pick one and edit it |
| **Step 2** | storyboard appears with **cinematography per scene** (shot, angle, elements) — edit the copy, the visual prompt, the camera, or the resolution; nothing renders yet |
| **Step 3** | confirm → render; watch progress; preview the finished 9:16 cut |
| **Step 4** | chat to revise (`make the hook punchier`) — see which scenes it targets, only those re-render |
| **Step 5** | download the finished MP4 and publish it anywhere (see note below) |

Step 1 replaces the old free-text script box. The brief is turned into four
*competing* frameworks by `avb/brief.py` (LLM when a key is set, deterministic
templates offline). Step 2's scene view is no longer a flat copy-split: every
shot carries a `shot`, `angle`, and `elements` description, and the composed
image/video prompt weaves them together (see `avb/parse.py`).

The **product traits** field is the one that decides whether the copy sounds
like it was written for *your* product. State what the thing physically *is*
(`53度酱香型白酒 / 赤水河核心产区 / 五年陈酿`), not just what it does for
someone. Skip it and a bottle of Moutai and a bedside lamp come back with the
same template copy. The traits are also injected as the subject of every
scene's visual prompt, so the product stays consistent across shots.

Video models are selectable in the UI: **Seedance**, **Kling**, **Jimeng**,
**Veo**, **Wan**, **Hailuo (MiniMax)** — including the **H3 / H3-Highspeed /
H3-Lite** tiers — **CogVideoX**. Whichever you pick, it is one config change —
same `Provider` interface. There is **no offline stand-in**: every provider is
a hosted model and needs its own key, which you paste into the settings page
(stored in `.env`). Unconfigured, a run fails loudly rather than shipping fake
footage.

Each vendor also exposes its **model revision** as a dropdown with a
free-text "custom id" escape hatch (`SEEDANCE_MODEL`, `KLING_MODEL`,
`MINIMAX_MODEL`, …). Vendors rev these identifiers constantly, so a new
revision is a settings change, not a release:

```bash
python -m avb.cli providers     # shows the active model per vendor
export MINIMAX_MODEL=MiniMax-Hailuo-2.3
```

The UI is **English by default** (Chinese is one click away in the header);
the generated voiceover defaults to `en-US-AriaNeural`.

**Export, don't auto-publish**: there is no built-in upload. The finished
9:16 MP4 is served at `/api/video/{file}` — download it and publish to TikTok,
Douyin, Reels, Shorts, or any platform yourself. (TikTok's Content Posting
API is gated behind a 2–6 week audit and forces `SELF_ONLY` until then, so
manual upload is the simpler path.)

**Everything is remembered**: past renders and script batches live in a local
SQLite store, so the **history** tab lists them after a restart, and a
failed render can be **resumed from the failure point**. Scenes are
content-addressed, so a resume re-runs only the scenes that actually failed
and serves the good ones from cache — topping up your balance after a
balance halt does not re-spend on shots that already rendered.

Full guide: **`docs/WEBAPP.md`**.

---

## The one architectural decision that matters

Most script-to-video tools do this:

    script --> generate clips --> stitch --> mp4

That works for a demo and collapses in production, because **there is no
layer that can be edited**. Ask it to change the first 10 seconds and the
only available operation is "regenerate everything" — 8 minutes and $80 to
move one cut.

This pipeline puts a structured representation in the middle:

```
script
  |
  v
[LLM scene parser]  --->  SceneGraph IR (JSON)  <--- [conversational editor]
  |                            |
  |                            v
  |                    asset plan (per scene)
  |                            |
  |              +-------------+-------------+
  |              v             v             v
  |          video/image     voice        music/sfx
  |              |             |             |
  +--------------+-------------+-------------+
                             |
                             v
                  [compositor] -> segments -> concat -> mp4
```

Every scene carries a content hash:

```python
scene.content_hash() == sha1(canonical({
    "beat": ..., "vo": {...}, "visual": {...},
    "caption": {...}, "sfx": [...], "transition": ..., "interrupt": ...,
}))[:12]
```

Change one line, re-hash, diff. Only the scenes whose hash moved get
re-generated; everything else is a filesystem cache hit. **The IR is the
only thing ever edited** — never pixels, never the MP4. That single
constraint is what makes editing cheap, diffable, and reversible.

One trap worth naming, because it is invisible until a client asks why
nothing changed: the cache key must include the renderer version. A
compositor fix does not touch any scene's semantics, so without it the
pipeline cheerfully reuses every stale frame. `RENDER_VERSION` lives in the
segment key for exactly that reason.

---

## Conversational editing

This is the feature the brief marks *Extremely Important*. Two routers:

- **rules** (`avb/edit.py`) — fast, deterministic, free. Handles the common
  phrasings. Always available.
- **LLM** (`avb/llm_edit.py`) — handles arbitrary phrasing. Proposes a plan
  as structured JSON against a closed vocabulary of intents, selectors and
  patch paths; the code sanitises and applies it.

```
user > Make the first 10 seconds more engaging with faster cuts and
       more aggressive text animations.

  intent      : pacing
  scene_ids   : ['s001', 's002', 's003', 's004']
  ops         : vo.speed x1.08 | caption.scale_pop x1.35
               visual.motion -> punch | transition -> flash
  invalidated : 4 / 20 scenes
  reused      : 16 / 20 segments straight from cache
```

Scene targeting resolves in three tiers, most specific first:

```
explicit timestamp  >  narrative beat  >  whole video
"first 10 seconds"     "the hook"         "throughout the video"
```

Measured reuse on the bundled 20-scene demo:

| Instruction | Scenes re-rendered | Reuse |
|---|---|---|
| "first 10 seconds" (time range) | 3 / 20 | 85% |
| "mechanism section → comic" (style) | 1 / 20 | 95% |
| "throughout the video" (global) | 20 / 20 | 0% |

Style swaps are the cheapest edit class — they do not touch the voiceover,
and the voiceover is the slowest asset to produce.

### Safety rails

The LLM proposes; the code disposes. It can only pick from a closed list of
intents, selectors and patch paths. Enum values snap to known ones
(`comic_book` → `comic`). Anything that sanitises to zero ops falls back to
the rule-based plan. A bad model answer degrades to a dumber edit, never to
a corrupted IR.

---

## Performance

Segment rendering can fan out across processes (`--workers`, default auto).
Measured on the bundled 20-scene 9:16 draft, on this machine:

| Renderers | Wall clock | Speedup |
|---|---|---|
| 1 (serial) | 217 s | 1.0x |
| 6 (process pool) | 206 s | **1.05x** |

That is an honest number and it is the interesting one. Parallelising the
compositor barely helps, because **the compositor is not the bottleneck** —
voice synthesis and music rendering are, and both are inherently serial
(audio ducking needs the final voice envelope before the bed can be laid
down). Frame work is also GIL-bound, so a thread pool is worse still
(measured 1.1x).

The process pool is still worth having: it is the right shape for the real
bottleneck the moment you swap the offline formant synth for a hosted TTS API
(where each segment is an HTTP round-trip and latency, not CPU, dominates).
On Windows it needs an `if __name__ == "__main__"` guard in the caller, so
the pool falls back to serial rendering rather than dying if it does not
get one.

If you do chase the actual bottleneck later: synthesis is content-addressed
and cached, so the second run of an unchanged script is mostly concat and
mux.

---

## Unit economics

High-volume DTC lives or dies on cost per finished video. Every provider
reports what it spent and `CostLedger` rolls it up per run.

| Component | Offline default | Real provider |
|---|---|---|
| voice (per 60s ad) | $0 (formant/edge) | ~$0.10 (ElevenLabs) |
| visuals | $0 (procedural) | $0.40–2.00/clip (Kling–Veo) |
| music | $0 (synth) | $0 (synth) |
| LLM (parse + edits) | $0 (rules) | ~$0.001–0.01 (DeepSeek) |
| **60s ad, all-real voice + 8 clips** | — | **~$4–16** |
| **60s ad, edge-tts + Seedance mini** | — | **~$0.5–1.5** |

A style change touches ~1 scene's visual and nothing else. A pacing change
touches voiceover but reuses every visual. The cache turns "change one line"
from a full rebuild into a diff.

---

## Tests

```bash
python -m pytest -q
```

99 tests, no network, no ffmpeg. They cover the config cascade,
comment-preserving writes, `.env` loading, JSON extraction, edit-plan
sanitisation (the guard rails), provider fallback, and the content-addressed
cache — plus IR round-trip + forward-compatible loading, history-DB migration
and round-trip, the web API (path-traversal guard, resume, single-flight
jobs), and an i18n key regression guard (`npm run check:i18n` is mirrored as a
pytest so a missing translation key fails CI, not the end user).

| Test file | What it proves |
|---|---|
| `tests/test_smoke.py` | config cascade, `.env` loading, edit-plan rails, provider fallback, cache |
| `tests/test_ir_roundtrip.py` | IR serialises and reloads; tolerates unknown keys / missing fields |
| `tests/test_history_db.py` | render history migrates and round-trips, including the IR snapshot |
| `tests/test_server_api.py` | API path-traversal guard, resume, single-flight job guarding |
| `tests/test_i18n_keys.py` | every referenced i18n key exists in both `zh` and `en` |

---

## Known limits

- **edge-tts is an unofficial endpoint.** Fine for a portfolio and for
  cost-sensitive volume; use ElevenLabs or self-hosted CosyVoice/IndexTTS
  for anything with an SLA.
- **The formant voice is a synthesiser, not a model.** It exists so the
  pipeline runs with zero credentials. It sounds like 1998. Use `edge-tts`
  (free) unless you are offline.
- **There is no offline video provider, on purpose.** A fake clip would make
  a broken ad look finished. Adapters for Seedance / Kling / Jimeng / Veo /
  Wan / Hailuo / CogVideoX are implemented; the video adapters are not yet
  live-tested against a billed endpoint.
- **Compliance is your job.** FTC endorsement guides and Meta ad policy
  apply to what you ship. This pipeline does not audit claims.
- **Parallelism is ~1.05×** on the current bottleneck mix. See Performance
  above.

---

## Layout

```
dtc-ads-video-ai-generator/
├── avb.toml              # config (secrets live in .env, not here)
├── .env                  # gitignored, holds DEEPSEEK_API_KEY etc.
├── pyproject.toml        # package metadata, `avb` entry point
├── avb/                  # the pipeline package
│   ├── config.py  llm.py  llm_edit.py  parse.py  edit.py  ir.py
│   ├── pipeline.py  qc.py  cli.py  brief.py
│   ├── webui.py          # local HTML config page (stdlib only)
│   ├── webapp.py         # documents what the installed package does / does not ship
│   ├── assets/           # providers: voice / video / image / music
│   └── render/           # compositor, ffmpeg, remotion
├── web/                  # FastAPI app + browser UI (the `avb web` server)
│   ├── server.py         # REST API, path-traversal-safe video serving, resume
│   ├── history_db.py     # SQLite render history + IR snapshots
│   └── frontend/         # React + Vite UI (TypeScript)
├── docs/                 # ARCHITECTURE / USAGE / WEBAPP / PROVIDERS / REVIEW / CASE_STUDY
├── tests/                # smoke, history_db, server_api, ir_roundtrip, i18n_keys
└── demo/                 # scripted tour + sample script
```
