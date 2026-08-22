/* 控制面板（变体 A · 紧凑单列）。
   全部状态来自缝事件 + Rust 广播，跑同一份 reducer；操作走 invoke。 */
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { isListenEvent } from "./core/events";
import { initialShellState, panelViewChanged, reduce, type Phase, type ShellState } from "./core/reducer";
import { listenPane, type AccountSession } from "./core/hosted";
import {
  applySettingsChange,
  loadSettings,
  sameSources,
  sourceLabelOf,
  type AudioSource,
  type FontFace,
  type FontSize,
  type SubEdge,
  type SubPlate,
  type SubStyle,
  type SubWeight,
  type ListenWay,
  type Mode,
  type Settings,
} from "./core/settings";

interface BootInfo {
  settings: Settings;
  sources: AudioSource[];
  sourceDead: boolean;
  weak: boolean;
  phase: Phase;
}

let shell: ShellState = initialShellState();
let settings!: Settings;
let sources: AudioSource[] = [];
let sourceDead = false;
let weak = false;
let refreshingSources = false;
let fetchingSources = false;
let sourceRefreshFeedback = "";
let panelTab: "listen" | "llm" | "account" = "listen";
let account: AccountSession = null;
let loginDraft = { email: "", password: "", remember: false };
let loginNote = "";
let loginBusy = false;
let llmDraft = {
  enabled: false,
  baseUrl: "https://opencode.ai/zen/go/v1",
  model: "deepseek-v4-flash",
  apiKey: "",
  thinking: "",
  thinkingParam: "",
};
let llmModels: string[] = [];
let llmThinkOptions: { id: string; label: string; param: string; value: string }[] = [];
let llmNote = "";
let llmBusy = false;
const HOSTED_ORIGIN = "http://127.0.0.1:8787";
const SOURCE_AUTO_REFRESH_MS = 2_000;

const body = document.getElementById("panelBody")!;
const footer = document.getElementById("panelFooter")!;
const panelRoot = document.querySelector(".panel")!;

async function getBootWhenReady(): Promise<BootInfo> {
  // Tauri 会先创建 WebView 再跑壳的 setup；热重编时 get_boot 偶尔比状态注册更早到。
  // 这时只等状态就绪，不把一个短暂竞态变成白屏或崩溃。
  for (let attempt = 0; attempt < 80; attempt++) {
    try {
      return await invoke<BootInfo>("get_boot");
    } catch (error) {
      if (!String(error).includes("控制面板正在启动")) throw error;
      await new Promise((resolve) => window.setTimeout(resolve, 50));
    }
  }
  throw new Error("控制面板启动超时，请从托盘重新打开。");
}

/* ---------- 渲染 ---------- */

const statusCls: Record<string, string> = { info: "", ok: "ok", warn: "warn", err: "err" };

function snapshotLoginDraft() {
  if (listenPane(settings.listenWay, account) !== "login") return;
  const email = (document.getElementById("loginEmail") as HTMLInputElement | null)?.value;
  if (email !== undefined) loginDraft.email = email;
  const password = (document.getElementById("loginPassword") as HTMLInputElement | null)?.value;
  if (password !== undefined) loginDraft.password = password;
}

function snapshotLlmDraft() {
  if (panelTab !== "llm") return;
  // 切进译文页的那一帧，DOM 还是听译页、没有这些输入框；
  // 元素不存在只能说明在换页，不能当成观众把字段清空了。
  const val = (id: string) => (document.getElementById(id) as HTMLInputElement | null)?.value;
  const baseUrl = val("llmBaseUrl");
  if (baseUrl) llmDraft.baseUrl = baseUrl;
  const model = val("llmModel");
  if (model) llmDraft.model = model;
  const apiKey = val("llmApiKey");
  if (apiKey !== undefined) llmDraft.apiKey = apiKey;
  const thinkParam = val("llmThinkParam");
  if (thinkParam !== undefined) llmDraft.thinkingParam = thinkParam;
  const thinkVal = val("llmThinkValue");
  if (thinkVal !== undefined) llmDraft.thinking = thinkVal;
}

