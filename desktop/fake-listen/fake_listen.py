"""假听译：回放原型时间轴，经本机 WebSocket 回事件。

缝协议（与真听译共用同一条缝；真听译忽略 playback 字段）：
  壳 → 听译: {"type":"start","source":"chrome.exe","playback":{"script":"en","speed":1}}
            {"type":"switch","source":"discord.exe"}
            {"type":"stop"}
  听译 → 壳: {"type":"draft","orig":"so we","trans":"我们"}     草稿（整条当前快照）
            {"type":"final","orig":"...","trans":"..."}          定稿（冻结）
            {"type":"notice","kind":"no_speech|not_lang|no_audio|crashed"}  提示

SCRIPTS 时间轴用例数据译自 chanpin/desktop/prototype/app.js（用例数据，不是代码迁移）。
speed 把整条时间轴除以该倍速，测试用来加速；产品用 1。

用法: python fake_listen.py [--port N]
      --port 0 绑定随机端口，就绪后向 stdout 打一行 READY <port>。
"""
from __future__ import annotations

import argparse
import asyncio
import json

import websockets

# 原型 SCRIPTS：cue.o / cue.x = [时刻, 快照]；fin = 定稿时刻；gap = 定稿后到下一条的静默。
SCRIPTS: dict[str, dict] = {
    "en": {
        "lang": "英",
        "cues": [
            {
                "o": [[0, "so we"], [600, "so we're gonna"], [1300, "so we're gonna try this"], [2200, "so we're gonna try this boss fight"]],
                "x": [[1000, "我们"], [1800, "我们打算"], [2600, "我们打算试试"], [3400, "我们打算试试这个"], [4300, "我们打算试试这个 Boss 战"]],
                "fin": 4800, "gap": 1300,
            },
            {
                "o": [[0, "he's got like"], [700, "he's got like three hundred"], [1600, "he's got like three hundred HP... wait"], [2500, "he's got like three thousand HP"]],
                "x": [[900, "他有大概"], [1700, "他有大概三百"], [2500, "他有大概三百血"], [3400, "他有大概三千血"]],
                "fin": 4000, "gap": 1000,
            },
            {
                "o": [[0, "nope, that's not"], [800, "nope, that's not gonna work"]],
                "x": [[1000, "不行"], [1800, "不行，这样"], [2700, "不行，这样行不通"]],
                "fin": 3300, "gap": 2700,
            },
            {
                "o": [[0, "okay let's"], [700, "okay let's regroup"], [1500, "okay let's regroup and try again"]],
                "x": [[900, "好，"], [1700, "好，我们重整"], [2600, "好，我们重整再来一次"]],
                "fin": 3100, "gap": 1200,
            },
            {
                "o": [[0, "chat, what do you think"], [900, "chat, what do you think about this build"]],
                "x": [[1100, "你们"], [1900, "你们觉得"], [2800, "你们觉得这套构筑"], [3700, "你们觉得这套构筑怎么样"]],
                "fin": 4200, "gap": 1500,
            },
            {
                "o": [[0, "alright,"], [600, "alright, back to"], [1400, "alright, back to the grind"]],
                "x": [[800, "好，"], [1500, "好，回去"], [2300, "好，回去继续肝"]],
                "fin": 2900, "gap": 1700,
            },
        ],
    },
    "ja": {
        "lang": "日",
        "cues": [
            {
                "o": [[0, "あの、"], [600, "あの、今日は"], [1400, "あの、今日は配信を"], [2300, "あの、今日は配信を見てくれて"]],
                "x": [[900, "那个，"], [1700, "那个，今天"], [2600, "那个，谢谢大家"], [3500, "那个，谢谢大家今天来看直播"]],
                "fin": 4000, "gap": 1400,
            },
            {
                "o": [[0, "ちょっと"], [700, "ちょっと待って"], [1500, "ちょっと待ってください"]],
                "x": [[900, "稍等"], [1800, "请稍等一下"]],
                "fin": 2400, "gap": 1600,
            },
            {
                "o": [[0, "この武器が"], [800, "この武器が強くて"], [1600, "この武器が強くてびっくりした"]],
                "x": [[1000, "这把武器"], [1900, "这把武器强到"], [2800, "这把武器强到吓我一跳"]],
                "fin": 3400, "gap": 2900,
            },
        ],
    },
    # 连珠炮：跟嘴切不过来，按硬切换条（约 16 字 / 6 秒，先到为准），不完美也换
    "rapid": {
        "lang": "英",
        "cues": [
            {
                "o": [[0, "and then we just go"], [500, "and then we just go around from here"]],
                "x": [[250, "然后我们就"], [650, "然后我们就直接从这边绕过去"]],
                "fin": 1000, "gap": 160,
            },
            {
                "o": [[0, "'cause there's no way"], [500, "'cause there's no way they expect us here"]],
                "x": [[250, "因为对面"], [650, "因为对面肯定想不到我们会走这边"]],
                "fin": 1000, "gap": 160,
            },
            {
                "o": [[0, "so as long as we don't"], [500, "so as long as we don't fight we're fine"]],
                "x": [[250, "所以只要"], [650, "所以只要不打起来我们就稳了"]],
                "fin": 1000, "gap": 160,
            },
            {
                "o": [[0, "you know what"], [450, "you know what I mean"]],
                "x": [[250, "懂我"], [600, "懂我意思吧"]],
                "fin": 950, "gap": 1400,
            },
        ],
    },
    "pause": {
        "lang": "英",
        "cues": [
            {
                "o": [[0, "let me think"], [700, "let me think for a second"]],
                "x": [[800, "让我"], [1500, "让我想一下"]],
                "fin": 2300, "gap": 3400,
            },
            {
                "o": [[0, "okay,"], [500, "okay, got it"]],
                "x": [[700, "好，"], [1200, "好，有了"]],
                "fin": 1900, "gap": 3400,
            },
        ],
    },
    # 失败脚本：onset 毫秒后回一条提示，不出字幕条
    "silence": {"lang": "英", "cues": [], "notices": [[2600, "no_speech"]]},
    "music": {"lang": "？", "cues": [], "notices": [[1600, "not_lang"]]},
    "perm": {"lang": "英", "cues": [], "notices": [[0, "no_audio"]]},
    "crash": {"lang": "英", "cues": [], "notices": [[4500, "crashed"]]},
}


