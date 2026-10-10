#!/usr/bin/env python3
"""Assemble MH1 landscape match highlight from detection_mh1.json (≤10 min)."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path("V:/highlight")
WORK = ROOT / "work" / "today" / "mh1"
OUT = ROOT / "out"

MAX_TOTAL = 600.0
MERGE_GAP = 5.0
MIN_CLIP = 6.0
MAX_CLIP = 45.0
COLD_OPEN_TARGET = 5.0
COLD_OPEN_N_MIN = 3
COLD_OPEN_N_MAX = 5
LANDING_PAD = (1.0, 4.0)  # ~5s match separator

PAD = {
    "my_kill": (5.0, 4.0),
    "my_knock": (5.0, 4.0),
    "my_assist": (5.0, 4.0),
    "utility": (2.0, 3.0),
    "engagement": (4.0, 4.0),
    "special": (3.0, 3.0),
    "winner": (3.0, 5.0),
    "landing": LANDING_PAD,
}

# lower number = keep first when trimming
PRIORITY = {
    "winner": 0,
    "my_kill": 1,
    "my_knock": 2,
    "landing": 2,  # protect match separators
    "my_assist": 3,
    "utility": 4,
    "engagement": 5,
    "special": 6,
}


def run(cmd, check=True):
    print("+", " ".join(map(str, cmd[:16])), ("..." if len(cmd) > 16 else ""), flush=True)
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def ffprobe_duration(path: Path) -> float:
    r = run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(r.stdout.strip())


def primary_kind(kind: str) -> str:
    parts = kind.split("+")
    best = parts[0]
    best_p = PRIORITY.get(best, 99)
    for p in parts[1:]:
        pr = PRIORITY.get(p, 99)
        if pr < best_p:
            best, best_p = p, pr
    return best


def events_to_clips(events: list[dict], duration: float) -> list[dict]:
    """Build fight clips. Landing events stay out — handled as match separators."""
    raw = []
    for e in events:
        kind = e["kind"]
        if kind == "landing":
            continue
        before, after = PAD.get(kind, (4.0, 3.0))
        start = max(0.0, e["t"] - before)
        end = min(duration, e["t"] + after)
        raw.append({
            "start": start,
            "end": end,
            "anchor": e["t"],
            "kind": kind,
            "text": e.get("text", ""),
            "prio": PRIORITY.get(kind, 50),
        })
    if not raw:
        return []
    raw.sort(key=lambda c: c["start"])
    merged = [raw[0].copy()]
    for c in raw[1:]:
        cur = merged[-1]
        if c["start"] <= cur["end"] + MERGE_GAP:
            cur["end"] = max(cur["end"], c["end"])
            cur["kind"] = cur["kind"] + "+" + c["kind"]
            cur["prio"] = min(cur["prio"], c["prio"])
            # keep earliest high-value anchor for trim
            if c["prio"] < PRIORITY.get(primary_kind(cur["kind"].split("+")[0]), 50):
                cur["anchor"] = c["anchor"]
        else:
            merged.append(c.copy())

    out = []
    for c in merged:
        dur = c["end"] - c["start"]
        if dur < MIN_CLIP:
            mid = (c["start"] + c["end"]) / 2
            c["start"] = max(0.0, mid - MIN_CLIP / 2)
            c["end"] = min(duration, c["start"] + MIN_CLIP)
        if c["end"] - c["start"] > MAX_CLIP:
            pk = primary_kind(c["kind"])
            before, after = PAD.get(pk, (4.0, 3.0))
            anchor = c.get("anchor", c["start"] + before)
            c["start"] = max(0.0, anchor - before)
            c["end"] = min(duration, c["start"] + MAX_CLIP)
        c["dur"] = round(c["end"] - c["start"], 2)
        c["prio"] = PRIORITY.get(primary_kind(c["kind"]), c.get("prio", 50))
        out.append(c)
    return out


def landing_clip(video: str, duration: float, landing_t: float) -> dict:
    before, after = LANDING_PAD
    start = max(0.0, landing_t - before)
    end = min(duration, landing_t + after)
    # keep separator readable (~5s)
    if end - start < 4.0:
        end = min(duration, start + 5.0)
    if end - start > 6.0:
        end = start + 6.0
    return {
        "start": start,
        "end": end,
        "anchor": landing_t,
        "kind": "landing",
        "text": "match_landing",
        "prio": PRIORITY["landing"],
        "dur": round(end - start, 2),
        "video": video,
        "role": "landing",
    }


def score_kill_event(e: dict, neighbors: list[dict]) -> float:
    kind = e.get("kind", "")
    if kind not in ("my_kill", "my_knock"):
        return -1.0
    score = 10.0 if kind == "my_kill" else 6.0
    text = (e.get("text") or "").lower()
    if re.search(r"head\s*shot|dshot|h\$d", text):
        score += 3.5
    if re.search(r"frag|grenade|molotov", text):
        score += 2.5
    if re.search(r"kar98|awm|m24|mk14", text):
        score += 2.0
    t = float(e["t"])
    near = sum(
        1 for n in neighbors
        if n.get("kind") in ("my_kill", "my_knock") and abs(float(n["t"]) - t) <= 8.0 and n is not e
    )
    score += min(4.0, near * 1.5)
    return score


def build_cold_open(results: list[dict]) -> list[dict]:
    """Top 3–5 kills as ~5s teaser at start (micro cuts). Full clips still in body."""
    scored: list[tuple[float, dict, str, float]] = []
    for r in results:
        evs = r.get("events") or []
        for e in evs:
            s = score_kill_event(e, evs)
            if s < 0:
                continue
            scored.append((s, e, r["video"], float(r["duration"])))
    if not scored:
        return []
    scored.sort(key=lambda x: x[0], reverse=True)
    # diversify across matches a bit: cap 2 per video in cold open
    picked: list[tuple[float, dict, str, float]] = []
    per_vid: dict[str, int] = {}
    for item in scored:
        vid = item[2]
        if per_vid.get(vid, 0) >= 2:
            continue
        picked.append(item)
        per_vid[vid] = per_vid.get(vid, 0) + 1
        if len(picked) >= COLD_OPEN_N_MAX:
            break
    if len(picked) < COLD_OPEN_N_MIN:
        picked = scored[:COLD_OPEN_N_MIN]
    n = max(COLD_OPEN_N_MIN, min(COLD_OPEN_N_MAX, len(picked)))
    picked = picked[:n]
    slice_dur = COLD_OPEN_TARGET / n
    cold = []
    for s, e, video, duration in picked:
        t = float(e["t"])
        # punch: just before tip → impact
        start = max(0.0, t - 0.25)
        end = min(duration, start + slice_dur)
        if end - start < 0.7:
            end = min(duration, t + 0.7)
        cold.append({
            "start": start,
            "end": end,
            "anchor": t,
            "kind": e["kind"],
            "text": e.get("text", ""),
            "prio": 0,
            "dur": round(end - start, 2),
            "video": video,
            "role": "cold_open",
            "score": round(s, 2),
        })
    return cold


def order_with_landings(
    results: list[dict],
    body_clips: list[dict],
    cold: list[dict],
    landings: dict[str, dict],
) -> list[dict]:
    """cold open → per match: landing (if any) → fight clips chrono."""
    out = list(cold)
    for r in results:
        vid = r["video"]
        if vid in landings:
            out.append(landings[vid])
        chunk = [c for c in body_clips if c["video"] == vid]
        chunk.sort(key=lambda c: c["start"])
        out.extend(chunk)
    return out


def trim_to_budget(clips: list[dict], budget: float = MAX_TOTAL) -> list[dict]:
    total = sum(c["dur"] for c in clips)
    if total <= budget:
        return clips
    # Drop lowest-priority first; never drop cold_open / landing / top kills early
    ranked = sorted(enumerate(clips), key=lambda iv: (-iv[1]["prio"], -iv[1]["dur"]))
    drop: set[int] = set()
    for idx, c in ranked:
        if total <= budget:
            break
        if c.get("role") in ("cold_open", "landing"):
            continue
        if c["prio"] <= 1 and total - c["dur"] < budget * 0.5:
            continue
        drop.add(idx)
        total -= c["dur"]
    kept = [c for i, c in enumerate(clips) if i not in drop]
    total = sum(c["dur"] for c in kept)
    while kept and total > budget:
        # trim from end, skip cold_open at front
        cut_i = len(kept) - 1
        while cut_i > 0 and kept[cut_i].get("role") == "cold_open":
            cut_i -= 1
        last = kept[cut_i]
        if last.get("role") in ("cold_open", "landing"):
            break
        overflow = total - budget
        min_keep = 4.0 if last.get("role") == "landing" else MIN_CLIP
        if last["dur"] - overflow >= min_keep:
            last["end"] -= overflow
            last["dur"] = round(last["end"] - last["start"], 2)
            total = budget
            break
        total -= last["dur"]
        kept.pop(cut_i)
    return kept


def encode_seg(video: Path, start: float, end: float, out_path: Path) -> None:
    dur = max(0.1, end - start)
    # landscape — full frame, no portrait crop
    vf = "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2"
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-hwaccel", "cuda", "-threads", "1",
        "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{dur:.3f}",
        "-vf", vf, "-r", "60",
        "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "18",
        "-b:v", "12M", "-maxrate", "16M", "-bufsize", "24M",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-threads", "1",
        "-movflags", "+faststart", "-y", str(out_path),
    ]
    r = run(cmd, check=False)
    if r.returncode != 0 or not out_path.exists():
        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-hwaccel", "cuda", "-threads", "1",
            "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{dur:.3f}",
            "-vf", vf, "-r", "60",
            "-c:v", "h264_nvenc", "-preset", "p5", "-cq", "18", "-b:v", "12M",
            "-c:a", "aac", "-b:a", "192k", "-threads", "1",
            "-y", str(out_path),
        ]
        run(cmd)


def concat_segs(segs, out_path: Path) -> None:
    lst = out_path.parent / "concat.txt"
    lines = []
    for s in segs:
        p = Path(s).resolve().as_posix()
        lines.append(f"file '{p}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c", "copy", "-movflags", "+faststart", "-y", str(out_path),
    ])


def sanitize_title(title: str) -> str:
    t = title.replace("|", " ")
    t = re.sub(r'[<>:"/\\?*]', " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:120]


def parse_title_arg(argv: list[str]) -> str | None:
    if "--title" in argv:
        i = argv.index("--title")
        if i + 1 < len(argv):
            return argv[i + 1].strip()
    # also allow TITLE=... via env-like bare first non-flag after script — skip
    return None


def resolve_ai_base(argv: list[str]) -> Path | None:
    if "--ai-base" in argv:
        i = argv.index("--ai-base")
        if i + 1 < len(argv):
            p = Path(argv[i + 1])
            if p.exists():
                return p
    # newest AI base in work/thumb_ai
    d = WORK / "thumb_ai"
    if d.exists():
        cands = sorted(d.glob("*.jpg"), key=lambda p: p.stat().st_mtime, reverse=True)
        cands += sorted(d.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        if cands:
            return cands[0]
    return None


def suggest_title_from_events(results: list[dict]) -> str:
    """Fallback hook only — prefer agent --title. Never use counts."""
    blob = " ".join(
        (e.get("text") or "")
        for r in results
        for e in r.get("events", [])
        if e.get("kind") in ("my_kill", "my_knock", "my_assist", "utility")
    ).lower()
    bits = []
    if re.search(r"kar98|m24|awm|head\s*shot|dshot", blob):
        bits.append("Kar98")
    if re.search(r"frag|grenade", blob):
        bits.append("Frag")
    if any(e.get("kind") == "my_assist" for r in results for e in r.get("events", [])):
        bits.append("Assist")
    if not bits:
        bits = ["Squad Fight"]
    hook = " ".join(bits[:3]) + " Fight Spree"
    return f"{hook} | PUBG"


def build_thumbs(ai_base: Path | None, out_dir: Path, title: str) -> None:
    if ai_base and ai_base.exists():
        r = run([
            sys.executable, "-u", str(ROOT / "scripts" / "composite_ai_thumb.py"),
            str(ai_base), str(out_dir), title,
        ], check=False)
        if r.returncode == 0 and (out_dir / f"{out_dir.name}.jpg").exists():
            return
        print("AI composite failed — falling back to logo-on-cover", flush=True)
    # Fallback: cover art + logo + hook (still no kill count)
    cover = ROOT / "augniik-cover.jpg"
    if cover.exists():
        run([
            sys.executable, "-u", str(ROOT / "scripts" / "composite_ai_thumb.py"),
            str(cover), str(out_dir), title,
        ], check=False)


def write_description(
    path: Path,
    title: str,
    counts: dict,
    clip_count: int,
    dur: float,
    winner: bool,
) -> None:
    cd_tag = " #ChickenDinner" if winner else ""
    body = f"""{title}

