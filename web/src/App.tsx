import { useCallback, useEffect, useState } from "react";
import { fetchSendState, type SendState } from "./api";
import Conversations from "./Conversations";
import { applyTheme, loadTheme, saveTheme, THEMES, type Theme } from "./theme";

// 线条图标（24×24 视图，描边绘制）
const ICONS: Record<string, string> = {
  会话: "M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v8a2.5 2.5 0 0 1-2.5 2.5H10l-4.5 4v-4h0A1.5 1.5 0 0 1 4 14.5z",
  工单: "M7 3h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2zM9 8h6M9 12h6M9 16h3",
  知识库: "M4 5a2 2 0 0 1 2-2h5v18H6a2 2 0 0 1-2-2zM13 3h5a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2h-5z",
  积分排名: "M5 20V11M12 20V5M19 20v-6",
};

const NAV: { label: string; active?: boolean }[] = [
  { label: "会话", active: true },
  { label: "工单" },
  { label: "知识库" },
  { label: "积分排名" },
];

function Icon({ d }: { d: string }) {
  return (
    <svg className="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}

function loadOperator(): string {
  try {
    return localStorage.getItem("operator") ?? "";
  } catch {
    return "";
  }
}

export default function App() {
  const [operator, setOperator] = useState(loadOperator);
  const [theme, setTheme] = useState<Theme>(loadTheme);
  const [toast, setToast] = useState<string | null>(null);
  const [sendState, setSendState] = useState<SendState | null>(null);

  const loadSendState = useCallback(() => {
    fetchSendState()
      .then(setSendState)
      .catch(() => setSendState(null));
  }, []);

  useEffect(() => {
    loadSendState();
    const timer = setInterval(loadSendState, 10_000);
    return () => clearInterval(timer);
  }, [loadSendState]);

  useEffect(() => {
    try {
      localStorage.setItem("operator", operator);
    } catch {
      /* 浏览器禁用存储时忽略 */
    }
  }, [operator]);

  useEffect(() => {
    applyTheme(theme);
    saveTheme(theme);
  }, [theme]);

  const notify = (text: string) => {
    setToast(text);
    setTimeout(() => setToast(null), 3000);
  };

  return (
    <div className="layout">
      <aside>
        <h1>
          <span className="logo" aria-hidden>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" />
            </svg>
          </span>
          <span className="brand">售后工作台</span>
        </h1>
        <nav>
          {NAV.map((item) => (
            <a key={item.label} className={item.active ? "active" : "disabled"} title={item.active ? undefined : "即将上线"}>
              <Icon d={ICONS[item.label]} />
              <span>{item.label}</span>
              {!item.active && <small className="soon">即将上线</small>}
            </a>
          ))}
        </nav>
      </aside>
      <main>
        <header>
          <h2>会话</h2>
          {sendState?.enabled && <SendIndicator state={sendState} />}
          <div className="header-tools">
            <div className="theme-switch" role="radiogroup" aria-label="界面风格" title="切换界面风格">
              {THEMES.map((t) => (
                <button key={t.key} role="radio" aria-checked={theme === t.key} className={theme === t.key ? "on" : ""} onClick={() => setTheme(t.key)}>
                  <i className={t.key} />
                  {t.label}
                </button>
              ))}
            </div>
            <label className="operator">
              处理人
              <input value={operator} maxLength={64} placeholder="填写你的名字" onChange={(e) => setOperator(e.target.value)} />
            </label>
          </div>
        </header>
        <Conversations
          operator={operator}
          canAutoSend={(c) => c === "wecom" && !!sendState?.ready}
          notify={notify}
        />
      </main>
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

function SendIndicator({ state }: { state: SendState }) {
  const agent = state.agents[0];
  let text = "发送电脑离线：只能复制文字后手动发送";
  if (state.ready) text = `发送电脑在线（${state.agents.filter((a) => a.wecom_ready).map((a) => a.agent_id).join("、")}）`;
  else if (agent) text = `发送电脑：${agent.detail || "企业微信未就绪"}`;
  return (
    <span className={`send-indicator ${state.ready ? "on" : "off"}`} title="电脑端发送程序操作企业微信客户端，把确认后的消息发到群里">
      <i />
      {text}
    </span>
  );
}
