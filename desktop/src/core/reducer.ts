/* 壳状态机（纯函数）：缝事件 + phase 变化 + 时钟 → 字幕条 / 面板状态。
   壳不关心听译怎么认字，只消费草稿 / 定稿 / 提示三类事件。
   时间来自 action（tick 或带时刻的事件），不用 Date.now()，保证可测。 */

import type { ListenEvent, NoticeKind } from "./events";

/** 切条之后没有下一条，当前条再留约两秒拿掉（CONTEXT.md：静默撤条） */
export const SILENT_WITHDRAW_MS = 2000;
/** 「不是英日韩」提示行停留时长（PRD 建议 11：几秒后拿掉） */
export const NOT_LANG_HINT_MS = 3800;
/** 「已换音源」短提示时长（PRD 建议 6） */
export const SWITCH_HINT_MS = 1600;

export type Phase = "idle" | "downloading" | "listening" | "failed";

export interface SubtitleBar {
  orig: string;
  trans: string;
  kind: "draft" | "final";
  finalAt: number | null;
}

export interface PanelStatus {
  kind: "info" | "ok" | "warn" | "err";
  text: string;
}

export interface ShellState {
  phase: Phase;
  downloadPct: number;
  bar: SubtitleBar | null;
  hint: string | null;
  hintUntil: number | null;
  panelStatus: PanelStatus;
  listeningSourceLabel: string;
  /** 最近一次失败的原因：no_audio 在面板里要给「改用系统混音 / 重试」两个出路 */
  failureKind: NoticeKind | null;
  /** 已绑定的进程退出后，必须明确重新选择一条音源。 */
  sourceGone: boolean;
  now: number;
}

export type ShellAction =
  | { type: "listen"; event: ListenEvent; now?: number }
  | { type: "phase"; phase: Phase; sourceLabel?: string; pct?: number }
  | { type: "source_selected"; sourceLabel: string; audible: boolean }
  | { type: "source_switched"; sourceLabel: string; now: number }
  | { type: "source_gone" }
  | { type: "download"; pct: number }
  | { type: "tick"; now: number };

/** 控制面板只吃 phase / 状态行 / 失败出路，不跟字幕条重绘。 */
export function panelViewChanged(prev: ShellState, next: ShellState): boolean {
  return (
    prev.phase !== next.phase ||
    prev.downloadPct !== next.downloadPct ||
    prev.failureKind !== next.failureKind ||
    prev.sourceGone !== next.sourceGone ||
    prev.panelStatus.kind !== next.panelStatus.kind ||
    prev.panelStatus.text !== next.panelStatus.text
  );
}

export function initialShellState(): ShellState {
  return {
    phase: "idle",
    downloadPct: 0,
    bar: null,
    hint: null,
    hintUntil: null,
    panelStatus: { kind: "info", text: "选一个音源，按开听。" },
    listeningSourceLabel: "",
    failureKind: null,
    sourceGone: false,
    now: 0,
  };
}

export function reduce(state: ShellState, action: ShellAction): ShellState {
  switch (action.type) {
    case "tick":
      return withClock(state, action.now);
    case "download":
      return { ...state, downloadPct: Math.min(100, action.pct) };
    case "phase":
      return applyPhase(state, action);
    case "source_selected":
      if (state.phase === "listening") {
        return {
          ...state,
          failureKind: null,
          sourceGone: false,
          panelStatus: { kind: "info", text: `正在切到 · ${action.sourceLabel}` },
        };
      }
      return {
        ...state,
        phase: "idle",
        bar: null,
        hint: null,
        hintUntil: null,
        failureKind: null,
        sourceGone: false,
        panelStatus: {
          kind: "info",
          text: action.audible
            ? `已选 ${action.sourceLabel}，按开听。`
            : `已选 ${action.sourceLabel}，暂未出声；可以先开听等它出声。`,
        },
      };
    case "source_switched": {
      const next = withClock({ ...state, listeningSourceLabel: action.sourceLabel }, action.now);
      return {
        ...next,
        bar: null, // 弃掉进行中的条，新音源从新条开始
        hint: "已换音源，继续在听",
        hintUntil: action.now + SWITCH_HINT_MS,
        sourceGone: false,
        panelStatus: { kind: "ok", text: `在听 · ${action.sourceLabel}` },
      };
    }
    case "source_gone":
      // 开听中音源进程退出了：停止开听（Rust 已撤字幕窗），停在面板等再选
      return {
        ...state,
        phase: "idle",
        bar: null,
        hint: null,
        hintUntil: null,
        sourceGone: true,
        panelStatus: {
          kind: "warn",
          text: "音源进程退出了。重新选一个，不会自动改用系统混音。",
        },
      };
    case "listen":
      return applyListenEvent(
        action.now === undefined ? state : { ...state, now: action.now },
        action.event
      );
  }
}

