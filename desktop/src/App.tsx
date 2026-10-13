import { useCallback, useEffect, useMemo, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import type { AppSettings, DeviceProbe, GameInfo, VideoProbe } from "./types";
import { defaultSettings } from "./types";

type ProgressEvt = {
  stage?: string;
  pct?: number;
  message?: string;
  error?: string;
  done?: boolean;
  ok?: boolean;
  cancelled?: boolean;
  output?: string;
  artifact?: string;
};

function fmtTime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export default function App() {
  const [games, setGames] = useState<GameInfo[]>([]);
  const [gameId, setGameId] = useState("pubg");
  const [templateId, setTemplateId] = useState("kill_short");
  const [sources, setSources] = useState<string[]>([]);
  const [settings, setSettings] = useState<AppSettings>(defaultSettings);
  const [probe, setProbe] = useState<DeviceProbe | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [running, setRunning] = useState(false);
  const [pct, setPct] = useState(0);
  const [status, setStatus] = useState("Ready.");
  const [log, setLog] = useState("");
  const [outputDir, setOutputDir] = useState<string | null>(null);
  const [dragHot, setDragHot] = useState(false);
  const [duration, setDuration] = useState(0);
  const [scanStart, setScanStart] = useState(0);
  const [scanEnd, setScanEnd] = useState(0);
  const [probeBusy, setProbeBusy] = useState(false);

  const templates = useMemo(
    () => games.find((g) => g.id === gameId)?.templates ?? [],
    [games, gameId]
  );

  const gpuOptions = useMemo(() => {
    const list = probe?.gpus ?? [];
    return [
      { id: "auto", name: `Auto (${probe?.auto_select ?? "…" })` },
      ...list.map((g) => ({
        id: g.id,
        name: `${g.name} · ${g.type}`,
      })),
      { id: "cpu", name: "CPU" },
    ];
  }, [probe]);

  const cpuForced = settings.gpuId === "cpu" || settings.encode === "libx264";

  useEffect(() => {
    (async () => {
      try {
        const g = await invoke<GameInfo[]>("list_games");
        setGames(g);
        if (g[0]) {
          setGameId(g[0].id);
          setTemplateId(g[0].templates[0]?.id ?? "kill_short");
        }
      } catch (e) {
        setLog((l) => l + `\nlist_games: ${e}`);
      }
      try {
        const d = await invoke<DeviceProbe>("probe_devices");
        setProbe(d);
        setSettings((s) => ({
          ...s,
          decode: d.recommend_decode,
          encode: d.recommend_encode,
          gpuId: "auto",
        }));
      } catch (e) {
        setLog((l) => l + `\nprobe_devices: ${e}`);
      }
    })();
  }, []);

  const firstSource = sources[0] ?? "";
  useEffect(() => {
    if (!firstSource) {
      setDuration(0);
      setScanStart(0);
      setScanEnd(0);
      return;
    }
    let cancelled = false;
    setProbeBusy(true);
    void invoke<VideoProbe>("probe_video", { path: firstSource })
      .then((info) => {
        if (cancelled) return;
        const d = Math.max(0, Math.floor(info.duration || 0));
        setDuration(d);
        setScanStart(0);
        setScanEnd(d);
        setStatus(`Ready · ${fmtTime(d)} VOD`);
      })
      .catch((e) => {
        if (cancelled) return;
        setDuration(0);
        setScanStart(0);
        setScanEnd(0);
        setLog((l) => l + `\nprobe_video: ${e}`);
      })
      .finally(() => {
        if (!cancelled) setProbeBusy(false);
      });
    return () => {
      cancelled = true;
    };
  }, [firstSource]);

  useEffect(() => {
    let un: (() => void) | undefined;
    let lastLogKey = "";
    let lastLogAt = 0;
    listen<ProgressEvt>("engine-progress", (ev) => {
      const p = ev.payload;
      if (p.pct != null) setPct(p.pct);

      const msg = p.message || p.error || p.artifact || "";
      const stage = p.stage || "";
      const line = [stage, p.pct != null ? `${p.pct.toFixed(0)}%` : "", msg]
        .filter(Boolean)
        .join(" ");

      // Live status always updates (extract ticks every ~1s)
      if (line && !p.done) {
        setStatus(line);
      }

      // Append to log: always for errors/artifacts/milestones; throttle noisy extract ticks
      const isTick =
        stage === "extract" || stage === "ocr" || stage === "ffmpeg" || stage === "assemble";
      const isMilestone =
        !!p.error ||
        !!p.artifact ||
        stage === "log" ||
        stage === "init" ||
        stage === "detect" ||
        /starting|done|failed|WARNING|reusing|retry/i.test(msg);
      const now = Date.now();
      const key = `${stage}|${msg.slice(0, 40)}`;
      if (line && (isMilestone || !isTick || key !== lastLogKey || now - lastLogAt > 8000)) {
        setLog((l) => (l ? l + "\n" : "") + line);
        lastLogKey = key;
        lastLogAt = now;
      }

      if (p.cancelled) {
        setStatus("Stopped.");
      }
      if (p.done) {
        setRunning(false);
        if (p.output) setOutputDir(p.output);
        if (p.cancelled) {
          setStatus("Stopped.");
          setLog((l) => (l.endsWith("Stopped.") ? l : l + "\nStopped."));
        } else if (p.ok === false) {
          // taskkill exit often looks like failure — don't overwrite Stop
          setStatus((prev) =>
            prev === "Stopped." || prev.startsWith("Stopping") ? "Stopped." : "FAILED"
          );
          setLog((l) => {
            if (l.includes("Stopped")) return l.endsWith("Stopped.") ? l : l + "\nStopped.";
            return l + "\nFAILED";
          });
        } else if (p.ok) {
          setStatus("Done.");
        }
      }
    }).then((fn) => {
      un = fn;
    });
    return () => {
      un?.();
    };
  }, []);

  const addFiles = useCallback(async () => {
    const picked = await open({
      multiple: true,
      filters: [{ name: "Video", extensions: ["mp4", "mkv", "mov", "avi"] }],
    });
    if (!picked) return;
    const list = Array.isArray(picked) ? picked : [picked];
    setSources((s) => [...s, ...list.filter((x) => !s.includes(x))]);
  }, []);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragHot(false);
    const files = Array.from(e.dataTransfer.files || []);
    // Tauri webview may not expose full path; use dialog for reliability on drop metadata
    const paths = files
      .map((f) => (f as File & { path?: string }).path)
      .filter((p): p is string => !!p);
    if (paths.length) {
      setSources((s) => [...s, ...paths.filter((x) => !s.includes(x))]);
    } else {
      void addFiles();
    }
  };

  const generate = async () => {
    if (!sources.length || running) return;
    setRunning(true);
    setPct(0);
    setStatus("Starting…");
    setLog("Starting…");
    setOutputDir(null);
    const include: string[] = [];
    if (settings.includeKnocks) include.push("my_knock");
    if (settings.includeKills) include.push("my_kill");
    if (settings.includeAssists) include.push("my_assist");

    const job = {
      active_game: gameId,
      active_template: templateId,
      sources,
      title: settings.title || null,
      track_override: {
        username: settings.username,
        clan_tags: settings.clanTags.split(/[,;\s]+/).filter(Boolean),
      },
      games: {
        [gameId]: {
          track: {
            username: settings.username,
            clan_tags: settings.clanTags.split(/[,;\s]+/).filter(Boolean),
          },
          detection: {
            mode: settings.detectionMode,
            ...(duration > 0 && (scanStart > 0 || scanEnd < duration)
              ? {
                  scan_start_seconds: scanStart,
                  scan_end_seconds: scanEnd,
                }
              : {
                  scan_start_seconds: null,
                  scan_end_seconds: null,
                }),
          },
          templates: {
            [templateId]: {
              include,
              padding: {
                before_seconds: settings.padBefore,
                after_seconds: settings.padAfter,
              },
            },
          },
        },
      },
      device: {
        decode: settings.decode,
        encode: settings.encode,
        gpu_id: settings.gpuId === "auto" ? null : settings.gpuId,
      },
      paths: {
        ...(settings.workDir ? { work_dir: settings.workDir } : {}),
        ...(settings.outputDir ? { output_dir: settings.outputDir } : {}),
      },
    };

    try {
      await invoke("run_job", { job });
    } catch (e) {
      setRunning(false);
      setLog((l) => l + `\n${e}`);
    }
  };

  const openOut = async () => {
    if (!outputDir) return;
    await invoke("open_path", { path: outputDir });
  };

  return (
    <div className="app">
      <header>
        <h1 className="brand">Highlight</h1>
        <p className="sub">Drop long VODs. Pick a template. Generate shorts offline.</p>
      </header>

      <section className="hero">
        <div className="row">
          <label className="field">
            Game
            <select value={gameId} onChange={(e) => setGameId(e.target.value)}>
              {games.map((g) => (
                <option key={g.id} value={g.id}>
                  {g.name}
                </option>
              ))}
              {!games.length && <option value="pubg">PUBG: BATTLEGROUNDS</option>}
            </select>
          </label>
          <label className="field">
            Template
            <select value={templateId} onChange={(e) => setTemplateId(e.target.value)}>
              {templates.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
              {!templates.length && <option value="kill_short">Kill Short</option>}
            </select>
          </label>
        </div>

        <div
          className={`drop ${dragHot ? "hot" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDragHot(true);
          }}
          onDragLeave={() => setDragHot(false)}
          onDrop={onDrop}
          onClick={() => void addFiles()}
        >
          <h2>Drop videos here</h2>
          <p>mp4 / mkv — or click to browse. Batch folders welcome.</p>
        </div>

        {sources.length > 0 && (
          <ul className="sources">
            {sources.map((s) => (
              <li key={s}>
                <span>{s}</span>
                <button type="button" onClick={() => setSources((x) => x.filter((y) => y !== s))}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}

        {sources.length > 0 && (
          <div className="scan-trim">
            <div className="scan-trim-head">
              <span className="scan-trim-label">Scan range</span>
              <span className="scan-trim-meta">
                {probeBusy
                  ? "Probing…"
                  : duration > 0
                    ? `${fmtTime(scanStart)} – ${fmtTime(scanEnd)} · ${fmtTime(scanEnd - scanStart)} of ${fmtTime(duration)}`
                    : "Duration unknown"}
              </span>
              {duration > 0 && (scanStart > 0 || scanEnd < duration) && (
                <button
                  type="button"
                  className="ghost tiny"
                  onClick={() => {
                    setScanStart(0);
                    setScanEnd(duration);
                  }}
                >
                  Full VOD
                </button>
              )}
            </div>
            <div className="scan-slider" aria-label="Scan range">
              <div
                className="scan-slider-track"
                style={{
                  ["--lo" as string]: duration ? `${(100 * scanStart) / duration}%` : "0%",
                  ["--hi" as string]: duration ? `${(100 * scanEnd) / duration}%` : "100%",
                }}
              />
              <input
                type="range"
                min={0}
                max={duration || 1}
                step={1}
                value={Math.min(scanStart, scanEnd)}
                disabled={!duration || running}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setScanStart(Math.min(v, scanEnd - 1));
                }}
                aria-label="Scan start"
              />
              <input
                type="range"
                min={0}
                max={duration || 1}
                step={1}
                value={Math.max(scanEnd, scanStart)}
                disabled={!duration || running}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setScanEnd(Math.max(v, scanStart + 1));
                }}
                aria-label="Scan end"
              />
            </div>
            <div className="scan-trim-inputs">
              <label className="field">
                Start (s)
                <input
                  type="number"
                  min={0}
                  max={Math.max(0, scanEnd - 1)}
                  value={scanStart}
                  disabled={!duration || running}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v)) setScanStart(Math.max(0, Math.min(v, scanEnd - 1)));
                  }}
                />
              </label>
              <label className="field">
                End (s)
                <input
                  type="number"
                  min={Math.min(duration, scanStart + 1)}
                  max={duration || 0}
                  value={scanEnd}
                  disabled={!duration || running}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    if (Number.isFinite(v))
                      setScanEnd(Math.max(scanStart + 1, Math.min(v, duration)));
                  }}
                />
              </label>
            </div>
            {sources.length > 1 && (
              <p className="scan-trim-note">Same absolute window applied to each source (clamped).</p>
            )}
          </div>
        )}

        <div className="actions">
          <button className="primary" disabled={!sources.length || running} onClick={() => void generate()}>
            {running ? "Generating…" : "Generate"}
          </button>
          {running && (
            <button
              className="danger"
              type="button"
              onClick={() => {
                void invoke("cancel_job")
                  .then(() => {
                    setStatus("Stopping…");
                    setLog((l) => l + "\nStopping…");
                  })
                  .catch((e) => setLog((l) => l + `\nStop failed: ${e}`));
              }}
            >
              Stop
            </button>
          )}
          <button className="ghost" type="button" onClick={() => setSettingsOpen(true)}>
            Settings
          </button>
          {outputDir && (
            <button className="ghost" type="button" onClick={() => void openOut()}>
              Open output
            </button>
          )}
        </div>

        <div className="progress">
          <span style={{ width: `${Math.min(100, pct)}%` }} />
        </div>
        <div className="status" aria-live="polite">
          {status}
          {running && pct > 0 ? ` · ${pct.toFixed(0)}%` : ""}
        </div>
        <div className="log">{log || "Ready."}</div>
      </section>

      {settingsOpen && (
        <>
          <div className="drawer-backdrop" onClick={() => setSettingsOpen(false)} />
          <aside className="drawer">
            <h2>Settings</h2>
            <div className="grid">
              <label className="field">
                Username
                <input
                  type="text"
                  value={settings.username}
                  onChange={(e) => setSettings({ ...settings, username: e.target.value })}
                />
              </label>
              <label className="field">
                Clan tags (comma)
                <input
                  type="text"
                  value={settings.clanTags}
                  onChange={(e) => setSettings({ ...settings, clanTags: e.target.value })}
                />
              </label>
              <label className="field">
                Detection mode
                <select
                  value={settings.detectionMode}
                  onChange={(e) =>
                    setSettings({
                      ...settings,
                      detectionMode: e.target.value as AppSettings["detectionMode"],
                    })
                  }
                >
                  <option value="feed_ocr">Kill feed OCR</option>
                  <option value="hybrid">Hybrid (feed + audio)</option>
                  <option value="audio_assist">Audio only</option>
                </select>
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={settings.includeKnocks}
                  onChange={(e) => setSettings({ ...settings, includeKnocks: e.target.checked })}
                />
                Include knocks
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={settings.includeKills}
                  onChange={(e) => setSettings({ ...settings, includeKills: e.target.checked })}
                />
                Include kills
              </label>
              <label className="check">
                <input
                  type="checkbox"
                  checked={settings.includeAssists}
                  onChange={(e) => setSettings({ ...settings, includeAssists: e.target.checked })}
                />
                Include assists
              </label>
              <label className="field">
                GPU
                <select
                  value={settings.gpuId}
                  onChange={(e) => setSettings({ ...settings, gpuId: e.target.value })}
                >
                  {gpuOptions.map((g) => (
                    <option key={g.id} value={g.id}>
                      {g.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Decode
                <select
                  value={settings.decode}
                  onChange={(e) => setSettings({ ...settings, decode: e.target.value })}
                >
                  {(probe?.hwaccels ?? ["cuda", "cpu"]).concat("cpu").filter((v, i, a) => a.indexOf(v) === i).map((h) => (
                    <option key={h} value={h}>
                      {h}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Encode
                <select
                  value={settings.encode}
                  onChange={(e) => setSettings({ ...settings, encode: e.target.value })}
                >
                  {(probe?.ffmpeg_encoders ?? ["h264_nvenc", "libx264"]).map((h) => (
                    <option key={h} value={h}>
                      {h}
                    </option>
                  ))}
                </select>
              </label>
              {cpuForced && (
                <p className="warn">CPU encode selected — long jobs may heat the PC. Prefer GPU when available.</p>
              )}
              <label className="field">
                Pad before (s)
                <input
                  type="number"
                  value={settings.padBefore}
                  onChange={(e) => setSettings({ ...settings, padBefore: Number(e.target.value) })}
                />
              </label>
              <label className="field">
                Pad after (s)
                <input
                  type="number"
                  value={settings.padAfter}
                  onChange={(e) => setSettings({ ...settings, padAfter: Number(e.target.value) })}
                />
              </label>
              <label className="field">
                Title (optional)
                <input
                  type="text"
                  value={settings.title}
                  placeholder="hook | PUBG"
                  onChange={(e) => setSettings({ ...settings, title: e.target.value })}
                />
              </label>
              <label className="field">
                Work dir (optional)
                <input
                  type="text"
                  value={settings.workDir}
                  onChange={(e) => setSettings({ ...settings, workDir: e.target.value })}
                />
              </label>
              <label className="field">
                Output dir (optional)
                <input
                  type="text"
                  value={settings.outputDir}
                  onChange={(e) => setSettings({ ...settings, outputDir: e.target.value })}
                />
              </label>
            </div>
            <button className="ghost" type="button" onClick={() => setSettingsOpen(false)}>
              Close
            </button>
          </aside>
        </>
      )}
    </div>
  );
}
