#!/usr/bin/env python3
"""Faster KS1 detect: audio peaks -> OCR banner windows for YOU KNOCKED/KILLED."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from PIL import Image

ROOT = Path("V:/highlight")
SOURCE = Path("V:/PUBG")
WORK = ROOT / "work" / "today" / "ks1"
SCRIPTS = ROOT / "scripts"
PLAYER = "rkanik"
BANNER = (560, 640, 1360, 940)
SPEC = (500, 880, 1420, 1040)
AUDIO_PEAK_DBFS = -22.0
WINDOW_BEFORE = 2
WINDOW_AFTER = 4

MY_PAT = re.compile(
    r"you\s*(knocked|killed|downed|eliminated)|"
    r"(knocked|killed|downed)\s+.+\s+with|"
    rf"{re.escape(PLAYER)}.{{0,40}}(knock|kill|elimin)",
    re.I,
)
SPEC_PAT = re.compile(r"spectat|death\s*cam", re.I)
VICTIM_ME = re.compile(rf"(killed|knocked|eliminated)\s+.{{0,40}}{re.escape(PLAYER)}", re.I)


def run(cmd, check=True):
    print("+", " ".join(map(str, cmd[:12])), "..." if len(cmd) > 12 else "", flush=True)
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def duration_of(video: Path) -> float:
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video)])
    return float(r.stdout.strip())


def extract_crop(video: Path, out_dir: Path, box) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if len(list(out_dir.glob("f_*.jpg"))) > 10:
        print(f"skip extract {out_dir.name}", flush=True)
        return
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-hwaccel", "cuda",
        "-i", str(video), "-vf", f"fps=1,crop={w}:{h}:{x1}:{y1}",
        "-q:v", "6", "-y", str(out_dir / "f_%06d.jpg"),
    ]
    r = run(cmd, check=False)
    if r.returncode != 0 or not any(out_dir.glob("f_*.jpg")):
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
            "-vf", f"fps=1,crop={w}:{h}:{x1}:{y1}", "-q:v", "6", "-threads", "2",
            "-y", str(out_dir / "f_%06d.jpg"),
        ])


def audio_peaks(video: Path, vwork: Path) -> list[float]:
    cache = vwork / "audio_peaks.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True,
    )
    stderr = (r.stderr or b"").decode("utf-8", errors="replace")
    (vwork / "ebur.txt").write_text(stderr, encoding="utf-8", errors="replace")
    times = []
    for line in stderr.splitlines():
        if "M:" not in line or "t:" not in line:
            continue
        tm = re.search(r"t:\s*([0-9.]+)", line)
        mm = re.search(r"M:\s*([+-]?[0-9.]+)", line)
        if not tm or not mm:
            continue
        t, m = float(tm.group(1)), float(mm.group(1))
        if m >= AUDIO_PEAK_DBFS:
            times.append(t)
    times.sort()
    clustered = []
    for t in times:
        if not clustered or t - clustered[-1] >= 0.8:
            clustered.append(t)
    cache.write_text(json.dumps(clustered), encoding="utf-8")
    print(f"audio peaks: {len(clustered)}", flush=True)
    return clustered


def ocr_one(path: Path) -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(SCRIPTS / "ocr_frame.ps1"), "-ImagePath", str(path)],
        capture_output=True,
    )
    stdout = (r.stdout or b"").decode("utf-8", errors="replace")
    # ignore DisposeAsync noise; last non-empty line is text
    lines = [
        ln.strip()
        for ln in stdout.splitlines()
        if ln.strip()
        and "DisposeAsync" not in ln
        and "CategoryInfo" not in ln
        and "FullyQualified" not in ln
        and "RuntimeException" not in ln
        and not ln.strip().startswith("At ")
    ]
    text_lines = [ln for ln in lines if not ln.startswith("+") and "Method invocation" not in ln]
    return text_lines[-1] if text_lines else ""


def classify(text: str) -> str | None:
    if not text:
        return None
    if SPEC_PAT.search(text):
        return "spectator"
    if VICTIM_ME.search(text):
        return None
    t = text.lower()
    if re.search(r"you\s*knocked|you\s*downed", t):
        return "my_knock"
    if re.search(r"you\s*killed|you\s*eliminated", t):
        return "my_kill"
    if MY_PAT.search(text) and not VICTIM_ME.search(text):
        if "knock" in t:
            return "my_knock"
        if "kill" in t or "elimin" in t:
            return "my_kill"
        return "my_kill_or_knock"
    return None


def frame_path(dir_path: Path, t: int) -> Path:
    return dir_path / f"f_{t+1:06d}.jpg"


def analyze_video(video: Path, idx: int) -> dict:
    vwork = WORK / f"v{idx}_{re.sub(r'[^0-9A-Za-z]+', '_', video.stem)}"
    vwork.mkdir(parents=True, exist_ok=True)
    cache = vwork / "ks1_events.json"
    if cache.exists():
        print(f"reuse {cache}", flush=True)
        return json.loads(cache.read_text(encoding="utf-8"))

    print(f"\n===== [{idx}] {video.name} =====", flush=True)
    duration = duration_of(video)
    banner_dir = vwork / "banner"
    spec_dir = vwork / "spec"
    extract_crop(video, banner_dir, BANNER)
    extract_crop(video, spec_dir, SPEC)
    peaks = audio_peaks(video, vwork)

    # candidate seconds to OCR
    cand = set()
    for p in peaks:
        for t in range(max(0, int(p) - WINDOW_BEFORE), min(int(duration), int(p) + WINDOW_AFTER) + 1):
            cand.add(t)
    # also sample every 15s for quiet utility knocks
    for t in range(0, int(duration), 15):
        cand.add(t)
    cand = sorted(cand)
    print(f"OCR candidates: {len(cand)} / {int(duration)}s", flush=True)

    ocr_cache_path = vwork / "banner_ocr_window.json"
    ocr_map = {}
    if ocr_cache_path.exists():
        ocr_map = {int(k): v for k, v in json.loads(ocr_cache_path.read_text(encoding="utf-8-sig")).items()}

    events = []
    spectator_times = []
    for i, t in enumerate(cand):
        if t in ocr_map:
            text = ocr_map[t]
        else:
            fp = frame_path(banner_dir, t)
            if not fp.exists():
                continue
            text = ocr_one(fp)
            ocr_map[t] = text
            if (i + 1) % 25 == 0:
                ocr_cache_path.write_text(json.dumps({str(k): v for k, v in ocr_map.items()}, indent=2), encoding="utf-8")
                print(f"  OCR {i+1}/{len(cand)}", flush=True)
        kind = classify(text)
        if kind == "spectator":
            spectator_times.append(float(t))
            continue
        if kind:
            events.append({"t": float(t), "kind": kind, "text": text, "source": "banner"})

    # spectator band sparse OCR around events + every 20s
    spec_times_check = set(int(e["t"]) for e in events) | set(range(0, int(duration), 20))
    for t in sorted(spec_times_check):
        fp = frame_path(spec_dir, t)
        if not fp.exists():
            continue
        text = ocr_one(fp)
        if SPEC_PAT.search(text or ""):
            spectator_times.append(float(t))

    # merge spectator ranges
    spectator_times = sorted(set(spectator_times))
    spec_ranges = []
    for t in spectator_times:
        if not spec_ranges or t > spec_ranges[-1][1] + 2:
            spec_ranges.append([max(0.0, t - 1), t + 1])
        else:
            spec_ranges[-1][1] = t + 1

    def in_spec(t):
        return any(a <= t <= b for a, b in spec_ranges)

    events = [e for e in events if not in_spec(e["t"])]
    events.sort(key=lambda e: e["t"])
    dedup = []
    for e in events:
        if dedup and e["t"] - dedup[-1]["t"] < 1.5:
            rank = {"my_kill": 3, "my_knock": 2, "my_kill_or_knock": 1}
            if rank.get(e["kind"], 0) > rank.get(dedup[-1]["kind"], 0):
                dedup[-1] = e
            continue
        dedup.append(e)

    ocr_cache_path.write_text(json.dumps({str(k): v for k, v in ocr_map.items()}, indent=2), encoding="utf-8")
    result = {
        "video": str(video),
        "duration": duration,
        "events": dedup,
        "spectator_ranges": spec_ranges,
        "audio_peaks": len(peaks),
        "ocr_candidates": len(cand),
    }
    cache.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"{video.name}: events={len(dedup)} spectator_ranges={len(spec_ranges)}", flush=True)
    for e in dedup:
        print(f"  t={e['t']:.0f} {e['kind']}: {e['text'][:80]}", flush=True)
    return result


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    videos = sorted(SOURCE.glob("*.mp4"))
    results = []
    for i, v in enumerate(videos, 1):
        results.append(analyze_video(v, i))
    out = WORK / "detection_all.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    total = sum(len(r["events"]) for r in results)
    print(f"\nTOTAL events={total}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
