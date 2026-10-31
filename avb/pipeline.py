"""The orchestrator.

    script -> IR -> assets (cached by content hash) -> segments (cached) -> mp4

`run(..., only={...})` re-renders just the named scenes and re-concats. That
is the payoff of the whole architecture, and it is what makes conversational
editing viable instead of theoretical.
"""

from __future__ import annotations

import json
import sys
import os
import shutil
import threading
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np

from .hashing import short_hash
from .assets.base import Cache, CostLedger
from .assets.music import SynthMusic, render_track, sfx
from .assets.video import VideoGenError, build_video_provider, classify_video_error, progress_scope
from .assets.voice import ElevenLabsVoice, FormantVoice, build_voice_provider, speakable
from .ir import Project, Scene
from .config import Config
from .qc import run_qc
from .render import ffmpeg
from .render.composer import SceneAssets, render_scene_frames

TAIL_PAD = 0.22

# Bump whenever avb/render/*.py changes what a frame looks like. Baked into
# the segment cache key so a compositor fix actually reaches the output.
RENDER_VERSION = "3-caption-wrap"

# When the user opts out of the voiceover, every scene still needs a sensible
# on-screen duration and a silent placeholder so the asset cache (which keys
# off a real vo.wav existing) keeps working across conversational edits.
NO_VOICE_CLIP = 4.0

# Magic bytes of image formats the compositor can open. Anything else copied
# into bg.png (an mp4 from a vendor API, a truncated download, an error page)
# used to blow up PIL with "cannot identify image file" mid-render.
_IMAGE_MAGIC: tuple[tuple[int, bytes], ...] = (
    (0, b"\x89PNG"),
    (0, b"\xff\xd8\xff"),  # JPEG
    (0, b"GIF8"),
    (0, b"BM"),  # BMP
    (0, b"RIFF"),  # WebP (checked together with WEBP at offset 8)
)


def is_image_file(path: Path) -> bool:
    """Cheap magic-byte check; PIL itself stays out of the hot path."""
    try:
        with open(path, "rb") as f:
            head = f.read(16)
    except OSError:
        return False
    if head[8:12] == b"WEBP":
        return True
    return any(head.startswith(magic) for _, magic in _IMAGE_MAGIC)


def _hex_rgb(color: str) -> tuple[int, int, int]:
    h = (color or "#101820").lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    try:
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except (ValueError, IndexError):
        return 16, 18, 24


@dataclass
class SceneTiming:
    scene_id: str
    index: int
    beat: str
    start: float
    duration: float
    vo_start: float
    text: str


@dataclass
class RunReport:
    video: Path
    project_id: str
    style: str
    aspect: str
    total_duration: float
    scenes: list[SceneTiming]
    rendered: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)
    # Scenes that finished with a solid backdrop because video generation
    # failed after retries. The video is still watchable; these shots just
    # need re-running once the cause (balance, a flagged prompt) is fixed.
    warnings: list[dict] = field(default_factory=list)
    cost_usd: float = 0.0
    cost_by_kind: dict[str, float] = field(default_factory=dict)
    cache_stats: dict[str, float] = field(default_factory=dict)
    qc: dict = field(default_factory=dict)
    wall_seconds: float = 0.0


