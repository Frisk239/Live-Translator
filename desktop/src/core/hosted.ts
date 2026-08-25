export type ListenWay = "local" | "local_llm" | "hosted";
export type AccountSession = { email: string; token: string } | null;
export type ListenPane = "sources" | "login";

/** 安装包写死的源；没有域名之前与开发回环相同。 */
export const DEFAULT_HOSTED_ORIGIN = "http://127.0.0.1:8787";

export function hostedOrigin(override?: string | null): string {
  const v = override?.trim().replace(/\/$/, "");
  if (v) return v;
  return DEFAULT_HOSTED_ORIGIN;
}

export function normalizeListenWay(value: string | undefined): ListenWay {
  if (value === "hosted") return "hosted";
  return "local";
}

export function listenPane(way: ListenWay, session: AccountSession): ListenPane {
  if (way === "hosted" && !session) return "login";
  return "sources";
}

export type AccountReply = { email?: string; token?: string; error?: string };

/** 账号缝的 JSON POST（注册 / 登录 / 退出 / 改密码 / 会话校验）。 */
export async function postAccount(
  origin: string,
  path: string,
  body: unknown,
): Promise<{ status: number; payload: AccountReply }> {
  const res = await fetch(`${origin}/account/${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = (await res.json().catch(() => ({}))) as AccountReply;
  return { status: res.status, payload };
}
