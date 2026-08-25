/* 控制面板（变体 A · 紧凑单列）。
   全部状态来自缝事件 + Rust 广播，跑同一份 reducer；操作走 invoke。 */
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { isListenEvent } from "./core/events";
import { initialShellState, panelViewChanged, reduce, type Phase, type ShellState } from "./core/reducer";
import { DEFAULT_HOSTED_ORIGIN, hostedOrigin, listenPane, postAccount, type AccountSession } from "./core/hosted";
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
  hostedOrigin?: string;
  hostedAccount?: AccountSession;
  hostedRemembered?: boolean;
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
/** 登录是否记在这台电脑上：改密码换新 token 时要不要重写凭据 */
let accountRemembered = false;
let loginDraft = { email: "", password: "", remember: false };
let loginNote = "";
let loginBusy = false;
let pwdDraft = { old: "", fresh: "" };
let pwdNote = "";
let pwdNoteOk = false;
let pwdBusy = false;
let llmDraft = {
  enabled: false,
  baseUrl: "https://opencode.ai/zen/go/v1",
  model: "deepseek-v4-flash",
  apiKey: "",
  thinking: "",
  thinkingParam: "",
};
let llmModels: string[] = [];
let llmNote = "";
let llmBusy = false;
/** DOM 内自绘下拉当前是否展开：透明窗里原生 datalist 弹层渲染不出来 */
let llmCombo = false;
/** 展开后输入的过滤词；打开时为空＝显示全部，打着字才收窄 */
let llmComboQuery = "";

function closeCombo() {
  llmCombo = false;
  llmComboQuery = "";
}
let hostedOriginUrl = DEFAULT_HOSTED_ORIGIN;
const SOURCE_AUTO_REFRESH_MS = 2_000;

const body = document.getElementById("panelBody")!;
const footer = document.getElementById("panelFooter")!;
const panelRoot = document.querySelector(".panel")! as HTMLElement;

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

function snapshotAccountDraft() {
  if (panelTab !== "account") return;
  const val = (id: string) => (document.getElementById(id) as HTMLInputElement | null)?.value;
  const old = val("pwdOld");
  if (old !== undefined) pwdDraft.old = old;
  const fresh = val("pwdFresh");
  if (fresh !== undefined) pwdDraft.fresh = fresh;
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
  snapshotAccountDraft();
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

  items.push(wayBlock());

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
  if (!account) {
    return `<section class="preferences" aria-label="个人中心">
      <div class="preferences-cap">个人中心</div>
      <p class="llm-lead">还没登录。去听译页选托管即可登录。</p>
    </section>`;
  }
  return `<section class="preferences" aria-label="个人中心">
    <div class="preferences-cap">个人中心</div>
    <p class="llm-lead">${esc(account.email)}</p>
    <div class="pref-divider"></div>
    <div class="preferences-cap">改密码</div>
    <label class="field"><span>旧密码</span>
      <input id="pwdOld" type="password" autocomplete="current-password" spellcheck="false" value="${esc(pwdDraft.old)}">
    </label>
    <label class="field"><span>新密码</span>
      <input id="pwdFresh" type="password" autocomplete="new-password" spellcheck="false" value="${esc(pwdDraft.fresh)}">
    </label>
    <div class="note-acts llm-acts">
      <button data-act="password" ${pwdBusy ? "disabled" : ""}>改密码</button>
    </div>
    <p class="llm-lead">改完其它电脑上的登录会退出。</p>
    ${pwdNote ? `<div class="note ${pwdNoteOk ? "ok-note" : "warn"}">${esc(pwdNote)}</div>` : ""}
    <div class="pref-divider"></div>
    <div class="note-acts llm-acts">
      <button data-act="logout">退出</button>
    </div>
  </section>`;
}

