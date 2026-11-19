import { useCallback, useEffect, useState } from "react";
import { fetchSendState, type SendState } from "./api";
import Conversations from "./Conversations";

const NAV: { label: string; active?: boolean }[] = [
  { label: "会话", active: true },
  { label: "工单" },
  { label: "知识库" },
  { label: "积分排名" },
];

function loadOperator(): string {
  try {
    return localStorage.getItem("operator") ?? "";
  } catch {
    return "";
  }
}

export default function App() {
  const [operator, setOperator] = useState(loadOperator);
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

  const notify = (text: string) => {
    setToast(text);
    setTimeout(() => setToast(null), 3000);
  };

  return (
    <div className="layout">
      <aside>
        <h1>售后工作台</h1>
        <nav>
          {NAV.map((item) => (
            <a key={item.label} className={item.active ? "active" : "disabled"} title={item.active ? undefined : "即将上线"}>
              {item.label}
            </a>
          ))}
        </nav>
      </aside>
      <main>
        <header>
          <h2>会话</h2>
          {sendState?.enabled && <SendIndicator state={sendState} />}
          <label className="operator">
            处理人
            <input value={operator} maxLength={64} placeholder="填写你的名字" onChange={(e) => setOperator(e.target.value)} />
          </label>
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
