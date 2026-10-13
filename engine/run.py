"""Canonical entry: detect → assemble with JSONL progress."""

from __future__ import annotations

import argparse
import json
import traceback
from pathlib import Path

from engine import progress
from engine.assemble import assemble
from engine.config import load_config, list_games
from engine.detect import detect_all
from engine.devices import probe_devices
from engine.config import ffprobe_bin
from engine.ocr import get_backend_name


def run_job(cfg) -> Path:
    progress.stage("init", pct=0, message=json.dumps(cfg.as_job_snapshot()))
    progress.emit(ocr_backend=get_backend_name())
    results = detect_all(cfg)
    if not results or not any(r.get("events") for r in results):
        progress.error("No events detected")
        progress.done(ok=False, reason="no_events")
        raise SystemExit(2)
    out_dir = assemble(results, cfg)
    progress.done(ok=True, output=str(out_dir))
    return out_dir


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="highlight-engine", description="Highlight short-video engine")
    sub = p.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Detect + assemble")
    run_p.add_argument("--game", default="pubg")
    run_p.add_argument("--template", default="kill_short")
    run_p.add_argument("--job", type=Path, help="Job JSON override")
    run_p.add_argument("--title", default=None)
    run_p.add_argument("--scan-start", type=float, default=None, help="Scan window start (seconds)")
    run_p.add_argument("--scan-end", type=float, default=None, help="Scan window end (seconds)")
    run_p.add_argument(
        "--no-reuse-cache",
        action="store_true",
        help="Force re-extract / re-OCR (ignore detect caches)",
    )
    run_p.add_argument("sources", nargs="*", help="Source video paths")

    sub.add_parser("devices", help="Probe GPUs / encoders (JSON)")
    sub.add_parser("games", help="List games + templates (JSON)")
    sub.add_parser("ocr-backend", help="Print active OCR backend")
    probe_p = sub.add_parser("probe", help="Probe video duration/size (JSON)")
    probe_p.add_argument("path", type=Path, help="Video file path")

    args = p.parse_args(argv)

    if args.cmd == "devices":
        print(json.dumps(probe_devices(), indent=2))
        return 0
    if args.cmd == "games":
        print(json.dumps(list_games(), indent=2))
        return 0
    if args.cmd == "ocr-backend":
        print(get_backend_name())
        return 0
    if args.cmd == "probe":
        # Quiet ffprobe only — no progress JSONL on stdout (UI parses one JSON object).
        import subprocess

        path = Path(args.path)
        if not path.exists():
            print(json.dumps({"error": f"missing: {path}"}))
            return 1
        try:
            ff = ffprobe_bin()
            dur_r = subprocess.run(
                [
                    ff,
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            size_r = subprocess.run(
                [
                    ff,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height",
                    "-of",
                    "json",
                    str(path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            streams = (json.loads(size_r.stdout or "{}").get("streams") or [{}])[0]
            print(
                json.dumps(
                    {
                        "path": str(path.resolve()),
                        "duration": float(dur_r.stdout.strip()),
                        "width": int(streams.get("width") or 1920),
                        "height": int(streams.get("height") or 1080),
                    }
                )
            )
            return 0
        except Exception as exc:
            print(json.dumps({"error": str(exc)}))
            return 1

    if args.cmd == "run":
        job: dict = {
            "active_game": args.game,
            "active_template": args.template,
        }
        if args.title:
            job["title"] = args.title
        if args.sources:
            job["sources"] = [str(Path(s).resolve()) for s in args.sources]
        det_overrides: dict = {}
        if args.scan_start is not None:
            det_overrides["scan_start_seconds"] = args.scan_start
        if args.scan_end is not None:
            det_overrides["scan_end_seconds"] = args.scan_end
        if args.no_reuse_cache:
            det_overrides["reuse_cache"] = False
        if det_overrides:
            job.setdefault("games", {}).setdefault(args.game, {}).setdefault(
                "detection", {}
            ).update(det_overrides)
        try:
            cfg = load_config(job_path=args.job, job=job)
            # Merge CLI sources over job file if both present
            if args.sources:
                cfg.sources = [str(Path(s).resolve()) for s in args.sources]
            if args.title:
                cfg.title = args.title
            if det_overrides:
                cfg.detection.update(det_overrides)
            # Apply device auto if unset
            if not cfg.device.get("encode"):
                d = probe_devices()
                cfg.device["encode"] = d["recommend_encode"]
                cfg.device["decode"] = d["recommend_decode"]
            run_job(cfg)
            return 0
        except SystemExit as e:
            return int(e.code or 1)
        except Exception as exc:
            progress.error(str(exc))
            progress.emit(traceback=traceback.format_exc())
            progress.done(ok=False)
            return 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
