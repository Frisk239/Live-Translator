/* 字幕窗：透明、置顶、没有按钮；字默认穿透，悬停才出把手。
   渲染当前一条字幕条（先草稿后定稿）+ 短提示；与面板跑同一份 reducer。 */
import { invoke } from "@tauri-apps/api/core";
import { PhysicalPosition, PhysicalSize } from "@tauri-apps/api/dpi";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { isListenEvent } from "./core/events";
import { nextOverlayHeight } from "./core/overlayFit";
import { initialShellState, reduce, type Phase, type ShellState } from "./core/reducer";
import type { AudioSource, Settings } from "./core/settings";

const FONT_PX: Record<string, number> = { s: 24, m: 30, l: 38, xl: 46 };
const FONT_FACE: Record<string, string> = {
  yahei: '"Microsoft YaHei","Segoe UI",system-ui,sans-serif',
  hei: '"SimHei","Microsoft YaHei",sans-serif',
  song: '"SimSun","Songti SC",serif',
};

let shell: ShellState = initialShellState();
let settings: Settings | null = null;
let suppressFitUntil = 0;
let fitInFlight = false;

const win = document.getElementById("subtitleWin")!;
const stack = document.getElementById("subStack")!;
const hint = document.getElementById("subHint")!;
const orig = document.getElementById("subOrig")!;
const trans = document.getElementById("subTrans")!;
const overlay = getCurrentWindow();

async function getBootWhenReady(): Promise<{ settings: Settings; sources: AudioSource[] }> {
  for (let attempt = 0; attempt < 80; attempt++) {
    try {
      return await invoke<{ settings: Settings; sources: AudioSource[] }>("get_boot");
    } catch (error) {
      if (!String(error).includes("控制面板正在启动")) throw error;
      await new Promise((resolve) => window.setTimeout(resolve, 50));
    }
  }
  throw new Error("字幕窗启动超时。");
}

function render() {
  if (settings) {
    win.style.setProperty("--subsize", `${FONT_PX[settings.font] ?? 30}px`);
    win.style.setProperty("--subfont", FONT_FACE[settings.face] ?? FONT_FACE.yahei);
    win.style.setProperty("--subink", settings.ink || "#ffffff");
    win.style.setProperty("--subweight", settings.weight === "regular" ? "400" : "600");
    const edge = settings.edge || "thick";
    const w = edge === "none" ? "0" : edge === "thin" ? "1px" : "2px";
    win.style.setProperty(
      "--subshadow",
      edge === "none"
        ? "none"
        : `-${w} -${w} 0 #000, ${w} -${w} 0 #000, -${w} ${w} 0 #000, ${w} ${w} 0 #000, 0 2px 5px rgba(0,0,0,.7)`
    );
    const plate = settings.plate || "none";
    win.style.setProperty(
      "--subplate",
      plate === "hard" ? "rgba(0,0,0,.72)" : plate === "soft" ? "rgba(0,0,0,.4)" : "transparent"
    );
    win.style.setProperty("--subpad", plate === "none" ? "0" : "8px 14px");
    win.classList.toggle("solo-orig", settings.mode === "orig");
    orig.style.display = settings.mode === "trans" ? "none" : "";
    trans.style.display = settings.mode === "orig" ? "none" : "";
  }

  hint.textContent = shell.hint ?? "";
  const bar = shell.bar;
  if (!bar) {
    orig.textContent = "";
    trans.textContent = "";
    orig.className = "sub-orig";
    trans.className = "sub-trans";
  } else {
    orig.textContent = bar.orig;
    trans.textContent = bar.trans;
    orig.className = `sub-orig ${bar.kind}`;
    trans.className = `sub-trans ${bar.kind}`;
  }
  void fitHeightToText();
}

async function fitHeightToText() {
  if (fitInFlight || performance.now() < suppressFitUntil) return;
  const pad =
    parseFloat(getComputedStyle(win).paddingTop) + parseFloat(getComputedStyle(win).paddingBottom);
  const contentCss = Math.ceil(stack.offsetHeight + pad);
  const nextCss = nextOverlayHeight(win.clientHeight, contentCss, Math.round(screen.availHeight * 0.45));
  if (nextCss == null) return;
  fitInFlight = true;
  try {
    const factor = await overlay.scaleFactor();
    const inner = await overlay.innerSize();
    const pos = await overlay.outerPosition();
    const nextPhysical = Math.round(nextCss * factor);
    const dy = nextPhysical - inner.height;
    if (dy <= 2) return;
    await overlay.setSize(new PhysicalSize(inner.width, nextPhysical));
    await overlay.setPosition(new PhysicalPosition(pos.x, pos.y - dy));
  } catch {
    /* 改大小失败就维持现状，下一轮再试 */
  } finally {
    fitInFlight = false;
  }
}

setInterval(() => {
  shell = reduce(shell, { type: "tick", now: performance.now() });
  render();
}, 200);

void listen("subtitle://hover", (e) => {
  win.classList.toggle("hovered", e.payload === true);
});

document.getElementById("subHandles")!.addEventListener("pointerdown", (e) => {
  const handle = (e.target as HTMLElement).closest<HTMLElement>("[data-resize]");
  const dir = handle?.dataset.resize;
  if (!dir) return;
  e.preventDefault();
  suppressFitUntil = performance.now() + 1500;
  void overlay.startResizeDragging(
    dir as "North" | "South" | "East" | "West" | "NorthEast" | "NorthWest" | "SouthEast" | "SouthWest"
  );
});

async function main() {
  await listen("listen://event", (e) => {
    if (isListenEvent(e.payload)) {
      shell = reduce(shell, { type: "listen", event: e.payload, now: performance.now() });
      render();
    }
  });
  await listen("app://phase", (e) => {
    const p = e.payload as { phase: Phase; sourceLabel: string };
    shell = reduce(shell, { type: "phase", phase: p.phase, sourceLabel: p.sourceLabel });
    render();
  });
  await listen("app://source_switched", (e) => {
    const p = e.payload as { sourceLabel: string };
    // 提示计时与 tick 同基准（performance.now）；Rust 侧不传时间，纪元毫秒会把 hintUntil 推到永远到不了
    shell = reduce(shell, { type: "source_switched", sourceLabel: p.sourceLabel, now: performance.now() });
    render();
  });
  await listen("settings://changed", (e) => {
    settings = e.payload as Settings;
    render();
  });
  const boot = await getBootWhenReady();
  settings = boot.settings;
  render();
}

void main();
