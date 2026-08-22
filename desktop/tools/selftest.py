"""三语自测：真实播放 → 系统环回采音 → 真听译引擎 → 断言草稿 / 定稿。

覆盖整条物理链：扬声器输出（pyaudiowpatch output stream 播 ogg 解码出的 PCM）
→ WASAPI 默认设备环回（就是「系统混音」那条路）→ 16k mono f32 二进制帧入缝
→ SenseVoice / OPUS-MT → draft / final / notice 事件。

素材：Wikimedia Commons 公开文件（tests/fixtures/selftest_{en,ja,ko}.ogg）。
依赖：soundfile pyaudiowpatch websockets numpy（引擎侧依赖见 engine/requirements.txt）

用法：python tools/selftest.py [--models-dir <dir>] [--only en,ja]
注意：会占用扬声器约 1 分钟；跑之前别把系统静音。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
MODELS_DEFAULT = Path.home() / "AppData" / "Roaming" / "com.livetranslator.desktop" / "models"

SR = 16000

LANG_CHECK = {
    "en": (re.compile(r"[A-Za-z]{3,}"), "拉丁词"),
    "ja": (re.compile(r"[\u3040-\u30ff]"), "假名"),
    "ko": (re.compile(r"[\uac00-\ud7af]"), "谚文"),
}


def resample(x: np.ndarray, frm: int) -> np.ndarray:
    if frm == SR or len(x) == 0:
        return x.astype(np.float32)
    ratio = SR / frm
    n_out = int(len(x) * ratio)
    idx = np.arange(n_out) / ratio
    i0 = np.clip(idx.astype(np.int64), 0, len(x) - 1)
    i1 = np.clip(i0 + 1, 0, len(x) - 1)
    frac = (idx - i0).astype(np.float32)
    return (x[i0] * (1 - frac) + x[i1] * frac).astype(np.float32)


def load_pcm16(path: Path) -> np.ndarray:
    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    return data, sr


class Loopback:
    """默认播放设备的 WASAPI 环回采集（系统混音），参照 reference/LiveTranslate 只学接口。"""

    def __init__(self):
        import pyaudiowpatch as pyaudio

        self.pa = pyaudio.PyAudio()
        wasapi = None
        for i in range(self.pa.get_host_api_count()):
            info = self.pa.get_host_api_info_by_index(i)
            if "WASAPI" in info["name"]:
                wasapi = info
                break
        if wasapi is None:
            raise RuntimeError("没有 WASAPI 宿主")
        default_out = self.pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
        self.dev = None
        for i in range(self.pa.get_device_count()):
            d = self.pa.get_device_info_by_index(i)
            if d.get("isLoopbackDevice") and default_out["name"] in d["name"]:
                self.dev = d
                break
        if self.dev is None:
            raise RuntimeError("找不到默认输出的环回设备")
        self.channels = self.dev["maxInputChannels"]
        self.rate = int(self.dev["defaultSampleRate"])

    def start(self, on_chunk, loop):
        """loop：事件循环引用。回调在 PortAudio 线程里跑，
        必须用显式 loop 调度发送（线程里 get_event_loop 拿不到主循环）。"""
        import pyaudiowpatch as pyaudio
        import websockets as _ws

        def cb(_in, frame_count, _ti, _st):
            try:
                audio = np.frombuffer(_in, dtype=np.float32)
                if self.channels > 1:
                    audio = audio.reshape(-1, self.channels).mean(axis=1)
                chunk = resample(audio, self.rate).astype("<f4").tobytes()
                asyncio.run_coroutine_threadsafe(on_chunk(chunk), loop)
            except Exception:
                pass
            return (None, 0)  # pyaudioContinue

        self.stream = self.pa.open(
            format=pyaudio.paFloat32,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=self.dev["index"],
            frames_per_buffer=int(self.rate * 0.1),
            stream_callback=cb,
        )
        self.stream.start_stream()

    def stop(self):
        try:
            self.stream.stop_stream()
            self.stream.close()
        except Exception:
            pass
        self.pa.terminate()


pyaudiowpatch_continue = 0  # pyaudioContinue == 0


def play_to_speaker(data: np.ndarray, sr: int):
    """用默认输出设备把 PCM 播出去（自测的「声音从哪来」）。"""
    import pyaudiowpatch as pyaudio

    pa = pyaudio.PyAudio()
    wasapi = None
    for i in range(pa.get_host_api_count()):
        info = pa.get_host_api_info_by_index(i)
        if "WASAPI" in info["name"]:
            wasapi = info
            break
    dev = pa.get_device_info_by_index(wasapi["defaultOutputDevice"])
    ch = min(2, dev["maxOutputChannels"])
    rate = int(dev["defaultSampleRate"])
    # 重采样到设备率 + 声道
    x = data
    if x.ndim == 1 and ch == 2:
        x = np.stack([x, x], axis=1)
    ratio = rate / sr
    n = int(len(x) * ratio)
    idx = np.arange(n) / ratio
    i0 = np.clip(idx.astype(np.int64), 0, len(x) - 1)
    i1 = np.clip(i0 + 1, 0, len(x) - 1)
    frac = (idx - i0).astype(np.float32)
    out = (x[i0] * (1 - frac)[..., None] + x[i1] * frac[..., None]) if x.ndim > 1 else (
        x[i0] * (1 - frac) + x[i1] * frac
    )
    pcm16 = (np.clip(out, -1, 1) * 32767).astype(np.int16)
    stream = pa.open(
        format=pyaudio.paInt16,
        channels=ch,
        rate=rate,
        output=True,
        output_device_index=dev["index"],
    )
    CHUNK = 4096
    for i in range(0, len(pcm16), CHUNK):
        chunk = pcm16[i : i + CHUNK]
        stream.write(chunk.tobytes(), exception_on_underflow=False)
    stream.stop_stream()
    stream.close()
    pa.terminate()


async def run_language(lang: str, models_dir: Path, engine_port: int) -> bool:
    import websockets

    fixture = FIXTURES / f"selftest_{lang}.ogg"
    data, sr = load_pcm16(fixture)
    print(f"[{lang}] 素材 {fixture.name}：{len(data)/sr:.0f}s @ {sr}Hz")

    events: list[dict] = []
    lb = Loopback()
    loop = asyncio.get_running_loop()
    async with websockets.connect(
        f"ws://127.0.0.1:{engine_port}", max_size=2**22, ping_interval=30, ping_timeout=60
    ) as ws:
        await ws.send(json.dumps({"type": "start", "source": "selftest.exe"}))

        # 从一开始就边收边存，别让接收缓冲堆满
        async def reader():
            try:
                async for m in ws:
                    events.append(json.loads(m))
            except Exception:
                pass

        read_task = asyncio.create_task(reader())
        lb.start(ws.send, loop)
        print(f"[{lang}] 环回采集：{lb.dev['name']} @ {lb.rate}Hz x{lb.channels}；开始播放…")
        t0 = time.monotonic()

        await loop.run_in_executor(None, lambda: play_to_speaker(data, sr))
        print(f"[{lang}] 播放完（{time.monotonic()-t0:.0f}s），已收 {len(events)} 个事件，等切条收尾…")
        await asyncio.sleep(6)
        lb.stop()
        try:
            await ws.send(json.dumps({"type": "stop"}))
        except Exception:
            pass
        await asyncio.sleep(1)
        read_task.cancel()

    pattern, label = LANG_CHECK[lang]
    drafts = [e for e in events if e["type"] == "draft"]
    finals = [e for e in events if e["type"] == "final"]
    all_text = " ".join(e["orig"] for e in drafts + finals)
    trans_text = " ".join(e["trans"] for e in finals)
    ok_lang = bool(pattern.search(all_text))
    ok_trans = bool(re.search(r"[\u4e00-\u9fff]", trans_text)) if lang != "zh" else True
    print(f"[{lang}] draft x{len(drafts)}, final x{len(finals)}")
    for f in finals[:3]:
        print(f"   FINAL {f['orig'][:60]!r} -> {f['trans'][:40]!r}")
    verdict = ok_lang and (len(finals) > 0)
    print(f"[{lang}] 原文含{label}: {ok_lang} | 定稿数>0: {len(finals) > 0} | 译文含中文: {ok_trans}")
    if not events:
        print(f"[{lang}] 一个事件都没有：检查系统是否静音 / 默认输出与环回设备是否一致")
    return verdict


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", type=Path, default=MODELS_DEFAULT)
    parser.add_argument("--only", type=str, default="en,ja,ko")
    args = parser.parse_args()

    eng = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "engine" / "real_listen.py"),
            "--port",
            "0",
            "--models-dir",
            str(args.models_dir),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    port = None
    while True:
        line = eng.stdout.readline()
        if not line:
            print("引擎启动失败")
            eng.kill()
            return 2
        if line.startswith("READY"):
            port = int(line.split()[1])
            break
    print(f"引擎 READY @ {port}，等模型预加载…")
    await asyncio.sleep(12)

    results = {}
    try:
        for lang in [x.strip() for x in args.only.split(",") if x.strip()]:
            results[lang] = await run_language(lang, args.models_dir, port)
    finally:
        eng.kill()

    print("\n=== 自测结果 ===")
    for lang, ok in results.items():
        print(f"  {lang}: {'PASS' if ok else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
