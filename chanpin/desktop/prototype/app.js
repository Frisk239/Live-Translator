/* 直播同传工具 · 可交互原型（扔掉用）
   舞台 = 一台模拟电脑。直播画面在观众自己的浏览器窗口里，不归本产品。
   本产品只出现在：托盘图标、控制面板（可从托盘飞出、可拖）、字幕窗。
   假听译时间轴：原文/译文草稿往外长（可中途改）→ 定稿 → 切条 → 静默约两秒撤条。
   词汇只用 CONTEXT.md 的：音源 / 系统混音 / 开听 / 托盘 / 控制面板 / 字幕窗 /
   字幕条 / 草稿 / 定稿 / 切条 / 原文 / 译文 / 字幕模式 / 听译。 */

"use strict";

/* ================= 状态 ================= */

const state = {
  running: true,          // 产品整份是否在跑（从托盘退出 = false）
  panelOpen: true,
  phase: "idle",          // idle | downloading | listening | failed
  modelReady: true,
  dlPct: 0,
  scene: "en",
  source: "chrome.exe",
  mode: "both",           // both | orig | trans
  font: "m",              // s | m | l
  autostart: false,
  weak: false,
  sourceGone: false,      // 导演台：上次音源进程已退出
  permArmed: false,       // 导演台：音源抓不到（权限不够）
  crashArmed: false,      // 导演台：听译挂了
  status: { kind: "info", text: "选一个音源，按开听。" },
  variant: "a",
};

const SOURCES = [
  { id: "chrome.exe",  name: "chrome.exe",  desc: "Chrome · 正在出声", level: 62 },
  { id: "discord.exe", name: "discord.exe", desc: "Discord · 语音",     level: 34 },
  { id: "spotify.exe", name: "spotify.exe", desc: "Spotify · 音乐",     level: 16 },
  { id: "system",      name: "系统混音",     desc: "喇叭里正在响的全部声音", level: 100, system: true },
];

const FONT_PX = { s: 24, m: 30, l: 38 };

/* ================= 假听译脚本 =================
   cue: o / x = [时刻, 草稿快照]；fin = 定稿时刻；gap = 定稿后到下一条的静默。
   gap > 2000 演示「静默约两秒撤条」。同一行快照前后不同 = 草稿中途改。 */

const SCRIPTS = {
  en: {
    lang: "英",
    cues: [
      {
        o: [[0, "so we"], [600, "so we're gonna"], [1300, "so we're gonna try this"], [2200, "so we're gonna try this boss fight"]],
        x: [[1000, "我们"], [1800, "我们打算"], [2600, "我们打算试试"], [3400, "我们打算试试这个"], [4300, "我们打算试试这个 Boss 战"]],
        fin: 4800, gap: 1300,
      },
      {
        o: [[0, "he's got like"], [700, "he's got like three hundred"], [1600, "he's got like three hundred HP... wait"], [2500, "he's got like three thousand HP"]],
        x: [[900, "他有大概"], [1700, "他有大概三百"], [2500, "他有大概三百血"], [3400, "他有大概三千血"]],
        fin: 4000, gap: 1000,
      },
      {
        o: [[0, "nope, that's not"], [800, "nope, that's not gonna work"]],
        x: [[1000, "不行"], [1800, "不行，这样"], [2700, "不行，这样行不通"]],
        fin: 3300, gap: 2700,
      },
      {
        o: [[0, "okay let's"], [700, "okay let's regroup"], [1500, "okay let's regroup and try again"]],
        x: [[900, "好，"], [1700, "好，我们重整"], [2600, "好，我们重整再来一次"]],
        fin: 3100, gap: 1200,
      },
      {
        o: [[0, "chat, what do you think"], [900, "chat, what do you think about this build"]],
        x: [[1100, "你们"], [1900, "你们觉得"], [2800, "你们觉得这套构筑"], [3700, "你们觉得这套构筑怎么样"]],
        fin: 4200, gap: 1500,
      },
      {
        o: [[0, "alright,"], [600, "alright, back to"], [1400, "alright, back to the grind"]],
        x: [[800, "好，"], [1500, "好，回去"], [2300, "好，回去继续肝"]],
        fin: 2900, gap: 1700,
      },
    ],
  },

  ja: {
    lang: "日",
    cues: [
      {
        o: [[0, "あの、"], [600, "あの、今日は"], [1400, "あの、今日は配信を"], [2300, "あの、今日は配信を見てくれて"]],
        x: [[900, "那个，"], [1700, "那个，今天"], [2600, "那个，谢谢大家"], [3500, "那个，谢谢大家今天来看直播"]],
        fin: 4000, gap: 1400,
      },
      {
        o: [[0, "ちょっと"], [700, "ちょっと待って"], [1500, "ちょっと待ってください"]],
        x: [[900, "稍等"], [1800, "请稍等一下"]],
        fin: 2400, gap: 1600,
      },
      {
        o: [[0, "この武器が"], [800, "この武器が強くて"], [1600, "この武器が強くてびっくりした"]],
        x: [[1000, "这把武器"], [1900, "这把武器强到"], [2800, "这把武器强到吓我一跳"]],
        fin: 3400, gap: 2900,
      },
    ],
  },

  /* 连珠炮：跟嘴切不过来，按硬切换条（约 16 字 / 6 秒，先到为准），不完美也换 */
  rapid: {
    lang: "英",
    cues: [
      {
        o: [[0, "and then we just go"], [500, "and then we just go around from here"]],
        x: [[250, "然后我们就"], [650, "然后我们就直接从这边绕过去"]],
        fin: 1000, gap: 160,
      },
      {
        o: [[0, "'cause there's no way"], [500, "'cause there's no way they expect us here"]],
        x: [[250, "因为对面"], [650, "因为对面肯定想不到我们会走这边"]],
        fin: 1000, gap: 160,
      },
      {
        o: [[0, "so as long as we don't"], [500, "so as long as we don't fight we're fine"]],
        x: [[250, "所以只要"], [650, "所以只要不打起来我们就稳了"]],
        fin: 1000, gap: 160,
      },
      {
        o: [[0, "you know what"], [450, "you know what I mean"]],
        x: [[250, "懂我"], [600, "懂我意思吧"]],
        fin: 950, gap: 1400,
      },
    ],
  },

  pause: {
    lang: "英",
    cues: [
      {
        o: [[0, "let me think"], [700, "let me think for a second"]],
        x: [[800, "让我"], [1500, "让我想一下"]],
        fin: 2300, gap: 3400,
      },
      {
        o: [[0, "okay,"], [500, "okay, got it"]],
        x: [[700, "好，"], [1200, "好，有了"]],
        fin: 1900, gap: 3400,
      },
    ],
  },

  silence: {
    lang: "英",
    cues: [],
    onStart() {
      return [[2600, () => setStatus("info", "在听 · 还没听到人声")]];
    },
  },

  music: {
    lang: "？",
    cues: [],
    onStart() {
      return [
        [1600, () => { showHint("听到声音，但不是英 / 日 / 韩的人声", 3800); }],
        [1600, () => setStatus("warn", "在听 · 没听出英 / 日 / 韩的人声")],
      ];
    },
  },
};

