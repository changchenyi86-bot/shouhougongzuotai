import { useEffect, useState } from "react";
import {
  adopt,
  cancelSend,
  dismiss,
  escalate,
  markManualSent,
  regenerate,
  resend,
  type Message,
  type Suggestion,
} from "./api";

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
  message: Message;
  canAutoSend: boolean;
  operator: string;
  onChanged: () => void;
  notify: (text: string) => void;
}

export default function SuggestionCard({ suggestion: s, message, canAutoSend, operator, onChanged, notify }: Props) {
  const [draft, setDraft] = useState(s.content ?? "");
  const [note, setNote] = useState("");
  const [showNote, setShowNote] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => setDraft(s.content ?? ""), [s.id, s.content]);
  useEffect(() => setConfirming(false), [s.status, s.send_status]);

  const groupName = message.group_name;
  const autoSend = canAutoSend && !!groupName;

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await fn();
      onChanged();
    } catch (e) {
      notify(`操作失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
      setConfirming(false);
    }
  };
  const copyText = async (text: string) => notify((await copy(text)) ? "已复制" : "复制失败，请手动选择文字");

  const confirmBar = (label: string, onConfirm: () => void) => (
    <div className="confirm-send">
      <span>
        将以客服账号把这条回复发到企业微信群「<b>{groupName}</b>」，确认发送？
      </span>
      <button className="primary" disabled={busy} onClick={onConfirm}>{label}</button>
      <button disabled={busy} onClick={() => setConfirming(false)}>取消</button>
    </div>
  );

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

  if (s.status in DONE_LABEL) {
    const st = s.send_status;
    const needsAction = st === "failed" || st === "cancelled";
    return (
      <div className={`sug done ${needsAction ? "send-failed" : ""}`}>
        <span className="badge">{DONE_LABEL[s.status]}</span>
        {s.handled_by && <span className="by">{s.handled_by}</span>}
        {st === "queued" && <span className="badge send">排队等待发送</span>}
        {st === "sending" && <span className="badge send">正在发送到群里…</span>}
        {st === "sent" && (
          <span className="badge ok">已自动发送{s.sent_at ? ` · ${new Date(s.sent_at).toLocaleTimeString("zh-CN")}` : ""}</span>
        )}
        {st === "failed" && <span className="badge err">自动发送失败</span>}
        {st === "cancelled" && <span className="badge warn">已取消发送</span>}
        {st === "manual" && <span className="badge">已手动发送</span>}
        {s.final_content && <p>{s.final_content}</p>}
        {st === "failed" && s.send_error && <p className="send-error">{s.send_error}</p>}
        {needsAction && confirming && confirmBar("确认重新发送", () => run(() => resend(s.id, operator)))}
        <div className="actions">
          {st === "queued" && (
            <button disabled={busy} onClick={() => run(() => cancelSend(s.id))}>取消发送</button>
          )}
          {needsAction && autoSend && !confirming && (
            <button className="primary" disabled={busy} onClick={() => setConfirming(true)}>重新发送</button>
          )}
          {s.final_content && st !== "queued" && st !== "sending" && (
            <button disabled={busy} onClick={() => copyText(s.final_content!)}>
              {needsAction ? "复制回复" : "再次复制"}
            </button>
          )}
          {needsAction && (
            <button disabled={busy} onClick={() => run(() => markManualSent(s.id))}>我已手动发送</button>
          )}
        </div>
      </div>
    );
  }

  const edited = draft.trim() !== (s.content ?? "").trim();
  const onCopyAdopt = () =>
    run(async () => {
      const text = draft.trim();
      await adopt(s.id, text, operator);
      notify((await copy(text)) ? "已复制回复，请到群里粘贴发送" : "已采纳，但复制失败，请手动复制");
    });
  const onSendAdopt = () =>
    run(async () => {
      await adopt(s.id, draft.trim(), operator, true);
      notify("已确认，正在发送到群里");
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
      <textarea
        value={draft}
        onChange={(e) => {
          setDraft(e.target.value);
          setConfirming(false);
        }}
        rows={Math.min(8, Math.max(3, draft.split("\n").length + 1))}
      />
      {confirming && confirmBar("确认发送", onSendAdopt)}
      <div className="actions">
        {autoSend ? (
          <>
            <button className="primary" disabled={busy || !draft.trim() || confirming} onClick={() => setConfirming(true)}>
              {edited ? "修改后发送到群" : "采纳并发送到群"}
            </button>
            <button disabled={busy || !draft.trim()} onClick={onCopyAdopt}>只复制，我自己发</button>
          </>
        ) : (
          <button className="primary" disabled={busy || !draft.trim()} onClick={onCopyAdopt}>
            {edited ? "修改后采纳并复制" : "采纳并复制"}
          </button>
        )}
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