class Pipeline:
    def __init__(
        self,
        workdir: str | Path = ".avb",
        *,
        draft: bool = False,
        crf: int | None = None,
        preset: str | None = None,
        workers: int = 0,
        config: "Config | None" = None,
    ) -> None:
        from .config import load_config

        # load_config(), not Config(): it is the only one that pulls in .env,
        # which is where every API key lives. A bare Config() here meant the
        # CLI (`avb render`) saw no credentials at all while the web app --
        # which calls load_dotenv() at import -- worked fine. Same trap as
        # llm.get_client().
        self.config = config or load_config()
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.cache = Cache(self.workdir / "cache")
        self.ledger = CostLedger()
        self.draft = draft
        self.workers = int(workers) or int(self.config.render.workers)
        # Cap simultaneous vendor submissions so a burst of scenes does not
        # trip the provider's concurrency/rate limit. Distinct from
        # self.workers (process-pool size for the CPU-bound compositing pass);
        # this bounds the I/O-bound API fan-out during synth().
        self.max_concurrent_api = max(1, min(self.workers or 4, 6))

        # Provider selection: explicit config wins; otherwise the best
        # available thing, because falling back matters more than picking
        # "the best" -- vendors shut APIs down (Sora, Sept 2026).
        self.voice = build_voice_provider(self.config.voice.provider)
        self.video = build_video_provider(self.config.video.provider)
        self.music = SynthMusic()
        # Optional observer for the web UI: fn(dict) -> None. Receives
        # per-scene render progress plus everything the video provider
        # reports (submit bytes, vendor task id, poll status, download MB).
        self.video_progress_cb = None

        default_crf = self.config.render.draft_crf if draft else self.config.render.crf
        self.crf = crf if crf is not None else default_crf
        self.preset = preset or self.config.render.preset

    def describe(self) -> str:
        return (
            f"voice={self.voice.describe()} | video={self.video.describe()} | "
            f"music={self.music.describe()} | draft={self.draft}"
        )

    # -- paths -------------------------------------------------------------
    def _proj_dir(self, project: Project) -> Path:
        d = self.workdir / "projects" / project.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _scene_dir(self, project: Project, scene: Scene) -> Path:
        d = self._proj_dir(project) / scene.id
        d.mkdir(parents=True, exist_ok=True)
        return d

    # -- assets ------------------------------------------------------------
    def synth(
        self,
        project: Project,
        only: set[str] | None = None,
        reference_images: list[str | Path] | None = None,
        with_voice: bool = True,
    ) -> dict[str, SceneAssets]:
        targets = [s for s in project.scenes if only is None or s.id in only]
        # Scenes whose backdrop degraded to a solid colour because generation
        # failed after retries. Reset per call so a previous run cannot leak
        # stale warnings into this report. See RunReport.warnings.
        self.scene_warnings: list[dict] = []
        # Product reference images (three-view set) ride along with every
        # scene's video generation so the vendor keeps the product identity.
        refs = [Path(p) for p in (reference_images or []) if Path(p).is_file()]

        out: dict[str, SceneAssets] = {}
        out_lock = threading.Lock()
        warn_lock = threading.Lock()
        cb_lock = threading.Lock()
        # Thread-safe account-halt flag. Set the moment any scene hits an
        # account-scope failure (out of balance / bad key / quota). Scenes that
        # have not yet started submitting will skip the API entirely; scenes
        # already in flight are left to finish so the caller keeps whatever
        # real footage they return -- a better outcome than the old serial
        # "skip everything after the first failure".
        account_halt = threading.Event()
        # Bound simultaneous vendor submissions so a burst of scenes does not
        # trip the provider's concurrency/rate limit.
        api_sem = threading.Semaphore(max(1, self.max_concurrent_api))

        def _emit(info: dict, idx: int, sid: str) -> None:
            if self.video_progress_cb is None:
                return
            with cb_lock:
                self.video_progress_cb(
                    {"scene_index": idx + 1, "scene_total": len(targets),
                     "scene_id": sid, **info}
                )

        def _work(scene: "Scene", idx: int) -> None:
            sid = scene.id
            sd = self._scene_dir(project, scene)
            vo_path = sd / "vo.wav"
            bg_path = sd / "bg.png"

            # ③ Pipeline overlap: voiceover synthesis runs on a sub-thread so it
            # never blocks the main (pool) thread. Two cases:
            #   - If the script pinned scene.duration, we submit the video
            #     first and the VO thread overlaps the long remote-video wait.
            #   - If not pinned, we join the VO thread first to learn its
            #     natural length (used to size the video request), but the work
            #     still happened off the main path and overlaps sibling scenes'
            #     video waits thanks to the pool. Either way VO writes only this
            #     scene's vo_path and its ledger.add() is locked, so concurrent
            #     sibling workers are safe. If the synth itself throws we fall
            #     back to a silent clip so the scene still carries a vo.wav on
            #     disk (the asset cache and the audio mux both expect one).
            vo_box: dict = {}
            vo_exc: dict = {}
            def _run_vo() -> None:
                try:
                    vo_box["r"] = self._synth_vo(scene, vo_path, with_voice)
                except Exception as e:  # noqa: BLE001
                    vo_exc["e"] = e
            vo_t = threading.Thread(target=_run_vo, name=f"vo-{sid}", daemon=True)
            vo_t.start()

            try:
                # Capture just before submitting: a scene whose turn arrives
                # after the halt was raised must skip the pointless (and
                # possibly billed) API call.
                was_skipped = account_halt.is_set()
                if was_skipped:
                    raise VideoGenError(
                        "skipped: account-level failure earlier in the run",
                        retryable=False, scope="account",
                    )
                # ① Video generation is the I/O-bound, vendor-GPU part and used
                # to run serially. Fan it out across worker threads, gated by
                # api_sem. The composer only consumes the clip's poster frame,
                # so the requested length need not equal the VO length: when the
                # script pinned a scene duration we use it (full overlap), else
                # we wait for the VO thread to report the natural length first.
                if scene.duration is not None:
                    vid_seconds = float(scene.duration) + TAIL_PAD
                else:
                    vo_t.join()
                    _w, _d = vo_box.get("r", (None, None))
                    vid_seconds = (float(_d) if _d else NO_VOICE_CLIP) + TAIL_PAD
                with api_sem, progress_scope(
                    lambda info, _i=idx, _s=sid: _emit(info, _i, _s)
                ):
                    vres = self.video.generate(
                        prompt=scene.visual.prompt,
                        style=scene.visual.style or project.style,
                        seed=scene.visual.seed,
                        seconds=vid_seconds,
                        w=project.width,
                        h=project.height,
                        cache=self.cache,
                        reference_images=refs or None,
                    )
                self._write_bg(vres.path, bg_path, project)
                self.ledger.add(vres.cost.provider, "video", vres.cost.usd,
                                vres.cost.units, vres.cost.unit)
                _emit({"phase": "scene_done", "cached": vres.cached}, idx, sid)
            except Exception as e:  # noqa: BLE001 - one bad shot must not kill the render
                self._write_solid_bg(bg_path, project)
                err = e if isinstance(e, VideoGenError) else classify_video_error(e)
                if err.scope == "account":
                    # Out of balance / bad key / quota: stop submitting new
                    # jobs but let in-flight ones finish (they may still land),
                    # so the caller gets the most real footage possible.
                    account_halt.set()
                    print(f"[avb] scene {sid}: account-level failure ({err}); "
                          f"halting further submissions", file=sys.stderr)
                else:
                    # Scene-scoped failure (content policy, a prompt the vendor
                    # hates, retries exhausted on a flaky connection): keep the
                    # render alive with a solid brand backdrop.
                    print(f"[avb] scene {sid}: video failed ({err}); "
                          f"using solid backdrop", file=sys.stderr)
                with warn_lock:
                    self.scene_warnings.append({
                        "scene_id": sid,
                        "index": idx + 1,
                        "error": str(err)[:300],
                        "scope": err.scope,
                        "skipped": was_skipped,
                    })
                _emit({"phase": "scene_degraded", "error": str(err)[:200],
                       "scope": err.scope}, idx, sid)

            # Resolve the VO outcome now that video is done (or skipped). The
            # thread is already joined in the duration branch above, or finishes
            # here; either way we read its result exactly once.
            vo_t.join()
            if "e" in vo_exc:
                # Voiceover synthesis failed independently of the video: degrade
                # to a silent placeholder so the scene keeps a vo.wav.
                print(f"[avb] scene {sid}: voiceover failed ({vo_exc['e']}); "
                      f"silent clip", file=sys.stderr)
                dur = NO_VOICE_CLIP
                silent = np.zeros(int(NO_VOICE_CLIP * ffmpeg.SR), dtype=np.float32)
                ffmpeg.write_wav(vo_path, silent)
                words: list[dict] = []
            else:
                words, dur = vo_box.get("r", ([], NO_VOICE_CLIP))
            with out_lock:
                out[sid] = SceneAssets(bg=bg_path, vo=vo_path,
                                       vo_words=words, vo_duration=dur)

        if self.workers <= 1 or len(targets) <= 1:
            for idx, scene in enumerate(targets):
                _work(scene, idx)
        else:
            n = max(1, min(self.workers, len(targets)))
            with ThreadPoolExecutor(max_workers=n) as pool:
                list(pool.map(lambda si: _work(si[1], si[0]), list(enumerate(targets))))
        return out

    def _synth_vo(
        self, scene: "Scene", vo_path: Path, with_voice: bool
    ) -> tuple[list[dict], float]:
        """Synthesize one scene's voiceover to vo_path. Returns (words, dur).

        Thread-safe: the ledger.add() it calls is locked, and each scene writes
        its own vo_path, so concurrent scenes never collide.
        """
        words: list[dict] = []
        dur = 0.0
        if with_voice and scene.vo.text is not None:
            # Unpronounceable lines ("" / "..." / "--") go to the offline
            # synth, which renders a beat of silence. Every scene getting a
            # real vo.wav on disk matters: load_assets() keys cache reuse
            # off the file existing, so a missing one silently drops the
            # scene.
            speaker = self.voice if speakable(scene.vo.text) else FormantVoice()
            res = speaker.synth(
                scene.vo.text,
                voice=scene.vo.voice,
                speed=scene.vo.speed,
                energy=scene.vo.energy,
                cache=self.cache,
            )
            shutil.copyfile(res.path, vo_path)
            words, dur = res.words, res.duration
            self.ledger.add(res.cost.provider, "voice", res.cost.usd,
                            res.cost.units, res.cost.unit)
        else:
            # No voiceover requested: lay down a silent placeholder so the
            # cache, segment reuse, and audio mux all stay consistent, and
            # give the scene a default on-screen length instead of a 0.2s
            # blink. Captions are word-driven, so with no words there is
            # nothing to draw -- the renderer skips them anyway.
            dur = NO_VOICE_CLIP
            silent = np.zeros(int(NO_VOICE_CLIP * ffmpeg.SR), dtype=np.float32)
            ffmpeg.write_wav(vo_path, silent)
        return words, dur

    def _write_bg(self, src: Path, bg_path: Path, project: Project) -> None:
        """Guarantee bg_path is an image the compositor can open, come what may.

        AI video vendors return mp4/webm; a naive copyfile put video bytes in
        a file named .png and PIL died with "cannot identify image file" on
        the first scene. Now: real images are copied as-is, videos get their
        poster frame extracted, and if even that fails we paint a solid brand
        colour so the render degrades instead of dying.
        """
        src = Path(src)
        if is_image_file(src):
            shutil.copyfile(src, bg_path)
            return
        try:
            ffmpeg.extract_frame(src, bg_path)
            if is_image_file(bg_path):
                return
        except Exception as e:  # noqa: BLE001 - any failure falls through
            print(f"[avb] bg poster extraction failed ({e}); using solid colour",
                  file=sys.stderr)
        self._write_solid_bg(bg_path, project)

    @staticmethod
    def _write_solid_bg(bg_path: Path, project: Project) -> None:
        from PIL import Image

        img = Image.new("RGB", (project.width, project.height), _hex_rgb(project.brand.ink))
        img.save(bg_path, format="PNG")

    def _apply_target_duration(self, project: Project, assets: dict[str, SceneAssets], target: float) -> None:
        """Stretch/shrink scenes proportionally so the video runs ~target seconds.

        VO always plays at its natural speed from each scene's start, and
        captions ride the word-level timings, so scaling only ever changes
        how long a scene *lingers* after its line -- never lip/caption sync.
        Per-scene length is clamped to [1, 20]s so a 3-scene 60s target
        cannot produce one absurd 40s freeze frame.
        """
        naturals = {
            s.id: self.scene_duration(s, assets[s.id])
            for s in project.scenes
            if s.id in assets
        }
        total_natural = sum(naturals.values())
        if total_natural <= 0 or not naturals:
            return
        factor = max(0.1, target) / total_natural
        for s in project.scenes:
            d = naturals.get(s.id)
            if d is None:
                continue
            s.duration = round(min(20.0, max(1.0, d * factor)), 4)

    def load_assets(self, project: Project) -> dict[str, SceneAssets]:
        """Read back previously synthesized assets from the project dir."""
        out: dict[str, SceneAssets] = {}
        for scene in project.scenes:
            sd = self._proj_dir(project) / scene.id
            vo, bg = sd / "vo.wav", sd / "bg.png"
            if not (vo.is_file() and bg.is_file()):
                continue
            words: list[dict] = []
            dur = 0.0
            meta = sd / "meta.json"
            if meta.is_file():
                m = json.loads(meta.read_text(encoding="utf-8"))
                words, dur = m.get("words", []), float(m.get("duration", 0.0))
            out[scene.id] = SceneAssets(bg=bg, vo=vo, vo_words=words, vo_duration=dur)
        return out

    def _save_meta(self, project: Project, scene: Scene, assets: SceneAssets) -> None:
        sd = self._scene_dir(project, scene)
        (sd / "meta.json").write_text(
            json.dumps({"words": assets.vo_words, "duration": assets.vo_duration}, ensure_ascii=False),
            encoding="utf-8",
        )

    # -- rendering ---------------------------------------------------------
    def segment(
        self,
        project: Project,
        scene: Scene,
        assets: SceneAssets,
        *,
        global_start: float = 0.0,
        total: float = 1.0,
        with_captions: bool = True,
    ) -> tuple[Path, bool]:
        """Render one scene to mp4. Returns (path, was_rendered)."""
        return _render_segment(
            project, scene, assets,
            global_start=global_start, total=total,
            draft=self.draft, crf=self.crf, preset=self.preset,
            workdir=self.workdir, with_captions=with_captions,
        )

    @staticmethod
    def scene_duration(scene: Scene, assets: SceneAssets) -> float:
        d = scene.duration if scene.duration is not None else (assets.vo_duration + TAIL_PAD)
        return max(0.5, round(d, 4))

    def plan_timeline(self, project: Project, assets: dict[str, SceneAssets]) -> tuple[list[SceneTiming], float]:
        """Compute each scene's start/end on the global timeline.

        Runs before any rendering so the progress bar and music bed can be
        laid out against the real total duration.
        """
        timings: list[SceneTiming] = []
        t = 0.0
        for scene in project.scenes:
            a = assets.get(scene.id)
            if a is None:
                continue
            dur = self.scene_duration(scene, a)
            timings.append(
                SceneTiming(
                    scene_id=scene.id,
                    index=scene.index,
                    beat=scene.beat,
                    start=round(t, 4),
                    duration=dur,
                    vo_start=round(t, 4),
                    text=scene.vo.text,
                )
            )
            t += dur
        return timings, max(0.5, round(t, 4))

    def assemble(
        self, project: Project, assets: dict[str, SceneAssets], out: Path,
        with_captions: bool = True,
    ) -> tuple[Path, list[SceneTiming], float, list[str], list[str]]:
        timings, total = self.plan_timeline(project, assets)

        # Fan out per-scene rendering. Frame composition is pure-Python/PIL
        # and therefore GIL-bound, so a thread pool barely moves the needle
        # (measured 1.1x). Process pools actually cross that boundary --
        # frames are generated and encoded entirely inside the worker.
        by_id = {s.id: s for s in project.scenes}
        order: list[str] = [tm.scene_id for tm in timings]
        tmap = {t.scene_id: t for t in timings}

        def _one(sid: str) -> tuple[str, Path, bool]:
            return self.segment(
                project, by_id[sid], assets[sid],
                global_start=tmap[sid].start, total=total, with_captions=with_captions,
            )

        workers = self.workers if self.workers > 0 else min(8, max(2, (os.cpu_count() or 4)))
        results: dict[str, tuple[Path, bool]] = {}
        # Process pools need to re-import __main__ in the child. When the
        # parent was launched from stdin or a notebook there is no real
        # module to re-import and the pool dies with BrokenProcessPool, so
        # fall back to threads (slower, GIL-bound) rather than failing.
        can_fork = bool(getattr(sys.modules.get("__main__"), "__file__", None))
        if workers > 1 and len(order) > 1 and can_fork:
            try:
                with ProcessPoolExecutor(max_workers=workers) as pool:
                    futs = {
                        pool.submit(
                            _segment_job, project, by_id[sid], assets[sid],
                            tmap[sid].start, total,
                            self.draft, self.crf, self.preset, self.workdir,
                            with_captions,
                        ): sid
                        for sid in order
                    }
                    for fut in as_completed(futs):
                        sid = futs[fut]
                        results[sid] = fut.result()
            except (BrokenProcessPool, PermissionError, OSError) as e:
                # Windows spawn re-imports __main__; a caller without an
                # `if __name__ == "__main__"` guard (or a restricted host)
                # tears the pool down mid-run. Rendering is idempotent and
                # content-addressed, so falling back to serial costs only
                # wall-clock -- never correctness.
                print(f"[avb] process pool unavailable ({type(e).__name__}), "
                      f"rendering serially", file=sys.stderr)
                results.clear()
                for sid in order:
                    results[sid] = _one(sid)
        elif workers > 1 and len(order) > 1:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for sid, res in zip(order, pool.map(_one, order)):
                    results[sid] = res
        else:
            for sid in order:
                results[sid] = _one(sid)

        segs = [results[sid][0] for sid in order]
        rendered = [sid for sid in order if results[sid][1]]
        reused = [sid for sid in order if not results[sid][1]]

        joined = out.with_name(out.stem + "._joined.mp4")

        if len(segs) == 1:
            shutil.copyfile(segs[0], joined)
        else:
            ffmpeg.concat(segs, joined)

        # ---- audio bed: VO + music (ducked) + SFX -------------------------
        mix = self._mix_audio(project, assets, timings, total)
        wav = out.with_name(out.stem + "._mix.wav")
        ffmpeg.write_wav(wav, mix)
        ffmpeg.mux_audio(joined, wav, out)

        # keep intermediates; they are the cache
        return out, timings, total, rendered, reused

    def _mix_audio(self, project: Project, assets: dict[str, SceneAssets], timings: list[SceneTiming], total: float) -> np.ndarray:
        sr = ffmpeg.SR
        n = int(total * sr) + sr // 4
        vo = np.zeros(n, dtype=np.float32)
        fx = np.zeros(n, dtype=np.float32)

        for tm in timings:
            a = assets.get(tm.scene_id)
            if not a or not a.vo.is_file():
                continue
            y = ffmpeg.decode_audio(a.vo, sr)
            s = int(tm.vo_start * sr)
            e = min(n, s + len(y))
            vo[s:e] += y[: e - s]

            for sfx_spec in next((s.sfx for s in project.scenes if s.id == tm.scene_id), []):
                y2 = sfx(sfx_spec.name, sr)
                s2 = int((tm.start + sfx_spec.at) * sr)
                e2 = min(n, s2 + len(y2))
                if s2 < n:
                    fx[s2:e2] += y2[: e2 - s2]

        bed = render_track(project.music.track, total + 1.0)[:n]

        # Sidechain-style ducking: derive a gain envelope from VO energy so
        # the music never fights the voice. This is the one audio decision
        # that most separates "produced" from "assembled".
        if project.music.duck_under_vo:
            win = int(0.05 * sr)
            env = np.sqrt(np.convolve(vo * vo, np.ones(win) / win, mode="same"))
            env = env / (env.max() + 1e-6)
            k = np.exp(-np.arange(0, win * 6) / (sr * 0.12))
            env = np.convolve(env, k / k.sum(), mode="same")
            gain = 1.0 - 0.72 * np.clip(env * 1.9, 0.0, 1.0)
        else:
            gain = np.ones(n, dtype=np.float32)

        music = bed * project.music.volume * gain
        out = vo * 0.95 + music + fx * 0.55
        peak = float(np.max(np.abs(out))) or 1.0
        if peak > 0.99:
            out = out / peak * 0.98
        return out.astype(np.float32)

    # -- top level ---------------------------------------------------------
    def run(
        self,
        project: Project,
        out: str | Path,
        only: set[str] | None = None,
        target_seconds: float | None = None,
        reference_images: list[str | Path] | None = None,
        with_voice: bool = True,
        with_captions: bool = True,
    ) -> RunReport:
        t0 = time.time()
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)

        # Fresh ledger per run, otherwise costs from earlier renders leak
        # into this one's report and the numbers stop meaning anything.
        self.ledger = CostLedger()

        fresh = self.synth(project, only=only, reference_images=reference_images, with_voice=with_voice)
        assets = self.load_assets(project)
        # In-memory results are authoritative: on a cold run there is no
        # meta.json yet, and on a warm run we only re-synthesized `only`.
        assets.update(fresh)
        for sid, a in fresh.items():
            self._save_meta(project, next(s for s in project.scenes if s.id == sid), a)

        if target_seconds:
            self._apply_target_duration(project, assets, float(target_seconds))

        video, timings, total, rendered, reused = self.assemble(
            project, assets, out, with_captions=with_captions
        )

        # Attribute render compute. Remotion bills $0.01/render; self-hosted
        # lands in the same ballpark once you amortise the box.
        self.ledger.add("renderer", "render", 0.010 * max(1, len(rendered)), len(rendered), "segment")

        qc = run_qc(project, assets, timings, total)

        return RunReport(
            video=video,
            project_id=project.id,
            style=project.style,
            aspect=project.aspect,
            total_duration=round(total, 3),
            scenes=timings,
            rendered=rendered,
            reused=reused,
            cost_usd=self.ledger.total(),
            cost_by_kind=self.ledger.by_kind(),
            cache_stats=self.cache.stats(),
            qc=qc,
            warnings=list(getattr(self, "scene_warnings", []) or []),
            wall_seconds=round(time.time() - t0, 2),
        )