function withClock(state: ShellState, now: number): ShellState {
  let next: ShellState = { ...state, now };
  if (next.bar?.kind === "final" && next.bar.finalAt !== null && now - next.bar.finalAt >= SILENT_WITHDRAW_MS) {
    next = { ...next, bar: null };
  }
  if (next.hint !== null && next.hintUntil !== null && now >= next.hintUntil) {
    next = { ...next, hint: null, hintUntil: null };
  }
  return next;
}

function applyPhase(
  state: ShellState,
  action: Extract<ShellAction, { type: "phase" }>
): ShellState {
  const sourceLabel = action.sourceLabel ?? state.listeningSourceLabel;
  // phase 变化只清字幕条；failureKind 留给下一次成功开听再清，
  // 免得 Rust 兜底广播的 failed 盖掉 no_audio 的两个出路按钮
  const base: ShellState = {
    ...state,
    phase: action.phase,
    listeningSourceLabel: sourceLabel,
    bar: null,
    hint: null,
    hintUntil: null,
  };
  if (action.phase === "listening" || action.phase === "idle") base.failureKind = null;
  if (action.phase === "listening") base.sourceGone = false;
  if (action.pct !== undefined) base.downloadPct = Math.min(100, action.pct);
  switch (action.phase) {
    case "listening":
      return { ...base, panelStatus: { kind: "ok", text: `在听 · ${sourceLabel}` } };
    case "idle":
      return { ...base, panelStatus: { kind: "info", text: "没在听。选一个音源，按开听。" } };
    case "downloading":
      return base;
    case "failed":
      return base;
  }
}

function applyListenEvent(state: ShellState, event: ListenEvent): ShellState {
  switch (event.type) {
    case "draft": {
      // 提示行由自己的计时拿掉（原型同款）：换音源提示要活得过紧接着的新草稿
      return { ...state, bar: { orig: event.orig, trans: event.trans, kind: "draft" as const, finalAt: null } };
    }
    case "final":
      return {
        ...state,
        bar: { orig: event.orig, trans: event.trans, kind: "final", finalAt: state.now },
      };
    case "notice":
      return applyNotice(state, event.kind);
  }
}

function applyNotice(state: ShellState, kind: NoticeKind): ShellState {
  switch (kind) {
    case "no_speech":
      return {
        ...state,
        panelStatus: { kind: "warn", text: "在听 · 还没听到人声" },
      };
    case "not_lang":
      return {
        ...state,
        hint: "听到声音，但不是英 / 日 / 韩的人声",
        hintUntil: state.now + NOT_LANG_HINT_MS,
        panelStatus: { kind: "warn", text: "在听 · 没听出英 / 日 / 韩的人声" },
      };
    case "no_audio":
      return {
        ...state,
        phase: "failed",
        bar: null,
        failureKind: "no_audio",
        panelStatus: {
          kind: "err",
          text: `${state.listeningSourceLabel || "音源"} 抓不到声音（权限不够）。`,
        },
      };
    case "crashed":
      return {
        ...state,
        phase: "failed",
        bar: null,
        failureKind: "crashed",
        panelStatus: { kind: "err", text: "听译停了。点开听重试。" },
      };
  }
}
