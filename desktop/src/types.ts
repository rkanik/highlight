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
};

export const defaultSettings = (): AppSettings => ({
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
});
