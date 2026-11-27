import { useState } from "react";
import { candidateAction, type InboxItem } from "./api";

interface Props {
  item: InboxItem;
  onDone: () => void;
  onOpenKnowledge: () => void;
  notify: (text: string) => void;
}

/** 回复发出后，AI 觉得是通用问题时在输入框上方提示一次：加入候选知识，或者不用。 */
export default function ReflowTip({ item, onDone, onOpenKnowledge, notify }: Props) {
  const [busy, setBusy] = useState(false);
  const cand = item.suggestion?.candidate;
  if (!cand) return null;

  const act = async (action: "confirm" | "decline") => {
    setBusy(true);
    try {
      await candidateAction(cand.id, action);
      notify(action === "confirm" ? "已加入候选知识，等其他同事审核" : "好的，这条不沉淀");
      onDone();
    } catch (e) {
      notify(`操作失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="reflow-tip">
      <span className="reflow-text">
        刚才回复的问题可以沉淀成知识，下次同类问题 AI 直接答：<b>{cand.question}</b>
      </span>
      <span className="reflow-actions">
        <button className="primary" disabled={busy} onClick={() => act("confirm")}>加入候选知识</button>
        <button disabled={busy} onClick={onOpenKnowledge}>先改一改</button>
        <button className="plain" disabled={busy} onClick={() => act("decline")}>不用</button>
      </span>
    </div>
  );
}
