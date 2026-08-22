---
name: listen-llm-usage
description: 同类如何用 LLM 译字幕，对照我们的 CT2+后台改写
metadata:
  node_type: memory
  type: project
  origin: mcp
---

字幕稳定已落地（docs/字幕稳定.md，ADR 0003 已改）：草稿译文冻前缀+藏尾巴（mask 2 汉字或 2 英文词）；对不上整行不动；定稿完整 CT2 再改写一次。LLM 不写进草稿。pytest 41，tsc 干净，vitest 面板/假听译 38。real_engine.seam 超时未等完。回放改写 HP/regroup 仍会二次定稿。
