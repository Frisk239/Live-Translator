/* 直播同传 · 安卓可交互原型（扔掉用）
   舞台 = 一台模拟安卓手机。直播画面在「看播」app 里，不归本产品。
   本产品只出现在：悬浮字幕窗（叠在直播上）、常驻通知、本产品自己的 app。
   假听译时间轴沿用桌面原型：原文/译文草稿往外长（可中途改）→ 定稿 → 切条 → 静默约两秒撤条。
   词汇只用 CONTEXT.md 的：音源 / 开听 / 字幕窗 / 字幕条 / 草稿 / 定稿 / 切条 / 原文 / 译文 /
   字幕模式 / 托管听译 / 账号 / 登录 / 在听会话 / 顶号 / 满员 / 端。 */

"use strict";

/* ================= 状态 ================= */

const state = {
  stage: "ours",           // home | live | ours（手机里开着什么）
  screen: "login",         // login | main | profile（本产品 app 内的页）
  auth: null,              // 登录邮箱；null = 未登录
  remember: true,
  phase: "idle",           // idle | listening | failed
  fail: null,              // capture | full | net（failed 的原因，画进 note）
  scene: "en",
  source: "liveapp",
  mode: "both",            // both | orig | trans
  font: "m",               // s | m | l
  subColor: "w",           // w | y | c（建议，未定）
  subPad: "none",          // none | bar（建议，未定）
  fullArmed: false,        // 导演台：下次开听时满员
  captureArmed: false,     // 导演台：直播App 不让抓
  bumpHinted: false,
  status: { kind: "info", text: "登录后选音源，按开听。" },
  notices: [],             // 通知抽屉里的横幅历史（常驻的在听通知单算）
};

const SOURCES = [
  { id: "liveapp",  name: "看播（直播 App）", desc: "正在出声 · kayin_uk 的英语直播", level: 64 },
  { id: "videoapp", name: "短视频 App",       desc: "没在出声", level: 5 },
  { id: "allsound", name: "全部可抓的声音",   desc: "系统给的兜底 · 可抓的混在一起", system: true },
];

const FONT_PX = { s: 18, m: 21, l: 26 };

/* ================= 假听译脚本（与桌面原型同源） ================= */

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
const screen = $("screen");
const stages = { home: $("stHome"), live: $("stLive"), ours: $("stOurs") };
const oursBody = $("oursBody");
const subWin = $("subtitleWin"), subOrig = $("subOrig"), subTrans = $("subTrans"), subHint = $("subHint");
const sbCast = $("sbCast"), headsUp = $("headsUp"), shade = $("shade"), shadeList = $("shadeList");
const sysDlg = $("sysDlg");
const readout = $("readout");

/* ================= 字幕窗 ================= */

function clearBar() {
  subOrig.textContent = ""; subTrans.textContent = ""; subHint.textContent = "";
  subOrig.className = "sub-orig"; subTrans.className = "sub-trans";
  renderReadout();
}
function showDraft(bar) {
  subOrig.textContent = bar.o || "";
  subTrans.textContent = bar.x || "";
  subOrig.className = "sub-orig draft"; subTrans.className = "sub-trans draft";
  renderReadout();
}
function showFinal(bar) {
  subOrig.textContent = bar.o || "";
  subTrans.textContent = bar.x || "";
  subOrig.className = "sub-orig final"; subTrans.className = "sub-trans final";
  renderReadout();
}
function showHint(text, ms) {
  subHint.textContent = text;
  if (ms) engine.timers.push(setTimeout(() => { if (subHint.textContent === text) subHint.textContent = ""; }, ms));
}
function reflowBarForMode() {
  subWin.classList.toggle("solo", state.mode === "orig");
  subOrig.style.display = state.mode === "trans" ? "none" : "";
  subTrans.style.display = state.mode === "orig" ? "none" : "";
}
function applyFont() { subWin.style.setProperty("--subsize", FONT_PX[state.font] + "px"); }

/* 字幕自定义样式：颜色三选 + 底衬。位置由拖动改（拖到哪记到哪），复位清掉内联样式 */
function applySubStyle() {
  subWin.classList.toggle("c-y", state.subColor === "y");
  subWin.classList.toggle("c-c", state.subColor === "c");
  subWin.classList.toggle("pad-bar", state.subPad === "bar");
}
function resetSubPos() {
  subWin.style.left = ""; subWin.style.right = "";
  subWin.style.top = ""; subWin.style.bottom = "";
}