/* ================= DOM ================= */

const $ = (id) => document.getElementById(id);
const os = $("os"), panel = $("panel"), panelBody = $("panelBody");
const subWin = $("subtitleWin"), subOrig = $("subOrig"), subTrans = $("subTrans"), subHint = $("subHint");
const trayOur = $("trayOur"), trayMenu = $("trayMenu"), toast = $("toast");
const readout = $("readout"), quitOverlay = $("quitOverlay");
const swLabel = $("swLabel"), startMenu = $("startMenu");
const browserWin = $("browserWin"), tbBrowser = $("tbBrowser");

/* ================= 字幕窗 ================= */

function clearBar() {
  subOrig.textContent = ""; subTrans.textContent = ""; subHint.textContent = "";
  subOrig.className = "sub-orig"; subTrans.className = "sub-trans";
  renderReadout();
}

function showDraft(bar) {
  subOrig.textContent = bar.o || "";
  subTrans.textContent = bar.x || "";
  subOrig.className = "sub-orig draft";
  subTrans.className = "sub-trans draft";
  renderReadout();
}

function showFinal(bar) {
  subOrig.textContent = bar.o || "";
  subTrans.textContent = bar.x || "";
  subOrig.className = "sub-orig final";
  subTrans.className = "sub-trans final";
  renderReadout();
}

function showHint(text, ms) {
  subHint.textContent = text;
  if (ms) engine.timers.push(setTimeout(() => { if (subHint.textContent === text) subHint.textContent = ""; }, ms));
}

/* 只换显示层，不动正在长的字 */
function reflowBarForMode() {
  subWin.classList.toggle("solo", state.mode === "orig");
  subOrig.style.display = state.mode === "trans" ? "none" : "";
  subTrans.style.display = state.mode === "orig" ? "none" : "";
}

/* ================= 假听译引擎 ================= */

const engine = {
  timers: [],
  bar: { o: "", x: "" },
  play(sceneKey) {
    this.stop();
    const scene = SCRIPTS[sceneKey];
    if (!scene) return;
    if (scene.onStart) scene.onStart().forEach(([t, fn]) => this.timers.push(setTimeout(fn, t)));
    const run = (i) => this.cue(scene.cues[i % scene.cues.length], () => run(i + 1));
    if (scene.cues.length) run(0);
  },
  cue(cue, next) {
    const evs = [];
    cue.o.forEach(([t, txt]) => evs.push({ t, k: "o", txt }));
    cue.x.forEach(([t, txt]) => evs.push({ t, k: "x", txt }));
    evs.push({ t: cue.fin, k: "final" });
    evs.sort((a, b) => a.t - b.t);
    evs.forEach((ev) => this.timers.push(setTimeout(() => {
      if (ev.k === "o") { this.bar.o = ev.txt; showDraft(this.bar); }
      if (ev.k === "x") { this.bar.x = ev.txt; showDraft(this.bar); }
      if (ev.k === "final") {
        showFinal(this.bar);
        /* 静默约两秒撤条；下一条来得早就立刻挤掉，不淡出 */
        this.timers.push(setTimeout(() => { clearBar(); this.bar = { o: "", x: "" }; }, 2000));
        this.timers.push(setTimeout(next, cue.gap));
      }
    }, ev.t)));
  },
  stop() {
    this.timers.forEach(clearTimeout);
    this.timers = [];
    this.bar = { o: "", x: "" };
    clearBar();
  },
};

