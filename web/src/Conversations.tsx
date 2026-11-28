import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  CHANNEL_LABEL,
  MISSED_LABEL,
  assignConversation,
  cancelTask,
  dismissTask,
  endConversation,
  fetchConversation,
  fetchCustomerCard,
  fetchGroups,
  fetchUpdates,
  fetchStaff,
  reopen,
  resendTask,
  setGroupName,
  type Attachment,
  type Channel,
  type ConversationEnd,
  type CustomerCard as CustomerCardData,
  type Group,
  type GroupView,
  type InboxItem,
  type Message,
  type SendTask,
  type Staff,
  type StaffBrief,
  type Suggestion,
} from "./api";
import Composer from "./Composer";
import CustomerCard from "./CustomerCard";
import { ImageGallery } from "./ImageViewer";
import MessageBody, { AttachmentView, isImage } from "./MessageBody";
import { Avatar, SenderLine, senderName } from "./Sender";
import ReflowTip from "./ReflowTip";
import { MentionText } from "./Mentions";

export interface ChatTarget {
  channel: Channel;
  group_id: string;
}

interface Props {
  me: Staff;
  canAutoSend: (channel: Channel) => boolean;
  notify: (text: string) => void;
  /** 打开知识库页（候选知识） */
  onOpenKnowledge: () => void;
  /** 候选知识有变化（刷新知识库角标） */
  onKnowledgeChanged: () => void;
}

const VIEWS: { key: GroupView; label: string; admin?: boolean }[] = [
  { key: "todo", label: "待处理" },
  { key: "unassigned", label: "待分配", admin: true },
  { key: "all", label: "全部" },
];

/** 这个群当前由谁负责、谁在处理 */
interface Who {
  owner: StaffBrief | null;
  assignee: StaffBrief | null;
  open: boolean;
}
const NOBODY: Who = { owner: null, assignee: null, open: false };
const whoOf = (c: { owner: StaffBrief | null; assignee: StaffBrief | null; conversation_open: boolean }): Who => ({
  owner: c.owner,
  assignee: c.assignee,
  open: c.conversation_open,
});
const OPEN: Suggestion["status"][] = ["generating", "ready", "failed"];

// 客户档案展开还是收起，记在各人自己的浏览器里；没记过时，宽屏默认展开
const CARD_KEY = "customerCard";
function loadCardOpen(): boolean {
  try {
    const v = localStorage.getItem(CARD_KEY);
    if (v) return v === "open";
  } catch {
    /* 浏览器禁用存储时忽略 */
  }
  return window.innerWidth >= 1440;
}
function saveCardOpen(open: boolean) {
  try {
    localStorage.setItem(CARD_KEY, open ? "open" : "closed");
  } catch {
    /* 同上 */
  }
}

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

const CANDIDATE_TAG: Partial<Record<string, string>> = { pending: "已进候选知识", approved: "已沉淀为知识" };

/** 并入的建议可能又并进了更后面的一条：顺着找到最终那条（找不到就是没加载到） */
function mergedTarget(s: Suggestion, byId: Map<string, Suggestion>): Suggestion | null {
  let t = s.merged_into ? byId.get(s.merged_into) ?? null : null;
  for (let i = 0; t && t.status === "merged" && t.merged_into && i < 10; i++) t = byId.get(t.merged_into) ?? null;
  return t && t.status !== "merged" ? t : null;
}

function MergedChip({ target, focused, onFocus }: { target: Suggestion | null; focused: boolean; onFocus: () => void }) {
  const t = target;
  if (t && (t.status === "adopted" || t.status === "edited"))
    return (
      <span className="chip-status ok" title={t.final_content ?? undefined}>
        已一并回复{t.handled_by ? ` · ${t.handled_by}` : ""}
      </span>
    );
  if (t && t.status === "dismissed") return <span className="chip-status">已一并标为无需回复</span>;
  if (t && t.status === "closed") return <span className="chip-status">会话已结束</span>;
  const tip = "客户接着又说了话，AI 把这几条合在一起写了一条建议";
  if (!t) return <span className="chip-status merged" title={tip}>已并入后面的建议</span>;
  return (
    <button className={`chip-status merged ${focused ? "on" : ""}`} title={tip} onClick={onFocus}>
      已并入下面的建议
    </button>
  );
}

