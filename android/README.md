# 安卓抓音 spike（扔掉用）

立项闸门的管道验证代码（`后续.md` 移动端一节）：MediaProjection 授权 →
`AudioPlaybackCapture` 抓播放音 → PCM 电平可视化。不是正式客户端：
UI 只为验证管道，全部代码不迁移进正式工程。栈与结论见
[ADR 0035](../docs/adr/0035-android-native-kotlin-compose.md) 与
[可行性调研](../docs/research/android-feasibility-stack.md)。

## 跑起来

```bash
# Android Studio 打开本目录，或命令行：
./gradlew installDebug
```

装到**模拟器**先验管道（模拟器没有第三方直播 App，用自播测试音验「抓到了声音」）；
装到**真机**再验目标 App 清单（见下）。

界面两个按钮：

- **开抓**：RECORD_AUDIO + POST_NOTIFICATIONS 授权 → 系统屏幕录制授权 →
  `mediaProjection` 类型前台服务 → AudioRecord（16kHz/mono/PCM16，匹配 USAGE_MEDIA/GAME/UNKNOWN）→
  界面显示实时电平（RMS）与累计字节数。电平在动 = 抓到了。
- **自播 440Hz**：AudioTrack 循环播放测试音（USAGE_MEDIA）。官方文档说抓取流会含
  抓取方自己的声音——正式版要 `excludeUid(Process.myUid())`，spike 用它先验证混音进没进来。

## 立项闸门：真机验证清单

调研结论：opt-out 的 App 表现是**静音而不是失败**，没有检测 API，只能逐个实测。
全部验完才有立项资格（顺序：模拟器管道 → 真机逐 App → 系统行为）。
验一项记一项，日期与结论写行内。

| # | 验什么 | 怎么验 | 结果 |
| --- | --- | --- | --- |
| 1 | 模拟器管道 | 模拟器装 spike + 自播测试音 → 开抓 → 电平在动 | ✅ 2026-08-26 spike37（API 35）：电平 12%·已采 103KB·在抓，字节持续涨 |
| 2 | 自家声音混入 | 同上，确认自播音被抓到（正式版要 excludeUid） | ✅ 2026-08-26 同轮：440Hz 自播进流，电平非零——确认要 excludeUid |
| 1b | 模拟器端到端 | 勾③自播英语素材 → 开抓 → 勾④推流宿主机 account.py（模拟器 10.0.2.2 → 宿主 loopback）→ 等字幕 | ✅ 2026-08-26 同日：155 事件，定稿「Well, we've been going at this for about twenty years now. / 嗯，我们已经进行这件事大约二十年了。」安卓抓音→识别→翻译→字幕回显全链路闭环 |
| 3 | 抖音 | 真机开直播 → 开抓 → 电平 | _待验_ |
| 4 | B 站 | 同上 | _待验_ |
| 5 | TikTok | 同上（海外版，如无法装注明） | _待验_ |
| 6 | YouTube | 同上（调研说可抓，复核） | _待验_ |
| 7 | Twitch | 同上（调研说近期抓不到，复核） | _待验_ |
| 8 | Netflix | 同上（调研说 DRM 抓不到，预期静音） | _待验_ |
| 9 | 锁屏行为 | 抓音中锁屏 → 电平是否停（Android 15 预期停） | _待验_ |
| 10 | 撤销授权 | 抓音中系统撤销投影 → onStop 回调是否到 | _待验_ |
| 11 | 再授权 | 停止后再开 → 新授权流程是否顺（token 一次有效） | _待验_ |

判据：3–5（抖音/B站/TikTok）里至少两个能抓，立项；只有 YouTube 级能抓 →
回到「不做/另想形态」重议。