/* ================= 通用渲染 ================= */

function setStatus(kind, text) { state.status = { kind, text }; renderPanel(); renderReadout(); }

function sourceById(id) { return SOURCES.find((s) => s.id === id); }
function sourceDead(s) { return state.sourceGone && s && !s.system && s.id === "chrome.exe"; }
function sourceLabel() {
  const s = sourceById(state.source);
  if (!s) return "（没选）";
  return s.system ? "系统混音" : s.name;
}
function langArrow() {
  const sc = SCRIPTS[state.scene];
  return sc && sc.lang && sc.lang !== "？" ? sc.lang + " → 中" : "";
}

function statusLineHTML() {
  const st = state.status;
  const cls = st.kind === "ok" ? "ok" : st.kind === "err" ? "err" : st.kind === "warn" ? "warn" : "";
  return `<div class="stat ${cls}"><span class="st-dot"></span><span>${st.text}</span></div>`;
}

function weakNoteHTML() {
  if (!state.weak) return "";
  return `<div class="note warn">这台机器算力偏弱，字幕可能出现得慢一些，但还能用。</div>`;
}

function permNoteHTML() {
  if (!(state.phase === "failed" && state.permArmed)) return "";
  return `<div class="note err">${sourceLabel()} 抓不到声音（权限不够）。换个音源，或改用系统混音兜底。
    <div class="note-acts">
      <button data-act="use-system">改用系统混音</button>
      <button data-act="retry">重试</button>
    </div></div>`;
}

function sourceBlockHTML(withMeters) {
  const rows = SOURCES.map((s) => {
    const dead = sourceDead(s);
    const sel = state.source === s.id && !dead ? "sel" : "";
    const meter = withMeters && !s.system ? `<span class="meter"><i style="width:${s.level}%"></i></span>` : "";
    return `<button class="src-row ${sel} ${dead ? "dead" : ""} ${s.system ? "system-row" : ""}" data-act="src" data-id="${s.id}" ${dead ? "disabled" : ""}>
      <span class="src-radio"></span>
      <span class="src-main"><span class="src-name">${s.name}</span><span class="src-desc" style="display:block">${s.desc}</span></span>
      ${dead ? '<span class="src-tag">已退出</span>' : meter}
    </button>`;
  }).join("");
  return `<div class="blk">
    <div class="blk-cap">音源（正在出声的应用程序）<button class="mini-btn" data-act="refresh">刷新</button></div>
    <div class="src-list">${rows}</div>
  </div>`;
}

function modeSegHTML() {
  const items = [["both", "双语"], ["orig", "仅原文"], ["trans", "仅译文"]];
  return `<div class="blk"><div class="blk-cap">字幕模式</div>
    <div class="seg">${items.map(([v, t]) =>
      `<button data-act="mode" data-v="${v}" class="${state.mode === v ? "sel" : ""}">${t}</button>`).join("")}</div></div>`;
}

function fontSegHTML() {
  const items = [["s", "小"], ["m", "中"], ["l", "大"]];
  return `<div class="blk"><div class="blk-cap">字幕字号（建议新增，未定）</div>
    <div class="seg">${items.map(([v, t]) =>
      `<button data-act="font" data-v="${v}" class="${state.font === v ? "sel" : ""}">${t}</button>`).join("")}</div></div>`;
}

function autostartHTML() {
  return `<button class="chk ${state.autostart ? "on" : ""}" data-act="autostart">
    <span class="box"></span>
    <span>开机自启<small>开机只进托盘，不会自动开听</small></span></button>`;
}

function dlBlockHTML() {
  const eta = state.dlPct < 30 ? "约还需 4 分钟" : state.dlPct < 70 ? "约还需 2 分钟" : "快好了";
  return `<div class="dl">
    <div class="dl-line">正在下载听译模型 <b>${Math.round(state.dlPct)}%</b> · ${eta}</div>
    <div class="dl-bar"><i style="width:${state.dlPct}%"></i></div>
    <div class="dl-sub">面板先能用。装完自动能听。</div>
  </div>`;
}