function SugChip({ s, focused, onFocus, onReopen }: { s: Suggestion; focused: boolean; onFocus: () => void; onReopen: () => void }) {
  if (s.status === "adopted" || s.status === "edited") {
    const tag = s.candidate && CANDIDATE_TAG[s.candidate.status];
    return (
      <span className="chip-line">
        <span className="chip-status ok" title={s.final_content ?? undefined}>已回复{s.handled_by ? ` · ${s.handled_by}` : ""}</span>
        {s.missed && (
          <span className="chip-status miss" title={`${MISSED_LABEL[s.missed]}，已进知识库的「待补知识」`}>
            AI 没答上
          </span>
        )}
        {tag && <span className="chip-status kn" title={s.candidate!.question}>{tag}</span>}
      </span>
    );
  }
  if (s.status === "dismissed" && s.auto_dismissed)
    return (
      <span className="chip-status auto" title={s.reason ?? undefined}>
        AI 判断无需回复
        <button className="link" onClick={onReopen}>要回复</button>
      </span>
    );
  if (s.status === "dismissed") return <span className="chip-status">无需回复</span>;
  if (s.status === "closed")
    return (
      <span className="chip-status" title={`${s.handled_by ?? ""}结束会话时这条还没回复`}>
        会话已结束
      </span>
    );
  const label = { generating: "AI 生成中…", ready: "AI 建议待处理", failed: "AI 建议生成失败" }[
    s.status as "generating" | "ready" | "failed"
  ];
  return (
    <button className={`chip-status open ${s.status} ${focused ? "on" : ""}`} onClick={onFocus}>
      {label}
    </button>
  );
}

function WhoChip({ who, me }: { who: Who; me: Staff }) {
  const { owner, assignee } = who;
  const ownerText = owner ? `负责人 ${owner.id === me.id ? "是我" : owner.name}` : "这个群还没有负责人";
  if (who.open && !assignee) return <span className="owner-chip warn" title={ownerText}>待分配</span>;
  if (who.open && assignee) {
    const text =
      assignee.id === me.id
        ? owner && owner.id !== me.id
          ? `替${owner.name}处理`
          : "我在处理"
        : `${assignee.name} 处理中`;
    return <span className="owner-chip" title={ownerText}>{text}</span>;
  }
  return <span className="owner-chip">{owner ? ownerText : "没有负责人"}</span>;
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
      <Avatar name={task.created_by} customer={false} />
      <div className="msg-col">
        <SenderLine name={task.created_by ?? "工作台"} kind="internal" time={hm(task.created_at)} />
        {task.attachments.map((a) => (
          <AttachmentView key={a.id} att={a} />
        ))}
        {task.content && (
          <div className="bubble">
            <MentionText text={task.content} mentions={task.mentions} />
          </div>
        )}
        <div className="outbox-status">
          {status}
          {task.status === "failed" && task.error && <span className="send-error">{task.error}</span>}
          {task.status !== "failed" && task.warning && <span className="send-warn">{task.warning}</span>}
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
    </div>
  );
}

