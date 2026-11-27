import { forwardRef, useEffect, useRef, type ReactNode } from "react";
import type { Member, MentionRef, Message, SenderKind } from "./api";
import { Avatar } from "./Sender";

/** 回复里的一处 @：文字里从 start 到 end 是「@名字」 */
export interface MentionSpan {
  start: number;
  end: number;
  member: Member;
}

/** 名字前面是英文字母或数字的 @（如邮箱 a@b.com）不算 @ 人 */
const WORD_BEFORE = /[A-Za-z0-9._%+-]/;
const QUERY_MAX = 20;

/** 选人时记下的：名字 → 选的是谁（同名时靠它区分） */
export type Picked = Record<string, Member>;

function memberFor(name: string, members: Member[], picked: Picked): Member | undefined {
  return picked[name] ?? members.find((m) => m.name === name);
}

/** 找出回复里所有「@群成员名字」，按出现的先后。名字有包含关系时取最长的（「@张三丰」不算 @张三）。 */
export function findMentions(text: string, members: Member[], picked: Picked): MentionSpan[] {
  const names = [...new Set([...members.map((m) => m.name), ...Object.keys(picked)])]
    .filter(Boolean)
    .sort((a, b) => b.length - a.length);
  if (!names.length) return [];
  const out: MentionSpan[] = [];
  for (let i = text.indexOf("@"); i >= 0; i = text.indexOf("@", i + 1)) {
    if (i > 0 && WORD_BEFORE.test(text[i - 1])) continue;
    const name = names.find((n) => text.startsWith(n, i + 1));
    const member = name ? memberFor(name, members, picked) : undefined;
    if (!name || !member) continue;
    out.push({ start: i, end: i + 1 + name.length, member });
    i += name.length;
  }
  return out;
}

export const toRefs = (spans: MentionSpan[]): MentionRef[] =>
  spans.map((s) => ({ user_id: s.member.id, name: s.member.name, kind: s.member.kind }));

/** 光标前面正在输入的「@筛选文字」：返回 @ 的位置和筛选文字；不在输入 @ 时返回 null。 */
export function activeQuery(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret);
  const at = before.lastIndexOf("@");
  if (at < 0 || (at > 0 && WORD_BEFORE.test(before[at - 1]))) return null;
  const query = before.slice(at + 1);
  if (query.length > QUERY_MAX || /\s/.test(query)) return null;
  return { start: at, query };
}

/** 成员列表筛选：名字里包含筛选文字（不分大小写），名字开头就对上的排前面。 */
export function filterMembers(members: Member[], query: string): Member[] {
  const q = query.toLowerCase();
  const hits = members.filter((m) => m.name.toLowerCase().includes(q));
  return q ? [...hits.filter((m) => m.name.toLowerCase().startsWith(q)), ...hits.filter((m) => !m.name.toLowerCase().startsWith(q))] : hits;
}

/** 这条消息的发送人能不能 @：要知道他在群里的名字。成员名单里有就用名单里的，没有就用消息上的名字。 */
export function memberOf(m: Pick<Message, "sender_id" | "sender_name" | "sender_kind" | "from_customer">, members: Member[]): Member | null {
  const listed = members.find((x) => x.id === m.sender_id);
  if (listed) return listed;
  if (!m.sender_name) return null;
  return {
    id: m.sender_id,
    name: m.sender_name,
    kind: m.sender_kind,
    customer: m.from_customer,
    last_spoke_at: null,
    duplicate: members.some((x) => x.name === m.sender_name),
  };
}

const SOURCE: Record<SenderKind, string> = { internal: "内", wechat: "@微信", wecom: "@企业微信", external: "外部" };

/** 输入框后面垫的一层：和输入框一样排版的同一段文字，@ 的人底下标上颜色。 */
export const MentionBackdrop = forwardRef<HTMLDivElement, { text: string; spans: MentionSpan[]; caretAt: number | null; caretRef: React.Ref<HTMLSpanElement> }>(
  function MentionBackdrop({ text, spans, caretAt, caretRef }, ref) {
    const parts: ReactNode[] = [];
    let pos = 0;
    const cut = (until: number) => {
      if (caretAt !== null && caretAt >= pos && caretAt < until) {
        parts.push(text.slice(pos, caretAt), <span key="caret" ref={caretRef} />, text.slice(caretAt, until));
      } else parts.push(text.slice(pos, until));
      pos = until;
    };
    for (const s of spans) {
      cut(s.start);
      parts.push(
        <mark key={s.start} className={`mention ${s.member.duplicate ? "dup" : ""}`}>
          {text.slice(s.start, s.end)}
        </mark>,
      );
      pos = s.end;
    }
    cut(text.length);
    if (caretAt !== null && caretAt === text.length) parts.push(<span key="caret" ref={caretRef} />);
    return (
      <div className="mention-backdrop" ref={ref} aria-hidden>
        {parts}
        {"​"}
      </div>
    );
  },
);

/** 打 @ 后弹出的群成员列表 */
export function MentionMenu(props: {
  members: Member[] | null;
  error: string | null;
  query: string;
  index: number;
  left: number;
  onPick: (m: Member) => void;
  onHover: (i: number) => void;
}) {
  const { members, query, index } = props;
  const list = members ? filterMembers(members, query) : [];
  const box = useRef<HTMLUListElement>(null);
  useEffect(() => {
    box.current?.querySelector<HTMLElement>(`[data-i="${index}"]`)?.scrollIntoView({ block: "nearest" });
  }, [index]);
  return (
    <div className="mention-menu" style={{ left: props.left }} role="listbox" aria-label="选择要 @ 的群成员" onMouseDown={(e) => e.preventDefault()}>
      <div className="mention-head">
        <span>@ 群成员</span>
        <small>↑↓ 选择 · Enter 确认 · Esc 关闭</small>
      </div>
      {!members && !props.error && <p className="mention-empty">正在加载群成员…</p>}
      {props.error && <p className="mention-empty">群成员加载失败：{props.error}</p>}
      {members && !list.length && (
        <p className="mention-empty">{query ? `群里没有名字含「${query}」的人` : "还没拿到这个群的成员名单"}</p>
      )}
      {list.length > 0 && (
        <ul ref={box}>
          {list.map((m, i) => (
            <li key={m.id}>
              <button
                type="button"
                data-i={i}
                role="option"
                aria-selected={i === index}
                className={i === index ? "on" : ""}
                onMouseEnter={() => props.onHover(i)}
                onClick={() => props.onPick(m)}
              >
                <Avatar name={m.name} customer={m.customer} />
                <span className="nm">{m.name}</span>
                <span className={`src-tag ${m.kind}`}>{SOURCE[m.kind]}</span>
                {m.duplicate && (
                  <span className="dup-tag" title="群里还有人叫这个名字，发送电脑分不清时会按普通文字 @，对方收不到提醒">
                    有同名
                  </span>
                )}
                {m.last_spoke_at && <span className="meta">说过话</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** 发出的回复里把 @ 的人标出来 */
export function MentionText({ text, mentions }: { text: string; mentions?: MentionRef[] }) {
  if (!mentions?.length) return <>{text}</>;
  const parts: ReactNode[] = [];
  let pos = 0;
  for (const m of mentions) {
    const at = text.indexOf(`@${m.name}`, pos);
    if (at < 0) continue;
    parts.push(text.slice(pos, at), <mark key={at} className="mention">@{m.name}</mark>);
    pos = at + 1 + m.name.length;
  }
  parts.push(text.slice(pos));
  return <>{parts}</>;
}
