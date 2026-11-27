// 会话输入框的发送快捷键：默认 Enter 发送（Shift+Enter 换行），可改为 Command/Ctrl+Enter 发送（Enter 换行），记在浏览器里
export type SendKey = "enter" | "mod-enter";

const IS_MAC = /Mac|iPhone|iPad|iPod/i.test(navigator.platform || navigator.userAgent);

/** Mac 上是 Command，Windows 上是 Ctrl。 */
export const MOD = IS_MAC ? "Command" : "Ctrl";

export const SEND_KEYS: { key: SendKey; label: string }[] = [
  { key: "enter", label: "Enter 键发送" },
  { key: "mod-enter", label: `${MOD}+Enter 键发送` },
];

const KEY = "sendKey";

export function loadSendKey(): SendKey {
  try {
    return localStorage.getItem(KEY) === "mod-enter" ? "mod-enter" : "enter";
  } catch {
    return "enter";
  }
}

export function saveSendKey(key: SendKey) {
  try {
    localStorage.setItem(KEY, key);
  } catch {
    /* 浏览器禁用存储时忽略 */
  }
}
