import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  CHANNEL_LABEL,
  cancelTask,
  dismissTask,
  endConversation,
  fetchConversation,
  fetchGroups,
  resendTask,
  setGroupName,
  type Channel,
  type ConversationEnd,
  type Group,
  type GroupView,
  type InboxItem,
  type SendTask,
  type Suggestion,
} from "./api";
import Composer, { customerLabel } from "./Composer";
import MessageBody, { AttachmentView } from "./MessageBody";

export interface ChatTarget {
  channel: Channel;
  group_id: string;
}

interface Props {
  canAutoSend: (channel: Channel) => boolean;
  notify: (text: string) => void;
}

const VIEWS: { key: GroupView; label: string }[] = [
  { key: "todo", label: "待处理" },
  { key: "escalated", label: "已转人工" },
  { key: "all", label: "全部" },
];
const OPEN: Suggestion["status"][] = ["generating", "ready", "failed", "escalated"];

const sameGroup = (a: ChatTarget | null, b: ChatTarget) => !!a && a.channel === b.channel && a.group_id === b.group_id;

/** 查不到群名的群（如外部客户群没有查询权限）显示成「未命名的群」，带上群 ID 末几位方便区分 */
const groupLabel = (name: string | null, id: string) => name ?? `未命名的群 · ${id.slice(-4)}`;

function day(iso: string) {
  return new Date(iso).toLocaleDateString("zh-CN", { year: "numeric", month: "long", day: "numeric", weekday: "short" });
}

const hm = (iso: string) => new Date(iso).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
const ms = (iso: string) => new Date(iso).getTime();
const groupKey = (g: ChatTarget) => `${g.channel}:${g.group_id}`;

