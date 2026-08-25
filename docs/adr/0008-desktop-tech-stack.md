# 壳用 Tauri 桌面应用，听译用本机 Python 服务

控制面板、托盘、字幕窗用 Tauri 2（Windows 上是独立 .exe，界面跑在系统 WebView 里，不在浏览器标签页打开）。不用 Electron、不用 WPF、不用 PyQt。听译单独跑 Python 进程：sherpa-onnx（SenseVoice + Silero）、CTranslate2（OPUS-MT）。壳抓音源 PCM，经本机 HTTP 或 WebSocket 交给听译，回草稿 / 定稿 / 提示。
