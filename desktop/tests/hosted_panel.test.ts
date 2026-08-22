import { describe, expect, it } from "vitest";
import { listenPane } from "../src/core/hosted";
import { loadSettings } from "../src/core/settings";

describe("控制面板听译页：本机还是登录提示", () => {
  it("未登录选托管时整页是登录提示", () => {
    expect(listenPane("hosted", null)).toBe("login");
  });

  it("本机未登录仍是音源和开听", () => {
    expect(listenPane("local", null)).toBe("sources");
  });

  it("旧的本机 LLM 存档当成本机", () => {
    expect(listenPane("local", null)).toBe("sources");
  });

  it("托管已登录后露出音源和开听", () => {
    expect(listenPane("hosted", { email: "a@b.c" })).toBe("sources");
  });
});

describe("听译方式存档", () => {
  it("没存过默认本机", () => {
    expect(loadSettings(undefined, []).settings.listenWay).toBe("local");
  });

  it("上次选的托管还在", () => {
    expect(loadSettings({ listenWay: "hosted" }, []).settings.listenWay).toBe("hosted");
  });

  it("上次选的本机 LLM 收成本机", () => {
    expect(loadSettings({ listenWay: "local_llm" }, []).settings.listenWay).toBe("local");
  });

  it("字幕样式缺省为雅黑描边白字", () => {
    const { settings } = loadSettings(undefined, []);
    expect(settings.face).toBe("yahei");
    expect(settings.style).toBe("outline");
    expect(settings.ink).toBe("#ffffff");
    expect(settings.edge).toBe("thick");
    expect(settings.plate).toBe("none");
  });
});
