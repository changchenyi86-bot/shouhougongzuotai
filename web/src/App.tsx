import { useEffect, useState } from "react";
import { CHANNEL_LABEL, fetchMessages, type Channel, type Message } from "./api";

const NAV = ["消息收件箱", "工单", "知识库", "积分排名"] as const;

export default function App() {
  const [channel, setChannel] = useState<Channel | undefined>();
  const [messages, setMessages] = useState<Message[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    fetchMessages(channel).then(setMessages).catch((e: Error) => setError(e.message));
  }, [channel]);

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
          <div className="filters">
            <button className={!channel ? "on" : ""} onClick={() => setChannel(undefined)}>全部</button>
            {(Object.keys(CHANNEL_LABEL) as Channel[]).map((c) => (
              <button key={c} className={channel === c ? "on" : ""} onClick={() => setChannel(c)}>
                {CHANNEL_LABEL[c]}
              </button>
            ))}
          </div>
        </header>
        {error && <p className="error">{error}</p>}
        {!error && messages.length === 0 && <p className="empty">暂无消息</p>}
        <ul className="messages">
          {messages.map((m) => (
            <li key={m.id}>
              <div className="meta">
                <span className={`tag ${m.channel}`}>{CHANNEL_LABEL[m.channel]}</span>
                <span>{m.group_name ?? m.group_id}</span>
                <span>{m.sender_name ?? m.sender_id}</span>
                <time>{new Date(m.sent_at).toLocaleString("zh-CN")}</time>
              </div>
              <p>{m.content}</p>
            </li>
          ))}
        </ul>
      </main>
    </div>
  );
}
