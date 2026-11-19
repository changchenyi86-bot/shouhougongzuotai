import { useCallback, useEffect, useState } from "react";
import { CHANNEL_LABEL, fetchInbox, fetchSendState, type Channel, type InboxItem, type SendState, type View } from "./api";
import SuggestionCard from "./SuggestionCard";

const NAV = ["消息收件箱", "工单", "知识库", "积分排名"] as const;
const VIEWS: { key: View; label: string }[] = [
  { key: "todo", label: "待处理" },
  { key: "escalated", label: "已转人工" },
  { key: "done", label: "已处理" },
  { key: "all", label: "全部消息" },
];

function loadOperator(): string {
  try {
    return localStorage.getItem("operator") ?? "";
  } catch {
    return "";
  }
}

export default function App() {
  const [view, setView] = useState<View>("todo");
  const [channel, setChannel] = useState<Channel | undefined>();
  const [items, setItems] = useState<InboxItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [operator, setOperator] = useState(loadOperator);
  const [toast, setToast] = useState<string | null>(null);
  const [sendState, setSendState] = useState<SendState | null>(null);

  const load = useCallback(() => {
    fetchInbox(view, channel)
      .then((data) => {
        setItems(data);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
    fetchSendState()
      .then(setSendState)
      .catch(() => setSendState(null));
  }, [view, channel]);

  // 有正在排队/发送的回复时刷新快一些，尽快看到发送结果
  const sending = items.some((i) => i.suggestion?.send_status === "queued" || i.suggestion?.send_status === "sending");

  useEffect(() => {
    load();
    const timer = setInterval(load, sending ? 3_000 : 10_000);
    return () => clearInterval(timer);
  }, [load, sending]);

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
          {NAV.map((item, i) => (
            <a key={item} className={i === 0 ? "active" : "disabled"} title={i ? "即将上线" : undefined}>
              {item}
            </a>
          ))}
        </nav>
      </aside>
      <main>
        <header>
          <h2>消息收件箱</h2>
          {sendState?.enabled && <SendIndicator state={sendState} />}
          <label className="operator">
            处理人
            <input value={operator} maxLength={64} placeholder="填写你的名字" onChange={(e) => setOperator(e.target.value)} />
          </label>
        </header>
        <div className="toolbar">
          <div className="tabs">
            {VIEWS.map((v) => (
              <button key={v.key} className={view === v.key ? "on" : ""} onClick={() => setView(v.key)}>
                {v.label}
              </button>
            ))}
          </div>
          <div className="filters">
            <button className={!channel ? "on" : ""} onClick={() => setChannel(undefined)}>全部渠道</button>
            {(Object.keys(CHANNEL_LABEL) as Channel[]).map((c) => (
              <button key={c} className={channel === c ? "on" : ""} onClick={() => setChannel(c)}>
                {CHANNEL_LABEL[c]}
              </button>
            ))}
          </div>
        </div>
        {error && <p className="error">加载失败：{error}</p>}
        {!error && items.length === 0 && <p className="empty">暂无消息</p>}
        <ul className="messages">
          {items.map(({ message: m, suggestion }) => (
            <li key={m.id}>
              <div className="meta">
                <span className={`tag ${m.channel}`}>{CHANNEL_LABEL[m.channel]}</span>
                {m.is_external_group && <span className="ext">客户群</span>}
                <span className="group">{m.group_name ?? m.group_id}</span>
                <span>{m.sender_name ?? m.sender_id}</span>
                <time>{new Date(m.sent_at).toLocaleString("zh-CN")}</time>
              </div>
              <p className="content">{m.content}</p>
              {suggestion && (
                <SuggestionCard
                  suggestion={suggestion}
                  message={m}
                  canAutoSend={m.channel === "wecom" && !!sendState?.ready}
                  operator={operator}
                  onChanged={load}
                  notify={notify}
                />
              )}
            </li>
          ))}
        </ul>
      </main>
      {toast && <div className="toast">{toast}</div>}
    </div>
  );
}

function SendIndicator({ state }: { state: SendState }) {
  const agent = state.agents[0];
  let text = "自动发送：发送程序离线，只能复制后手动发送";
  if (state.ready) text = `自动发送：在线（${state.agents.filter((a) => a.wecom_ready).map((a) => a.agent_id).join("、")}）`;
  else if (agent) text = `自动发送：${agent.detail || "企业微信未就绪"}`;
  return (
    <span className={`send-indicator ${state.ready ? "on" : "off"}`} title="电脑端发送程序操作企业微信客户端，把确认后的回复发到群里">
      <i />
      {text}
    </span>
  );
}
