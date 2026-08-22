/* 壳核心逻辑测试：事件流（缝协议）→ 字幕条 / 面板状态。
   外部行为不测内部：输入 = 缝事件 + phase/settings 变化 + 时钟 tick，输出 = 渲染状态。 */
import { describe, expect, it } from "vitest";
import {
  initialShellState,
  panelViewChanged,
  reduce,
  SILENT_WITHDRAW_MS,
  SWITCH_HINT_MS,
  type ShellState,
} from "../src/core/reducer";
import {
  applySettingsChange,
  loadSettings,
  sameSources,
  type AudioSource,
  type Settings,
} from "../src/core/settings";

let now = 0;
const tick = (ms: number) => {
  now += ms;
  return { type: "tick" as const, now };
};
const freshState = (): ShellState => {
  now = 0;
  return initialShellState();
};
const startListening = (state: ShellState): ShellState =>
  reduce(state, { type: "phase", phase: "listening", sourceLabel: "chrome.exe" });

describe("字幕条：草稿、定稿、切条、静默撤条", () => {
  it("草稿往外长，定稿冻住，静默约两秒后撤条", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "listen", event: { type: "draft", orig: "so we", trans: "" } });
    expect(s.bar).toMatchObject({ orig: "so we", kind: "draft" });
    s = reduce(s, { type: "listen", event: { type: "draft", orig: "so we're gonna", trans: "我们打算" } });
    expect(s.bar).toMatchObject({ orig: "so we're gonna", trans: "我们打算", kind: "draft" });
    s = reduce(s, { type: "listen", event: { type: "final", orig: "so we're gonna", trans: "我们打算试试" } });
    expect(s.bar).toMatchObject({ kind: "final", trans: "我们打算试试" });

    s = reduce(s, tick(SILENT_WITHDRAW_MS - 1));
    expect(s.bar, "差 1ms 不撤").not.toBeNull();
    s = reduce(s, tick(1));
    expect(s.bar, "满两秒直接拿掉，不淡出").toBeNull();
  });

  it("下一条草稿立刻挤掉上一条定稿，不等撤条计时", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "listen", event: { type: "final", orig: "a", trans: "甲" } });
    s = reduce(s, tick(300));
    s = reduce(s, { type: "listen", event: { type: "draft", orig: "b", trans: "乙" } });
    expect(s.bar).toMatchObject({ orig: "b", kind: "draft" });
    // 新条的撤条计时从头算
    s = reduce(s, { type: "listen", event: { type: "final", orig: "b", trans: "乙" } });
    s = reduce(s, tick(SILENT_WITHDRAW_MS));
    expect(s.bar).toBeNull();
  });

  it("提示行不抢字幕条：新草稿出现时提示照自己的计时走", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "listen", event: { type: "notice", kind: "not_lang" } });
    expect(s.hint).toContain("不是英 / 日 / 韩的人声");
    s = reduce(s, { type: "listen", event: { type: "draft", orig: "hi", trans: "嗨" } });
    expect(s.hint, "新条一出提示不闪断").toContain("不是英 / 日 / 韩的人声");
  });

  it("「不是英日韩」提示行几秒后自己拿掉，开听保持", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "listen", event: { type: "notice", kind: "not_lang" } });
    expect(s.phase).toBe("listening");
    expect(s.panelStatus).toMatchObject({ kind: "warn" });
    expect(s.panelStatus.text).toContain("没听出英 / 日 / 韩的人声");
    s = reduce(s, tick(3800));
    expect(s.hint).toBeNull();
  });
});