/* 悬浮字幕只在别的 app / 桌面上叠着；在本产品自己的界面里藏（建议，未拍板） */
function updateSubtitleVis() {
  subWin.classList.toggle("hidden", !(state.phase === "listening" && state.stage !== "ours"));
}

/* ================= 假听译引擎（与桌面原型同源） ================= */

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

/* ================= 通用 ================= */

function setStatus(kind, text) { state.status = { kind, text }; renderOurs(); renderReadout(); }
function sourceById(id) { return SOURCES.find((s) => s.id === id); }
function sourceLabel() {
  const s = sourceById(state.source);
  return s ? (s.system ? "全部可抓的声音" : s.name) : "（没选）";
}
function langArrow() {
  const sc = SCRIPTS[state.scene];
  return sc && sc.lang && sc.lang !== "？" ? sc.lang + " → 中" : "";
}

/* ================= 舞台切换 ================= */

function goStage(stage) {
  state.stage = stage;
  Object.entries(stages).forEach(([k, el]) => el.classList.toggle("hidden", k !== stage));
  /* 回到本产品时按当前状态重画：后台期间被顶、满员、断网的变化都还在 */
  if (stage === "ours") renderOurs();
  updateSubtitleVis();
  markDirBtns(); renderReadout();
}

$("launchLive").addEventListener("click", () => goStage("live"));
$("launchOurs").addEventListener("click", () => goStage("ours"));
$("dockLive").addEventListener("click", () => goStage("live"));
$("dockOurs").addEventListener("click", () => goStage("ours"));
$("gestureBar").addEventListener("click", () => goStage("home"));

/* ================= 本产品 app 三个页 ================= */

function statusLineHTML() {
  const st = state.status;
  const cls = st.kind === "ok" ? "ok" : st.kind === "err" ? "err" : st.kind === "warn" ? "warn" : "";
  return `<div class="stat ${cls}"><span class="st-dot"></span><span>${st.text}</span></div>`;
}

function noteHTML() {
  if (state.phase !== "failed") return "";
  if (state.fail === "capture") {
    return `<div class="note err">看播（直播 App）不允许抓它的声音。试试「全部可抓的声音」，或在电脑上开听。
      <div class="note-acts">
        <button data-act="use-allsound">改用全部可抓的声音</button>
        <button data-act="retry">重试</button>
      </div></div>`;
  }
  if (state.fail === "full") {
    return `<div class="note err">现在满员：同时开听的路数已到上限，稍后再试。已开听的不受影响。</div>`;
  }
  if (state.fail === "net") {
    return `<div class="note err">网络断了，已停听。网络好了再按开听。</div>`;
  }
  return "";
}

function sourceBlockHTML() {
  const rows = SOURCES.map((s) => {
    const sel = state.source === s.id ? "sel" : "";
    const right = s.system ? '<span class="src-tag">兜底</span>'
      : `<span class="meter"><i style="width:${s.level}%"></i></span>`;
    return `<button class="src-row ${sel}" data-act="src" data-id="${s.id}">
      <span class="src-radio"></span>
      <span class="src-main"><span class="src-name">${s.name}</span><span class="src-desc">${s.desc}</span></span>
      ${right}
    </button>`;
  }).join("");
  return `<div class="blk">
    <div class="blk-cap">音源（手机上的形态待抓音验证）<button class="mini-btn" data-act="refresh">刷新</button></div>
    <div class="src-list">${rows}</div>
  </div>`;
}

function segHTML(cap, items, cur, act) {
  return `<div class="blk"><div class="blk-cap">${cap}</div>
    <div class="seg">${items.map(([v, t]) =>
      `<button data-act="${act}" data-v="${v}" class="${cur === v ? "sel" : ""}">${t}</button>`).join("")}</div></div>`;
}

function primaryBtnHTML() {
  if (state.phase === "listening") return `<button class="btn-main stop" data-act="startstop">停止</button>`;
  const dead = !state.source;
  return `<button class="btn-main" data-act="startstop" ${dead ? "disabled" : ""}>${dead ? "先选一个音源" : "开听"}</button>`;
}

