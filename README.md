# Highlight

Cross-platform desktop app + Python engine for short highlight videos from long VODs.

- **Game** selector (PUBG v1; more later)
- **Kill Short** default — knocks + kills + assists from **top-right kill feed OCR**
- Config-driven templates, GPU probe, offline generate
- Tauri 2 UI + frozen `highlight-engine` / ffmpeg sidecars

## Quick start (dev)

```bash
pip install -r requirements.txt
cd desktop && npm install   # once
npm run dev                 # from repo root — starts Tauri + Vite
```

Engine CLI:

```bash
PYTHONPATH=. python -m engine games
PYTHONPATH=. python -m engine devices
PYTHONPATH=. python -m engine run --game pubg --template kill_short --title "Hook | PUBG" path/to/vod.mp4
```

## Package

See [scripts/package_desktop.md](scripts/package_desktop.md).

## Layout

| Path                   | Role                                           |
| ---------------------- | ---------------------------------------------- |
| `engine/`              | Detect, assemble, OCR, devices, JSONL progress |
| `config/defaults.yaml` | PUBG game profile + templates                  |
| `desktop/`             | Tauri 2 + React UI                             |
| `scripts/`             | Legacy KS1/KS1A/MH1 helpers + packaging        |

<!-- Check from 01:36:00 to 02:37:00 -->
