# Usage

From zero to a rendered video ad, and then how to revise it by talking to
it.

---

## 0. Requirements

| | |
|---|---|
| Python | 3.11+ (3.13 tested) |
| ffmpeg | on `PATH` (with `ffprobe`) |
| OS | macOS / Linux / Windows |
| GPU | not required |
| Network | needed for DeepSeek + edge-tts; everything else runs offline |

```bash
cd dtc-ads-video-ai-generator
python -m pip install -e ".[all]"     # core + edge-tts + rich
```

`[all]` is optional. The core install works with zero optional packages and
degrades to an offline formant voice.

---

## 1. Configure

Three equivalent ways. Pick one.

### A. Files

```bash
cp .env.example .env
# edit .env, put your key in DEEPSEEK_API_KEY=...
```

Then optionally edit `avb.toml`. The shipped default already points at
DeepSeek:

```toml
[llm]
provider    = "deepseek"
model       = "deepseek-flash"
base_url    = "https://api.deepseek.com/v1"
api_key_env = "DEEPSEEK_API_KEY"
```

### B. CLI

```bash
python -m avb.cli config init                       # writes avb.toml + .env.example
python -m avb.cli config set llm.model deepseek-v4-pro
python -m avb.cli config set voice.voice zh-CN-XiaoxiaoNeural
python -m avb.cli config set video.provider seedance
python -m avb.cli config get llm.model
python -m avb.cli config list                       # full effective config, JSON
```

Comments in `avb.toml` survive `config set`.

### C. Local web UI

```bash
python -m avb.cli config web                        # http://127.0.0.1:8787
```

Renders a form from the config schema, shows live provider status, and has
**Test LLM** / **Test TTS** buttons. Writes the same `avb.toml`. Binds to
localhost only.

---

## 2. Verify

```bash
python -m avb.cli doctor
```

Expected (with a DeepSeek key and `edge-tts` installed):

```
llm connectivity   OK (deepseek-flash, 0.7s)
voice provider     edge-tts (available)
video provider     seedance (available)
ffmpeg             /usr/bin/ffmpeg
```

```bash
python -m avb.cli providers          # every provider and whether it is live
python -m avb.cli tts "Hello from the pipeline."
```

---

## 3. Render

```bash
python -m avb.cli render my_script.txt --draft --out out/draft.mp4
python -m avb.cli render my_script.txt --out out/final.mp4
python -m avb.cli render my_script.txt --style comic --aspect 4:5
python -m avb.cli render my_script.txt --llm          # LLM scene breakdown
```

| Flag | Meaning |
|---|---|
| `--draft` | half res, higher CRF — iterate fast |
| `--llm` | use the LLM parser instead of the heuristic one |
| `--style` | `dr_vsl` `comic` `vector` `claymation` `talking_head` `ugc` `kinetic` `cinematic` `song` |
| `--aspect` | `9:16` `4:5` `1:1` `16:9` |
| `--only s003,s004` | (re)render just these scenes |
| `--out` | copy the finished mp4 here |
| `--llm-model` / `--voice` / `--video` | one-shot provider overrides |

Script format is plain text: paragraphs become shots, sentence length drives
duration. Look at `demo/scripts/vsl_supplement.txt` for the shape.

### CLI vs demo

`demo/run_demo.py` is a scripted tour (7 stages, style matrix, contact
sheet) built on exactly these commands. Use the CLI for real work.

---

## 4. Edit by talking to it

This is the feature the brief marks "Extremely Important".

```bash
python -m avb.cli edit project.json "Spice up the hook and make the first 10 seconds punchier"
python -m avb.cli edit project.json "make the mechanism section feel like a comic book"
python -m avb.cli edit project.json "swap the music for something darker"
python -m avb.cli edit project.json "throughout the video, make it feel more premium"
python -m avb.cli edit project.json "..." --rules-only     # skip the LLM router
```

Output:

```
intent:    pacing
scenes:    4 -> s001, s002, s003, s004
ops:       vo.speed, visual.intensity, transition
why:       [llm:deepseek-flash] I tightened the opening 10 seconds with a slightly
           faster, more energetic voiceover and sharper visual delivery...
stale:     4 scene(s) will re-render
```

Then re-render just those:

```bash
python -m avb.cli render my_script.txt --only s001,s002,s003,s004 --out out/v2.mp4
```

### How targeting works

```
explicit timestamp  >  narrative beat  >  whole video
"first 10 seconds"     "the hook"         "throughout"
```

Scene beats (`hook` / `problem` / `mechanism` / `proof` / `offer` / `cta`)
are assigned at parse time and are what makes "make the proof section pop"
a precise instruction.

### Safety rails

The LLM proposes; the code disposes. It can only pick from a closed list of
intents, selectors and patch paths (`avb/llm_edit.py:ALLOWED_PATHS`). Enum
values snap to known ones (`comic_book` → `comic`). Anything that sanitises
to zero ops falls back to the rule-based plan. A bad model answer degrades
to a dumber edit, never to a corrupted IR.

---

## 5. Model reference

### LLM (`[llm]`)

| provider | base_url | notes |
|---|---|---|
| `deepseek` | `https://api.deepseek.com/v1` | `deepseek-flash` (fast) / `deepseek-v4-pro` |
| `openai` | `https://api.openai.com/v1` | `gpt-4o-mini` etc. |
| `moonshot` | `https://api.moonshot.cn/v1` | |
| `zhipu` | `https://open.bigmodel.cn/api/paas/v4` | |
| `ollama` | `http://localhost:11434/v1` | needs `--jinja` |

