---
name: listen-quality-gate
description: 听译质量延时门：阶段1保留、阶段2回退
metadata:
  node_type: memory
  type: project
  origin: mcp
---

阶段1 前缀翻译复用保留。阶段2 VAD 驱动已回退（ko_fast「맞だ.」）。

未熟成条不翻译 + 有声≥0.5s 后 150ms 重试：已保留。8×3 无新增红线。en 首草稿仍 1079ms。诊断：SenseVoice 在开口后 200–300ms 吐日语 glitch（うん/なんか？），两词要到约 1100ms 有声才稳定出现——1000ms 门低于当前离线整段 ASR 的两词墙，再抠翻译/节流打不过。

现行报告：desktop/manual-test-materials/quality-probe-report.json（未熟重试 8×3）。探针用 Python 3.12。

下一杠杆：阶段5 流式 ASR（en 首草稿）；阶段4 日韩直连中译（ja2 玄宗锚点）。阶段3 流水线重叠帮不了第一条草稿。不要重开阶段2 轮询。
