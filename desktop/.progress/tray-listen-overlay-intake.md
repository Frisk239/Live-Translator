# intake · tray-listen-overlay

日期：2026-08-18  
裁决：**有条件通过**

## 合并

仓库尚无任何 commit。`docs/` `chanpin/` `reference/` 被根 `.gitignore` 排除。未 push。

## 证据

- 缝 + reducer 测试在磁盘上（`desktop/tests/`），执行会话报 27/27。本窗未复跑。
- 人在场走过：音源检出声进程、面板渲染、首次下载态、开听出字幕、托盘退出。
- 人手验未走完：开听中改模式/字号、字幕窗把手、停止撤窗、托盘右键。

## 对照 Must

齐：Tauri 2 托盘 + 变体 A 面板 + 字幕窗；假听译 WS；`draft|final|notice` 缝；状态机；进程音源枚举（超额）；开听中进程退出监测（超额）；代码只在 `desktop/`。

未齐 / 歪：

- `改用系统混音` 只 `select_source`，不重开听（`desktop/src/panel.ts`）
- 无 `desktop/.gitignore`，`node_modules/` `src-tauri/target/` 会进仓
- `src-tauri/.cargo/config.toml` 写死本机 MSVC 绝对路径
- 面板 `visible: true`，自启会弹出，不是「只进托盘」
- README / `settings.ts` 仍写假清单或 Toolhelp32，实现是 `IAudioSessionManager2`
- 托盘钉在通知区：无代码

## 债务（不阻塞下一刀主题，commit 前必须先处理标 * 的）

- * 补 `desktop/.gitignore`
- * 本机 `.cargo/config.toml` 不要进仓（或改成可覆盖的本地文件）
- `use-system` 选系统混音并重开听
- 自启只进托盘、不弹面板、不开听
- 托盘钉可见
- 弱机器 / 打包带假听译 / 真采音 / 真下模型 — 属后续刀

## 下一刀候选

1. `local-engine`（推荐）：真采音 + SenseVoice + OPUS-MT，缝协议不改
2. `shell-hardening`：只收本刀壳债，无新观众路径
