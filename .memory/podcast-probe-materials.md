---
name: podcast-probe-materials
description: 手验素材改播客口语；切条按电影字幕
metadata:
  node_type: memory
  type: project
  origin: mcp
---

- 朗读基线已删。现 manual-test-materials：01-en-hpr-podcast.mp3（HPR CC BY-SA）、02-en-rubenerd-podcast.mp3（CC BY 连说）、03-ko-fsi-dialogue.mp3（FSI 公有领域）。B 站 BV1ZLkfBvEMX 是版权汇编不下。日语开源播客未补上。
- 切条：TRANS_MAX_CHARS=20、SEG_MAX_SECONDS=3.5、从句/逗号够 10 字就切；改写可在逗号提前发。探针 HPR 80s→29 条中位 15 字。
- 勿再抠 SenseVoice 1000ms 门、勿接线 Zipformer、勿直连救 ja2、勿重开阶段2 VAD 轮询。

Why: 素材版权和切条参数是定过的。
How to apply: 手验用播客，不要用朗读基线；不要重开已否的 ASR 方向。
