"""schema 版本化迁移：新库建记录表；老库幂等重放补版本，数据不丢。"""
import sqlite3

from store import _MIGRATIONS, SqliteStore, now_ms


def _setup(store: SqliteStore):
    import asyncio

    asyncio.run(store.setup())
    asyncio.run(store.close())


def test_fresh_store_records_migration_version(tmp_path):
    path = str(tmp_path / "fresh.sqlite3")
    _setup(SqliteStore(path))
    conn = sqlite3.connect(path)
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
    conn.close()
    assert versions == [v for v, _ in _MIGRATIONS]


def test_setup_is_idempotent(tmp_path):
    path = str(tmp_path / "again.sqlite3")
    _setup(SqliteStore(path))
    _setup(SqliteStore(path))  # 再起来一遍
    conn = sqlite3.connect(path)
    count = conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0]
    conn.close()
    assert count == len(_MIGRATIONS)


def test_legacy_db_without_migration_table_gets_versioned(tmp_path):
    path = str(tmp_path / "legacy.sqlite3")
    conn = sqlite3.connect(path)
    for stmt in _MIGRATIONS[0][1]["sqlite"]:
        conn.execute(stmt)  # 老库：表都在、没有 schema_migrations
    conn.execute(
        "INSERT INTO accounts(email, salt, digest, created_ms) VALUES(?,?,?,?)",
        ("old@b.c", b"s", b"d", now_ms()),
    )
    conn.commit()
    conn.close()

    _setup(SqliteStore(path))
    conn = sqlite3.connect(path)
    versions = [r[0] for r in conn.execute("SELECT version FROM schema_migrations")]
    kept = conn.execute("SELECT COUNT(*) FROM accounts WHERE email = 'old@b.c'").fetchone()[0]
    conn.close()
    assert versions == [1]
    assert kept == 1, "老库数据不能丢"