function paint(html: string, footerHtml: string, keepScroll: boolean) {
  const y = keepScroll ? body.scrollTop : 0;
  body.innerHTML = html;
  footer.innerHTML = footerHtml;
  if (keepScroll) body.scrollTop = y;
}

function render(keepScroll = false) {
  snapshotLoginDraft();
  snapshotLlmDraft();
  const phase = shell.phase;
  const items: string[] = [];

  items.push(`<div class="tabs" role="tablist">
    <button class="tab ${panelTab === "listen" || panelTab === "account" ? "sel" : ""}" data-act="tab" data-v="listen" role="tab" aria-selected="${panelTab !== "llm"}">听译</button>
    <button class="tab ${panelTab === "llm" ? "sel" : ""}" data-act="tab" data-v="llm" role="tab" aria-selected="${panelTab === "llm"}">LLM 配置</button>
  </div>`);

  if (account) {
    items.push(`<button class="account-mail" data-act="account">${esc(account.email)}</button>`);
  }

  if (panelTab === "account") {
    items.push(accountBlock());
    paint(items.join(""), "", false);
    return;
  }

  if (panelTab === "llm") {
    items.push(llmBlock());
    paint(items.join(""), "", false);
    return;
  }

  if (settings.listenWay === "hosted") items.push(wayBlock());

  if (listenPane(settings.listenWay, account) === "login") {
    items.push(loginBlock());
    paint(items.join(""), "", false);
    return;
  }

  items.push(`<div class="stat ${statusCls[shell.panelStatus.kind] ?? ""}" role="status" aria-live="polite" aria-atomic="true">
    <span class="st-dot"></span><span>${esc(shell.panelStatus.text)}</span></div>`);

  if (weak) {
    items.push(`<div class="note warn">这台机器算力偏弱，字幕可能出现得慢一些，但还能用。</div>`);
  }

  if (phase === "failed" && shell.failureKind === "no_audio") {
    items.push(`<div class="note err">${esc(sourceLabelOf(settings.source, sources))} 抓不到声音（权限不够）。换个音源，或改用系统混音兜底。
      <div class="note-acts">
        <button data-act="use-system">改用系统混音</button>
        <button data-act="retry">重试</button>
      </div></div>`);
  }

  items.push(sourceBlock());
  items.push(preferencesBlock());
  paint(items.join(""), primaryBlock(), keepScroll);
}

function wayBlock(): string {
  const locked = shell.phase === "listening";
  return segBlock("listen-way", "听译", [
    ["local", "本机"],
    ["hosted", "托管"],
  ], settings.listenWay, locked);
}

function loginBlock(): string {
  return `<section class="preferences" aria-label="登录">
    <div class="preferences-cap">用托管听译需要账号</div>
    <label class="field"><span>邮箱</span>
      <input id="loginEmail" type="email" autocomplete="username" spellcheck="false" value="${esc(loginDraft.email)}">
    </label>
    <label class="field"><span>密码</span>
      <input id="loginPassword" type="password" autocomplete="current-password" value="${esc(loginDraft.password)}">
    </label>
    <button class="chk ${loginDraft.remember ? "on" : ""}" data-act="login-remember">
      <span class="box"></span>
      <span>记住我<small>关掉软件后还留在这台电脑</small></span>
    </button>
    <div class="note-acts llm-acts">
      <button data-act="login" ${loginBusy ? "disabled" : ""}>登录</button>
      <button data-act="register" ${loginBusy ? "disabled" : ""}>注册</button>
    </div>
    <p class="llm-lead">还没有账号就用邮箱注册</p>
    ${loginNote ? `<div class="note warn">${esc(loginNote)}</div>` : ""}
  </section>`;
}

function accountBlock(): string {
  return `<section class="preferences" aria-label="个人中心">
    <div class="preferences-cap">个人中心</div>
    <p class="llm-lead">${esc(account?.email ?? "")}</p>
    <div class="note-acts llm-acts">
      <button data-act="logout">退出</button>
    </div>
  </section>`;
}

function modelField(): string {
  const list = llmModels.length
    ? `<datalist id="llmModelList">${llmModels.map((id) => `<option value="${esc(id)}">`).join("")}</datalist>`
    : "";
  return `<label class="field"><span>模型</span>
    <input id="llmModel" list="llmModelList" type="text" autocomplete="off" spellcheck="false" value="${esc(llmDraft.model)}" placeholder="拉模型后可下拉，也可手写">
    ${list}
  </label>`;
}