/** 输入框 + 自绘下拉。输入内容即过滤词；点输入框展开/收起；选项点了写回草稿。 */
function comboField(id: string, cap: string, value: string, placeholder: string, all: string[]): string {
  const q = llmCombo ? llmComboQuery.trim().toLowerCase() : "";
  const shown = (q ? all.filter((o) => o.toLowerCase().includes(q)) : all).slice(0, 80);
  const count = all.length ? `<small style="float:right;font-weight:400">${all.length} 个可选</small>` : "";
  const list =
    llmCombo && shown.length
      ? `<div class="combo-list">${shown
          .map(
            (o) =>
              `<button type="button" class="combo-opt${o === value ? " sel" : ""}" data-act="combo-pick" data-v="${esc(o)}" title="${esc(o)}">${esc(o)}</button>`,
          )
          .join("")}</div>`
      : "";
  return `<label class="field combo-wrap"><span>${cap}${count}</span>
    <input id="${id}" data-combo="model" type="text" autocomplete="off" spellcheck="false" value="${esc(value)}" placeholder="${placeholder}">
    ${list}
  </label>`;
}

function refocusField(id: string) {
  const el = document.getElementById(id) as HTMLInputElement | null;
  if (el) {
    el.focus();
    el.setSelectionRange(el.value.length, el.value.length);
  }
}

/** 下拉往输入框下方展开：快到面板底部时把输入框滚到中部，给列表腾地方 */
function ensureComboRoom(id: string) {
  const el = document.getElementById(id);
  const body = document.querySelector(".panel-body");
  if (!el || !body) return;
  const er = el.getBoundingClientRect();
  const br = body.getBoundingClientRect();
  if (er.bottom + 190 > br.bottom) el.scrollIntoView({ block: "center" });
}

function modelField(): string {
  return comboField("llmModel", "模型", llmDraft.model, "拉模型后点输入框可选，也可手写", llmModels);
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
    </div>
    ${modelField()}
    <p class="llm-lead">思考强度自动按最低档配置，接口不认会自动去掉，不用管。</p>
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
    const { status, payload } = await postAccount(hostedOriginUrl, kind, {
      email: loginDraft.email,
      password: loginDraft.password,
      rememberMe: loginDraft.remember,
    });
    if (status !== 200 || !payload.email || !payload.token) {
      loginNote = (status !== 200 && payload.error) || "没连上托管。";
      return;
    }
    account = { email: payload.email, token: payload.token };
    accountRemembered = loginDraft.remember;
    try {
      await invoke("save_hosted_session", {
        email: account.email,
        token: account.token,
        remember: loginDraft.remember,
      });
    } catch {
      loginNote = "登录成功，但没能记住这台电脑。";
    }
    loginDraft.password = "";
    panelTab = "listen";
  } catch {
    loginNote = "没连上托管。";
  } finally {
    loginBusy = false;
    render();
  }
}

async function submitPasswordChange() {
  if (!account) return;
  snapshotAccountDraft();
  if (!pwdDraft.old || !pwdDraft.fresh) {
    pwdNote = "先填旧密码和新密码。";
    pwdNoteOk = false;
    render();
    return;
  }
  pwdBusy = true;
  pwdNote = "";
  render();
  try {
    const { status, payload } = await postAccount(hostedOriginUrl, "password", {
      token: account.token,
      oldPassword: pwdDraft.old,
      newPassword: pwdDraft.fresh,
    });
    if (status === 200 && payload.token) {
      // 本机换新 token 仍保持登录；凭据库里也换成新 token（ADR 0028）
      account = { email: payload.email || account.email, token: payload.token };
      try {
        await invoke("save_hosted_session", {
          email: account.email,
          token: account.token,
          remember: accountRemembered,
        });
      } catch {
        /* 记不住也已在内存换好新 token */
      }
      pwdDraft = { old: "", fresh: "" };
      pwdNote = "密码改好了。其它电脑上的登录已退出。";
      pwdNoteOk = true;
    } else if (payload.error?.includes("旧密码")) {
      pwdNote = "旧密码不对。";
      pwdNoteOk = false;
    } else if (status === 401) {
      pwdNote = "";
      account = null;
      panelTab = "listen";
      void invoke("clear_hosted_session");
    } else {
      pwdNote = payload.error || "没连上托管。";
      pwdNoteOk = false;
    }
  } catch {
    pwdNote = "没连上托管。";
    pwdNoteOk = false;
  } finally {
    pwdBusy = false;
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
  if (el.dataset?.combo && llmCombo) {
    // 打字收窄下拉；重绘后把焦点和光标放回输入框
    llmComboQuery = el.value;
    snapshotLlmDraft();
    render(true);
    refocusField(el.id);
  }
});

