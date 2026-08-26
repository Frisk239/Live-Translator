# 安卓端用 Kotlin + Compose 原生，不走跨端框架

安卓端三大核心能力——AudioPlaybackCapture 抓播放音、mediaProjection 类型前台服务、悬浮字幕窗——全是 Android 私有 API。调研（docs/research/android-feasibility-stack.md）逐条查证：Flutter / RN / Tauri 2 做这三件事最终都要自写同一份 Kotlin 原生层、再多养一层桥接和三方插件；iOS 已定不做（后续.md），跨端收益为零。选 Kotlin + Jetpack Compose 单端原生，抓音、前台服务、WS 推流全部直调；悬浮字幕窗本体用 View + WindowManager（先例项目实测 Compose 与 SYSTEM_ALERT_WINDOW 配合不佳），App 内界面用 Compose。换栈条件：将来要做 iOS 或复用 UI 到别的端时再议。
