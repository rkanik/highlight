# Package Highlight desktop (self-contained)

## 1. Python deps

```bash
pip install -r requirements.txt
```

## 2. Freeze engine sidecar

```bash
python scripts/build_engine_sidecar.py
```

Writes `desktop/src-tauri/binaries/highlight-engine-<target-triple>.exe` (Tauri sidecar naming).

## 3. Vendor ffmpeg / ffprobe

Copy LGPL builds into `desktop/src-tauri/binaries/` named:

- `ffmpeg-<target-triple>.exe`
- `ffprobe-<target-triple>.exe`

Get triple with `rustc --print host-tuple` (e.g. `x86_64-pc-windows-msvc`).
See `scripts/vendor_ffmpeg.md`.

## 4. Optional RapidOCR models

Place ONNX under `models/rapidocr/` (see `models/README.md`).

## 5. Enable Tauri externalBin

In `desktop/src-tauri/tauri.conf.json` under `bundle`, add:

```json
"externalBin": [
  "binaries/highlight-engine",
  "binaries/ffmpeg",
  "binaries/ffprobe"
]
```

## 6. Build installer

```bash
cd desktop
npm install
npm run tauri build
```

## 7. Smoke (clean machine)

Install MSI/NSIS without Python/ffmpeg on PATH. Drop a VOD, Game=PUBG, Kill Short, Generate offline.

## Dev (no freeze)

```bash
cd desktop && npm install && npm run tauri dev
```

Uses `python -m engine` from repo root via Rust host.
