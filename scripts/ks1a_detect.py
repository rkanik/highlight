#!/usr/bin/env python3
"""
KS1A — kills/knocks + ASSIST moments.

GPU-safe:
  1) banner tip OCR (reuse cache; sparse new OCR)
  2) bottom-left team roster (few stills)
  3) top-right kill feed (GPU strip + sparse OCR), match roster names
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
WORK = ROOT / "work" / "today" / "ks1"
SCRIPTS = ROOT / "scripts"

# 1920x1080 crops
TEAM = (10, 860, 430, 1045)
FEED = (1450, 40, 1910, 300)
MAX_BANNER_OCR = 30
MAX_FEED_OCR = 35
MAX_TEAM_OCR = 4
OCR_PAUSE = 0.3
FEED_DIFF_THRESH = 14.0
PLAYER = "rkanik"

sys.path.insert(0, str(SCRIPTS))
import ks1_detect_gpu as ks1  # noqa: E402

ASSIST_RE = re.compile(
    r"\bi\s*assist|\bassists?\b|\bassisted\b|\+\s*\d+\s*assist|assist\s*\+|you\s*assist|"
    r"\d+\s*assists?\b|u?ssists?|ass+ist",
    re.I,
)
MAP_ONLY_ASSIST = re.compile(r"^\s*\d+\s*assisted\s*$", re.I)
STOP_TOKENS = {
    "asa", "asai", "iasai", "iasa", "alive", "killed", "assisted", "kill", "phase",
    "san", "map", "you", "out", "with", "the", "and", "for", "from",
}


def classify_assist(text: str) -> str | None:
    if not text or MAP_ONLY_ASSIST.search(text.strip()):
        return None
    if ASSIST_RE.search(text):
        if ks1.VICTIM_ME.search(text) and not re.search(r"\bassist", text, re.I):
            return None
        return "my_assist"
    return None


def _roster_victim(text: str, name: str) -> bool:
    """True when name sits on victim side: 'KNOCKED [ASA] name out'."""
    pos = name_pos(name, text)
    if pos is None:
        return False
    before = text.lower()[max(0, pos - 48) : pos]
    return bool(re.search(r"(knocked|killed|eliminated|cked\s*out)\b", before))


def classify_any(text: str, me: str = PLAYER, team: list[str] | None = None) -> str | None:
    a = classify_assist(text)
    if a:
        return a
    if team and text:
        t = text.lower()
        has_you = bool(re.search(r"\byou\b", t))
        has_me = name_pos(me, text) is not None and not _roster_victim(text, me)
        for n in team:
            if _norm_token(n) == _norm_token(me):
                continue
            if name_pos(n, text) is None:
                continue
            if _roster_victim(text, n):
                return None
            if has_you or has_me:
                break
            # teammate-only tip (no YOU / me) → assist, not my knock/kill
            if re.search(r"kill|knock|cked\s*out|elimin|\bx\b", t):
                return "my_assist"
            return None
    return ks1.classify(text)


def ocr_one(path: Path) -> str:
    return ks1.ocr_one(path)


def extract_region_gpu(video: Path, out_dir: Path, box: tuple[int, int, int, int]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    n = len(list(out_dir.glob("f_*.jpg")))
    if n > 50:
        print(f"skip extract ({n} frames): {out_dir}", flush=True)
        return
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-hwaccel", "cuda", "-threads", "1",
        "-i", str(video),
        "-vf", f"fps=1,crop={w}:{h}:{x1}:{y1}",
        "-q:v", "6", "-threads", "1",
        "-y", str(out_dir / "f_%06d.jpg"),
    ]
    r = ks1.run(cmd, check=False)
    if r.returncode != 0 or not any(out_dir.glob("f_*.jpg")):
        ks1.run([
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-hwaccel", "cuda", "-threads", "1",
            "-i", str(video),
            "-vf", f"fps=1,crop={w}:{h}:{x1}:{y1},format=yuv420p",
            "-q:v", "6", "-threads", "1",
            "-y", str(out_dir / "f_%06d.jpg"),
        ])


def extract_team_stills(video: Path, out_dir: Path, duration: float) -> list[Path]:
    """Few team-panel stills — not a full-video strip."""
    out_dir.mkdir(parents=True, exist_ok=True)
    times = [30.0, 90.0, min(180.0, max(10.0, duration * 0.25)), min(duration - 5.0, 400.0)]
    times = sorted({max(1.0, min(duration - 1.0, t)) for t in times})
    paths = []
    x1, y1, x2, y2 = TEAM
    w, h = x2 - x1, y2 - y1
    for i, t in enumerate(times[:MAX_TEAM_OCR]):
        fp = out_dir / f"team_{i + 1:02d}_{int(t)}.jpg"
        if not fp.exists():
            ks1.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-hwaccel", "cuda", "-threads", "1",
                "-ss", f"{t:.2f}", "-i", str(video),
                "-frames:v", "1",
                "-vf", f"crop={w}:{h}:{x1}:{y1}",
                "-q:v", "3", "-threads", "1",
                "-y", str(fp),
            ], check=False)
        if fp.exists():
            paths.append(fp)
    return paths


def _norm_token(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def parse_team_names(text: str) -> list[str]:
    """Pull squad names from bottom-left OCR (tolerant of ASA garble)."""
    if not text:
        return []
    cleaned = re.sub(r"[\[\]\(\)\"']", " ", text)
    # strip clan-tag OCR junk: ASA / IASAI / lilASAl / ASAl
    cleaned = re.sub(r"(?i)[a-z]{0,3}asa[il1]{0,2}", " ", cleaned)
    chunks = re.split(r"\b[1-4]\b", cleaned)
    names: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        toks = [
            t for t in re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}", chunk)
            if _norm_token(t) not in STOP_TOKENS and "asa" not in _norm_token(t)
        ]
        if not toks:
            continue
        cand = max(toks, key=len)
        if len(toks) >= 2 and len(cand) < 8:
            joined = "".join(toks[:2])
            if len(joined) >= 8:
                cand = joined
        key = _norm_token(cand)
        if len(key) < 4 or key in STOP_TOKENS or key in seen:
            continue
        # collapse PLAYER OCR variants to canonical
        if _norm_token(PLAYER) in key or key in _norm_token(PLAYER):
            key = _norm_token(PLAYER)
            cand = PLAYER
        if key in seen:
            continue
        seen.add(key)
        names.append(cand)
    if _norm_token(PLAYER) in _norm_token(text) and _norm_token(PLAYER) not in seen:
        names.insert(0, PLAYER)
    return names[:4]


def load_or_ocr_team(video: Path, vwork: Path, duration: float) -> list[str]:
    cache = vwork / "team_names.json"
    if cache.exists():
        raw = json.loads(cache.read_text(encoding="utf-8-sig"))
        names = raw.get("names") or []
        if names:
            print(f"team roster (cache): {names}", flush=True)
            return names
    paths = extract_team_stills(video, vwork / "team", duration)
    names: list[str] = []
    ocr_blob = []
    for p in paths:
        tx = ocr_one(p)
        ocr_blob.append(tx)
        for n in parse_team_names(tx):
            if _norm_token(n) not in {_norm_token(x) for x in names}:
                names.append(n)
        time.sleep(OCR_PAUSE)
    if _norm_token(PLAYER) not in {_norm_token(n) for n in names}:
        # fallback — still search feed for PLAYER
        names.insert(0, PLAYER)
    cache.write_text(
        json.dumps({"names": names, "ocr": ocr_blob}, indent=2),
        encoding="utf-8",
    )
    print(f"team roster: {names}", flush=True)
    return names


def name_pos(name: str, text: str) -> int | None:
    """Earliest match of name / stem in feed OCR."""
    if not name or not text:
        return None
    t = text.lower()
    n = name.lower()
    idx = t.find(n)
    if idx >= 0:
        return idx
    stem = _norm_token(name)
    if len(stem) < 4:
        return None
    # try stems 5–8 chars for OCR damage (Ashiaqul / sazalOas)
    for L in (8, 6, 5, 4):
        if len(stem) < L:
            continue
        s = stem[:L]
        compact = _norm_token(text)
        cidx = compact.find(s)
        if cidx < 0:
            continue
        # map compact idx → approx raw idx
        raw_i = 0
        comp_i = 0
        while raw_i < len(t) and comp_i < cidx:
            if t[raw_i].isalnum():
                comp_i += 1
            raw_i += 1
        return raw_i
    return None


def classify_feed_line(text: str, me: str, team: list[str]) -> str | None:
    """
    Kill feed is killer (left) → victim (right).
    Leftmost roster name = squad actor.
    me → my_knock / my_kill; other teammate → my_assist.
    """
    if not text or ks1.SPEC_PAT.search(text):
        return None
    t = text.lower()
    if ks1.VICTIM_ME.search(text):
        return None

    hits: list[tuple[int, str, bool]] = []
    for n in team:
        pos = name_pos(n, text)
        if pos is None:
            continue
        is_me = _norm_token(me) in _norm_token(n) or _norm_token(n) in _norm_token(me)
        hits.append((pos, n, is_me))
    if not hits:
        return None
    hits.sort(key=lambda x: x[0])
    pos, name, is_me = hits[0]

    # name too far right → likely victim, not killer
    if pos > max(10, int(len(t) * 0.55)):
        return None
    if _roster_victim(text, name):
        return None

    rest = t[pos : pos + 70]
    if is_me:
        if re.search(r"\bx\b|knock", rest):
            return "my_knock"
        return "my_kill"

    # teammate on left of feed = their frag / my assist window
    return "my_assist"


def feed_candidates(feed_dir: Path, duration: float) -> list[tuple[float, int]]:
    files = sorted(feed_dir.glob("f_*.jpg"))
    n = min(int(duration) + 2, len(files))
    prev = None
    scored: list[tuple[float, int]] = []
    for t in range(n):
        fp = feed_dir / f"f_{t + 1:06d}.jpg"
        if not fp.exists():
            continue
        im = Image.open(fp).convert("L")
        if prev is not None:
            if prev.size != im.size:
                im_r = im.resize(prev.size)
            else:
                im_r = im
            diff = float(ImageStat.Stat(ImageChops.difference(prev, im_r)).mean[0])
            if diff >= FEED_DIFF_THRESH:
                scored.append((diff, t))
        prev = im
    scored.sort(reverse=True)
    return scored


def unscanned_banner(banner_dir: Path, duration: float, ocr_map: dict) -> list[int]:
    scored = ks1.banner_candidates(banner_dir, duration)
    out = [t for _, t in scored if t not in ocr_map]
    return out[:MAX_BANNER_OCR]


def dedupe_events(events: list[dict], gap: float = 1.8) -> list[dict]:
    events = sorted(events, key=lambda e: e["t"])
    dedup: list[dict] = []
    rank = {"my_kill": 4, "my_knock": 3, "my_assist": 2, "my_kill_or_knock": 1}
    for e in events:
        if dedup and e["t"] - dedup[-1]["t"] < gap:
            if rank.get(e["kind"], 0) > rank.get(dedup[-1]["kind"], 0):
                dedup[-1] = e
            continue
        dedup.append(e)
    return dedup


def analyze(video: Path, idx: int) -> dict:
    vwork = WORK / f"v{idx}_{re.sub(r'[^0-9A-Za-z]+', '_', video.stem)}"
    vwork.mkdir(parents=True, exist_ok=True)
    print(f"\n===== KS1A [{idx}] {video.name} =====", flush=True)
    duration = ks1.duration_of(video)

    # --- team roster (bottom-left) ---
    team = load_or_ocr_team(video, vwork, duration)
    me = next((n for n in team if _norm_token(PLAYER) in _norm_token(n)), PLAYER)

    # --- banner tips ---
    banner_dir = vwork / "banner"
    ks1.extract_banner_gpu(video, banner_dir)

    ocr_map = ks1.load_ocr_maps(vwork)
    to_ocr = unscanned_banner(banner_dir, duration, ocr_map)
    for t, text in list(ocr_map.items()):
        if ASSIST_RE.search(text or ""):
            for n in (t - 1, t + 1, t + 2):
                if 0 <= n <= int(duration) and n not in ocr_map and n not in to_ocr:
                    to_ocr.append(n)
    to_ocr = sorted(set(to_ocr))[:MAX_BANNER_OCR]
    print(f"banner cached={len(ocr_map)} new_ocr={len(to_ocr)}", flush=True)

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
            print(f"  banner OCR {i}/{len(to_ocr)}", flush=True)

    if assist_new:
        assist_ocr_path.write_text(
            json.dumps({str(k): v for k, v in sorted(assist_new.items())}, indent=2),
            encoding="utf-8",
        )
    (vwork / "banner_ocr_sparse.json").write_text(
        json.dumps({str(k): v for k, v in sorted(ocr_map.items())}, indent=2),
        encoding="utf-8",
    )

    events: list[dict] = []
    ocr_for_class = ks1.promote_neighbor_partials(ocr_map)
    for t, text in sorted(ocr_for_class.items()):
        kind = classify_any(text, me=me, team=team)
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

    # --- kill feed (top-right) matched to team roster ---
    feed_dir = vwork / "feed"
    extract_region_gpu(video, feed_dir, FEED)
    feed_ocr_path = vwork / "feed_ocr_sparse.json"
    feed_ocr: dict[int, str] = {}
    if feed_ocr_path.exists():
        feed_ocr = {int(k): v for k, v in json.loads(feed_ocr_path.read_text(encoding="utf-8-sig")).items()}

    scored_feed = feed_candidates(feed_dir, duration)
    feed_to_ocr = [t for _, t in scored_feed if t not in feed_ocr][:MAX_FEED_OCR]
    # also OCR feed at banner hit times (tip flash often pairs with feed line)
    for e in events:
        t = int(e["t"])
        for n in (t, t + 1, t - 1):
            if 0 <= n <= int(duration) and n not in feed_ocr and n not in feed_to_ocr:
                feed_to_ocr.append(n)
    feed_to_ocr = sorted(set(feed_to_ocr))[:MAX_FEED_OCR]
    print(f"feed cached={len(feed_ocr)} new_ocr={len(feed_to_ocr)} team={team}", flush=True)

    for i, t in enumerate(feed_to_ocr, 1):
        if t in feed_ocr:
            continue
        fp = feed_dir / f"f_{t + 1:06d}.jpg"
        if not fp.exists():
            continue
        text = ocr_one(fp)
        feed_ocr[t] = text
        time.sleep(OCR_PAUSE)
        if i % 10 == 0:
            feed_ocr_path.write_text(
                json.dumps({str(k): v for k, v in sorted(feed_ocr.items())}, indent=2),
                encoding="utf-8",
            )
            print(f"  feed OCR {i}/{len(feed_to_ocr)}", flush=True)

    feed_ocr_path.write_text(
        json.dumps({str(k): v for k, v in sorted(feed_ocr.items())}, indent=2),
        encoding="utf-8",
    )

    for t, text in sorted(feed_ocr.items()):
        kind = classify_feed_line(text, me, team)
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

    dedup = dedupe_events(events, gap=1.8)
    result = {
        "video": str(video),
        "duration": duration,
        "events": dedup,
        "template": "KS1A",
        "mode": "ks1a_team_feed",
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