function shortTime(iso: string) {
  const d = new Date(iso);
  return new Date().toDateString() === d.toDateString()
    ? hm(iso)
    : d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function SugChip({ s, focused, onFocus }: { s: Suggestion; focused: boolean; onFocus: () => void }) {
  if (s.status === "adopted" || s.status === "edited")
    return <span className="chip-status ok" title={s.final_content ?? undefined}>已回复{s.handled_by ? ` · ${s.handled_by}` : ""}</span>;
  if (s.status === "dismissed") return <span className="chip-status">无需回复</span>;
  if (s.status === "closed")
    return (
      <span className="chip-status" title={`${s.handled_by ?? ""}结束会话时这条还没回复`}>
        会话已结束
      </span>
    );
  const label = { generating: "AI 生成中…", ready: "AI 建议待处理", failed: "AI 建议生成失败", escalated: "已转人工" }[s.status];
  return (
    <button className={`chip-status open ${s.status} ${focused ? "on" : ""}`} onClick={onFocus}>
      {label}
    </button>
  );
}

function EndSep({ end }: { end: ConversationEnd }) {
  const today = new Date().toDateString() === new Date(end.ended_at).toDateString();
  const when = today ? hm(end.ended_at) : `${new Date(end.ended_at).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })} ${hm(end.ended_at)}`;
  return (
    <div className="end-sep" title={end.closed_count ? `${end.closed_count} 条没回复的消息随会话结束` : undefined}>
      <span>
        {end.ended_by ? `${end.ended_by} 结束了会话` : "会话已结束"} · {when}
      </span>
    </div>
  );
}

function OutboxBubble({ task, busy, onAction }: { task: SendTask; busy: boolean; onAction: (kind: "resend" | "cancel" | "dismiss") => void }) {
  const [confirming, setConfirming] = useState(false);
  const status = {
    queued: <span className="badge send">排队等待发送</span>,
    sending: <span className="badge send">发送电脑正在发送…</span>,
    sent: <span className="badge ok">已发出 · 稍后出现在聊天记录里</span>,
    failed: <span className="badge err">发送失败</span>,
    cancelled: <span className="badge warn">已取消</span>,
    manual: <span className="badge">已手动处理</span>,
  }[task.status];
  const needsAction = task.status === "failed" || task.status === "cancelled";
  return (
    <div className={`bubble-row staff outbox ${task.status}`}>
      <div className="who">
        {task.created_by ?? "工作台"}
        <time>{hm(task.created_at)}</time>
      </div>
      {task.attachments.map((a) => (
        <AttachmentView key={a.id} att={a} />
      ))}
      {task.content && <div className="bubble">{task.content}</div>}
      <div className="outbox-status">
        {status}
        {task.status === "failed" && task.error && <span className="send-error">{task.error}</span>}
        {task.status === "queued" && (
          <button className="link" disabled={busy} onClick={() => onAction("cancel")}>取消发送</button>
        )}
        {needsAction && !confirming && (
          <>
            <button className="link" disabled={busy} onClick={() => setConfirming(true)}>重新发送</button>
            <button className="link" disabled={busy} onClick={() => onAction("dismiss")}>
              {task.status === "failed" ? "我已手动发送" : "不发了"}
            </button>
          </>
        )}
      </div>
      {confirming && (
        <div className="confirm-send">
          <span>确认重新发送到群里？</span>
          <button className="primary" disabled={busy} onClick={() => { setConfirming(false); onAction("resend"); }}>确认重新发送</button>
          <button disabled={busy} onClick={() => setConfirming(false)}>取消</button>
        </div>
      )}
    </div>
  );
}

export default function Conversations({ canAutoSend, notify }: Props) {
  const [view, setView] = useState<GroupView>("todo");
  const [groups, setGroups] = useState<Group[]>([]);
  const [search, setSearch] = useState("");
  const [target, setTarget] = useState<ChatTarget | null>(null);
  const [items, setItems] = useState<InboxItem[]>([]);
  const [outbox, setOutbox] = useState<SendTask[]>([]);
  const [ends, setEnds] = useState<ConversationEnd[]>([]);
  const [endAsk, setEndAsk] = useState<string | null>(null); // 群列表里正在确认结束的群
  const [headAsk, setHeadAsk] = useState(false); // 会话顶部正在确认结束
  const [moreBefore, setMoreBefore] = useState(false);
  const [moreAfter, setMoreAfter] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [nameDraft, setNameDraft] = useState<string | null>(null); // 正在填写的群名
  const listRef = useRef<HTMLDivElement>(null);
  const stick = useRef(true); // 停在最底部时，图片加载完撑高了页面也保持在底部
  const scrollPlan = useRef<{ kind: "bottom" } | { kind: "keep"; height: number; top: number } | null>(null);

  const loadGroups = useCallback(() => {
    fetchGroups(view)
      .then((g) => {
        setGroups(g);
        setError(null);
      })
      .catch((e: Error) => setError(e.message));
  }, [view]);

  useEffect(() => {
    loadGroups();
    const t = setInterval(loadGroups, 15_000);
    return () => clearInterval(t);
  }, [loadGroups]);

  // 打开一个群：显示最新的 50 条
  useEffect(() => {
    setNameDraft(null);
    setHeadAsk(false);
    if (!target) return;
    setItems([]);
    setOutbox([]);
    setEnds([]);
    setFocusId(null);
    setLoading(true);
    fetchConversation(target.channel, target.group_id)
      .then((c) => {
        scrollPlan.current = { kind: "bottom" };
        setItems(c.items);
        setOutbox(c.outbox);
        setEnds(c.ends);
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
    else el.scrollTop = plan.top + (el.scrollHeight - plan.height);
  }, [items, outbox]);

  const loadOlder = async () => {
    if (!target || !items.length) return;
    const el = listRef.current;
    try {
      const c = await fetchConversation(target.channel, target.group_id, { before: items[0].message.id });
      if (el) scrollPlan.current = { kind: "keep", height: el.scrollHeight, top: el.scrollTop };
      setItems((prev) => [...c.items, ...prev]);
      setEnds(c.ends);
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
      setOutbox(c.outbox);
      setEnds(c.ends);
      setMoreAfter(c.has_more_after);
    } catch (e) {
      notify(`加载失败：${(e as Error).message}`);
    }
  };

  // 刷新已加载的最近 100 条（AI 建议会变）和发件状态，停在最新处时顺便带上新消息
  const refresh = useCallback(
    async (toBottom = false) => {
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
        const atBottom = !!el && el.scrollHeight - el.scrollTop - el.clientHeight < 60;
        if (toBottom || (atBottom && (fresh.length > count || c.outbox.length !== outbox.length)))
          scrollPlan.current = { kind: "bottom" };
        setItems((prev) => [...prev.slice(0, start), ...fresh]);
        setOutbox(c.outbox);
        setEnds(c.ends);
        if (!moreAfter) setMoreAfter(c.has_more_after);
      } catch {
        /* 下次再刷新 */
      }
    },
    [target, items, moreAfter, outbox.length],
  );

  const sending = outbox.some((t) => t.status === "queued" || t.status === "sending");
  useEffect(() => {
    // 有消息正在发送时每秒看一次结果，发完马上显示「已发出」
    const t = setInterval(() => refresh(), sending ? 1_000 : 10_000);
    return () => clearInterval(t);
  }, [refresh, sending]);

  const changed = () => {
    refresh(true);
    loadGroups();
  };

  const saveName = async () => {
    if (!target || nameDraft === null || !nameDraft.trim()) return;
    setBusy(true);
    try {
      const r = await setGroupName(target.channel, target.group_id, nameDraft);
      notify(r.duplicate ? `群名已保存。还有别的群也叫「${r.name}」，为防发错群，这些群不能自动发送` : "群名已保存");
      setNameDraft(null);
      changed();
    } catch (e) {
      notify(`保存失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  // 结束会话：没回复的客户消息不再提醒，客户再发消息时这个群重新回到待处理
  const endGroup = async (g: ChatTarget) => {
    setBusy(true);
    try {
      const r = await endConversation(g.channel, g.group_id);
      notify(r.closed_count ? `会话已结束，${r.closed_count} 条没回复的消息不再提醒` : "会话已结束");
      setEndAsk(null);
      setHeadAsk(false);
      if (sameGroup(target, g)) refresh(true);
    } catch (e) {
      notify(`结束失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
      loadGroups();
    }
  };

  useEffect(() => {
    if (!endAsk && !headAsk) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setEndAsk(null);
      setHeadAsk(false);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [endAsk, headAsk]);

  const taskAction = async (task: SendTask, kind: "resend" | "cancel" | "dismiss") => {
    setBusy(true);
    try {
      if (kind === "resend") await resendTask(task.id);
      else if (kind === "cancel") await cancelTask(task.id);
      else await dismissTask(task.id);
      changed();
    } catch (e) {
      notify(`操作失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  // 待回复的客户消息：默认处理最新的一条，点气泡上的标签可以换
  const open = useMemo(() => items.filter((i) => i.suggestion && OPEN.includes(i.suggestion.status)), [items]);
  const focus = open.find((i) => i.suggestion!.id === focusId) ?? open[open.length - 1] ?? null;
  const shownIndex = focus ? open.indexOf(focus) : 0;

  const shown = groups.filter((g) => !search.trim() || groupLabel(g.group_name, g.group_id).includes(search.trim()));
  const current = target ? groups.find((g) => sameGroup(target, g)) : undefined;
  const groupName =
    current?.group_name ?? [...items].reverse().find((i) => i.message.group_name)?.message.group_name ?? null;

  // 还没处理完的：没回复的客户消息、没发出去的回复。群列表里的数是全群的，聊天里只算已加载的
  const unsent = outbox.filter((t) => t.status === "failed" || t.status === "cancelled").length;
  const openCount = Math.max(current ? current.pending_count + current.escalated_count : 0, open.length + unsent);

  // 会话结束的分隔线放在结束前的最后一条消息后面；落在已加载范围之外的不显示
  const endsAt = useMemo(() => {
    const before: ConversationEnd[] = [];
    const after = new Map<number, ConversationEnd[]>();
    if (!items.length) return { before, after };
    for (const e of ends) {
      const t = ms(e.ended_at);
      if (t < ms(items[0].message.sent_at)) {
        if (!moreBefore) before.push(e);
        continue;
      }
      let i = items.length - 1;
      while (i > 0 && ms(items[i].message.sent_at) > t) i--;
      if (i === items.length - 1 && moreAfter) continue;
      after.set(i, [...(after.get(i) ?? []), e]);
    }
    return { before, after };
  }, [ends, items, moreBefore, moreAfter]);

  return (
    <div className="chat-layout">
      <div className={`group-list ${target ? "has-target" : ""}`}>
        <div className="tabs group-tabs">
          {VIEWS.map((v) => (
            <button
              key={v.key}
              className={view === v.key ? "on" : ""}
              onClick={() => {
                setView(v.key);
                setEndAsk(null);
              }}
            >
              {v.label}
            </button>
          ))}
        </div>
        <input className="group-search" placeholder="搜索群名" value={search} onChange={(e) => setSearch(e.target.value)} />
        {error && !target && <p className="error pad">加载失败：{error}</p>}
        {shown.length === 0 && (
          <p className="empty pad">
            {view === "todo" ? "没有待处理的群 🎉" : view === "escalated" ? "没有转人工的消息" : "暂无群聊"}
          </p>
        )}
        <ul>
          {shown.map((g) => {
            const k = groupKey(g);
            const left = g.pending_count + g.escalated_count;
            const asking = endAsk === k;
            return (
              <li
                key={k}
                className={`${sameGroup(target, g) ? "on" : ""} ${asking ? "asking" : ""}`}
                onClick={() => !sameGroup(target, g) && setTarget({ channel: g.channel, group_id: g.group_id })}
              >
                <span className={`g-avatar ${g.channel}`} aria-hidden>
                  {(g.group_name ?? "群").trim().slice(0, 1)}
                </span>
                <div className="g-main">
                  <div className="g-top">
                    <span className={`g-name ${g.group_name ? "" : "unnamed"}`}>{groupLabel(g.group_name, g.group_id)}</span>
                    <time>{shortTime(g.last_message_at)}</time>
                    {left > 0 && !asking && (
                      <button
                        className="g-end"
                        title="结束会话"
                        disabled={busy}
                        onClick={(e) => {
                          e.stopPropagation();
                          setEndAsk(k);
                        }}
                      >
                        结束
                      </button>
                    )}
                  </div>
                  <div className="g-bottom">
                    <span className={`tag ${g.channel}`}>{CHANNEL_LABEL[g.channel]}</span>
                    {g.is_external_group && <span className="ext">客户群</span>}
                    <span className="g-preview">{g.last_content}</span>
                    {view === "escalated"
                      ? g.escalated_count > 0 && <span className="g-badge warn">{g.escalated_count}</span>
                      : g.pending_count > 0 && <span className="g-badge" title="待处理">{g.pending_count}</span>}
                  </div>
                  {asking && (
                    <div className="g-confirm" onClick={(e) => e.stopPropagation()}>
                      <span>还有 {left} 条没处理完，结束后不再提醒</span>
                      <button className="primary" disabled={busy} onClick={() => endGroup(g)}>结束会话</button>
                      <button disabled={busy} onClick={() => setEndAsk(null)}>取消</button>
                    </div>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      </div>
      <section className="chat-pane">
        {!target && <p className="empty pad">在左边选一个群，查看聊天记录并回复</p>}
        {target && (
          <>
            <div className="chat-head">
              <button className="link back" onClick={() => setTarget(null)}>‹ 群列表</button>
              {nameDraft === null ? (
                <>
                  <b className={groupName ? "" : "unnamed"}>{groupName ?? "未命名的群"}</b>
                  <button
                    className="link name-btn"
                    title="照企业微信里显示的群名填写，自动发送靠它找群"
                    onClick={() => setNameDraft(groupName ?? "")}
                  >
                    {groupName ? "改群名" : "填写群名"}
                  </button>
                </>
              ) : (
                <form
                  className="name-edit"
                  onSubmit={(e) => {
                    e.preventDefault();
                    saveName();
                  }}
                >
                  <input
                    autoFocus
                    value={nameDraft}
                    maxLength={256}
                    placeholder="和企业微信里显示的群名一字不差"
                    onChange={(e) => setNameDraft(e.target.value)}
                    onKeyDown={(e) => e.key === "Escape" && setNameDraft(null)}
                  />
                  <button type="submit" className="primary" disabled={busy || !nameDraft.trim()}>保存</button>
                  <button type="button" className="link" onClick={() => setNameDraft(null)}>取消</button>
                </form>
              )}
              <span className={`tag ${target.channel}`}>{CHANNEL_LABEL[target.channel]}</span>
              {current && <span className="msg-count">共 {current.message_count} 条消息</span>}
              <button
                className="end-btn"
                disabled={busy || openCount === 0 || headAsk}
                title={openCount ? "结束这个会话" : "没有待处理的消息"}
                onClick={() => setHeadAsk(true)}
              >
                结束会话
              </button>
            </div>
            {headAsk && (
              <div className="confirm-send end-confirm">
                <span>
                  {openCount > 0 ? `还有 ${openCount} 条没处理完，结束后不再提醒` : "结束这个会话"}
                  {unsent > 0 && `，其中没发出去的 ${unsent} 条回复不再发送`}；客户再发消息会重新回到待处理。
                </span>
                <button className="primary" disabled={busy} onClick={() => endGroup(target)}>结束会话</button>
                <button disabled={busy} onClick={() => setHeadAsk(false)}>取消</button>
              </div>
            )}
            <div
              className="chat-list"
              ref={listRef}
              onScroll={(e) => {
                const el = e.currentTarget;
                stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 60;
              }}
              onLoadCapture={() => {
                const el = listRef.current;
                if (el && stick.current) el.scrollTop = el.scrollHeight;
              }}
            >
              {error && <p className="error">加载失败：{error}</p>}
              {loading && <p className="empty pad">加载中…</p>}
              {moreBefore && <button className="load-more" onClick={loadOlder}>加载更早的消息</button>}
              {!loading && !moreBefore && items.length > 0 && <p className="chat-start">已经是最早的消息</p>}
              {endsAt.before.map((e) => (
                <EndSep key={e.id} end={e} />
              ))}
              {items.map(({ message: m, suggestion }, i) => (
                <div key={m.id}>
                  {(i === 0 || day(items[i - 1].message.sent_at) !== day(m.sent_at)) && (
                    <div className="day-sep">{day(m.sent_at)}</div>
                  )}
                  <div
                    id={`m-${m.id}`}
                    className={`bubble-row ${m.from_customer ? "cust" : "staff"} ${focus?.message.id === m.id ? "focused" : ""}`}
                  >
                    <div className="who">
                      {customerLabel(m)}
                      <time>{hm(m.sent_at)}</time>
                    </div>
                    <MessageBody message={m} onChanged={() => refresh()} />
                    {suggestion && (
                      <SugChip s={suggestion} focused={focus?.suggestion?.id === suggestion.id} onFocus={() => setFocusId(suggestion.id)} />
                    )}
                  </div>
                  {endsAt.after.get(i)?.map((e) => (
                    <EndSep key={e.id} end={e} />
                  ))}
                </div>
              ))}
              {moreAfter && <button className="load-more" onClick={loadNewer}>加载更新的消息</button>}
              {!moreAfter &&
                outbox.map((t) => <OutboxBubble key={t.id} task={t} busy={busy} onAction={(k) => taskAction(t, k)} />)}
            </div>
            <Composer
              key={`${target.channel}:${target.group_id}`}
              channel={target.channel}
              groupId={target.group_id}
              groupName={groupName}
              canAutoSend={canAutoSend(target.channel)}
              focus={focus}
              focusIndex={shownIndex}
              openTotal={open.length}
              onFocusStep={(d) => {
                const next = open[shownIndex + d];
                if (next) {
                  setFocusId(next.suggestion!.id);
                  document.getElementById(`m-${next.message.id}`)?.scrollIntoView({ block: "center", behavior: "smooth" });
                }
              }}
              onChanged={changed}
              notify={notify}
            />
          </>
        )}
      </section>
    </div>
  );
}
