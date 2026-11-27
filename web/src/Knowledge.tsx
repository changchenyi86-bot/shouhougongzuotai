import { useState } from "react";
import type { KnowledgeRef, Suggestion } from "./api";

export const KIND: Record<string, string> = {
  card: "纠错卡",
  manual: "产品手册",
  faq: "FAQ",
  ticket: "历史工单",
  bug: "缺陷单",
  sop: "处理规范",
  wiki: "知识文章",
  workbench: "工作台知识",
  history: "历史回复",
};

function hint(k: KnowledgeRef): string {
  if (k.kind === "history")
    return `${k.author ?? "技术支持"}${k.group_name ? `在「${k.group_name}」` : ""}的回复：${k.snippet}`;
  if (k.kind === "workbench") return `审核过的答案：${k.snippet}`;
  return k.snippet || k.title;
}

function Item({ k }: { k: KnowledgeRef }) {
  const draft = k.kind === "card" && k.card_status === "draft";
  return (
    <li className={k.cited ? "cited" : ""}>
      {/* 编号和 AI 判断依据里写的 K1、W1、H1 对应 */}
      <span className="kn-ref">{k.ref}</span>
      {/* 技术支持确认过的（纠错卡、工作台知识）用绿色；不叫 card，免得和团队设置页的卡片样式冲突 */}
      <span className={`kn-kind ${k.kind === "card" || k.kind === "workbench" ? "verified" : ""}`}>{KIND[k.kind] ?? k.kind}</span>
      <span className="kn-branch">{k.branch}</span>
      {k.url ? (
        <a href={k.url} target="_blank" rel="noreferrer" title={hint(k)}>{k.title}</a>
      ) : (
        <span className="kn-title" title={hint(k)}>{k.title}</span>
      )}
      {draft && <span className="kn-draft" title="技术支持还没确认这张纠错卡">草稿</span>}
      {k.kind === "history" && <span className="kn-draft" title="技术支持以前发出的回复，没经过审核">未审核</span>}
    </li>
  );
}

/** AI 建议下方：这条建议依据了哪些知识（售后数字人、工作台知识、历史回复）；没有依据时明确标出来。 */
export default function Knowledge({ s }: { s: Suggestion }) {
  const [showAll, setShowAll] = useState(false);
  const list = s.knowledge ?? [];
  const cited = list.filter((k) => k.cited);
  const rest = list.filter((k) => !k.cited);

  let none: string | null = null;
  if (cited.length === 0 && !s.knowledge_error) {
    if (s.knowledge_note) none = `${s.knowledge_note}，这条建议只依据聊天记录`;
    else if (rest.length) none = "查到的资料都不相关，这条建议没有依据知识库";
    else none = "知识库里没查到相关资料，这条建议没有依据知识库";
  }

  return (
    <div className="knowledge">
      {s.knowledge_error && <p className="kn-error">{s.knowledge_error}</p>}
      {(cited.length > 0 || none || rest.length > 0) && (
        <div className="kn-head">
          {cited.length > 0 ? (
            <span>依据知识 {cited.length} 条</span>
          ) : (
            none && (
              <span className="kn-none" title="AI 没有可以对照的资料，发送前请核对内容">
                {none}
              </span>
            )
          )}
          {rest.length > 0 && (
            <button className="link" onClick={() => setShowAll((v) => !v)} aria-expanded={showAll}>
              {showAll ? "收起" : `${cited.length > 0 ? "另" : ""}查到 ${rest.length} 条未采用`}
            </button>
          )}
        </div>
      )}
      {cited.length > 0 && (
        <ul>
          {cited.map((k) => <Item key={k.ref} k={k} />)}
        </ul>
      )}
      {showAll && rest.length > 0 && (
        <ul className="kn-rest">
          {rest.map((k) => <Item key={k.ref} k={k} />)}
        </ul>
      )}
    </div>
  );
}
