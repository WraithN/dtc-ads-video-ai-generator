"""One-command demo.

    python demo/run_demo.py
    python demo/run_demo.py --draft          # 540x960 @ 24fps, for iteration
    python demo/run_demo.py --style comic    # render the other style adapters

Runs the full pipeline end to end, then demonstrates the part the client
called "extremely important": conversational editing with partial
re-rendering. Watch the segment counts -- that is the whole thesis.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from avb.edit import apply_plan, invalidate, plan_edit  # noqa: E402
from avb.ir import Project  # noqa: E402
from avb.parse import parse_script  # noqa: E402
from avb.pipeline import Pipeline  # noqa: E402
from avb.styles import STYLES  # noqa: E402

SCRIPTS = ROOT / "demo" / "scripts"
OUT = ROOT / "demo" / "out"


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------


def rule(title: str = "") -> None:
    print("\n" + "=" * 74)
    if title:
        print(title)
        print("=" * 74)


def scene_table(project: Project) -> None:
    print(f"{'#':>3}  {'scene':<7}{'beat':<11}{'start':>7}{'dur':>7}  {'kind':<8}{'motion':<10} voice")
    print("-" * 74)
    for s in project.scenes:
        print(
            f"{s.index:>3}  {s.id:<7}{s.beat:<11}{'':>7}{'':>7}  {s.visual.kind:<8}"
            f"{s.visual.motion:<10} {s.vo.voice}"
        )
    print(f"\n{len(project.scenes)} scenes")


def cost_table(report) -> None:
    print(f"{'kind':<12}{'usd':>12}")
    print("-" * 74)
    for k, v in sorted(report.cost_by_kind.items()):
        print(f"{k:<12}{v:>12.4f}")
    print("-" * 74)
    print(f"{'TOTAL':<12}{report.cost_usd:>12.4f}")


def qc_table(report) -> None:
    q = report.qc
    status = "PASS" if q["passed"] else "FAIL"
    print(f"QC: {status}   errors={q['errors']}  warnings={q['warnings']}")
    for i in q["issues"][:12]:
        print(f"  [{i['severity']:<5}] {i['scene']}  {i['code']}: {i['detail']}")


# --------------------------------------------------------------------------
# demo
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="dtc-ads-video-ai-generator end-to-end demo")
    ap.add_argument("--script", default=str(SCRIPTS / "vsl_supplement.txt"))
    ap.add_argument("--style", default="dr_vsl", choices=sorted(STYLES))
    ap.add_argument("--aspect", default="9:16", choices=["9:16", "4:5", "1:1", "16:9"])
    ap.add_argument("--draft", action="store_true", help="half resolution, faster")
    ap.add_argument("--workdir", default=str(ROOT / ".avb"))
    ap.add_argument("--workers", type=int, default=0, help="parallel segment renders (0 = auto)")
    ap.add_argument(
        "--stages",
        default="1,2,3,4,5,6,7",
        help=(
            "comma-separated stage ids to run: 1=parse 2=providers 3=v1  "
            "4=edit#1 5=edit#2 6=styles 7=scene-map. Split this when you "
            "need to fit the run inside a hard wall-clock limit -- later "
            "stages reuse the on-disk segment cache from earlier ones."
        ),
    )
    args = ap.parse_args()
    want = {s.strip() for s in args.stages.split(",") if s.strip()}

    OUT.mkdir(parents=True, exist_ok=True)
    script = Path(args.script).read_text(encoding="utf-8")

    rule("1. SCRIPT -> SceneGraph IR")
    project = parse_script(
        script,
        project_id="demo_vsl",
        name="NutriLean Restore - DR VSL",
        style=args.style,
        aspect=args.aspect,
    )
    print(f"style={project.style}  aspect={project.aspect}  {project.width}x{project.height} @ {project.fps}fps")
    scene_table(project)
    print("\nFirst scene IR:")
    print(json.dumps(project.to_dict()["scenes"][0], indent=2, ensure_ascii=False))

    rule("2. PROVIDERS")
    # No offline video path exists any more, so check before building the
    # pipeline -- otherwise the demo dies inside Pipeline.__init__ with a
    # stack trace instead of telling you what to do.
    from avb.assets.video import ProviderUnavailable, build_video_provider

    try:
        vid = build_video_provider()
    except ProviderUnavailable as e:
        print(f"video provider : NOT CONFIGURED\n\n{e}\n")
        print("Set one key in .env, or use the web settings page (`avb web`):")
        print("  SEEDANCE_API_KEY / KLING_API_KEY / VOLC_ACCESS_KEY+VOLC_SECRET_KEY")
        print("  GOOGLE_API_KEY / BAILIAN_API_KEY / MINIMAX_API_KEY / ZHIPU_API_KEY")
        print("See docs/PROVIDERS.md for where each one comes from.")
        return 2

    pipe = Pipeline(args.workdir, draft=args.draft, workers=args.workers)
    print(pipe.describe())
    print("\n(every provider is a hosted model; swapping vendors is a config change,")
    print(" never a code change)")

    # Stage 3 is the base for 4/5/7; run it whenever any of them are wanted.
    r1 = None
    if want & {"3", "4", "5", "7"}:
        rule("3. ASSET SYNTHESIS + RENDER  (v1)")
        t0 = time.time()
        r1 = pipe.run(project, OUT / f"v1_{args.style}_{args.aspect.replace(':','x')}.mp4")
        print(f"duration : {r1.total_duration:.2f}s")
        print(f"rendered : {len(r1.rendered)} segments   reused: {len(r1.reused)}")
        print(f"cache    : {r1.cache_stats}")
        cost_table(r1)
        qc_table(r1)
        print(f"\nwall: {time.time()-t0:.1f}s  ->  {r1.video}")

    if "4" in want:
        rule("4. CONVERSATIONAL EDIT #1")
        instr1 = "Make the first 10 seconds more engaging with faster cuts and more aggressive text animations."
        print(f"user > {instr1}\n")

        plan1 = plan_edit(project, instr1, r1.scenes)
        print("plan:")
        print(json.dumps(plan1.to_dict(), indent=2, ensure_ascii=False))

        inv1 = invalidate(project, apply_plan(project, plan1))
        print(f"\ninvalidation: {json.dumps(inv1, ensure_ascii=False)}")

        p2 = apply_plan(project, plan1)
        n_inv = len(inv1["invalidated"])
        print(
            f"\npatch touches {n_inv}/{len(project.scenes)} scenes -> "
            f"{100.0 * (1 - n_inv / max(1, len(project.scenes))):.0f}% of the video is reused as-is"
        )

        r2 = pipe.run(p2, OUT / f"v2_{args.style}_faster_intro.mp4", only=set(inv1["invalidated"]))
        print(f"\nrendered : {len(r2.rendered)} segments   reused: {len(r2.reused)}")
        print(f"cache    : {r2.cache_stats}")
        print(f"-> {r2.video}")

    if "5" in want:
        rule("5. CONVERSATIONAL EDIT #2")
        instr2 = "Make the mechanism section look like a comic book animation."
        print(f"user > {instr2}\n")

        plan2 = plan_edit(p2, instr2, r2.scenes)
        print("plan:")
        print(json.dumps(plan2.to_dict(), indent=2, ensure_ascii=False))

        inv2 = invalidate(p2, apply_plan(p2, plan2))
        print(f"\ninvalidation: {json.dumps(inv2, ensure_ascii=False)}")

        p3 = apply_plan(p2, plan2)
        r3 = pipe.run(p3, OUT / f"v3_{args.style}_comic_mechanism.mp4", only=set(inv2["invalidated"]))
        print(f"\nrendered : {len(r3.rendered)} segments   reused: {len(r3.reused)}")
        print(f"-> {r3.video}")

    if "6" in want:
        rule("6. SAME SCRIPT, DIFFERENT STYLE ADAPTERS")
        print("Same IR, same voiceover, different visual parameter bundles.\n")
        rows = []
        for sid in ("dr_vsl", "comic", "vector", "claymation"):
            proj_s = parse_script(script, project_id=f"demo_{sid}", name=sid, style=sid, aspect=args.aspect)
            rep = pipe.run(proj_s, OUT / f"style_{sid}_{args.aspect.replace(':','x')}.mp4")
            rows.append((sid, len(proj_s.scenes), rep.total_duration, len(rep.rendered), len(rep.reused), rep.cost_usd))
            print(
                f"  {sid:<12} scenes={len(proj_s.scenes):<3} dur={rep.total_duration:6.2f}s  "
                f"rendered={len(rep.rendered):<3} reused={len(rep.reused):<3}  ${rep.cost_usd:.4f}"
            )

    if "7" in want and r1 is not None:
        rule("7. FINAL SCENE MAP (v1)")
        print(f"{'#':>3}  {'beat':<11}{'start':>7}{'dur':>7}  text")
        print("-" * 74)
        for tm in r1.scenes:
            txt = tm.text if len(tm.text) <= 42 else tm.text[:39] + "..."
            print(f"{tm.index:>3}  {tm.beat:<11}{tm.start:>7.2f}{tm.duration:>7.2f}  {txt}")

    print("\nOutputs in:", OUT)
    for p in sorted(OUT.glob("*.mp4")):
        print("  ", p.name, f"{p.stat().st_size/1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
