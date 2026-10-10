#!/usr/bin/env python3
"""KS1 detection via GPU region extracts + Windows OCR for my knocks/kills."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path("V:/highlight")
SOURCE = Path("V:/PUBG")
WORK = ROOT / "work" / "today" / "ks1"
SCRIPTS = ROOT / "scripts"

PLAYER = "rkanik"
SAMPLE_FPS = 1

# Banner / combat tip area (YOU KNOCKED / YOU KILLED)
BANNER = (560, 640, 1360, 940)  # x1,y1,x2,y2
# Kill feed top-right
FEED = (1500, 60, 1915, 320)
# Spectator / nameplate band
SPEC = (500, 880, 1420, 1040)

MY_KILL_RE = re.compile(
    r"(you\s*(knocked|killed|downed|eliminated))"
    r"|(knocked\s*(out|down)?)"
    r"|(\bkilled\b)",
    re.I,
)
SPECTATOR_RE = re.compile(r"spectat|death\s*cam|next\s*player|hold\s*to\s*respawn", re.I)
PLAYER_RE = re.compile(re.escape(PLAYER), re.I)
VICTIM_ME_RE = re.compile(rf"(killed|knocked|eliminated)\s+.{{0,40}}{re.escape(PLAYER)}", re.I)


def run(cmd, check=True):
    print("+", " ".join(map(str, cmd[:14])), ("..." if len(cmd) > 14 else ""))
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def extract_crop(video: Path, out_dir: Path, box, fps=SAMPLE_FPS) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    if any(out_dir.glob("f_*.jpg")):
        print(f"skip extract (exists): {out_dir}")
        return
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    pattern = str(out_dir / "f_%06d.jpg")
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-hwaccel", "cuda", "-i", str(video),
        "-vf", f"fps={fps},crop={w}:{h}:{x1}:{y1}",
        "-q:v", "6", "-y", pattern,
    ]
    r = run(cmd, check=False)
    if r.returncode != 0 or not any(out_dir.glob("f_*.jpg")):
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", str(video),
            "-vf", f"fps={fps},crop={w}:{h}:{x1}:{y1}",
            "-q:v", "6", "-threads", "2", "-y", pattern,
        ]
        run(cmd)


def ocr_dir(dir_path: Path, out_json: Path) -> list[dict]:
    if out_json.exists():
        print(f"reuse OCR {out_json}")
        return json.loads(out_json.read_text(encoding="utf-8") or "[]")
    ps = SCRIPTS / "ocr_batch.ps1"
    cmd = [
        "powershell", "-NoProfile", "-File", str(ps),
        "-Dir", str(dir_path),
        "-OutJson", str(out_json),
        "-EveryN", "1",
    ]
    print(f"OCR {dir_path} ...")
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout)
    if r.returncode != 0:
        print(r.stderr)
        r.check_returncode()
    raw = json.loads(out_json.read_text(encoding="utf-8") or "[]")
    if isinstance(raw, dict):
        raw = [raw]
    return raw


def normalize_entries(raw) -> list[dict]:
    if not raw:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    out = []
    for e in raw:
        out.append({"t": float(e["t"]), "text": e.get("text") or "", "file": e.get("file")})
    return out


def merge_ranges(times: list[float], pad=0.5, gap=1.5) -> list[list[float]]:
    if not times:
        return []
    times = sorted(times)
    ranges = [[max(0.0, times[0] - pad), times[0] + pad]]
    for t in times[1:]:
        if t <= ranges[-1][1] + gap:
            ranges[-1][1] = t + pad
        else:
            ranges.append([max(0.0, t - pad), t + pad])
    return ranges


def in_ranges(t: float, ranges: list[list[float]]) -> bool:
    for a, b in ranges:
        if a <= t <= b:
            return True
    return False


def classify_banner(text: str) -> str | None:
    t = text.lower()
    if SPECTATOR_RE.search(t):
        return "spectator"
    if VICTIM_ME_RE.search(t):
        return None
    if re.search(r"you\s*knocked|you\s*downed", t):
        return "my_knock"
    if re.search(r"you\s*killed|you\s*eliminated", t):
        return "my_kill"
    # generic combat tip sometimes OCR'd without YOU
    if PLAYER_RE.search(t) and re.search(r"knock|kill|elimin", t) and not VICTIM_ME_RE.search(t):
        return "my_kill_or_knock"
    if re.search(r"\bknocked\b", t) and re.search(r"\bwith\b", t):
        return "my_knock"
    if re.search(r"\bkilled\b", t) and re.search(r"\bwith\b", t):
        return "my_kill"
    return None


def classify_feed(text: str) -> str | None:
    t = text.lower()
    if not PLAYER_RE.search(t):
        return None
    if VICTIM_ME_RE.search(t):
        return None
    # my name present in feed line with knock/kill verb nearby
    if re.search(rf"{re.escape(PLAYER)}.{{0,50}}(knock|kill|elimin)", t, re.I):
        if re.search(r"knock", t, re.I):
            return "my_knock"
        return "my_kill"
    if re.search(rf"(knock|kill|elimin).{{0,50}}{re.escape(PLAYER)}", t, re.I):
        # likely I am victim — skip
        return None
    return None


def ocr_dir_every(dir_path: Path, out_json: Path, every_n: int = 1) -> list[dict]:
    if out_json.exists():
        print(f"reuse OCR {out_json}", flush=True)
        return json.loads(out_json.read_text(encoding="utf-8") or "[]")
    ps = SCRIPTS / "ocr_batch.ps1"
    cmd = [
        "powershell", "-NoProfile", "-File", str(ps),
        "-Dir", str(dir_path),
        "-OutJson", str(out_json),
        "-EveryN", str(every_n),
    ]
    print(f"OCR every={every_n} {dir_path} ...", flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout, flush=True)
    if r.returncode != 0:
        print(r.stderr, flush=True)
        r.check_returncode()
    raw = json.loads(out_json.read_text(encoding="utf-8") or "[]")
    if isinstance(raw, dict):
        raw = [raw]
    return raw


def analyze_video(video: Path, idx: int) -> dict:
    vwork = WORK / f"v{idx}_{re.sub(r'[^0-9A-Za-z]+', '_', video.stem)}"
    vwork.mkdir(parents=True, exist_ok=True)
    cache = vwork / "ks1_events.json"
    if cache.exists():
        print(f"reuse {cache}", flush=True)
        return json.loads(cache.read_text(encoding="utf-8"))

    banner_dir = vwork / "banner"
    spec_dir = vwork / "spec"
    # Banner = YOU KNOCKED / YOU KILLED (player-only). Spec band for spectator skip.
    extract_crop(video, banner_dir, BANNER)
    extract_crop(video, spec_dir, SPEC)

    banner_ocr = normalize_entries(ocr_dir_every(banner_dir, vwork / "banner_ocr.json", every_n=1))
    # Spectator UI changes slowly — OCR every 2s
    spec_ocr = normalize_entries(ocr_dir_every(spec_dir, vwork / "spec_ocr.json", every_n=2))

    spectator_times = [e["t"] for e in spec_ocr if SPECTATOR_RE.search(e["text"] or "")]
    spectator_times += [e["t"] for e in banner_ocr if SPECTATOR_RE.search(e["text"] or "")]
    spectator_ranges = merge_ranges(spectator_times, pad=1.0, gap=2.0)

    events = []
    for e in banner_ocr:
        kind = classify_banner(e["text"])
        if not kind or kind == "spectator":
            continue
        if in_ranges(e["t"], spectator_ranges):
            continue
        events.append({"t": e["t"], "kind": kind, "text": e["text"], "source": "banner"})

    # dedupe 1.5s
    events.sort(key=lambda x: x["t"])
    dedup = []
    for e in events:
        if dedup and e["t"] - dedup[-1]["t"] < 1.5:
            rank = {"my_kill": 3, "my_knock": 2, "my_kill_or_knock": 1}
            if rank.get(e["kind"], 0) > rank.get(dedup[-1]["kind"], 0):
                dedup[-1] = e
            continue
        dedup.append(e)

    r = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video),
    ])
    duration = float(r.stdout.strip())

    result = {
        "video": str(video),
        "duration": duration,
        "events": dedup,
        "spectator_ranges": spectator_ranges,
        "banner_ocr_hits": len(banner_ocr),
        "feed_ocr_hits": 0,
    }
    cache.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"{video.name}: events={len(dedup)} spectator_ranges={len(spectator_ranges)}", flush=True)
    return result


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    videos = sorted(SOURCE.glob("*.mp4"))
    all_results = []
    for i, v in enumerate(videos, 1):
        print(f"\n===== [{i}/{len(videos)}] {v.name} =====")
        all_results.append(analyze_video(v, i))
    out = WORK / "detection_all.json"
    out.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    total = sum(len(r["events"]) for r in all_results)
    print(f"\nTOTAL knock/kill events: {total}")
    for r in all_results:
        print(f"  {Path(r['video']).name}: {len(r['events'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
