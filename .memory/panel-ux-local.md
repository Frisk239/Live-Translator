---
name: panel-ux-local
description: 控制面板本机 UX 已落地的约束
metadata:
  node_type: memory
  type: project
  origin: mcp
---

- 托管选项默认藏（仅 listenWay===hosted 才露出）。开听钉 panel-footer。音源 2s 静默对清单，sameSources 无变化不重绘；草稿不刷面板（panelViewChanged）。
- 系统混音保留兜底。python.exe 出现在音源是因为 settings.json 记住了开发时选择 + 听译子进程也叫 python.exe；干净机没有。用户说先不滤。
- 字幕窗：四边四角可拉、字随窗宽、去掉 line-clamp、内容超出往上长高不缩。

Why: 面板/字幕窗交互已拍板。
How to apply: 不要把托管选项露出来；不要过滤 python.exe 音源，除非用户改口。
