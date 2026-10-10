#!/usr/bin/env python3
"""
KS1A fast recovery: reclassify cached OCR with improved patterns.
Sparse enhanced OCR only for blank tip frames near weapon fragments.
No full-video re-extract. Thermal-safe.
"""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

ROOT = Path("V:/highlight")
SOURCE = Path("V:/PUBG")
WORK = ROOT / "work" / "today" / "ks1"
sys.path.insert(0, str(ROOT / "scripts"))
import ks1_detect_gpu as ks1  # noqa: E402
import ks1a_detect as ks1a  # noqa: E402

MAX_ENH_OCR = 25
OCR_PAUSE = 0.3


def recover_video(video: Path, idx: int) -> dict:
    vwork = WORK / f"v{idx}_{re.sub(r'[^0-9A-Za-z]+', '_', video.stem)}"
    print(f"\n===== RECOVER [{idx}] {video.name} =====", flush=True)
    duration = ks1.duration_of(video)
    banner_dir = vwork / "banner"
    if not banner_dir.exists() or len(list(banner_dir.glob("f_*.jpg"))) < 50:
        ks1.extract_banner_gpu(video, banner_dir)

    ocr_map = ks1.load_ocr_maps(vwork)
    # also load assist ocr if present
    ap = vwork / "banner_ocr_assist.json"
    if ap.exists():
        ocr_map.update({int(k): v for k, v in json.loads(ap.read_text(encoding="utf-8-sig")).items()})

    scored = ks1.banner_candidates(banner_dir, duration)
    # Enhanced OCR: blank high-rank candidates + neighbors of weapon fragments
    need: list[int] = []
    for _, t in scored:
        if not (ocr_map.get(t) or "").strip():
            need.append(t)
    for t, tx in ocr_map.items():
        if not tx:
            continue
        if ks1.WITH_WEAPON.search(tx) or ks1.HEADSHOT_WITH.search(tx) or ks1.MY_KILLS_COUNTER.search(tx):
            for n in (t - 2, t - 1, t + 1, t + 2):
                if 0 <= n <= int(duration) and not (ocr_map.get(n) or "").strip():
                    need.append(n)
    # unique preserve order
    seen = set()
    need_u = []
    for t in need:
        if t not in seen:
            seen.add(t)
            need_u.append(t)
    need_u = need_u[:MAX_ENH_OCR]
    print(f"cached={len(ocr_map)} enh_ocr={len(need_u)}", flush=True)

    enh_dir = vwork / "banner_enh"
    rescan_path = vwork / "banner_ocr_recover.json"
    rescan = {}
    if rescan_path.exists():
        rescan = {int(k): v for k, v in json.loads(rescan_path.read_text(encoding="utf-8-sig")).items()}
        ocr_map.update(rescan)

    for i, t in enumerate(need_u, 1):
        if (ocr_map.get(t) or "").strip():
            continue
        fp = banner_dir / f"f_{t + 1:06d}.jpg"
        if not fp.exists():
            continue
        text = ks1.ocr_one(fp) or ks1.ocr_enhanced(fp, enh_dir)
        ocr_map[t] = text
        rescan[t] = text
        time.sleep(OCR_PAUSE)
        if i % 8 == 0:
            print(f"  OCR {i}/{len(need_u)}", flush=True)

    if rescan:
        rescan_path.write_text(
            json.dumps({str(k): v for k, v in sorted(rescan.items())}, indent=2),
            encoding="utf-8",
        )
    (vwork / "banner_ocr_sparse.json").write_text(
        json.dumps({str(k): v for k, v in sorted(ocr_map.items())}, indent=2),
        encoding="utf-8",
    )

    ocr_for_class = ks1.promote_neighbor_partials(ocr_map)
    events = []
    for t, text in sorted(ocr_for_class.items()):
        kind = ks1a.classify_any(text)
        if not kind:
            continue
        events.append({
            "t": float(t),
            "kind": kind,
            "text": ocr_map.get(t, text),
            "source": "banner_recover",
        })
        safe = (ocr_map.get(t, text) or "")[:90].encode("ascii", "replace").decode("ascii")
        print(f"  HIT t={t} {kind}: {safe}", flush=True)

    events.sort(key=lambda e: e["t"])
    dedup = []
    rank = {"my_kill": 4, "my_knock": 3, "my_assist": 2, "my_kill_or_knock": 1}
    for e in events:
        if dedup and e["t"] - dedup[-1]["t"] < 5.0:
            if rank.get(e["kind"], 0) > rank.get(dedup[-1]["kind"], 0):
                dedup[-1] = e
            continue
        dedup.append(e)
    result = {
        "video": str(video),
        "duration": duration,
        "events": dedup,
        "template": "KS1A",
        "mode": "ks1a_recover_v4",
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
    videos = sorted(SOURCE.glob("*.mp4"))
    results = []
    for i, v in enumerate(videos, 1):
        results.append(recover_video(v, i))
        time.sleep(0.8)
    out = WORK / "detection_ks1a.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    # also refresh detection_all (kills/knocks only)
    ks1_only = []
    for r in results:
        ev = [e for e in r["events"] if e["kind"] in ("my_kill", "my_knock")]
        ks1_only.append({
            "video": r["video"],
            "duration": r["duration"],
            "events": ev,
            "spectator_ranges": [],
            "mode": "reclassify_v4",
            "ocr_cached": 0,
        })
    (WORK / "detection_all.json").write_text(json.dumps(ks1_only, indent=2), encoding="utf-8")
    total = sum(len(r["events"]) for r in results)
    print(f"\nKS1A RECOVER TOTAL events={total}", flush=True)
    for r in results:
        print(
            f"  {Path(r['video']).name}: {len(r['events'])} {r['counts']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
