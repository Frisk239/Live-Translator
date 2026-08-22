/* 持久化设置与音源清单的纯逻辑。
   规则（PRD User Story 19/20）：音源和选择下次打开还在；
   上次那个进程不在了，停在控制面板里再选，不改成系统混音。 */

import { normalizeListenWay, type ListenWay } from "./hosted";

export type Mode = "both" | "orig" | "trans";
export type FontSize = "s" | "m" | "l" | "xl";
export type FontFace = "yahei" | "hei" | "song";
export type SubStyle = "outline" | "yellow" | "plate";
export type SubEdge = "none" | "thin" | "thick";
export type SubPlate = "none" | "soft" | "hard";
export type SubWeight = "regular" | "bold";
export type { ListenWay };

export function normalizeFontSize(value: string | undefined): FontSize {
  if (value === "s" || value === "l" || value === "xl") return value;
  return "m";
}

export function normalizeFontFace(value: string | undefined): FontFace {
  if (value === "hei" || value === "song") return value;
  return "yahei";
}

export function normalizeSubStyle(value: string | undefined): SubStyle {
  if (value === "yellow" || value === "plate") return value;
  return "outline";
}

export function normalizeInk(value: string | undefined): string {
  return /^#[0-9a-fA-F]{6}$/.test(value ?? "") ? (value as string).toLowerCase() : "#ffffff";
}

export function normalizeEdge(value: string | undefined): SubEdge {
  if (value === "none" || value === "thin") return value;
  return "thick";
}

export function normalizePlate(value: string | undefined): SubPlate {
  if (value === "soft" || value === "hard") return value;
  return "none";
}

export function normalizeWeight(value: string | undefined): SubWeight {
  return value === "regular" ? "regular" : "bold";
}

export const SUB_PRESETS: Record<SubStyle, { ink: string; edge: SubEdge; plate: SubPlate; weight: SubWeight }> = {
  outline: { ink: "#ffffff", edge: "thick", plate: "none", weight: "bold" },
  yellow: { ink: "#ffe566", edge: "thick", plate: "none", weight: "bold" },
  plate: { ink: "#ffffff", edge: "none", plate: "hard", weight: "bold" },
};

/** 音源：一台正在出声的应用程序；系统混音兜底（ADR 0001）。
    清单由壳枚举系统音频会话（IAudioSessionManager2 峰值电平）实时给出。 */
export interface AudioSource {
  id: string;
  processName: string;
  friendlyName: string;
  audible: boolean;
  system: boolean;
}

export interface Settings {
  source: string | null;
  mode: Mode;
  font: FontSize;
  face: FontFace;
  style: SubStyle;
  ink: string;
  edge: SubEdge;
  plate: SubPlate;
  weight: SubWeight;
  autostart: boolean;
  modelReady: boolean;
  listenWay: ListenWay;
}

/** 控制面板中「存档设置 + 当前音源清单」的视图状态。 */
export interface SourceViewState {
  settings: Settings;
  sourceDead: boolean;
}

export const DEFAULT_SETTINGS: Settings = {
  source: null,
  mode: "both",
  font: "m",
  face: "yahei",
  style: "outline",
  ink: "#ffffff",
  edge: "thick",
  plate: "none",
  weight: "bold",
  autostart: false,
  modelReady: false,
  listenWay: "local",
};

/** 合并存档与当前音源清单。sourceDead = 上次选的进程已不在清单，面板要停住等再选。 */
export function loadSettings(
  saved: Partial<Settings> | undefined,
  sources: AudioSource[]
): SourceViewState {
  const settings: Settings = {
    ...DEFAULT_SETTINGS,
    ...saved,
    listenWay: normalizeListenWay(saved?.listenWay),
    font: normalizeFontSize(saved?.font),
    face: normalizeFontFace(saved?.face),
    style: normalizeSubStyle(saved?.style),
    ink: normalizeInk(saved?.ink),
    edge: normalizeEdge(saved?.edge),
    plate: normalizePlate(saved?.plate),
    weight: normalizeWeight(saved?.weight),
  };
  const sourceDead =
    settings.source !== null &&
    settings.source !== "system" &&
    !sources.some((s) => s.id === settings.source);
  return { settings, sourceDead };
}

/**
 * 设置广播到达后合并到控制面板。
 *
 * 每次都结合刚刷出的清单重算失效态，避免旧音源退出后的黄字把新选择锁住。
 */
export function applySettingsChange(
  view: SourceViewState,
  patch: Partial<Settings>,
  sources: AudioSource[]
): SourceViewState {
  return loadSettings({ ...view.settings, ...patch }, sources);
}

export function sameSources(a: AudioSource[], b: AudioSource[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((s, i) => {
    const t = b[i];
    return (
      s.id === t.id &&
      s.processName === t.processName &&
      s.friendlyName === t.friendlyName &&
      s.audible === t.audible &&
      s.system === t.system
    );
  });
}

export function sourceLabelOf(source: string | null, sources: AudioSource[]): string {
  if (!source) return "";
  if (source === "system") return "系统混音";
  const hit = sources.find((s) => s.id === source);
  return hit ? hit.processName || hit.friendlyName : source;
}
