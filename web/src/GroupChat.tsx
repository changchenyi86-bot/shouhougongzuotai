import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { CHANNEL_LABEL, fetchConversation, fetchGroups, type Channel, type Group, type InboxItem, type Message } from "./api";
import SuggestionCard from "./SuggestionCard";

export interface ChatTarget {
  channel: Channel;
  group_id: string;
  anchor?: string; // 打开时定位到这条消息
}

interface Props {
  target: ChatTarget | null;
  onTarget: (t: ChatTarget | null) => void;
  operator: string;
  canAutoSend: (channel: Channel) => boolean;
  notify: (text: string) => void;
}

const sameGroup = (a: { channel: Channel; group_id: string } | null, b: { channel: Channel; group_id: string }) =>
  !!a && a.channel === b.channel && a.group_id === b.group_id;

function senderLabel(m: Message): string {
  if (m.sender_name) return m.sender_name;
  // 企业微信会话存档只给 ID：员工是账号名，客户是一长串 ID，只显示末尾几位便于区分
  return m.from_customer ? `客户 ·${m.sender_id.slice(-4)}` : m.sender_id;
}

function day(iso: string) {
  return new Date(iso).toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "short" });
}

function shortTime(iso: string) {
  const d = new Date(iso);
  const today = new Date().toDateString() === d.toDateString();
  return today
    ? d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

export default function GroupChat({ target, onTarget, operator, canAutoSend, notify }: Props) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [search, setSearch] = useState("");
  const [items, setItems] = useState<InboxItem[]>([]);
  const [moreBefore, setMoreBefore] = useState(false);
  const [moreAfter, setMoreAfter] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const listRef = useRef<HTMLDivElement>(null);
  const scrollPlan = useRef<{ kind: "bottom" } | { kind: "anchor"; id: string } | { kind: "keep"; height: number; top: number } | null>(null);

  const loadGroups = useCallback(() => {
    fetchGroups()
      .then(setGroups)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    loadGroups();
    const t = setInterval(loadGroups, 15_000);
    return () => clearInterval(t);
  }, [loadGroups]);

  // 打开一个群：定位到指定消息，或显示最新的 50 条
  useEffect(() => {
    if (!target) return;
    setItems([]);
    setLoading(true);
    fetchConversation(target.channel, target.group_id, target.anchor ? { anchor: target.anchor, limit: 40 } : {})
      .then((c) => {
        scrollPlan.current = target.anchor ? { kind: "anchor", id: target.anchor } : { kind: "bottom" };
        setItems(c.items);
        setMoreBefore(c.has_more_before);
        setMoreAfter(c.has_more_after);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [target]);

  useLayoutEffect(() => {
    const el = listRef.current;
    const plan = scrollPlan.current;
    if (!el || !plan) return;
    scrollPlan.current = null;
    if (plan.kind === "bottom") el.scrollTop = el.scrollHeight;
    else if (plan.kind === "keep") el.scrollTop = plan.top + (el.scrollHeight - plan.height);
    else document.getElementById(`m-${plan.id}`)?.scrollIntoView({ block: "center" });
  }, [items]);

  const loadOlder = async () => {
    if (!target || !items.length) return;
    const el = listRef.current;
    try {
      const c = await fetchConversation(target.channel, target.group_id, { before: items[0].message.id });
      if (el) scrollPlan.current = { kind: "keep", height: el.scrollHeight, top: el.scrollTop };
      setItems((prev) => [...c.items, ...prev]);
      setMoreBefore(c.has_more_before);
    } catch (e) {
      notify(`加载失败：${(e as Error).message}`);
    }
  };

  const loadNewer = async () => {
    if (!target || !items.length) return;
    try {
      const c = await fetchConversation(target.channel, target.group_id, { after: items[items.length - 1].message.id });
      setItems((prev) => [...prev, ...c.items]);
      setMoreAfter(c.has_more_after);
    } catch (e) {
      notify(`加载失败：${(e as Error).message}`);
    }
  };

  // 刷新已加载的最近 100 条（AI 建议、发送状态会变），停在最新处时顺便带上新消息
  const refresh = useCallback(async () => {
    if (!target || !items.length) return;
    const start = Math.max(0, items.length - 100);
    const count = items.length - start;
    try {
      const c = await fetchConversation(target.channel, target.group_id, {
        anchor: items[start].message.id,
        limit: Math.min(200, 2 * count + (moreAfter ? 0 : 20)),
      });
      const from = c.items.findIndex((i) => i.message.id === items[start].message.id);
      let fresh = c.items.slice(from);
      if (moreAfter) fresh = fresh.slice(0, count); // 没停在最新处，只更新已显示的
      const el = listRef.current;
      const atBottom = !!el && el.scrollHeight - el.scrollTop - el.clientHeight < 40;
      if (atBottom && fresh.length > count) scrollPlan.current = { kind: "bottom" };
      setItems((prev) => [...prev.slice(0, start), ...fresh]);
      if (!moreAfter) setMoreAfter(c.has_more_after);
    } catch {
      /* 下次再刷新 */
    }
  }, [target, items, moreAfter]);

  useEffect(() => {
    const t = setInterval(refresh, 10_000);
    return () => clearInterval(t);
  }, [refresh]);

  const shown = groups.filter((g) => !search.trim() || (g.group_name ?? g.group_id).includes(search.trim()));
  const current = target ? groups.find((g) => sameGroup(target, g)) : undefined;

  return (
    <div className="chat-layout">
      <div className={`group-list ${target ? "has-target" : ""}`}>
        <input className="group-search" placeholder="搜索群名" value={search} onChange={(e) => setSearch(e.target.value)} />
        {shown.length === 0 && <p className="empty">暂无群聊</p>}
        <ul>
          {shown.map((g) => (
            <li
              key={`${g.channel}:${g.group_id}`}
              className={sameGroup(target, g) ? "on" : ""}
              onClick={() => onTarget({ channel: g.channel, group_id: g.group_id })}
            >
              <div className="g-top">
                <span className="g-name">{g.group_name ?? g.group_id}</span>
                <time>{shortTime(g.last_message_at)}</time>
              </div>
              <div className="g-bottom">
                <span className={`tag ${g.channel}`}>{CHANNEL_LABEL[g.channel]}</span>
                {g.is_external_group && <span className="ext">客户群</span>}
                <span className="g-preview">{g.last_content}</span>
                {g.pending_count > 0 && <span className="g-badge">{g.pending_count}</span>}
              </div>
            </li>
          ))}
        </ul>
      </div>
      <section className="chat-pane">
        {!target && <p className="empty pad">在左边选一个群，查看完整聊天记录</p>}
        {target && (
          <>
            <div className="chat-head">
              <button className="link back" onClick={() => onTarget(null)}>‹ 群列表</button>
              <b>{current?.group_name ?? items[0]?.message.group_name ?? target.group_id}</b>
              {current && <span>共 {current.message_count} 条消息</span>}
            </div>
            <div className="chat-list" ref={listRef}>
              {error && <p className="error">加载失败：{error}</p>}
              {loading && <p className="empty pad">加载中…</p>}
              {moreBefore && (
                <button className="load-more" onClick={loadOlder}>加载更早的消息</button>
              )}
              {!loading && !moreBefore && items.length > 0 && <p className="chat-start">已经是最早的消息</p>}
              {items.map(({ message: m, suggestion }, i) => (
                <div key={m.id}>
                  {(i === 0 || day(items[i - 1].message.sent_at) !== day(m.sent_at)) && (
                    <div className="day-sep">{day(m.sent_at)}</div>
                  )}
                  <div id={`m-${m.id}`} className={`bubble-row ${m.from_customer ? "cust" : "staff"} ${target.anchor === m.id ? "anchor" : ""}`}>
                    <div className="who">
                      {senderLabel(m)}
                      <time>{new Date(m.sent_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}</time>
                    </div>
                    <div className={`bubble ${m.msg_type !== "text" ? "media" : ""}`}>{m.content}</div>
                    {suggestion && (
                      <div className="bubble-sug">
                        <SuggestionCard
                          suggestion={suggestion}
                          message={m}
                          canAutoSend={canAutoSend(m.channel)}
                          operator={operator}
                          onChanged={refresh}
                          notify={notify}
                        />
                      </div>
                    )}
                  </div>
                </div>
              ))}
              {moreAfter && (
                <button className="load-more" onClick={loadNewer}>加载更新的消息</button>
              )}
            </div>
          </>
        )}
      </section>
    </div>
  );
}