function primaryBtnHTML() {
  if (state.phase === "downloading") return dlBlockHTML();
  const dead = sourceDead(sourceById(state.source)) || !state.source;
  if (state.phase === "listening") {
    return `<button class="btn-main stop" data-act="startstop">停止</button>`;
  }
  return `<button class="btn-main" data-act="startstop" ${dead || !state.modelReady || !state.running ? "disabled" : ""}>
    ${dead ? "先选一个音源" : "开听"}</button>`;
}

/* ================= 三个控制面板变体（结构不同，可切换） ================= */

function panelA() {
  return `
    ${statusLineHTML()}<div style="height:10px"></div>
    ${weakNoteHTML()}${permNoteHTML()}
    ${sourceBlockHTML(false)}
    ${modeSegHTML()}
    ${fontSegHTML()}
    <div class="blk">${autostartHTML()}</div>
    ${primaryBtnHTML()}`;
}

function panelB() {
  return `
    ${statusLineHTML()}<div style="height:12px"></div>
    ${weakNoteHTML()}${permNoteHTML()}
    <div class="cols">
      <div>${sourceBlockHTML(true)}</div>
      <div>${modeSegHTML()}${fontSegHTML()}
        <div class="blk">${autostartHTML()}</div>
        ${primaryBtnHTML()}
      </div>
    </div>`;
}

function panelC() {
  const big = state.phase === "listening" ? "在听" : state.phase === "downloading" ? "在装听译模型" : "没在听";
  const sub = state.phase === "listening"
    ? `${sourceLabel()} · ${langArrow()}`
    : state.phase === "downloading" ? "面板先能用，装完自动能听" : "选音源，按开听";
  return `
    <div class="hero-stat">
      <div class="hs-big ${state.phase === "listening" ? "listening" : ""}">${big}</div>
      <div class="hs-sub">${sub}</div>
      ${primaryBtnHTML()}
    </div>
    ${weakNoteHTML()}${permNoteHTML()}
    <details class="adjust" open>
      <summary>调整音源和字幕</summary>
      <div style="padding-top:6px">
        ${sourceBlockHTML(false)}
        ${modeSegHTML()}
        ${fontSegHTML()}
        ${autostartHTML()}
      </div>
    </details>`;
}

const VARIANTS = {
  a: { name: "A · 紧凑单列", render: panelA, wide: false },
  b: { name: "B · 双栏带电平", render: panelB, wide: true },
  c: { name: "C · 状态卡加折叠", render: panelC, wide: false },
};

function renderPanel() {
  const v = VARIANTS[state.variant];
  panel.classList.toggle("wide", !!v.wide);
  panel.classList.toggle("hidden", !state.panelOpen || !state.running);
  panelBody.innerHTML = v.render();
}

panelBody.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (!el || el.disabled) return;
  const act = el.dataset.act;
  if (act === "src") changeSource(el.dataset.id);
  else if (act === "refresh") refreshSources();
  else if (act === "mode") { state.mode = el.dataset.v; reflowBarForMode(); renderPanel(); renderReadout(); }
  else if (act === "font") { state.font = el.dataset.v; applyFont(); renderPanel(); renderReadout(); }
  else if (act === "autostart") { state.autostart = !state.autostart; renderPanel(); renderTrayMenu(); renderReadout(); }
  else if (act === "startstop") { state.phase === "listening" ? stopListening() : startListening(); }
  else if (act === "use-system") {
    state.permArmed = false; state.phase = "idle";
    changeSource("system");
    setStatus("info", "音源换成系统混音了，可以开听。");
  }
  else if (act === "retry") { state.permArmed = false; state.phase = "idle"; renderPanel(); startListening(); }
});

function applyFont() { subWin.style.setProperty("--subsize", FONT_PX[state.font] + "px"); }

function refreshSources() {
  SOURCES.forEach((s) => { if (!s.system) s.level = 8 + Math.round(Math.random() * 85); });
  renderPanel();
}

/* ================= 开听 / 停止 / 换音源 ================= */

function startListening() {
  if (!state.running || state.phase === "downloading") return;
  const s = sourceById(state.source);
  if (!s || sourceDead(s)) { setStatus("err", "先选一个能出声的音源。"); return; }
  if (!state.modelReady) return;

  if (state.permArmed) {
    state.phase = "failed";
    setStatus("err", `${sourceLabel()} 抓不到声音（权限不够）。`);
    renderTray(); renderReadout();
    return;
  }
  state.phase = "listening";
  clearBar();
  engine.play(state.scene);
  setStatus("ok", `在听 · ${sourceLabel()} · ${langArrow()}`);
  renderTray(); renderReadout();

  if (state.crashArmed) {
    engine.timers.push(setTimeout(crashNow, 4500));
    state.crashArmed = false;
    markDirBtns();
  }
}

function stopListening(silent) {
  engine.stop();
  state.phase = "idle";
  if (!silent) setStatus("info", "没在听。选一个音源，按开听。");
  renderTray(); renderPanel(); renderReadout();
}

