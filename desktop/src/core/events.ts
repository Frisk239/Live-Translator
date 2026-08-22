/* 壳 ↔ 听译 的缝协议。听译只回这三类事件：草稿 / 定稿 / 提示。
   真听译与假听译共用同一条缝；playback 字段只被假听译用来回放时间轴。

   PCM 上行（壳 → 听译，local-engine 刀起）：WebSocket 二进制帧 =
   f32le / mono / 16kHz PCM 块，无帧头，到达顺序即时间序；JSON 控制消息
   仍是文本帧；假听译对二进制帧一律忽略。 */

/** PCM 帧参数：壳侧采音统一重采样到这个格式再入缝 */
export const PCM_FORMAT = { sampleRate: 16000, channels: 1, sampleFormat: "f32le" } as const;

export type NoticeKind =
  | "no_speech" // 没人声：画面不出东西，面板状态行说明
  | "not_lang" // 不是英 / 日 / 韩的人声：字幕位置出一条提示行
  | "no_audio" // 音源抓不到：停止开听，面板给改用系统混音 / 重试两个出路
  | "crashed"; // 听译挂了：撤字幕窗，托盘通知，重试直接回在听

/** 壳 → 听译 */
export type ShellCommand =
  | {
      type: "start";
      source: string;
      /** 假听译回放参数；真听译忽略 */
      playback?: { script: string; speed?: number };
    }
  | { type: "switch"; source: string }
  | { type: "stop" };

/** 听译 → 壳 */
export type ListenEvent =
  | { type: "draft"; orig: string; trans: string }
  | { type: "final"; orig: string; trans: string }
  | { type: "notice"; kind: NoticeKind };

export function isListenEvent(data: unknown): data is ListenEvent {
  if (typeof data !== "object" || data === null) return false;
  const d = data as Record<string, unknown>;
  if (d.type === "draft" || d.type === "final")
    return typeof d.orig === "string" && typeof d.trans === "string";
  if (d.type === "notice")
    return (
      d.kind === "no_speech" ||
      d.kind === "not_lang" ||
      d.kind === "no_audio" ||
      d.kind === "crashed"
    );
  return false;
}