function renderLogin() {
  return `
    <div class="login-hero">
      <div class="l-name">直播同传</div>
      <div class="l-sub">在手机上看外语直播，字幕叠在画面上。<br>听译跑在我们机器上（托管听译），手机只抓音、出字幕。</div>
    </div>
    <form id="loginForm">
      <input class="field" type="email" id="mailIn" placeholder="邮箱" value="demo@example.com" required>
      <input class="field" type="password" id="pwdIn" placeholder="密码" value="········" required>
      <button class="chk ${state.remember ? "on" : ""}" type="button" data-act="remember">
        <span class="box"></span><span>记住我<small>不勾：杀掉 App 就要重新登录</small></span>
      </button>
      <div class="login-row">
        <button class="btn-main" type="submit">登录</button>
        <button class="btn-main" type="button" data-act="register" style="background:var(--panel-2);color:var(--text);border:1px solid var(--line)">注册</button>
      </div>
    </form>
    <div class="login-note">登录只为开托管听译。登录和注册同一屏：新邮箱填好直接点注册。</div>`;
}

function renderMain() {
  return `
    <div class="ours-head">
      <span class="ours-logo"><i class="bar3"></i></span>
      <span class="ours-name">直播同传</span>
      <button class="ours-mail" data-act="profile">${state.auth}</button>
    </div>
    <div style="height:4px"></div>
    ${statusLineHTML()}
    <div style="height:10px"></div>
    ${noteHTML()}
    ${sourceBlockHTML()}
    ${segHTML("字幕模式", [["both", "双语"], ["orig", "仅原文"], ["trans", "仅译文"]], state.mode, "mode")}
    ${segHTML("字幕字号（建议，未定）", [["s", "小"], ["m", "中"], ["l", "大"]], state.font, "font")}
    ${segHTML("字幕颜色（建议，未定）", [["w", "白"], ["y", "黄"], ["c", "青"]], state.subColor, "subcolor")}
    ${segHTML("字幕底衬（建议，未定）", [["none", "无"], ["bar", "半透明黑条"]], state.subPad, "subpad")}
    <div class="blk">
      <div class="blk-cap">悬浮字幕位置<button class="mini-btn" data-act="resetpos">回到默认</button></div>
      <div style="font-size:11px;color:var(--faint)">字幕窗可拖到屏幕任意位置，拖到哪就记到哪。</div>
    </div>
    ${primaryBtnHTML()}
    <div class="ours-foot">手机版只做托管听译。同一账号同时只能一路在听：这边开了，电脑那边会被顶掉，反之一样。</div>`;
}

function renderProfile() {
  return `
    <div class="ours-head">
      <button class="ours-back" data-act="back">‹</button>
      <span class="ours-title">个人中心</span>
    </div>
    <div class="prof-card">
      <div class="prof-cap">账号</div>
      <div class="prof-mail">${state.auth}</div>
    </div>
    <div class="prof-card">
      <div class="prof-cap">改密码</div>
      <input class="field" type="password" placeholder="现在的密码">
      <input class="field" type="password" placeholder="新密码">
      <input class="field" type="password" placeholder="再输一遍新密码">
      <button class="btn-main" data-act="pwd">改密码</button>
      <div class="login-note" id="pwdNote" style="margin-top:9px"></div>
    </div>
    <button class="btn-ghost" data-act="logout">退出登录</button>`;
}

function renderOurs() {
  if (state.stage !== "ours") return;
  if (!state.auth) { state.screen = "login"; oursBody.innerHTML = renderLogin(); }
  else if (state.screen === "profile") oursBody.innerHTML = renderProfile();
  else { state.screen = "main"; oursBody.innerHTML = renderMain(); }
}

