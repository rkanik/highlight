"""ffmpeg/ffprobe helpers — respect HIGHLIGHT_FFMPEG env and device config."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

from engine import progress
from engine.config import AppConfig, ffmpeg_bin, ffprobe_bin


def _hms(seconds: float) -> str:
    s = int(max(0, seconds))
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    preview = " ".join(map(str, cmd[:16]))
    if len(cmd) > 16:
        preview += " ..."
    progress.stage("log", message=f"+ {preview}")
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def run_ffmpeg_progress(
    cmd: list[str],
    *,
    duration: float | None = None,
    stage: str = "ffmpeg",
    label: str = "ffmpeg",
    out_dir: Path | None = None,  # reserved: optional frame-count probes
) -> subprocess.CompletedProcess:
    """
    Run ffmpeg with -progress pipe:1 and stream % / time into JSONL for the UI.
    Long silent extracts (multi-hour feed) otherwise look hung.
    """
    _ = out_dir
    # Inject progress reporting after binary / before other args if missing
    if "-progress" not in cmd:
        cmd = [cmd[0], "-progress", "pipe:1", "-nostats", *cmd[1:]]

    preview = " ".join(map(str, cmd[:14]))
    if len(cmd) > 14:
        preview += " ..."
    progress.stage(stage, pct=0, message=f"{label}: starting")
    progress.stage("log", message=f"+ {preview}")

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    assert proc.stderr is not None

    last_emit = 0.0
    last_pct = -1.0
    cur_t = 0.0
    frame_n = 0
    err_chunks: list[str] = []

    def _emit(force: bool = False) -> None:
        nonlocal last_emit, last_pct
        now = time.monotonic()
        pct = None
        if duration and duration > 0:
            pct = max(0.0, min(99.5, 100.0 * cur_t / duration))
        msg = f"{label}: {_hms(cur_t)}"
        if duration and duration > 0:
            msg += f" / {_hms(duration)}"
        if frame_n:
            msg += f" | frame {frame_n}"
        if force or (now - last_emit) >= 1.0 or (pct is not None and abs(pct - last_pct) >= 0.4):
            # ETA from wall time
            if duration and duration > 0 and cur_t > 5 and hasattr(_emit, "_t0"):
                elapsed = now - _emit._t0  # type: ignore[attr-defined]
                speed = cur_t / elapsed if elapsed > 0 else 0
                if speed > 0:
                    remain = (duration - cur_t) / speed
                    msg += f" | ETA {_hms(remain)} ({speed:.1f}x)"
            progress.stage(stage, pct=pct if pct is not None else None, message=msg)
            last_emit = now
            if pct is not None:
                last_pct = pct

    _emit._t0 = time.monotonic()  # type: ignore[attr-defined]

    # Drain stderr in background so pipe never blocks
    import threading

    def _drain_err() -> None:
        for line in proc.stderr:
            line = line.rstrip()
            if line:
                err_chunks.append(line)
                progress.stage("log", message=line)

    err_thread = threading.Thread(target=_drain_err, daemon=True)
    err_thread.start()

    for raw in proc.stdout:
        line = raw.strip()
        if not line or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key == "frame":
            try:
                frame_n = int(val)
            except ValueError:
                pass
        elif key in ("out_time_ms", "out_time_us"):
            try:
                n = float(val)
                cur_t = n / 1_000_000.0 if key == "out_time_us" else n / 1000.0
            except ValueError:
                pass
            _emit()
        elif key == "out_time":
            try:
                parts = val.split(":")
                if len(parts) == 3:
                    cur_t = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
                    _emit()
            except ValueError:
                pass
        elif key == "progress" and val == "end":
            cur_t = duration or cur_t
            _emit(force=True)

    code = proc.wait()
    err_thread.join(timeout=2.0)
    _emit(force=True)
    if code == 0:
        progress.stage(stage, pct=100.0 if duration else None, message=f"{label}: done")
    else:
        tail = err_chunks[-3:] if err_chunks else ["(no stderr)"]
        progress.stage("log", message=f"{label}: ffmpeg exit {code} | {' | '.join(tail)}")
    return subprocess.CompletedProcess(cmd, code, "", "\n".join(err_chunks))


def probe_duration(path: Path) -> float:
    r = run([
        ffprobe_bin(), "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ])
    return float(r.stdout.strip())


def probe_size(path: Path) -> tuple[int, int]:
    r = run([
        ffprobe_bin(), "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json",
        str(path),
    ])
    data = json.loads(r.stdout or "{}")
    streams = data.get("streams") or [{}]
    w = int(streams[0].get("width") or 1920)
    h = int(streams[0].get("height") or 1080)
    return w, h


def hwaccel_args(cfg: AppConfig) -> list[str]:
    decode = str(cfg.device.get("decode") or "cpu").lower()
    if decode in ("cuda", "nvdec"):
        return ["-hwaccel", "cuda"]
    if decode in ("d3d11va", "dxva2", "videotoolbox"):
        return ["-hwaccel", decode]
    return []


def extract_region(
    video: Path,
    out_dir: Path,
    box: tuple[int, int, int, int],
    cfg: AppConfig,
    fps: float = 1.0,
    expected_duration: float | None = None,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = [p for p in out_dir.glob("f_*.jpg")]
    need = (
        max(1, int(expected_duration * fps * 0.85))
        if expected_duration and expected_duration > 0
        else None
    )
    # Reuse only complete full-timeline extracts (not sparse long-VOD leftovers)
    if cfg.detection.get("reuse_cache", True) and need is not None and len(existing) >= need:
        progress.stage(
            "extract",
            pct=100.0,
            message=f"reusing {len(existing)} feed frames (complete cache)",
        )
        return
    if cfg.detection.get("reuse_cache", True) and need is None and len(existing) > 50:
        progress.stage(
            "extract",
            pct=100.0,
            message=f"reusing {len(existing)} feed frames",
        )
        return
    # Incomplete / sparse cache → wipe so contiguous f_%06d matches absolute seconds
    if existing:
        progress.stage(
            "extract",
            message=f"clearing incomplete cache ({len(existing)} frames)",
        )
        for p in existing:
            try:
                p.unlink()
            except OSError:
                pass
        for sub in ("_coarse",):
            d = out_dir / sub
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
        for d in out_dir.glob("_w_*"):
            shutil.rmtree(d, ignore_errors=True)

    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    threads = str(cfg.device.get("ffmpeg_threads") or 1)
    # Small kill-feed JPEG; slightly lower quality = faster disk write on multi-hour VODs
    vf = f"fps={fps},crop={w}:{h}:{x1}:{y1}"
    hw = hwaccel_args(cfg)
    label = f"kill-feed extract ({'CUDA' if hw else 'CPU'} @ {fps}fps)"
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
        *hw,
        "-threads", threads,
        "-i", str(video),
        "-vf", vf,
        "-q:v", "7",
        "-threads", threads,
        "-y", str(out_dir / "f_%06d.jpg"),
    ]
    r = run_ffmpeg_progress(
        cmd,
        duration=expected_duration,
        stage="extract",
        label=label,
        out_dir=out_dir,
    )
    if r.returncode != 0 or not any(out_dir.glob("f_*.jpg")):
        progress.stage("extract", message="CUDA extract failed - retrying CPU decode")
        cmd = [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
            "-threads", threads,
            "-i", str(video),
            "-vf", vf,
            "-q:v", "7",
            "-y", str(out_dir / "f_%06d.jpg"),
        ]
        run_ffmpeg_progress(
            cmd,
            duration=expected_duration,
            stage="extract",
            label=f"kill-feed extract (CPU @ {fps}fps)",
            out_dir=out_dir,
        )


def audio_peak_times(video: Path, threshold_dbfs: float = -26.0) -> list[float]:
    """Activity starts via silencedetect (audio-only — cheap on long VODs)."""
    import re

    cmd = [
        ffmpeg_bin(), "-hide_banner", "-nostats",
        "-vn",
        "-i", str(video),
        "-af", f"silencedetect=noise={threshold_dbfs}dB:d=0.25",
        "-f", "null", "-",
    ]
    r = run(cmd, check=False)
    text = (r.stderr or "") + (r.stdout or "")
    ends = [float(m) for m in re.findall(r"silence_end:\s*([\d.]+)", text)]
    starts = [float(m) for m in re.findall(r"silence_start:\s*([\d.]+)", text)]
    # Midpoints of loud stretches + silence_end markers
    times = list(ends)
    for i, s in enumerate(starts):
        if i + 1 < len(ends):
            times.append((s + ends[i + 1]) / 2.0)
        elif i > 0:
            times.append(s)
    return sorted(set(round(t, 1) for t in times if t >= 0))


def cluster_windows(
    times: list[float],
    pad: float = 4.0,
    merge_gap: float = 8.0,
    duration: float | None = None,
) -> list[tuple[float, float]]:
    """Merge timestamps into [start, end] windows with padding."""
    if not times:
        return []
    pts = sorted(times)
    windows: list[tuple[float, float]] = []
    cur_s = max(0.0, pts[0] - pad)
    cur_e = pts[0] + pad
    for t in pts[1:]:
        s = max(0.0, t - pad)
        e = t + pad
        if s <= cur_e + merge_gap:
            cur_e = max(cur_e, e)
        else:
            windows.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    windows.append((cur_s, cur_e))
    if duration is not None:
        windows = [(s, min(e, duration)) for s, e in windows]
    return windows


def frames_covering(out_dir: Path, start: float, end: float) -> int:
    """Count absolute-second frames present in [start, end]."""
    n = 0
    for t in range(int(start), int(end) + 1):
        if (out_dir / f"f_{t + 1:06d}.jpg").exists():
            n += 1
    return n


def _rename_window_frames(tmp: Path, out_dir: Path, start: float) -> int:
    written = 0
    for i, src in enumerate(sorted(tmp.glob("f_*.jpg"))):
        t = int(start) + i
        dst = out_dir / f"f_{t + 1:06d}.jpg"
        if dst.exists():
            dst.unlink()
        src.rename(dst)
        written += 1
    for p in tmp.glob("*"):
        p.unlink()
    try:
        tmp.rmdir()
    except OSError:
        pass
    return written


def extract_region_windows(
    video: Path,
    out_dir: Path,
    box: tuple[int, int, int, int],
    cfg: AppConfig,
    windows: list[tuple[float, float]],
    fps: float = 1.0,
    label_prefix: str = "window",
) -> int:
    """Extract one crop region inside time windows. Frame name = absolute second+1."""
    out_dir.mkdir(parents=True, exist_ok=True)
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    threads = str(cfg.device.get("ffmpeg_threads") or 1)
    written = 0
    total_span = sum(max(0.5, e - s) for s, e in windows) or 1.0
    done_span = 0.0
    for start, end in windows:
        dur = max(0.5, end - start)
        need = max(1, int(dur * fps * 0.85))
        if cfg.detection.get("reuse_cache", True) and frames_covering(out_dir, start, end) >= need:
            progress.stage(
                "extract",
                pct=min(99.0, 100.0 * (done_span + dur) / total_span),
                message=f"reusing {label_prefix} {_hms(start)}–{_hms(end)}",
            )
            done_span += dur
            continue
        tmp = out_dir / f"_w_{int(start)}"
        tmp.mkdir(parents=True, exist_ok=True)
        for p in tmp.glob("*.jpg"):
            p.unlink()
        vf = f"fps={fps},crop={w}:{h}:{x1}:{y1}"
        label = f"{label_prefix} extract {_hms(start)}–{_hms(end)}"
        cmd = [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
            *hwaccel_args(cfg),
            "-threads", threads,
            "-ss", f"{start:.2f}",
            "-t", f"{dur:.2f}",
            "-i", str(video),
            "-vf", vf,
            "-q:v", "6",
            "-threads", threads,
            "-y", str(tmp / "f_%06d.jpg"),
        ]
        r = run_ffmpeg_progress(
            cmd,
            duration=dur,
            stage="extract",
            label=label,
            out_dir=tmp,
        )
        if r.returncode != 0 or not any(tmp.glob("f_*.jpg")):
            progress.stage("extract", message=f"{label}: CUDA failed - retry CPU")
            cmd = [
                ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
                "-threads", threads,
                "-ss", f"{start:.2f}",
                "-t", f"{dur:.2f}",
                "-i", str(video),
                "-vf", vf,
                "-q:v", "6",
                "-y", str(tmp / "f_%06d.jpg"),
            ]
            run_ffmpeg_progress(
                cmd,
                duration=dur,
                stage="extract",
                label=f"{label} (CPU)",
                out_dir=tmp,
            )
        written += _rename_window_frames(tmp, out_dir, start)
        done_span += dur
    return written


def extract_dual_region_windows(
    video: Path,
    tip_dir: Path,
    tip_box: tuple[int, int, int, int],
    feed_dir: Path,
    feed_box: tuple[int, int, int, int],
    cfg: AppConfig,
    windows: list[tuple[float, float]],
    fps: float = 1.0,
) -> int:
    """
    One decode pass → tip + feed crops (avoids extract progress running 0→100 twice).
    Frame names = absolute second+1 in each out dir.
    """
    tip_dir.mkdir(parents=True, exist_ok=True)
    feed_dir.mkdir(parents=True, exist_ok=True)
    tx1, ty1, tx2, ty2 = tip_box
    fx1, fy1, fx2, fy2 = feed_box
    tw, th = tx2 - tx1, ty2 - ty1
    fw, fh = fx2 - fx1, fy2 - fy1
    threads = str(cfg.device.get("ffmpeg_threads") or 1)
    written = 0
    total_span = sum(max(0.5, e - s) for s, e in windows) or 1.0
    done_span = 0.0
    for start, end in windows:
        dur = max(0.5, end - start)
        need = max(1, int(dur * fps * 0.85))
        tip_ok = frames_covering(tip_dir, start, end) >= need
        feed_ok = frames_covering(feed_dir, start, end) >= need
        if cfg.detection.get("reuse_cache", True) and tip_ok and feed_ok:
            progress.stage(
                "extract",
                pct=min(99.0, 100.0 * (done_span + dur) / total_span),
                message=f"reusing tip+feed {_hms(start)}–{_hms(end)}",
            )
            done_span += dur
            continue
        # Only one side missing → fall back to single-region extract for that side
        if tip_ok and not feed_ok:
            written += extract_region_windows(
                video, feed_dir, feed_box, cfg, [(start, end)], fps=fps, label_prefix="feed"
            )
            done_span += dur
            continue
        if feed_ok and not tip_ok:
            written += extract_region_windows(
                video, tip_dir, tip_box, cfg, [(start, end)], fps=fps, label_prefix="tip"
            )
            done_span += dur
            continue

        tip_tmp = tip_dir / f"_w_{int(start)}"
        feed_tmp = feed_dir / f"_w_{int(start)}"
        for d in (tip_tmp, feed_tmp):
            d.mkdir(parents=True, exist_ok=True)
            for p in d.glob("*.jpg"):
                p.unlink()

        fc = (
            f"[0:v]fps={fps},split=2[t0][f0];"
            f"[t0]crop={tw}:{th}:{tx1}:{ty1}[t];"
            f"[f0]crop={fw}:{fh}:{fx1}:{fy1}[f]"
        )
        label = f"tip+feed extract {_hms(start)}–{_hms(end)}"
        cmd = [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
            *hwaccel_args(cfg),
            "-threads", threads,
            "-ss", f"{start:.2f}",
            "-t", f"{dur:.2f}",
            "-i", str(video),
            "-filter_complex", fc,
            "-map", "[t]", "-q:v", "6", "-threads", threads, "-y", str(tip_tmp / "f_%06d.jpg"),
            "-map", "[f]", "-q:v", "6", "-threads", threads, "-y", str(feed_tmp / "f_%06d.jpg"),
        ]
        r = run_ffmpeg_progress(cmd, duration=dur, stage="extract", label=label, out_dir=tip_tmp)
        if r.returncode != 0 or not any(tip_tmp.glob("f_*.jpg")) or not any(feed_tmp.glob("f_*.jpg")):
            progress.stage("extract", message=f"{label}: dual failed — sequential fallback")
            for d in (tip_tmp, feed_tmp):
                for p in d.glob("*"):
                    p.unlink()
                try:
                    d.rmdir()
                except OSError:
                    pass
            written += extract_region_windows(
                video, tip_dir, tip_box, cfg, [(start, end)], fps=fps, label_prefix="tip"
            )
            written += extract_region_windows(
                video, feed_dir, feed_box, cfg, [(start, end)], fps=fps, label_prefix="feed"
            )
            done_span += dur
            continue
        written += _rename_window_frames(tip_tmp, tip_dir, start)
        written += _rename_window_frames(feed_tmp, feed_dir, start)
        done_span += dur
    return written


def extract_region_coarse(
    video: Path,
    out_dir: Path,
    box: tuple[int, int, int, int],
    cfg: AppConfig,
    stride_seconds: float,
    duration: float,
) -> int:
    """One-pass low-fps feed strip; names frames by absolute second (stride * i)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    x1, y1, x2, y2 = box
    w, h = x2 - x1, y2 - y1
    threads = str(cfg.device.get("ffmpeg_threads") or 1)
    fps = 1.0 / max(1.0, stride_seconds)
    tmp = out_dir / "_coarse"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    vf = f"fps={fps},crop={w}:{h}:{x1}:{y1}"
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
        *hwaccel_args(cfg),
        "-threads", threads,
        "-i", str(video),
        "-vf", vf,
        "-q:v", "6",
        "-threads", threads,
        "-y", str(tmp / "f_%06d.jpg"),
    ]
    r = run(cmd, check=False)
    if r.returncode != 0 or not any(tmp.glob("f_*.jpg")):
        cmd = [
            ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
            "-threads", threads,
            "-i", str(video),
            "-vf", vf,
            "-q:v", "6",
            "-y", str(tmp / "f_%06d.jpg"),
        ]
        run(cmd, check=False)
    written = 0
    for i, src in enumerate(sorted(tmp.glob("f_*.jpg"))):
        t = min(int(i * stride_seconds), int(duration))
        dst = out_dir / f"f_{t + 1:06d}.jpg"
        if not dst.exists():
            src.rename(dst)
            written += 1
        else:
            src.unlink()
    shutil.rmtree(tmp, ignore_errors=True)
    return written


