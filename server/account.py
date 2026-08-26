"""托管服务（ADR 0005/0014/0018）：账号 HTTPS JSON + 听译 WebSocket 调度。

账号与登录会话落在 Store（默认 SQLite，配 DSN 走 Postgres）；
在听路数登记在进程内（Routes），DB 里的在听行只做记录，起来时清脏（ADR 0019）。
顶号与满员是两道闸（ADR 0016）：同一账号后开顶先开，跨账号只看路数与内存。
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import hmac
import json
import os
import secrets
import sys
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from store import Store, default_store, now_ms  # noqa: E402

AUTH_TIMEOUT_S = 5.0


class AccountIn(BaseModel):
    email: str
    password: str
    rememberMe: bool = False  # 记住我是壳的事（ADR 0027），服务端只管发 token


class TokenIn(BaseModel):
    token: str


class PasswordIn(BaseModel):
    token: str
    oldPassword: str
    newPassword: str


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _hash_password(password: str, salt: bytes | None = None) -> tuple[bytes, bytes]:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000)
    return salt, digest


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _default_max_routes() -> int:
    # 启动按核数给保守默认（ADR 0015）：一路实时听译吃约 4 核；真正的 N 靠压测写配置
    return max(1, min(8, (os.cpu_count() or 4) // 4))


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_origins(name: str) -> list[str] | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    return [o.strip() for o in raw.split(",") if o.strip()]


def client_source(request: Request, trust_proxy: bool) -> str:
    """限流与在听记录用的来源地址。直连时就是对端 IP；反代后面只有配了
    TRUST_PROXY 才认 X-Forwarded-For，且只信最右一跳——那是本机反代亲眼
    看到的客户端地址，攻击者自带的假 XFF 会被反代追加在左边（ADR 0030 后续）。"""
    if trust_proxy:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[-1].strip()
    return request.client.host if request.client else "?"


def _default_infer_workers() -> int:
    # 有界推理池（ADR 0021）：同时跑的路数个位数，其余排队
    return max(1, min(8, (os.cpu_count() or 4) // 2))


def _available_mb() -> float | None:
    try:
        import psutil

        return psutil.virtual_memory().available / (1024 * 1024)
    except Exception:
        pass
    try:
        if sys.platform == "win32":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
            return stat.ullAvailPhys / (1024 * 1024)
        with open("/proc/meminfo", encoding="ascii") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return float(line.split()[1]) / 1024
    except Exception:
        pass
    return None


class LoginThrottle:
    """同一来源短时间登录失败太多次则暂拒，过一会儿自动恢复；不锁账号（ADR 0030）。"""

    def __init__(self, max_fails: int, window_s: float, cooldown_s: float):
        self.max_fails = max_fails
        self.window_s = window_s
        self.cooldown_s = cooldown_s
        self._fails: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def wait_left(self, key: str) -> float:
        now = time.monotonic()
        with self._lock:
            fails = self._prune(key, now)
            if len(fails) < self.max_fails:
                return 0.0
            return max(0.0, fails[-1] + self.cooldown_s - now)

    def record_fail(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, deque()).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)

    def _prune(self, key: str, now: float) -> deque[float]:
        fails = self._fails.get(key, deque())
        while fails and now - fails[0] > self.window_s:
            fails.popleft()
        return fails


class Route:
    def __init__(self, email: str, token: str, started_ms: int, task: asyncio.Task, send):
        self.email = email
        self.token = token
        self.started_ms = started_ms
        self.task = task
        self.send = send


class Routes:
    """在听路数登记：顶号与满员都在开听这一刻判定（spec / ADR 0024）。进程内权威。"""

    def __init__(self, max_routes: int):
        self.max_routes = max_routes
        self.draining = False  # 优雅退出：不再接新路，与满员同一句说明（ADR 0026）
        self._lock = asyncio.Lock()
        self._routes: dict[str, Route] = {}

    async def admit(self, email: str, token: str, send) -> Route | None:
        async with self._lock:
            if self.draining:
                return None
            old = self._routes.get(email)
            if old is not None:
                await self._kick(old, "kicked")
            if len(self._routes) >= self.max_routes:
                return None
            route = Route(email, token, now_ms(), asyncio.current_task(), send)
            self._routes[email] = route
            return route

    async def kick_token(self, email: str, token: str, kind: str) -> None:
        """只挤用这枚 token 开的那一路（退出登录：本机停，别机不动）。"""
        async with self._lock:
            route = self._routes.get(email)
            if route is not None and route.token == token:
                await self._kick(route, kind)

    async def kick_other_tokens(self, email: str, token: str, kind: str) -> None:
        """挤掉这账号里除这枚 token 外的在听（改密码：其它电脑停，本机不动，ADR 0028）。"""
        async with self._lock:
            route = self._routes.get(email)
            if route is not None and route.token != token:
                await self._kick(route, kind)

    def leave(self, route: Route) -> None:
        # 同步放路数：调用方可能正被取消（被顶 / 测试退出），那条路径上不能再 await。
        # 只删属于自己的登记，与 admit/kick 的字典操作在 GIL 下不互斥也不丢账。
        if self._routes.get(route.email) is route:
            del self._routes[route.email]

    async def _kick(self, route: Route, kind: str) -> None:
        self._routes.pop(route.email, None)
        try:
            await route.send({"type": "notice", "kind": kind})
        except Exception:
            pass
        route.task.cancel()


def _models_dir() -> Path:
    env = os.environ.get("LIVE_TRANSLATOR_MODELS")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent / "models"


def _models_ready(models_dir: Path) -> bool:
    need = (
        "sense-voice/model.int8.onnx",
        "sense-voice/tokens.txt",
        "vad/silero_vad.onnx",
    )
    return all((models_dir / rel).is_file() and (models_dir / rel).stat().st_size > 100 for rel in need)


async def _send_notice(ws: WebSocket, kind: str) -> None:
    try:
        await ws.send_text(json.dumps({"type": "notice", "kind": kind}))
    except Exception:
        pass


def create_app(
    store: Store | None = None,
    *,
    max_routes: int | None = None,
    infer_workers: int | None = None,
    idle_timeout: float | None = None,
    mem_floor_mb: float | None = None,
    login_max_fails: int | None = None,
    login_window_s: float | None = None,
    login_cooldown_s: float | None = None,
    trust_proxy: bool | None = None,
    cors_origins: list[str] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        # 优雅退出的收尾：晚到的开听按满员拒，已有在听交给 uvicorn 的宽限窗（ADR 0026）
        app.state.routes.draining = True
        app.state.listen_pool.shutdown(wait=False)
        try:
            await app.state.store.close()
        except Exception:
            pass

    app = FastAPI(lifespan=lifespan)
    origins = cors_origins if cors_origins is not None else (
        _env_origins("LIVE_TRANSLATOR_CORS_ORIGINS") or ["*"]
    )
    app.add_middleware(
        CORSMiddleware,
        # 未配置保持全开（开发 / 测试零配置）；生产在 LIVE_TRANSLATOR_CORS_ORIGINS
        # 收敛成壳的 origin（Windows 上是 http://tauri.localhost），收敛动作见 DEPLOY.md
        allow_origins=origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.trust_proxy = (
        trust_proxy if trust_proxy is not None else _env_flag("LIVE_TRANSLATOR_TRUST_PROXY")
    )
    app.state.store = store or default_store()
    app.state.store_lock = asyncio.Lock()
    app.state.routes = Routes(
        max_routes if max_routes is not None else _env_int("LIVE_TRANSLATOR_HOSTED_MAX_ROUTES", _default_max_routes())
    )
    app.state.idle_timeout = (
        idle_timeout if idle_timeout is not None else _env_float("LIVE_TRANSLATOR_HOSTED_IDLE_TIMEOUT", 120.0)
    )
    app.state.mem_floor_mb = (
        mem_floor_mb if mem_floor_mb is not None else _env_float("LIVE_TRANSLATOR_HOSTED_MEM_FLOOR_MB", 1500.0)
    )
    app.state.login_throttle = LoginThrottle(
        login_max_fails if login_max_fails is not None else _env_int("LIVE_TRANSLATOR_LOGIN_MAX_FAILS", 5),
        login_window_s if login_window_s is not None else _env_float("LIVE_TRANSLATOR_LOGIN_WINDOW_S", 60.0),
        login_cooldown_s if login_cooldown_s is not None else _env_float("LIVE_TRANSLATOR_LOGIN_COOLDOWN_S", 300.0),
    )
    app.state.engine_holder = None
    app.state.listen_pool = concurrent.futures.ThreadPoolExecutor(
        max_workers=(
            infer_workers if infer_workers is not None else _env_int("LIVE_TRANSLATOR_HOSTED_INFER_WORKERS", _default_infer_workers())
        ),
        thread_name_prefix="listen",
    )
    models_dir = _models_dir()
    if not _models_ready(models_dir):
        print(
            f"听译模型不在 {models_dir}。设置 LIVE_TRANSLATOR_MODELS，或把模型放到该目录。",
            flush=True,
        )
    print(
        f"托管服务：路数上限 {app.state.routes.max_routes}，推理池 {app.state.listen_pool._max_workers}，"
        f"空闲超时 {app.state.idle_timeout:.0f}s，内存红线 {app.state.mem_floor_mb:.0f}MB。",
        flush=True,
    )

    async def get_store() -> Store:
        st = app.state
        if not st.store.ready:
            async with st.store_lock:
                if not st.store.ready:
                    await st.store.setup()
        return st.store

    def holder():
        if app.state.engine_holder is None:
            from listen.session import EngineHolder

            app.state.engine_holder = EngineHolder(
                models_dir,
                enable_llm=False,
                executor=app.state.listen_pool,
            )
        return app.state.engine_holder

    def memory_tight() -> bool:
        floor = app.state.mem_floor_mb
        if floor <= 0:
            return False
        available = _available_mb()
        return available is not None and available < floor

    @app.post("/account/register")
    async def register(body: AccountIn):
        email = _normalize_email(body.email)
        if not email or "@" not in email or not body.password:
            return JSONResponse({"error": "填邮箱和密码"}, status_code=400)
        store = await get_store()
        salt, digest = _hash_password(body.password)
        if not await store.create_account(email, salt, digest):
            return JSONResponse({"error": "这个邮箱已经有账号，去登录"}, status_code=409)
        token = secrets.token_urlsafe(32)
        await store.add_token(token, email)
        return {"email": email, "token": token}

    @app.post("/account/login")
    async def login(body: AccountIn, request: Request):
        email = _normalize_email(body.email)
        key = client_source(request, app.state.trust_proxy)
        throttle = app.state.login_throttle
        if throttle.wait_left(key) > 0:
            return JSONResponse({"error": "试得太勤了，过几分钟再来。"}, status_code=429)
        store = await get_store()
        row = await store.get_account(email)
        ok = row is not None and hmac.compare_digest(
            _hash_password(body.password, row[0])[1], row[1]
        )
        if not ok:
            throttle.record_fail(key)
            return JSONResponse({"error": "邮箱或密码不对"}, status_code=401)
        throttle.reset(key)
        token = secrets.token_urlsafe(32)
        await store.add_token(token, email)
        return {"email": email, "token": token}

    @app.post("/account/logout")
    async def logout(body: TokenIn):
        store = await get_store()
        email = await store.email_for_token(body.token)
        if email:
            await store.delete_token(body.token)
            # 退出的是这枚 token：它开的听译停掉，别机同账号不动（故事 36）
            await app.state.routes.kick_token(email, body.token, "auth")
        return {"ok": True}

    @app.post("/account/session")
    async def session_check(body: TokenIn):
        email = await (await get_store()).email_for_token(body.token)
        if not email:
            return JSONResponse({"error": "登录已失效，请重新登录"}, status_code=401)
        return {"email": email}

    @app.post("/account/password")
    async def change_password(body: PasswordIn):
        store = await get_store()
        email = await store.email_for_token(body.token)
        row = await store.get_account(email) if email else None
        if not email or row is None:
            return JSONResponse({"error": "登录已失效，请重新登录"}, status_code=401)
        _, digest = _hash_password(body.oldPassword, row[0])
        if not hmac.compare_digest(digest, row[1]):
            return JSONResponse({"error": "旧密码不对"}, status_code=401)
        if not body.newPassword or len(body.newPassword) > 200:
            return JSONResponse({"error": "新密码不能为空"}, status_code=400)
        salt, digest = _hash_password(body.newPassword)
        # 换锁：这账号的全部 token 作废（含当前），当场换发新 token（ADR 0028）
        await store.revoke_tokens_for(email)
        await store.set_account_password(email, salt, digest)
        token = secrets.token_urlsafe(32)
        await store.add_token(token, email)
        # 其它电脑正在开听：停听并说明要重新登录；本机这路不动
        await app.state.routes.kick_other_tokens(email, body.token, "auth")
        return {"email": email, "token": token}

    @app.websocket("/listen")
    async def listen_ws(ws: WebSocket):
        await ws.accept()
        st = app.state
        session = None
        route = None
        try:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=AUTH_TIMEOUT_S)
                data = json.loads(raw)
            except (asyncio.TimeoutError, json.JSONDecodeError, ValueError):
                return
            token = data.get("token") if data.get("type") == "auth" else None
            email = (
                await (await get_store()).email_for_token(token) if isinstance(token, str) else None
            )
            if not email:
                await _send_notice(ws, "auth")
                return

            async def send_json(obj):
                await ws.send_text(json.dumps(obj, ensure_ascii=False))

            # 内存到线硬拒，与满员同一句说明（ADR 0015/0016）
            if memory_tight():
                await _send_notice(ws, "full")
                return
            peer = ws.client.host if ws.client else ""
            if app.state.trust_proxy:
                xff = ws.headers.get("x-forwarded-for", "")
                if xff:
                    peer = xff.split(",")[-1].strip()
            route = await st.routes.admit(email, token, send_json)
            if route is None:
                await _send_notice(ws, "full")
                return
            await (await get_store()).put_listening(email, route.started_ms, peer)

            from listen.session import ListenSession

            eng = holder()
            session = ListenSession(send_json, eng, force_ct2=True)
            session.start_background()
            if eng.peek() is None and eng.error is None:
                asyncio.create_task(eng.preload())
            while True:
                # 既无 PCM 也无文本帧到点即当断开：清在听、放名额，按网断处理（ADR 0029）
                msg = await asyncio.wait_for(ws.receive(), timeout=st.idle_timeout)
                if msg.get("type") == "websocket.disconnect":
                    break
                if msg.get("bytes") is not None:
                    await session.on_bytes(msg["bytes"])
                elif msg.get("text") is not None:
                    await session.on_text(msg["text"])
                if session.should_close:
                    break
        except asyncio.TimeoutError:
            pass  # 空闲到点：当网断收尾，名额在 finally 放出（ADR 0029）
        except asyncio.CancelledError:
            pass  # 被顶 / 登录作废 / 连接被收回：notice 已由挤掉的一方发出，收尾全在 finally
        except Exception:
            pass
        finally:
            # 收尾若又被取消（取消可以重复投递），退到纯同步收尾，不再 await
            cancelled = False
            if session is not None:
                try:
                    await session.close()
                except asyncio.CancelledError:
                    cancelled = True
                    session.cancel_background()
                except Exception:
                    pass
            if route is not None:
                st.routes.leave(route)
                if not cancelled:
                    try:
                        await (await get_store()).drop_listening(route.email, route.started_ms)
                    except Exception:
                        pass
            if not cancelled:
                try:
                    await ws.close()
                except Exception:
                    pass

    return app


app = create_app()


def _start_rss_reporter() -> None:
    """每 5 秒把自身 RSS 打进 stdout：压测标定与生产排障看内存水位；
    开发沙箱里 psutil 从外部读子进程会失真，自报最可靠。"""
    try:
        import psutil
    except ImportError:
        return
    proc = psutil.Process()

    def loop():
        while True:
            time.sleep(5)
            try:
                print(f"RSS {proc.memory_info().rss // 1048576}MB", flush=True)
            except Exception:
                return

    threading.Thread(target=loop, daemon=True).start()


if __name__ == "__main__":
    import uvicorn

    _start_rss_reporter()
    uvicorn.run(
        "account:app",
        host=os.environ.get("LIVE_TRANSLATOR_HOST", "127.0.0.1"),  # TLS 在反向代理（ADR 0025）
        port=int(os.environ.get("LIVE_TRANSLATOR_PORT", "8787")),
        timeout_graceful_shutdown=_env_int("LIVE_TRANSLATOR_HOSTED_DRAIN_TIMEOUT_S", 20),
    )
