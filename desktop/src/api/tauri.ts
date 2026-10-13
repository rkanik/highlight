import { convertFileSrc, invoke } from "@tauri-apps/api/core";
import type {
  AppInfo,
  DeviceProbe,
  GameInfo,
  OutputItem,
  UserConfig,
  VideoProbe,
} from "../types";

export const api = {
  listGames: () => invoke<GameInfo[]>("list_games"),
  probeDevices: () => invoke<DeviceProbe>("probe_devices"),
  probeVideo: (path: string) => invoke<VideoProbe>("probe_video", { path }),
  runJob: (job: Record<string, unknown>) => invoke<void>("run_job", { job }),
  cancelJob: () => invoke<void>("cancel_job"),
  openPath: (path: string) => invoke<void>("open_path", { path }),
  appInfo: () => invoke<AppInfo>("app_info"),
  listOutputs: (outputDir: string | null) =>
    invoke<OutputItem[]>("list_outputs", { outputDir }),
  readOutput: (dir: string) => invoke<OutputItem>("read_output", { dir }),
  getUserConfig: () => invoke<UserConfig | null>("get_user_config"),
  saveUserConfig: (config: UserConfig) => invoke<void>("save_user_config", { config }),
  copyBrandAsset: (src: string, kind: string) =>
    invoke<string>("copy_brand_asset", { src, kind }),
};

export function fileSrc(path: string | null | undefined, bust?: number | string): string {
  if (!path) return "";
  const url = convertFileSrc(path);
  return bust != null ? `${url}?v=${bust}` : url;
}

export function fmtTime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`;
  return `${m}:${String(sec).padStart(2, "0")}`;
}

export function baseName(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}