function crashNow() {
  engine.stop();
  state.phase = "failed";
  setStatus("err", "听译停了。点开听重试。");
  showToast("直播同传工具", "听译停了，字幕先撤了。点托盘图标回控制面板重试。");
  renderTray(); renderReadout();
}

/* 开听中换音源：建议立刻切到新音源，不停 */
function changeSource(id) {
  const s = sourceById(id);
  if (!s || sourceDead(s)) return;
  const wasListening = state.phase === "listening";
  state.source = id;
  if (wasListening) {
    engine.stop();
    state.phase = "listening";
    engine.play(state.scene);
    setStatus("ok", `在听 · ${sourceLabel()} · ${langArrow()}`);
    showHint("已换音源，继续在听", 1600);
  } else if (state.phase === "failed" && state.permArmed) {
    state.permArmed = false; state.phase = "idle";
    setStatus("info", `音源换成 ${sourceLabel()}，可以开听。`);
  }
  renderPanel(); renderReadout();
}

/* ================= 托盘 ================= */

function renderTray() {
  trayOur.className = "tb-tray-our " +
    (!state.running ? "quit-hidden " : "") +
    (state.phase === "listening" ? "listing" : state.phase === "failed" ? "failed" : "");
}

function renderTrayMenu() {
  const listenLabel = state.phase === "listening" ? "停止" : state.phase === "downloading" ? "正在装模型…" : "开听";
  trayMenu.innerHTML = `
    <button data-act="tm-open">打开控制面板</button>
    <button data-act="tm-toggle" ${state.phase === "downloading" || !state.running ? "disabled" : ""}>${listenLabel}</button>
    <button class="tm-chk ${state.autostart ? "on" : ""}" data-act="tm-autostart">开机自启<span class="tm-mark">✓</span></button>
    <div class="tm-sep"></div>
    <button class="tm-quit" data-act="tm-quit">退出</button>`;
}

trayOur.addEventListener("click", () => {
  if (!state.running) return;
  state.panelOpen = !state.panelOpen;
  trayMenu.classList.add("hidden");
  renderPanel(); renderReadout();
});

trayOur.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  e.stopPropagation();
  if (!state.running) return;
  renderTrayMenu();
  trayMenu.classList.remove("hidden");
});

trayMenu.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (!el || el.disabled) return;
  const act = el.dataset.act;
  trayMenu.classList.add("hidden");
  if (act === "tm-open") { state.panelOpen = true; renderPanel(); renderReadout(); }
  else if (act === "tm-toggle") {
    if (state.phase === "listening") stopListening();
    else { state.panelOpen = true; renderPanel(); startListening(); }
  }
  else if (act === "tm-autostart") { state.autostart = !state.autostart; renderPanel(); renderTrayMenu(); renderReadout(); }
  else if (act === "tm-quit") quitAll();
});

os.addEventListener("pointerdown", (e) => {
  if (!trayMenu.classList.contains("hidden") && !e.target.closest("#trayMenu")) trayMenu.classList.add("hidden");
  if (!startMenu.classList.contains("hidden") && !e.target.closest("#startMenu")) startMenu.classList.add("hidden");
});

/* 桌面右键不出浏览器的真菜单，保持像一台电脑 */
os.addEventListener("contextmenu", (e) => e.preventDefault());

/* ================= 系统气泡 / 退出 ================= */

let toastTimer = null;
function showToast(title, text, ms = 4200) {
  toast.innerHTML = `<b>${title}</b>${text}`;
  toast.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add("hidden"), ms);
}

function quitAll() {
  engine.stop();
  state.running = false;
  state.panelOpen = false;
  state.phase = "idle";
  trayMenu.classList.add("hidden");
  toast.classList.add("hidden");
  trayOur.classList.add("quit-hidden");
  clearBar();
  renderPanel(); renderTray(); renderReadout();
  quitOverlay.classList.remove("hidden");
}

$("relaunchBtn").addEventListener("click", () => {
  quitOverlay.classList.add("hidden");
  state.running = true;
  state.phase = "idle";
  state.panelOpen = false;
  trayOur.classList.remove("quit-hidden");
  setStatus("info", "在托盘等着。点托盘图标开控制面板。");
  renderPanel(); renderTray(); renderReadout();
  showToast("直播同传工具", "已在托盘。开机自启也只到这里，不会自动开听。");
});

/* ================= 面板开合 ================= */

$("panelClose").addEventListener("click", () => {
  state.panelOpen = false;
  renderPanel(); renderReadout();
});

/* ================= 导演台（原型用） ================= */

const SCENES = {
  en: { label: "英语直播" }, ja: { label: "日语直播" }, rapid: { label: "连珠炮 · 硬切" },
  pause: { label: "停两秒 · 静默撤条" }, silence: { label: "很久没人声" }, music: { label: "音乐 · 非英日韩" },
  perm: { label: "权限不够" }, crash: { label: "听译挂了" },
};

document.querySelectorAll(".dir-btn").forEach((btn) => {
  btn.addEventListener("click", () => runScene(btn.dataset.scene));
});

