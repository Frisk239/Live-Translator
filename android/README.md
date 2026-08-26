# 安卓客户端（正式工程）

观众听译的安卓端（[ADR 0035](../docs/adr/0035-android-native-kotlin-compose.md)：
Kotlin + Compose 单端原生；[ADR 0036](../docs/adr/0036-android-compute-hosted-only.md)：
端上零听译算力，只抓音推流 + 字幕浮窗）。设计真相源见
[android-spec](../docs/android-spec.md)，生命周期与凭据语义见
ADR 0037 / 0038。

## 跑起来

```bash
# 命令行（本机 JAVA_HOME 指 JDK 21，见记忆 android-toolchain-gotchas）：
./gradlew installDebug
```

连本机托管服务（模拟器上 `10.0.2.2` 即宿主机 loopback）：

```bash
LIVE_HOSTED_HTTP=http://10.0.2.2:8787 ./gradlew installDebug
# 或改 gradle.properties；默认值就是 http://10.0.2.2:8787
```

模拟器自验：面板勾「（调试）自播英语素材」+ 音源选「（调试）全部声音含本 App」
（debug 构建才有这两项），开听 → 投屏授权（Entire screen）→ 等字幕浮窗。
注意默认音源「全部可抓的声音」会排除本 App——自播素材抓不到，8 秒后状态行
会提示静音，这是预期行为。

## 结构

```
app/src/main/java/com/livetranslator/android/
├── core/        缝协议（Seam）+ 字幕条状态机（CaptionState/CaptionReducer）+ 偏好
├── account/     账号仓库（AccountRepo）+ 凭据存储（TokenStore，Keystore AES-GCM）
├── listen/      音源枚举（Sources）+ 状态总线（ListenBus）+ 缝客户端（HostedLink）
│                + 前台监听服务（ListenService：抓音推流/常驻通知/生命周期）
├── overlay/     悬浮字幕窗（CaptionOverlay：View + WindowManager，ADR 0035）
└── ui/          四屏：Onboarding / Login / Panel / Profile（单 Activity）
```

## 测试

- JVM 单测：`./gradlew :app:testDebugUnitTest`（缝解析、字幕状态机、账号、
  缝客户端行为——auth/start 透传、顶号终态不重连、闪断再开）
- 仪器冒烟：`./gradlew :app:connectedDebugAndroidTest`（登录屏/面板渲染）
- 模拟器 E2E（手跑）：上面「跑起来」的自验流程；2026-08-26 跑通全链路——
  注册登录 → 开听授权 → 自播素材 → 宿主识别翻译 → 浮窗字幕出现 → 静默撤条
  → 停止复位。修出的关键 bug：缝回调线程直接改 View（必须 post 到主线程）、
  主动停止被 projection.onStop 回调覆盖成「锁屏」文案、音源选择不持久。

## 立项闸门：真机验证清单

模拟器闸门已过（下表 #1/2/1b）。真机逐 App 验证 opt-out 表现（静音而非失败，
无检测 API，只能实测）；全部验完才有立项资格。

| # | 验什么 | 怎么验 | 结果 |
| --- | --- | --- | --- |
| 1 | 模拟器管道 | 模拟器 + 自播测试音 → 开抓 → 电平在动 | ✅ 2026-08-26 spike37（API 35） |
| 2 | 自家声音混入 | 确认自播音被抓到（正式版已 excludeUid） | ✅ 2026-08-26 |
| 1b | 模拟器端到端 | 自播素材 → 宿主 account.py → 字幕回显 | ✅ 2026-08-26 spike 155 事件；同日正式客户端 E2E 全通（浮窗+撤条+停止复位） |
| 3 | 抖音 | 真机开直播 → 开听 → 看字幕/静音提示 | _待验_ |
| 4 | B 站 | 同上 | _待验_ |
| 5 | TikTok | 同上（海外版，如无法装注明） | _待验_ |
| 6 | YouTube | 同上（调研说可抓，复核） | _待验_ |
| 7 | Twitch | 同上（调研说近期抓不到，复核） | _待验_ |
| 8 | Netflix | 同上（DRM 预期静音） | _待验_ |
| 9 | 锁屏行为 | 听译中锁屏 → 是否即停（Android 15 预期停） | _待验_ |
| 10 | 撤销授权 | 系统撤销投影 → onStop 是否停听 | _待验_ |
| 11 | 再授权 | 停止后再开 → 新授权流程是否顺（token 一次有效） | _待验_ |

判据：3–5（抖音/B站/TikTok）里至少两个能抓，立项；只有 YouTube 级能抓 →
回到「不做/另想形态」重议。