/* 本产品 app 内的点击（事件委托） */
oursBody.addEventListener("click", (e) => {
  const el = e.target.closest("[data-act]");
  if (!el || el.disabled) return;
  const act = el.dataset.act;
  if (act === "src") { state.source = el.dataset.id; renderOurs(); renderReadout(); }
  else if (act === "refresh") {
    SOURCES.forEach((s) => { if (!s.system) s.level = 8 + Math.round(Math.random() * 85); });
    renderOurs();
  }
  else if (act === "mode") { state.mode = el.dataset.v; reflowBarForMode(); renderOurs(); renderReadout(); }
  else if (act === "font") { state.font = el.dataset.v; applyFont(); renderOurs(); renderReadout(); }
  else if (act === "subcolor") { state.subColor = el.dataset.v; applySubStyle(); renderOurs(); renderReadout(); }
  else if (act === "subpad") { state.subPad = el.dataset.v; applySubStyle(); renderOurs(); renderReadout(); }
  else if (act === "resetpos") { resetSubPos(); showHint("字幕位置已回到默认", 1500); }
  else if (act === "startstop") { state.phase === "listening" ? stopListening() : askToStart(); }
  else if (act === "profile") { state.screen = "profile"; renderOurs(); }
  else if (act === "back") { state.screen = "main"; renderOurs(); }
  else if (act === "logout") { doLogout(); }
  else if (act === "remember") { state.remember = !state.remember; renderOurs(); renderReadout(); }
  else if (act === "pwd") {
    const note = $("pwdNote");
    if (note) note.textContent = "已改。其它端的登录都下线了，这台手机还登着。";
    showHeadsUp("直播同传", "密码已改", "其它端需要重新登录。这台还登着。", true);
  }
  else if (act === "use-allsound") {
    state.captureArmed = false; state.fail = null; state.phase = "idle";
    state.source = "allsound";
    setStatus("info", "音源换成「全部可抓的声音」了，可以开听。");
  }
  else if (act === "retry") {
    state.captureArmed = false; state.fail = null; state.phase = "idle";
    renderOurs(); askToStart();
  }
});

/* 登录表单：提交即登录（注册按钮也是登录，新邮箱当新账号） */
oursBody.addEventListener("submit", (e) => {
  if (e.target.id !== "loginForm") return;
  e.preventDefault();
  doLogin($("mailIn").value.trim() || "demo@example.com");
});
oursBody.addEventListener("click", (e) => {
  const el = e.target.closest('[data-act="register"]');
  if (!el) return;
  doLogin($("mailIn").value.trim() || "demo@example.com", true);
});

function doLogin(email, isNew) {
  state.auth = email;
  state.screen = "main";
  state.phase = "idle"; state.fail = null;
  setStatus("info", isNew ? "账号建好了，已登录。选音源，按开听。" : "已登录。选音源，按开听。");
  renderOurs();
  showHeadsUp("直播同传", isNew ? "账号建好了" : "已登录", "托管听译已解锁。同一账号同时只能一路在听。", true);
}

function doLogout() {
  if (state.phase === "listening") stopListening(true);
  state.auth = null; state.screen = "login"; state.notices = [];
  renderShade(); renderOurs(); renderReadout();
}

/* ================= 开听：先过系统的媒体投影授权 ================= */

function askToStart() {
  if (!state.auth) { goStage("ours"); setStatus("warn", "先登录，才能开托管听译。"); return; }
  if (state.phase === "listening") { stopListening(); return; }
  sysDlg.classList.remove("hidden");
}
$("sysCancel").addEventListener("click", () => {
  sysDlg.classList.add("hidden");
  setStatus("warn", "没开成：授权被取消了。");
});
$("sysStart").addEventListener("click", () => {
  sysDlg.classList.add("hidden");

  /* 满员在握手里判定：路数到上限，新的开听被拒并说明 */
  if (state.fullArmed) {
    state.fullArmed = false;
    state.phase = "failed"; state.fail = "full";
    setStatus("err", "没开成：现在满员。");
    showHeadsUp("直播同传", "现在满员", "同时开听的路数已到上限。已开听的不受影响，稍后再试。", true);
    markDirBtns(); renderReadout();
    return;
  }

  state.phase = "listening"; state.fail = null;
  sbCast.classList.remove("hidden");
  renderShade();
  clearBar();
  engine.play(state.scene);
  setStatus("ok", `在听 · ${sourceLabel()} · ${langArrow()}`);
  showHint("已开听 · 去看你的直播，字幕叠在上面", 2600);
  updateSubtitleVis();

  /* 开听后自动去看播（建议，未拍板：也可以留在本产品里） */
  setTimeout(() => { if (state.phase === "listening") goStage("live"); }, 850);

  /* 抓不到声音：该 App 不让抓（导演台装的雷） */
  if (state.captureArmed && state.source === "liveapp") {
    engine.timers.push(setTimeout(() => {
      engine.stop();
      state.phase = "failed"; state.fail = "capture";
      state.captureArmed = false;
      setStatus("err", "看播（直播 App）不允许抓它的声音。");
      showHint("这个 App 不让抓声音", 3200);
      markDirBtns(); renderReadout(); updateSubtitleVis();
    }, 1400));
  }
  markDirBtns(); renderReadout();
});

