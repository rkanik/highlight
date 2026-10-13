# Vendor ffmpeg / ffprobe for Tauri sidecars

Place platform builds here (names must match `tauri.conf.json` `externalBin`):

```
desktop/src-tauri/binaries/ffmpeg.exe      # Windows
desktop/src-tauri/binaries/ffprobe.exe
desktop/src-tauri/binaries/highlight-engine.exe
```

On Linux/macOS omit `.exe`. Prefer **LGPL** static builds for redistribution.

Windows example (manual):

1. Download a LGPL ffmpeg shared/static build
2. Copy `ffmpeg.exe` and `ffprobe.exe` into `desktop/src-tauri/binaries/`
3. Run `python scripts/build_engine_sidecar.py`

OCR models (optional, RapidOCR can use defaults):

```
models/rapidocr/*.onnx
```

Set at runtime:

- `HIGHLIGHT_FFMPEG` / `HIGHLIGHT_FFPROBE`
- `HIGHLIGHT_RESOURCES` → repo or app resource root
