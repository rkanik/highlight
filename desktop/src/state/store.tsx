import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { open } from "@tauri-apps/plugin-dialog";
import { notifications } from "@mantine/notifications";
import { api, baseName } from "../api/tauri";
import {
  defaultUserConfig,
  VIDEO_EXTS,
  type AppInfo,
  type AppSettings,
  type Clip,
  type DeviceProbe,
  type GameInfo,
  type JobMode,
  type ProgressEvt,
  type ScanRange,
  type SourceItem,
  type UserConfig,
} from "../types";

export type Page = "dashboard" | "generate" | "library" | "settings";
export type Route = { page: Page; detailDir: string | null };

export type JobPhase = "idle" | "scanning" | "review" | "assembling" | "done" | "failed";

export type JobState = {
  running: boolean;
  mode: JobMode | null;
  phase: JobPhase;
  pct: number;
  status: string;
  log: string[];
  outputDir: string | null;
  manifest: string | null;
  clips: Clip[];
  selected: string[];
};

const LOG_CAP = 3000;

const idleJob = (): JobState => ({
  running: false,
  mode: null,
  phase: "idle",
  pct: 0,
  status: "Ready.",
  log: [],
  outputDir: null,
  manifest: null,
  clips: [],
  selected: [],
});

type Store = {
  route: Route;
  navigate: (page: Page, detailDir?: string | null) => void;
  config: UserConfig;
  configLoaded: boolean;
  setConfig: (fn: (c: UserConfig) => UserConfig) => void;
  updateSettings: (patch: Partial<AppSettings>) => void;
  appInfo: AppInfo | null;
  games: GameInfo[];
  probe: DeviceProbe | null;
  sources: SourceItem[];
  addSources: (paths: string[]) => void;
  browseSources: () => Promise<void>;
  removeSource: (path: string) => void;
  setRanges: (path: string, ranges: ScanRange[]) => void;
  job: JobState;
  startScan: () => Promise<void>;
  startAssemble: () => Promise<void>;
  cancelJob: () => Promise<void>;
  toggleClip: (id: string) => void;
  setSelected: (ids: string[]) => void;
  clearLog: () => void;
  dragHot: boolean;
  outputsVersion: number;
  effectiveOutputDir: string | null;
};

const Ctx = createContext<Store | null>(null);

export function useStore(): Store {
  const s = useContext(Ctx);
  if (!s) throw new Error("useStore outside StoreProvider");
  return s;
}

function isVideo(path: string): boolean {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  return VIDEO_EXTS.includes(ext);
}

function mergeConfig(saved: Partial<UserConfig> | null): UserConfig {
  const base = defaultUserConfig();
  if (!saved) return base;
  return {
    ...base,
    ...saved,
    settings: { ...base.settings, ...(saved.settings ?? {}) },
    brand: { ...base.brand, ...(saved.brand ?? {}) },
    providers: Array.isArray(saved.providers) ? saved.providers : [],
  };
}

function isFullRange(src: SourceItem): boolean {
  if (src.ranges.length !== 1 || src.duration <= 0) return false;
  const [a, b] = src.ranges[0];
  return a <= 0.5 && b >= src.duration - 0.5;
}

