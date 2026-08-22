---
name: listen-market-stack
description: 同类听译产品选型对照：ASR没错，译和首显有方向差
metadata:
  node_type: memory
  type: project
  origin: mcp
---

调研（Exa/GitHub/B站，2026-08）：看外语直播叠字幕是红海。近邻：SakiRinn/LiveCaptions-Translator（3.5k★，Win11 Live Captions + LLM）、TheDeathDragon/LiveTranslate（SenseVoice/Whisper + LLM API）、RoastSub（Live Captions 或 whisper.cpp + DeepSeek）、begin0808/LiveCaption_Global（停顿后 SenseVoice + Ollama）。讯飞同传/通义听悟是会议云端，不是观众进程音源。

选型结论：CPU 本机英日韩用 SenseVoice-Small 不是方向错误（FunASR 官方 CPU 首选）。方向差在两处——（1）译文质量：成功产品几乎都用 LLM 译半句/专名，Marian/OPUS 枢轴是质量天花板；（2）首显：别人不跟「开口起算两稳词≤1s」死磕，而是借系统 Live Captions、或停顿后再认、或云端流式。我们可守的差异：进程级音源（多数只有系统混音）、无 Key 本机、观众向产品。第二形态/可选 LLM 译才是质量跃迁，不是再抠 CT2。
