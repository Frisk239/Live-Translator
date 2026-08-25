"""本机听译 sidecar：把仓库根（开发）或安装包旁的 listen/ 放进 path，再跑缝。"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import websockets


def _listen_root() -> Path:
    here = Path(__file__).resolve().parent
    for cand in (here.parent, here.parent.parent, here.parent.parent.parent):
        if (cand / "listen" / "engine.py").is_file():
            return cand
    return here.parent.parent


_ROOT = _listen_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from listen import engine as _engine
from listen.engine import _raise_process_priority_above_normal
from listen.session import EngineHolder, ListenSession

for _k, _v in vars(_engine).items():
    if _k.startswith("__") and _k.endswith("__"):
        continue
    globals()[_k] = _v


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8731)
    parser.add_argument("--models-dir", type=Path, default=Path("models"))
    parser.add_argument("--notice-scale", type=float, default=1.0)
    args = parser.parse_args()

    _raise_process_priority_above_normal()
    holder = EngineHolder(args.models_dir, enable_llm=True)

    async def handle(ws):
        async def send_json(obj):
            await ws.send(json.dumps(obj, ensure_ascii=False))

        session = ListenSession(send_json, holder, notice_scale=args.notice_scale)
        await session.serve_ws(ws)

    async with websockets.serve(
        handle,
        "127.0.0.1",
        args.port,
        ping_interval=None,
    ) as server:
        port = server.sockets[0].getsockname()[1]
        print(f"READY {port}", flush=True)
        asyncio.create_task(holder.preload())
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
