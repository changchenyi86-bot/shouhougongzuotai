import { useEffect, useRef, useState } from "react";

/**
 * 把文字复制到剪贴板。
 * 同事用局域网地址（http://10.x…:5173）打开工作台时，浏览器不给网页用新的剪贴板接口，
 * 这时退回老办法：放进一个看不见的输入框里选中再复制。
 */
export async function copyText(text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      /* 退回老办法 */
    }
  }
  const active = document.activeElement as HTMLElement | null;
  const box = document.createElement("textarea");
  box.value = text;
  box.setAttribute("readonly", "");
  box.style.cssText = "position:fixed;top:-1000px;left:0;opacity:0;";
  document.body.appendChild(box);
  box.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  box.remove();
  active?.focus?.();
  return ok;
}

const CopyIcon = () => (
  <svg viewBox="0 0 16 16" aria-hidden>
    <rect x="5.5" y="5.5" width="8" height="8" rx="1.6" fill="none" stroke="currentColor" strokeWidth="1.3" />
    <path d="M10.5 3.5v-.4A1.6 1.6 0 0 0 8.9 1.5H3.6A1.6 1.6 0 0 0 2 3.1v5.3a1.6 1.6 0 0 0 1.6 1.6H4" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
  </svg>
);
const CheckIcon = () => (
  <svg viewBox="0 0 16 16" aria-hidden>
    <path d="M3.5 8.5l3 3 6-6.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

/**
 * 「复制」小按钮：点一下复制整段文字，按钮上短暂显示「已复制」。
 * label 不传时只显示图标（聊天气泡旁边，鼠标移上去才出现）。
 */
export default function CopyButton({ text, label, title = "复制这段文字" }: { text: string; label?: string; title?: string }) {
  const [state, setState] = useState<"idle" | "ok" | "fail">("idle");
  const timer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(timer.current), []);

  const onClick = async () => {
    const ok = await copyText(text);
    setState(ok ? "ok" : "fail");
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState("idle"), 1500);
  };

  const hint = state === "ok" ? "已复制" : state === "fail" ? "复制失败，请手动选中文字复制" : title;
  return (
    <button
      type="button"
      className={`copy-btn ${label ? "with-label" : ""} ${state}`}
      title={hint}
      aria-label={hint}
      onClick={onClick}
    >
      {state === "ok" ? <CheckIcon /> : <CopyIcon />}
      {label && <span>{state === "ok" ? "已复制" : state === "fail" ? "复制失败" : label}</span>}
    </button>
  );
}