function thinkField(): string {
  const params = [...new Set(llmThinkOptions.map((o) => o.param).filter(Boolean))];
  const values = [...new Set(llmThinkOptions.map((o) => o.value).filter(Boolean))];
  const paramList = params.length
    ? `<datalist id="llmThinkParamList">${params.map((p) => `<option value="${esc(p)}">`).join("")}</datalist>`
    : "";
  const valueList = values.length
    ? `<datalist id="llmThinkValueList">${values.map((v) => `<option value="${esc(v)}">`).join("")}</datalist>`
    : "";
  return `<label class="field"><span>思考字段</span>
    <input id="llmThinkParam" list="llmThinkParamList" type="text" autocomplete="off" spellcheck="false" value="${esc(llmDraft.thinkingParam)}" placeholder="放空则不传">
    ${paramList}
  </label>
  <label class="field"><span>思考档</span>
    <input id="llmThinkValue" list="llmThinkValueList" type="text" autocomplete="off" spellcheck="false" value="${esc(llmDraft.thinking)}" placeholder="放空则用模型默认">
    ${valueList}
  </label>
  <p class="llm-lead">听译要快，建议填关闭或低档（off / low / none）。各模型默认档不同，放空会走接口默认，可能很慢。</p>`;
}

function llmBlock(): string {
  const hostedNote = settings.listenWay === "hosted"
    ? `<p class="llm-lead">托管听译不用这边的接口，改回本机才生效。</p>`
    : `<p class="llm-lead">兼容任意 OpenAI 格式接口。不配也能开听，只用本机翻译。配了以后草稿仍走本机，定稿再尽量改顺。</p>`;
  return `<section class="preferences" aria-label="LLM 配置">
    <div class="preferences-cap">选用大模型改写定稿</div>
    ${hostedNote}
    <button class="chk ${llmDraft.enabled ? "on" : ""}" data-act="llm-enabled">
      <span class="box"></span>
      <span>开启改写<small>关着就只用本机翻译</small></span>
    </button>
    <div class="pref-divider"></div>
    <label class="field"><span>Base URL</span>
      <input id="llmBaseUrl" type="url" autocomplete="off" spellcheck="false" value="${esc(llmDraft.baseUrl)}" placeholder="https://api.example.com/v1">
    </label>
    <label class="field"><span>API Key</span>
      <input id="llmApiKey" type="password" autocomplete="off" spellcheck="false" value="${esc(llmDraft.apiKey)}" placeholder="只存在这台电脑">
    </label>
    <div class="note-acts llm-acts">
      <button data-act="llm-models" ${llmBusy ? "disabled" : ""}>拉模型</button>
      <button data-act="llm-think" ${llmBusy ? "disabled" : ""}>探测思考</button>
    </div>
    ${modelField()}
    ${thinkField()}
    <div class="note-acts llm-acts">
      <button data-act="llm-save" ${llmBusy ? "disabled" : ""}>保存</button>
      <button data-act="llm-test" ${llmBusy ? "disabled" : ""}>试连</button>
    </div>
    ${llmNote ? `<div class="note ${/失败|没能|先填|拉不到|连不上/.test(llmNote) ? "warn" : "ok-note"}">${esc(llmNote)}</div>` : ""}
  </section>`;
}

