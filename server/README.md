# 托管服务

账号 HTTPS JSON + 听译 WebSocket 调度（ADR 0005 / 0014 / 0018）。听译本体在仓库根
`listen/` 包，这里只做账号、顶号、满员和多路调度。

## 跑起来

```bash
pip install -r requirements.txt
# 模型目录默认 server/models，或用 LIVE_TRANSLATOR_MODELS 指到桌面端已下好的那份
python account.py            # 监听 127.0.0.1:8787
```

测试（在仓库任意目录都能跑，conftest 已把路径配好）：

```bash
python -m pytest server/tests -q
```

端到端探针（上线前 / 换机器手跑）：起真 uvicorn 进程 + SQLite 文件库 + 真模型，
依次验证满听译出定稿、满员拒绝、顶号挤掉先开的一路——

```bash
python tools/e2e_probe.py --models-dir <模型目录>
```

生产走反向代理终结 TLS（ADR 0025），进程只听本机；壳里的源写死（ADR 0031），开发用
`LIVE_TRANSLATOR_HOSTED_ORIGIN` 盖成 `http://127.0.0.1:8787`。

## 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `LIVE_TRANSLATOR_DB` | `server/hosted.sqlite3` | 账号 / token 存储；`:memory:` 为内存库 |
| `LIVE_TRANSLATOR_DB_DSN` | 无 | 配了就走 Postgres（ADR 0032），优先于上一行 |
| `LIVE_TRANSLATOR_HOSTED_MAX_ROUTES` | 按核数保守给（cpu/4，1–8） | 满员路数闸；真正的 N 靠上机压测写入 |
| `LIVE_TRANSLATOR_HOSTED_INFER_WORKERS` | 按核数（cpu/2，1–8） | 有界推理池（ADR 0021） |
| `LIVE_TRANSLATOR_HOSTED_MEM_FLOOR_MB` | 1500，`0` 关闭 | 可用内存低于此值硬拒新开听（ADR 0015） |
| `LIVE_TRANSLATOR_HOSTED_IDLE_TIMEOUT` | 120 秒 | 缝上既无 PCM 也无文本帧即当断开、放名额（ADR 0029） |
| `LIVE_TRANSLATOR_LOGIN_MAX_FAILS` | 5 | 同一来源登录失败次数闸（ADR 0030） |
| `LIVE_TRANSLATOR_LOGIN_WINDOW_S` | 60 | 失败计数窗口 |
| `LIVE_TRANSLATOR_LOGIN_COOLDOWN_S` | 300 | 暂拒时长，过一会儿自动恢复 |
| `LIVE_TRANSLATOR_HOSTED_DRAIN_TIMEOUT_S` | 20 | 优雅退出时已有在听的宽限窗（ADR 0026） |
| `LIVE_TRANSLATOR_HOST` / `LIVE_TRANSLATOR_PORT` | `127.0.0.1` / `8787` | 监听地址（ADR 0025：生产只听本机） |
| `LIVE_TRANSLATOR_MODELS` | `server/models` | 听译模型目录 |

注意：登录防爆破按直连对端计数；上了反向代理后同一来源都是代理 IP，闸会全域共享，
收敛来源需在代理层带真实 IP（暂未做，ADR 0030 后续）。
