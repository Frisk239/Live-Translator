---
name: listen-next-levers
description: 听译下一杠杆：流式ASR二过 + 勿用直连救ja2
metadata:
  node_type: memory
  type: project
  origin: mcp
---

英一流式 Zipformer 已 spike 并证伪（本素材）：
- 20M：把 Concord 认成 UD，两词 2400ms
- 2023-06-26 int8（encoder 70MB / 包 310MB）：CONCORD RETURNED 在 2100ms 媒体时，开口起约 1460ms，慢于 SenseVoice 1079ms
- en2 葛底斯堡同样更慢。热路径未接入。代码留 StreamingEn / stream_en_draft_ok / fetch_zipformer_en.py。

ja2 锚点已在 manual-test-materials/README.md 写明：接受为已知极限（原文层已死），不为此下直连中译。

en 1000ms 门仍低于离线两词墙。下一模型候选需先跑 onset_zipformer_probe.py 赢过 SenseVoice 再接线。
