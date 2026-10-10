#!/usr/bin/env python3
"""
MH1 — Match Highlight detect (landscape package input).

GPU-safe:
  Reuse KS1/KS1A banner+feed OCR caches.
  Add engagements from audio peaks + feed-diff (no dense OCR).
  Classify utility/special from cached OCR text only (≤20 new OCR/video).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

ROOT = Path("V:/highlight")
SOURCE = Path("V:/PUBG")
KS1_WORK = ROOT / "work" / "today" / "ks1"
WORK = ROOT / "work" / "today" / "mh1"
SCRIPTS = ROOT / "scripts"

FEED = (1450, 40, 1910, 300)
# Upper-center sky strip — freefall / parachute (tiny crop, light CPU score)
SKY = (700, 20, 1220, 260)
LANDING_SCAN_SEC = 200
MAX_NEW_OCR = 0  # reuse caches; set --ocr for up to 20 new frames/video
OCR_PAUSE = 0.3
FEED_DIFF_THRESH = 14.0
MAX_ENGAGEMENT_PER_VIDEO = 12
PLAYER = "rkanik"
DO_OCR = "--ocr" in sys.argv
USE_AUDIO = "--audio" in sys.argv  # optional; full ebur128 is slow on long VODs
LANDING_OCR = re.compile(
    r"\b(parachute|para\s*chute|free\s*fall|freefall|jump(?:ed|ing)?\s*(out|from)|"
    r"land(?:ed|ing)\b|touch\s*down|drop\s*zone)\b",
    re.I,
)

sys.path.insert(0, str(SCRIPTS))
import ks1_detect_gpu as ks1  # noqa: E402
import ks1a_detect as ks1a  # noqa: E402

UTILITY_RE = re.compile(
    r"\b(frag(?:\s*grenade)?|grenade|molotov|molly|stun(?:\s*grenade)?|"
    r"flash(?:bang)?|smoke(?:\s*grenade)?|throwable|cooked|threw)\b",
    re.I,
)
WINNER_RE = re.compile(
    r"chicken\s*dinner|winner\s*winner|winner\s*#?\s*1|rank\s*#?\s*1\b|"
    r"\b#\s*1\b.*winner|you\s*are\s*the\s*winner",
    re.I,
)
SPECIAL_RE = re.compile(
    r"\b(air\s*drop|airdrop|care\s*package|supply\s*crate|crate\s*drop|"
    r"destroyed\s*(a\s*)?vehicle|vehicle\s*destroy)\b",
    re.I,
)

KIND_RANK = {
    "winner": 8,
    "my_kill": 7,
    "my_knock": 6,
    "my_assist": 5,
    "landing": 4,
    "utility": 3,
    "special": 2,
    "engagement": 1,
}


def run(cmd, check=True):
    print("+", " ".join(map(str, cmd[:14])), ("..." if len(cmd) > 14 else ""), flush=True)
    return subprocess.run(cmd, check=check, capture_output=True)


def vwork_ks1(video: Path, idx: int) -> Path:
    return KS1_WORK / f"v{idx}_{re.sub(r'[^0-9A-Za-z]+', '_', video.stem)}"


def audio_peak_times(video: Path, cache_dir: Path) -> list[float]:
    """Optional. Only runs when --audio; always prefers cache."""
    cache = cache_dir / "ebur_peaks.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8-sig")).get("times") or []
    if not USE_AUDIO:
        return []
    ebur = cache_dir / "ebur.txt"
    cmd = [
        "ffmpeg", "-hide_banner", "-threads", "1",
        "-i", str(video),
        "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    ebur.write_text(r.stderr, encoding="utf-8", errors="replace")
    times: list[float] = []
    for line in r.stderr.splitlines():
        if "M:" not in line or "t:" not in line:
            continue
        tm = re.search(r"t:\s*([0-9.]+)", line)
        mm = re.search(r"M:\s*([+-]?[0-9.]+)", line)
        if not tm or not mm:
            continue
        try:
            t = float(tm.group(1))
            m = float(mm.group(1))
        except ValueError:
            continue
        if m >= -26.0:
            times.append(t)
    times.sort()
    clustered: list[float] = []
    for t in times:
        if not clustered or t - clustered[-1] >= 0.5:
            clustered.append(t)
    cache.write_text(json.dumps({"times": clustered}, indent=2), encoding="utf-8")
    return clustered


def feed_change_times(feed_dir: Path, duration: float) -> list[tuple[float, float]]:
    """Return (t, diff_score) for feed strip changes."""
    files = sorted(feed_dir.glob("f_*.jpg"))
    n = min(int(duration) + 2, len(files))
    prev = None
    out: list[tuple[float, float]] = []
    for t in range(n):
        fp = feed_dir / f"f_{t + 1:06d}.jpg"
        if not fp.exists():
            continue
        im = Image.open(fp).convert("L")
        if prev is not None:
            im_r = im.resize(prev.size) if im.size != prev.size else im
            diff = float(ImageStat.Stat(ImageChops.difference(prev, im_r)).mean[0])
            if diff >= FEED_DIFF_THRESH:
                out.append((float(t), diff))
        prev = im
    return out


def classify_utility_special(text: str) -> str | None:
    if not text:
        return None
    if WINNER_RE.search(text):
        return "winner"
    # Kill/knock/assist tips win — "with Frag Grenade" is a frag kill, not a throw pad
    if ks1a.classify_any(text) or ks1.classify(text):
        return None
    if re.search(r"there\s+is\s+no\s+smoke|pain\s*kill|used\s+energy|used\s+pain", text, re.I):
        return None
    if UTILITY_RE.search(text):
        # Prefer clear throw / inventory tips over weapon-name leftovers
        if re.search(
            r"\b(threw|throw|cooked|tossed|deployed|used)\b|"
            r"\b(frag|molotov|molly|stun|flash|smoke)\s*(grenade|bang)?\b",
            text,
            re.I,
        ):
            # still skip kill-feed style "KILLED … with Frag"
            if re.search(r"\b(kill|knock|elimin)", text, re.I) and re.search(r"\bwith\b", text, re.I):
                return None
            return "utility"
        return None
    if SPECIAL_RE.search(text):
        return "special"
    return None


def load_all_ocr(vwork: Path) -> dict[int, str]:
    ocr: dict[int, str] = {}
    for name in (
        "banner_ocr_sparse.json",
        "banner_ocr_window.json",
        "banner_ocr_rescan.json",
        "banner_ocr_assist.json",
        "feed_ocr_sparse.json",
    ):
        p = vwork / name
        if not p.exists():
            continue
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
        for k, v in raw.items():
            ocr[int(k)] = v or ""
    return ocr


def sky_blue_score(path: Path) -> float:
    """0–100-ish: higher = more open sky (freefall/parachute). Tiny CPU on crop."""
    im = Image.open(path).convert("RGB").resize((96, 48), Image.Resampling.BILINEAR)
    px = im.get_flattened_data() if hasattr(im, "get_flattened_data") else list(im.getdata())
    # RGB flat → triples
    if px and not isinstance(px[0], tuple):
        triples = list(zip(px[0::3], px[1::3], px[2::3]))
    else:
        triples = px
    if not triples:
        return 0.0
    hit = 0
    for r, g, b in triples:
        if b > 95 and b > r + 12 and b > g + 5 and r < 200:
            hit += 1
        elif b > 140 and g > 150 and r > 150 and abs(r - g) < 25:
            hit += 1
    return 100.0 * hit / len(triples)


def extract_sky_gpu(video: Path, out_dir: Path, end_t: float) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(out_dir.glob("f_*.jpg")))
    need = int(min(end_t, LANDING_SCAN_SEC)) + 2
    if n >= need - 5:
        return
    x1, y1, x2, y2 = SKY
    w, h = x2 - x1, y2 - y1
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-hwaccel", "cuda", "-threads", "1",
        "-i", str(video),
        "-t", f"{min(end_t, LANDING_SCAN_SEC):.1f}",
        "-vf", f"fps=1,crop={w}:{h}:{x1}:{y1}",
        "-q:v", "7", "-threads", "1",
        "-y", str(out_dir / "f_%06d.jpg"),
    ]
    r = run(cmd, check=False)
    if r.returncode != 0 or not any(out_dir.glob("f_*.jpg")):
        run([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-hwaccel", "cuda", "-threads", "1",
            "-i", str(video),
            "-t", f"{min(end_t, LANDING_SCAN_SEC):.1f}",
            "-vf", f"fps=1,crop={w}:{h}:{x1}:{y1},format=yuv420p",
            "-q:v", "7", "-threads", "1",
            "-y", str(out_dir / "f_%06d.jpg"),
        ], check=False)


def find_landing_t(
    video: Path,
    mh1_cache: Path,
    duration: float,
    ocr_map: dict[int, str],
    first_fight_t: float | None,
) -> float | None:
    """
    Landing moment if match includes freefall/parachute early.
    Prefer OCR tip; else sky-blue plateau → drop (touchdown).
    """
    cache = mh1_cache / "landing.json"
    if cache.exists():
        raw = json.loads(cache.read_text(encoding="utf-8-sig"))
        if "t" in raw:
            return float(raw["t"]) if raw["t"] is not None else None

    # OCR hints in early window
    for t, text in sorted(ocr_map.items()):
        if t > LANDING_SCAN_SEC:
            continue
        if LANDING_OCR.search(text or ""):
            # prefer later in freefall phrase → near ground
            landing_t = float(min(duration - 1.0, t + 2.0))
            cache.write_text(
                json.dumps({"t": landing_t, "source": "ocr", "text": text}, indent=2),
                encoding="utf-8",
            )
            return landing_t

    # Mid-match start: first fight very early and no long pre-fight → skip
    if first_fight_t is not None and first_fight_t < 45.0:
        cache.write_text(json.dumps({"t": None, "source": "early_fight"}, indent=2), encoding="utf-8")
        return None

    sky_dir = mh1_cache / "sky"
    extract_sky_gpu(video, sky_dir, min(duration, LANDING_SCAN_SEC))
    scores: list[tuple[int, float]] = []
    for fp in sorted(sky_dir.glob("f_*.jpg")):
        try:
            idx = int(fp.stem.split("_")[-1]) - 1
        except ValueError:
            continue
        if idx > LANDING_SCAN_SEC:
            continue
        scores.append((idx, sky_blue_score(fp)))

    if len(scores) < 20:
        cache.write_text(json.dumps({"t": None, "source": "no_sky"}, indent=2), encoding="utf-8")
        return None

    # sustained high-sky window (freefall)
    hi = [t for t, s in scores if s >= 28.0]
    if len(hi) < 6:
        cache.write_text(
            json.dumps({"t": None, "source": "no_freefall", "max_sky": max(s for _, s in scores)}, indent=2),
            encoding="utf-8",
        )
        return None

    # landing ≈ end of high-sky run (blue collapses toward ground)
    hi.sort()
    run_start = hi[0]
    best_end = hi[0]
    cur_start = hi[0]
    prev = hi[0]
    for t in hi[1:]:
        if t - prev <= 3:
            prev = t
            best_end = t
        else:
            if prev - cur_start > best_end - run_start:
                run_start, best_end = cur_start, prev
            cur_start = t
            prev = t
    if prev - cur_start > best_end - run_start:
        run_start, best_end = cur_start, prev

    if best_end - run_start < 5:
        cache.write_text(json.dumps({"t": None, "source": "short_sky"}, indent=2), encoding="utf-8")
        return None

    # Need sky DROP after freefall inside scan window — else still airborne / mid-match sky
    after = [(t, s) for t, s in scores if t > best_end]
    drop = next((t for t, s in after if s <= 18.0), None)
    if drop is None:
        # high-sky ran to end of scan → no confirmed touchdown
        cache.write_text(
            json.dumps({
                "t": None,
                "source": "no_touchdown_drop",
                "sky_run": [run_start, best_end],
            }, indent=2),
            encoding="utf-8",
        )
        return None

    landing_t = float(min(duration - 1.0, drop))
    # must be before first fight (or no fight yet)
    if first_fight_t is not None and landing_t > first_fight_t - 5.0:
        cache.write_text(json.dumps({"t": None, "source": "fight_before_land"}, indent=2), encoding="utf-8")
        return None
    if landing_t < 20.0:
        # lobby / plane door spam — too early to be a clear land beat
        cache.write_text(json.dumps({"t": None, "source": "too_early"}, indent=2), encoding="utf-8")
        return None

    cache.write_text(
        json.dumps({
            "t": landing_t,
            "source": "sky",
            "sky_run": [run_start, best_end],
            "drop_t": drop,
            "max_sky": max(s for _, s in scores),
        }, indent=2),
        encoding="utf-8",
    )
    return landing_t


def cluster_engagements(
    peaks: list[float],
    feed_hits: list[tuple[float, float]],
    covered: list[tuple[float, float]],
) -> list[dict]:
    """Build engagement anchors from dense kill-feed change clusters (+ optional audio)."""
    # Primary: feed-diff bursts (GPU strip already extracted for KS1A)
    if not feed_hits:
        return []
    feed_hits = sorted(feed_hits, key=lambda x: x[0])
    clusters: list[list[tuple[float, float]]] = [[feed_hits[0]]]
    for hit in feed_hits[1:]:
        if hit[0] - clusters[-1][-1][0] <= 6.0:
            clusters[-1].append(hit)
        else:
            clusters.append([hit])

    scored: list[dict] = []
    for cl in clusters:
        if len(cl) < 3:
            continue
        mid = cl[len(cl) // 2][0]
        if any(a - 3.0 <= mid <= b + 3.0 for a, b in covered):
            continue
        # boost if audio peak nearby (when available)
        audio_boost = 1.0
        if peaks and any(abs(mid - p) <= 3.0 for p in peaks):
            audio_boost = 1.5
        score = len(cl) * audio_boost + sum(d for _, d in cl) * 0.05
        scored.append({
            "t": float(mid),
            "kind": "engagement",
            "text": f"feed_cluster n={len(cl)}",
            "source": "feed_cluster",
            "score": float(score),
        })
    scored.sort(key=lambda e: e["score"], reverse=True)
    return scored[:MAX_ENGAGEMENT_PER_VIDEO]


def dedupe_events(events: list[dict], gap: float = 2.0) -> list[dict]:
    events = sorted(events, key=lambda e: e["t"])
    dedup: list[dict] = []
    for e in events:
        if dedup and e["t"] - dedup[-1]["t"] < gap:
            if KIND_RANK.get(e["kind"], 0) > KIND_RANK.get(dedup[-1]["kind"], 0):
                dedup[-1] = e
            continue
        dedup.append(e)
    return dedup


def analyze(video: Path, idx: int, ks1a_result: dict | None) -> dict:
    vwork = vwork_ks1(video, idx)
    vwork.mkdir(parents=True, exist_ok=True)
    mh1_cache = WORK / f"v{idx}_{re.sub(r'[^0-9A-Za-z]+', '_', video.stem)}"
    mh1_cache.mkdir(parents=True, exist_ok=True)

    print(f"\n===== MH1 [{idx}] {video.name} =====", flush=True)
    duration = ks1.duration_of(video)

    events: list[dict] = []
    team: list[str] = []
    me = PLAYER

    # --- base: KS1A kills/knocks/assists ---
    if ks1a_result and ks1a_result.get("events"):
        for e in ks1a_result["events"]:
            events.append({
                "t": float(e["t"]),
                "kind": e["kind"],
                "text": e.get("text", ""),
                "source": e.get("source", "ks1a"),
            })
        team = ks1a_result.get("team") or []
        me = ks1a_result.get("me") or PLAYER
        print(f"reuse KS1A events={len(ks1a_result['events'])}", flush=True)
    else:
        # fallback: run KS1A analyze for this video
        print("no KS1A cache — running ks1a.analyze", flush=True)
        ks1a_result = ks1a.analyze(video, idx)
        for e in ks1a_result["events"]:
            events.append({
                "t": float(e["t"]),
                "kind": e["kind"],
                "text": e.get("text", ""),
                "source": e.get("source", "ks1a"),
            })
        team = ks1a_result.get("team") or []
        me = ks1a_result.get("me") or PLAYER

    # --- utility / special / winner from cached OCR ---
    ocr_map = load_all_ocr(vwork)
    for t, text in sorted(ocr_map.items()):
        kind = classify_utility_special(text)
        if not kind:
            continue
        # skip map-only assist junk already handled elsewhere
        if kind == "utility" and ks1a.MAP_ONLY_ASSIST.search((text or "").strip()):
            continue
        events.append({
            "t": float(t),
            "kind": kind,
            "text": text,
            "source": "ocr_cache",
        })
        safe = (text or "")[:80].encode("ascii", "replace").decode("ascii")
        print(f"  HIT tip t={t} {kind}: {safe}", flush=True)

    # optional sparse new OCR (off by default — thermal)
    banner_dir = vwork / "banner"
    if DO_OCR and banner_dir.exists():
        scored = ks1.banner_candidates(banner_dir, duration)
        to_ocr = [t for _, t in scored if t not in ocr_map][:MAX_NEW_OCR or 20]
        new_ocr = {}
        for i, t in enumerate(to_ocr, 1):
            fp = banner_dir / f"f_{t + 1:06d}.jpg"
            if not fp.exists():
                continue
            text = ks1.ocr_one(fp)
            ocr_map[t] = text
            new_ocr[t] = text
            kind = classify_utility_special(text) or ks1a.classify_any(text)
            if kind:
                events.append({
                    "t": float(t),
                    "kind": kind,
                    "text": text,
                    "source": "banner_new",
                })
            time.sleep(OCR_PAUSE)
            if i % 10 == 0:
                print(f"  MH1 OCR {i}/{len(to_ocr)}", flush=True)
        if new_ocr:
            (mh1_cache / "banner_ocr_mh1.json").write_text(
                json.dumps({str(k): v for k, v in sorted(new_ocr.items())}, indent=2),
                encoding="utf-8",
            )

    # --- engagements: feed-diff clusters (GPU strip); audio optional/cached ---
    feed_dir = vwork / "feed"
    if not feed_dir.exists() or not any(feed_dir.glob("f_*.jpg")):
        ks1a.extract_region_gpu(video, feed_dir, FEED)
    feed_hits = feed_change_times(feed_dir, duration) if feed_dir.exists() else []
    peaks = audio_peak_times(video, mh1_cache)

    covered = []
    for e in events:
        if e["kind"] in ("my_kill", "my_knock", "my_assist", "winner"):
            covered.append((e["t"] - 6.0, e["t"] + 6.0))
    eng = cluster_engagements(peaks, feed_hits, covered)
    events.extend(eng)
    print(f"engagements={len(eng)} audio_peaks={len(peaks)} feed_changes={len(feed_hits)}", flush=True)

    # --- landing (match separator) ---
    fight_ts = [
        e["t"] for e in events
        if e["kind"] in ("my_kill", "my_knock", "my_assist", "engagement")
    ]
    first_fight = min(fight_ts) if fight_ts else None
    landing_t = find_landing_t(video, mh1_cache, duration, ocr_map, first_fight)
    if landing_t is not None:
        events.append({
            "t": float(landing_t),
            "kind": "landing",
            "text": "match_landing",
            "source": "landing",
        })
        print(f"  landing t={landing_t:.1f}", flush=True)
    else:
        print("  landing: none", flush=True)

    dedup = dedupe_events(events, gap=2.0)
    counts = {k: 0 for k in KIND_RANK}
    for e in dedup:
        counts[e["kind"]] = counts.get(e["kind"], 0) + 1

    result = {
        "video": str(video),
        "duration": duration,
        "events": dedup,
        "template": "MH1",
        "mode": "mh1_match_highlight",
        "team": team,
        "me": me,
        "counts": counts,
        "landing_t": landing_t,
        "winner_confirmed": counts.get("winner", 0) > 0,
    }
    (mh1_cache / "mh1_events.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(f"{video.name}: events={len(dedup)} {counts}", flush=True)
    return result


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    videos = sorted(SOURCE.glob("*.mp4"))
    if not videos:
        print("No videos in", SOURCE)
        return 1

    ks1a_by_video: dict[str, dict] = {}
    ks1a_path = KS1_WORK / "detection_ks1a.json"
    if ks1a_path.exists():
        for r in json.loads(ks1a_path.read_text(encoding="utf-8-sig")):
            p = Path(r["video"])
            ks1a_by_video[p.name] = r
            try:
                ks1a_by_video[str(p.resolve())] = r
            except OSError:
                pass
        print(f"loaded KS1A detection ({len(ks1a_by_video)} keys)", flush=True)

    results = []
    for i, v in enumerate(videos, 1):
        hit = ks1a_by_video.get(v.name) or ks1a_by_video.get(str(v.resolve()))
        results.append(analyze(v, i, hit))
        time.sleep(0.8)

    out = WORK / "detection_mh1.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    total = sum(len(r["events"]) for r in results)
    print(f"\nMH1 TOTAL events={total}", flush=True)
    for r in results:
        print(f"  {Path(r['video']).name}: {len(r['events'])} {r['counts']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
