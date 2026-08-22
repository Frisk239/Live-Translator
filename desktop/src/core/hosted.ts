export type ListenWay = "local" | "local_llm" | "hosted";
export type AccountSession = { email: string } | null;
export type ListenPane = "sources" | "login";

export function normalizeListenWay(value: string | undefined): ListenWay {
  if (value === "hosted") return "hosted";
  return "local";
}

export function listenPane(way: ListenWay, session: AccountSession): ListenPane {
  if (way === "hosted" && !session) return "login";
  return "sources";
}