function buildJob(
  mode: JobMode,
  config: UserConfig,
  sources: SourceItem[],
  extra: Record<string, unknown> = {}
): Record<string, unknown> {
  const s = config.settings;
  const include: string[] = [];
  if (s.includeKnocks) include.push("my_knock");
  if (s.includeKills) include.push("my_kill");
  if (s.includeAssists) include.push("my_assist");
  const clanTags = s.clanTags.split(/[,;\s]+/).filter(Boolean);
  const track = { username: s.username, clan_tags: clanTags };
  const brand = Object.fromEntries(Object.entries(config.brand).filter(([, v]) => !!v));

  const provider =
    mode === "scan" ? undefined : config.providers.find((p) => p.id === config.activeProviderId);
  const wantsAi = s.aiTitle || s.aiDescription || s.aiThumbnail;

  return {
    mode,
    active_game: s.gameId,
    active_template: s.templateId,
    sources: sources.map((src) => ({
      path: src.path,
      scan_ranges: isFullRange(src) || !src.ranges.length ? null : src.ranges,
    })),
    title: s.title.trim() || null,
    track_override: track,
    games: {
      [s.gameId]: {
        track,
        detection: {
          mode: s.detectionMode,
          scan_start_seconds: null,
          scan_end_seconds: null,
        },
        templates: {
          [s.templateId]: {
            include,
            padding: { before_seconds: s.padBefore, after_seconds: s.padAfter },
          },
        },
      },
    },
    device: {
      decode: s.decode,
      encode: s.encode,
      gpu_id: s.gpuId === "auto" ? null : s.gpuId,
    },
    paths: {
      ...(s.workDir ? { work_dir: s.workDir } : {}),
      ...(s.outputDir ? { output_dir: s.outputDir } : {}),
      ...(Object.keys(brand).length ? { brand } : {}),
    },
    ai:
      provider && wantsAi
        ? {
            provider: {
              id: provider.id,
              name: provider.name,
              type: provider.type,
              api_key: provider.apiKey,
              base_url: provider.baseUrl,
              text_model: provider.textModel,
              image_model: provider.imageModel,
            },
            title: s.aiTitle && !s.title.trim(),
            description: s.aiDescription,
            thumbnail: s.aiThumbnail,
          }
        : null,
    ...extra,
  };
}

