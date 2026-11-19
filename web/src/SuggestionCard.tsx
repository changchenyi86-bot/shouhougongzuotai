import { useEffect, useState } from "react";
import { adopt, dismiss, escalate, regenerate, type Suggestion } from "./api";

const CONFIDENCE: Record<string, string> = { high: "把握高", medium: "把握一般", low: "把握低" };
const DONE_LABEL: Record<string, string> = {
  adopted: "已采纳",
  edited: "已修改后采纳",
  dismissed: "已忽略",
};

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

interface Props {
  suggestion: Suggestion;
  operator: string;
  onChanged: () => void;
  notify: (text: string) => void;
}

export default function SuggestionCard({ suggestion: s, operator, onChanged, notify }: Props) {
  const [draft, setDraft] = useState(s.content ?? "");
  const [note, setNote] = useState("");
  const [showNote, setShowNote] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => setDraft(s.content ?? ""), [s.id, s.content]);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } catch (e) {
      notify(`操作失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  if (s.status === "generating") return <div className="sug pending">AI 正在生成回复建议…</div>;

  if (s.status === "failed")
    return (
      <div className="sug failed">
        <p>AI 建议生成失败：{s.error}</p>
        <div className="actions">
          <button disabled={busy} onClick={() => run(() => regenerate(s.id))}>重新生成</button>
          <button disabled={busy} onClick={() => run(() => escalate(s.id, "", operator))}>转人工</button>
        </div>
      </div>
    );

  if (s.status in DONE_LABEL)
    return (
      <div className="sug done">
        <span className="badge">{DONE_LABEL[s.status]}</span>
        {s.handled_by && <span className="by">{s.handled_by}</span>}
        {s.final_content && <p>{s.final_content}</p>}
        {s.final_content && (
          <button className="link" onClick={async () => notify((await copy(s.final_content!)) ? "已复制" : "复制失败，请手动选择文字")}>
            再次复制
          </button>
        )}
      </div>
    );

  const edited = draft.trim() !== (s.content ?? "").trim();
  const onAdopt = () =>
    run(async () => {
      const text = draft.trim();
      const r = await adopt(s.id, text, operator);
      if (r.delivery === "manual") {
        notify((await copy(text)) ? "已复制回复，请到群里粘贴发送" : "已采纳，但复制失败，请手动复制");
      } else {
        notify("已发送到群里");
      }
    });

  return (
    <div className={`sug ${s.status}`}>
      <div className="sug-head">
        {s.status === "escalated" ? (
          <span className="badge warn">已转人工{s.handled_by ? ` · ${s.handled_by}` : ""}</span>
        ) : s.can_answer ? (
          <span className="badge ok">AI 建议可直接回复</span>
        ) : (
          <span className="badge warn">AI 建议转人工</span>
        )}
        {s.confidence && <span className="conf">{CONFIDENCE[s.confidence]}</span>}
        {s.reason && <span className="reason">{s.reason}</span>}
      </div>
      {s.note && <p className="note">转人工备注：{s.note}</p>}
      <textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={Math.min(8, Math.max(3, draft.split("\n").length + 1))} />
      <div className="actions">
        <button className="primary" disabled={busy || !draft.trim()} onClick={onAdopt}>
          {edited ? "修改后采纳并复制" : "采纳并复制"}
        </button>
        {s.status !== "escalated" && (
          <button disabled={busy} onClick={() => setShowNote((v) => !v)}>转人工</button>
        )}
        <button disabled={busy} onClick={() => run(() => dismiss(s.id, operator))}>无需回复</button>
        {s.status !== "escalated" && (
          <button disabled={busy} onClick={() => run(() => regenerate(s.id))}>重新生成</button>
        )}
      </div>
      {showNote && (
        <div className="escalate">
          <input placeholder="备注（可选）：需要谁处理、缺什么信息" value={note} onChange={(e) => setNote(e.target.value)} />
          <button disabled={busy} onClick={() => run(() => escalate(s.id, note, operator))}>确认转人工</button>
        </div>
      )}
    </div>
  );
}
