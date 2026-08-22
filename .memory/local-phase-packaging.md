---
name: local-phase-packaging
description: 本机阶段：模型不下包、CT2 首次下、内置 Python
metadata:
  node_type: memory
  type: project
  origin: mcp
---

- 仍处本机听译阶段；托管未测、未当产品。
- 安装包不进模型。首次打开下 SenseVoice + VAD + 三对 CT2 + Xenova tokenizer.json；不下 ONNX 译权重、不下 Zipformer。缺文件会重置 model_ready 再下。
- 观众不能 pip：pnpm build 跑 tools/prepare_runtime.py，内置 Python 3.12 embed + requirements（含 ctranslate2），打进 NSIS resources（python/、engine/）。开发仍用 PATH python。
- 现行安装包版本 0.1.4：E:\code\Live-Translator\desktop\src-tauri\target\release\bundle\nsis\直播同传工具_0.1.4_x64-setup.exe
- 单开：tauri-plugin-single-instance，第二下唤出已有面板。
- NSIS 自定义模板 src-tauri/windows/installer.nsi：读注册表旧路径覆盖升级，跳过选目录；installMode currentUser；0.1.1 起当升级。

Why: 下一会话会再问打包/升级/模型下载。
How to apply: 不要把模型打进安装包；不要改成 ONNX 首次下载；升级走覆盖不要让观众重选目录。
