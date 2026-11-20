import { useState } from "react";
import type { KnowledgeRef, Suggestion } from "./api";

const KIND: Record<string, string> = {
  card: "纠错卡",
  manual: "产品手册",
  faq: "FAQ",
  ticket: "历史工单",
  bug: "缺陷单",
  sop: "处理规范",
  wiki: "知识文章",
};

function Item({ k }: { k: KnowledgeRef }) {
  const draft = k.kind === "card" && k.card_status === "draft";
  return (
    <li className={k.cited ? "cited" : ""}>
      {/* 编号和 AI 判断依据里写的 K1、K2 对应 */}
      <span className="kn-ref">{k.ref}</span>
      <span className={`kn-kind ${k.kind === "card" ? "card" : ""}`}>{KIND[k.kind] ?? k.kind}</span>
      <span className="kn-branch">{k.branch}</span>
      {k.url ? (
        <a href={k.url} target="_blank" rel="noreferrer" title={k.snippet || k.title}>{k.title}</a>
      ) : (
        <span className="kn-title" title={k.snippet || k.title}>{k.title}</span>
      )}
      {draft && <span className="kn-draft" title="技术支持还没确认这张纠错卡">草稿</span>}
    </li>
  );
}

/** AI 建议下方：这条建议参考了售后数字人里的哪些知识。 */
export default function Knowledge({ s }: { s: Suggestion }) {
  const [showAll, setShowAll] = useState(false);
  const list = s.knowledge ?? [];
  const cited = list.filter((k) => k.cited);
  const rest = list.filter((k) => !k.cited);

  if (s.knowledge_error) {
    return <p className="kn-error">{s.knowledge_error}</p>;
  }
  if (list.length === 0) return null;

  return (
    <div className="knowledge">
      <div className="kn-head">
        {cited.length > 0 ? (
          <span>依据售后数字人知识 {cited.length} 条</span>
        ) : (
          <span>售后数字人里没找到能用的知识，这条建议没有引用</span>
        )}
        {rest.length > 0 && (
          <button className="link" onClick={() => setShowAll((v) => !v)} aria-expanded={showAll}>
            {showAll ? "收起" : `${cited.length > 0 ? "另" : ""}查到 ${rest.length} 条未采用`}
          </button>
        )}
      </div>
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
