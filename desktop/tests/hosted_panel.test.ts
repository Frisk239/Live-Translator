import { describe, expect, it, vi, afterEach } from "vitest";
import { DEFAULT_HOSTED_ORIGIN, hostedOrigin, listenPane, postAccount } from "../src/core/hosted";
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
    expect(listenPane("hosted", { email: "a@b.c", token: "t" })).toBe("sources");
  });
});

describe("托管源", () => {
  it("没有覆盖时用写死的源", () => {
    expect(hostedOrigin()).toBe(DEFAULT_HOSTED_ORIGIN);
    expect(hostedOrigin("")).toBe(DEFAULT_HOSTED_ORIGIN);
  });

  it("开发可用环境变量盖掉", () => {
    expect(hostedOrigin("http://127.0.0.1:9999/")).toBe("http://127.0.0.1:9999");
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

describe("账号缝 JSON POST", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("按路径 POST 并把 JSON 带回来", async () => {
    const fetchMock = vi.fn(
      async (_url: string, _init?: RequestInit) =>
        new Response(JSON.stringify({ email: "a@b.c", token: "t1" }), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const out = await postAccount("http://127.0.0.1:8787", "login", {
      email: "a@b.c",
      password: "secret12",
    });

    expect(out.status).toBe(200);
    expect(out.payload.token).toBe("t1");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://127.0.0.1:8787/account/login");
    expect(init?.method).toBe("POST");
  });

  it("服务端回的不是 JSON 也当空对象，不炸", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("gateway timeout", { status: 502 })),
    );

    const out = await postAccount("http://127.0.0.1:8787", "logout", { token: "t" });

    expect(out.status).toBe(502);
    expect(out.payload).toEqual({});
  });
});