function markDirBtns() {
  document.querySelectorAll(".dir-btn").forEach((b) => {
    const s = b.dataset.scene;
    const on = (SCENES[s] && s === state.scene) ||
      (s === "weak" && state.weak) || (s === "sourcegone" && state.sourceGone) ||
      (s === "perm" && state.permArmed) || (s === "crash" && state.crashArmed);
    b.classList.toggle("on", !!on);
  });
}

function runScene(key) {
  if (key === "weak") { state.weak = !state.weak; renderPanel(); }
  else if (key === "sourcegone") {
    state.sourceGone = !state.sourceGone;
    if (state.sourceGone) {
      if (state.phase === "listening" && state.source === "chrome.exe") {
        stopListening(true);
        setStatus("warn", "音源进程退出了。重新选一个，不会自动改用系统混音。");
      } else if (state.source === "chrome.exe") {
        setStatus("warn", "上次的音源已退出。重新选一个，不会自动改用系统混音。");
      }
    } else {
      setStatus("info", "选一个音源，按开听。");
    }
    renderPanel();
  }
  else if (key === "perm") {
    state.permArmed = !state.permArmed;
    if (state.permArmed) {
      if (state.phase === "listening") {
        engine.stop();
        state.phase = "failed";
        setStatus("err", `${sourceLabel()} 抓不到声音（权限不够）。`);
      } else {
        setStatus("warn", "已设定：下次开听时这个音源抓不到声音。");
      }
    } else {
      state.phase = "idle";
      setStatus("info", "选一个音源，按开听。");
    }
    renderTray(); renderPanel();
  }
  else if (key === "crash") {
    if (state.phase === "listening") { crashNow(); state.crashArmed = false; }
    else { state.crashArmed = !state.crashArmed; if (state.crashArmed) setStatus("warn", "已设定：下次开听听一会儿就挂。"); else setStatus("info", "选一个音源，按开听。"); }
  }
  else if (key === "firstrun") {
    stopListening(true);
    state.modelReady = false;
    state.phase = "downloading";
    state.dlPct = 0;
    setStatus("info", "第一次打开：正在下载听译模型。");
    renderPanel(); renderTray();
    const tick = setInterval(() => {
      state.dlPct = Math.min(100, state.dlPct + 1.4 + Math.random() * 1.4);
      if (state.dlPct >= 100) {
        clearInterval(tick);
        state.phase = "idle";
        state.modelReady = true;
        setStatus("ok", "模型装好了。选一个音源，按开听。");
        showToast("直播同传工具", "听译模型装好了，随时可以开听。");
      }
      renderPanel(); renderReadout();
    }, 260);
  }
  else {
    state.scene = key;
    if (state.phase === "listening") {
      engine.stop();
      clearBar();
      engine.play(key);
      setStatus("ok", `在听 · ${sourceLabel()} · ${langArrow()}`);
    } else {
      setStatus("info", `场景已切到「${SCENES[key].label}」，按开听生效。`);
    }
    renderPanel();
  }
  markDirBtns();
  renderReadout();
}

/* ================= 状态读出 ================= */