function sourceBlock(): string {
  const apps = sources.filter((s) => !s.system);
  const needsReplacement = sourceDead || shell.sourceGone;
  const row = (s: AudioSource) => {
        const sel = settings.source === s.id && !needsReplacement ? "sel" : "";
        const activity = s.audible ? "正在出声" : "暂未出声";
        return `<button class="src-row ${sel} ${s.audible ? "" : "quiet"}" data-act="src" data-id="${esc(s.id)}" aria-pressed="${sel ? "true" : "false"}">
          <span class="src-radio"></span>
          <span class="src-main"><span class="src-name">${esc(s.processName)}<span class="src-activity ${s.audible ? "live" : "quiet"}">${activity}</span></span>
          <span class="src-desc">${esc(s.friendlyName)}</span></span>
        </button>`;
      };
  const activeApps = apps.filter((s) => s.audible);
  const quietApps = apps.filter((s) => !s.audible);
  const rows = activeApps.length
    ? `<div class="src-group-cap">正在出声</div>${activeApps.map(row).join("")}`
    : `<div class="src-empty">暂无正在出声的应用。开始播放后会自动出现。</div>`;
  const retained = quietApps.length
    ? `<div class="src-group-cap saved">已保存，暂未出声</div>${quietApps.map(row).join("")}`
    : "";
  const system = sources
    .filter((s) => s.system)
    .map((s) => {
      const sel = settings.source === s.id && !needsReplacement ? "sel" : "";
      return `<button class="src-row system-row ${sel}" data-act="src" data-id="${esc(s.id)}" aria-pressed="${sel ? "true" : "false"}">
        <span class="src-radio"></span>
        <span class="src-main"><span class="src-name">系统混音</span>
        <span class="src-desc">${esc(s.friendlyName)}</span></span>
      </button>`;
    }).join("");
  return `<div class="blk source-block">
    <div class="blk-cap">音源<button class="mini-btn" data-act="refresh" title="重新扫描正在出声的应用" ${refreshingSources ? "disabled" : ""}>${refreshingSources ? "刷新中…" : "刷新"}</button></div>
    <div class="src-list" aria-busy="${refreshingSources}">${rows}${retained}<div class="src-group-cap saved">兜底</div>${system}</div>
    <div class="src-refresh-note" role="status" aria-live="polite">${sourceRefreshFeedback || "每两秒自动检查一次；已保存的静音音源留在下方。"}</div>
    ${needsReplacement ? `<div class="src-dead-note">上一条音源已经不能继续听。选择一条新的音源后才能开听。</div>` : ""}
  </div>`;
}

function segBlock(
  act: string,
  cap: string,
  options: readonly (readonly [string, string])[],
  cur: string,
  locked = false,
): string {
  return `<div class="blk setting-field"><div class="blk-cap">${cap}</div>
    <div class="seg">${options.map(([v, t]) =>
      `<button data-act="${act}" data-v="${v}" class="${cur === v ? "sel" : ""}" ${locked ? "disabled" : ""}>${t}</button>`).join("")}
    </div></div>`;
}

function preferencesBlock(): string {
  return `<section class="preferences" aria-label="字幕显示与偏好">
    <div class="preferences-cap">字幕显示与偏好</div>
    ${segBlock("mode", "字幕模式", [
      ["both", "双语"], ["orig", "仅原文"], ["trans", "仅译文"],
    ] as const, settings.mode)}
    ${segBlock("font", "字幕字号", [
      ["s", "小"], ["m", "中"], ["l", "大"], ["xl", "特大"],
    ] as const, settings.font)}
    ${segBlock("face", "字体", [
      ["yahei", "雅黑"], ["hei", "黑体"], ["song", "宋体"],
    ] as const, settings.face)}
    ${segBlock("style", "快捷样式", [
      ["outline", "描边"], ["yellow", "黄字"], ["plate", "黑底"],
    ] as const, settings.style)}
    <div class="blk setting-field"><div class="blk-cap">字色</div>
      <div class="swatches">
        ${["#ffffff", "#ffe566", "#000000", "#7dff9a", "#7de8ff", "#ff7ad9"].map((c) =>
          `<button type="button" class="swatch ${settings.ink === c ? "sel" : ""}" data-act="ink" data-v="${c}" style="background:${c}" title="${c}"></button>`
        ).join("")}
        <input id="inkCustom" type="color" value="${esc(settings.ink)}" title="自选字色">
      </div>
    </div>
    ${segBlock("edge", "描边", [
      ["none", "无"], ["thin", "细"], ["thick", "粗"],
    ] as const, settings.edge)}
    ${segBlock("plate", "底", [
      ["none", "无"], ["soft", "浅"], ["hard", "深"],
    ] as const, settings.plate)}
    ${segBlock("weight", "字重", [
      ["regular", "常规"], ["bold", "粗"],
    ] as const, settings.weight)}
    <div class="pref-divider"></div>
    <button class="chk ${settings.autostart ? "on" : ""}" data-act="autostart">
      <span class="box"></span>
      <span>开机自启<small>开机只进托盘，不会自动开听</small></span>
    </button>
  </section>`;
}

