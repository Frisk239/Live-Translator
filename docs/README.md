# docs/

设计文档。词表（观众、音源、字幕条、顶号……）的真相源在仓库根 [CONTEXT.md](../CONTEXT.md)，先读它再读这里。

- `v1-形态.md` / `v2-形态.md`：第一形态（本机听译）与第二形态（托管听译）的分层说明；`后续.md` 是还没做的形态与立项判断。
- `hosted-listen-spec.md`：托管听译的规格真相源（Problem / User Stories / Implementation Decisions / Testing Decisions）。
- `adr/`：决策日志，编号递增，一文件一个决定。改主意时写新 ADR 记录，旧文只在文末加一行落地补丁指引（如 0018 → 0032）。
- 主题文档（听译优化路线、字幕稳定、改写调度、游戏术语首显）：译文质量路线的专题展开，入口是 `听译优化路线.md`。

产品稿与原型在 `chanpin/`（`desktop/` 为第一形态，`android/` 为后续设计中）。