PUBG match highlight (MH1) — landscape fight reel from today's matches. Cold-open top kills, landing separators between matches, then knocks/kills/assists/engagements/utility. Spectator / replay-panel ranges skipped.

Moments:
- Cold-open teaser + {counts.get('landing', 0)} landing separators
- {counts.get('my_knock', 0)} knocks · {counts.get('my_kill', 0)} kills · {counts.get('my_assist', 0)} assists
- {counts.get('engagement', 0)} engagements · {counts.get('utility', 0)} utility · {counts.get('special', 0)} special
- {clip_count} packages · ~{dur:.0f}s landscape
- Player POV only ([ASA] rkanik)

Keywords:
PUBG highlights, match highlight, PUBG fights, PUBG kills, assist, grenade play, squad fight, battlegrounds highlight, FPS montage, PUBG TPP

#PUBG #PUBGHighlights #MatchHighlight #FPS #Gaming #Battlegrounds #PCGaming #Gunfight #TeamPlay #Assist #PUBGPC{cd_tag}
"""
    path.write_text(body.strip() + "\n", encoding="utf-8")


def main() -> int:
    det_path = WORK / "detection_mh1.json"
    if not det_path.exists():
        print("missing", det_path, "— run mh1_detect.py first")
        return 1
    results = json.loads(det_path.read_text(encoding="utf-8-sig"))

    body_clips = []
    landings: dict[str, dict] = {}
    counts = {
        "my_knock": 0, "my_kill": 0, "my_assist": 0,
        "utility": 0, "engagement": 0, "special": 0, "winner": 0,
        "landing": 0,
    }
    winner = False
    for r in results:
        clips = events_to_clips(r["events"], r["duration"])
        for c in clips:
            c["video"] = r["video"]
            c["role"] = "body"
        body_clips.extend(clips)
        lt = r.get("landing_t")
        if lt is None:
            for e in r.get("events") or []:
                if e.get("kind") == "landing":
                    lt = e["t"]
                    break
        if lt is not None:
            landings[r["video"]] = landing_clip(r["video"], float(r["duration"]), float(lt))
        for e in r["events"]:
            k = e.get("kind")
            if k in counts:
                counts[k] += 1
        winner = winner or bool(r.get("winner_confirmed"))
    if counts["landing"] == 0:
        counts["landing"] = len(landings)

    cold = build_cold_open(results)
    ordered = order_with_landings(results, body_clips, cold, landings)
    before = sum(c["dur"] for c in ordered)
    all_clips = trim_to_budget(ordered, MAX_TOTAL)
    after = sum(c["dur"] for c in all_clips)
    cold_kept = [c for c in all_clips if c.get("role") == "cold_open"]
    land_kept = [c for c in all_clips if c.get("role") == "landing"]
    (WORK / "all_clips_mh1.json").write_text(
        json.dumps({
            "clips": all_clips,
            "counts": counts,
            "template": "MH1",
            "cold_open": cold_kept,
            "landings": land_kept,
            "seconds_before_trim": round(before, 1),
            "seconds_after_trim": round(after, 1),
            "winner_confirmed": winner,
        }, indent=2),
        encoding="utf-8",
    )
    print(
        f"[MH1] clips={len(all_clips)} cold={len(cold_kept)} landings={len(land_kept)} "
        f"counts={counts} {before:.1f}s -> {after:.1f}s",
        flush=True,
    )
    if not all_clips:
        return 2

    segs_dir = WORK / "segs"
    if segs_dir.exists():
        shutil.rmtree(segs_dir)
    segs_dir.mkdir(parents=True)
    seg_paths = []
    for i, c in enumerate(all_clips, 1):
        outp = segs_dir / f"seg_{i:03d}.mp4"
        print(f"encode {i}/{len(all_clips)} {Path(c['video']).name} {c['start']:.1f}-{c['end']:.1f} {c['kind']}", flush=True)
        encode_seg(Path(c["video"]), c["start"], c["end"], outp)
        seg_paths.append(outp)

    title = parse_title_arg(sys.argv) or suggest_title_from_events(results)
    # Hard: never ship a count-led title
    if re.match(r"^\s*\d+\s+", title) or re.search(r"\b\d+\s*frags?\b", title, re.I):
        title = suggest_title_from_events(results)
    if "|" not in title:
        title = f"{title} | PUBG"

    folder = sanitize_title(title)
    out_dir = OUT / folder
    out_dir.mkdir(parents=True, exist_ok=True)
    final_mp4 = out_dir / f"{folder}.mp4"
    concat_segs(seg_paths, final_mp4)

    ai_base = resolve_ai_base(sys.argv)
    build_thumbs(ai_base, out_dir, title)

    dur = ffprobe_duration(final_mp4)
    write_description(out_dir / f"{folder}.txt", title, counts, len(all_clips), dur, winner)
    meta = {
        "template": "MH1",
        "aspect": "16:9",
        "resolution": "1920x1080",
        "title": title,
        "folder": folder,
        "video": final_mp4.name,
        "description": f"{folder}.txt",
        "thumbnail": f"{folder}.jpg",
        "thumbnail_youtube": f"{folder} YT.jpg",
        "ai_thumb_base": str(ai_base) if ai_base else None,
        "counts": counts,
        "clips": len(all_clips),
        "duration_seconds": round(dur, 2),
        "max_seconds": MAX_TOTAL,
        "winner_confirmed": winner,
        "sources": [Path(r["video"]).name for r in results],
    }
    (out_dir / f"{folder}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("DONE", out_dir, flush=True)
    print(json.dumps(meta, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