panelRoot.addEventListener("keydown", (e: KeyboardEvent) => {
  if (e.key === "Escape" && llmCombo) {
    closeCombo();
    render(true);
  }
});

panelRoot.addEventListener("click", (e) => {
  const raw = e.target as HTMLElement;
  const comboInput = raw.closest<HTMLElement>("[data-combo]");
  if (comboInput) {
    if (!llmCombo) {
      llmCombo = true;
      llmComboQuery = ""; // 打开时显示全部，别拿已选的值当过滤词
    }
    snapshotLlmDraft();
    render(true);
    ensureComboRoom(comboInput.id);
    refocusField(comboInput.id);
    return;
  }
  if (llmCombo && !raw.closest(".combo-list")) {
    closeCombo();
    render(true);
  }
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
  else if (act === "password") void submitPasswordChange();
  else if (act === "account") {
    snapshotLoginDraft();
    pwdNote = "";
    panelTab = "account";
    render();
  }
  else if (act === "logout") {
    // 服务端把这枚 token 作废（顺带停掉它开的听译），再清这台的登录
    const token = account?.token;
    if (token) void postAccount(hostedOriginUrl, "logout", { token }).catch(() => {});
    account = null;
    accountRemembered = false;
    pwdDraft = { old: "", fresh: "" };
    pwdNote = "";
    panelTab = "listen";
    void invoke("clear_hosted_session");
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
  else if (act === "combo-pick" && el.dataset.v) {
    snapshotLlmDraft();
    llmDraft.model = el.dataset.v;
    closeCombo();
    render();
  }
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
    // 拉到就展开给全部列表：观众不用再找下拉在哪
    llmCombo = r.ok && llmModels.length > 0;
    llmComboQuery = "";
    llmNote = r.preview || "拉模型结束。";
    if (r.ok && llmModels.length) {
      const probeNote = await autoProbeThinking();
      if (probeNote) llmNote = `${llmNote} ${probeNote}`;
    }
  } catch {
    llmNote = "拉模型失败。";
  } finally {
    llmBusy = false;
    render();
  }
}

/** 思考强度后台自动配：探测接口认哪个参数，直接挑最低档写进存档，观众无感。 */
async function autoProbeThinking(): Promise<string | null> {
  try {
    const p = await invoke<{
      ok: boolean;
      preview: string;
      recommended: { param: string; value: string } | null;
    }>("probe_llm_thinking", { config: llmPayload() });
    if (p.ok && p.recommended?.param) {
      llmDraft.thinkingParam = p.recommended.param;
      llmDraft.thinking = p.recommended.value;
      return `思考已自动调到最低（${p.recommended.param} · ${p.recommended.value}）。`;
    }
    return "思考按最通用参数带最低档，接口不认会自动去掉。";
  } catch {
    return null;
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
  hostedOriginUrl = hostedOrigin(boot.hostedOrigin);
  if (boot.hostedAccount?.email && boot.hostedAccount.token) {
    account = boot.hostedAccount;
    accountRemembered = !!boot.hostedRemembered;
  }
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
    const p = e.payload as { sourceLabel: string };
    dispatch(reduce(shell, { type: "source_switched", sourceLabel: p.sourceLabel, now: performance.now() }));
  });
  await listen("app://source_gone", () => {
    dispatch(reduce(shell, { type: "source_gone" }));
    // 音源没了，列表里的旧行也该消失
    void refreshSources(true);
  });
  await listen("hosted://account", (e) => {
    // 壳侧清了登录（登录失效 / 退出）：面板跟着回登录页
    if (e.payload == null && account) {
      account = null;
      accountRemembered = false;
      render();
    }
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
