/* 缝测试：直接打壳 ↔ 听译的唯一接缝。
   起假听译 Python 进程（随机端口），按 WS 协议发 start/switch/stop，
   断言回放的草稿 / 定稿 / 提示事件跟原型 SCRIPTS 时间轴一致。
   用例数据 = chanpin/desktop/prototype/app.js 的 SCRIPTS。 */
import { spawn, type ChildProcess } from "node:child_process";
import { mkdtemp } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { beforeAll, describe, expect, it } from "vitest";
import WebSocket from "ws";
import type { ListenEvent, ShellCommand } from "../src/core/events";

const SPEED = 10; // 时间轴加速 10 倍，全套几十毫秒级跑完

class FakeListen {
  child: ChildProcess;
  port: number;
  ws: WebSocket;

  static async start(script: string): Promise<FakeListen> {
    const child = spawn("python", [join(__dirname, "..", "fake-listen", "fake_listen.py"), "--port", "0"], {
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stderr = "";
    child.stderr!.on("data", (c) => (stderr += c));
    const ready = new Promise<number>((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("假听译 10s 未就绪\n" + stderr)), 10_000);
      child.stdout!.on("data", (chunk: Buffer) => {
        const m = /READY (\d+)/.exec(chunk.toString());
        if (m) {
          clearTimeout(timer);
          resolve(Number(m[1]));
        }
      });
      child.on("exit", (code) => reject(new Error(`假听译提前退出 code=${code}\n${stderr}`)));
    });
    const port = await ready;
    const ws = new WebSocket(`ws://127.0.0.1:${port}`);
    await new Promise<void>((resolve, reject) => {
      ws.once("open", () => resolve());
      ws.once("error", reject);
    });
    const fl = new FakeListen(child, port, ws);
    fl.send({ type: "start", source: "chrome.exe", playback: { script, speed: SPEED } });
    return fl;
  }

  private constructor(child: ChildProcess, port: number, ws: WebSocket) {
    this.child = child;
    this.port = port;
    this.ws = ws;
  }

  send(cmd: ShellCommand) {
    this.ws.send(JSON.stringify(cmd));
  }

  /** 收 n 个事件的序列（未收到的留在缓冲里给下一次用） */
  async events(n: number, timeoutMs = 8000): Promise<ListenEvent[]> {
    const out: ListenEvent[] = [];
    let buffer: ListenEvent[] = (this as FakeListen & { _buf?: ListenEvent[] })._buf ?? [];
    while (out.length < n) {
      if (buffer.length) {
        out.push(buffer.shift()!);
        continue;
      }
      buffer = await new Promise<ListenEvent[]>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error(`等第 ${out.length + 1} 个事件超时；已收到 ${JSON.stringify(out)}`)), timeoutMs);
        this.ws.once("message", (raw: WebSocket.RawData) => {
          clearTimeout(timer);
          resolve([JSON.parse(raw.toString()) as ListenEvent]);
        });
        this.ws.once("error", reject);
      });
    }
    (this as FakeListen & { _buf?: ListenEvent[] })._buf = buffer;
    return out;
  }

  /** 断言 quietMs 内没有再出事件 */
  async expectSilence(quietMs: number) {
    const got = await new Promise<ListenEvent | null>((resolve) => {
      const timer = setTimeout(() => resolve(null), quietMs);
      this.ws.once("message", (raw: WebSocket.RawData) => {
        clearTimeout(timer);
        resolve(JSON.parse(raw.toString()) as ListenEvent);
      });
    });
    expect(got, "这段不该有事件").toBeNull();
  }

  close() {
    try {
      this.ws.close();
    } catch {
      /* already closed */
    }
    this.child.kill();
  }
}

const isDraft = (e: ListenEvent): e is Extract<ListenEvent, { type: "draft" }> => e.type === "draft";
const isFinal = (e: ListenEvent): e is Extract<ListenEvent, { type: "final" }> => e.type === "final";

beforeAll(async () => {
  await mkdtemp(join(tmpdir(), "lt-seam-")); // 探路：tmpdir 不可用就第一个红
});

