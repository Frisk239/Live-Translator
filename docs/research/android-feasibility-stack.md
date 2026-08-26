# 安卓端可行性与技术栈（调研汇总）

四路子代理调研（2026-08-25，阻塞式，公网一手来源）的合并结论：抓音可行性、政策与 ROM、竞品先例、技术栈。立项闸门是真机抓音验证（见 [后续.md](../后续.md) 移动端一节）。

## 结论速览

1. **抓音路径成立且有先例**：AudioPlaybackCapture + MediaProjection + 悬浮窗是唯一的非 root 正规路径，开源（LiveTranscribe-Android、live-translate）与商业（ViiTor、讯飞听见内录）都已验证。
2. **「音源按 App 选」是公开 API**：`AudioPlaybackCaptureConfiguration.Builder.addMatchingUid()/excludeUid()`，API 29+ 就有——产品音源模型可以保留桌面版的形态，不必退化成纯混音。
3. **目标 App 覆盖必须真机逐个验**：YouTube 实测可抓；Netflix、Twitch 实测抓不到（opt-out）；抖音 / B站 / TikTok **没有可信实测**。opt-out 的表现是拿到静音而不是失败，无检测 API，只能靠能量启发式。
4. **技术栈四路证据一致指向 Kotlin + Compose 原生**：Flutter / RN / Tauri 2 做本项目三大核心能力（抓音、MediaProjection 前台服务、悬浮窗）最终都要自写同一份 Kotlin 原生代码，跨端收益为零（iOS 不做）。
5. **政策可过但有硬约束**：Play 允许悬浮+录屏组合（每会话系统授权、Android 14+ 前台服务申报、Android 15 锁屏自动停录）；无障碍路线政策明文禁走；国内上架需工信部备案 + 软著，国产 ROM 无保活豁免。

## 一、抓音可行性

- AudioPlaybackCapture 最低 API 29，必须走 MediaProjection 屏幕录制授权 + `RECORD_AUDIO`，与目标 App 同用户 profile。
- `allowAudioPlaybackCapture` 默认值：targetSdk ≥29 默认允许被抓、显式 false 才禁用；targetSdk ≤28 默认不允许。DRM/secure 内容天然抓不到。
- 抓取方可以按 UID 精确过滤（`addMatchingUid`/`excludeUid`，不能与 exclude 混用）；Android 14/15/16 没有新的按 App 选择音频的 API（14 的单 App 窗口共享只管画面）；要拿目标 UID 需过 Android 11+ 包可见性（`<queries>` 等）。
- 官方明确：抓到的是其它 App **以及自己** 的混音，排除自己用 `excludeUid(Process.myUid())`。对方 opt-out 后是静音而非失败（CTS `EXPECT_SILENCE`），没有 API 检测 opt-out；用户撤销授权同样变静音（要监听 `Callback.onStop`）。
- targetSdk 34+ 顺序硬约束：先 `createScreenCaptureIntent()` 用户授权 → 起 `mediaProjection` 类型前台服务（+ `FOREGROUND_SERVICE_MEDIA_PROJECTION` 权限）→ 再 `getMediaProjection()`，否则 `SecurityException`；token 一次有效、每会话重新授权。
- 实测记录：YouTube 可抓（AudioRelay 兼容列表、scrcpy 实测）；Netflix 抓不到（sndcpy 作者确认）；Twitch 官方 App 近期抓不到（AudioRelay 2026-05）；MS Teams 抓不到；抖音/B站/TikTok 未查到可信实测（仅中文博客二手说法称抖音/快手/B站开了 allow）。

来源：<https://developer.android.com/media/platform/av-capture> · <https://developer.android.com/reference/android/media/AudioPlaybackCaptureConfiguration> · <https://developer.android.com/reference/android/media/projection/MediaProjectionManager> · <https://developer.android.com/media/grow/media-projection> · <https://developer.android.com/develop/background-work/services/fgs/service-types> · <https://github.com/rom1v/sndcpy/issues/105> · <https://community.audiorelay.net/t/audio-from-android-twitch-app-not-being-captured/3931> · <https://github.com/Genymobile/scrcpy/issues/4425>

## 二、政策与 ROM

- Play：SAW（悬浮窗）属受限权限，须引导用户去系统设置授权；录屏须尊重 FLAG_SECURE；Android 14+ 录屏须前台服务并在 Console 申报用途；Android 11+ 投影期间 SAW 自动授予；Android 15 起状态栏常显录屏标记、锁屏自动停止。无障碍 API 政策 2025-10 再收紧（禁止自主执行/采集通话），不走。字幕类被拒审的公开案例未查到；历史下架潮均涉广告/恶意行为。
- 国产 ROM：无保活豁免，全部要用户手动设置。逐家「四件套」：悬浮窗、自启动、省电无限制、最近任务加锁（MIUI/HyperOS、ColorOS、OriginOS、OneUI 路径已记录在案）；Android 15 起侧载应用的高敏权限（含悬浮窗）受限（OriginOS 官方确认）。
- 国内分发：不走 Play 则不受其政策约束；上架硬门槛 = 工信部 APP 备案 + 软著；小米审核「权限与功能不符」直接驳回、金标联盟已上线权限合规审核；录音受 YD/T 4177.9-2024 最小必要标准约束。
- 三大风险与规避：① 形态近似偷录（意图疑点）→ 只做用户主动开启的会话、常驻状态提示、一键停止、不绕 FLAG_SECURE；② 系统断流（每会话重授权、锁屏停录）→ 做锁屏暂停/解锁重授权交互；③ 杀后台+侧载限制 → 逐厂商保活引导 + 上架商店。

