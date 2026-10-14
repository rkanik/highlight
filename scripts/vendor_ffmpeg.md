# Vendor ffmpeg / ffprobe for Tauri sidecars

Place platform builds here (names must match `tauri.conf.json` `externalBin`):

```
desktop/src-tauri/binaries/ffmpeg-x86_64-pc-windows-msvc.exe
desktop/src-tauri/binaries/ffprobe-x86_64-pc-windows-msvc.exe
desktop/src-tauri/binaries/highlight-engine-x86_64-pc-windows-msvc.exe
```

Get the triple with `rustc --print host-tuple`. Prefer **LGPL** static builds for redistribution.

`npm run build:win` copies ffmpeg/ffprobe from `PATH` automatically when these files are missing.
To vendor a specific build instead, copy it in under the names above before building.

OCR models (optional, RapidOCR can use defaults):

```
models/rapidocr/*.onnx
```

Set at runtime:

- `HIGHLIGHT_FFMPEG` / `HIGHLIGHT_FFPROBE`
- `HIGHLIGHT_RESOURCES` → repo or app resource root