export function StoreProvider({ children }: { children: ReactNode }) {
  const [route, setRoute] = useState<Route>({ page: "dashboard", detailDir: null });
  const [config, setConfigState] = useState<UserConfig>(defaultUserConfig);
  const [configLoaded, setConfigLoaded] = useState(false);
  const [appInfo, setAppInfo] = useState<AppInfo | null>(null);
  const [games, setGames] = useState<GameInfo[]>([]);
  const [probe, setProbe] = useState<DeviceProbe | null>(null);
  const [sources, setSources] = useState<SourceItem[]>([]);
  const [job, setJob] = useState<JobState>(idleJob);
  const [dragHot, setDragHot] = useState(false);
  const [outputsVersion, setOutputsVersion] = useState(0);
  const modeRef = useRef<JobMode | null>(null);
  const sourcesRef = useRef<SourceItem[]>(sources);
  sourcesRef.current = sources;

  const appendLog = useCallback((line: string) => {
    setJob((j) => {
      const log = j.log.length >= LOG_CAP ? j.log.slice(-LOG_CAP + 1) : j.log.slice();
      log.push(line);
      return { ...j, log };
    });
  }, []);

  const navigate = useCallback((page: Page, detailDir: string | null = null) => {
    setRoute({ page, detailDir });
  }, []);

  const setConfig = useCallback((fn: (c: UserConfig) => UserConfig) => {
    setConfigState((c) => fn(c));
  }, []);

  const updateSettings = useCallback((patch: Partial<AppSettings>) => {
    setConfigState((c) => ({ ...c, settings: { ...c.settings, ...patch } }));
  }, []);

  // Boot: config, games, devices, app info
  useEffect(() => {
    (async () => {
      let saved: UserConfig | null = null;
      try {
        saved = await api.getUserConfig();
      } catch (e) {
        appendLog(`config: ${e}`);
      }
      const merged = mergeConfig(saved);
      setConfigState(merged);
      setConfigLoaded(true);

      try {
        setAppInfo(await api.appInfo());
      } catch (e) {
        appendLog(`app_info: ${e}`);
      }
      try {
        const g = await api.listGames();
        setGames(g);
        if (g.length && !g.some((x) => x.id === merged.settings.gameId)) {
          setConfigState((c) => ({
            ...c,
            settings: {
              ...c.settings,
              gameId: g[0].id,
              templateId: g[0].templates[0]?.id ?? c.settings.templateId,
            },
          }));
        }
      } catch (e) {
        appendLog(`list_games: ${e}`);
      }
      try {
        const d = await api.probeDevices();
        setProbe(d);
        if (!saved) {
          setConfigState((c) => ({
            ...c,
            settings: { ...c.settings, decode: d.recommend_decode, encode: d.recommend_encode },
          }));
        }
      } catch (e) {
        appendLog(`probe_devices: ${e}`);
      }
    })();
  }, [appendLog]);

  // Persist config (debounced)
  useEffect(() => {
    if (!configLoaded) return;
    const t = window.setTimeout(() => {
      void api.saveUserConfig(config).catch((e) => appendLog(`save config: ${e}`));
    }, 400);
    return () => window.clearTimeout(t);
  }, [config, configLoaded, appendLog]);

  const probeOne = useCallback(
    async (path: string) => {
      try {
        const info = await api.probeVideo(path);
        const d = Math.max(0, Math.floor(info.duration || 0));
        setSources((list) =>
          list.map((s) =>
            s.path === path
              ? {
                  ...s,
                  probing: false,
                  duration: d,
                  width: info.width,
                  height: info.height,
                  ranges: [[0, d]],
                }
              : s
          )
        );
      } catch (e) {
        setSources((list) =>
          list.map((s) => (s.path === path ? { ...s, probing: false, error: String(e) } : s))
        );
        appendLog(`probe_video ${baseName(path)}: ${e}`);
      }
    },
    [appendLog]
  );

  const addSources = useCallback(
    (paths: string[]) => {
      const vids = paths.filter(isVideo);
      if (!vids.length) {
        if (paths.length)
          notifications.show({ color: "red", title: "Not a video", message: "Drop mp4 / mkv / mov / avi." });
        return;
      }
      const added = vids.filter((p) => !sourcesRef.current.some((s) => s.path === p));
      setSources((list) => {
        const fresh = added.filter((p) => !list.some((s) => s.path === p));
        return [
          ...list,
          ...fresh.map<SourceItem>((p) => ({
            path: p,
            duration: 0,
            width: 0,
            height: 0,
            probing: true,
            error: null,
            ranges: [],
          })),
        ];
      });
      setJob((j) => (j.running ? j : { ...j, phase: "idle", clips: [], selected: [], manifest: null }));
      setRoute({ page: "generate", detailDir: null });
      void (async () => {
        for (const p of added) await probeOne(p);
      })();
    },
    [probeOne]
  );

  const browseSources = useCallback(async () => {
    const picked = await open({
      multiple: true,
      filters: [{ name: "Video", extensions: VIDEO_EXTS }],
    });
    if (!picked) return;
    addSources(Array.isArray(picked) ? picked : [picked]);
  }, [addSources]);

  const removeSource = useCallback((path: string) => {
    setSources((list) => list.filter((s) => s.path !== path));
  }, []);

  const setRanges = useCallback((path: string, ranges: ScanRange[]) => {
    setSources((list) => list.map((s) => (s.path === path ? { ...s, ranges } : s)));
  }, []);

  // Native file drop (Tauri intercepts HTML5 drops, so paths only arrive here)
  useEffect(() => {
    let un: (() => void) | undefined;
    if (!("__TAURI_INTERNALS__" in window)) return;
    void getCurrentWebview()
      .onDragDropEvent((ev) => {
        const p = ev.payload;
        if (p.type === "enter" || p.type === "over") setDragHot(true);
        else if (p.type === "leave") setDragHot(false);
        else if (p.type === "drop") {
          setDragHot(false);
          addSources(p.paths);
        }
      })
      .then((fn) => {
        un = fn;
      });
    return () => un?.();
  }, [addSources]);

  // Engine progress
  useEffect(() => {
    let un: (() => void) | undefined;
    let lastKey = "";
    let lastAt = 0;
    void listen<ProgressEvt>("engine-progress", (ev) => {
      const p = ev.payload;
      const msg = p.message || p.error || p.artifact || "";
      const stage = p.stage || "";
      const line = [stage, p.pct != null ? `${p.pct.toFixed(0)}%` : "", msg]
        .filter(Boolean)
        .join(" ");

      if (p.pct != null || (line && !p.done)) {
        setJob((j) => ({
          ...j,
          pct: p.pct != null ? p.pct : j.pct,
          status: line && !p.done ? line : j.status,
        }));
      }

      const isTick = ["extract", "ocr", "ffmpeg", "encode", "assemble"].includes(stage);
      const isMilestone =
        !!p.error ||
        !!p.artifact ||
        ["log", "init", "detect", "ai", "clips"].includes(stage) ||
        /starting|done|failed|WARNING|reusing|retry/i.test(msg);
      const now = Date.now();
      const key = `${stage}|${msg.slice(0, 40)}`;
      if (line && (isMilestone || !isTick || key !== lastKey || now - lastAt > 8000)) {
        appendLog(line);
        lastKey = key;
        lastAt = now;
      }

      if (p.clips) {
        const clips = p.clips;
        setJob((j) => ({
          ...j,
          clips,
          selected: clips.map((c) => c.id),
          manifest: p.manifest ?? j.manifest,
        }));
      }

      if (!p.done) return;
      const mode = modeRef.current;
      if (p.cancelled) {
        setJob((j) => ({
          ...j,
          running: false,
          status: "Stopped.",
          phase: mode === "assemble" ? "review" : j.clips.length ? "review" : "idle",
        }));
        return;
      }
      if (p.ok) {
        setJob((j) => {
          if (!j.running && j.phase !== "scanning" && j.phase !== "assembling") return j;
          return {
            ...j,
            running: false,
            pct: 100,
            status: "Done.",
            manifest: p.manifest ?? j.manifest,
            outputDir: p.output ?? j.outputDir,
            phase: mode === "scan" ? "review" : "done",
          };
        });
        if (p.output || p.manifest) {
          if (mode === "scan") {
            notifications.show({ color: "teal", title: "Scan complete", message: "Review clips, then assemble." });
          } else {
            setOutputsVersion((v) => v + 1);
            notifications.show({ color: "teal", title: "Video ready", message: p.output ? baseName(p.output) : "" });
          }
        }
      } else {
        setJob((j) => {
          if (!j.running) return j;
          return {
            ...j,
            running: false,
            status: "FAILED",
            phase: mode === "assemble" ? "review" : "failed",
          };
        });
        appendLog("FAILED");
      }
    }).then((fn) => {
      un = fn;
    });
    return () => un?.();
  }, [appendLog]);

  const runMode = useCallback(
    async (mode: JobMode, extra: Record<string, unknown> = {}) => {
      if (!sources.length || job.running) return;
      modeRef.current = mode;
      setJob((j) => ({
        ...j,
        running: true,
        mode,
        pct: 0,
        status: "Starting…",
        log: [...j.log, `── ${mode === "scan" ? "Scan" : "Assemble"} started ──`],
        phase: mode === "scan" ? "scanning" : "assembling",
        ...(mode === "scan" ? { clips: [], selected: [], manifest: null, outputDir: null } : {}),
      }));
      try {
        await api.runJob(buildJob(mode, config, sources, extra));
      } catch (e) {
        appendLog(String(e));
        setJob((j) => ({ ...j, running: false, status: "FAILED", phase: mode === "scan" ? "failed" : "review" }));
      }
    },
    [sources, job.running, config, appendLog]
  );

  const startScan = useCallback(() => runMode("scan"), [runMode]);
  const startAssemble = useCallback(
    () =>
      runMode("assemble", {
        clips_manifest: job.manifest,
        selected_clips: job.clips.filter((c) => job.selected.includes(c.id)).map((c) => c.id),
      }),
    [runMode, job.manifest, job.clips, job.selected]
  );

  const cancelJob = useCallback(async () => {
    try {
      await api.cancelJob();
      setJob((j) => ({ ...j, status: "Stopping…" }));
    } catch (e) {
      appendLog(`Stop failed: ${e}`);
    }
  }, [appendLog]);

  const toggleClip = useCallback((id: string) => {
    setJob((j) => ({
      ...j,
      selected: j.selected.includes(id) ? j.selected.filter((x) => x !== id) : [...j.selected, id],
    }));
  }, []);

  const setSelected = useCallback((ids: string[]) => {
    setJob((j) => ({ ...j, selected: ids }));
  }, []);

  const clearLog = useCallback(() => setJob((j) => ({ ...j, log: [] })), []);

  const effectiveOutputDir = config.settings.outputDir || appInfo?.output_dir || null;

  const value: Store = {
    route,
    navigate,
    config,
    configLoaded,
    setConfig,
    updateSettings,
    appInfo,
    games,
    probe,
    sources,
    addSources,
    browseSources,
    removeSource,
    setRanges,
    job,
    startScan,
    startAssemble,
    cancelJob,
    toggleClip,
    setSelected,
    clearLog,
    dragHot,
    outputsVersion,
    effectiveOutputDir,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}
