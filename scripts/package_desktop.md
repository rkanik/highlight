# Package Hilvox for Windows

One command from the repo root:

```bash
npm run build:win
```

Output: `desktop/src-tauri/target/release/bundle/nsis/Hilvox_<version>_x64-setup.exe`

Reuse an already-frozen engine (faster rebuilds after UI-only changes):

```bash
npm run build:win -- --skip-engine
```

## What `build:win` does

1. Freezes the Python engine with PyInstaller (`scripts/build_engine_sidecar.py`) into
   `desktop/src-tauri/binaries/highlight-engine-<target-triple>.exe`.
2. Copies `ffmpeg.exe` / `ffprobe.exe` from `PATH` into `desktop/src-tauri/binaries/`
   as `ffmpeg-<target-triple>.exe` / `ffprobe-<target-triple>.exe`, unless already there.
   Prefer LGPL builds if you redistribute (see `scripts/vendor_ffmpeg.md`).
3. Runs `tauri build --bundles nsis`, bundling `config/`, `models/`, `scripts/ocr_frame.ps1`
   and the `augniik-*` brand defaults as app resources.

Cargo is limited to half the CPU cores (override with `CARGO_BUILD_JOBS`).

## Prerequisites

- `pip install -r requirements.txt` (includes PyInstaller)
- Rust MSVC toolchain + VS C++ Build Tools (see `desktop/README.md`)
- `cd desktop && npm install` once
- ffmpeg + ffprobe on `PATH` (or pre-copied into `desktop/src-tauri/binaries/`)

## Installer options

The NSIS installer lets you:

- install for the current user (no admin, `%LOCALAPPDATA%\Hilvox`) or for all users (`Program Files\Hilvox`)
- change the install directory
- create a desktop shortcut (finish page)
- add a Start Menu entry under `Hilvox`

## Installed vs dev

| | Installed (release) | `npm run dev` (debug) |
|---|---|---|
| Window / nav | `Hilvox` | `Hilvox (Dev)` + red `DEV` badge |
| Settings | `%APPDATA%\Hilvox` | `%APPDATA%\HilvoxDev` |
| Work dir | `%LOCALAPPDATA%\com.rkanik.hilvox\work` | `<repo>/work` |
| Output dir | `%USERPROFILE%\Videos\Hilvox` | `<repo>/out` |
| Engine | frozen sidecar | live `python -m engine` |

Both can be installed and run side by side. Work / output dirs can be overridden in Settings.

## Smoke test (clean machine)

Install without Python/ffmpeg on `PATH`. First launch asks for your PUBG username.
Drop a VOD, Game = PUBG, Kill Short, Generate.