describe("四类失败态的出口", () => {
  it("没人声：画面不出东西，面板状态行说明", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "listen", event: { type: "notice", kind: "no_speech" } });
    expect(s.bar).toBeNull();
    expect(s.hint).toBeNull();
    expect(s.phase).toBe("listening");
    expect(s.panelStatus.text).toContain("还没听到人声");
  });

  it("不是英日韩：字幕位置提示 + 面板状态行，开听保持", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "listen", event: { type: "notice", kind: "not_lang" } });
    expect(s.phase).toBe("listening");
    expect(s.hint).toContain("听到声音");
  });

  it("音源抓不到：进 failed，面板状态行给两个出路（改系统混音 / 重试）", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "listen", event: { type: "notice", kind: "no_audio" } });
    expect(s.phase).toBe("failed");
    expect(s.panelStatus.kind).toBe("err");
    expect(s.panelStatus.text).toContain("抓不到声音");
    expect(s.failureKind).toBe("no_audio");
    expect(s.bar).toBeNull();
  });

  it("听译挂了：进 failed，状态行让点开听重试", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "listen", event: { type: "notice", kind: "crashed" } });
    expect(s.phase).toBe("failed");
    expect(s.panelStatus.text).toContain("重试");
    expect(s.bar).toBeNull();
  });

  it("开听中音源进程退出：停止开听，面板黄字让再选，不改成系统混音", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "listen", event: { type: "draft", orig: "hi", trans: "嗨" } });
    s = reduce(s, { type: "phase", phase: "idle" }); // Rust 先停
    s = reduce(s, { type: "source_gone" });
    expect(s.phase).toBe("idle");
    expect(s.bar).toBeNull();
    expect(s.panelStatus.kind).toBe("warn");
    expect(s.panelStatus.text).toContain("音源进程退出了");
    expect(s.panelStatus.text).toContain("不会自动改用系统混音");
    expect(s.sourceGone).toBe(true);
  });

  it("音源退出后重新选择，状态行立刻确认新的选择", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "phase", phase: "idle" });
    s = reduce(s, { type: "source_gone" });
    s = reduce(s, { type: "source_selected", sourceLabel: "discord.exe", audible: true });

    expect(s.panelStatus).toMatchObject({ kind: "info" });
    expect(s.panelStatus.text).toContain("已选 discord.exe");
    expect(s.panelStatus.text).toContain("按开听");
    expect(s.sourceGone).toBe(false);
  });

  it("选中暂未出声的已保存音源时，明确说明它仍在等待声音", () => {
    let s = freshState();
    s = reduce(s, { type: "source_selected", sourceLabel: "chrome.exe", audible: false });

    expect(s.panelStatus.text).toContain("暂未出声");
    expect(s.panelStatus.text).toContain("先开听");
  });

  it("重试：从 failed 回到在听，清掉失败残留", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "listen", event: { type: "notice", kind: "crashed" } });
    s = reduce(s, { type: "phase", phase: "listening", sourceLabel: "chrome.exe" });
    expect(s.phase).toBe("listening");
    expect(s.panelStatus.kind).toBe("ok");
    expect(s.bar).toBeNull();
  });
});

describe("开听 / 停止 / 换音源", () => {
  it("开听清屏，状态行点亮在听", () => {
    let s = freshState();
    s = reduce(s, { type: "listen", event: { type: "draft", orig: "x", trans: "y" } });
    s = startListening(s);
    expect(s.bar).toBeNull();
    expect(s.panelStatus.text).toContain("在听");
    expect(s.panelStatus.text).toContain("chrome.exe");
  });

  it("停止清屏回到没在听", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "listen", event: { type: "final", orig: "x", trans: "y" } });
    s = reduce(s, { type: "phase", phase: "idle" });
    expect(s.bar).toBeNull();
    expect(s.panelStatus.text).toContain("没在听");
  });

  it("开听中换音源：继续在听，字幕位置短提示", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "source_switched", sourceLabel: "discord.exe", now: (now = 1000) });
    expect(s.phase).toBe("listening");
    expect(s.hint).toBe("已换音源，继续在听");
    s = reduce(s, tick(SWITCH_HINT_MS - 1));
    expect(s.hint).not.toBeNull();
    s = reduce(s, tick(1));
    expect(s.hint).toBeNull();
  });

  it("换音源弃掉进行中的字幕条（新音源从新条开始）", () => {
    let s = startListening(freshState());
    s = reduce(s, { type: "listen", event: { type: "final", orig: "old", trans: "旧" } });
    s = reduce(s, { type: "source_switched", sourceLabel: "discord.exe", now: (now = 500) });
    expect(s.bar).toBeNull();
  });
});

