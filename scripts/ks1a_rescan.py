#!/usr/bin/env python3
"""
KS1A rescan — OCR next unscanned banner/feed candidates (cached strips).
No re-extract if frames exist. Thermal-safe: ≤40 new OCR/region/video.
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

MAX_BANNER = 35
MAX_FEED = 40
OCR_PAUSE = 0.3


def rescan_video(video: Path, idx: int) -> dict:
    vwork = WORK / f"v{idx}_{re.sub(r'[^0-9A-Za-z]+', '_', video.stem)}"
    vwork.mkdir(parents=True, exist_ok=True)
    print(f"\n===== RESCAN [{idx}] {video.name} =====", flush=True)
    duration = ks1.duration_of(video)
    team = ks1a.load_or_ocr_team(video, vwork, duration)
    me = next((n for n in team if ks1a._norm_token(ks1a.PLAYER) in ks1a._norm_token(n)), ks1a.PLAYER)

    banner_dir = vwork / "banner"
    ks1.extract_banner_gpu(video, banner_dir)
    ocr_map = ks1.load_ocr_maps(vwork)
    for name in ("banner_ocr_assist.json", "banner_ocr_recover.json", "banner_ocr_rescan2.json"):
        p = vwork / name
        if p.exists():
            ocr_map.update({int(k): v for k, v in json.loads(p.read_text(encoding="utf-8-sig")).items()})

    scored_b = ks1.banner_candidates(banner_dir, duration)
    ban_new = [t for _, t in scored_b if t not in ocr_map][:MAX_BANNER]
    print(f"banner cached={len(ocr_map)} new_ocr={len(ban_new)}", flush=True)
    rescan_b: dict[int, str] = {}
    bp = vwork / "banner_ocr_rescan2.json"
    if bp.exists():
        rescan_b = {int(k): v for k, v in json.loads(bp.read_text(encoding="utf-8-sig")).items()}
        ocr_map.update(rescan_b)
    for i, t in enumerate(ban_new, 1):
        if t in ocr_map and (ocr_map.get(t) or "").strip():
            continue
        fp = banner_dir / f"f_{t + 1:06d}.jpg"
        if not fp.exists():
            continue
        text = ks1a.ocr_one(fp)
        if not (text or "").strip():
            text = ks1.ocr_enhanced(fp, vwork / "banner_enh")
        ocr_map[t] = text
        rescan_b[t] = text
        time.sleep(OCR_PAUSE)
        if i % 10 == 0:
            print(f"  banner OCR {i}/{len(ban_new)}", flush=True)
    if rescan_b:
        bp.write_text(json.dumps({str(k): v for k, v in sorted(rescan_b.items())}, indent=2), encoding="utf-8")
    (vwork / "banner_ocr_sparse.json").write_text(
        json.dumps({str(k): v for k, v in sorted(ocr_map.items())}, indent=2), encoding="utf-8"
    )

    feed_dir = vwork / "feed"
    ks1a.extract_region_gpu(video, feed_dir, ks1a.FEED)
    feed_ocr: dict[int, str] = {}
    feed_path = vwork / "feed_ocr_sparse.json"
    if feed_path.exists():
        feed_ocr = {int(k): v for k, v in json.loads(feed_path.read_text(encoding="utf-8-sig")).items()}
    scored_f = ks1a.feed_candidates(feed_dir, duration)
    feed_new = [t for _, t in scored_f if t not in feed_ocr][:MAX_FEED]
    # Also OCR feed at new banner hit times after classify pass 1
    print(f"feed cached={len(feed_ocr)} new_ocr={len(feed_new)} team={team}", flush=True)
    for i, t in enumerate(feed_new, 1):
        if t in feed_ocr:
            continue
        fp = feed_dir / f"f_{t + 1:06d}.jpg"
        if not fp.exists():
            continue
        feed_ocr[t] = ks1a.ocr_one(fp)
        time.sleep(OCR_PAUSE)
        if i % 10 == 0:
            feed_path.write_text(
                json.dumps({str(k): v for k, v in sorted(feed_ocr.items())}, indent=2), encoding="utf-8"
            )
            print(f"  feed OCR {i}/{len(feed_new)}", flush=True)
    feed_path.write_text(
        json.dumps({str(k): v for k, v in sorted(feed_ocr.items())}, indent=2), encoding="utf-8"
    )

    events: list[dict] = []
    ocr_for_class = ks1.promote_neighbor_partials(ocr_map)
    for t, text in sorted(ocr_for_class.items()):
        kind = ks1a.classify_any(text, me=me, team=team)
        if not kind:
            continue
        events.append({
            "t": float(t),
            "kind": kind,
            "text": ocr_map.get(t, text),
            "source": "banner",
        })
        safe = (text or "")[:90].encode("ascii", "replace").decode("ascii")
        print(f"  HIT banner t={t} {kind}: {safe}", flush=True)

    # OCR feed neighbors of banner hits (pair tip + feed)
    extra_feed = []
    for e in events:
        t = int(e["t"])
        for n in (t - 1, t, t + 1, t + 2):
            if 0 <= n <= int(duration) and n not in feed_ocr:
                extra_feed.append(n)
    extra_feed = sorted(set(extra_feed))[:12]
    for t in extra_feed:
        fp = feed_dir / f"f_{t + 1:06d}.jpg"
        if not fp.exists():
            continue
        feed_ocr[t] = ks1a.ocr_one(fp)
        time.sleep(OCR_PAUSE)
    if extra_feed:
        feed_path.write_text(
            json.dumps({str(k): v for k, v in sorted(feed_ocr.items())}, indent=2), encoding="utf-8"
        )

    for t, text in sorted(feed_ocr.items()):
        kind = ks1a.classify_feed_line(text, me, team)
        if not kind:
            continue
        events.append({
            "t": float(t),
            "kind": kind,
            "text": text,
            "source": "feed",
            "team": team,
        })
        safe = (text or "")[:90].encode("ascii", "replace").decode("ascii")
        print(f"  HIT feed t={t} {kind}: {safe}", flush=True)

    dedup = ks1a.dedupe_events(events, gap=1.8)
    result = {
        "video": str(video),
        "duration": duration,
        "events": dedup,
        "template": "KS1A",
        "mode": "ks1a_rescan",
        "team": team,
        "me": me,
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
        results.append(rescan_video(v, i))
        time.sleep(1.0)
    out = WORK / "detection_ks1a.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")

    # KS1 = knocks/kills only
    ks1_results = []
    for r in results:
        ev = [e for e in r["events"] if e["kind"] in ("my_kill", "my_knock")]
        ks1_results.append({
            **r,
            "events": ev,
            "template": "KS1",
            "mode": "ks1_from_rescan",
            "counts": {
                "my_kill": sum(1 for e in ev if e["kind"] == "my_kill"),
                "my_knock": sum(1 for e in ev if e["kind"] == "my_knock"),
            },
        })
    (WORK / "detection_all.json").write_text(json.dumps(ks1_results, indent=2), encoding="utf-8")

    total = sum(len(r["events"]) for r in results)
    kills = sum(r["counts"]["my_kill"] + r["counts"]["my_knock"] for r in results)
    assists = sum(r["counts"]["my_assist"] for r in results)
    print(f"\nRESCAN TOTAL events={total} kill_knock={kills} assists={assists}", flush=True)
    for r in results:
        print(f"  {Path(r['video']).name}: {r['counts']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