class Session:
    """一条 WS 连接 = 一段回放。start/switch 取消旧回放重新开始；stop 只取消。"""

    def __init__(self, ws) -> None:
        self.ws = ws
        self.task: asyncio.Task | None = None

    def cancel(self) -> None:
        if self.task:
            self.task.cancel()
            self.task = None

    async def send(self, payload: dict) -> None:
        await self.ws.send(json.dumps(payload, ensure_ascii=False))

    async def play(self, script_key: str, speed: float) -> None:
        script = SCRIPTS.get(script_key)
        if script is None:
            await self.send({"type": "notice", "kind": "crashed"})
            return
        scale = 1.0 / max(speed, 0.01)
        now = asyncio.get_running_loop().time
        for at_ms, kind in script.get("notices", []):
            await asyncio.sleep(at_ms * scale / 1000)
            await self.send({"type": "notice", "kind": kind})
        cues = script["cues"]
        i = 0
        while True:
            cue = cues[i % len(cues)]
            events: list[tuple[float, str, str]] = []
            for at_ms, text in cue["o"]:
                events.append((at_ms * scale / 1000, "o", text))
            for at_ms, text in cue["x"]:
                events.append((at_ms * scale / 1000, "x", text))
            events.append((cue["fin"] * scale / 1000, "final", ""))
            events.sort(key=lambda e: e[0])
            started = now()
            bar = {"o": "", "x": ""}
            for at_s, kind, text in events:
                await asyncio.sleep(max(0.0, started + at_s - now()))
                if kind in ("o", "x"):
                    bar[kind] = text
                    await self.send({"type": "draft", "orig": bar["o"], "trans": bar["x"]})
                else:
                    await self.send({"type": "final", "orig": bar["o"], "trans": bar["x"]})
            await asyncio.sleep(cue["gap"] * scale / 1000)
            i += 1


async def handle(ws) -> None:
    session = Session(ws)
    playback: dict = {"script": "en", "speed": 1.0}
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            mtype = msg.get("type")
            if mtype == "start":
                playback = msg.get("playback") or {"script": "en", "speed": 1.0}
                session.cancel()
                session.task = asyncio.ensure_future(
                    session.play(str(playback.get("script", "en")), float(playback.get("speed", 1)))
                )
            elif mtype == "switch":
                # 换音源：立刻弃掉当前条，从新音源重新听（回放从头）
                session.cancel()
                session.task = asyncio.ensure_future(
                    session.play(str(playback["script"]), float(playback["speed"]))
                )
            elif mtype == "stop":
                session.cancel()
    finally:
        session.cancel()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8730)
    args = parser.parse_args()
    async with websockets.serve(handle, "127.0.0.1", args.port) as server:
        port = server.sockets[0].getsockname()[1]
        print(f"READY {port}", flush=True)
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
