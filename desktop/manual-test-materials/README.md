# 手动听译测试素材（播客 / 口语）

朗读基线换成开源播客与口语对话：长句、口气、从句切条才跟得上真实听译。B 站 BV1ZLkfBvEMX（《Daily English Podcast》）是版权汇编，不能当开源素材；下面用同类口语、可再分发的录音。

## 素材矩阵

| 文件 | 语言 | 时长 | 语体 | 授权 | 考什么 |
| --- | --- | ---: | --- | --- | --- |
| `01-en-hpr-podcast.mp3` | 英 | 80s | Hacker Public Radio 独白播客 | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | 口语长句、专名（Openverse / Flickr） |
| `02-en-rubenerd-podcast.mp3` | 英 | 90s | Rubenerd Show 现场独白 | [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/) | 连说、换气少、切条是否跟嘴 |
| `03-ko-fsi-dialogue.mp3` | 韩 | 80s | FSI 韩语会话（两人问答） | 美国政府作品，公有领域 | 韩语口语、问答节奏 |

日语开源播客这次没下到（TalkBank 需登录，YouTube 超时）。有合适的 CC 日语播客再补。

## 来源

- HPR 3977 *Creative Commons Search Engine*（Ahuka）：[archive.org/details/hpr3977](https://archive.org/details/hpr3977)，截 40–120s。
- Rubenerd Show 423 *The Tokyo 2022 episode*（Ruben Schade）：[archive.org/details/RubenerdShow423](https://archive.org/details/RubenerdShow423)，截 120–210s。
- FSI *Korean Basic Course* Volume 1 Unit 02：[archive.org/details/FSIKoreanBasicCourseVolume1Unit02](https://archive.org/details/FSIKoreanBasicCourseVolume1Unit02)，截 45–125s。

## 手验

1. 一次只放一条，选播放器进程作音源，不要选系统混音。
2. 看字幕条是否按口气/从句切，而不是等整段说完才换。
3. 开了改写时，纠正应能在下一条挤上来之前看清。

## 探针

```powershell
cd desktop
$env:PYTHONIOENCODING='utf-8'
python tools/quality_probe.py --only en,en2,ko --runs 1
```
