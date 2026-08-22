---
name: llm-config-discovery
description: LLM 配置：拉模型 + 思考档从报错发现
metadata:
  node_type: memory
  type: project
  origin: mcp
---

- 听译页已合成一条本机；「译文」改名「LLM 配置」；local_llm 存档收成本机；开听一律 translate=ct2，改写只看 llm.local.json enabled。
- 拉模型 GET {base}/models。思考档不写死：对 thinking/reasoning_effort/enable_thinking 发哨兵，从 4xx 报错解析枚举；200 当该字段被忽略。字段或档位放空则不传，走模型默认。
- 真测 OpenRouter ox-alpha：reasoning_effort 合法 none|minimal|low|medium|high|xhigh|max；none 400（强制思考）；听译填 reasoning_effort + low/minimal。
- 真测 OpenCode deepseek-v4-flash：合法 none/minimal/low/medium/high；放空和 none 最快；high 约 8.5s；off/no_think 422。听译填 reasoning_effort + none 或放空。
- 开发和安装包共用 %APPDATA%\com.livetranslator.desktop（settings.json + llm.local.json），不是打进包。

Why: 配置探测和路径是手测出来的，重做贵。
How to apply: 不要写死思考档位；空字段不传。