function primaryBlock(): string {
  if (shell.phase === "downloading") {
    const pct = Math.round(shell.downloadPct);
    const eta = pct < 30 ? "约还需 4 分钟" : pct < 70 ? "约还需 2 分钟" : "快好了";
    return `<div class="dl">
      <div class="dl-line">正在下载听译模型 <b>${pct}%</b> · ${eta}</div>
      <div class="dl-bar"><i style="width:${pct}%"></i></div>
      <div class="dl-sub">面板先能用。装完自动能听。</div>
    </div>`;
  }
  if (shell.phase === "listening") {
    return `<button class="btn-main stop" data-act="startstop">停止</button>`;
  }
  const disabled = sourceDead || shell.sourceGone || !settings.source;
  return `<button class="btn-main" data-act="startstop" ${disabled ? "disabled" : ""}>
    ${disabled ? "先选一个音源" : "开听"}</button>`;
}

async function submitAccount(kind: "login" | "register") {
  snapshotLoginDraft();
  loginBusy = true;
  loginNote = "";
  render();
  try {
    const res = await fetch(`${HOSTED_ORIGIN}/account/${kind}`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        email: loginDraft.email,
        password: loginDraft.password,
        rememberMe: loginDraft.remember,
      }),
    });
    const payload = await res.json().catch(() => ({} as { email?: string; error?: string }));
    if (!res.ok) {
      loginNote = payload.error || "没连上托管。";
      return;
    }
    if (!payload.email) {
      loginNote = "没连上托管。";
      return;
    }
    account = { email: payload.email };
    loginDraft.password = "";
    panelTab = "listen";
  } catch {
    loginNote = "没连上托管。";
  } finally {
    loginBusy = false;
    render();
  }
}

function esc(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]!);
}

/* ---------- 事件 ---------- */

function dispatch(next: ShellState) {
  const paintPanel = panelViewChanged(shell, next);
  shell = next;
  if (paintPanel) render(true);
}

panelRoot.addEventListener("input", (e) => {
  const el = e.target as HTMLInputElement;
  if (el.id === "inkCustom" && el.value) void invoke("set_ink", { ink: el.value });
});

panelRoot.addEventListener("click", (e) => {
  const el = (e.target as HTMLElement).closest<HTMLElement>("[data-act]");
  if (!el || el.hasAttribute("disabled")) return;
  const act = el.dataset.act;
  if (act === "tab" && (el.dataset.v === "listen" || el.dataset.v === "llm")) {
    snapshotLoginDraft();
    snapshotLlmDraft();
    panelTab = el.dataset.v;
    render();
  }
  else if (act === "listen-way" && (el.dataset.v === "local" || el.dataset.v === "hosted")) {
    if (shell.phase === "listening") return;
    snapshotLoginDraft();
    void invoke("set_listen_way", { way: el.dataset.v as ListenWay });
  }
  else if (act === "login-remember") {
    snapshotLoginDraft();
    loginDraft.remember = !loginDraft.remember;
    render();
  }
  else if (act === "login") void submitAccount("login");
  else if (act === "register") void submitAccount("register");
  else if (act === "account") {
    snapshotLoginDraft();
    panelTab = "account";
    render();
  }
  else if (act === "logout") {
    account = null;
    panelTab = "listen";
    render();
  }
  else if (act === "src" && el.dataset.id) void selectSource(el.dataset.id);
  else if (act === "refresh") void refreshSources();
  else if (act === "mode") void invoke("set_mode", { mode: el.dataset.v as Mode });
  else if (act === "font") void invoke("set_font", { font: el.dataset.v as FontSize });
  else if (act === "face") void invoke("set_face", { face: el.dataset.v as FontFace });
  else if (act === "style") void invoke("set_style", { style: el.dataset.v as SubStyle });
  else if (act === "ink" && el.dataset.v) void invoke("set_ink", { ink: el.dataset.v });
  else if (act === "edge") void invoke("set_edge", { edge: el.dataset.v as SubEdge });
  else if (act === "plate") void invoke("set_plate", { plate: el.dataset.v as SubPlate });
  else if (act === "weight") void invoke("set_weight", { weight: el.dataset.v as SubWeight });
  else if (act === "autostart") void invoke("set_autostart", { on: !settings.autostart });
  else if (act === "startstop") {
    if (shell.phase === "listening") void invoke("listen_stop");
    else if (settings.listenWay === "hosted") return;
    else if (settings.source && !sourceDead) void invoke("listen_start", { source: settings.source });
  } else if (act === "use-system") {
    // 音源抓不到的出路：改用系统混音并立刻重开听，不用再按一次开听
    void (async () => {
      await selectSource("system");
      await invoke("listen_start", { source: "system" });
    })();
  } else if (act === "retry") {
    if (settings.source) void invoke("listen_start", { source: settings.source });
  } else if (act === "llm-enabled") {
    snapshotLlmDraft();
    llmDraft.enabled = !llmDraft.enabled;
    render();
  } else if (act === "llm-save") void saveLlm();
  else if (act === "llm-test") void testLlm();
  else if (act === "llm-models") void listLlmModels();
  else if (act === "llm-think") void probeLlmThinking();
});