def _segment_job(
    project: Project,
    scene: Scene,
    assets: SceneAssets,
    global_start: float,
    total: float,
    draft: bool,
    crf: int,
    preset: str,
    workdir: str | Path,
    with_captions: bool = True,
) -> tuple[Path, bool]:
    """Process-pool entry point. Must stay module-level to be picklable."""
    return _render_segment(
        project, scene, assets,
        global_start=global_start, total=total,
        draft=draft, crf=crf, preset=preset,
        workdir=workdir, with_captions=with_captions,
    )


def _render_segment(
    project: Project,
    scene: Scene,
    assets: SceneAssets,
    *,
    global_start: float,
    total: float,
    draft: bool,
    crf: int,
    preset: str,
    workdir: str | Path,
    with_captions: bool = True,
) -> tuple[Path, bool]:
    """Render one scene to mp4. Returns (path, was_rendered)."""
    # RENDER_VERSION is in the key on purpose. The segment cache is keyed
    # on scene semantics; changing the compositor without bumping it
    # silently reuses stale frames, which is exactly how a caption fix
    # ends up "not working". `cap` lets toggling captions on/off invalidate
    # the cached frames instead of reusing stale ones.
    key = short_hash({
        **scene.semantic(),
        "dur": scene.duration,
        "w": project.width,
        "h": project.height,
        "fps": project.fps,
        "render": RENDER_VERSION,
        "cap": 1 if with_captions else 0,
        # Hash the actual backdrop bytes. Normally this is stable (same
        # inputs -> same cached clip -> same poster frame), but when video
        # generation degraded to a solid placeholder it MUST NOT be reused
        # later as if it were the real shot -- without this the first
        # successful re-render after a top-up would be silently discarded in
        # favour of the cached placeholder segment.
        "bg": short_hash({"n": assets.bg.stat().st_size}) if assets.bg and assets.bg.is_file() else None,
    })
    seg_dir = Path(workdir) / "projects" / project.id / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)
    out = seg_dir / f"{scene.id}.{key}.mp4"

    if out.is_file() and out.stat().st_size > 0:
        return out, False

    w, h = project.width, project.height
    frames = render_scene_frames(
        scene,
        assets,
        project,
        w,
        h,
        project.fps,
        global_start=global_start,
        total=total,
        draft=draft,
        with_captions=with_captions,
    )
    tmp = out.with_suffix(".tmp.mp4")
    ffmpeg.encode_segment(
        frames,
        int(w * (0.5 if draft else 1.0)),
        int(h * (0.5 if draft else 1.0)),
        project.fps,
        assets.vo if assets.vo and assets.vo.is_file() else None,
        tmp,
        crf=crf,
        preset=preset,
    )
    tmp.replace(out)
    return out, True

