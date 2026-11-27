import { useCallback, useEffect, useRef, useState } from "react";
import {
  candidateAction,
  fetchCandidateCounts,
  fetchCandidates,
  KIND_LABEL,
  type Branch,
  type Candidate,
  type CandidateEdit,
  type CandidateView,
  type KnowledgeKind,
} from "./api";

const VIEWS: { key: CandidateView; label: string }[] = [
  { key: "proposed", label: "待我确认" },
  { key: "pending", label: "待审核" },
  { key: "approved", label: "已通过" },
  { key: "rejected", label: "未通过" },
  { key: "skipped", label: "不收录" },
];
const BRANCHES: Branch[] = ["有数", "知数", "通用"];

function when(iso: string | null) {
  if (!iso) return "";
  const d = new Date(iso);
  return `${d.getMonth() + 1}月${d.getDate()}日 ${d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" })}`;
}

interface CardProps {
  c: Candidate;
  me: string;
  onChanged: () => void;
  notify: (text: string) => void;
}

function syncText(c: Candidate) {
  if (c.status !== "approved") return null;
  if (!c.card_id) return "知数的纠错卡暂不支持，只在工作台里用";
  if (c.synced_status === "confirmed") return `已写入售后数字人（${when(c.synced_at)}）`;
  return "工作台已在用，等待写入售后数字人";
}