function llmPayload() {
  snapshotLlmDraft();
  return {
    enabled: llmDraft.enabled,
    baseUrl: llmDraft.baseUrl.trim(),
    model: llmDraft.model.trim(),
    apiKey: llmDraft.apiKey.trim(),
    thinking: llmDraft.thinking.trim(),
    thinkingParam: llmDraft.thinkingParam.trim(),
    timeoutS: 20,
    maxTokens: 256,
  };
}

async function listLlmModels() {
  llmBusy = true;
  llmNote = "";
  render();
  try {
    const r = await invoke<{ ok: boolean; preview: string; models: string[] }>("list_llm_models", {
      config: llmPayload(),
    });
    llmModels = r.models || [];
    if (r.ok && llmModels.length && !llmModels.includes(llmDraft.model)) {
      llmDraft.model = llmModels[0];
    }
    llmNote = r.preview || "拉模型结束。";
  } catch {
    llmNote = "拉模型失败。";
  } finally {
    llmBusy = false;
    render();
  }
}

async function probeLlmThinking() {
  llmBusy = true;
  llmNote = "";
  render();
  try {
    const r = await invoke<{
      ok: boolean;
      preview: string;
      options: { id: string; label: string; param: string; value: string }[];
    }>("probe_llm_thinking", { config: llmPayload() });
    llmThinkOptions = (r.options || []).map((o) => ({
      id: o.id,
      label: o.label,
      param: o.param || "",
      value: o.value || "",
    }));
    llmNote = r.preview || "探测结束。";
  } catch {
    llmNote = "探测思考失败。";
  } finally {
    llmBusy = false;
    render();
  }
}

async function saveLlm() {
  llmBusy = true;
  llmNote = "";
  render();
  try {
    const payload = llmPayload();
    await invoke("set_llm_config", { config: payload });
    llmNote = !payload.enabled
      ? "已保存。现在只用本机翻译。"
      : payload.apiKey ? "已保存。下次定稿会按这个改写。" : "已保存，但还没填密钥，改写不会生效。";
  } catch {
    llmNote = "没能写到本机配置，请再试一次。";
  } finally {
    llmBusy = false;
    render();
  }
}

async function testLlm() {
  llmBusy = true;
  llmNote = "";
  render();
  try {
    const r = await invoke<{ ok: boolean; ms: number; preview: string }>("test_llm_config", {
      config: llmPayload(),
    });
    llmNote = r.ok ? `连上了（${r.ms}ms）：${r.preview}` : r.preview || "没连上。";
  } catch {
    llmNote = "试连失败。";
  } finally {
    llmBusy = false;
    render();
  }
}

document.getElementById("panelClose")!.addEventListener("click", () => {
  void getCurrentWindow().hide(); // 只藏面板，开听不停
});

document.getElementById("panelMinimize")!.addEventListener("click", () => {
  void getCurrentWindow().minimize(); // 留在 Windows 任务栏，方便切回
});

