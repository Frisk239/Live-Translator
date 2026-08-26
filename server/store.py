"""账号与在听会话的持久化（ADR 0018 / 0032）。

默认内嵌 SQLite：单机自足，测试零依赖；设 LIVE_TRANSLATOR_DB_DSN
则走 Postgres——以后加机器时两台认同一批账号与在听，满员仍是各自的路数闸。
两种后端同一份 schema、同一组行为；SQL 写成 ? 占位，Postgres 侧统一改写成 $n。
在听会话行只在开听期间存在，进程起来时先清干净（ADR 0019 清脏在听）。

改表走编号迁移（_MIGRATIONS）：setup() 建 schema_migrations 记录表，按版本
升序补齐未应用的迁移。老库（建过表、没有记录）靠迁移 1 的 IF NOT EXISTS
幂等重放补上版本号，不需要手工对账。
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_V1_SQLITE = [
    """CREATE TABLE IF NOT EXISTS accounts(
  email TEXT PRIMARY KEY, salt BLOB NOT NULL, digest BLOB NOT NULL, created_ms INTEGER NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS tokens(
  token TEXT PRIMARY KEY, email TEXT NOT NULL, created_ms INTEGER NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS tokens_email ON tokens(email)",
    """CREATE TABLE IF NOT EXISTS listen_sessions(
  email TEXT PRIMARY KEY, started_ms INTEGER NOT NULL, peer TEXT NOT NULL DEFAULT '')""",
]

_V1_POSTGRES = [
    """CREATE TABLE IF NOT EXISTS accounts(
  email TEXT PRIMARY KEY, salt BYTEA NOT NULL, digest BYTEA NOT NULL, created_ms BIGINT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS tokens(
  token TEXT PRIMARY KEY, email TEXT NOT NULL, created_ms BIGINT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS tokens_email ON tokens(email)",
    """CREATE TABLE IF NOT EXISTS listen_sessions(
  email TEXT PRIMARY KEY, started_ms BIGINT NOT NULL, peer TEXT NOT NULL DEFAULT '')""",
]

# (版本, {后端: [语句...]})；只追加新版本，不改写已发布的迁移
_MIGRATIONS: list[tuple[int, dict[str, list[str]]]] = [
    (1, {"sqlite": _V1_SQLITE, "postgres": _V1_POSTGRES}),
]


def now_ms() -> int:
    return int(time.time() * 1000)


def _pg_style(sql: str) -> str:
    """? 占位 → $n（Postgres）。我们的 SQL 里没有字面量问号。"""
    out: list[str] = []
    n = 0
    for ch in sql:
        if ch == "?":
            n += 1
            out.append(f"${n}")
        else:
            out.append(ch)
    return "".join(out)


class Store:
    """异步接口；ready 由外层用 setup() 置位。"""

    ready = False

    async def setup(self) -> None: ...
    async def close(self) -> None: ...
    async def create_account(self, email: str, salt: bytes, digest: bytes) -> bool: ...
    async def get_account(self, email: str) -> tuple[bytes, bytes] | None: ...
    async def set_account_password(self, email: str, salt: bytes, digest: bytes) -> None: ...
    async def add_token(self, token: str, email: str) -> None: ...
    async def email_for_token(self, token: str) -> str | None: ...
    async def delete_token(self, token: str) -> None: ...
    async def revoke_tokens_for(self, email: str, keep: str | None = None) -> int: ...
    async def put_listening(self, email: str, started_ms: int, peer: str) -> None: ...
    async def drop_listening(self, email: str, started_ms: int) -> None: ...


class SqliteStore(Store):
    def __init__(self, path: str):
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    def _db(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _run(self, sql: str, args: tuple = ()) -> Any:
        with self._lock:
            conn = self._db()
            cur = conn.execute(sql, args)
            conn.commit()
            return cur

    async def setup(self) -> None:
        def init() -> None:
            with self._lock:
                conn = self._db()
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS schema_migrations(
  version INTEGER PRIMARY KEY, applied_ms INTEGER NOT NULL)"""
                )
                applied = {
                    row[0] for row in conn.execute("SELECT version FROM schema_migrations")
                }
                for version, stmts in _MIGRATIONS:
                    if version in applied:
                        continue
                    for stmt in stmts["sqlite"]:
                        conn.execute(stmt)
                    conn.execute(
                        "INSERT INTO schema_migrations(version, applied_ms) VALUES(?,?)",
                        (version, now_ms()),
                    )
                # 起来清脏在听：上个进程留下的行一概不算数
                conn.execute("DELETE FROM listen_sessions")
                conn.commit()

        await asyncio.to_thread(init)
        self.ready = True

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    async def create_account(self, email: str, salt: bytes, digest: bytes) -> bool:
        def op() -> bool:
            with self._lock:
                conn = self._db()
                try:
                    conn.execute(
                        "INSERT INTO accounts(email, salt, digest, created_ms) VALUES(?,?,?,?)",
                        (email, salt, digest, now_ms()),
                    )
                    conn.commit()
                    return True
                except sqlite3.IntegrityError:
                    return False

        return await asyncio.to_thread(op)

    async def get_account(self, email: str) -> tuple[bytes, bytes] | None:
        row = await asyncio.to_thread(
            self._run, "SELECT salt, digest FROM accounts WHERE email = ?", (email,)
        )
        return (row.fetchone()) if row else None

    async def set_account_password(self, email: str, salt: bytes, digest: bytes) -> None:
        await asyncio.to_thread(
            self._run,
            "UPDATE accounts SET salt = ?, digest = ? WHERE email = ?",
            (salt, digest, email),
        )

    async def add_token(self, token: str, email: str) -> None:
        await asyncio.to_thread(
            self._run,
            "INSERT INTO tokens(token, email, created_ms) VALUES(?,?,?)",
            (token, email, now_ms()),
        )

    async def email_for_token(self, token: str) -> str | None:
        cur = await asyncio.to_thread(
            self._run, "SELECT email FROM tokens WHERE token = ?", (token,)
        )
        row = cur.fetchone() if cur else None
        return row[0] if row else None

    async def delete_token(self, token: str) -> None:
        await asyncio.to_thread(self._run, "DELETE FROM tokens WHERE token = ?", (token,))

    async def revoke_tokens_for(self, email: str, keep: str | None = None) -> int:
        if keep:
            cur = await asyncio.to_thread(
                self._run,
                "DELETE FROM tokens WHERE email = ? AND token != ?",
                (email, keep),
            )
        else:
            cur = await asyncio.to_thread(
                self._run, "DELETE FROM tokens WHERE email = ?", (email,)
            )
        return cur.rowcount if cur else 0

    async def put_listening(self, email: str, started_ms: int, peer: str) -> None:
        await asyncio.to_thread(
            self._run,
            """INSERT INTO listen_sessions(email, started_ms, peer) VALUES(?,?,?)
               ON CONFLICT(email) DO UPDATE SET started_ms = ?, peer = ?""",
            (email, started_ms, peer, started_ms, peer),
        )

    async def drop_listening(self, email: str, started_ms: int) -> None:
        await asyncio.to_thread(
            self._run,
            "DELETE FROM listen_sessions WHERE email = ? AND started_ms = ?",
            (email, started_ms),
        )


class PostgresStore(Store):
    """生产 / 多机形态：LIVE_TRANSLATOR_DB_DSN 指向 Postgres。asyncpg 惰性导入。"""

    def __init__(self, dsn: str):
        self._dsn = dsn
        self._pool: Any = None
        self._lock = asyncio.Lock()

    async def _pg(self):
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    import asyncpg

                    self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def _exec(self, sql: str, *args: Any) -> Any:
        pool = await self._pg()
        async with pool.acquire() as conn:
            return await conn.execute(_pg_style(sql), *args)

    async def _fetchrow(self, sql: str, *args: Any) -> Any:
        pool = await self._pg()
        async with pool.acquire() as conn:
            return await conn.fetchrow(_pg_style(sql), *args)

    async def setup(self) -> None:
        pool = await self._pg()
        async with pool.acquire() as conn:
            await conn.execute(
                """CREATE TABLE IF NOT EXISTS schema_migrations(
  version INTEGER PRIMARY KEY, applied_ms BIGINT NOT NULL)"""
            )
            rows = await conn.fetch("SELECT version FROM schema_migrations")
            applied = {r["version"] for r in rows}
            for version, stmts in _MIGRATIONS:
                if version in applied:
                    continue
                async with conn.transaction():
                    for stmt in stmts["postgres"]:
                        await conn.execute(stmt)
                    await conn.execute(
                        "INSERT INTO schema_migrations(version, applied_ms) VALUES($1, $2)",
                        version,
                        now_ms(),
                    )
            await conn.execute("DELETE FROM listen_sessions")
        self.ready = True

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def create_account(self, email: str, salt: bytes, digest: bytes) -> bool:
        pool = await self._pg()
        async with pool.acquire() as conn:
            try:
                await conn.execute(
                    _pg_style(
                        "INSERT INTO accounts(email, salt, digest, created_ms) VALUES(?,?,?,?)"
                    ),
                    email,
                    salt,
                    digest,
                    now_ms(),
                )
                return True
            except asyncpg.UniqueViolationError:
                return False

    async def get_account(self, email: str) -> tuple[bytes, bytes] | None:
        row = await self._fetchrow("SELECT salt, digest FROM accounts WHERE email = ?", email)
        return (row["salt"], row["digest"]) if row else None

    async def set_account_password(self, email: str, salt: bytes, digest: bytes) -> None:
        await self._exec(
            "UPDATE accounts SET salt = ?, digest = ? WHERE email = ?", salt, digest, email
        )

    async def add_token(self, token: str, email: str) -> None:
        await self._exec(
            "INSERT INTO tokens(token, email, created_ms) VALUES(?,?,?)", token, email, now_ms()
        )

    async def email_for_token(self, token: str) -> str | None:
        row = await self._fetchrow("SELECT email FROM tokens WHERE token = ?", token)
        return row["email"] if row else None

    async def delete_token(self, token: str) -> None:
        await self._exec("DELETE FROM tokens WHERE token = ?", token)

    async def revoke_tokens_for(self, email: str, keep: str | None = None) -> int:
        if keep:
            res = await self._exec(
                "DELETE FROM tokens WHERE email = ? AND token != ?", email, keep
            )
        else:
            res = await self._exec("DELETE FROM tokens WHERE email = ?", email)
        return int(res.split()[-1]) if res else 0

    async def put_listening(self, email: str, started_ms: int, peer: str) -> None:
        await self._exec(
            """INSERT INTO listen_sessions(email, started_ms, peer) VALUES(?,?,?)
               ON CONFLICT(email) DO UPDATE SET started_ms = ?, peer = ?""",
            email,
            started_ms,
            peer,
            started_ms,
            peer,
        )

    async def drop_listening(self, email: str, started_ms: int) -> None:
        await self._exec(
            "DELETE FROM listen_sessions WHERE email = ? AND started_ms = ?", email, started_ms
        )


def default_store() -> Store:
    dsn = _env("LIVE_TRANSLATOR_DB_DSN")
    if dsn:
        return PostgresStore(dsn)
    path = _env("LIVE_TRANSLATOR_DB")
    if not path:
        path = str(Path(__file__).resolve().parent / "hosted.sqlite3")
    return SqliteStore(path)


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v and v.strip() else None