function renderReadout() {
  if (!readout) return;
  const barDesc = (() => {
    if (!subTrans.textContent && !subOrig.textContent && !subHint.textContent) return "无";
    if (subHint.textContent) return `提示「${subHint.textContent}」`;
    const line = state.mode === "orig" ? subOrig.textContent : subTrans.textContent || subOrig.textContent;
    const kind = subTrans.className.includes("draft") || subOrig.className.includes("draft") ? "草稿" : "定稿";
    return `${kind}「${(line || "").slice(0, 14)}」`;
  })();
  const phaseText = !state.running ? "已从托盘退出" :
    state.phase === "listening" ? "在听" : state.phase === "downloading" ? `下载模型 ${Math.round(state.dlPct)}%` :
    state.phase === "failed" ? "出错" : "没在听";
  const rows = [
    ["产品", phaseText],
    ["面板", state.panelOpen ? "显示" : "藏"],
    ["音源", sourceLabel() + (sourceDead(sourceById(state.source)) ? "（已退出）" : "")],
    ["字幕模式", { both: "双语", orig: "仅原文", trans: "仅译文" }[state.mode]],
    ["字号", { s: "小", m: "中", l: "大" }[state.font]],
    ["当前条", barDesc],
    ["场景", SCENES[state.scene] ? SCENES[state.scene].label : state.scene],
    ["开机自启", state.autostart ? "开" : "关"],
  ];
  readout.innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

/* ================= 桌面交互：窗口拖动 / 缩放 / 层级 / 图标 ================= */

let zTop = 10;
function bringToFront(win) { win.style.zIndex = ++zTop > 50 ? (zTop = 11) : zTop; }

/* 观众的浏览器窗口 */
(function () {
  let drag = null, size = null;
  $("bwTitlebar").addEventListener("pointerdown", (e) => {
    bringToFront(browserWin);
    if (e.target.closest("[data-bw]") || browserWin.classList.contains("max")) return;
    const rect = os.getBoundingClientRect(), wRect = browserWin.getBoundingClientRect();
    drag = { startX: e.clientX, startY: e.clientY, left: wRect.left - rect.left, top: wRect.top - rect.top };
    e.preventDefault();
  });
  $("bwTitlebar").addEventListener("pointermove", (e) => {
    if (!drag) return;
    const rect = os.getBoundingClientRect();
    const left = Math.max(0, Math.min(rect.width - 120, drag.left + e.clientX - drag.startX));
    const top = Math.max(0, Math.min(rect.height - 100, drag.top + e.clientY - drag.startY));
    browserWin.style.left = left + "px";
    browserWin.style.top = top + "px";
  });
  const endDrag = () => { drag = null; };
  $("bwTitlebar").addEventListener("pointerup", endDrag);
  $("bwTitlebar").addEventListener("pointercancel", endDrag);

  $("bwResize").addEventListener("pointerdown", (e) => {
    if (browserWin.classList.contains("max")) return;
    const rect = os.getBoundingClientRect(), wRect = browserWin.getBoundingClientRect();
    size = { startX: e.clientX, startY: e.clientY, w: wRect.width, h: wRect.height };
    $("bwResize").setPointerCapture(e.pointerId);
    e.preventDefault(); e.stopPropagation();
  });
  $("bwResize").addEventListener("pointermove", (e) => {
    if (!size) return;
    const w = Math.max(560, Math.min(size.w + e.clientX - size.startX, os.clientWidth - 20));
    const h = Math.max(380, Math.min(size.h + e.clientY - size.startY, os.clientHeight - 68));
    browserWin.style.width = w + "px";
    browserWin.style.height = h + "px";
  });
  const endSize = () => { size = null; };
  $("bwResize").addEventListener("pointerup", endSize);
  $("bwResize").addEventListener("pointercancel", endSize);
})();

browserWin.addEventListener("pointerdown", () => bringToFront(browserWin));

document.querySelectorAll("[data-bw]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const a = btn.dataset.bw;
    if (a === "max") browserWin.classList.toggle("max");
    else { browserWin.classList.add("hidden"); tbBrowser.classList.add("hidden-win"); }
  });
});
tbBrowser.addEventListener("click", () => {
  const hidden = browserWin.classList.contains("hidden");
  browserWin.classList.toggle("hidden", !hidden);
  tbBrowser.classList.toggle("hidden-win", hidden);
  if (hidden) bringToFront(browserWin);
});

/* 控制面板也能拖着走（按标题栏拖） */
(function () {
  let drag = null;
  const titlebar = panel.querySelector(".panel-titlebar");
  titlebar.addEventListener("pointerdown", (e) => {
    if (e.target.closest("#panelClose")) return;
    const rect = os.getBoundingClientRect(), pRect = panel.getBoundingClientRect();
    drag = { startX: e.clientX, startY: e.clientY, left: pRect.left - rect.left, top: pRect.top - rect.top };
    e.preventDefault();
  });
  window.addEventListener("pointermove", (e) => {
    if (!drag) return;
    panel.style.right = "auto"; panel.style.bottom = "auto";
    const rect = os.getBoundingClientRect();
    panel.style.left = Math.max(0, Math.min(rect.width - 200, drag.left + e.clientX - drag.startX)) + "px";
    panel.style.top = Math.max(0, Math.min(rect.height - 120, drag.top + e.clientY - drag.startY)) + "px";
  });
  const end = () => { drag = null; };
  window.addEventListener("pointerup", end);
  window.addEventListener("pointercancel", end);
})();

/* 开始菜单（布景） */
document.querySelector(".tb-start").addEventListener("click", (e) => {
  e.stopPropagation();
  startMenu.classList.toggle("hidden");
});
startMenu.addEventListener("pointerdown", (e) => e.stopPropagation());
startMenu.addEventListener("click", () => startMenu.classList.add("hidden"));

/* 桌面图标：点一个选中，点桌面空白取消 */
document.querySelectorAll(".desk-icon").forEach((ic) => {
  ic.addEventListener("pointerdown", (e) => e.stopPropagation());
  ic.addEventListener("click", (e) => {
    e.stopPropagation();
    document.querySelectorAll(".desk-icon").forEach((x) => x.classList.remove("sel"));
    ic.classList.add("sel");
  });
});
os.addEventListener("pointerdown", (e) => {
  if (!e.target.closest(".desk-icon")) document.querySelectorAll(".desk-icon").forEach((x) => x.classList.remove("sel"));
});

/* ================= 字幕窗：拖动 + 改大小（原型把手） ================= */

