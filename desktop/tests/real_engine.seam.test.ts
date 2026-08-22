/* 真听译的缝测试：夹具 wav → PCM 二进制帧 → 真引擎（SenseVoice + OPUS-MT）→ draft/final。
   模型未下载（LT_ENGINE_MODELS 或默认 app_data 目录不全）时整组跳过。 */
import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { homedir } from "node:os";
import { describe, expect, it } from "vitest";
import WebSocket from "ws";
import type { ListenEvent } from "../src/core/events";

const MODELS_DIR =
  process.env.LT_ENGINE_MODELS ??
  join(homedir(), "AppData", "Roaming", "com.livetranslator.desktop", "models");

const REQUIRED = [
  "sense-voice/model.int8.onnx",
  "sense-voice/tokens.txt",
  "vad/silero_vad.onnx",
  "opus-en-zh/tokenizer.json",
  "opus-en-zh-ct2/config.json",
  "opus-en-zh-ct2/model.bin",
  "opus-en-zh-ct2/shared_vocabulary.json",
];

const modelsReady = REQUIRED.every((rel) => {
  const f = join(MODELS_DIR, rel);
  return existsSync(f) && (f.endsWith(".json") || readFileSync(f).length > 100);
});

/** 起真听译子进程，等到模型 LOADED（READY 只代表端口通了，模型未必加载完）。 */
async function spawnEngineUntilLoaded(noticeScale: string) {
  const child = spawn(
    process.env.PYTHON || "python",
    [
      join(__dirname, "..", "engine", "real_listen.py"),
      "--port",
      "0",
      "--models-dir",
      MODELS_DIR,
      "--notice-scale",
      noticeScale,
    ],
    { stdio: ["ignore", "pipe", "pipe"] }
  );
  let stderr = "";
  child.stderr!.on("data", (c) => (stderr += c));
  const port = await new Promise<number>((resolve, reject) => {
    let got = 0; // 位标记：1=READY，2=LOADED；两条行可能分属不同 chunk
    let readyPort: number | null = null;
    const timer = setTimeout(
      () => reject(new Error("真听译 90s 未就绪（READY+LOADED）\n" + stderr)),
      90_000
    );
    child.stdout!.on("data", (chunk: Buffer) => {
      const text = chunk.toString();
      const m = /READY (\d+)/.exec(text);
      if (m) {
        readyPort = Number(m[1]);
        got |= 1;
      }
      if (/LOADED/.test(text)) got |= 2;
      if (got === 3 && readyPort !== null) {
        clearTimeout(timer);
        resolve(readyPort);
      }
    });
    child.on("exit", (code) => reject(new Error(`引擎提前退出 code=${code}\n${stderr}`)));
  });
  return { child, port, stderr: () => stderr };
}

describe.skipIf(!modelsReady)("缝：真听译（需模型，缺则跳过）", () => {
  it("英语 wav → PCM → 草稿与定稿都回；译文是中文", async () => {
    const engine = await spawnEngineUntilLoaded("0.15");
    const child = engine.child;
    const ws = new WebSocket(`ws://127.0.0.1:${engine.port}`);
    await new Promise<void>((res, rej) => {
      ws.once("open", () => res());
      ws.once("error", rej);
    });

    const events: ListenEvent[] = [];
    ws.on("message", (raw: WebSocket.RawData) => {
      events.push(JSON.parse(raw.toString()) as ListenEvent);
    });

    // wav（16k mono s16）→ f32 块，100ms 一帧
    const wav = readFileSync(join(__dirname, "fixtures", "en_speech.wav"));
    const pcm = new Int16Array(wav.buffer, 44, (wav.length - 44) / 2);
    const f32 = new Float32Array(pcm.length);
    for (let i = 0; i < pcm.length; i++) f32[i] = pcm[i] / 32768;

    ws.send(JSON.stringify({ type: "start", source: "test.exe" }));
    const CHUNK = 1600; // 100ms 一块，按实时速率推（VAD / 切条按墙钟走）
    for (let i = 0; i < f32.length; i += CHUNK) {
      ws.send(Buffer.from(f32.slice(i, i + CHUNK).buffer, 0, Math.min(CHUNK, f32.length - i) * 4));
      await new Promise((r) => setTimeout(r, 90));
    }
    // 语音推完后灌 3 秒静音，逼口气停顿切条出定稿
    const silence = Buffer.alloc(CHUNK * 4);
    for (let i = 0; i < 30; i++) {
      ws.send(silence);
      await new Promise((r) => setTimeout(r, 90));
    }

    // 最多等 20s 收 final
    const deadline = Date.now() + 20_000;
    while (Date.now() < deadline && !events.some((e) => e.type === "final")) {
      await new Promise((r) => setTimeout(r, 400));
    }

    const drafts = events.filter((e): e is Extract<ListenEvent, { type: "draft" }> => e.type === "draft");
    const finals = events.filter((e): e is Extract<ListenEvent, { type: "final" }> => e.type === "final");
    const notices = events.filter((e): e is Extract<ListenEvent, { type: "notice" }> => e.type === "notice");

    expect(drafts.length + finals.length, "至少出过草稿或定稿").toBeGreaterThan(0);
    const origText = [...drafts, ...finals].map((e) => e.orig).join(" ").toLowerCase();
    expect(origText, `识别内容应含关键词，实得「${origText}」`).toMatch(/boss|fight|hp|regroup|thousand|okay/);
    const zh = finals.map((f) => f.trans).join("");
    if (finals.length > 0) {
      expect(zh, "定稿译文应是中文").toMatch(/[\u4e00-\u9fff]/);
    }
    expect(notices.every((n) => n.kind !== "crashed"), "引擎不该挂").toBe(true);

    ws.close();
    child.kill();
  }, 120_000);

  it("纯静音 → no_speech 提示，不出字幕条", async () => {
    const engine = await spawnEngineUntilLoaded("0.15");
    const child = engine.child;
    const ws = new WebSocket(`ws://127.0.0.1:${engine.port}`);
    await new Promise<void>((res, rej) => {
      ws.once("open", () => res());
      ws.once("error", rej);
    });
    const events: ListenEvent[] = [];
    ws.on("message", (raw: WebSocket.RawData) => {
      events.push(JSON.parse(raw.toString()) as ListenEvent);
    });
    ws.send(JSON.stringify({ type: "start", source: "test.exe" }));
    const silence = Buffer.alloc(1600 * 4); // 100ms 静音块
    // 灌 6 秒静音（notice-scale 0.15 → no_speech 约 1.8s 出）
    for (let i = 0; i < 60; i++) {
      ws.send(silence);
      await new Promise((r) => setTimeout(r, 40));
    }
    const kinds = events.filter((e) => e.type === "notice").map((e) => (e as { kind: string }).kind);
    expect(kinds, `应出 no_speech，实得 ${JSON.stringify(events)}`).toContain("no_speech");
    expect(events.some((e) => e.type === "draft" || e.type === "final"), "静音不该出字幕条").toBe(false);
    ws.close();
    child.kill();
  }, 90_000);
});
