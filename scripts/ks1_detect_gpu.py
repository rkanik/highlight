#!/usr/bin/env python3
"""
KS1 detect — GPU-safe (v2).

Heavy work: ffmpeg CUDA banner extract (1 job, threads=1).
Light CPU: score crops, OCR <=80 new candidates/video (serial, paused).
Reuses prior OCR caches; reclassifies with OCR-typo-tolerant patterns.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path("V:/highlight")
SOURCE = Path("V:/PUBG")
WORK = ROOT / "work" / "today" / "ks1"
SCRIPTS = ROOT / "scripts"

BANNER = (560, 640, 1360, 940)
MAX_OCR_NEW = 80
OCR_PAUSE_S = 0.3
FORCE = "--force" in sys.argv

PLAYER = "rkanik"
SPEC_PAT = re.compile(r"spectat|death\s*cam", re.I)
# I got knocked/killed — skip (do NOT match "YOU KNOCKED" / "YOU KILLED")
VICTIM_ME = re.compile(
    rf"(knocked|killed|eliminated)\s+you\b|"
    rf"knocked\s+you\s+out|"
    rf"\byou\s+(were|got|have\s+been)\s+(knocked|killed|eliminated)|"
    rf"(knocked|killed|eliminated)\s+.{{0,30}}{re.escape(PLAYER)}\b",
    re.I,
)

# OCR-tolerant "YOU KNOCKED / YOU KILLED"
YOU_KNOCK = re.compile(
    r"\byou\s*kn[o0]ck|\byou\s*d[o0]wn|\by[o0]u\s*kn[o0]ck|"
    r"\byou\s*kn[o0]c|\byou\s*knocke[od]?",
    re.I,
)
YOU_KILL = re.compile(
    r"\byou\s*kill|\byou\s*elimin|\by[o0]u\s*kill|"
    r"\byou\s*kille[ed]?|\byou\s*killl?",
    re.I,
)
# "2 KILLS" / "3 KILLS" personal counter flash near fight tip
MY_KILLS_COUNTER = re.compile(r"\b([1-9]|1[0-9])\s*kills?\b", re.I)


def run(cmd, check=True):
    print("+", " ".join(map(str, cmd[:14])), ("..." if len(cmd) > 14 else ""), flush=True)
    return subprocess.run(cmd, check=check, capture_output=True)


def duration_of(video: Path) -> float:
    r = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(video),
    ])
    return float(r.stdout.decode().strip())


def extract_banner_gpu(video: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(out_dir.glob("f_*.jpg")))
    if n > 50:
        print(f"skip extract ({n} frames): {out_dir}", flush=True)
        return
    x1, y1, x2, y2 = BANNER
    w, h = x2 - x1, y2 - y1
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-hwaccel", "cuda", "-threads", "1",
        "-i", str(video),
        "-vf", f"fps=1,crop={w}:{h}:{x1}:{y1}",
        "-q:v", "6", "-threads", "1",
        "-y", str(out_dir / "f_%06d.jpg"),
    ]
    r = run(cmd, check=False)
    if r.returncode != 0 or not any(out_dir.glob("f_*.jpg")):
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-hwaccel", "cuda", "-threads", "1",
            "-i", str(video),
            "-vf", f"fps=1,crop={w}:{h}:{x1}:{y1},format=yuv420p",
            "-q:v", "6", "-threads", "1",
            "-y", str(out_dir / "f_%06d.jpg"),
        ])


def load_ocr_maps(vwork: Path) -> dict[int, str]:
    ocr_map: dict[int, str] = {}
    for name in ("banner_ocr_sparse.json", "banner_ocr_window.json", "banner_ocr_rescan.json"):
        p = vwork / name
        if not p.exists():
            continue
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        for k, v in raw.items():
            ocr_map[int(k)] = v or ""
    return ocr_map


def banner_candidates(banner_dir: Path, duration: float) -> list[tuple[float, int]]:
    n = min(int(duration) + 2, len(list(banner_dir.glob("f_*.jpg"))))
    prev = None
    scored = []
    for t in range(n):
        fp = banner_dir / f"f_{t + 1:06d}.jpg"
        if not fp.exists():
            continue
        im = Image.open(fp).convert("L")
        w, h = im.size
        full = sum(im.histogram()[200:]) / max(1, w * h) * 100
        if full > 12:
            prev = None
            continue
        mid = im.crop((int(w * 0.05), int(h * 0.18), int(w * 0.95), int(h * 0.55)))
        white = sum(mid.histogram()[205:]) / max(1, mid.size[0] * mid.size[1]) * 100
        if not (0.55 <= white <= 4.8):
            prev = white
            continue
        delta = white - (prev if prev is not None else 0.0)
        prev = white
        if delta >= 0.12:
            scored.append((delta + white * 0.35, t))
    scored.sort(reverse=True)
    return scored


def ocr_one(path: Path) -> str:
    r = subprocess.run(
        ["powershell", "-NoProfile", "-File", str(SCRIPTS / "ocr_frame.ps1"), "-ImagePath", str(path)],
        capture_output=True,
    )
    stdout = (r.stdout or b"").decode("utf-8", errors="replace")
    lines = []
    for ln in stdout.splitlines():
        s = ln.strip()
        if not s:
            continue
        if any(x in s for x in (
            "DisposeAsync", "CategoryInfo", "FullyQualified", "RuntimeException", "Method invocation",
        )):
            continue
        if s.startswith("At ") or s.startswith("+"):
            continue
        lines.append(s)
    return lines[-1] if lines else ""


def classify(text: str) -> str | None:
    if not text or SPEC_PAT.search(text):
        return None
    if VICTIM_ME.search(text):
        return None
    t = text.lower()
    # teammate knock lines without YOU — skip unless YOU present
    if YOU_KNOCK.search(text):
        return "my_knock"
    if YOU_KILL.search(text):
        return "my_kill"
    # Scrambled OCR: YOU must appear before kill/knock (avoids "X KNOCKED YOU")
    if re.search(r"\byou\b.{0,40}kill", t) and not re.search(r"painkill", t):
        return "my_kill"
    if re.search(r"\byou\b.{0,40}knock", t):
        return "my_knock"
    # Heavy OCR damage on YOU KNOCKED OUT (seen in production):
    # "U NOC ? OUT", "Y KNOC QUT", "UK CKEDOUT", "KN CKED OUT", "CKEDOUT"
    if re.search(
        r"(u\s*noc|y\s*knoc|you?\s*kn[o0c]{1,4}|uk?\s*c?ked\s*out|kn\s*c?ked\s*out|cked\s*out|knock\s*o\s*t\s*you)",
        t,
    ):
        return "my_knock"
    # Kill feed line: "rkanik ... X ... [ENEMY]" (X = knock icon in OCR)
    if re.search(rf"{re.escape(PLAYER)}.{{0,50}}\bx\b.{{0,40}}\[", t):
        return "my_knock"
    if re.search(rf"{re.escape(PLAYER)}.{{0,60}}killed", t) and not VICTIM_ME.search(text):
        return "my_kill"
    return None


def analyze_video(video: Path, idx: int) -> dict:
    vwork = WORK / f"v{idx}_{re.sub(r'[^0-9A-Za-z]+', '_', video.stem)}"
    vwork.mkdir(parents=True, exist_ok=True)
    cache = vwork / "ks1_events.json"

    print(f"\n===== [{idx}] {video.name} =====", flush=True)
    duration = duration_of(video)
    banner_dir = vwork / "banner"
    extract_banner_gpu(video, banner_dir)

    ocr_map = load_ocr_maps(vwork)
    scored = banner_candidates(banner_dir, duration)
    # Prefer unscanned high-score frames, then fill with already-scanned
    unscanned = [(s, t) for s, t in scored if t not in ocr_map]
    scanned = [(s, t) for s, t in scored if t in ocr_map]
    to_ocr = [t for _, t in unscanned[:MAX_OCR_NEW]]
    # Also OCR neighbors of weak "YOU" hints already cached
    for t, text in list(ocr_map.items()):
        if re.search(r"\byou\b", text or "", re.I) and (
            re.search(r"kill|knock|with\s+m|with\s+k|frag|head", text or "", re.I)
        ):
            for n in (t - 1, t + 1, t + 2):
                if 0 <= n <= int(duration) and n not in ocr_map and n not in to_ocr:
                    to_ocr.append(n)
    to_ocr = sorted(set(to_ocr))[:MAX_OCR_NEW]
    print(
        f"candidates ranked={len(scored)} cached_ocr={len(ocr_map)} new_ocr={len(to_ocr)}",
        flush=True,
    )

    rescan_path = vwork / "banner_ocr_rescan.json"
    rescan = {}
    if rescan_path.exists():
        rescan = {int(k): v for k, v in json.loads(rescan_path.read_text(encoding="utf-8-sig")).items()}

    for i, t in enumerate(to_ocr, 1):
        if t in ocr_map:
            continue
        fp = banner_dir / f"f_{t + 1:06d}.jpg"
        if not fp.exists():
            continue
        text = ocr_one(fp)
        ocr_map[t] = text
        rescan[t] = text
        time.sleep(OCR_PAUSE_S)
        if i % 10 == 0:
            rescan_path.write_text(json.dumps({str(k): v for k, v in rescan.items()}, indent=2), encoding="utf-8")
            print(f"  OCR {i}/{len(to_ocr)}", flush=True)

    if rescan:
        rescan_path.write_text(json.dumps({str(k): v for k, v in rescan.items()}, indent=2), encoding="utf-8")
    # merge into sparse cache for reuse
    sparse_path = vwork / "banner_ocr_sparse.json"
    sparse_path.write_text(json.dumps({str(k): v for k, v in sorted(ocr_map.items())}, indent=2), encoding="utf-8")

    events = []
    for t, text in sorted(ocr_map.items()):
        kind = classify(text)
        if not kind:
            continue
        events.append({"t": float(t), "kind": kind, "text": text, "source": "banner"})
        safe = (text or "")[:90].encode("ascii", errors="replace").decode("ascii")
        print(f"  HIT t={t} {kind}: {safe}", flush=True)

    events.sort(key=lambda e: e["t"])
    dedup = []
    for e in events:
        if dedup and e["t"] - dedup[-1]["t"] < 1.8:
            rank = {"my_kill": 3, "my_knock": 2, "my_kill_or_knock": 1}
            if rank.get(e["kind"], 0) > rank.get(dedup[-1]["kind"], 0):
                dedup[-1] = e
            continue
        dedup.append(e)

    result = {
        "video": str(video),
        "duration": duration,
        "events": dedup,
        "spectator_ranges": [],
        "ocr_cached": len(ocr_map),
        "ocr_new": len(to_ocr),
        "mode": "gpu_safe_rescan_v2",
    }
    cache.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"{video.name}: events={len(dedup)}", flush=True)
    return result


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    if FORCE:
        for p in WORK.glob("v*/ks1_events.json"):
            p.unlink(missing_ok=True)
            print("cleared", p, flush=True)
    videos = sorted(SOURCE.glob("*.mp4"))
    results = []
    for i, v in enumerate(videos, 1):
        results.append(analyze_video(v, i))
        time.sleep(1.5)
    out = WORK / "detection_all.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    total = sum(len(r["events"]) for r in results)
    print(f"\nTOTAL events={total}", flush=True)
    for r in results:
        print(f"  {Path(r['video']).name}: {len(r['events'])}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
