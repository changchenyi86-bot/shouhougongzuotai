import { useCallback, useEffect, useState } from "react";
import {
  MISSED_LABEL,
  fetchGaps,
  type CandidateStatus,
  type CandidateView,
  type Gap,
  type GapState,
  type GapView,
} from "./api";

const FILTERS: { key: GapView; label: string }[] = [
  { key: "todo", label: "待补" },
  { key: "filled", label: "已补上" },
  { key: "skipped", label: "不用补" },
  { key: "all", label: "全部" },
];
const STATE_LABEL: Record<GapState, string> = { todo: "待补", filled: "已补上", skipped: "不用补" };

/** 整理成的候选知识走到哪一步了，能去哪个页签处理 */
const CANDIDATE_STEP: Record<CandidateStatus, { text: string; view?: CandidateView }> = {
  proposed: { text: "AI 已整理成问答，等回复的人确认", view: "proposed" },
  pending: { text: "已进候选知识，等同事审核", view: "pending" },
  approved: { text: "已补进知识库，之后同类问题 AI 会参考", view: "approved" },
  rejected: { text: "整理的问答没通过审核，还要再补", view: "rejected" },
  retired: { text: "补的知识已停用，还要再补", view: "rejected" },
  declined: { text: "回复的人觉得不用沉淀", view: "skipped" },
  not_general: { text: "AI 判断是这个客户的个案，不用补", view: "skipped" },
};

function when(iso: string) {
  const d = new Date(iso);
  return `${d.getMonth() + 1}月${d.getDate()}日 ${d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
}

function replyText(g: Gap) {
  if (g.final_content) return null;
  if (g.status === "dismissed") return "标为无需回复";
  if (g.status === "closed") return "会话结束时还没回复";
  return "还没回复";
}

function step(g: Gap): { text: string; view?: CandidateView } {
  if (g.candidate) return CANDIDATE_STEP[g.candidate.status];
  if (g.status === "dismissed") return { text: "标为无需回复，不用补" };
  if (!g.final_content) return { text: "回复后 AI 会把答案整理成问答" };
  return { text: "还没整理成问答" };
}

function GapCard({ g, me, onOpen }: { g: Gap; me: string; onOpen: (view: CandidateView) => void }) {
  const next = step(g);
  // 「待我确认」只列自己回复的：别人回复的由他确认
  const canOpen = next.view && g.state !== "filled" && (next.view !== "proposed" || g.handled_by === me);
  const knowledge = g.knowledge_problem
    ? g.knowledge_problem
    : g.knowledge_found
      ? `查到 ${g.knowledge_found} 条资料，用上 ${g.knowledge_cited} 条`
      : "知识库里没查到相关资料";
  const pending = replyText(g);
  return (
    <article className={`cand gap ${g.state}`}>
      <header className="cand-head">
        <span className="gap-why">{MISSED_LABEL[g.missed]}</span>
        <span className="cand-meta">
          {g.missed_by ?? "技术支持"} · {g.group_name ?? "客户群"} · {when(g.missed_at)}
        </span>
        <span className={`gap-state ${g.state}`}>{STATE_LABEL[g.state]}</span>
      </header>

      <div className="cand-body">
        <div className="cand-main">
          <label>
            <span>客户问的</span>
            <p className="cand-q">{g.customer_text}</p>
          </label>
          {g.question && g.question !== g.customer_text && (
            <label>
              <span>AI 理解的问题</span>
              <p className="cand-a">{g.question}</p>
            </label>
          )}
          <label>
            <span>实际回复{g.handled_by ? `（${g.handled_by}）` : ""}</span>
            {pending ? <p className="cand-a muted">{pending}</p> : <p className="cand-a">{g.final_content}</p>}
          </label>
        </div>
        <div className="cand-orig">
          <div>
            <span>AI 当时的建议</span>
            <p>{g.ai_suggestion || "AI 没写出建议"}</p>
          </div>
          <div>
            <span>知识库</span>
            <p>{knowledge}</p>
          </div>
          {g.note && (
            <div>
              <span>备注</span>
              <p>{g.note}</p>
            </div>
          )}
        </div>
      </div>

      <footer className="cand-foot">
        <span className="cand-by">{next.text}</span>
        {canOpen && <button className="plain" onClick={() => onOpen(next.view!)}>去处理</button>}
      </footer>
    </article>
  );
}

/** 待补知识：AI 没答上的问题。客服大改或没用 AI 建议、转给同事求助时自动记进来，整理成的知识审核通过就算补上了。 */
export default function Gaps({ me, onOpen }: { me: string; onOpen: (view: CandidateView) => void }) {
  const [view, setView] = useState<GapView>("todo");
  const [items, setItems] = useState<Gap[]>([]);
  const [counts, setCounts] = useState<Partial<Record<GapState, number>>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchGaps(view)
      .then((r) => {
        setItems(r.items);
        setCounts(r.counts);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
  }, [view]);

  useEffect(() => {
    setLoading(true);
    setItems([]);
    load();
    const t = setInterval(load, 20_000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <>
      <div className="gap-filters">
        <div className="subtabs">
          {FILTERS.map((f) => (
            <button key={f.key} className={view === f.key ? "on" : ""} onClick={() => setView(f.key)}>
              {f.label}
              {f.key !== "all" && counts[f.key] !== undefined && <small> {counts[f.key]}</small>}
            </button>
          ))}
        </div>
      </div>
      <div className="cand-list">
        {error && <p className="error pad">加载失败：{error}</p>}
        {loading && <p className="empty pad">加载中…</p>}
        {!loading && !error && items.length === 0 && (
          <p className="empty pad">
            {view === "todo"
              ? "没有待补的。客服大改或没用 AI 建议、把会话转给同事求助时，这一轮会记到这里"
              : view === "filled"
                ? "还没有补上的"
                : view === "skipped"
                  ? "没有不用补的"
                  : "AI 还没有答不上的问题"}
          </p>
        )}
        {items.map((g) => (
          <GapCard key={g.suggestion_id} g={g} me={me} onOpen={onOpen} />
        ))}
      </div>
    </>
  );
}