function stopListening(silent) {
  engine.stop();
  state.phase = "idle"; state.fail = null;
  sbCast.classList.add("hidden");
  renderShade();
  updateSubtitleVis();
  if (!silent) setStatus("info", "没在听。选音源，按开听。");
  renderOurs(); renderReadout();
}

/* ================= 通知：横幅 + 抽屉 ================= */

let huTimer = null;
function showHeadsUp(app, title, text, ours, ms = 4600) {
  headsUp.innerHTML = `
    <div class="hu-app ${ours ? "ours" : ""}"><span class="hu-dot"></span>${app}<span style="margin-left:auto">现在</span></div>
    <div class="hu-title">${title}</div>
    <div class="hu-text">${text}</div>`;
  headsUp.classList.remove("away");
  clearTimeout(huTimer);
  huTimer = setTimeout(() => headsUp.classList.add("away"), ms);
  state.notices.unshift({ app, title, text, ours });
  if (state.notices.length > 3) state.notices.length = 3;
  renderShade();
}

function renderShade() {
  const persist = state.phase === "listening" ? `
    <div class="notif persist">
      <div class="n-app"><span>直播同传</span><span>现在</span></div>
      <div class="n-title">正在听译 · ${sourceLabel()}</div>
      <div class="n-text">字幕正叠在你的直播上。点开直播同传可以改字幕模式。</div>
      <button class="n-act" data-shade="stop">停止</button>
    </div>` : "";
  const list = state.notices.map((n) => `
    <div class="notif">
      <div class="n-app"><span>${n.app}</span><span>稍早</span></div>
      <div class="n-title">${n.title}</div>
      <div class="n-text">${n.text}</div>
    </div>`).join("");
  shadeList.innerHTML = persist || list
    ? persist + list
    : `<div class="n-empty">没有通知</div>`;
}

shadeList.addEventListener("click", (e) => {
  const el = e.target.closest('[data-shade="stop"]');
  if (!el) return;
  toggleShade(false);
  stopListening();
});
headsUp.addEventListener("click", () => toggleShade(true));

function toggleShade(open) {
  const willOpen = open === undefined ? shade.classList.contains("away") : open;
  shade.classList.toggle("away", !willOpen);
}
$("statusbar").addEventListener("click", (e) => {
  if (e.target.closest(".shade")) return;
  toggleShade();
});
shade.addEventListener("click", (e) => {
  if (!e.target.closest(".notif") && !e.target.closest(".shade-head")) toggleShade(false);
});

/* ================= 顶号 / 断网 / 闪断（导演台事件） ================= */

function fireBump() {
  if (state.phase !== "listening") { showHint("现在没在听，顶不了", 1800); return; }
  engine.stop();
  state.phase = "idle";
  sbCast.classList.add("hidden");
  updateSubtitleVis();
  setStatus("warn", "被顶了：已在别处开听。再按开听可以顶回来。");
  showHeadsUp("直播同传", "已在别处开听", "同一账号同时只能一路在听。这台手机已停，不再自动开回来。", true);
  renderShade(); renderOurs(); renderReadout();
}

function fireNet() {
  if (state.phase !== "listening") { showHint("现在没在听。开听后再按，演示断网。", 1800); return; }
  engine.stop();
  state.phase = "failed"; state.fail = "net";
  sbCast.classList.add("hidden");
  updateSubtitleVis();
  setStatus("err", "网络断了，已停听。");
  showHeadsUp("直播同传", "网络断了", "已停听，字幕撤掉。不自动重开，网络好了再按开听。", true);
  renderShade(); renderOurs(); renderReadout();
}

