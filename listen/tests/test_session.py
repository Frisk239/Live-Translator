"""缝协议：start 不加载模型；托管强制 ct2；PCM 走同一份会话。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1].parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from listen.session import EngineHolder, ListenSession  # noqa: E402


class StubEngine:
    def __init__(self):
        self.llm_direct = False
        self.empty_hits = 0
        self.last_not_lang_at = -1e9
        self.busy = False
        self.reset_calls = 0
        self.processed = 0

    def reset_session(self):
        self.reset_calls += 1

    def wait_llm(self, timeout=3.0):
        return

    def process(self, pcm, seg, now, on_draft, on_final):
        self.processed += 1
        on_draft("hello there", "你好")


def test_start_does_not_load_engine():
    loaded: list[int] = []

    def factory():
        loaded.append(1)
        raise RuntimeError("start 不该加载模型")

    async def run():
        sent = []

        async def send_json(obj):
            sent.append(obj)

        holder = EngineHolder(Path("."), enable_llm=False, factory=factory)
        session = ListenSession(send_json, holder, force_ct2=True)
        await session.on_text(
            json.dumps({"type": "start", "source": "system", "translate": "llm"})
        )
        assert session.live["started"] is True
        assert session.live["llm_direct"] is False
        assert loaded == []
        await session.on_text(json.dumps({"type": "stop"}))
        assert session.live["started"] is False
        await session.close()

    asyncio.run(run())


def test_pcm_after_start_emits_draft():
    async def run():
        sent = []

        async def send_json(obj):
            sent.append(obj)

        stub = StubEngine()
        holder = EngineHolder(Path("."), enable_llm=False, factory=lambda: stub)
        session = ListenSession(send_json, holder, force_ct2=True)
        session.start_background()
        await session.on_text(
            json.dumps({"type": "start", "source": "system", "translate": "ct2"})
        )
        pcm = np.zeros(512, dtype="<f4").tobytes()
        await session.on_bytes(pcm)
        for _ in range(80):
            if any(e.get("type") == "draft" for e in sent):
                break
            await asyncio.sleep(0.02)
        await session.close()
        assert stub.processed >= 1
        drafts = [e for e in sent if e.get("type") == "draft"]
        assert drafts and drafts[0]["orig"] == "hello there"
        assert drafts[0]["trans"] == "你好"

    asyncio.run(run())
