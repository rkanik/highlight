#!/usr/bin/env python3
"""Freeze highlight-engine with PyInstaller into desktop/src-tauri/binaries/."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "desktop" / "src-tauri" / "binaries"
OUT.mkdir(parents=True, exist_ok=True)

name = "highlight-engine"
sep = ";" if sys.platform == "win32" else ":"
add_data = f"{ROOT / 'config' / 'defaults.yaml'}{sep}config"
cmd = [
    sys.executable, "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name", name,
    "--paths", str(ROOT),
    "--add-data", add_data,
    "--collect-submodules", "engine",
    "--collect-all", "rapidocr_onnxruntime",
    "--collect-all", "onnxruntime",
    str(ROOT / "engine" / "__main__.py"),
]
print("+", " ".join(cmd))
subprocess.check_call(cmd, cwd=ROOT)

ext = ".exe" if sys.platform == "win32" else ""
built = ROOT / "dist" / (name + ext)
# Tauri externalBin expects name-<target-triple>[.exe]
import subprocess as sp
try:
    triple = sp.check_output(["rustc", "--print", "host-tuple"], text=True).strip()
except Exception:
    triple = "x86_64-pc-windows-msvc" if sys.platform == "win32" else "x86_64-unknown-linux-gnu"
target = OUT / f"{name}-{triple}{ext}"
target.write_bytes(built.read_bytes())
print("wrote", target)
