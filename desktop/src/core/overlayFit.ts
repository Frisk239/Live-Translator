/** 字幕窗高度：内容超出才长，不缩，避免换条时窗口跳动。 */
export function nextOverlayHeight(
  currentPx: number,
  contentPx: number,
  maxPx: number,
  slackPx = 2
): number | null {
  if (contentPx <= currentPx + slackPx) return null;
  return Math.min(Math.ceil(contentPx), maxPx);
}
