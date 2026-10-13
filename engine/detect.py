"""Unified detect — full-timeline kill-feed + tip OCR for any VOD length."""

from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from pathlib import Path

from PIL import Image, ImageChops, ImageStat

from engine import progress
from engine.classify import (
    ASSIST_RE,
    TIP_VICTIM,
    classify_feed_line,
    classify_tip_line,
    counter_events_from_ocr,
    dedupe_events,
    filter_include,
    parse_kill_counter,
    tip_kill_count_events,
)
from engine.config import AppConfig
from engine.ffmpeg_util import (
    extract_dual_region_windows,
    extract_region,
    extract_region_windows,
    frames_covering,
    probe_duration,
    probe_size,
)
from engine.ocr import get_backend_name, get_ocr_ep, ocr_crop


def _slug(stem: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", stem)


def _frame_t(path: Path) -> int | None:
    try:
        return int(path.stem.split("_")[1]) - 1
    except (IndexError, ValueError):
        return None


def _textiness(im: Image.Image) -> float:
    g = im.convert("L")
    hist = g.histogram()
    bright = sum(hist[200:]) / max(1, sum(hist))
    return bright * 100.0


def feed_candidates(feed_dir: Path, thresh: float) -> list[tuple[float, int]]:
    files = sorted(feed_dir.glob("f_*.jpg"))
    scored: list[tuple[float, int]] = []
    prev = None
    prev_t = None
    for fp in files:
        t = _frame_t(fp)
        if t is None:
            continue
        im = Image.open(fp)
        gray = im.convert("L")
        text_boost = _textiness(im)
        diff = 0.0
        if prev is not None and prev_t is not None and abs(t - prev_t) <= 2:
            im_r = gray.resize(prev.size) if prev.size != gray.size else gray
            diff = float(ImageStat.Stat(ImageChops.difference(prev, im_r)).mean[0])
        score = diff + 0.35 * text_boost
        if diff >= thresh or text_boost >= 8.0:
            scored.append((score, t))
        prev = gray
        prev_t = t
    scored.sort(reverse=True)
    return scored


def tip_flash_candidates(
    tip_dir: Path,
    t0: float,
    t1: float,
) -> list[tuple[float, int]]:
    """White-flash peaks for YOU KNOCKED / YOU KILLED tips (KS1 banner scorer)."""
    scored: list[tuple[float, int]] = []
    prev: float | None = None
    for fp in sorted(tip_dir.glob("f_*.jpg")):
        t = _frame_t(fp)
        if t is None or not (t0 <= t <= t1):
            continue
        try:
            im = Image.open(fp).convert("L")
        except OSError:
            continue
        w, h = im.size
        full = sum(im.histogram()[200:]) / max(1, w * h) * 100
        if full > 12:
            prev = None
            continue
        mid = im.crop((int(w * 0.05), int(h * 0.18), int(w * 0.95), int(h * 0.55)))
        white = sum(mid.histogram()[205:]) / max(1, mid.size[0] * mid.size[1]) * 100
        # Peak tip frames often sit ~4.5–6.0 mid-white (YOU KNOCKED flash)
        if not (0.45 <= white <= 6.5):
            prev = white
            continue
        delta = white - (prev if prev is not None else 0.0)
        prev = white
        if delta >= 0.10:
            scored.append((delta + white * 0.35, t))
    scored.sort(reverse=True)
    return scored


def pick_ocr_targets(
    scored: list[tuple[float, int]],
    max_ocr: int,
    bucket_seconds: float,
    per_bucket: int,
) -> list[int]:
    """Spread OCR across the whole scan — never fill early buckets first then stop.

    Old behavior walked buckets in time order and returned as soon as max_ocr filled,
    so long VODs only OCR'd the first ~N minutes (tip ceiling 200 ⇒ ~50 min cutoff).
    """
    if not scored or max_ocr <= 0:
        return []
    buckets: dict[int, list[tuple[float, int]]] = defaultdict(list)
    bsize = max(30.0, bucket_seconds)
    for score, t in scored:
        buckets[int(t // bsize)].append((score, t))
    for b in buckets:
        buckets[b].sort(reverse=True)

    picked: list[int] = []
    seen: set[int] = set()
    # Round-robin: one slot per bucket per pass, up to per_bucket each, until budget.
    idxs = {b: 0 for b in buckets}
    taken_in_bucket = {b: 0 for b in buckets}
    while len(picked) < max_ocr:
        progressed = False
        for b in sorted(buckets):
            if taken_in_bucket[b] >= max(1, per_bucket):
                continue
            slot = buckets[b]
            i = idxs[b]
            while i < len(slot):
                _, t = slot[i]
                i += 1
                if t in seen:
                    continue
                seen.add(t)
                picked.append(t)
                taken_in_bucket[b] += 1
                progressed = True
                break
            idxs[b] = i
            if len(picked) >= max_ocr:
                break
        if not progressed:
            break
    # Leftover budget: highest remaining scores anywhere (still capped).
    if len(picked) < max_ocr:
        for _, t in sorted(scored, reverse=True):
            if t in seen:
                continue
            seen.add(t)
            picked.append(t)
            if len(picked) >= max_ocr:
                break
    return sorted(picked)


def merge_ocr_times(*groups: list[int], max_ocr: int) -> list[int]:
    """Merge candidate time lists with round-robin so late-VOD times are not sliced off.

    `sorted(union)[:max_ocr]` kept the earliest N seconds only — on a 2.5h VOD with
    budget 900 that cut everything after ~7130s even when change-pick had late hits.
    """
    if max_ocr <= 0:
        return []
    queues = [sorted(set(g)) for g in groups if g]
    if not queues:
        return []
    picked: list[int] = []
    seen: set[int] = set()
    idxs = [0] * len(queues)
    while len(picked) < max_ocr:
        progressed = False
        for qi, q in enumerate(queues):
            i = idxs[qi]
            while i < len(q):
                t = q[i]
                i += 1
                if t in seen:
                    continue
                seen.add(t)
                picked.append(t)
                progressed = True
                break
            idxs[qi] = i
            if len(picked) >= max_ocr:
                break
        if not progressed:
            break
    return sorted(picked)


def ocr_budget(duration: float, cfg: AppConfig) -> int:
    base = int(cfg.safety.get("max_ocr_per_video") or 40)
    per_min = float(cfg.detection.get("ocr_per_minute") or 4.0)
    ceiling = int(cfg.detection.get("max_ocr_ceiling") or 900)
    scaled = int(max(base, (duration / 60.0) * per_min))
    return max(base, min(ceiling, scaled))


def tip_ocr_budget(scan_span: float, cfg: AppConfig) -> int:
    """Separate tip budget so YOU flashes are not starved by feed OCR.

    Ceiling must scale with VOD length — a flat 200 on a 165 min tape only covered
    the first ~50 min when picks filled early buckets first.
    """
    per_min = float(cfg.detection.get("tip_ocr_per_minute") or 8.0)
    floor = int(cfg.detection.get("tip_ocr_floor") or 40)
    ceiling = int(cfg.detection.get("tip_ocr_ceiling") or 900)
    scaled = int((scan_span / 60.0) * per_min)
    return max(floor, min(ceiling, scaled))


def _load_ocr_json(path: Path) -> dict[int, str]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    out: dict[int, str] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                out[int(k)] = str(v or "")
            except ValueError:
                continue
    return out


def _legacy_ks1_dirs(work: Path, video: Path) -> list[Path]:
    slug = _slug(video.stem)
    roots = [
        work.parents[2] / "today" / "ks1",
        Path("work/today/ks1"),
    ]
    out: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for folder in root.glob(f"*{slug}*"):
            if folder.is_dir():
                out.append(folder)
    return out


def _merge_legacy_ocr(work: Path, video: Path, kind: str) -> dict[int, str]:
    """Reuse prior KS1/KS1A sparse OCR for the same VOD stem when present."""
    merged: dict[int, str] = {}
    names = {
        "feed": ["feed_ocr_sparse.json", "feed_ocr.json"],
        "tip": [
            "banner_ocr_sparse.json",
            "banner_ocr_rescan.json",
            "banner_ocr_rescan2.json",
            "banner_ocr_window.json",
            "banner_ocr.json",
        ],
    }
    for folder in _legacy_ks1_dirs(work, video):
        for name in names.get(kind, []):
            merged.update(
                {k: v for k, v in _load_ocr_json(folder / name).items() if (v or "").strip()}
            )
    return merged


def _merge_legacy_counter_events(work: Path, video: Path) -> list[dict]:
    events: list[dict] = []
    for folder in _legacy_ks1_dirs(work, video):
        path = folder / "counter_events.json"
        if not path.exists():
            continue
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(raw, dict):
            for key in ("kills", "assists"):
                for e in raw.get(key) or []:
                    if isinstance(e, dict) and "t" in e and "kind" in e:
                        events.append(dict(e))
        elif isinstance(raw, list):
            events.extend([e for e in raw if isinstance(e, dict) and "t" in e])
    return events


def _ocr_list(
    frames: list[int],
    frame_dir: Path,
    ocr_map: dict[int, str],
    *,
    pause: float,
    label: str,
    max_n: int,
    redo_blank: bool = False,
) -> None:
    todo: list[int] = []
    for t in frames:
        if t not in ocr_map:
            todo.append(t)
        elif redo_blank and not (ocr_map.get(t) or "").strip():
            todo.append(t)
        if len(todo) >= max_n:
            break
    if not todo:
        return
    progress.stage("ocr", pct=0, message=f"{label}: {len(todo)} frames")
    for i, t in enumerate(todo, 1):
        fp = frame_dir / f"f_{t + 1:06d}.jpg"
        if not fp.exists():
            continue
        region = "tip" if "tip" in label.lower() else "feed"
        ocr_map[t] = ocr_crop(fp, region=region)
        progress.stage("ocr", pct=100.0 * i / max(1, len(todo)), message=f"{label} t={t}")
        time.sleep(pause)


def _extract_scan(
    video: Path,
    out_dir: Path,
    box: tuple[int, int, int, int],
    cfg: AppConfig,
    *,
    t0: float,
    t1: float,
    duration: float,
    fps: float,
    label: str,
) -> None:
    """Full-timeline extract, or windowed extract when scan trim is set."""
    span = max(1.0, t1 - t0)
    windowed = t0 > 0.5 or t1 < duration - 0.5
    if windowed:
        need = max(1, int(span * fps * 0.85))
        have = frames_covering(out_dir, t0, t1)
        if cfg.detection.get("reuse_cache", True) and have >= need:
            progress.stage(
                "extract",
                pct=100.0,
                message=f"reusing {have} {label} frames ({t0:.0f}–{t1:.0f}s)",
            )
            return
        progress.stage(
            "extract",
            message=f"{label} window extract {t0:.0f}–{t1:.0f}s @ {fps}fps",
        )
        extract_region_windows(
            video, out_dir, box, cfg, [(t0, t1)], fps=fps, label_prefix=label
        )
        return
    progress.stage("extract", message=f"{label} extract {duration:.0f}s @ {fps}fps")
    extract_region(video, out_dir, box, cfg, fps=fps, expected_duration=duration)


def detect_video(
    video: Path,
    idx: int,
    cfg: AppConfig,
    window: tuple[float, float] | None = None,
) -> dict:
    work = cfg.work_dir / cfg.game_id / cfg.template_id / f"v{idx}_{_slug(video.stem)}"
    work.mkdir(parents=True, exist_ok=True)
    progress.stage("detect", message=f"[{idx}] {video.name}")

    duration = probe_duration(video)
    width, height = probe_size(video)
    t0, t1 = window if window is not None else cfg.scan_window(duration)
    scan_span = max(1.0, t1 - t0)
    me = cfg.username
    if not me:
        raise ValueError("track.username required - cannot tell kill vs death without player name")

    team = [me]
    stems = cfg.name_stems
    clans = cfg.clan_tags
    include = cfg.include
    mode = cfg.detection_mode
    max_ocr = ocr_budget(scan_span, cfg)
    pause = float(cfg.safety.get("ocr_pause_seconds") or 0.12)
    # RapidOCR is local ONNX — no thermal need for long Windows.OCR pauses
    if get_backend_name() == "rapidocr":
        pause = float(cfg.safety.get("ocr_pause_rapidocr_seconds") or 0.0)
    fps = float(cfg.detection.get("sample_fps") or 1.0)
    thresh = float(cfg.detection.get("feed_diff_threshold") or 14.0)
    bucket_sec = float(cfg.detection.get("ocr_bucket_seconds") or 60.0)
    per_bucket = int(cfg.detection.get("ocr_per_bucket") or 3)
    me_stride = int(cfg.detection.get("me_hunt_stride_seconds") or 6)

    progress.stage(
        "detect",
        message=f"ocr={get_backend_name()} ep={get_ocr_ep()}",
    )
    if t0 > 0.5 or t1 < duration - 0.5:
        progress.stage(
            "detect",
            message=f"scan window {t0:.0f}–{t1:.0f}s (of {duration:.0f}s)",
        )

    events: list[dict] = []
    regions = set(cfg.template_regions())

    tip_dir = work / "tip"
    feed_dir = work / "feed"
    tip_ocr: dict[int, str] = {}
    tip_budget = tip_ocr_budget(scan_span, cfg)
    tip_enabled = bool((cfg.regions.get("tip") or {}).get("enabled", False)) or "tip" in regions
    need_frames = max(1, int(scan_span * fps * 0.85))
    reuse = bool(cfg.detection.get("reuse_cache", True))

    legacy_banner = None
    if tip_enabled and mode in ("feed_ocr", "hybrid"):
        for folder in _legacy_ks1_dirs(work, video):
            b = folder / "banner"
            if b.is_dir() and frames_covering(b, t0, t1) >= need_frames * 0.4:
                legacy_banner = b
                break

    tip_ready = False
    if tip_enabled and mode in ("feed_ocr", "hybrid"):
        if legacy_banner is not None:
            tip_dir = legacy_banner
            tip_ready = True
            progress.stage("extract", message=f"reusing legacy tip/banner ({tip_dir})")
        elif reuse and frames_covering(tip_dir, t0, t1) >= need_frames:
            tip_ready = True
            progress.stage(
                "extract",
                message=f"reusing tip frames ({t0:.0f}–{t1:.0f}s)",
            )

    feed_needed = "kill_feed" in regions and mode in ("feed_ocr", "hybrid", "audio_assist")
    feed_ready = False
    if feed_needed and mode != "audio_assist":
        if reuse and frames_covering(feed_dir, t0, t1) >= need_frames:
            feed_ready = True
            progress.stage(
                "extract",
                message=f"reusing feed frames ({t0:.0f}–{t1:.0f}s)",
            )

    # One decode for tip+feed when both missing (was: extract 0→100% twice)
    need_tip_x = tip_enabled and mode in ("feed_ocr", "hybrid") and not tip_ready
    need_feed_x = feed_needed and mode != "audio_assist" and not feed_ready
    tip_box = cfg.absolute_box("tip", width, height) if tip_enabled else None
    feed_box = cfg.absolute_box("kill_feed", width, height) if feed_needed else None
    windowed = t0 > 0.5 or t1 < duration - 0.5

    if need_tip_x and need_feed_x and tip_box and feed_box and windowed:
        progress.stage(
            "extract",
            message=f"tip+feed one-pass {t0:.0f}–{t1:.0f}s @ {fps}fps",
        )
        extract_dual_region_windows(
            video, tip_dir, tip_box, feed_dir, feed_box, cfg, [(t0, t1)], fps=fps
        )
        tip_ready = feed_ready = True
    else:
        if need_tip_x and tip_box:
            _extract_scan(
                video, tip_dir, tip_box, cfg,
                t0=t0, t1=t1, duration=duration, fps=fps, label="tip",
            )
            tip_ready = True
        if need_feed_x and feed_box:
            _extract_scan(
                video, feed_dir, feed_box, cfg,
                t0=t0, t1=t1, duration=duration, fps=fps, label="feed",
            )
            feed_ready = True

    # --- tip OCR / classify ---
    if tip_enabled and mode in ("feed_ocr", "hybrid"):
        tip_cache = work / "tip_ocr.json"
        tip_ocr = _load_ocr_json(tip_cache) if reuse else {}
        tip_ocr.update(_merge_legacy_ocr(work, video, "tip"))

        if tip_dir.is_dir() and any(tip_dir.glob("f_*.jpg")):
            flash = tip_flash_candidates(tip_dir, t0, t1)
            flash_pick = pick_ocr_targets(flash, tip_budget, 30.0, 3)
            diff_scored = [
                (s, t)
                for s, t in feed_candidates(tip_dir, max(10.0, thresh - 2))
                if t0 <= t <= t1
            ]
            diff_pick = pick_ocr_targets(diff_scored, max(8, tip_budget // 4), 45.0, 1)
            tip_targets: list[int] = []
            seen_t: set[int] = set()
            for t in list(flash_pick) + list(diff_pick):
                if t in seen_t:
                    continue
                seen_t.add(t)
                tip_targets.append(t)
                if len(tip_targets) >= tip_budget:
                    break
            # Skip blank redo on cache hits — re-OCR empty tips every run wasted ~1s/frame
            progress.stage(
                "ocr",
                message=f"tip OCR {len(tip_targets)}/{tip_budget} "
                f"(flash={len(flash_pick)} diff={len(diff_pick)})",
            )
            _ocr_list(
                tip_targets,
                tip_dir,
                tip_ocr,
                pause=pause,
                label="tip OCR",
                max_n=tip_budget,
                redo_blank=False,
            )
            # Assist / finish banners often land a few seconds after knock-you or squad kill.
            # Per parent tip (not global RR) so late-VOD assists are not starved.
            tip_expand: list[int] = []
            seen_exp: set[int] = set()
            for t, text in list(tip_ocr.items()):
                if not (t0 <= t <= t1) or not (text or "").strip():
                    continue
                if not (
                    TIP_VICTIM.search(text)
                    or ASSIST_RE.search(text)
                    or re.search(r"kill|kn[\d@o0]?ck|knoc|assi", text, re.I)
                ):
                    continue
                kind_here = classify_tip_line(text)
                # Prefer +4,+5 (assist chip); unclassified also try +1..+3.
                prefer = [int(t) + 4, int(t) + 5, int(t) + 3, int(t) + 6]
                if not kind_here:
                    prefer = [int(t) + 5, int(t) + 4, int(t) + 1, int(t) + 2, int(t) + 3, int(t) + 6]
                added = 0
                for n in prefer:
                    if added >= 2:
                        break
                    if not (t0 <= n <= t1) or n in seen_exp:
                        continue
                    if n in tip_ocr and (tip_ocr.get(n) or "").strip():
                        continue
                    if (tip_dir / f"f_{n + 1:06d}.jpg").exists():
                        seen_exp.add(n)
                        tip_expand.append(n)
                        added += 1
            tip_expand = sorted(tip_expand)
            if tip_expand:
                progress.stage(
                    "ocr",
                    message=f"tip neighbor OCR {len(tip_expand)} (assist/follow-up)",
                )
                _ocr_list(
                    tip_expand,
                    tip_dir,
                    tip_ocr,
                    pause=pause,
                    label="tip neighbor OCR",
                    max_n=len(tip_expand),
                    # Empty cache slots must redo — neighbors often reserved as "" earlier.
                    redo_blank=True,
                )
            tip_cache.write_text(
                json.dumps({str(k): v for k, v in tip_ocr.items()}, indent=2), encoding="utf-8"
            )
        for t, text in tip_ocr.items():
            if not (t0 <= t <= t1):
                continue
            kind = classify_tip_line(text)
            if kind:
                events.append({"t": float(t), "kind": kind, "text": text, "source": "tip"})
        events.extend(
            e for e in tip_kill_count_events(tip_ocr) if t0 <= float(e["t"]) <= t1
        )

    # Proven kill-counter timeline from prior KS1 runs on same VOD
    for e in _merge_legacy_counter_events(work, video):
        ee = dict(e)
        ee["t"] = float(e["t"]) + 1.5
        if t0 <= ee["t"] <= t1:
            events.append(ee)

    # --- kill feed OCR / classify ---
    feed_ocr_map: dict[int, str] = {}
    if feed_needed:
        box = feed_box or cfg.absolute_box("kill_feed", width, height)
        cache_path = work / "feed_ocr.json"
        if reuse and cache_path.exists():
            feed_ocr_map = _load_ocr_json(cache_path)
        feed_ocr_map.update(_merge_legacy_ocr(work, video, "feed"))
        feed_ocr_map = {k: v for k, v in feed_ocr_map.items() if (v or "").strip()}

        if mode != "audio_assist":
            # extract already done above when needed
            if not feed_ready and feed_box:
                _extract_scan(
                    video, feed_dir, feed_box, cfg,
                    t0=t0, t1=t1, duration=duration, fps=fps, label="feed",
                )
            n_frames = frames_covering(feed_dir, t0, t1)
            progress.stage(
                "extract",
                message=f"feed frames in window={n_frames} / ~{int(scan_span * fps)}",
            )

            scored = [
                (s, t) for s, t in feed_candidates(feed_dir, thresh) if t0 <= t <= t1
            ]
            if not scored:
                step = max(5, int(bucket_sec / max(1, per_bucket)))
                scored = [(1.0, t) for t in range(int(t0), int(t1) + 1, step)]

            # Drop OCR for missing frames
            feed_ocr_map = {
                t: txt
                for t, txt in feed_ocr_map.items()
                if (feed_dir / f"f_{t + 1:06d}.jpg").exists()
            }

            # 1) Me-hunt: even stride across scan window
            hunt = [
                t
                for t in range(int(t0), int(t1) + 1, max(3, me_stride))
                if (feed_dir / f"f_{t + 1:06d}.jpg").exists()
            ]
            hunt_scored: list[tuple[float, int]] = []
            for t in hunt:
                fp = feed_dir / f"f_{t + 1:06d}.jpg"
                try:
                    hunt_scored.append((_textiness(Image.open(fp)), t))
                except OSError:
                    continue
            hunt_scored.sort(reverse=True)
            hunt_pick = [t for _, t in hunt_scored[: max(80, max_ocr // 2)]]

            # 2) Change buckets
            change_pick = pick_ocr_targets(scored, max_ocr, bucket_sec, per_bucket)

            expand: list[int] = []
            for t, text in list(feed_ocr_map.items()):
                if not (t0 <= t <= t1):
                    continue
                if classify_feed_line(text, me, team, stems, clans):
                    for n in range(t - 2, t + 4):
                        if t0 <= n <= t1 and (feed_dir / f"f_{n + 1:06d}.jpg").exists():
                            expand.append(n)
            to_ocr = [
                t
                for t in merge_ocr_times(change_pick, hunt_pick, expand, max_ocr=max_ocr)
                if t0 <= t <= t1
            ]

            progress.stage(
                "ocr",
                pct=0,
                message=f"feed OCR {len(to_ocr)}/{max_ocr} budget ({t0:.0f}–{t1:.0f}s)",
            )
            for i, t in enumerate(to_ocr, 1):
                if t in feed_ocr_map:
                    continue
                fp = feed_dir / f"f_{t + 1:06d}.jpg"
                if not fp.exists():
                    continue
                text = ocr_crop(fp, region="feed")
                feed_ocr_map[t] = text
                progress.stage("ocr", pct=100.0 * i / max(1, len(to_ocr)), message=f"t={t}")
                if classify_feed_line(text, me, team, stems, clans):
                    for n in (t - 1, t + 1, t + 2):
                        if not (t0 <= n <= t1):
                            continue
                        nfp = feed_dir / f"f_{n + 1:06d}.jpg"
                        if nfp.exists() and n not in feed_ocr_map:
                            feed_ocr_map[n] = ocr_crop(nfp, region="feed")
                            time.sleep(pause)
                time.sleep(pause)

            cache_path.write_text(
                json.dumps({str(k): v for k, v in feed_ocr_map.items()}, indent=2),
                encoding="utf-8",
            )

            for t, text in feed_ocr_map.items():
                if not (t0 <= t <= t1):
                    continue
                kind = classify_feed_line(text, me, team, stems, clans)
                if kind:
                    events.append(
                        {"t": float(t), "kind": kind, "text": text, "source": "kill_feed"}
                    )

            events.extend(
                e for e in counter_events_from_ocr(feed_ocr_map) if t0 <= float(e["t"]) <= t1
            )

    events = dedupe_events(events, gap=2.0)
    # Drop early kill-counter OCR noise (plane/map "0 KILLED" misreads) unless tip/feed nearby
    anchored_ts = [
        e["t"]
        for e in events
        if e.get("source") in ("tip", "kill_feed", "tip_kill_count")
    ]
    filtered: list[dict] = []
    for e in events:
        if not (t0 <= e["t"] <= t1):
            continue
        if e.get("source") == "kill_counter":
            near = any(abs(e["t"] - a) <= 30 for a in anchored_ts)
            # First ~35 min often has false counter ticks while on map/plane
            if e["t"] < 2100 and not near:
                zeroish = any(
                    (n := parse_kill_counter(feed_ocr_map.get(int(e["t"]) + dt, ""))) == 0
                    for dt in range(-2, 4)
                )
                if zeroish or not near:
                    continue
        filtered.append(e)
    events = filtered
    events = filter_include(events, include)

    result = {
        "video": str(video.resolve()),
        "index": idx,
        "duration": duration,
        "scan_start": t0,
        "scan_end": t1,
        "width": width,
        "height": height,
        "events": events,
        "counts": {
            "my_knock": sum(1 for e in events if e["kind"] == "my_knock"),
            "my_kill": sum(1 for e in events if e["kind"] == "my_kill"),
            "my_assist": sum(1 for e in events if e["kind"] == "my_assist"),
        },
    }
    (work / "events.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    progress.stage(
        "detect",
        message=f"events={len(events)} knocks={result['counts']['my_knock']} "
        f"kills={result['counts']['my_kill']} assists={result['counts']['my_assist']}",
    )
    return result


def _merge_window_results(parts: list[dict], cfg: AppConfig, video: Path, idx: int) -> dict:
    """Union events from several scan windows of one VOD into one detect result."""
    events = dedupe_events(
        sorted((e for p in parts for e in p["events"]), key=lambda e: e["t"]),
        gap=2.0,
    )
    result = dict(parts[0])
    result.update(
        {
            "scan_start": min(p["scan_start"] for p in parts),
            "scan_end": max(p["scan_end"] for p in parts),
            "scan_ranges": [[p["scan_start"], p["scan_end"]] for p in parts],
            "events": events,
            "counts": {
                k: sum(1 for e in events if e["kind"] == k)
                for k in ("my_knock", "my_kill", "my_assist")
            },
        }
    )
    work = cfg.work_dir / cfg.game_id / cfg.template_id / f"v{idx}_{_slug(video.stem)}"
    (work / "events.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def detect_all(cfg: AppConfig) -> list[dict]:
    sources = [Path(s) for s in cfg.sources]
    if not sources:
        raise ValueError("No sources in job - drop video files first")
    results = []
    for i, video in enumerate(sources, 1):
        if not video.exists():
            progress.error(f"missing source: {video}")
            continue
        progress.stage("detect", pct=100.0 * (i - 1) / len(sources), message=video.name)
        windows = cfg.scan_ranges(video, probe_duration(video))
        if len(windows) == 1:
            results.append(detect_video(video, i, cfg, window=windows[0]))
            continue
        parts = []
        for w_i, win in enumerate(windows, 1):
            progress.stage(
                "detect",
                message=f"[{i}] range {w_i}/{len(windows)} {win[0]:.0f}-{win[1]:.0f}s",
            )
            parts.append(detect_video(video, i, cfg, window=win))
        results.append(_merge_window_results(parts, cfg, video, i))
    out = cfg.work_dir / cfg.game_id / cfg.template_id / "detection.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    progress.artifact(str(out), kind="detection")
    return results
