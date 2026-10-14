#!/usr/bin/env node
/**
 * Build the Hilvox Windows installer (NSIS).
 *
 *   npm run build:win                 freeze engine, vendor ffmpeg, bundle
 *   npm run build:win -- --skip-engine   reuse existing frozen engine sidecar
 */
import { spawnSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, readdirSync, statSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const DESKTOP = path.join(ROOT, "desktop");
const BIN_DIR = path.join(DESKTOP, "src-tauri", "binaries");
const TARGET_DIR = process.env.CARGO_TARGET_DIR
  ? path.resolve(DESKTOP, "src-tauri", process.env.CARGO_TARGET_DIR)
  : path.join(DESKTOP, "src-tauri", "target");
const NSIS_OUT = path.join(TARGET_DIR, "release", "bundle", "nsis");
const RELEASE_DIR = path.join(ROOT, "release");
const args = new Set(process.argv.slice(2));

if (process.platform !== "win32") {
  fail("build:win must run on Windows.");
}

function fail(msg) {
  console.error(`\n[build:win] ${msg}`);
  process.exit(1);
}

function step(msg) {
  console.log(`\n[build:win] ${msg}`);
}

const quote = (a) => (/[\s"]/.test(a) ? `"${a.replace(/"/g, '\\"')}"` : a);
const line = (cmd, cmdArgs) => [cmd, ...cmdArgs].map(quote).join(" ");

function run(cmd, cmdArgs, cwd = ROOT) {
  const c = line(cmd, cmdArgs);
  console.log(`+ ${c}`);
  const r = spawnSync(c, { cwd, stdio: "inherit", shell: true });
  if (r.status !== 0) fail(`${cmd} exited with ${r.status ?? r.error}`);
}

function capture(cmd, cmdArgs) {
  const r = spawnSync(line(cmd, cmdArgs), { encoding: "utf8", shell: true });
  return r.status === 0 ? r.stdout.trim() : "";
}

const triple = capture("rustc", ["--print", "host-tuple"]) || "x86_64-pc-windows-msvc";
const sidecar = (stem) => path.join(BIN_DIR, `${stem}-${triple}.exe`);

// This PC has thermal-restarted under full CPU load; keep rustc to half the cores.
process.env.CARGO_BUILD_JOBS ||= String(Math.max(2, Math.floor(os.cpus().length / 2)));

step(`target ${triple}, cargo jobs ${process.env.CARGO_BUILD_JOBS}`);

if (args.has("--skip-engine") && existsSync(sidecar("highlight-engine"))) {
  step("reusing frozen engine sidecar");
} else {
  step("freezing Python engine (PyInstaller)");
  run("python", [path.join("scripts", "build_engine_sidecar.py")]);
}

for (const stem of ["ffmpeg", "ffprobe"]) {
  const dest = sidecar(stem);
  if (existsSync(dest)) continue;
  const found = capture("where", [stem]).split(/\r?\n/).find((p) => p.toLowerCase().endsWith(".exe"));
  if (!found) {
    fail(`${stem}.exe not found. Put it on PATH or copy it to ${dest} (see scripts/vendor_ffmpeg.md).`);
  }
  step(`vendoring ${stem} from ${found}`);
  copyFileSync(found, dest);
}

step("bundling NSIS installer (tauri build)");
run("npm", ["run", "tauri", "--", "build", "--bundles", "nsis"], DESKTOP);

const installers = existsSync(NSIS_OUT)
  ? readdirSync(NSIS_OUT)
      .filter((f) => f.toLowerCase().endsWith(".exe"))
      .map((f) => path.join(NSIS_OUT, f))
      .sort((a, b) => statSync(b).mtimeMs - statSync(a).mtimeMs)
  : [];
if (!installers.length) fail(`no installer found in ${NSIS_OUT}`);
mkdirSync(RELEASE_DIR, { recursive: true });
const final = path.join(RELEASE_DIR, path.basename(installers[0]));
copyFileSync(installers[0], final);
step(`installer ready:\n  ${final}`);
