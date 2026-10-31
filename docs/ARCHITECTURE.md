# Architecture

**dtc-ads-video-ai-generator** turns an ad script into a finished, Meta-ready video,
then lets you revise it by talking to it. This document explains the shape
of the system and the reasons it has that shape.

```
                 ┌──────────────────────────────────────────┐
   avb.toml  ──▶ │              config layer                │
   .env      ──▶ │  defaults < file < env < CLI flags       │
                 └───────────────┬──────────────────────────┘
                                 │
     script.txt                  ▼
        │              ┌──────────────────┐
        ├─────────────▶│  parse.py        │  heuristic  ─┐
        │              │  parse_script_llm │  LLM (opt)  ─┤
        │              └────────┬─────────┘               │
        │                       ▼                         │
        │              ┌──────────────────┐               │
        │              │  SceneGraph IR   │◀──────────────┘
        │              │  (Project/Scene) │
        │              └────────┬─────────┘
        │                       │
        │   "make it punchier"  │
        │              ┌────────▼─────────┐
        │              │  edit.py         │  rule router ─┐
        │              │  llm_edit.py     │  LLM router  ─┤
        │              └────────┬─────────┘               │
        │                       ▼                         │
        │              ┌──────────────────┐               │
        │              │  apply_plan()    │  JSON patch   │
        │              │  invalidate()    │  -> stale set │
        │              └────────┬─────────┘               │
        │                       ▼                         │
        │              ┌──────────────────┐               │
        └─────────────▶│  pipeline.py     │◀──────────────┘
                       │  synth → render  │
                       │  → assemble → QC │
                       └────────┬─────────┘
                                ▼
                             out.mp4
```

---

## 1. The one decision that matters: an IR in the middle

Most script-to-video tools do this:

```
script --> generate clips --> stitch --> mp4
```

That works for a demo and collapses in production, because **there is no
layer that can be edited**. Ask it to change the first 10 seconds and the
only available operation is "regenerate everything" — minutes and dollars to
move one cut.

This pipeline puts a structured representation in the middle:

```
script --> SceneGraph IR --> assets --> frames --> mp4
```

The IR (`avb/ir.py`) is the only editable surface. Nothing downstream of it
is ever edited in place; everything upstream of it is disposable.

| Layer | Edits? | Regeneration cost when it changes |
|---|---|---|
| `Project` / `Scene` (IR) | **yes — this is the edit surface** | n/a |
| voice / video / music assets | no (content-addressed) | only if their input hash changed |
| rendered segments | no (content-addressed) | only if scene hash changed |
| final mp4 | no | concat only — seconds |

A scene is identified by the hash of its semantic content
(`Scene.semantic()`). Change the voiceover text and the scene re-renders.
Change the music mood and no scene re-renders. That arithmetic is what makes
"conversational editing" an engineering property rather than a marketing
claim.

---

## 2. Configuration

`avb/config.py` implements a four-layer cascade, later wins:

```
built-in defaults  <  avb.toml  <  environment variables  <  CLI flags
```

Secrets are never written to `avb.toml`. The file names the *env var* that
holds each key (`api_key_env = "DEEPSEEK_API_KEY"`), and `avb/config.py`
pulls `.env` (gitignored) into the environment at load time. So the config
file is safe to commit and share.

Three front-ends write the same file:

| Front-end | Command | Notes |
|---|---|---|
| hand-edited TOML | any text editor | comments preserved |
| CLI | `avb config set llm.model deepseek-v4-pro` | line-level rewrite, comments preserved |
| local web UI | `avb config web` | `http://127.0.0.1:8787`, stdlib only |

The web UI is deliberately built on `http.server` — a config tool that
requires a dependency install to configure its dependencies is a joke. It
binds to localhost only, and it can write your config file, so do not put it
behind a public proxy.

---

## 3. Providers

Everything generative sits behind `Provider` (`avb/assets/base.py`):

```python
class Provider(ABC):
    def available(self) -> bool: ...   # credentials/binaries present?
    def price(self, **kw) -> float: ... # USD for a hypothetical request
    def generate(...) / synth(...): ... # returns a path; idempotent
```

Two reasons for the abstraction:

1. **Vendor risk.** The Sora 2 API shut down on 2026-09-24. Any pipeline
   wired directly to one model dies with it. Selection is a config change.
2. **Unit economics.** High-volume DTC lives or dies on cost per finished
   video. Every provider reports spend and `CostLedger` rolls it up. If you
   cannot see the number, you cannot control it.

### Fallback chain

Each asset type has a chain, best-available first, and the last entry always
works offline:

| | preferred | then | always-works |
|---|---|---|---|
| voice | ElevenLabs (keyed) | **edge-tts** (free, neural) | formant DSP |
| video | Seedance / Kling / Jimeng / Veo / Wan / Hailuo / CogVideoX | — | none (fails loudly) |
| music | — | — | numpy/chord synth |
| LLM | configured endpoint | — | rule-based fallback |

`avb doctor` prints the resolved chain and what is actually active.

### Why edge-tts is the default voice

It is free, needs no key, ships ~400 neural voices (including native zh-CN),
and emits `WordBoundary` events — exactly what frame-accurate kinetic
captions need. That combination is rare. The catch is that it is an
unofficial endpoint, so treat it as *fallback-grade*: fine for a portfolio
pipeline and for volume where cost matters more than an SLA. For production,
swap to ElevenLabs or self-hosted CosyVoice / IndexTTS behind the same
interface. See `docs/PROVIDERS.md` for the full TTS landscape.

---

## 4. The LLM layer

