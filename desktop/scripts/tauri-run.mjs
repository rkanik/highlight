#!/usr/bin/env node
/**
 * Prefer cargo-installed tauri-cli (native EXE) over npm @tauri-apps/cli.
 * On this machine WDAC/Application Control blocks unsigned .node natives
 * used by the npm CLI package.
 */
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import process from "node:process";

const args = process.argv.slice(2);
const home = process.env.USERPROFILE || process.env.HOME || "";
const cargoTauri = path.join(home, ".cargo", "bin", "cargo-tauri.exe");
const cargoTauriCmd = path.join(home, ".cargo", "bin", "tauri.exe");

function run(cmd, cmdArgs, shell = false) {
  return new Promise((resolve) => {
    const child = spawn(cmd, cmdArgs, { stdio: "inherit", shell });
    child.on("exit", (code) => resolve(code ?? 1));
    child.on("error", () => resolve(1));
  });
}

async function main() {
  if (existsSync(cargoTauri)) {
    // cargo-tauri expects: cargo-tauri tauri <args> OR just use `tauri` shim
    const code = await run(cargoTauri, ["tauri", ...args]);
    process.exit(code);
  }
  if (existsSync(cargoTauriCmd)) {
    process.exit(await run(cargoTauriCmd, args));
  }

  // Fall back to npm package (fails under WDAC blocking .node)
  const npmCli = path.join(
    process.cwd(),
    "node_modules",
    "@tauri-apps",
    "cli",
    "tauri.js"
  );
  if (existsSync(npmCli)) {
    const code = await run(process.execPath, [npmCli, ...args]);
    if (code !== 0) {
      console.error(`
Tauri failed. Common causes on this machine:

  A) Rust/cargo missing (needed to compile src-tauri):
       winget install Rustlang.Rustup
       # open a NEW terminal, then:
       rustup default stable
       cd desktop && npm run tauri -- ${args.join(" ") || "dev"}

  B) Smart App Control / WDAC blocking *.node (if you see ERR_DLOPEN_FAILED):
       Turn Smart App Control off, or allow
       node_modules/@tauri-apps/cli-win32-x64-msvc/*.node

  C) Engine-only (no UI):
       cd repo root && PYTHONPATH=. python -m engine run --template kill_short path\\\\to\\\\vod.mp4
`);
    }
    process.exit(code);
  }

  console.error("No Tauri CLI found. Install @tauri-apps/cli or cargo tauri-cli.");
  process.exit(1);
}

main();
