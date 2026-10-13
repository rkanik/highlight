export type GameInfo = {
  id: string;
  name: string;
  templates: { id: string; name: string; description: string }[];
};

export type GpuInfo = {
  id: string;
  index: number;
  name: string;
  vendor: string;
  type: string;
  encoders: string[];
  decode: string[];
};

export type DeviceProbe = {
  gpus: GpuInfo[];
  cpu: GpuInfo;
  ffmpeg_encoders: string[];
  hwaccels: string[];
  auto_select: string;
  recommend_encode: string;
  recommend_decode: string;
};

export type VideoProbe = {
  path: string;
  duration: number;
  width: number;
  height: number;
};

export type AppSettings = {
  gameId: string;
  templateId: string;
  username: string;
  clanTags: string;
  detectionMode: "feed_ocr" | "audio_assist" | "hybrid";
  includeKnocks: boolean;
  includeKills: boolean;
  includeAssists: boolean;
  decode: string;
  encode: string;
  gpuId: string;
  padBefore: number;
  padAfter: number;
  title: string;
  workDir: string;
  outputDir: string;
  aiTitle: boolean;
  aiDescription: boolean;
  aiThumbnail: boolean;
};

export type AiProviderType = "openai" | "gemini" | "openai_compat";

export type AiProvider = {
  id: string;
  name: string;
  type: AiProviderType;
  apiKey: string;
  baseUrl: string;
  textModel: string;
  imageModel: string;
};

export type BrandAssets = {
  character: string;
  cover: string;
  profile: string;
};

export type UserConfig = {
  version: 1;
  settings: AppSettings;
  providers: AiProvider[];
  activeProviderId: string | null;
  brand: BrandAssets;
};

export type AppInfo = {
  repo_root: string;
  output_dir: string;
  work_dir: string;
  brand_defaults: BrandAssets;
};

export type ScanRange = [number, number];

export type SourceItem = {
  path: string;
  duration: number;
  width: number;
  height: number;
  probing: boolean;
  error: string | null;
  ranges: ScanRange[];
};

export type Clip = {
  id: string;
  index: number;
  path: string;
  start: number;
  end: number;
  dur: number;
  kind: string;
  role: "cold_open" | "body" | string;
  video: string;
  text?: string;
  anchor?: number;
  scoped?: boolean;
  aspect?: string;
};

export type OutputItem = {
  dir: string;
  name: string;
  title: string;
  mp4: string | null;
  thumb: string | null;
  thumb_yt: string | null;
  description: string;
  meta: Record<string, unknown> | null;
  modified: number;
};

export type JobMode = "scan" | "assemble" | "run";

export type ProgressEvt = {
  stage?: string;
  pct?: number;
  message?: string;
  error?: string;
  done?: boolean;
  ok?: boolean;
  cancelled?: boolean;
  output?: string;
  manifest?: string;
  artifact?: string;
  clips?: Clip[];
};

export const PROVIDER_PRESETS: Record<
  AiProviderType,
  { label: string; baseUrl: string; textModel: string; imageModel: string }
> = {
  openai: {
    label: "OpenAI",
    baseUrl: "https://api.openai.com/v1",
    textModel: "gpt-4o-mini",
    imageModel: "gpt-image-1",
  },
  gemini: {
    label: "Google Gemini",
    baseUrl: "https://generativelanguage.googleapis.com/v1beta",
    textModel: "gemini-2.5-flash",
    imageModel: "gemini-2.5-flash-image",
  },
  openai_compat: {
    label: "OpenAI-compatible",
    baseUrl: "http://localhost:11434/v1",
    textModel: "",
    imageModel: "",
  },
};

export const defaultSettings = (): AppSettings => ({
  gameId: "pubg",
  templateId: "kill_short",
  username: "rkanik",
  clanTags: "ASA",
  detectionMode: "feed_ocr",
  includeKnocks: true,
  includeKills: true,
  includeAssists: true,
  decode: "cuda",
  encode: "h264_nvenc",
  gpuId: "auto",
  padBefore: 4,
  padAfter: 3,
  title: "",
  workDir: "",
  outputDir: "",
  aiTitle: true,
  aiDescription: true,
  aiThumbnail: true,
});

export const defaultUserConfig = (): UserConfig => ({
  version: 1,
  settings: defaultSettings(),
  providers: [],
  activeProviderId: null,
  brand: { character: "", cover: "", profile: "" },
});

export const VIDEO_EXTS = ["mp4", "mkv", "mov", "avi", "webm"];