`avb/llm.py` is a thin OpenAI-compatible `/chat/completions` client. DeepSeek
V4 (`deepseek-flash`, `deepseek-v4-pro`), Moonshot, Zhipu, local vLLM/Ollama
with `--jinja`, and OpenAI itself all fit.

Two behaviours worth knowing:

**Reasoning models.** DeepSeek V4 emits `reasoning_content` alongside
`content` and bills for both. A short `max_tokens` silently truncates the
answer and leaves `content` empty — the failure looks like "the model
returned nothing" rather than a budget error. The client raises the default
budget to 8000 and surfaces reasoning-token counts in the error message.

**JSON extraction.** `chat_json()` strips fences, finds the outermost
object/array, and retries once at temperature 0. It never raises for missing
keys — callers decide what to fall back to.

### Where the LLM is used

| Call site | Function | Falls back to |
|---|---|---|
| script → IR | `parse_script_llm` | `parse_script` (heuristic) |
| NL → edit plan | `plan_edit_llm` | `plan_edit` (rules) |

### The conversational edit router

This is the piece the DTC brief marks "Extremely Important". Design
constraints, in priority order:

1. **Never make things worse.** If the LLM is unavailable, misbehaves, or
   returns a plan that would touch paths the IR does not have, fall back to
   the rule-based plan. A slightly dumber plan beats a crashed pipeline.
2. **The LLM proposes; the code disposes.** The model picks from a closed
   vocabulary of intents, scene selectors and patch paths
   (`ALLOWED_PATHS`, `ENUM_PATHS` in `avb/llm_edit.py`). It never emits code
   and never names arbitrary IR fields. Anything outside the whitelist is
   dropped; enum values are snapped to allowed ones (`comic_book` → `comic`).
3. **It must know the project.** The prompt carries the scene list (ids,
   beats, durations, current settings) and the edit vocabulary, so "make the
   proof section pop" resolves to real scene ids rather than a guess.

Scene targeting resolves in three tiers, most specific first:

```
explicit timestamp ("first 10 seconds")  >  narrative beat ("the hook")  >  whole video
```

An LLM plan that sanitises to zero ops is discarded in favour of the rule
plan.

---

## 5. Caching

`avb/assets/base.py:Cache` is content-addressed on the local filesystem.
Same inputs → same path → no regeneration.

```
.avb/cache/
  voice-edge/<hash>.mp3 + <hash>.json   # sidecar keeps word timings on a cache hit
  video/<hash>.png
  segments/<hash>.mp4
```

The segment cache key is:

```python
key = short_hash({**scene.semantic(), "dur":…, "w":…, "h":…, "fps":…,
                  "render": RENDER_VERSION})
```

`RENDER_VERSION` is bumped whenever the compositor changes what a frame
looks like — without it a compositor fix would never reach the output.

Measured effect on the demo project (20 scenes):

| Edit | Scenes re-rendered | Reuse |
|---|---|---|
| "first 10 seconds" (time range) | 3 / 20 | 85% |
| "mechanism section → comic" (style) | 1 / 20 | 95% |
| "throughout the video" (global) | 20 / 20 | 0% |

Style swaps are the cheapest edit class because they do not touch the
voiceover, and the voiceover is the slowest asset to produce.

---

## 6. Concurrency

Segment rendering fans out with `ProcessPoolExecutor` (bypasses the GIL for
the numpy/Pillow frame loop), with a `ThreadPoolExecutor` fallback and a
`BrokenProcessPool` degrade-to-serial path.

**Honest numbers.** Parallelism barely helps on this workload: serial 217s
vs 6 workers 206s = **1.05×**. The bottleneck is serial voice synthesis plus
music rendering (audio ducking needs the final vocal envelope before the bed
can be laid down), and frame compositing is GIL-bound enough that thread
pools are *worse* (1.1×). The process pool is kept because with a real TTS
API the bottleneck becomes HTTP latency, where it earns its keep.

---

## 7. Rendering

- `avb/render/composer.py` — Pillow per-frame kinetic typography, pattern
  interrupts, caption wrapping (`max_w = W*0.90`, oversize words shrink).
- `avb/render/ffmpeg.py` — encode, concat (**absolute paths** in the concat
  manifest — relative paths are resolved against the manifest's directory
  and fail silently), mux.
- `avb/render/remotion.py` — alternative backend emitting TSX + JSON for
  teams that want a React timeline.

Captions are drawn with PIL's native `stroke_width` (an earlier 25-pass
outlines implementation cost 180s/scene; the native path is 7.2ms/frame).

---

## 8. Quality control

`avb/qc.py` runs after every render and reports:

- caption sync offset vs word timings
- silence gaps
- duration deviation from the IR estimate
- safe-area / readability violations

The report is part of `RunReport` and printed by the CLI (`qc: PASS/FAIL`).

---

## 9. Cost accounting

`CostLedger` accumulates `Cost(provider, kind, usd, units, unit)` entries.
Every provider reports what it spent; `RunReport.cost_by_kind` rolls it up.
LLM spend is tracked separately via `LLMResponse` token counts.

---

## 10. Extension points

| To add… | Do this |
|---|---|
| a new TTS vendor | subclass `Provider`, add to `VOICE_REGISTRY` |
| a new video model | subclass `_AsyncVideoBase`, implement `_submit`/`_poll`, add to `_REGISTRY` |
| a new edit intent | add to `INTENTS`, add paths to `ALLOWED_PATHS`, extend the rule router |
| a new style | add a `StyleSpec` in `styles.py` |
| a new config key | add the field to the dataclass in `config.py` — TOML/CLI/web pick it up automatically |

The `config.py` dataclass is the single source of truth for the schema: the
web UI renders its form from it, `avb config` validates against it, and
`avb.toml` round-trips through it.
