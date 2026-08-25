# 本机听译用 SenseVoice + OPUS-MT，不上 LLM

第一版只认英 / 日 / 韩 → 简体中文。识别用 SenseVoice-Small（sherpa-onnx），翻译用三只 OPUS-MT 双语对（en-zh / ja-zh / ko-zh，CTranslate2）。切条仍跟嘴。默认不接任何大模型。Whisper 大模型和 LLM 译句更慢更贵，留给以后。
