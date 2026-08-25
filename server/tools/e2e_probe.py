"""托管缝端到端探针：起真服务进程，验满听译、满员、顶号三条对外行为。

用法（模型目录必给，路数上限 1 便于一次跑完三道闸）：

    python tools/e2e_probe.py --models-dir <模型目录>

不是单元测试：留给上线前 / 换机器时手跑，验证部署形态（uvicorn 进程 + SQLite 文件库
+ 真模型 + 真 WebSocket 缝），与 `python account.py` 的运行方式一致。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import wave
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
REPO = SERVER.parent
PORT = 8797
BASE = f"http://127.0.0.1:{PORT}"
WS = f"ws://127.0.0.1:{PORT}/listen"
WAV = REPO / "desktop" / "tests" / "fixtures" / "en_speech.wav"


def log(msg: str) -> None:
    print(f"[e2e] {msg}", flush=True)


def http_post(path: str, body: dict) -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def load_pcm_16k() -> bytes:
    with wave.open(str(WAV), "rb") as w:
        raw = w.readframes(w.getnframes())
    import numpy as np

    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    # 22050 → 16000 线性重采样（与壳侧采音统一到的格式一致）
    n = int(len(samples) * 16000 / 22050)
    idx = np.linspace(0, len(samples) - 1, n)
    out = np.interp(idx, np.arange(len(samples)), samples).astype(np.float32)
    return out.tobytes()


async def wait_port(timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{BASE}/docs", timeout=1)
            return
        except Exception:
            await asyncio.sleep(0.3)
    raise RuntimeError("服务进程没在限时内起来")


async def collect_until_final(ws, timeout: float = 90.0) -> tuple[list[dict], dict | None]:
    """收事件直到出现带中文译文的定稿（或超时）。"""
    events: list[dict] = []
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    final = None
    while loop.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - loop.time())
        except asyncio.TimeoutError:
            break
        msg = json.loads(raw)
        events.append(msg)
        if msg.get("type") == "final" and msg.get("trans"):
            final = msg
            break
    return events, final


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", required=True)
    args = parser.parse_args()
    if not WAV.is_file():
        log(f"缺少测试音频 {WAV}")
        return 2

    tmp = tempfile.mkdtemp(prefix="lt-e2e-")
    db_path = os.path.join(tmp, "e2e.sqlite3")
    env = dict(
        os.environ,
        LIVE_TRANSLATOR_MODELS=str(Path(args.models_dir).resolve()),
        LIVE_TRANSLATOR_DB=db_path,
        LIVE_TRANSLATOR_PORT=str(PORT),
        LIVE_TRANSLATOR_HOST="127.0.0.1",
        LIVE_TRANSLATOR_HOSTED_MAX_ROUTES="1",
        LIVE_TRANSLATOR_HOSTED_IDLE_TIMEOUT="300",
    )
    out = open(os.path.join(tmp, "server.log"), "wb")
    proc = subprocess.Popen(
        [sys.executable, "account.py"],
        cwd=SERVER,
        env=env,
        stdout=out,
        stderr=subprocess.STDOUT,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    import websockets

    ok = True
    try:
        await wait_port()
        log("服务进程已起（真 uvicorn + SQLite 文件库）")

        code, body = http_post("/account/register", {"email": "e2e-a@t.c", "password": "secret12"})
        assert code == 200, body
        token_a = body["token"]
        code, body = http_post("/account/login", {"email": "e2e-a@t.c", "password": "secret12"})
        assert code == 200, body
        token_a2 = body["token"]
        http_post("/account/register", {"email": "e2e-b@t.c", "password": "secret12"})
        code, body = http_post("/account/login", {"email": "e2e-b@t.c", "password": "secret12"})
        assert code == 200, body
        token_b = body["token"]
        log("注册 / 登录走通，拿到两账号的 token")

        # 第一路：真模型听真英语，收草稿与带译文的定稿
        async with websockets.connect(WS) as ws1:
            await ws1.send(json.dumps({"type": "auth", "token": token_a}))
            await ws1.send(json.dumps({"type": "start", "source": "system", "translate": "ct2"}))
            pcm = load_pcm_16k()
            chunk = 1600 * 4  # 100ms 一块，按实时速率推（VAD / 切条按墙钟走）
            for i in range(0, len(pcm), chunk):
                await ws1.send(pcm[i : i + chunk])
                await asyncio.sleep(0.09)
            # 语音推完后灌 3 秒静音，逼口气停顿切条出定稿
            for _ in range(30):
                await ws1.send(b"\x00" * chunk)
                await asyncio.sleep(0.09)
            log(f"PCM 已喂（{len(pcm) / 4 / 16000:.1f}s 语音 + 3s 静音），等字幕…")
            events, final = await collect_until_final(ws1)
            drafts = [e for e in events if e["type"] == "draft"]
            if final:
                # 草稿节奏是听译内部行为（listen/ 单测与本机缝测试覆盖），这里只管定稿管线
                log(
                    f"PASS 满听译：定稿原文={final['orig']!r} 译文={final['trans']!r}"
                    f"（途中草稿 {len(drafts)} 条）"
                )
            else:
                ok = False
                log(f"FAIL 满听译：没等到带译文的定稿，事件序列={events[:6]}")

            # 第二账号：路数上限 1，应收「满员」
            async with websockets.connect(WS) as ws2:
                await ws2.send(json.dumps({"type": "auth", "token": token_b}))
                msg = json.loads(await asyncio.wait_for(ws2.recv(), timeout=10))
                if msg.get("kind") == "full":
                    log("PASS 满员：第二账号被拒并收到 full 说明")
                else:
                    ok = False
                    log(f"FAIL 满员：收到 {msg}")

            # 同账号第二路（顶号）：第一路应收「被顶」并断开
            async with websockets.connect(WS) as ws3:
                await ws3.send(json.dumps({"type": "auth", "token": token_a2}))
                kicked = False
                try:
                    while True:
                        raw = await asyncio.wait_for(ws1.recv(), timeout=15)
                        msg = json.loads(raw)
                        if msg.get("kind") == "kicked":
                            kicked = True
                            break
                except Exception:
                    pass
                if kicked:
                    log("PASS 顶号：先开的一路收到 kicked 说明")
                else:
                    ok = False
                    log("FAIL 顶号：先开的一路没收到 kicked")
                await ws3.send(json.dumps({"type": "start", "source": "system", "translate": "ct2"}))
                await ws3.send(json.dumps({"type": "stop"}))
            try:
                await asyncio.wait_for(ws1.recv(), timeout=5)
                ok = False
                log("FAIL 顶号：被顶的连接居然还活着")
            except Exception:
                log("PASS 顶号：被顶的连接已断")

        log("全部通过" if ok else "有失败项")
        return 0 if ok else 1
    finally:
        if sys.platform == "win32":
            proc.terminate()
        else:
            proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        out.close()
        log(f"服务日志与临时库在 {tmp}")


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
