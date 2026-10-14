# Hilvox desktop (Tauri 2 + React)

Offline short-video generator UI over the Python `engine` package.

## Dev

From repo root:

```bash
pip install -r requirements.txt
cd desktop && npm install   # once
# from repo root:
npm run dev
```

Requires **Rust (MSVC)** + **VS Build Tools (C++)** + WebView2 (Windows).

```powershell
winget install Rustlang.Rustup
winget install Microsoft.VisualStudio.2022.BuildTools --override "--wait --quiet --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
```

**Git Bash tip:** MSVC `link.exe` must win over Git’s `/usr/bin/link`. Prefer **PowerShell** / **cmd**, or put VS linker first on `PATH`.

If Smart App Control blocks `@tauri-apps/cli` `.node` files, turn it off (you already did) or allow that path.

Engine-only (no UI):

```bash
cd V:\highlight
PYTHONPATH=. python -m engine run --template kill_short path\to\vod.mp4
```

## UI flow

1. **Game** — PUBG (v1 only)
2. Drop VODs
3. **Template** — Kill Short (default) or Match Highlight
4. **Generate** — streams JSONL progress from `python -m engine run`
5. **Settings** — username/clan, include toggles, GPU/encode, padding, paths

First launch shows onboarding for your PUBG username. Dev builds show a `DEV` badge and keep settings in `%APPDATA%\HilvoxDev`, separate from the installed app.

## Packaging

From repo root: `npm run build:win` (NSIS installer). See [`scripts/package_desktop.md`](../scripts/package_desktop.md).
