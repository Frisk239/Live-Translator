# 安卓端算力：端侧 vs 云端（调研汇总）

回答的问题：安卓端实时听译（直播同传场景）的算力放在哪——手机本地跑模型（on-device）还是托管服务？
结论先说：**业界没有人把「识别+翻译」双模型的实时同传做成端侧产品；做成端侧的都是单 ASR 字幕、系统级 App、专用小模型。我们已定的「手机端只做托管听译」（spec v2 / 原型 README）与业界实践一致。**

## 一、商用产品的实际选择

| 产品 | 算力位置 | 证据 |
| --- | --- | --- |
| Google Live Transcribe（实时对话转写） | **云端 ASR** | 官方博客自述「Powered by Google Cloud」「seamless connectivity to speech processing servers」「Relying on cloud ASR provides us greater accuracy」；GitHub 开源的是「与 Cloud Speech API 通信的 Android 客户端库」。端侧只放一个语音检测小模型（AudioSet 系）用于省流量断线 |
| Google Live Caption（媒体字幕，无翻译） | 端侧，但**系统级 App** | RNN-T ASR+标点+声音事件三模型全端侧；Google Speech 团队专门 edge 剪枝（功耗降 50%）、无语音时**把 ASR 模型从内存卸载**；起步仅 Pixel；抓音走系统特权（先前调研已确认 `CAPTURE_MEDIA_OUTPUT`，第三方拿不到） |
| 小米 AI 字幕 | 端侧（新机型限定） | 仅 MIX4 等新机型支持；社区记录官方砍老机型的原因即「调用语音识别、机器翻译、实时渲染对性能消耗不小，老机型又卡又发热，用户吐槽多了一刀切」 |
| 华为 AI 字幕 | 系统级（HarmonyOS 智慧语音） | 系统内置功能，非第三方可复制路径 |
| 讯飞听见（内录同传）、ViiTor | 云端 | 先前调研（android-feasibility-stack.md）：服务端出字幕/翻译 |
| Picovoice Zebra Translate / Bao-Translate（端侧 SDK/开源） | 端侧 | 商业卖点全部是**隐私/离线/合规**（air-gapped、GDPR），不是直播同传质量场景 |

关键对照：**同一个 Google，2019 年同时做了两个产品——带翻译方向的 Live Transcribe 选云端，不带翻译的 Live Caption 做端侧且只限 Pixel+系统特权。** 翻译模型上端是分界线。

## 二、端侧实时同传的技术现状（第三方 App 视角）

- **通用模型在手机 CPU 上不可行**：whisper.cpp 在骁龙 662 上 base.en RTF≈1.82（慢于实时，横评估算）；2026-01 GitHub 实测帖：tiny q8_0 流式模式慢 5×于实时，延迟累积 3s→10s→30s 直至 ANR/进程被杀（批处理模式勉强 1-2s/5s 音频）。VoxRT 技术博客：Whisper 是批处理模型改流式的设计错配，chunk 延迟 500–1500ms 持续「jerky」。
- **开发者社区证词**（r/androiddev 2025-02）：「很难找到谁把 Whisper 实时转写在安卓上做可靠……连拿了百万美元融资的 ArgMax 都还没做出来」，发帖人结论是回到云端流式 API。
- **能实时的只有 30–80MB 专用流式小模型，且基本只有英语 ASR**：骁龙 662 实测/估算 RTF——VoxRT 0.30（实测）、Cheetah ~0.47、Vosk small ~0.68、whisper base ~1.82 ✗、Moonshine ~19 ✗。质量天花板：Vosk small WER 9.85%、Cheetah 5.4%（均为英语 LibriSpeech）；sherpa-onnx 流式 Zipformer 无公开移动端 RTF/WER。
- **端侧先例的实际形态**（我们 ADR 引用的 LiveTranscribe-Android）：Vosk 流式小模型（30–80MB）做 ASR + Google ML Kit（~30MB/语言对）做文本翻译——**双小模型拼装**；同项目仍提供 LibreTranslate/远程 Whisper 端点作为更强后端选项。对应到我们的质量线（托管侧 SenseVoice+CT2+LLM），端侧组合的识别与译文质量明显低一档。
- **手机厂商的端侧 AI 字幕是系统级特权路径**：自家 NPU 深度优化、可裁机型、可热卸载模型；第三方 App 拿不到同级资源（NPU 加速碎片化，NNAPI 已被官方弃用转向各厂私有栈）。

## 三、对产品的含义

1. **维持「手机端只做托管听译」**（spec v2、chanpin/android/prototype README 已定）：手机抓音+出字幕，识别翻译全在托管服务。与业界一致，且我们的端到端模拟器链路已实测闭环（155 事件、整句定稿）。
2. 用户的两个担心都有实证：性能——中低端机连英语 whisper base 都跑不进实时；发热——小米官方因老机型发热砍功能是第一手先例。持续 1 小时+的直播场景放大两者。
3. **端侧不留「本机听译」档位**，不做「离线兜底」——纯 ASR 流式小模型（Vosk 级）技术上可离线出原文，但译文质量线达不到产品要求，且与「音源按 App 选、托管顶号满员」整套模型冲突；真有离线诉求是另一个产品。
4. 端侧唯一的正经角色：**VAD/能量检测**（「这个 App 不让抓」的静音提示，调研已定的能量启发式）——这类小计算留在手机上没有争议。

来源：<https://research.google/blog/real-time-continuous-transcription-with-live-transcribe/> · <https://research.google/blog/on-device-captioning-with-live-caption/> · <https://github.com/google/live-transcribe-speech-engine> · <https://github.com/ggml-org/whisper.cpp/discussions/3567> · <https://voxrt.com/asr-comparison> · <https://dev.to/voxrtio/streaming-asr-vs-whisper-on-mobile-when-to-switch-5cm7> · <https://www.reddit.com/r/androiddev/comments/1iha4t8/> · <https://github.com/chartmann1590/LiveTranscribe-Android> · <https://picovoice.ai/products/language/translation/> · <https://www.aichuke.com/aidaohang/54675.html>（小米 AI 字幕机型裁撤） · <https://consumer.huawei.com/cn/support/content/zh-cn15829100/>
