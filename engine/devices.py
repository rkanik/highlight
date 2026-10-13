"""Probe GPUs and ffmpeg encoders/hwaccels for UI dropdowns."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

from engine.config import ffmpeg_bin


def _run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        return (r.stdout or "") + (r.stderr or "")
    except Exception as exc:
        return str(exc)


def list_ffmpeg_encoders() -> list[str]:
    text = _run([ffmpeg_bin(), "-hide_banner", "-encoders"])
    found = []
    for name in ("h264_nvenc", "hevc_nvenc", "h264_amf", "h264_qsv", "h264_videotoolbox", "libx264", "libx265"):
        if name in text:
            found.append(name)
    return found


def list_hwaccels() -> list[str]:
    text = _run([ffmpeg_bin(), "-hide_banner", "-hwaccels"])
    out = []
    for name in ("cuda", "d3d11va", "dxva2", "qsv", "videotoolbox", "vaapi"):
        if re.search(rf"\b{name}\b", text):
            out.append(name)
    return out


def _nvidia_gpus() -> list[dict[str, Any]]:
    """NVML-free: nvidia-smi query."""
    text = _run([
        "nvidia-smi",
        "--query-gpu=index,name,memory.total",
        "--format=csv,noheader,nounits",
    ])
    gpus = []
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        name = parts[1]
        kind = "discrete"
        low = name.lower()
        if "nvidia" in low and any(x in low for x in ("laptop", "max-q", "mobile")):
            kind = "discrete"
        # Heuristic: eGPU often has Thunderbolt in system — we can't see bus easily; mark as discrete
        gpus.append({
            "id": f"nvidia:{idx}",
            "index": idx,
            "name": name,
            "vendor": "nvidia",
            "type": kind,
            "encoders": ["h264_nvenc", "hevc_nvenc"],
            "decode": ["cuda"],
        })
    return gpus


def _windows_dxgi_fallback() -> list[dict[str, Any]]:
    """Best-effort list via PowerShell Win32_VideoController."""
    if os.name != "nt":
        return []
    ps = (
        "Get-CimInstance Win32_VideoController | "
        "Select-Object Name, AdapterRAM | ConvertTo-Json -Compress"
    )
    text = _run(["powershell", "-NoProfile", "-Command", ps])
    gpus = []
    try:
        data = json.loads(text.strip() or "[]")
        if isinstance(data, dict):
            data = [data]
        for i, row in enumerate(data or []):
            name = str(row.get("Name") or f"GPU {i}")
            low = name.lower()
            typ = "integrated" if any(x in low for x in ("intel", "uhd", "iris", "radeon graphics")) else "discrete"
            if "nvidia" in low:
                enc = ["h264_nvenc"]
                dec = ["cuda"]
                vendor = "nvidia"
            elif "amd" in low or "radeon" in low:
                enc = ["h264_amf"]
                dec = ["d3d11va"]
                vendor = "amd"
            else:
                enc = ["libx264"]
                dec = ["d3d11va"]
                vendor = "other"
            gpus.append({
                "id": f"dxgi:{i}",
                "index": i,
                "name": name,
                "vendor": vendor,
                "type": typ,
                "encoders": enc,
                "decode": dec,
            })
    except json.JSONDecodeError:
        pass
    return gpus


def probe_devices() -> dict[str, Any]:
    encoders = list_ffmpeg_encoders()
    hwaccels = list_hwaccels()
    gpus = _nvidia_gpus()
    if not gpus:
        gpus = _windows_dxgi_fallback()

    # Auto-select: external → discrete nvidia → other discrete → integrated → CPU
    def rank(g: dict) -> tuple:
        t = g.get("type")
        order = {"external": 0, "discrete": 1, "integrated": 2}.get(t, 3)
        nvidia = 0 if g.get("vendor") == "nvidia" else 1
        return (order, nvidia, g.get("index", 99))

    auto = None
    if gpus:
        auto = sorted(gpus, key=rank)[0]["id"]

    cpu = {
        "id": "cpu",
        "index": -1,
        "name": "CPU",
        "vendor": "cpu",
        "type": "cpu",
        "encoders": [e for e in encoders if e.startswith("lib")],
        "decode": ["cpu"],
    }

    return {
        "gpus": gpus,
        "cpu": cpu,
        "ffmpeg_encoders": encoders,
        "hwaccels": hwaccels,
        "auto_select": auto or "cpu",
        "recommend_encode": (
            "h264_nvenc" if "h264_nvenc" in encoders
            else "h264_videotoolbox" if "h264_videotoolbox" in encoders
            else "libx264"
        ),
        "recommend_decode": (
            "cuda" if "cuda" in hwaccels
            else "videotoolbox" if "videotoolbox" in hwaccels
            else "cpu"
        ),
    }


def auto_select_gpu(probe: dict | None = None) -> str:
    p = probe or probe_devices()
    return str(p.get("auto_select") or "cpu")
