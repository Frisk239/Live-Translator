"""多路 PCM 压测：在目标机器上扫并发路数，标定 LIVE_TRANSLATOR_HOSTED_MAX_ROUTES。

spec（hosted-listen-spec.md）定的路数闸：真正的 N 在目标机器上用多路 PCM 压测
（红线：开口延迟、RSS）后写进配置，不把任何数写死在代码里。本工具就是那场压测：

    python tools/load_probe.py --models-dir <模型目录> --levels 1,2,4,6,8

每档起满同样路数的真 WS 缝，同时按实时速率喂同一段英语素材，量：
- 每路「首草稿延迟」：该路第一块语音 PCM 发出 → 第一条草稿事件的墙钟差
  （口径对齐门禁开口红线：P95 ≤ 1500ms 算达标）；
- 服务进程 RSS 峰值（psutil，每秒采样）。
全档跑完输出表格和建议 N（最后一个每项都达标的档）。

不是单元测试：留给目标机器标定时手跑。开发机也能跑，但 N 只对目标机器有效。
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
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
REPO = SERVER.parent
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
WS = f"ws://127.0.0.1:{PORT}/listen"
WAV = REPO / "desktop" / "tests" / "fixtures" / "en_speech.wav"

FIRST_DRAFT_RED_LINE_MS = 1500.0  # 门禁开口红线的 P95 口径


def log(msg: str) -> None:
    print(f"[load] {msg}", flush=True)


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
    import wave

    with wave.open(str(WAV), "rb") as w:
        raw = w.readframes(w.getnframes())
    import numpy as np

    samples = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
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


class RssSampler:
    """双源采样：服务进程每 5s 自报的 `RSS <n>MB` 日志行（最可靠，开发沙箱里
    外部 psutil 读子进程会失真）+ 外部 psutil 读数，取全部峰值。"""

    def __init__(self, pid: int, log_path: str):
        self.pid = pid
        self.log_path = log_path
        self.peak_mb = 0.0
        self.source = "-"
        self._task: asyncio.Task | None = None

    def _sample(self) -> None:
        import re

        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.search(r"RSS (\d+)MB", line)
                    if m and int(m.group(1)) > self.peak_mb:
                        self.peak_mb = int(m.group(1))
                        self.source = "self-report"
        except Exception:
            pass
        try:
            import psutil

            rss = psutil.Process(self.pid).memory_info().rss / 1048576
            if rss > self.peak_mb:
                self.peak_mb = rss
                self.source = "psutil"
        except Exception:
            pass

    async def run(self) -> None:
        while True:
            self._sample()
            await asyncio.sleep(1.0)

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)


async def one_route(idx: int, token: str, pcm: bytes) -> dict:
    """一路：喂完整段语音 + 2s 静音逼切条，量首草稿延迟与定稿数。"""
    import websockets

    result: dict = {"idx": idx, "first_draft_ms": None, "finals": 0, "error": None}
    chunk = 1600 * 4  # 100ms 一块，按实时速率推（VAD / 切条按墙钟走）
    try:
        async with websockets.connect(WS, max_queue=4096) as ws:
            await ws.send(json.dumps({"type": "auth", "token": token}))
            await ws.send(json.dumps({"type": "start", "source": "system", "translate": "ct2"}))
            t0 = time.monotonic()

            async def pump() -> None:
                for i in range(0, len(pcm), chunk):
                    await ws.send(pcm[i : i + chunk])
                    await asyncio.sleep(0.09)
                for _ in range(20):  # 2s 静音逼口气停顿出定稿
                    await ws.send(b"\x00" * chunk)
                    await asyncio.sleep(0.09)

            pump_task = asyncio.create_task(pump())
            deadline = time.monotonic() + 180.0
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=10)
                except asyncio.TimeoutError:
                    if pump_task.done():
                        break
                    continue
                msg = json.loads(raw)
                if msg.get("type") == "draft" and result["first_draft_ms"] is None:
                    result["first_draft_ms"] = (time.monotonic() - t0) * 1000.0
                elif msg.get("type") == "final":
                    result["finals"] += 1
                if pump_task.done() and result["finals"] >= 1:
                    break
            try:
                # 管道背压时 ws.send 可能一直不返回：pump 不给兜底就会挂死整档
                await asyncio.wait_for(asyncio.shield(pump_task), timeout=60)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                pump_task.cancel()
                try:
                    await pump_task
                except (asyncio.CancelledError, Exception):
                    pass
            await ws.send(json.dumps({"type": "stop"}))
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)
    return result


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1))))
    return s[k]


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models-dir", required=True)
    parser.add_argument("--levels", default="1,2,4,6,8", help="逗号分隔的并发档位")
    parser.add_argument("--mem-red-line-mb", type=float, default=0.0, help="RSS 峰值红线，0 = 不设")
    args = parser.parse_args()
    if not WAV.is_file():
        log(f"缺少测试音频 {WAV}")
        return 2
    levels = [int(x) for x in args.levels.split(",") if x.strip()]
    max_level = max(levels)

    tmp = tempfile.mkdtemp(prefix="lt-load-")
    env = dict(
        os.environ,
        LIVE_TRANSLATOR_MODELS=str(Path(args.models_dir).resolve()),
        LIVE_TRANSLATOR_DB=os.path.join(tmp, "load.sqlite3"),
        LIVE_TRANSLATOR_PORT=str(PORT),
        LIVE_TRANSLATOR_HOST="127.0.0.1",
        LIVE_TRANSLATOR_HOSTED_MAX_ROUTES=str(max_level),
        LIVE_TRANSLATOR_HOSTED_IDLE_TIMEOUT="600",
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
    sampler = RssSampler(proc.pid, os.path.join(tmp, "server.log"))

    pcm = load_pcm_16k()
    log(f"素材 {len(pcm) / 4 / 16000:.1f}s 语音，档位 {levels}，满配 {max_level} 路")
    report: list[dict] = []
    try:
        await wait_port()
        sampler.start()
        log("服务进程已起，先单路预热模型…")
        warm = await one_route(0, (await make_token("warm@t.c")), pcm)
        log(f"预热完成（首草稿 {warm['first_draft_ms'] and round(warm['first_draft_ms'])}ms，定稿 {warm['finals']} 条）")

        for n in levels:
            tokens = [(await make_token(f"load-{n}-{i}@t.c")) for i in range(n)]
            sampler.peak_mb = 0.0
            log(f"— {n} 路并发开听…")
            t_start = time.monotonic()
            results = await asyncio.gather(*(one_route(i, tok, pcm) for i, tok in enumerate(tokens)))
            wall = time.monotonic() - t_start
            lat = [r["first_draft_ms"] for r in results if r["first_draft_ms"] is not None]
            errs = [r for r in results if r["error"]]
            finals_total = sum(r["finals"] for r in results)
            p50, p95 = pct(lat, 50), pct(lat, 95)
            row = {
                "routes": n,
                "first_draft_p50_ms": round(p50) if p50 is not None else None,
                "first_draft_p95_ms": round(p95) if p95 is not None else None,
                "finals_total": finals_total,
                "errors": len(errs),
                "rss_peak_mb": round(sampler.peak_mb),
                "wall_s": round(wall, 1),
            }
            row["ok"] = (
                row["first_draft_p95_ms"] is not None
                and row["first_draft_p95_ms"] <= FIRST_DRAFT_RED_LINE_MS
                and row["errors"] == 0
                and (args.mem_red_line_mb <= 0 or row["rss_peak_mb"] <= args.mem_red_line_mb)
            )
            report.append(row)
            log(
                f"  首草稿 P50={row['first_draft_p50_ms']}ms P95={row['first_draft_p95_ms']}ms，"
                f"定稿 {finals_total} 条，错误 {row['errors']}，RSS 峰值 {row['rss_peak_mb']}MB"
                f"（{sampler.source}），耗时 {row['wall_s']}s → {'达标' if row['ok'] else '不达标'}"
            )
            await asyncio.sleep(5)  # 档间缓口气，看内存回落

        log("")
        log("档位 | 首草稿P50 | 首草稿P95 | 定稿 | 错误 | RSS峰值 | 判定")
        for r in report:
            log(
                f"{r['routes']:>4} | {r['first_draft_p50_ms']:>8} | {r['first_draft_p95_ms']:>8} |"
                f" {r['finals_total']:>4} | {r['errors']:>4} | {r['rss_peak_mb']:>7} |"
                f" {'达标' if r['ok'] else '不达标'}"
            )
        ok_max = max((r["routes"] for r in report if r["ok"]), default=0)
        log("")
        log(
            f"建议：LIVE_TRANSLATOR_HOSTED_MAX_ROUTES={ok_max}"
            f"（最后达标的档；低于它取更小值保守上线）"
        )
        report_path = os.path.join(tmp, "load_report.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump({"red_line_first_draft_p95_ms": FIRST_DRAFT_RED_LINE_MS, "rows": report}, f, ensure_ascii=False, indent=2)
        log(f"报告：{report_path}")
        return 0
    finally:
        await sampler.stop()
        # 沙箱/代理环境下按 pid 杀常常杀到代理层：psutil 杀进程树，再按命令行
        # 兜底扫一遍 account.py（Windows 开发机只有压测会起它）
        for target in _server_processes(proc.pid):
            try:
                target.kill()
            except Exception:
                pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        out.close()
        log(f"服务日志在 {tmp}")


def _server_processes(pid: int) -> list:
    """要杀的进程：按 pid 的进程树 + 命令行里带 account.py 的 python。"""
    targets: list = []
    try:
        import psutil

        me = psutil.Process()
        for p in psutil.process_iter(["pid", "cmdline"]):
            if p.pid == me.pid:
                continue
            try:
                cmd = " ".join(p.info["cmdline"] or [])
            except Exception:
                cmd = ""
            if p.pid == pid or "account.py" in cmd:
                targets.append(p)
        parent = psutil.Process(pid)
        targets.extend(parent.children(recursive=True))
    except Exception:
        pass
    return targets


async def make_token(email: str) -> str:
    code, body = http_post("/account/register", {"email": email, "password": "secret12"})
    if code == 409:
        code, body = http_post("/account/login", {"email": email, "password": "secret12"})
    assert code == 200, body
    return body["token"]


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