describe("缝：假听译回放原型时间轴", () => {
  it("en：草稿往外长、中途改、定稿冻结、下一条挤掉上一条", async () => {
    const fl = await FakeListen.start("en");
    try {
      const evs = await fl.events(12);
      const firstBar = evs.slice(0, evs.findIndex((e) => e.type === "final"));
      const drafts = firstBar.filter(isDraft);
      // 第一条：原文 / 译文快照各自往外长（draft 每次带整条组合快照，取去重序列）
      const dedupe = (xs: string[]) => [...new Set(xs)];
      expect(dedupe(drafts.map((e) => e.orig))).toEqual([
        "so we",
        "so we're gonna",
        "so we're gonna try this",
        "so we're gonna try this boss fight",
      ]);
      // 译文草稿中途改：不是一步到位
      const transSnaps = dedupe(drafts.map((e) => e.trans).filter(Boolean));
      expect(transSnaps[0]).toBe("我们");
      expect(transSnaps).toContain("我们打算");
      // 定稿 = 冻住的整条（原型 fin=4800 时刻，o/x 各自最后快照）
      const final = evs.find(isFinal)!;
      expect(final).toEqual({
        type: "final",
        orig: "so we're gonna try this boss fight",
        trans: "我们打算试试这个 Boss 战",
      });
      // 定稿之后紧跟着下一条的草稿（挤掉上一条，不堆历史）
      const afterFinal = evs.slice(evs.indexOf(final) + 1);
      expect(isDraft(afterFinal[0])).toBe(true);
      if (isDraft(afterFinal[0])) expect(afterFinal[0].orig).toBe("he's got like");
    } finally {
      fl.close();
    }
  });

  it("rapid：连珠炮按硬切节奏连发四条", async () => {
    const fl = await FakeListen.start("rapid");
    try {
      // 每条 cue = o×2 + x×2 + final；看满三条定稿再带出第四条的草稿
      const evs = await fl.events(17);
      const finals = evs.filter(isFinal);
      expect(finals.map((e) => e.trans)).toEqual([
        "然后我们就直接从这边绕过去",
        "因为对面肯定想不到我们会走这边",
        "所以只要不打起来我们就稳了",
      ]);
      expect(finals[0].orig).toBe("and then we just go around from here");
      // 第四条也在路上（连发，中间 gap 只有 160ms）
      expect(evs.some((e) => isDraft(e) && e.orig === "you know what")).toBe(true);
    } finally {
      fl.close();
    }
  });

  it("pause：一条定稿后到下一条之间留出长静默（给壳演示撤条）", async () => {
    const fl = await FakeListen.start("pause");
    try {
      const evs = await fl.events(6);
      const finalIdx = evs.findIndex((e) => e.type === "final");
      // 定稿之后、下一条草稿之前，没有任何事件（gap=3400ms，静默撤条交给壳）
      const next = evs[finalIdx + 1];
      expect(isDraft(next)).toBe(true);
      if (isDraft(next)) expect(next.orig).toBe("okay,");
    } finally {
      fl.close();
    }
  });

  it("silence：只回「没人声」提示，不出字幕条", async () => {
    const fl = await FakeListen.start("silence");
    try {
      const evs = await fl.events(1);
      expect(evs[0]).toEqual({ type: "notice", kind: "no_speech" });
      await fl.expectSilence(500);
    } finally {
      fl.close();
    }
  });

  it("music：回「不是英 / 日 / 韩的人声」提示", async () => {
    const fl = await FakeListen.start("music");
    try {
      const evs = await fl.events(1);
      expect(evs[0]).toEqual({ type: "notice", kind: "not_lang" });
    } finally {
      fl.close();
    }
  });

  it("perm：立即回「音源抓不到」提示", async () => {
    const fl = await FakeListen.start("perm");
    try {
      const evs = await fl.events(1);
      expect(evs[0]).toEqual({ type: "notice", kind: "no_audio" });
    } finally {
      fl.close();
    }
  });

  it("crash：回「听译挂了」提示", async () => {
    const fl = await FakeListen.start("crash");
    try {
      const evs = await fl.events(1);
      expect(evs[0]).toEqual({ type: "notice", kind: "crashed" });
    } finally {
      fl.close();
    }
  });

  it("switch：开听中换音源，弃掉当前条、从新音源重新回放", async () => {
    const fl = await FakeListen.start("en");
    try {
      await fl.events(3); // 长到第一条中途
      fl.send({ type: "switch", source: "discord.exe" });
      const evs = await fl.events(4);
      // 从头重放：最初草稿重新出现。switch 发出瞬间的旧草稿可能挤进窗口
      // （CI 慢机上常见），不锁死第一条，只断言重放已从头开始
      expect(evs).toContainEqual({ type: "draft", orig: "so we", trans: "" });
    } finally {
      fl.close();
    }
  });

  it("stop：停止后不再回事件", async () => {
    const fl = await FakeListen.start("en");
    try {
      await fl.events(2);
      fl.send({ type: "stop" });
      await fl.expectSilence(600);
    } finally {
      fl.close();
    }
  });

  it("二进制 PCM 帧：假听译忽略，不打断 JSON 语义", async () => {
    const fl = await FakeListen.start("en");
    try {
      // 100ms 的 16kHz mono f32le 静音块，混在回放中持续灌
      const pcm = Buffer.alloc(4 * 16000 * 0.1);
      const timer = setInterval(() => fl.ws.send(pcm), 100);
      try {
        const evs = await fl.events(4);
        // 时间轴照常：第一条草稿原样往外长
        expect(evs[0]).toEqual({ type: "draft", orig: "so we", trans: "" });
        expect(evs.some((e) => e.type === "draft" && e.orig === "so we're gonna")).toBe(true);
      } finally {
        clearInterval(timer);
      }
    } finally {
      fl.close();
    }
  });
});
