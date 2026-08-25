"""stop→start 会话隔离回归：真引擎下，旧会话在途的草稿/定稿不得漏进新会话。

风险来源：识别/翻译在线程池里跑，stop 到达时可能有一轮重活还没回来；没有
会话代守卫时，旧回调会在新 start 之后才发出，观众看到上一场直播的字幕。
模型缺失时整组跳过（与 test_segmenter 的集成用例同策略）。
pytest 跑：cd desktop && python -m pytest engine/tests -q
"""
import asyncio
import json
import os
import re
import sys
import time
import wave
from pathlib import Path

import pytest

pytest.importorskip("websockets")

ROOT = Path(__file__).resolve().parents[2]
MODELS = Path(
    os.environ.get(
        "LT_ENGINE_MODELS",
        Path.home() / "AppData" / "Roaming" / "com.livetranslator.desktop" / "models",
    )
)
NEEDED = [
    MODELS / "sense-voice" / "model.int8.onnx",
    MODELS / "sense-voice" / "tokens.txt",
    MODELS / "vad" / "silero_vad.onnx",
]
if not all(path.is_file() for path in NEEDED):
    pytest.skip("本机没有真听译模型", allow_module_level=True)
pytest.importorskip("sherpa_onnx")


def load_speech_pcm() -> "np.ndarray":
    """en_speech.wav（22.05k mono s16）→ 16k f32，与壳缝格式一致。"""
    import numpy as np

    with wave.open(str(ROOT / "tests" / "fixtures" / "en_speech.wav"), "rb") as w:
        assert w.getframerate() == 22050 and w.getnchannels() == 1 and w.getsampwidth() == 2
        pcm16 = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    ratio = 16000 / 22050
    n_out = int(len(pcm16) * ratio)
    idx = np.arange(n_out) / ratio
    i0 = np.clip(idx.astype(np.int64), 0, len(pcm16) - 1)
    i1 = np.clip(i0 + 1, 0, len(pcm16) - 1)
    frac = (idx - i0).astype(np.float32)
    return (pcm16[i0] * (1 - frac) + pcm16[i1] * frac).astype(np.float32)


async def spawn_engine():
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(ROOT / "engine" / "real_listen.py"),
        "--port",
        "0",
        "--models-dir",
        str(MODELS),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    port = None
    loaded = False
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=5)
        except asyncio.TimeoutError:
            continue
        if not line:
            break
        text_line = line.decode(errors="replace").strip()
        m = re.match(r"READY\s+(\d+)", text_line)
        if m:
            port = int(m.group(1))
        if text_line == "LOADED":
            loaded = True
        if port is not None and loaded:
            break

    async def drain():
        while True:
            line = await proc.stderr.readline()
            if not line:
                return

    if port is None or not loaded:
        proc.kill()
        await proc.wait()
        raise RuntimeError("真听译 120 秒未 READY/LOADED")
    asyncio.create_task(drain())
    return proc, port


async def run_scenario() -> None:
    import websockets

    proc, port = await spawn_engine()
    try:
        async with websockets.connect(
            f"ws://127.0.0.1:{port}", max_size=2**22, ping_interval=30, ping_timeout=120
        ) as ws:
            events: list[dict] = []

            async def reader():
                try:
                    async for raw in ws:
                        events.append(json.loads(raw))
                except Exception:
                    pass

            read_task = asyncio.create_task(reader())
            pcm = load_speech_pcm()
            frame = 1600  # 100ms

            await ws.send(json.dumps({"type": "start", "source": "restart-test.exe"}))
            await asyncio.sleep(0.1)
            for off in range(0, min(len(pcm), 6 * 16000), frame):
                await ws.send(pcm[off : off + frame].astype("<f4").tobytes())
                await asyncio.sleep(0.1)

            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not any(
                e.get("type") in ("draft", "final") for e in events
            ):
                await asyncio.sleep(0.2)
            assert any(e.get("type") in ("draft", "final") for e in events), (
                f"语音阶段应至少出一条草稿或定稿，实得 {events!r}"
            )

            # stop → 立即 start → 只灌静音。旧会话在途的重活结果若漏，会落进这个窗口。
            await ws.send(json.dumps({"type": "stop"}))
            await ws.send(json.dumps({"type": "start", "source": "restart-test.exe"}))
            mark = len(events)
            silence = bytes(frame * 4)
            for _ in range(30):
                await ws.send(silence)
                await asyncio.sleep(0.1)
            await asyncio.sleep(1.5)

            leaked = [e for e in events[mark:] if e.get("type") in ("draft", "final")]
            assert not leaked, f"新会话漏进了旧会话的字幕事件：{leaked!r}"
            read_task.cancel()
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()


def test_stop_start_does_not_leak_old_session_events():
    asyncio.run(run_scenario())