function CandidateCard({ c, me, onChanged, notify }: CardProps) {
  const own = me === c.author || me === c.confirmed_by;
  // 提交后由其他同事审核时改；自己提交的等审核时不能再改
  const editable = ["proposed", "not_general", "declined"].includes(c.status) || (c.status === "pending" && !own);
  const [draft, setDraft] = useState<Required<Omit<CandidateEdit, "note">>>({
    title: c.title,
    question: c.question,
    answer: c.answer,
    branch: c.branch,
    kind: c.kind,
    module: c.module,
  });
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [askRetire, setAskRetire] = useState(false);

  const act = async (action: "confirm" | "decline" | "approve" | "reject" | "retire", done: string) => {
    setBusy(true);
    try {
      await candidateAction(c.id, action, editable ? { ...draft, note } : { note });
      notify(done);
      onChanged();
    } catch (e) {
      notify(`操作失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  const set = <K extends keyof typeof draft>(k: K, v: (typeof draft)[K]) => setDraft((d) => ({ ...d, [k]: v }));
  const filled = !!draft.question.trim() && !!draft.answer.trim();

  return (
    <article className={`cand ${c.status}`}>
      <header className="cand-head">
        {editable ? (
          <>
            <select value={draft.branch} onChange={(e) => set("branch", e.target.value as Branch)} aria-label="适用分支">
              {BRANCHES.map((b) => (
                <option key={b}>{b}</option>
              ))}
            </select>
            <select value={draft.kind} onChange={(e) => set("kind", e.target.value as KnowledgeKind)} aria-label="类型">
              {(Object.keys(KIND_LABEL) as KnowledgeKind[]).map((k) => (
                <option key={k} value={k}>{KIND_LABEL[k]}</option>
              ))}
            </select>
          </>
        ) : (
          <>
            <span className="cand-branch">{c.branch}</span>
            <span className="cand-kind">{KIND_LABEL[c.kind] ?? c.kind}</span>
          </>
        )}
        {c.module && <span className="cand-module">{c.module}</span>}
        <span className="cand-meta">
          {c.author ?? "技术支持"} 回复 · {c.group_name ?? "客户群"} · {when(c.created_at)}
        </span>
      </header>

      <div className="cand-body">
        <div className="cand-main">
          <label>
            <span>问题</span>
            {editable ? (
              <input value={draft.question} maxLength={2000} onChange={(e) => set("question", e.target.value)} />
            ) : (
              <p className="cand-q">{c.question}</p>
            )}
          </label>
          <label>
            <span>通用答案{c.status === "approved" ? "" : "（审核通过后写入）"}</span>
            {editable ? (
              <textarea
                value={draft.answer}
                maxLength={8000}
                rows={Math.min(10, Math.max(4, Math.ceil(draft.answer.length / 34) + draft.answer.split("\n").length))}
                onChange={(e) => set("answer", e.target.value)}
              />
            ) : (
              <p className="cand-a">{c.answer}</p>
            )}
          </label>
        </div>
        <div className="cand-orig">
          <div>
            <span>客户原话</span>
            <p>{c.customer_question}</p>
          </div>
          <div>
            <span>客户实际收到的回复</span>
            <p>{c.customer_reply}</p>
          </div>
          {c.ai_suggestion && (
            <details>
              <summary>AI 最初的建议</summary>
              <p>{c.ai_suggestion}</p>
            </details>
          )}
        </div>
      </div>

      {c.reason && <p className="cand-reason">AI 的判断：{c.reason}</p>}
      {(c.status === "rejected" || c.status === "retired") && (
        <p className="cand-reason">
          {c.status === "retired" ? "已停用" : `${c.reviewer ?? ""} 审核未通过`}
          {c.review_note ? `：${c.review_note}` : ""}
        </p>
      )}

      <footer className="cand-foot">
        {c.status === "proposed" && (
          <>
            <button className="primary" disabled={busy || !filled} onClick={() => act("confirm", "已加入候选知识，等其他同事审核")}>
              加入候选知识
            </button>
            <button disabled={busy} onClick={() => act("decline", "好的，这条不沉淀")}>不用</button>
          </>
        )}
        {(c.status === "not_general" || c.status === "declined") && (
          <button disabled={busy || !filled} onClick={() => act("confirm", "已改为收录，等其他同事审核")}>改为收录</button>
        )}
        {c.status === "pending" && (
          <>
            <input
              className="cand-note"
              placeholder="审核意见（可选）"
              value={note}
              maxLength={1000}
              onChange={(e) => setNote(e.target.value)}
            />
            <button
              className="primary"
              disabled={busy || own || !filled}
              title={own ? "不能审核自己回复或提交的知识，请其他同事审核" : undefined}
              onClick={() => act("approve", "审核通过，之后同类问题 AI 会参考这条")}
            >
              审核通过
            </button>
            <button disabled={busy || own} onClick={() => act("reject", "已标为不通过")}>不通过</button>
            <span className="cand-by">{c.confirmed_by} 提交{own ? " · 请其他同事审核" : ""}</span>
          </>
        )}
        {c.status === "approved" && (
          <>
            <span className="cand-by">
              {c.reviewer} 审核通过 · {when(c.reviewed_at)}
              {c.updated_by && c.updated_by !== c.author ? ` · ${c.updated_by} 改过内容` : ""}
            </span>
            <span className={`cand-sync ${c.synced_status === "confirmed" ? "ok" : ""}`}>{syncText(c)}</span>
            {askRetire ? (
              <span className="cand-confirm">
                停用后 AI 不再参考这条，售后数字人里的卡也会停用
                <button className="danger" disabled={busy} onClick={() => act("retire", "已停用")}>确认停用</button>
                <button disabled={busy} onClick={() => setAskRetire(false)}>取消</button>
              </span>
            ) : (
              <button className="plain" onClick={() => setAskRetire(true)}>停用</button>
            )}
          </>
        )}
      </footer>
    </article>
  );
}

interface Props {
  /** 当前登录的人（显示名） */
  me: string;
  notify: (text: string) => void;
  onCountsChanged: () => void;
}

/** 知识库：回复发出后 AI 建议沉淀的问答，回复的人确认、其他同事审核通过后，工作台和售后数字人都会用上。 */
export default function KnowledgePage({ me, notify, onCountsChanged }: Props) {
  const [view, setView] = useState<CandidateView>("proposed");
  const [items, setItems] = useState<Candidate[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const firstCounts = useRef(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchCandidates(view, view === "proposed" ? me : undefined)
      .then((r) => {
        setItems(r);
        setError(null);
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setLoading(false));
    fetchCandidateCounts()
      .then((c) => {
        setCounts({ proposed: c.mine, pending: c.pending });
        // 刚打开时没有等我确认的、但有等我审核的，直接看待审核
        if (firstCounts.current && !c.mine && c.pending) setView("pending");
        firstCounts.current = false;
      })
      .catch(() => undefined);
  }, [view, me]);

  useEffect(() => {
    setLoading(true);
    setItems([]);
    load();
    const t = setInterval(load, 20_000);
    return () => clearInterval(t);
  }, [load]);

  const changed = () => {
    load();
    onCountsChanged();
  };

  return (
    <div className="page-panel knowledge-page">
      <div className="tabs group-tabs page-tabs">
        {VIEWS.map((v) => (
          <button key={v.key} className={view === v.key ? "on" : ""} onClick={() => setView(v.key)}>
            {v.label}
            {!!counts[v.key] && <span className="tab-count">{counts[v.key]}</span>}
          </button>
        ))}
      </div>
      <div className="cand-list">
        {error && <p className="error pad">加载失败：{error}</p>}
        {loading && <p className="empty pad">加载中…</p>}
        {!loading && !error && items.length === 0 && (
          <p className="empty pad">
            {view === "proposed"
              ? "没有等你确认的。你改过 AI 建议后发出的回复，AI 觉得通用时会出现在这里"
              : view === "pending"
                ? "没有等审核的候选知识"
                : view === "approved"
                  ? "还没有审核通过的知识"
                  : view === "rejected"
                    ? "没有未通过或停用的"
                    : "没有 AI 判断不收录的"}
          </p>
        )}
        {items.map((c) => (
          <CandidateCard key={`${c.id}:${c.status}`} c={c} me={me} onChanged={changed} notify={notify} />
        ))}
      </div>
    </div>
  );
}