def encode_segment(
    video: Path,
    start: float,
    end: float,
    out_path: Path,
    cfg: AppConfig,
) -> None:
    fmt = cfg.template.get("format") or {}
    crop = fmt.get("crop")
    scale = fmt.get("scale_to") or "1080x1920"
    fps = str(fmt.get("fps") or 60)
    if crop:
        vf = (
            f"crop={crop['portrait_width']}:{crop['portrait_height']}:"
            f"{crop['x_offset']}:{crop['y_offset']},scale={scale.replace('x', ':')}"
        )
    else:
        # landscape pad/scale
        sw, sh = scale.split("x") if "x" in scale else ("1920", "1080")
        vf = f"scale={sw}:{sh}:force_original_aspect_ratio=decrease,pad={sw}:{sh}:(ow-iw)/2:(oh-ih)/2"

    encode = str(cfg.device.get("encode") or "h264_nvenc")
    preset = str(cfg.device.get("encode_preset") or "p5")
    cq = str(cfg.device.get("cq") or 18)
    br = f"{cfg.device.get('video_bitrate_mbps', 12)}M"
    mr = f"{cfg.device.get('maxrate_mbps', 16)}M"
    abr = f"{cfg.device.get('audio_bitrate_kbps', 192)}k"
    threads = str(cfg.device.get("ffmpeg_threads") or 1)
    dur = max(0.1, end - start)

    base = [
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
        *hwaccel_args(cfg),
        "-threads", threads,
        "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{dur:.3f}",
        "-vf", vf, "-r", fps,
    ]
    if encode in ("h264_nvenc", "hevc_nvenc"):
        vcodec = [
            "-c:v", encode, "-preset", preset, "-rc", "vbr", "-cq", cq,
            "-b:v", br, "-maxrate", mr, "-bufsize", "24M",
        ]
    elif encode in ("h264_videotoolbox", "hevc_videotoolbox"):
        vcodec = ["-c:v", encode, "-b:v", br]
    else:
        # CPU encode — explicit user/device choice
        vcodec = ["-c:v", "libx264", "-preset", "veryfast", "-crf", cq, "-b:v", br]

    cmd = base + vcodec + [
        "-c:a", "aac", "-b:a", abr, "-ar", "48000", "-ac", "2",
        "-threads", threads,
        "-movflags", "+faststart", "-y", str(out_path),
    ]
    r = run(cmd, check=False)
    if r.returncode != 0 or not out_path.exists():
        # Retry CPU encode as last resort
        cmd = base + [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", abr,
            "-threads", threads, "-y", str(out_path),
        ]
        run(cmd, check=False)


def concat_copy(segs: list[Path], out_path: Path) -> None:
    lst = out_path.parent / "concat.txt"
    lines = [f"file '{p.resolve().as_posix()}'" for p in segs]
    lst.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run([
        ffmpeg_bin(), "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(lst),
        "-c", "copy", "-movflags", "+faststart", "-y", str(out_path),
    ])