async function refreshSources(quiet = false) {
  if (fetchingSources) return;
  fetchingSources = true;
  if (!quiet) {
    refreshingSources = true;
    sourceRefreshFeedback = "";
    render(true);
  }
  try {
    const nextSources = await invoke<AudioSource[]>("refresh_sources");
    const next = loadSettings(settings, nextSources);
    const changed = !sameSources(sources, nextSources) || sourceDead !== next.sourceDead;
    sources = nextSources;
    settings = next.settings;
    sourceDead = next.sourceDead;
    if (!quiet) sourceRefreshFeedback = "已刷新。选择一条正在出声的音源。";
    else if (changed && panelTab === "listen") render(true);
  } catch {
    if (!quiet) sourceRefreshFeedback = "音源列表没刷新成功，请再试一次。";
  } finally {
    fetchingSources = false;
    if (!quiet) {
      refreshingSources = false;
      render(true);
    }
  }
}

async function selectSource(source: string) {
  // 壳会稍后广播同一份设置；这里先本地确认，避免观众点完后还看见旧的黄字。
  const next = applySettingsChange({ settings, sourceDead }, { source }, sources);
  settings = next.settings;
  sourceDead = next.sourceDead;
  const label = sourceLabelOf(source, sources);
  sourceRefreshFeedback = `已选择 ${label}。`;
  const audible = source === "system" || sources.some((s) => s.id === source && s.audible);
  dispatch(reduce(shell, { type: "source_selected", sourceLabel: label, audible }));
  try {
    await invoke("select_source", { source });
  } catch {
    sourceRefreshFeedback = "没能切到这条音源，请重新选择后再试。";
    render();
  }
}

/* ---------- 启动 ---------- */

async function main() {
  const boot = await getBootWhenReady();
  const merged = loadSettings(boot.settings, boot.sources);
  settings = merged.settings;
  sources = boot.sources;
  sourceDead = boot.sourceDead;
  weak = boot.weak;
  shell = reduce(initialShellState(), {
    type: "phase",
    phase: boot.phase,
    sourceLabel: sourceLabelOf(settings.source, sources),
  });
  render();
  try {
    const cfg = await invoke<{
      enabled: boolean;
      baseUrl: string;
      model: string;
      apiKey: string;
      thinking?: string;
      thinkingParam?: string;
    }>("get_llm_config");
    llmDraft = {
      enabled: !!cfg.enabled,
      baseUrl: cfg.baseUrl || llmDraft.baseUrl,
      model: cfg.model || llmDraft.model,
      apiKey: cfg.apiKey || "",
      thinking: cfg.thinking || "",
      thinkingParam: cfg.thinkingParam || "",
    };
  } catch {
    /* 没有配置就用面板默认 */
  }
  render();

  await listen("listen://event", (e) => {
    if (isListenEvent(e.payload)) {
      dispatch(reduce(shell, { type: "listen", event: e.payload, now: performance.now() }));
    }
  });
  await listen("app://phase", (e) => {
    const p = e.payload as { phase: Phase; sourceLabel: string };
    dispatch(reduce(shell, { type: "phase", phase: p.phase, sourceLabel: p.sourceLabel }));
  });
  await listen("app://download", (e) => {
    dispatch(reduce(shell, { type: "download", pct: e.payload as number }));
  });
  await listen("app://source_switched", (e) => {
    const p = e.payload as { sourceLabel: string; now: number };
    dispatch(reduce(shell, { type: "source_switched", sourceLabel: p.sourceLabel, now: p.now }));
  });
  await listen("app://source_gone", () => {
    dispatch(reduce(shell, { type: "source_gone" }));
    // 音源没了，列表里的旧行也该消失
    void refreshSources(true);
  });
  await listen("settings://changed", (e) => {
    const next = applySettingsChange(
      { settings, sourceDead },
      e.payload as Partial<Settings>,
      sources
    );
    settings = next.settings;
    sourceDead = next.sourceDead;
    render(true);
  });

  // 面板重新获得焦点或保持打开时，音源清单会自己追上正在出声的进程；
  // 手动「刷新」仍可立即重扫并给出明确反馈。
  window.addEventListener("focus", () => void refreshSources(true));
  window.setInterval(() => {
    if (document.visibilityState === "visible") void refreshSources(true);
  }, SOURCE_AUTO_REFRESH_MS);
}

void main();
