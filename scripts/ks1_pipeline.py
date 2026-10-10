#!/usr/bin/env python3
"""KS1 kill-shot pipeline: detect my knocks/kills, skip spectator, assemble short."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat

ROOT = Path("V:/highlight")
SOURCE_DIR = Path("V:/PUBG")
WORK = ROOT / "work" / "today" / "ks1"
OUT = ROOT / "out"

# 1920x1080 regions
REGIONS = {
    "killfeed": (1450, 180, 1910, 520),
    "banner": (620, 700, 1300, 920),  # YOU KNOCKED / YOU KILLED
    "health": (760, 985, 1160, 1070),
    "tr_stats": (1500, 15, 1910, 110),
    "replay_bar": (500, 1005, 1700, 1075),
    "team": (10, 860, 430, 1045),
}

PAD_BEFORE = 4.0
PAD_AFTER = 3.0
MERGE_GAP = 3.0
MIN_CLIP = 5.0
MAX_CLIP = 30.0
SAMPLE_FPS = 1
FEED_DIFF_THRESH = 18.0
BANNER_BRIGHT_THRESH = 55.0
BANNER_DELTA_THRESH = 12.0
HEALTH_WHITE_MIN = 0.04  # fraction of near-white pixels => player HUD present
REPLAY_GRAY_THRESH = 0.34
AUDIO_PEAK_DBFS = -26.0


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(str(c) for c in cmd[:12]), "..." if len(cmd) > 12 else "")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def ffprobe_duration(path: Path) -> float:
    r = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ]
    )
    return float(r.stdout.strip())


def mean_abs_diff(a: Image.Image, b: Image.Image) -> float:
    a = a.convert("L")
    b = b.convert("L")
    if a.size != b.size:
        b = b.resize(a.size)
    d = ImageChops.difference(a, b)
    return float(ImageStat.Stat(d).mean[0])


def bright_ratio(im: Image.Image, thresh: int = 200) -> float:
    g = im.convert("L")
    hist = g.histogram()
    bright = sum(hist[thresh:])
    return bright / max(1, sum(hist))


def gray_ratio(im: Image.Image, lo: int = 140, hi: int = 210) -> float:
    g = im.convert("L")
    hist = g.histogram()
    mid = sum(hist[lo : hi + 1])
    return mid / max(1, sum(hist))


def extract_region_strip(video: Path, region: str, out_dir: Path, fps: int = SAMPLE_FPS) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    x1, y1, x2, y2 = REGIONS[region]
    w, h = x2 - x1, y2 - y1
    pattern = str(out_dir / "f_%06d.jpg")
    # Prefer CUDA decode; fall back to CPU if needed
    cmd_cuda = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-hwaccel",
        "cuda",
        "-i",
        str(video),
        "-vf",
        f"fps={fps},crop={w}:{h}:{x1}:{y1}",
        "-q:v",
        "5",
        "-y",
        pattern,
    ]
    r = run(cmd_cuda, check=False)
    if r.returncode != 0 or not any(out_dir.glob("f_*.jpg")):
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            f"fps={fps},crop={w}:{h}:{x1}:{y1}",
            "-q:v",
            "5",
            "-threads",
            "2",
            "-y",
            pattern,
        ]
        run(cmd)
    return out_dir


def audio_peak_times(video: Path, work: Path) -> list[float]:
    """Use ebur128 momentary peaks; return seconds above threshold."""
    ebur = work / "ebur.txt"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(video),
        "-af",
        "ebur128=peak=true",
        "-f",
        "null",
        "-",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    ebur.write_text(r.stderr, encoding="utf-8", errors="replace")
    times: list[float] = []
    # M: momentary loudness
    for line in r.stderr.splitlines():
        if "M:" not in line or "t:" not in line:
            continue
        try:
            # ... t: 12.3 ... M: -18.2
            tm = re.search(r"t:\s*([0-9.]+)", line)
            mm = re.search(r"M:\s*([+-]?[0-9.]+)", line)
            if not tm or not mm:
                continue
            t = float(tm.group(1))
            m = float(mm.group(1))
            if m >= AUDIO_PEAK_DBFS:
                times.append(t)
        except ValueError:
            continue
    # cluster to ~0.5s
    times.sort()
    clustered: list[float] = []
    for t in times:
        if not clustered or t - clustered[-1] >= 0.5:
            clustered.append(t)
    return clustered


@dataclass
class Event:
    t: float
    kind: str
    score: float
    source: str


def analyze_video(video: Path, vwork: Path) -> dict:
    vwork.mkdir(parents=True, exist_ok=True)
    duration = ffprobe_duration(video)
    cache = vwork / "detection.json"
    if cache.exists():
        print(f"reuse cache {cache}")
        return json.loads(cache.read_text(encoding="utf-8"))

    feed_dir = extract_region_strip(video, "killfeed", vwork / "feed")
    banner_dir = extract_region_strip(video, "banner", vwork / "banner")
    health_dir = extract_region_strip(video, "health", vwork / "health")
    replay_dir = extract_region_strip(video, "replay_bar", vwork / "replay")

    feed_files = sorted(feed_dir.glob("f_*.jpg"))
    banner_files = sorted(banner_dir.glob("f_*.jpg"))
    health_files = sorted(health_dir.glob("f_*.jpg"))
    replay_files = sorted(replay_dir.glob("f_*.jpg"))

    n = min(len(feed_files), len(banner_files), len(health_files), len(replay_files))
    spectator_ranges: list[list[float]] = []
    replay_ranges: list[list[float]] = []
    events: list[Event] = []

    prev_feed = None
    prev_banner_bright = None
    spec_run_start = None
    replay_run_start = None

    for i in range(n):
        t = i / SAMPLE_FPS
        feed = Image.open(feed_files[i])
        banner = Image.open(banner_files[i])
        health = Image.open(health_files[i])
        replay = Image.open(replay_files[i])

        hw = bright_ratio(health, 210)
        is_player = hw >= HEALTH_WHITE_MIN
        # heuristic: very dark/empty health region + game running => likely spectator/death
        is_spectator = not is_player

        rg = gray_ratio(replay, 130, 210)
        replay_open = rg >= REPLAY_GRAY_THRESH

        if is_spectator:
            if spec_run_start is None:
                spec_run_start = t
        else:
            if spec_run_start is not None:
                spectator_ranges.append([spec_run_start, t])
                spec_run_start = None

        if replay_open:
            if replay_run_start is None:
                replay_run_start = max(0.0, t - 0.3)
        else:
            if replay_run_start is not None:
                replay_ranges.append([replay_run_start, t + 0.3])
                replay_run_start = None

        if prev_feed is not None:
            diff = mean_abs_diff(prev_feed, feed)
            if diff >= FEED_DIFF_THRESH and is_player and not replay_open:
                events.append(Event(t=t, kind="kill_feed_change", score=diff, source="feed"))
        prev_feed = feed

        bbright = bright_ratio(banner, 190) * 100
        if prev_banner_bright is not None:
            delta = bbright - prev_banner_bright
            if (
                bbright >= BANNER_BRIGHT_THRESH
                and delta >= BANNER_DELTA_THRESH
                and is_player
                and not replay_open
            ):
                events.append(Event(t=t, kind="knock_or_kill_banner", score=bbright, source="banner"))
        prev_banner_bright = bbright

    if spec_run_start is not None:
        spectator_ranges.append([spec_run_start, n / SAMPLE_FPS])
    if replay_run_start is not None:
        replay_ranges.append([replay_run_start, n / SAMPLE_FPS])

    audio_peaks = audio_peak_times(video, vwork)

    def in_ranges(t: float, ranges: list[list[float]]) -> bool:
        for a, b in ranges:
            if a <= t <= b:
                return True
        return False

    # Keep banner events always; keep feed changes near audio peaks (my fights)
    kept: list[Event] = []
    for e in events:
        if in_ranges(e.t, spectator_ranges) or in_ranges(e.t, replay_ranges):
            continue
        if e.kind == "knock_or_kill_banner":
            kept.append(e)
            continue
        # feed change: require nearby audio peak within 2.5s
        if any(abs(e.t - ap) <= 2.5 for ap in audio_peaks):
            kept.append(e)

    # Dedupe within 1.2s keeping highest score
    kept.sort(key=lambda e: e.t)
    deduped: list[Event] = []
    for e in kept:
        if deduped and e.t - deduped[-1].t < 1.2:
            if e.score > deduped[-1].score:
                deduped[-1] = e
        else:
            deduped.append(e)

    result = {
        "video": str(video),
        "duration": duration,
        "events": [asdict(e) for e in deduped],
        "spectator_ranges": spectator_ranges,
        "replay_ranges": replay_ranges,
        "audio_peak_count": len(audio_peaks),
        "raw_event_count": len(events),
    }
    cache.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def events_to_clips(events: list[dict], duration: float) -> list[dict]:
    raw = []
    for e in events:
        start = max(0.0, e["t"] - PAD_BEFORE)
        end = min(duration, e["t"] + PAD_AFTER)
        raw.append({"start": start, "end": end, "anchor": e["t"], "kind": e["kind"]})
    if not raw:
        return []
    raw.sort(key=lambda c: c["start"])
    merged = [raw[0].copy()]
    for c in raw[1:]:
        cur = merged[-1]
        if c["start"] <= cur["end"] + MERGE_GAP:
            cur["end"] = max(cur["end"], c["end"])
            cur["kind"] = cur["kind"] + "+" + c["kind"]
        else:
            merged.append(c.copy())
    # enforce min/max
    out = []
    for c in merged:
        dur = c["end"] - c["start"]
        if dur < MIN_CLIP:
            mid = (c["start"] + c["end"]) / 2
            c["start"] = max(0.0, mid - MIN_CLIP / 2)
            c["end"] = min(duration, c["start"] + MIN_CLIP)
            dur = c["end"] - c["start"]
        if dur > MAX_CLIP:
            # keep window around first anchor
            anchor = c.get("anchor", c["start"] + PAD_BEFORE)
            c["start"] = max(0.0, anchor - PAD_BEFORE)
            c["end"] = min(duration, c["start"] + MAX_CLIP)
        c["dur"] = round(c["end"] - c["start"], 2)
        out.append(c)
    return out


def encode_seg(video: Path, start: float, end: float, out_path: Path) -> None:
    dur = max(0.1, end - start)
    # portrait crop per prefs
    vf = "crop=608:1000:656:40,scale=1080:1920"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-hwaccel",
        "cuda",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(video),
        "-t",
        f"{dur:.3f}",
        "-vf",
        vf,
        "-r",
        "60",
        "-c:v",
        "h264_nvenc",
        "-preset",
        "p5",
        "-rc",
        "vbr",
        "-cq",
        "18",
        "-b:v",
        "12M",
        "-maxrate",
        "16M",
        "-bufsize",
        "24M",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        "-y",
        str(out_path),
    ]
    r = run(cmd, check=False)
    if r.returncode != 0 or not out_path.exists():
        cmd = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(video),
            "-t",
            f"{dur:.3f}",
            "-vf",
            vf,
            "-r",
            "60",
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p5",
            "-cq",
            "18",
            "-b:v",
            "12M",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-y",
            str(out_path),
        ]
        run(cmd)


def concat_segs(segs: list[Path], out_path: Path) -> None:
    lst = out_path.parent / "concat.txt"
    lines = []
    for s in segs:
        p = str(s).replace("'", r"'\''")
        lines.append(f"file '{p}'")
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(lst),
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        "-y",
        str(out_path),
    ]
    run(cmd)


def sanitize_title(title: str) -> str:
    t = title.replace("|", " ")
    t = re.sub(r'[<>:"/\\?*]', " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:120]


def make_thumbnail(video: Path, title: str, out_jpg: Path, out_yt: Path) -> None:
    # grab a mid frame
    tmp = out_jpg.parent / "_thumb_src.jpg"
    dur = ffprobe_duration(video)
    ss = max(0.0, dur * 0.42)
    run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            f"{ss:.2f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-y",
            str(tmp),
        ]
    )
    im = Image.open(tmp).convert("RGB")
    im = ImageEnhance.Contrast(im).enhance(1.25)
    im = ImageEnhance.Color(im).enhance(1.2)
    # vignette-ish
    vign = Image.new("L", im.size, 0)
    d = ImageDraw.Draw(vign)
    d.ellipse((-im.width * 0.1, -im.height * 0.05, im.width * 1.1, im.height * 1.05), fill=255)
    vign = vign.filter(ImageFilter.GaussianBlur(80))
    dark = Image.new("RGB", im.size, (0, 0, 0))
    im = Image.composite(im, dark, vign)

    draw = ImageDraw.Draw(im)
    hook = title.split("|")[0].strip()
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 72)
        font2 = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
        font2 = font

    def stroke_text(xy, text, font, fill, stroke):
        x, y = xy
        for dx, dy in [(-3, 0), (3, 0), (0, -3), (0, 3), (-2, -2), (2, 2), (-2, 2), (2, -2)]:
            draw.text((x + dx, y + dy), text, font=font, fill=stroke)
        draw.text((x, y), text, font=font, fill=fill)

    # wrap hook
    words = hook.split()
    lines = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) > im.width - 80 and cur:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    y = int(im.height * 0.62)
    for line in lines[:3]:
        tw = draw.textlength(line, font=font)
        stroke_text(((im.width - tw) / 2, y), line, font, "#FFEE00", "#000000")
        y += 84
    tw = draw.textlength("PUBG", font=font2)
    stroke_text(((im.width - tw) / 2, y + 10), "PUBG", font2, "#FFFFFF", "#000000")
    im.save(out_jpg, quality=92)
    # YT landscape from same frame center-ish
    yt = im.resize((720, 1280)).crop((0, 200, 720, 920)).resize((1280, 720))
    yt.save(out_yt, quality=92)
    tmp.unlink(missing_ok=True)


def write_description(path: Path, title: str, clips: list[dict], kill_count: int) -> None:
    body = f"""{title}

