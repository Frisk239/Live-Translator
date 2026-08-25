"""听译缝会话：start/switch/stop + PCM → draft/final/notice。

本机 websockets sidecar 与托管 FastAPI 都喂这一层，不在对端重写切条/草稿。
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import numpy as np

from listen.engine import (
    NO_AUDIO_MS,
    NO_SPEECH_MS,
    NOT_LANG_COOLDOWN_MS,
    NOT_LANG_EMPTY_HITS,
    Engine,
    Segmenter,
    _debug_log,
    models_present,
)

SendJson = Callable[[dict[str, Any]], Awaitable[None]]


class EngineHolder:
    """一份模型。会话状态仍在 Engine 上，这一刀一路占用。"""

    def __init__(
        self,
        models_dir: Path,
        *,
        enable_llm: bool = True,
        executor=None,
        factory=None,
    ):
        self.models_dir = Path(models_dir)
        self.enable_llm = enable_llm
        self.executor = executor
        self._factory = factory
        self._engine: Engine | None = None
        self._lock = asyncio.Lock()
        self.error: str | None = None

    def peek(self) -> Engine | None:
        return self._engine

    def _load(self) -> Engine:
        if self._factory is not None:
            return self._factory()
        if not models_present(self.models_dir):
            raise FileNotFoundError(f"听译模型不在 {self.models_dir}")
        return Engine(self.models_dir, enable_llm=self.enable_llm)

    async def get(self) -> Engine:
        if self._engine is not None:
            return self._engine
        async with self._lock:
            if self._engine is not None:
                return self._engine
            loop = asyncio.get_running_loop()
            self._engine = await loop.run_in_executor(self.executor, self._load)
            return self._engine

    async def preload(self) -> Engine | None:
        try:
            eng = await self.get()
        except Exception as exc:  # noqa: BLE001
            self.error = str(exc)
            print(f"听译模型没装上：{exc}", flush=True)
            return None
        warmup = getattr(eng, "warmup_translation", None)
        if warmup:
            threading.Thread(target=warmup, daemon=True).start()
        print("LOADED", flush=True)
        return eng


class ListenSession:
    def __init__(
        self,
        send_json: SendJson,
        holder: EngineHolder,
        notice_scale: float = 1.0,
        *,
        force_ct2: bool = False,
    ):
        self.send_json = send_json
        self.holder = holder
        self.notice_scale = notice_scale
        self.force_ct2 = force_ct2
        self.seg = Segmenter()
        self.live: dict[str, Any] = {
            "started": False,
            "got_pcm_at": None,
            "last_voice_at": None,
            "last_no_speech_at": -1e9,
            "started_at": 0.0,
            "gen": 0,
            "llm_direct": False,
        }
        self.pcm_queue: asyncio.Queue[tuple[int, np.ndarray]] = asyncio.Queue()
        self.should_close = False
        self._worker: asyncio.Task | None = None
        self._watchdog: asyncio.Task | None = None

    @staticmethod
    def tnow() -> float:
        return time.monotonic()

    def start_background(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._heavy_worker())
        if self._watchdog is None:
            self._watchdog = asyncio.create_task(self._watchdog_loop())

    async def crash(self, reason: str) -> None:
        print(reason, flush=True)
        self.should_close = True
        self.live["started"] = False
        try:
            await self.send_json({"type": "notice", "kind": "crashed"})
        except Exception:
            pass

    async def on_bytes(self, raw: bytes) -> None:
        if not self.live["started"] or self.should_close:
            return
        if self.holder.error:
            await self.crash(f"听译模型没装上：{self.holder.error}")
            return
        if self.live["got_pcm_at"] is None:
            self.live["got_pcm_at"] = self.tnow()
        self.pcm_queue.put_nowait((self.live["gen"], np.frombuffer(raw, dtype="<f4").copy()))

    async def on_text(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        if not isinstance(msg, dict):
            return
        mtype = msg.get("type")
        if mtype == "start":
            self.live["started"] = True
            self.live["started_at"] = self.tnow()
            self.live["gen"] += 1
            self.live["llm_direct"] = False if self.force_ct2 else msg.get("translate") == "llm"
            self.seg.reset()
            eng = self.holder.peek()
            if eng is not None:
                eng.reset_session()
                eng.llm_direct = bool(self.live["llm_direct"])
        elif mtype == "switch":
            self.live["gen"] += 1
            self.seg.reset()
            eng = self.holder.peek()
            if eng is not None:
                eng.reset_session()
        elif mtype == "stop":
            eng = self.holder.peek()
            if eng is not None:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(self.holder.executor, lambda: eng.wait_llm(3.0))
            self.live["started"] = False
            self.live["got_pcm_at"] = None
            self.live["gen"] += 1
            if eng is not None:
                eng.reset_session()
            self.seg.reset()

    async def close(self) -> None:
        self.live["started"] = False
        self.live["gen"] += 1
        tasks = [t for t in (self._worker, self._watchdog) if t is not None]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._worker = None
        self._watchdog = None
        self.seg.reset()

    def cancel_background(self) -> None:
        """只取消后台任务、不等它们结束：调用方自己正被取消、不能再 await 时用。"""
        for t in (self._worker, self._watchdog):
            if t is not None:
                t.cancel()
        self._worker = None
        self._watchdog = None
        self.live["started"] = False
        self.live["gen"] += 1

    async def serve_ws(self, ws) -> None:
        """websockets 库：async for 得到 str / bytes。"""
        self.start_background()
        try:
            async for raw in ws:
                if self.should_close:
                    break
                if isinstance(raw, bytes):
                    await self.on_bytes(raw)
                else:
                    text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
                    await self.on_text(text)
        finally:
            await self.close()

    async def _send_event(self, kind: str, orig: str, trans: str, gen: int) -> None:
        if not self.live["started"] or self.live["gen"] != gen:
            return
        _debug_log(kind, orig, trans)
        await self.send_json({"type": kind, "orig": orig, "trans": trans})

    def _threadsafe_cb(self, kind: str, gen: int):
        loop = asyncio.get_running_loop()

        def wrapper(orig, trans):
            if not self.live["started"] or self.live["gen"] != gen:
                return
            asyncio.run_coroutine_threadsafe(self._send_event(kind, orig, trans, gen), loop)

        return wrapper

    async def _heavy_worker(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            try:
                gen, first = await self.pcm_queue.get()
                frames = [first]
                while True:
                    try:
                        item_gen, item = self.pcm_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if item_gen != gen:
                        continue
                    frames.append(item)
                if self.live["gen"] != gen:
                    continue
                try:
                    eng = await self.holder.get()
                except Exception as exc:  # noqa: BLE001
                    await self.crash(f"听译模型没装上：{exc}")
                    continue
                pcm = np.concatenate(frames)
                draft_cb = self._threadsafe_cb("draft", gen)
                final_cb = self._threadsafe_cb("final", gen)
                eng.llm_direct = bool(self.live.get("llm_direct", False))
                eng.busy = True
                try:
                    await loop.run_in_executor(
                        self.holder.executor,
                        lambda: eng.process(pcm, self.seg, self.tnow(), draft_cb, final_cb),
                    )
                finally:
                    eng.busy = False
                if self.seg.last_voice_at is not None:
                    self.live["last_voice_at"] = self.seg.last_voice_at
            except asyncio.CancelledError:
                raise
            except Exception:
                traceback.print_exc()

    async def _watchdog_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            if not self.live["started"]:
                continue
            now = self.tnow()
            if self.live["got_pcm_at"] is None and now - self.live["started_at"] >= (
                NO_AUDIO_MS * self.notice_scale
            ) / 1000:
                await self.send_json({"type": "notice", "kind": "no_audio"})
                self.live["started"] = False
                continue
            if self.live["got_pcm_at"] is not None:
                silent_for = self.live["last_voice_at"] or self.live["started_at"]
                no_voice = now - silent_for >= (NO_SPEECH_MS * self.notice_scale) / 1000
                if no_voice and now - self.live["last_no_speech_at"] >= (
                    NO_SPEECH_MS * self.notice_scale
                ) / 1000:
                    self.live["last_no_speech_at"] = now
                    await self.send_json({"type": "notice", "kind": "no_speech"})
                eng = self.holder.peek()
                if (
                    eng is not None
                    and eng.empty_hits >= NOT_LANG_EMPTY_HITS
                    and now - eng.last_not_lang_at >= NOT_LANG_COOLDOWN_MS / 1000
                ):
                    eng.last_not_lang_at = now
                    eng.empty_hits = 0
                    await self.send_json({"type": "notice", "kind": "not_lang"})
