#!/usr/bin/env python3
"""Rebuild KS1A detection from KILLED/ASSISTED counter OCR + feed/banner."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path("V:/highlight")
sys.path.insert(0, str(ROOT / "scripts"))
import ks1_detect_gpu as ks1  # noqa: E402
import ks1a_detect as ks1a  # noqa: E402

WORK = ROOT / "work" / "today" / "ks1"
vwork = WORK / "v1_2026_10_12_19_55_22"
VIDEO = r"V:\PUBG\2026-10-12 19-55-22.mp4"
DURATION = 9929.066667
PLAYER = "rkanik"
TEAM = ["rkanik", "sazalDas", "AshfaqulAsif"]


def parse_k(text: str | None) -> int | None:
    if not text:
        return None
    t = text.lower()
    m = re.search(r"\b0(\d)\s*killed", t)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d{1,2})\s*killed", t)
    if not m:
        m = re.search(r"killed\s*(\d{1,2})", t)
    if not m:
        if re.search(r"killed\s*0\b", t):
            return 0
        return None
    k = int(m.group(1))
    return k if k <= 15 else None


def parse_a(text: str | None) -> int | None:
    if not text:
        return None
    t = text.lower()
    m = re.search(r"(\d{1,2})\s*assist", t)
    if not m:
        m = re.search(r"assist\w*\s*(\d{1,2})", t)
    if not m:
        if re.search(r"assist\w*\s*0\b", t):
            return 0
        return None
    a = int(m.group(1))
    return a if a <= 15 else None


def hysteresis_increments(series: list[tuple[int, int | None]], kind: str, source: str) -> list[dict]:
    """Emit events when counter rises; require 2 consecutive same values."""
    out: list[dict] = []
    stable = None
    pending = None
    pend_n = 0
    pend_t = 0
    for t, val in series:
        if val is None:
            continue
        if pending != val:
            pending, pend_n, pend_t = val, 1, t
        else:
            pend_n += 1
        if pend_n < 2:
            continue
        if stable is None:
            stable = pending
            continue
        if pending == stable:
            continue
        # Assists: only trust +1 steps (OCR often mashes ALIVE into ASSISTED).
        if kind == "my_assist" and pending - stable != 1:
            stable = pending
            continue
        if pending > stable and pending - stable <= 6:
            # Counter updates after frag — pull back 2s toward the shot.
            base_t = max(0.0, float(pend_t) - 2.0)
            for i, count in enumerate(range(stable + 1, pending + 1)):
                out.append(
                    {
                        "t": base_t + i * 1.2,
                        "kind": kind,
                        "text": f"{source} → {count}",
                        "source": source,
                        "count": count,
                    }
                )
        stable = pending
    return out


def dedupe_counter_kills(events: list[dict]) -> list[dict]:
    clean: list[dict] = []
    for e in events:
        if any(
            e["count"] == x["count"] and abs(e["t"] - x["t"]) < 60 for x in clean[-6:]
        ):
            if clean and e["count"] == clean[-1]["count"] and e["t"] - clean[-1]["t"] < 120:
                continue
            if clean and e["count"] < clean[-1]["count"]:
                clean.append(e)  # new match
                continue
            if clean and e["count"] == clean[-1]["count"]:
                continue
        clean.append(e)
    return clean


def main() -> int:
    cache = {
        int(k): v
        for k, v in json.loads((vwork / "stats_ocr.json").read_text(encoding="utf-8-sig")).items()
    }
    feed_ocr = {
        int(k): v
        for k, v in json.loads(
            (vwork / "feed_ocr_sparse.json").read_text(encoding="utf-8-sig")
        ).items()
    }

    k_series = [(t, parse_k(cache[t])) for t in sorted(cache)]
    a_series = [(t, parse_a(cache[t])) for t in sorted(cache)]
    clean_kills = dedupe_counter_kills(
        hysteresis_increments(k_series, "my_kill", "kill_counter")
    )
    assist_events = hysteresis_increments(a_series, "my_assist", "assist_counter")
    # drop assist OCR noise: huge jumps from ALIVE digits already capped

    print(f"counter kills={len(clean_kills)} assists={len(assist_events)}")
    for e in clean_kills:
        print(f"  K {e['t']:.0f} #{e['count']}")

    feed_events = []
    for t, text in sorted(feed_ocr.items()):
        kind = ks1a.classify_feed_line(text, PLAYER, TEAM)
        if kind in ("my_knock", "my_kill"):
            feed_events.append(
                {"t": float(t), "kind": kind, "text": text, "source": "feed"}
            )
        elif kind == "my_assist" and re.search(r"\bx\b", text or "", re.I):
            feed_events.append(
                {"t": float(t), "kind": "my_assist", "text": text, "source": "feed"}
            )

    ocr_map = ks1.load_ocr_maps(vwork)
    promo = ks1.promote_neighbor_partials(ocr_map)
    banner_events = []
    drop = {3897, 9408, 2688}
    for t, text in sorted(promo.items()):
        if t in drop:
            continue
        kind = ks1a.classify_any(text, me=PLAYER, team=TEAM)
        if not kind:
            continue
        raw = ocr_map.get(t, text)
        low = (raw or "").lower()
        if ks1.VICTIM_ME.search(raw or ""):
            continue
        if re.search(r"ashfaq|faqul|hfaff|sazal", low) and "you" not in low:
            continue
        if "frag" in low and "you" not in low:
            continue
        banner_events.append(
            {"t": float(t), "kind": kind, "text": raw, "source": "banner"}
        )

    # Vector stack: kill counter stayed 0 → knocks
    visual = [
        {
            "t": 1873.0,
            "kind": "my_knock",
            "text": "visual feed: [ASA] rkanik Vector x4",
            "source": "feed_visual",
        },
        {
            "t": 1874.5,
            "kind": "my_knock",
            "text": "visual feed: [ASA] rkanik Vector",
            "source": "feed_visual",
        },
        {
            "t": 1876.0,
            "kind": "my_knock",
            "text": "visual feed: [ASA] rkanik Vector",
            "source": "feed_visual",
        },
        {
            "t": 1877.5,
            "kind": "my_knock",
            "text": "visual feed: [ASA] rkanik Vector",
            "source": "feed_visual",
        },
    ]

    kill_times = [e["t"] for e in clean_kills]
    events: list[dict] = list(clean_kills) + list(assist_events)

    def near_kill(t: float, win: float = 6.0) -> bool:
        return any(abs(t - kt) <= win for kt in kill_times)

    for e in visual + feed_events + banner_events:
        if e["kind"] in ("my_kill", "my_knock") and near_kill(e["t"], 6.0):
            continue
        events.append(e)

    rank_kind = {"my_kill": 4, "my_knock": 3, "my_assist": 2}
    rank_src = {
        "kill_counter": 5,
        "assist_counter": 4,
        "feed": 3,
        "feed_visual": 3,
        "banner": 1,
    }
    # Keep counter kills even when close (multi-kill bursts).
    events.sort(key=lambda e: e["t"])
    final: list[dict] = []
    for e in events:
        if final and e["t"] - final[-1]["t"] < 1.5:
            cur, prev = e, final[-1]
            # Never drop a kill_counter event for a weaker duplicate
            if cur.get("source") == "kill_counter" and prev.get("source") == "kill_counter":
                if cur.get("count") != prev.get("count"):
                    final.append(cur)
                    continue
            if (rank_kind.get(cur["kind"], 0), rank_src.get(cur.get("source"), 0)) > (
                rank_kind.get(prev["kind"], 0),
                rank_src.get(prev.get("source"), 0),
            ):
                final[-1] = cur
            continue
        final.append(e)

    for e in clean_kills:
        if not any(
            abs(e["t"] - x["t"]) < 1.0
            and x["kind"] == "my_kill"
            and x.get("count") == e.get("count")
            for x in final
        ):
            final.append(e)
    final.sort(key=lambda e: e["t"])

    counts = {
        "my_kill": sum(1 for e in final if e["kind"] == "my_kill"),
        "my_knock": sum(1 for e in final if e["kind"] == "my_knock"),
        "my_assist": sum(1 for e in final if e["kind"] == "my_assist"),
    }
    print(f"\nFINAL {counts} total={len(final)}")
    for e in final:
        tx = (e.get("text") or "")[:50].encode("ascii", "replace").decode()
        print(f"  {e['t']:7.1f} {e['kind']:10} {e.get('source', '?'):14} {tx}")

    result = {
        "video": VIDEO,
        "duration": DURATION,
        "events": final,
        "template": "KS1A",
        "mode": "kill_counter_primary_v1",
        "team": TEAM,
        "me": PLAYER,
        "counts": counts,
    }
    (vwork / "ks1a_events.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (WORK / "detection_ks1a.json").write_text(json.dumps([result], indent=2), encoding="utf-8")
    ks1_only = [e for e in final if e["kind"] in ("my_kill", "my_knock")]
    (WORK / "detection_all.json").write_text(
        json.dumps(
            [
                {
                    "video": VIDEO,
                    "duration": DURATION,
                    "events": [
                        {k: e[k] for k in ("t", "kind", "text", "source") if k in e}
                        for e in ks1_only
                    ],
                    "spectator_ranges": [],
                    "mode": result["mode"],
                }
            ],
            indent=2,
        ),
        encoding="utf-8",
    )
    (vwork / "counter_events.json").write_text(
        json.dumps({"kills": clean_kills, "assists": assist_events}, indent=2),
        encoding="utf-8",
    )

    pad_b, pad_a, gap = 4.0, 3.0, 3.0
    clips = [
        [max(0.0, e["t"] - pad_b), min(DURATION, e["t"] + pad_a), e["kind"]]
        for e in final
    ]
    clips.sort()
    merged = [clips[0]]
    for c in clips[1:]:
        if c[0] <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], c[1])
            merged[-1][2] += "+" + c[2]
        else:
            merged.append(c)
    print(f"clips={len(merged)} sec={sum(c[1] - c[0] for c in merged):.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
