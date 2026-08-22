# 直播同传工具 · 壳（desktop/）

Tauri 2 Windows 桌面应用：托盘 + 控制面板 + 字幕窗。听译跑在本机 Python 进程，
经本机 WebSocket 与壳互通（ADR 0008）。

## 启动

```bash
cd desktop
pnpm install        # 首次
pnpm dev            # 真听译：真采音 + SenseVoice + CT2，真下模型
FAKE_SCRIPT=en pnpm dev   # 假听译回放（演示 / 联调，默认脚本见 fake-listen/）
```

前置（开发）：Node.js、Rust（msvc，编译配置见 `src-tauri/.cargo/config.toml.example`）、
Python 3（`python -m pip install -r engine/requirements.txt`）。
模型第一次打开自动下载（进度占开听键位置），下到
`%APPDATA%/com.livetranslator.desktop/models/`，不进 git。

打给观众的安装包：

```bash
pnpm build    # 先内置 Python（含 ctranslate2 等），再打 NSIS；模型不进包
```

产物在 `src-tauri/target/release/bundle/nsis/`。观众不用装 Python、不用 pip。

测试与检查：

```bash
pnpm test           # 缝测试（假听译回放 + 二进制 PCM 兼容）+ reducer + 真听译（模型在才跑）
python -m pytest engine/tests -q   # 切条纯逻辑
pnpm typecheck && (cd src-tauri && cargo check)
```

三语全链自测（真实播放 → 系统环回采音 → 真听译 → 断言草稿/定稿）：

```bash
python -m pip install soundfile pyaudiowpatch   # 自测脚本额外依赖
python tools/selftest.py                        # 会用扬声器放英/日/韩素材约 10 分钟，别静音
python tools/selftest.py --only en              # 只放一个语言
```

素材是 Wikimedia Commons 公开语音（tests/fixtures/selftest_{en,ja,ko}.ogg）；
播放走默认输出设备（WASAPI 输出流），采集抓同一设备的环回——就是「系统混音」那条路。

## 演示开关（环境变量，随 `pnpm dev` 传入）

| 变量 | 作用 |
|---|---|
| `FAKE_SCRIPT=en\|ja\|rapid\|pause\|silence\|music\|perm\|crash` | 走假听译回放该脚本（设了就不走真听译） |
| `FAKE_FIRSTRUN=1` | 强制重走「第一次打开」下载进度（假进度） |
| `LT_FAKE_WEAK=1` | 面板显示弱机器黄字提醒（真硬件检测是债务） |
| `LT_ENGINE_MODELS=<dir>` | 真听译缝测试用：指定模型目录 |

## 壳 ↔ 听译的缝（唯一接缝）

壳 → 听译：JSON **文本帧** `start{source,playback?}` / `switch{source}` / `stop`；
PCM 走**二进制帧**（f32le / mono / 16kHz，无帧头，顺序即时间序），壳侧采音：
进程音源 = Application Loopback（IAudioClient 按进程激活），系统混音 = 默认设备环回。

听译 → 壳，只回三类事件：

```json
{ "type": "draft", "orig": "so we", "trans": "我们" }   草稿（整条当前快照）
{ "type": "final", "orig": "...", "trans": "..." }      定稿（冻结）
{ "type": "notice", "kind": "no_speech | not_lang | no_audio | crashed" }  提示
```

静默约两秒撤条、切条挤压、提示行限时是壳的规则（`src/core/reducer.ts`），
切条跟嘴是听译的规则（`engine/real_listen.py` 的 Segmenter，ADR 0002）。
测试都打在这条缝上（`tests/`）。

音源列表**实际检测**：枚举系统音频会话（IAudioSessionManager2）查峰值电平，
只列当前真在出声的进程；上次选的音源若进程还在只是没出声，保留成灰色可选行；
系统混音置底。**探活**：开听中音源进程退出 → 停止开听 + 面板黄字等再选；
进程还在但没出声（如浏览器所有标签都静了）→ 壳补静音心跳，
表现为「在听 · 还没听到人声」，不误报「抓不到」；「抓不到」只在环回激活失败时出现。

## 模型

壳首次打开下载（`src-tauri/src/models.rs` 的 manifest）：
SenseVoice-Small int8（识别）、silero_vad（VAD）、
CTranslate2 int8（翻译：en→zh 直译，ja/ko→en→zh 串联；分词用 Xenova tokenizer.json）。
下载顺序（国内优先）：hf-mirror（HF 国内镜像，直连）→ huggingface.co（走 `HTTPS_PROXY`，若有）；
VAD 走 ghfast.top / gh-proxy.com（GitHub 国内加速）→ GitHub 官方。

## 本刀债务（明示）

- **ja/ko 仍经 en 转译**：官方无 ja→zh / ko→zh 权重。本机翻译走 CT2 int8；ONNX 回退留在引擎里，首次下载不再拉。
- 模型下载无断点续传（按文件粒度续装）；hf-mirror 反爬 / HF 限流会表现为下载失败。
- 打包：`pnpm build` 会内置 Python 与听译依赖；模型仍首次打开再下。安装包尚未用干净机器手验。
- 弱机器判定是环境变量开关，没有真硬件检测。
- 开机自启写注册表只在 dev 形态验证过；托盘钉在通知区未做（Windows 不好程序化）。
- 延迟未系统测量（开发入口的 1s/2s 验收待真机播直播时量）。
- 首次开听时模型加载约几秒 —— 已改进程启动即预加载；但引擎与壳同时冷启动的头几秒仍可能缺。
- 真引擎缝测试（tests/real_engine.seam.test.ts）对系统负载敏感，满载时偶发超时（重跑即绿）。