function fireBlip() {
  if (state.phase !== "listening") { showHint("现在没在听。开听后再按，演示闪断。", 1800); return; }
  engine.stop();
  state.phase = "listening"; /* 闪断不算停：壳再开听 */
  clearBar();
  showHint("连接闪断，正在再开…", 1800);
  setStatus("warn", "闪断 · 正在再开");
  engine.timers.push(setTimeout(() => {
    if (state.phase !== "listening") return;
    engine.play(state.scene);
    setStatus("ok", `在听 · ${sourceLabel()} · ${langArrow()}`);
    showHint("已再开，继续在听", 1600);
  }, 1700));
}

/* ================= 导演台 ================= */

const SCENES = {
  en: "英语直播", ja: "日语直播", rapid: "连珠炮 · 硬切", pause: "停两秒 · 静默撤条",
  silence: "很久没人声", music: "音乐 · 非英日韩",
};

document.querySelectorAll(".dir-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    if (btn.dataset.stage) goStage(btn.dataset.stage);
    else if (btn.dataset.scene) runScene(btn.dataset.scene);
    else if (btn.dataset.act) runAct(btn.dataset.act);
    else if (btn.dataset.ev) runEvent(btn.dataset.ev);
  });
});

function runAct(act) {
  if (act === "login-as") {
    state.auth = "demo@example.com"; state.screen = "main";
    if (state.phase === "listening") stopListening(true);
    setStatus("info", "已登录。选音源，按开听。");
    goStage("ours");
  }
  else if (act === "logout") { doLogout(); }
  markDirBtns(); renderReadout();
}

function runScene(key) {
  state.scene = key;
  if (state.phase === "listening") {
    engine.stop(); clearBar(); engine.play(key);
    setStatus("ok", `在听 · ${sourceLabel()} · ${langArrow()}`);
  } else {
    setStatus("info", `场景已切到「${SCENES[key]}」，按开听生效。`);
  }
  renderOurs(); markDirBtns(); renderReadout();
}

function runEvent(ev) {
  if (ev === "bump") fireBump();
  else if (ev === "net") fireNet();
  else if (ev === "blip") fireBlip();
  else if (ev === "full") {
    state.fullArmed = !state.fullArmed;
    setStatus(state.fullArmed ? "warn" : "info", state.fullArmed ? "已设定：下次开听时满员。" : "选音源，按开听。");
  }
  else if (ev === "capture") {
    if (state.phase === "listening" && state.source === "liveapp") {
      engine.stop();
      state.phase = "failed"; state.fail = "capture";
      updateSubtitleVis();
      setStatus("err", "看播（直播 App）不允许抓它的声音。");
      showHint("这个 App 不让抓声音", 3200);
    } else {
      state.captureArmed = !state.captureArmed;
      setStatus(state.captureArmed ? "warn" : "info",
        state.captureArmed ? "已设定：下次开听时这个 App 不让抓。" : "选音源，按开听。");
    }
  }
  renderOurs(); markDirBtns(); renderReadout();
}

function markDirBtns() {
  document.querySelectorAll(".dir-btn").forEach((b) => {
    const on =
      (b.dataset.stage && b.dataset.stage === state.stage) ||
      (b.dataset.scene && b.dataset.scene === state.scene) ||
      (b.dataset.ev === "full" && state.fullArmed) ||
      (b.dataset.ev === "capture" && state.captureArmed);
    b.classList.toggle("on", !!on);
  });
}

/* ================= 状态读出 ================= */

