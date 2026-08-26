# 托管服务部署 runbook

一台国内 Linux 机器、一个 OS 进程（ADR 0018）。TLS 由反向代理终结，进程只听本机
（ADR 0025）。下面从裸机到上线按序执行；`deploy/` 目录里有 systemd unit、
EnvironmentFile 和 nginx 配置的样例。

## 0. 前提

- Linux（systemd），Python 3.12，nginx，域名与证书（生产是 WSS/HTTPS，壳里写死源，ADR 0031）
- 听译模型一份（桌面端已下好的 `models/` 目录直接拷）

## 1. 放代码与模型

```bash
sudo useradd --system --home /opt/livetranslator --shell /usr/sbin/nologin livetranslator
sudo mkdir -p /opt/livetranslator /var/lib/livetranslator
# 仓库放到 /opt/livetranslator（git clone 或 rsync），模型放 /opt/livetranslator/models
sudo chown -R livetranslator:livetranslator /opt/livetranslator /var/lib/livetranslator

cd /opt/livetranslator/server
sudo -u livetranslator python3.12 -m venv /opt/livetranslator/venv
sudo -u livetranslator /opt/livetranslator/venv/bin/pip install -r requirements.txt
```

## 2. 配置

```bash
sudo mkdir -p /etc/livetranslator
sudo cp deploy/livetranslator.env.example /etc/livetranslator/livetranslator.env
# 编辑：模型路径、库路径、域名相关项。
# 生产必配：LIVE_TRANSLATOR_TRUST_PROXY=1、LIVE_TRANSLATOR_CORS_ORIGINS=http://tauri.localhost
```

## 3. 压测标定路数 N（ADR 0015，spec 要求）

先用保守默认（`MAX_ROUTES=1`）把服务跑起来，然后扫档：

```bash
cd /opt/livetranslator/server
sudo -u livetranslator /opt/livetranslator/venv/bin/python tools/load_probe.py \
    --models-dir /opt/livetranslator/models --levels 1,2,4,6,8
```

红线：每路首草稿 P95 ≤ 1500ms（开口延迟）、无错误路；可选 `--mem-red-line-mb` 给 RSS
上限。取最后一个达标的档写进 `/etc/livetranslator/livetranslator.env` 的
`LIVE_TRANSLATOR_HOSTED_MAX_ROUTES`。换机器 / 升模型后重跑。

## 4. systemd 装载

```bash
sudo cp deploy/livetranslator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now livetranslator
sudo journalctl -u livetranslator -f   # 看到「托管服务：路数上限 …」即起好
```

## 5. nginx 反代（TLS + WSS + 真实 IP）

```bash
sudo cp deploy/nginx.conf.example /etc/nginx/sites-available/livetranslator.conf
# 改域名与证书路径，软链到 sites-enabled，nginx -t 验证后 reload
```

三处不能改错：`Upgrade`/`Connection` 头（WSS 握手）、`X-Forwarded-For` 用
`$proxy_add_x_forwarded_for`（append 语义，服务端只信最右一跳）、读超时大于缝空闲超时。

## 6. 上线前验证（目标机器上）

```bash
cd /opt/livetranslator/server
/opt/livetranslator/venv/bin/python -m pytest tests -q          # 静态行为
/opt/livetranslator/venv/bin/python tools/e2e_probe.py \
    --models-dir /opt/livetranslator/models                      # 三道闸：满听译 / 满员 / 顶号
```

再用一台壳配 `LIVE_TRANSLATOR_HOSTED_ORIGIN=https://<域名>` 明文连一次真 WSS，
注册→开听→被第二台顶号→改密码，走完 spec 的关键故事。

## 7. 日常

- 日志：`journalctl -u livetranslator`；库文件在 `LIVE_TRANSLATOR_DB` 指向处，备份它即备份账号。
- 升级：rsync 新代码 → `systemctl restart livetranslator`（在听的壳闪断自动再开，ADR 0019）；
  schema 变更由进程启动时的编号迁移自动补齐（`store.py` 的 `_MIGRATIONS`）。
- 回滚：切回旧代码目录 restart；迁移只向前，回滚版本的代码要能容忍新 schema（只加列不删列）。
