---
name: panel-llm-tab-hand-test
description: 译文 tab 手验结论：测出 3 处 bug，2026-08-20 已全部修复并回归
metadata:
  type: project
---

2026-08-20 手验控制面板「译文」页（flash 默认）→ 测出 3 处 bug → 同日全部修复 + 真窗回归通过。**未 commit**（仓库仍无初始 commit）。

## 测出并已修的 bug

1. **P0 key 清空/抹盘**（`panel.ts snapshotLlmDraft`）：`render()` 开头无条件快照，从听译切译文那一刻 DOM 还没有 `#llmApiKey`，`llmDraft.apiKey` 被赋 `""`（baseUrl/model 有 `|| fallback` 幸免）。后果：重启后 key 不显示；进译文页点保存把磁盘 key 抹成 0 还报「已保存。下次定稿会按这个改写」。**修法**：快照只在元素存在时写入（apiKey 用 `!== undefined` 判定，故意清空仍生效）；`saveLlm` 提示语对齐（enabled 但无 key → 「已保存，但还没填密钥，改写不会生效。」）。
2. **BOM 配置读不了**：记事本写的 llm.local.json 带 UTF-8 BOM，Rust serde_json / Python json 双双解析失败，面板显示默认值、打包版引擎静默无 LLM。**修法**：`llm.rs load` 剥 `\u{feff}` 前缀；`real_listen.py` 改 `utf-8-sig`。cargo 补 4 个单测（往返 / BOM+CRLF / snake_case 别名 / 缺文件默认）。
3. **首次试连可能 21.8s**：冷连接把 thinking:off 档拖满 20s 超时 → reasoning_effort 档在 opencode 端点固定 422（~0.7s，端点行为非 bug）→ 裸跑档成功。**修法**：probe 单档超时 `clamp(3, 10)s`。热连本来 ~0.9s。

## 回归结论（全过）

- 静态：tsc 干净、vitest 33/33（含真引擎缝测试）、cargo 7/7。
- 真窗（CDP）：重启后进译文页 key 67 字符直接可见；进页立即保存/来回切 tab 后保存 key 不丢；故意清空 key 出新警告语；试连有效 1.8s/0.9s pong、乱写 id 401 黄字；手写 BOM 配置被面板 invoke 和引擎 `load_llm_config` 双读成功。
- 业务：关改写保存 → 引擎 None（显式 false 短路不回退 desktop 副本）；关着开听「在听 · 还没听到人声」+ 停止正常；E2E 真引擎 A/B：开改写每条 CT2 定稿后 0.7–1.3s 跟 LLM 润色版（「30H」→「30点生命值」），disabled 对照零改写。

## 测试基建（留在 `.build_mats/`，未跟踪）

`cdp.mjs`（WebView2 CDP 客户端）、`llm_e2e_ab.mjs`（真引擎 A/B 冒烟）、`llm_variant_probe.py`（分思考字段延迟）。CDP 方法论见自动记忆 tauri-panel-cdp-automation。