function renderReadout() {
  if (!readout) return;
  const barDesc = (() => {
    if (!subTrans.textContent && !subOrig.textContent && !subHint.textContent) return "无";
    if (subHint.textContent) return `提示「${subHint.textContent}」`;
    const line = state.mode === "orig" ? subOrig.textContent : subTrans.textContent || subOrig.textContent;
    const kind = subTrans.className.includes("draft") || subOrig.className.includes("draft") ? "草稿" : "定稿";
    return `${kind}「${(line || "").slice(0, 12)}」`;
  })();
  const rows = [
    ["产品", state.phase === "listening" ? "在听" : state.phase === "failed" ? `出错（${state.fail}）` : "没在听"],
    ["舞台", { home: "桌面", live: "看播", ours: "本产品 App" }[state.stage]],
    ["登录", state.auth || "未登录"],
    ["音源", sourceLabel()],
    ["字幕模式", { both: "双语", orig: "仅原文", trans: "仅译文" }[state.mode]],
    ["字号", { s: "小", m: "中", l: "大" }[state.font]],
    ["字幕样式", ({ w: "白", y: "黄", c: "青" }[state.subColor]) + " · " + (state.subPad === "bar" ? "黑条" : "无底衬")],
    ["当前条", barDesc],
    ["场景", SCENES[state.scene] || state.scene],
    ["通知", state.phase === "listening" ? "常驻 + 历史" : state.notices.length ? `${state.notices.length} 条历史` : "无"],
  ];
  readout.innerHTML = rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("");
}

/* ================= 悬浮字幕：拖动 / 点按 ================= */

(function () {
  let drag = null;
  subWin.addEventListener("pointerdown", (e) => {
    const rect = screen.getBoundingClientRect();
    const wRect = subWin.getBoundingClientRect();
    drag = {
      startX: e.clientX, startY: e.clientY, moved: false,
      startLeft: wRect.left - rect.left, startTop: wRect.top - rect.top, rect,
    };
    subWin.setPointerCapture(e.pointerId);
    e.preventDefault();
  });
  subWin.addEventListener("pointermove", (e) => {
    if (!drag) return;
    const dx = e.clientX - drag.startX, dy = e.clientY - drag.startY;
    if (Math.abs(dx) + Math.abs(dy) > 7) drag.moved = true;
    if (!drag.moved) return;
    const left = Math.max(4, Math.min(drag.rect.width - 60, drag.startLeft + dx));
    const top = Math.max(40, Math.min(drag.rect.height - 60, drag.startTop + dy));
    subWin.style.left = left + "px";
    subWin.style.right = "auto";
    subWin.style.top = top + "px";
    subWin.style.bottom = "auto";
  });
  const end = (e) => {
    if (drag && !drag.moved && state.phase === "listening") goStage("ours"); /* 点一下回本产品（建议，未拍板） */
    drag = null;
  };
  subWin.addEventListener("pointerup", end);
  subWin.addEventListener("pointercancel", () => { drag = null; });
})();

/* ================= 直播弹幕（布景） ================= */

const DM_POOL = [
  ["mossygreen", "W stream"], ["boxfan", "no way he pulls this off"],
  ["sleepy_lt", "he is cooking"], ["pingspike", "that was insane"],
  ["dripgoblin", "the enemies just walked in LOL"], ["nine_lives", "clip that"],
  ["orbit_soma", "POG"], ["fernwed", "clean rotation"],
];
(function () {
  const list = $("liveDm");
  let i = 0;
  function addOne() {
    const [name, text] = DM_POOL[i % DM_POOL.length]; i++;
    const div = document.createElement("div");
    div.className = "dm-msg";
    const b = document.createElement("b");
    b.textContent = name + "：";
    div.appendChild(b);
    div.appendChild(document.createTextNode(text));
    list.appendChild(div);
    while (list.children.length > 4) list.removeChild(list.firstChild);
  }
  for (let k = 0; k < 3; k++) addOne();
  setInterval(addOne, 3400 + Math.random() * 1600);
})();

/* ================= 时钟 / 初始化 ================= */

const WEEK = ["日", "一", "二", "三", "四", "五", "六"];
setInterval(() => {
  const d = new Date();
  const hm = `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  const date = `${d.getMonth() + 1}月${d.getDate()}日 周${WEEK[d.getDay()]}`;
  $("sbTime").textContent = hm;
  $("hcTime").textContent = hm;
  $("hcDate").textContent = date;
  $("shadeDate").textContent = date;
}, 1000);

/* 通知层用 .away 做滑入滑出，初始收起 */
headsUp.classList.remove("hidden"); headsUp.classList.add("away");
shade.classList.remove("hidden"); shade.classList.add("away");

applyFont();
applySubStyle();
reflowBarForMode();
goStage("ours");
renderShade();
renderOurs();
renderReadout();
markDirBtns();
/* 抽屉默认收起：开着会盖住手机右缘 */
