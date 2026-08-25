# 账号存储默认内嵌 SQLite，配 DSN 时走 Postgres

ADR 0018 定的账号 / 登录会话 / 在听会话进 Postgres，是给「以后加机器认同一批账号」留的路。第一版只有一台机器时，默认用仓库内嵌 SQLite（`server/hosted.sqlite3`，零部署依赖，测试用 `:memory:` 即隔离）；设 `LIVE_TRANSLATOR_DB_DSN` 指向 Postgres 时整个 Store 切到 asyncpg，schema 与行为同一份（SQL 写 `?` 占位，Postgres 侧统一改写成 `$n`）。单机自足与多机演进不各写一套代码；加机器那天只需给两台配同一个 DSN，满员仍是每台自己的路数闸（0018 原语义不变）。
