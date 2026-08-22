---
name: subtitle-style-axes
description: 字幕样式：YouTube/ASS 五轴 + 快捷预设
metadata:
  node_type: memory
  type: feedback
  origin: mcp
---

- 用户嫌三档预设简陋。对标 YouTube CC / ASS / LiveCaptions-Translator：字色、描边、底、字号、字体。
- 已实现：快捷样式 outline/yellow/plate 会套 ink+edge+plate+weight；细项字号 s/m/l/xl、雅黑/黑体/宋体、六色块+自选、描边无/细/粗、底无/浅/深、字重常规/粗。改完立刻广播到字幕窗 CSS 变量。
- 视觉评估后用户要改底贴字。已改：.subtitle-win align-items:center；.sub-stack width:max-content; max-width:100%。长句仍随窗宽折行。

Why: 样式轴和底贴字是拍过板的。
How to apply: 底必须包住字，不要通栏。