PUBG kill-shot montage (KS1) — knocks and kills only from today's matches. Spectator segments and Chicken Dinner screens skipped.

Moments:
- {kill_count} knock/kill moments kept
- Hard-cut fight packages from multiple matches
- Player POV only ([ASA] rkanik)

Keywords:
PUBG highlights, PUBG kills, PUBG knock, kill montage, PUBG TPP, battlegrounds highlights, FPS short, gaming reel, multi kill, squad fight

#PUBG #PUBGHighlights #KillMontage #FPS #Gaming #Shorts #Reels #Battlegrounds #PCGaming #Twitch #ActionGames #Gunfight #PUBGPC
"""
    path.write_text(body.strip() + "\n", encoding="utf-8")


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    videos = sorted(SOURCE_DIR.glob("*.mp4"))
    if not videos:
        print("No videos in", SOURCE_DIR)
        return 1

    all_clips = []
    total_events = 0
    for idx, video in enumerate(videos, 1):
        print(f"\n=== [{idx}/{len(videos)}] {video.name} ===")
        vwork = WORK / f"v{idx}_{video.stem.replace(' ', '_')}"
        det = analyze_video(video, vwork)
        clips = events_to_clips(det["events"], det["duration"])
        for c in clips:
            c["video"] = str(video)
            c["video_index"] = idx
        all_clips.extend(clips)
        total_events += len(det["events"])
        print(f"events={len(det['events'])} clips={len(clips)} spectator_ranges={len(det['spectator_ranges'])}")

    summary = {"clips": all_clips, "total_events": total_events, "total_clip_seconds": sum(c["dur"] for c in all_clips)}
    (WORK / "all_clips.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nTOTAL events={total_events} clips={len(all_clips)} seconds={summary['total_clip_seconds']:.1f}")

    if not all_clips:
        print("No kill-shot clips found.")
        return 2

    segs_dir = WORK / "segs"
    if segs_dir.exists():
        shutil.rmtree(segs_dir)
    segs_dir.mkdir(parents=True)
    seg_paths = []
    for i, c in enumerate(all_clips, 1):
        outp = segs_dir / f"seg_{i:03d}.mp4"
        print(f"encode seg {i}/{len(all_clips)} from {Path(c['video']).name} {c['start']:.1f}-{c['end']:.1f}")
        encode_seg(Path(c["video"]), c["start"], c["end"], outp)
        seg_paths.append(outp)

    kill_count = total_events
    title = f"{kill_count} Kill Shots Today | PUBG"
    folder_name = sanitize_title(title)
    out_dir = OUT / folder_name
    out_dir.mkdir(parents=True, exist_ok=True)
    final_mp4 = out_dir / f"{folder_name}.mp4"
    concat_segs(seg_paths, final_mp4)

    make_thumbnail(final_mp4, title, out_dir / f"{folder_name}.jpg", out_dir / f"{folder_name} YT.jpg")
    write_description(out_dir / f"{folder_name}.txt", title, all_clips, kill_count)
    meta = {
        "template": "KS1",
        "title": title,
        "folder": folder_name,
        "video": final_mp4.name,
        "description": f"{folder_name}.txt",
        "thumbnail": f"{folder_name}.jpg",
        "thumbnail_youtube": f"{folder_name} YT.jpg",
        "kill_or_knock_events": kill_count,
        "clips": len(all_clips),
        "duration_seconds": round(ffprobe_duration(final_mp4), 2),
        "sources": [v.name for v in videos],
    }
    (out_dir / f"{folder_name}.meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print("\nDONE ->", out_dir)
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