export default function Conversations({ me, canAutoSend, notify, onOpenKnowledge, onKnowledgeChanged }: Props) {
  const admin = me.role === "admin";
  const [view, setView] = useState<GroupView>("todo");
  const [groups, setGroups] = useState<Group[]>([]);
  const [search, setSearch] = useState("");
  const [target, setTarget] = useState<ChatTarget | null>(null);
  const [items, setItems] = useState<InboxItem[]>([]);
  const [outbox, setOutbox] = useState<SendTask[]>([]);
  const [ends, setEnds] = useState<ConversationEnd[]>([]);
  const [who, setWho] = useState<Who>(NOBODY);
  const [transferOpen, setTransferOpen] = useState(false);
  const [colleagues, setColleagues] = useState<Staff[]>([]);
  const [remind, setRemind] = useState<number | null>(null); // 分给我、还在等回复的群数
  const transferBox = useRef<HTMLDivElement>(null);
  const [endAsk, setEndAsk] = useState<string | null>(null); // 群列表里正在确认结束的群
  const [headAsk, setHeadAsk] = useState(false); // 会话顶部正在确认结束
  const [moreBefore, setMoreBefore] = useState(false);
  const [moreAfter, setMoreAfter] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [focusId, setFocusId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [nameDraft, setNameDraft] = useState<string | null>(null); // 正在填写的群名
  const [card, setCard] = useState<CustomerCardData | null>(null);
  const [cardError, setCardError] = useState<string | null>(null);
  const [cardOpen, setCardOpenState] = useState(loadCardOpen);
  const setCardOpen = (open: boolean) => {
    setCardOpenState(open);
    saveCardOpen(open);
  };
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
    setTransferOpen(false);
    if (!target) return;
    setItems([]);
    setOutbox([]);
    setEnds([]);
    setWho(NOBODY);
    setFocusId(null);
    setLoading(true);
    fetchConversation(target.channel, target.group_id)
      .then((c) => {
        scrollPlan.current = { kind: "bottom" };
        setItems(c.items);
        setOutbox(c.outbox);
        setEnds(c.ends);
        setWho(whoOf(c));
        setMoreBefore(c.has_more_before);
        setMoreAfter(c.has_more_after);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [target]);

  // 客户档案：换群时加载；同步一小时一次，开着的群每 5 分钟重新取一次
  const cardFor = useRef<ChatTarget | null>(null); // 档案请求回来时已经换了群，就不用它
  const loadCard = useCallback(() => {
    cardFor.current = target;
    if (!target) return;
    fetchCustomerCard(target.channel, target.group_id)
      .then((c) => {
        if (cardFor.current !== target) return;
        setCard(c);
        setCardError(null);
      })
      .catch((e: Error) => cardFor.current === target && setCardError(e.message));
  }, [target]);

  useEffect(() => {
    setCard(null);
    setCardError(null);
    loadCard();
    const t = setInterval(loadCard, 300_000);
    return () => clearInterval(t);
  }, [loadCard]);

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
        setWho(whoOf(c));
        if (!moreAfter) setMoreAfter(c.has_more_after);
      } catch (e) {
        // 会话被转给了别人，这个群已经不归我了
        if ((e as Error).message === "这个群没有分给你") {
          setTarget(null);
          notify("这个群已经转给别人处理了");
        }
        /* 其他错误：下次再刷新 */
      }
    },
    [target, items, moreAfter, outbox.length, notify],
  );

  // 发出去了、但有人没 @ 上：提示一次（气泡上也一直写着，直到它出现在聊天记录里）
  const warned = useRef(new Set<string>());
  useEffect(() => {
    for (const t of outbox) {
      if (t.status !== "sent" || !t.warning || warned.current.has(t.id)) continue;
      warned.current.add(t.id);
      if (t.finished_at && Date.now() - new Date(t.finished_at).getTime() < 60_000) notify(`已发出。${t.warning}`);
    }
  }, [outbox, notify]);

  const sending = outbox.some((t) => t.status === "queued" || t.status === "sending");
  useEffect(() => {
    // 有消息正在发送时每秒看一次结果，发完马上显示「已发出」
    const t = setInterval(() => refresh(), sending ? 1_000 : 10_000);
    return () => clearInterval(t);
  }, [refresh, sending]);

  // 每 2 秒问一次后台有没有新变化，有就马上刷新群列表和当前群，新消息几秒内就能看到。
  // 上面的定时刷新保留作兜底；页面切到后台时不问，切回来立刻问一次。
  const latest = useRef({ refresh, loadGroups });
  latest.current = { refresh, loadGroups };
  useEffect(() => {
    let version: string | null = null;
    let stopped = false;
    const check = () => {
      if (stopped || document.hidden) return;
      fetchUpdates()
        .then(({ version: v }) => {
          if (stopped) return;
          if (version !== null && v !== version) {
            latest.current.loadGroups();
            latest.current.refresh();
          }
          version = v;
        })
        .catch(() => {
          /* 下次再问 */
        });
    };
    check();
    const t = setInterval(check, 2_000);
    document.addEventListener("visibilitychange", check);
    return () => {
      stopped = true;
      clearInterval(t);
      document.removeEventListener("visibilitychange", check);
    };
  }, []);

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
      // 替别人处理的群，结束后就不归我了
      const g2 = groups.find((x) => sameGroup(x, g));
      const mine = admin || g2?.owner?.id === me.id || (sameGroup(target, g) && who.owner?.id === me.id);
      if (sameGroup(target, g)) {
        if (mine) refresh(true);
        else setTarget(null);
      }
    } catch (e) {
      notify(`结束失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
      loadGroups();
    }
  };

  // 转给同事：打开时取最新的成员在线情况和手上会话数
  const openTransfer = () => {
    setTransferOpen(true);
    fetchStaff()
      .then(setColleagues)
      .catch((e: Error) => notify(`加载同事失败：${e.message}`));
  };

  const transfer = async (to: Staff) => {
    if (!target) return;
    setBusy(true);
    try {
      await assignConversation(target.channel, target.group_id, to.id);
      notify(to.id === me.id ? "这次会话由你处理" : `已转给${to.name}`);
      setTransferOpen(false);
      if (admin || who.owner?.id === me.id || to.id === me.id) refresh();
      else setTarget(null); // 转走以后就不归我了
      loadGroups();
    } catch (e) {
      notify(`转交失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!transferOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!transferBox.current?.contains(e.target as Node)) setTransferOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setTransferOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [transferOpen]);

  // 打开工作台、或从「离开」切回「在线」时，提醒分给我、还在等回复的群（包括我不在时分来的）
  useEffect(() => {
    if (me.status !== "online") return;
    fetchGroups("todo")
      .then((g) => setRemind(g.filter((x) => x.assignee?.id === me.id).length || null))
      .catch(() => {});
  }, [me.status, me.id]);

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

  const reopenOne = async (s: Suggestion) => {
    setBusy(true);
    try {
      await reopen(s.id);
      setFocusId(s.id);
      changed();
    } catch (e) {
      notify(`操作失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  // 我刚回复的、AI 建议沉淀成知识的：在输入框上方提示一次
  const proposal = useMemo(
    () =>
      [...items].reverse().find((i) => i.suggestion?.candidate?.status === "proposed" && i.suggestion.handled_by === me.name) ?? null,
    [items, me.name],
  );

  // 待回复的客户消息：默认处理最新的一条，点气泡上的标签可以换
  const open = useMemo(() => items.filter((i) => i.suggestion && OPEN.includes(i.suggestion.status)), [items]);
  // 这个群聊天记录里的全部图片（含正在发的），看大图时可以左右切换
  const chatImages = useMemo(
    () =>
      [...items.map((i) => i.message.attachment), ...(moreAfter ? [] : outbox.flatMap((t) => t.attachments))].filter(
        (a): a is Attachment => !!a && isImage(a),
      ),
    [items, outbox, moreAfter],
  );
  const focus = open.find((i) => i.suggestion!.id === focusId) ?? open[open.length - 1] ?? null;
  const shownIndex = focus ? open.indexOf(focus) : 0;
  // 一条建议可能回同一轮的好几条客户消息（连发、几个人各说一句），这几条一起高亮
  const sugById = useMemo(
    () => new Map(items.flatMap((i) => (i.suggestion ? [[i.suggestion.id, i.suggestion] as const] : []))),
    [items],
  );
  const focusRound = useMemo(() => {
    if (!focus) return [];
    const ids = focus.suggestion?.covers?.length ? focus.suggestion.covers : [focus.message.id];
    const found = ids.map((id) => items.find((i) => i.message.id === id)?.message).filter((m): m is Message => !!m);
    return found.length ? found : [focus.message];
  }, [focus, items]);
  const roundIds = useMemo(() => new Set(focusRound.map((m) => m.id)), [focusRound]);

  const shown = groups.filter((g) => !search.trim() || groupLabel(g.group_name, g.group_id).includes(search.trim()));
  const current = target ? groups.find((g) => sameGroup(target, g)) : undefined;
  const groupName =
    current?.group_name ?? [...items].reverse().find((i) => i.message.group_name)?.message.group_name ?? null;

  // 还没处理完的：没回复的客户消息、没发出去的回复。群列表里的数是全群的，聊天里只算已加载的
  const unsent = outbox.filter((t) => t.status === "failed" || t.status === "cancelled").length;
  const openCount = Math.max(current ? current.pending_count : 0, open.length + unsent);
  const canTransfer = who.open && (admin || who.assignee?.id === me.id || who.owner?.id === me.id);
  const ranked = colleagues
    .filter((s) => s.active && s.id !== who.assignee?.id)
    .sort((a, b) => Number(b.online) - Number(a.online) || a.open_count - b.open_count);

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
    <div className={`chat-layout ${target && cardOpen ? "with-card" : ""}`}>
      <div className={`group-list ${target ? "has-target" : ""}`}>
        <div className="tabs group-tabs">
          {VIEWS.filter((v) => admin || !v.admin).map((v) => (
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
        {remind && (
          <div className="remind-bar">
            <span>有 {remind} 个群分给了你，还在等回复</span>
            <button className="link" aria-label="知道了" onClick={() => setRemind(null)}>知道了</button>
          </div>
        )}
        <input className="group-search" placeholder="搜索群名" value={search} onChange={(e) => setSearch(e.target.value)} />
        {error && !target && <p className="error pad">加载失败：{error}</p>}
        {shown.length === 0 && (
          <p className="empty pad">
            {view === "todo"
              ? "没有待处理的群 🎉"
              : view === "unassigned"
                ? "没有等着分配的会话"
                : admin
                  ? "暂无群聊"
                  : "还没有分给你的群"}
          </p>
        )}
        <ul>
          {shown.map((g) => {
            const k = groupKey(g);
            const left = g.pending_count;
            const asking = endAsk === k;
            // 管理员看谁在处理；其他人只在替别人处理时标出来
            const chip = admin
              ? g.conversation_open
                ? g.assignee
                  ? <span className="who-chip">{g.assignee.name}</span>
                  : <span className="who-chip warn">待分配</span>
                : null
              : g.assignee?.id === me.id && g.owner && g.owner.id !== me.id
                ? <span className="who-chip">替{g.owner.name}</span>
                : null;
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
                    {chip}
                    <time>{shortTime(g.last_message_at)}</time>
                    {(left > 0 || g.conversation_open) && !asking && (
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
                    {g.pending_count > 0 && <span className="g-badge" title="待处理">{g.pending_count}</span>}
                  </div>
                  {asking && (
                    <div className="g-confirm" onClick={(e) => e.stopPropagation()}>
                      <span>{left > 0 ? `还有 ${left} 条没处理完，结束后不再提醒` : "消息都处理完了，结束这次会话？"}</span>
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
              {!cardOpen && (
                <button className="cust-chip" title="打开客户档案" onClick={() => setCardOpen(true)}>
                  {card?.profile?.name ?? card?.link.customer ?? "客户档案"}
                  {card?.profile?.level && card.profile.level !== "普通" && <span className="lvl imp">{card.profile.level}</span>}
                  {card && card.alerts.length > 0 && <span className="cust-dot" title={card.alerts.join("；")} />}
                </button>
              )}
              <WhoChip who={who} me={me} />
              <div className="head-actions" ref={transferBox}>
                {canTransfer && (
                  <button disabled={busy} aria-expanded={transferOpen} onClick={() => (transferOpen ? setTransferOpen(false) : openTransfer())}>
                    {who.assignee ? "转给同事" : "分给…"}
                  </button>
                )}
                <button
                  disabled={busy || (openCount === 0 && !who.open) || headAsk}
                  title={openCount || who.open ? "结束这个会话" : "没有进行中的会话"}
                  onClick={() => setHeadAsk(true)}
                >
                  结束会话
                </button>
                {transferOpen && (
                  <div className="transfer-pop" role="dialog" aria-label="转给同事">
                    <p className="hint">
                      {who.assignee
                        ? "转过去以后由对方处理这次会话，对方的待处理里会出现这个群。还没回复的消息会记为 AI 没答上。"
                        : "这次会话还没分给人，选一个人处理。"}
                    </p>
                    {ranked.length === 0 && <p className="hint">{colleagues.length ? "没有可以转的同事" : "加载中…"}</p>}
                    <ul className="people">
                      {ranked.map((s) => (
                        <li key={s.id}>
                          <button disabled={busy} onClick={() => transfer(s)}>
                            <span className="ava s">
                              {s.name.slice(0, 1)}
                              <i className={`st-dot ${s.online ? "online" : s.status === "away" ? "away" : "off"}`} />
                            </span>
                            <span className="nm">
                              {s.name}
                              {s.id === me.id && <small>（我）</small>}
                            </span>
                            {s.team && <span className="team-tag">{s.team.name}</span>}
                            <span className="meta">
                              {s.online ? "在线" : s.status === "away" ? "离开" : "不在线"} · 手上 {s.open_count} 个
                            </span>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>
            </div>
            {headAsk && (
              <div className="confirm-send end-confirm">
                <span>
                  {openCount > 0 ? `还有 ${openCount} 条没处理完，结束后不再提醒` : "消息都处理完了，结束这次会话"}
                  {unsent > 0 && `，其中没发出去的 ${unsent} 条回复不再发送`}；客户再发消息会重新回到待处理。
                </span>
                <button className="primary" disabled={busy} onClick={() => endGroup(target)}>结束会话</button>
                <button disabled={busy} onClick={() => setHeadAsk(false)}>取消</button>
              </div>
            )}
            <ImageGallery images={chatImages}>
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
                      className={`bubble-row ${m.from_customer ? "cust" : "staff"} ${roundIds.has(m.id) ? "focused" : ""}`}
                    >
                      <Avatar name={m.sender_name} customer={m.from_customer} />
                      <div className="msg-col">
                        <SenderLine name={senderName(m)} kind={m.sender_kind} time={hm(m.sent_at)} />
                        <MessageBody message={m} onChanged={() => refresh()} />
                        {suggestion?.status === "merged" ? (
                          <MergedChip
                            target={mergedTarget(suggestion, sugById)}
                            focused={!!focus && roundIds.has(m.id)}
                            onFocus={() => {
                              const t = mergedTarget(suggestion, sugById);
                              if (t) setFocusId(t.id);
                            }}
                          />
                        ) : suggestion && (
                          <SugChip
                            s={suggestion}
                            focused={focus?.suggestion?.id === suggestion.id}
                            onFocus={() => setFocusId(suggestion.id)}
                            onReopen={() => !busy && reopenOne(suggestion)}
                          />
                        )}
                      </div>
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
            </ImageGallery>
            {proposal && (
              <ReflowTip
                key={proposal.suggestion!.candidate!.id}
                item={proposal}
                onDone={() => {
                  refresh();
                  onKnowledgeChanged();
                }}
                onOpenKnowledge={onOpenKnowledge}
                notify={notify}
              />
            )}
            <Composer
              key={`${target.channel}:${target.group_id}`}
              channel={target.channel}
              groupId={target.group_id}
              groupName={groupName}
              canAutoSend={canAutoSend(target.channel)}
              focus={focus}
              round={focusRound}
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
              alerts={card?.alerts}
            />
          </>
        )}
      </section>
      {target && cardOpen && (
        <CustomerCard
          target={target}
          card={card}
          error={cardError}
          me={me}
          onClose={() => setCardOpen(false)}
          onChanged={loadCard}
          notify={notify}
        />
      )}
    </div>
  );
}