describe("设置加载：上次音源不在了，停在面板再选", () => {
  const sources: AudioSource[] = [
    { id: "discord.exe", processName: "discord.exe", friendlyName: "Discord", audible: true, system: false },
    { id: "system", processName: "", friendlyName: "系统混音", audible: true, system: true },
  ];
  const saved: Settings = {
    source: "chrome.exe",
    mode: "trans",
    font: "l",
    face: "yahei",
    style: "outline",
    ink: "#ffffff",
    edge: "thick",
    plate: "none",
    weight: "bold",
    autostart: true,
    modelReady: true,
    listenWay: "local",
  };

  it("上次的进程不在清单里：保留原选择并标已退出，绝不改成系统混音", () => {
    const { settings, sourceDead } = loadSettings(saved, sources);
    expect(settings.source).toBe("chrome.exe");
    expect(sourceDead).toBe(true);
    expect(settings.mode).toBe("trans");
    expect(settings.font).toBe("l");
  });

  it("上次的进程还在：原样保留", () => {
    const { settings, sourceDead } = loadSettings(saved, [
      { id: "chrome.exe", processName: "chrome.exe", friendlyName: "Chrome", audible: true, system: false },
      ...sources,
    ]);
    expect(settings.source).toBe("chrome.exe");
    expect(sourceDead).toBe(false);
  });

  it("没存过：默认双语、中字号、不自启、没选音源", () => {
    const { settings, sourceDead } = loadSettings(undefined, sources);
    expect(settings).toMatchObject({ source: null, mode: "both", font: "m", face: "yahei", style: "outline", autostart: false });
    expect(sourceDead).toBe(false);
  });

  it("上次选的是系统混音：一直是有效音源", () => {
    const { settings, sourceDead } = loadSettings({ ...saved, source: "system" }, []);
    expect(settings.source).toBe("system");
    expect(sourceDead).toBe(false);
  });

  it("重新选择清单中的音源时，立刻撤掉旧音源已退出状态", () => {
    const view = applySettingsChange(
      { settings: saved, sourceDead: true },
      { source: "discord.exe" },
      sources
    );

    expect(view.settings.source).toBe("discord.exe");
    expect(view.sourceDead).toBe(false);
  });
});

describe("控制面板不必跟字幕条重绘", () => {
  it("草稿和定稿不改面板要画的字段", () => {
    let s = startListening(freshState());
    const afterDraft = reduce(s, {
      type: "listen",
      event: { type: "draft", orig: "so we", trans: "我们" },
    });
    expect(panelViewChanged(s, afterDraft)).toBe(false);
    const afterFinal = reduce(afterDraft, {
      type: "listen",
      event: { type: "final", orig: "so we", trans: "我们" },
    });
    expect(panelViewChanged(afterDraft, afterFinal)).toBe(false);
  });

  it("没人声提示要改状态行", () => {
    const s = startListening(freshState());
    const next = reduce(s, { type: "listen", event: { type: "notice", kind: "no_speech" } });
    expect(panelViewChanged(s, next)).toBe(true);
  });
});

describe("音源清单比较", () => {
  const list: AudioSource[] = [
    { id: "discord.exe", processName: "discord.exe", friendlyName: "Discord", audible: true, system: false },
    { id: "system", processName: "", friendlyName: "系统混音", audible: true, system: true },
  ];

  it("同序同字段视为没变", () => {
    expect(sameSources(list, list.map((s) => ({ ...s })))).toBe(true);
  });

  it("出声状态变了要重绘", () => {
    const next = list.map((s, i) => (i === 0 ? { ...s, audible: false } : s));
    expect(sameSources(list, next)).toBe(false);
  });
});
