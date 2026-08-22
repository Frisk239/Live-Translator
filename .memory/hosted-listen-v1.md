---
name: hosted-listen-v1
description: 托管听译第一版已对齐的形态与约束
metadata:
  node_type: memory
  type: project
  origin: mcp
---

第一版托管听译（已对齐，规格 docs/hosted-listen-spec.md，ADR 0009–0012）：

同一壳，本机|托管分段。未登录点托管→整页邮箱+密码登录/注册+记住我；不发信、不验证、不找回。点邮箱进个人中心，先只有改密码。

壳↔听译复用现有 WS 缝，对端换成国内一台机器（WSS，地址写进安装包）。音不落盘。Postgres 只存账号和登录会话。16核32G、模型一份、约30路。同时一路、顶号。断网/被顶停听说明，不偷偷切本机。只开托管不下本机模型。托管不用译文 tab 的大模型。先不收费不限额。

Why: 后续会话不要再重烤一遍形态。
How to apply: 实现前读该 spec 和 ADR；改形态先改文档。
