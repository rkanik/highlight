#!/usr/bin/env python3
"""
KS1A — kills/knocks + my ASSIST moments.
GPU-safe: reuse OCR caches; sparse new OCR <=50/video for ASSIST misses.
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
MAX_NEW_OCR = 50
OCR_PAUSE = 0.3

# Import classify helpers from ks1_detect_gpu
sys.path.insert(0, str(SCRIPTS))
import ks1_detect_gpu as ks1  # noqa: E402

ASSIST_RE = re.compile(
    r"\bi\s*assist|\bassists?\b|\bassisted\b|\+\s*\d+\s*assist|assist\s*\+|you\s*assist|"
    r"\d+\s*assists?\b",
    re.I,
)
# map-only counter noise without fight context
MAP_ONLY_ASSIST = re.compile(r"^\s*\d+\s*assisted\s*$", re.I)


def classify_assist(text: str) -> str | None:
    if not text or MAP_ONLY_ASSIST.search(text.strip()):
        return None
    if ASSIST_RE.search(text):
        # skip if clearly "knocked YOU" victim line without assist credit
        if ks1.VICTIM_ME.search(text) and not re.search(r"\bassist", text, re.I):
            return None
        return "my_assist"
    return None


def classify_any(text: str) -> str | None:
    a = classify_assist(text)
    if a:
        return a
    return ks1.classify(text)


def ocr_one(path: Path) -> str:
    return ks1.ocr_one(path)


def unscanned_candidates(banner_dir: Path, duration: float, ocr_map: dict) -> list[int]:
    scored = ks1.banner_candidates(banner_dir, duration)
    out = [t for _, t in scored if t not in ocr_map]
    return out[:MAX_NEW_OCR]


def analyze(video: Path, idx: int) -> dict:
    vwork = WORK / f"v{idx}_{re.sub(r'[^0-9A-Za-z]+', '_', video.stem)}"
    vwork.mkdir(parents=True, exist_ok=True)
    print(f"\n===== KS1A [{idx}] {video.name} =====", flush=True)
    duration = ks1.duration_of(video)
    banner_dir = vwork / "banner"
    ks1.extract_banner_gpu(video, banner_dir)

    ocr_map = ks1.load_ocr_maps(vwork)
    to_ocr = unscanned_candidates(banner_dir, duration, ocr_map)
    # also neighbor OCR around any existing ASSIST-ish hits
    for t, text in list(ocr_map.items()):
        if ASSIST_RE.search(text or ""):
            for n in (t - 1, t + 1, t + 2):
                if 0 <= n <= int(duration) and n not in ocr_map and n not in to_ocr:
                    to_ocr.append(n)
    to_ocr = sorted(set(to_ocr))[:MAX_NEW_OCR]
    print(f"cached={len(ocr_map)} new_ocr={len(to_ocr)}", flush=True)

    assist_ocr_path = vwork / "banner_ocr_assist.json"
    assist_new = {}
    if assist_ocr_path.exists():
        assist_new = {int(k): v for k, v in json.loads(assist_ocr_path.read_text(encoding="utf-8-sig")).items()}
        ocr_map.update(assist_new)

    for i, t in enumerate(to_ocr, 1):
        if t in ocr_map:
            continue
        fp = banner_dir / f"f_{t + 1:06d}.jpg"
        if not fp.exists():
            continue
        text = ocr_one(fp)
        ocr_map[t] = text
        assist_new[t] = text
        time.sleep(OCR_PAUSE)
        if i % 10 == 0:
            assist_ocr_path.write_text(
                json.dumps({str(k): v for k, v in assist_new.items()}, indent=2),
                encoding="utf-8",
            )
            print(f"  OCR {i}/{len(to_ocr)}", flush=True)

    if assist_new:
        assist_ocr_path.write_text(
            json.dumps({str(k): v for k, v in sorted(assist_new.items())}, indent=2),
            encoding="utf-8",
        )
    # merge into sparse for reuse
    (vwork / "banner_ocr_sparse.json").write_text(
        json.dumps({str(k): v for k, v in sorted(ocr_map.items())}, indent=2),
        encoding="utf-8",
    )

    events = []
    for t, text in sorted(ocr_map.items()):
        kind = classify_any(text)
        if not kind:
            continue
        events.append({"t": float(t), "kind": kind, "text": text, "source": "banner"})
        safe = (text or "")[:90].encode("ascii", "replace").decode("ascii")
        print(f"  HIT t={t} {kind}: {safe}", flush=True)

    events.sort(key=lambda e: e["t"])
    dedup = []
    rank = {"my_kill": 4, "my_knock": 3, "my_assist": 2, "my_kill_or_knock": 1}
    for e in events:
        if dedup and e["t"] - dedup[-1]["t"] < 1.8:
            if rank.get(e["kind"], 0) > rank.get(dedup[-1]["kind"], 0):
                dedup[-1] = e
            continue
        dedup.append(e)

    result = {
        "video": str(video),
        "duration": duration,
        "events": dedup,
        "template": "KS1A",
        "mode": "ks1a_assists",
        "counts": {
            "my_kill": sum(1 for e in dedup if e["kind"] == "my_kill"),
            "my_knock": sum(1 for e in dedup if e["kind"] == "my_knock"),
            "my_assist": sum(1 for e in dedup if e["kind"] == "my_assist"),
        },
    }
    (vwork / "ks1a_events.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"{video.name}: {len(dedup)} events {result['counts']}", flush=True)
    return result


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    videos = sorted(SOURCE.glob("*.mp4"))
    results = []
    for i, v in enumerate(videos, 1):
        results.append(analyze(v, i))
        time.sleep(1.2)
    out = WORK / "detection_ks1a.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    total = sum(len(r["events"]) for r in results)
    assists = sum(r["counts"]["my_assist"] for r in results)
    print(f"\nKS1A TOTAL events={total} assists={assists}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
