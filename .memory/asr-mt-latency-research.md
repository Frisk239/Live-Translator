---
name: asr-mt-latency-research
description: 2026-08 流式ASR/日韩直连/业界延迟口径调研结论
metadata:
  node_type: memory
  type: project
  origin: mcp
---

只调研、未改代码。对照 listen-quality-gate：en 首草稿 1079ms 是 SenseVoice 约 1100ms 有声才稳吐两英文词；ja2 玄宗是 ASR+英文枢轴。

A 流式 ASR：SenseVoice 论文明确 not designed for streaming；sherpa-onnx 只有 VAD+offline 伪流式。Paraformer-streaming 官方 600ms chunk+300ms lookahead，但只有 zh/en、220M。真流式 CPU 候选是 sherpa Zipformer（en/ko 有 online；ja ReazonSpeech 是 offline）。Moonshine v2 streaming 只保证英文 TTFT 50–258ms。Whisper/faster-whisper/Canary 不适合当本机首草稿。

B 直连：Helsinki 无 opus-mt-ja-zh/ko-zh。Tatoeba jpn-zho BLEU 12.1、kor-zho 5.8。shun89/opus-mt-ja-zh 约 310MB Marian，可走现有 CT2。NLLB-600M int8 CT2 约 591MB，CC-BY-NC，专名更可能好。mBART/Qwen 不当默认译器。

C 口径：业界报 TTFB/首 partial，不是开口起算两稳词。IWSLT low-latency 是 AL≤1000ms（整句平均滞后）。Ofcom 直播字幕实际 5.1s、指导 <3s。1000ms 两词门对 offline 整段过苛。

值得做：① Zipformer/Moonshine EN 一流式 + SenseVoice 二过；② NLLB 或 shun89 日韩直连钉玄宗。不要碰：SenseVoice 伪流式、Whisper 当直播首草稿、重开阶段2。