(function () {
  let drag = null;
  subWin.addEventListener("pointerdown", (e) => {
    if (!state.running) return;
    const rect = os.getBoundingClientRect();
    const wRect = subWin.getBoundingClientRect();
    if (e.target.closest(".sub-resize")) {
      drag = { mode: "size", startX: e.clientX, startW: wRect.width, rect };
    } else {
      drag = {
        mode: "move", startX: e.clientX, startY: e.clientY,
        startLeft: wRect.left - rect.left, startBottom: rect.bottom - wRect.bottom, rect,
      };
    }
    subWin.setPointerCapture(e.pointerId);
    e.preventDefault();
    e.stopPropagation();
  });
  subWin.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.startX;
    if (drag.mode === "size") {
      subWin.style.width = Math.max(drag.rect.width * 0.22, Math.min(drag.rect.width * 0.86, drag.startW + dx)) + "px";
    } else {
      const dy = e.clientY - drag.startY;
      const left = Math.max(0, Math.min(drag.rect.width - 80, drag.startLeft + dx));
      const bottom = Math.max(8, Math.min(drag.rect.height - 60, drag.startBottom - dy));
      subWin.style.left = (left / drag.rect.width * 100) + "%";
      subWin.style.bottom = bottom + "px";
    }
  });
  const end = () => { drag = null; };
  subWin.addEventListener("pointerup", end);
  subWin.addEventListener("pointercancel", end);
})();

/* ================= 变体切换（原型用） ================= */

const VARIANT_KEYS = ["a", "b", "c"];
function setVariant(v, push) {
  state.variant = v;
  swLabel.innerHTML = `面板变体 <b>${VARIANTS[v].name}</b>`;
  if (push) location.hash = v; /* file:// 下 replaceState 会被拒，直接写 hash */
  renderPanel();
}
$("swPrev").addEventListener("click", () => {
  const i = VARIANT_KEYS.indexOf(state.variant);
  setVariant(VARIANT_KEYS[(i + VARIANT_KEYS.length - 1) % VARIANT_KEYS.length], true);
});
$("swNext").addEventListener("click", () => {
  const i = VARIANT_KEYS.indexOf(state.variant);
  setVariant(VARIANT_KEYS[(i + 1) % VARIANT_KEYS.length], true);
});
document.addEventListener("keydown", (e) => {
  if (e.target.closest("input, textarea, [contenteditable]")) return;
  if (e.key === "ArrowLeft") $("swPrev").click();
  if (e.key === "ArrowRight") $("swNext").click();
});

/* ================= 导演台抽屉（原型用） ================= */

const drawer = $("drawer");
$("drawerToggle").addEventListener("click", () => drawer.classList.toggle("open"));
$("drawerGrip").addEventListener("click", () => drawer.classList.toggle("open"));

/* ================= 直播聊天栏（布景，持续滚动） ================= */

const CHAT_POOL = [
  ["kayin_fan22", "W game", "#8ad0ff"],
  ["boxmain", "no way he pulls this off", "#ffb3a0"],
  ["sleepylt", "chat he is cooking", "#b5e48c"],
  ["mossgreens", "first time here, this is great", "#e0a8ff"],
  ["pingspike", "that positioning was insane", "#ffd489"],
  ["lukewarm_takes", "gg go next", "#9fe8d8"],
  ["orbit_soma", "POG", "#8ad0ff"],
  ["dripgoblin", "the enemies just walked in LOL", "#ffb3a0"],
  ["nine_lives", "clip that", "#b5e48c"],
  ["fernwed", "rotation was clean", "#e0a8ff"],
];
(function () {
  const list = $("chatList");
  let i = 0;
  function addOne() {
    const [name, text, color] = CHAT_POOL[i % CHAT_POOL.length]; i++;
    const div = document.createElement("div");
    div.className = "chat-msg";
    const b = document.createElement("b");
    b.textContent = name; b.style.color = color;
    div.appendChild(b);
    div.appendChild(document.createTextNode(text));
    list.appendChild(div);
    while (list.children.length > 14) list.removeChild(list.firstChild);
    list.scrollTop = list.scrollHeight;
  }
  for (let k = 0; k < 6; k++) addOne();
  setInterval(addOne, 3800 + Math.random() * 2200);
})();

/* ================= 时钟 / 初始化 ================= */

setInterval(() => {
  const d = new Date();
  $("clockTime").textContent = `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  $("clockDate").textContent = `${d.getMonth() + 1}/${d.getDate()}`;
}, 1000);

applyFont();
reflowBarForMode();
setVariant((location.hash || "#a").slice(1) in VARIANTS ? (location.hash || "#a").slice(1) : "a", false);
renderPanel(); renderTray(); renderTrayMenu(); renderReadout(); markDirBtns();
/* 抽屉默认收起：开着会盖住右下角的控制面板 */

/* 音源电平表微动（变体 B 里看得见） */
setInterval(() => {
  SOURCES.forEach((s) => { if (!s.system) s.level = Math.max(6, Math.min(96, s.level + (Math.random() * 30 - 15))); });
  document.querySelectorAll(".meter i").forEach((el, i) => {
    const s = SOURCES.filter((x) => !x.system)[i];
    if (s) el.style.width = s.level + "%";
  });
}, 700);
