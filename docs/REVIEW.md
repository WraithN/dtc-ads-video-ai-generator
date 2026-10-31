# Engineering Review — dtc-ads-video-ai-generator

> Scope: the full web app (`web/server.py` + `web/frontend`) and the `avb`
> pipeline package, as of v0.3.0. This is a local, single-user DTC ad-video
> generator: paste a product brief → four ad-script concepts → per-scene
> storyboard → render → download. The review was triggered after a long stretch
> of feature work, to catch issues before they ship in a portfolio demo.

## Verdict

**Solid for its stated purpose.** The core design — a SceneGraph IR that is the
single source of truth, with content-addressed caching on top — is the right
call and is implemented competently. The work that followed the review
(security, restart-safe resume, i18n and IR round-trip guards) closed every
issue that mattered. Two P3 items remain intentionally accepted as acceptable
for a local single-user tool.

## Architecture strengths

- **IR as the only editable artifact.** Pixels/frames are a pure function of the
  IR plus a cache of generated assets. This is what makes conversational
  editing, partial re-render, and rollback all cheap. (`avb/ir.py`)
- **Content-addressed scene cache.** `hashing.canonical()` uses `sort_keys` +
  `separators` so the semantic hash is stable; the segment cache key correctly
  folds in `w/h/fps/render_version/captions/bg`, so changing resolution does
  **not** silently reuse a wrong frame. (`avb/pipeline.py`, `avb/assets/video.py`)
- **Cost-awareness.** Video generation itself has its own cache keyed by
  `short_hash(...)`, so a resume re-runs only failed scenes and serves the rest
  from cache — a mid-balance crash does not re-spend on finished shots.
- **Visible degradation.** When the LLM fails, the reason is surfaced to the UI
  instead of being swallowed — the user never mistakes a template for AI output.

## Findings and resolutions

| Sev | Issue | Status |
|---|---|---|
| 🔴 P0 | `/api/video/{filename}` joined the URL straight into a path with no validation → arbitrary file read (`.env`, `history.db`, source). | **Fixed** — resolves and asserts the file stays inside a module-level `VIDEO_DIR`; anything else is 404. (`web/server.py`) |
| 🟠 P1 | "Resume after restart" was documented but not implemented: `_run_resume` only rebuilt the IR from the in-memory `_projects` dict, which is empty after a restart. | **Fixed** — renders persist `project.to_json()` into a new `project_json` column (with an `ALTER` migration for old DBs); resume rebuilds via `Project.from_json()` when the in-memory IR is gone. (`web/history_db.py`, `web/server.py`) |
| 🟡 P2 | i18n keys are plain strings (`t('x')`); a missing key renders the raw key at runtime (the "需求 D" bug). TypeScript's `en: typeof zh` catches en-side gaps but not a key absent from *both* locales. | **Fixed** — `scripts/i18n-check.mjs` extracts every key from `zh.ts`/`en.ts`, scans all `t()`/`i18n.t()` call sites, and asserts each exists; wired as `npm run check:i18n` and a pytest wrapper. |
| 🟢 P3 | `_update_job` is called from worker threads (ticker + completion) for the same job; the update+snapshot was not atomic. | **Fixed** — wrapped in a module-level `threading.Lock`. |
| 🟢 P3 | No guard against two jobs running at once; they would mutate the shared `_projects`/`_plans` maps keyed by plan id and corrupt each other. | **Fixed** — a single-flight guard (`_require_no_active_job` → 409) on `/api/render`, `/api/render/resume`, `/api/generate`, enforcing the documented "one task at a time". |
| 🟢 P3 | `Project.from_dict` splatted nested dicts into dataclass constructors with `**kwargs`; a newer schema's extra field would raise `TypeError` when an older build loads the persisted `project_json`. | **Fixed** — a `_pick(cls, d)` helper keeps only fields the dataclass declares, so unknown keys are ignored and missing keys default. |
| ℹ️ | `docs/WEBAPP.md` previously claimed `avb/ir.py` had no `from_dict()`. | **Corrected** — it has `from_dict`/`from_json`; the note now describes the real restart-safe resume. |

## Verification

- Full Python suite: **99 passed** (covers IR round-trip & tolerance, history
  store, video endpoint traversal + single-flight, resume semantics, server
  concurrency, plus the pre-existing storyboard/brief/concurrency tests).
- `tsc --noEmit` and `vite build` pass; the i18n check passes (352 keys, 224
  static + 10 dynamic references).
- Negative checks were run during development: injecting a missing i18n key
  fails the check with a precise message; a stale `project_json` with extra
  fields still loads.

## Notes for a production hardening pass (out of scope here)

- The shared dicts are still module globals; the lock + single-flight guard are
  sufficient for local single-user use. A multi-user deployment would want a
  proper job queue and per-user isolation.
- `_pick` drops unknown keys silently; if schema changes are frequent, consider
  logging a warning on dropped keys so drift is observable rather than silent.