DeepSeek V4 is a **reasoning** family: it spends tokens thinking before it
answers. Two consequences:

- `max_tokens` must leave headroom or `content` comes back empty (the client
  raises a clear error when it detects this).
- `effort = "low"` is cheaper and faster and is plenty for edit planning.

### Voice (`[voice]`)

| provider | cost | word timings | quality |
|---|---|---|---|
| `edge` | free | yes (WordBoundary) | very good |
| `elevenlabs` | ~$0.18/1k chars | yes (exact) | best |
| `formant` | free | estimated | robotic (offline fallback) |

edge-tts presets: `en-US-AriaNeural` `en-US-GuyNeural` `en-US-JennyNeural`
`zh-CN-XiaoxiaoNeural` `zh-CN-YunxiNeural` `zh-CN-YunjianNeural` and ~400
more (`edge-tts --list-voices`).

### Video (`[video]`)

| provider | env var | ~$/sec (API) | status |
|---|---|---|---|
| `seedance` | `SEEDANCE_API_KEY` (+ `SEEDANCE_MODEL`) | 0.03–0.15 | adapter ready, default pick |
| `jimeng` / `dreamina` | `VOLC_ACCESS_KEY` + `VOLC_SECRET_KEY` | ~0.10 | adapter ready (HMAC, not Bearer) |
| `kling` | `KLING_API_KEY` | 0.08–0.42 | adapter ready |
| `veo` | `GOOGLE_API_KEY` | ~0.40 | adapter ready |
| `bailian` / `wan` | `BAILIAN_API_KEY` | ~0.08 | adapter ready |
| `minimax` / `hailuo` | `MINIMAX_API_KEY` | ~0.06 | adapter ready |
| `zhipu` / `cogvideox` | `ZHIPU_API_KEY` | ~0.07 | adapter ready |

No offline provider exists: with none of these set, `build_video_provider()`
raises `ProviderUnavailable` naming the env vars to fill in. Set keys in
`.env` (next to `avb.toml`) or in the web settings page — `avb web`.

See `docs/PROVIDERS.md` for the full selection rationale and the open-source
landscape.

---

## 6. What a run produces

```
.avb/
  cache/                    content-addressed assets (survives across runs)
  projects/<id>/
    <scene>/segment.mp4
    meta.json               per-scene timings + cost
  out/<id>.mp4
```

Delete `.avb/cache/` to force a full rebuild. Delete a single namespace
(`.avb/cache/voice-edge/`) to rebuild just that asset class.

`RunReport` from the Python API carries: total duration, per-scene timings,
rendered vs reused counts, cost by kind, cache hit rate, and the QC report.

---

## 7. Python API

```python
from avb import parse_script, Pipeline, plan_edit_llm, apply_plan, invalidate
from avb.config import load_config

cfg = load_config()                          # avb.toml + .env + env
project = parse_script(open("script.txt").read(), style="dr_vsl")

pipe = Pipeline(workdir=".avb", config=cfg)
report = pipe.run(project, "out/v1.mp4")

plan = plan_edit_llm(project, "make the hook punchier", report.scenes)
patched = apply_plan(project, plan)
stale = invalidate(project, patched)         # -> {"stale_scenes": [...]}

pipe.run(patched, "out/v2.mp4", only=set(stale["stale_scenes"]))
```

---

## 8. Tests

```bash
python -m pytest -q
```

16 smoke tests, no network, no ffmpeg. They cover the config cascade,
comment-preserving writes, `.env` loading, JSON extraction, edit-plan
sanitisation (the guard rails), provider fallback, and the content-addressed
cache.

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `LLM not configured: set $DEEPSEEK_API_KEY` | `.env` not loaded or key missing | `avb doctor`; check `.env` sits next to `avb.toml` |
| `LLM returned empty content` | `max_tokens` eaten by reasoning | raise `[llm].max_tokens` to 8000+ or set `effort = "low"` |
| `llm connectivity FAILED (401)` | wrong key | re-check `DEEPSEEK_API_KEY` |
| voice falls back to `formant` | `edge-tts` not installed or no network | `pip install edge-tts`; check `avb doctor` |
| `ffmpeg: command not found` | ffmpeg missing | install it; `avb doctor` verifies |
| edit returns `intent: unknown` | phrasing the router did not understand | try rephrasing, or drop `--rules-only`; the LLM path handles far more than the rules |
| render is slow | full re-render | use `--only` with the stale set from the edit; check `reused:` count |
| `WindowsError` on cache writes | parallel cache races (older versions) | upgrade — `Cache.put` is now atomic |
| `No audio was received. Please verify that your parameters are correct.` | edge-tts got a line with nothing to pronounce (`""`, `...`, `--`, `♪`) | harmless now — that line renders as a beat of silence via the offline voice. To silence the warning, delete punctuation-only separator lines from the script |

---

## 10. Known limits

- **edge-tts is unofficial.** Fine for a portfolio and for cost-sensitive
  volume; use ElevenLabs or self-hosted CosyVoice/IndexTTS for anything
  with an SLA.
- **Formant voice is a synthesiser, not a model.** It exists so the pipeline
  runs with zero credentials and zero network. It sounds like 1998.
- **There is no offline video provider.** Every clip comes from a hosted
  model, so a run without a key fails with the exact env var to set. Add the
  key in the web settings page (`avb web`) or in `.env` next to `avb.toml`.
- **Compliance is your job.** FTC endorsement guides and Meta ad policy
  apply to what you ship. This pipeline does not audit claims.
- **Parallelism is ~1.05×.** See `docs/ARCHITECTURE.md` §6 for the measured
  numbers and why.
