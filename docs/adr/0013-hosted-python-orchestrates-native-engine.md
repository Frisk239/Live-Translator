# 托管听译第一版：Python 编排，原生推理，不换运行时

托管听译第一版仍用 Python 接现有缝（账号 HTTP + 听译 WebSocket），识别/翻译继续走 sherpa-onnx 与 CTranslate2 的 C++ 实现；模型在进程里只加载一份，多路会话共用。不对标 FunASR C++ runtime 或 Kyutai/Riva 的 Rust/Triton 服务端——那些是通用 ASR 农场或 GPU 大模型 serving，会拆掉本机已经调过的切条、草稿策略和缝协议。Python 慢的常见误用是每路一份模型、或在事件循环里同步跑推理，不是解释器本身。等真要 GPU 批处理或测出编排层是墙，再考虑换运行时。