来源：<https://support.google.com/googleplay/android-developer/answer/9888170> · <https://support.google.com/googleplay/android-developer/answer/10964491> · <https://support.google.com/googleplay/android-developer/answer/13392821> · <https://dev.mi.com/xiaomihyperos/documentation/detail?pId=1826> · <https://www.gov.cn/zhengce/zhengceku/202308/content_6897341.htm> · <https://dev.mi.com/xiaomihyperos/documentation/detail?pId=2251> · <https://dontkillmyapp.com/xiaomi>

## 三、竞品先例

- Google Live Transcribe：只收麦克风，不抓播放声音；系统级 Live Caption（Pixel/三星）：抓设备音频流但要系统特权（AOSP 确认用 `CAPTURE_MEDIA_OUTPUT`），不开放第三方。
- 讯飞听见：2023-09 上「内录」——抓手机播放声音出字幕/翻译，音源内录/外录二选一，会议/电话场景不支持；免费版每 5 分钟清屏。
- 网易见外：个人版 2020 停服，现仅存企业向服务。
- ViiTor（小米/Play 在架，2026-07 更新）：明确「实时获取屏幕播放音频流 + 悬浮窗翻译」，支持 TikTok/YouTube/Twitch——与我们的路径完全同源。市场上一批「视频字幕翻译」小 App 靠屏幕 OCR/无障碍读屏（抓不到音频），差评集中在不准/慢。
- 开源先例：LiveTranscribe-Android（MediaProjection 抓音 + Vosk + 悬浮窗）、live-translate（AudioPlaybackCapture + 悬浮双语字幕）。
- 空白所在：系统抓音不开放、MediaProjection 每次授权且对方可拒、通话/VoIP 全覆盖不了、离线中文识别有限——第三方「播放抓音 + 同传悬浮」的闭环已被验证可行，但产品竞争点在授权体验与字幕质量。

来源：<https://www.iflyrec.com/fanyi/64f1487b.html> · <https://app.xiaomi.com/details?id=com.ilivedata.viitor> · <https://github.com/chartmann1590/LiveTranscribe-Android> · <https://github.com/luoxiaoxin123/live-translate> · <https://android.googlesource.com/platform/frameworks/base/+/a3a4bbba98f08cb60b18a3cb2790bdb26c2d71d5>

## 四、技术栈

- 现状（2026-08）：Kotlin 2.4.0；Jetpack Compose 1.11.4（官方「recommended modern toolkit」，Compose-first，View 进入维护模式）；Flutter 3.47；RN 0.87；Tauri 2.11.5。
- 逐条查证的桥接现状：Flutter 的 AudioPlaybackCapture 靠第三方插件（playback_capture / system_audio_recorder），内部全是自写 Kotlin；悬浮窗靠 flutter_overlay_window，同样是原生自写。RN 无主流抓音插件，必须自写原生模块（@parastud/react-native-vban 一类）；悬浮窗社区模块脆弱。Tauri 2 Android 端只是 WebView Activity，官方插件表没有任何音频采集/悬浮窗插件，全要自写 Kotlin 插件桥接。
- 生产实例考证：LiveTranscribe-Android = 纯 Kotlin（Compose 主 UI + View 悬浮窗 + ForegroundService，场景与我们几乎一致）；android_transcribe_app = Kotlin + Rust；Binozo/FlutterPlaybackCapture = Flutter 壳 + 全自写 Kotlin 核心（反向印证）。
- 推荐：**Kotlin + Compose 单端原生**。三大核心能力全是 Android 私有 API，跨端框架最后都得维护同一份 Kotlin 加一层桥；iOS 不做，跨端收益为零；AI 维护下单一官方栈最可控。两个落地注意：① 悬浮字幕窗本体建议 View + WindowManager（先例作者实测 Compose 与 SYSTEM_ALERT_WINDOW 配合不佳），App 内 UI 用 Compose；② MediaProjection token 一次有效，要设计好「停止后再开」的重授权流程。

来源：<https://developer.android.com/develop/ui/compose/first> · <https://github.com/chartmann1590/LiveTranscribe-Android> · <https://github.com/Binozo/FlutterPlaybackCapture> · <https://v2.tauri.app/plugin/> · <https://reactnative.dev/blog/2026/08/11/react-native-0-87>

## 五、对产品与下一步的含义

- **音源模型**：保留「按 App 选」+「全部可抓的声音」兜底（与 `chanpin/android/prototype` 画的形态一致）；按 App 选用 UID 过滤实现，UI 上标注「尽力而为」——对方不让抓时是静音，需要能量检测提示「这个 App 不让抓」。
- **闸门收窄但没过**：管道可行、API 可行、先例存在，剩下唯一未知是**目标直播 App（抖音/B站/TikTok）的实测覆盖**——模拟器先验管道，真机逐个验这张清单：抖音、B站、TikTok、YouTube、Twitch、Netflix + 锁屏行为 + 重授权流程。
- **技术栈**：调研一致指向 Kotlin + Compose，待拍板后立 ADR，spike 与正式客户端都按此执行。
