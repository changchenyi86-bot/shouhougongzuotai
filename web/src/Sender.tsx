import type { Message, SenderKind } from "./api";

/** 聊天记录里显示的发送人名字：群里的昵称；拿不到时客户显示 ID 末尾几位，员工显示账号。 */
export function senderName(m: Pick<Message, "sender_name" | "sender_id" | "from_customer">): string {
  if (m.sender_name) return m.sender_name;
  return m.from_customer ? `客户 ·${m.sender_id.slice(-4)}` : m.sender_id;
}

const CJK = /\p{Script=Han}/u;
const WORD = /[\p{L}\p{N}]/u;

/** 头像上的字：和七鱼一样取名字的最后一个字，有汉字时取最后一个汉字。 */
function avatarText(name: string | null, customer: boolean): string {
  if (!name) return customer ? "客" : "员";
  const chars = Array.from(name);
  const pick = (re: RegExp) => [...chars].reverse().find((c) => re.test(c));
  return pick(CJK) ?? pick(WORD) ?? chars[0];
}

const SOURCE: Record<SenderKind, { label: string; title: string }> = {
  internal: { label: "内", title: "本企业员工" },
  wechat: { label: "@微信", title: "微信用户" },
  wecom: { label: "@企业微信", title: "其他企业的企业微信用户" },
  external: { label: "外部", title: "外部人员" },
};

export function Avatar({ name, customer }: { name: string | null; customer: boolean }) {
  return (
    <span className="avatar" aria-hidden>
      {avatarText(name, customer)}
    </span>
  );
}

/** 气泡上方一行：昵称、来源（内部 / 微信 / 企业微信）、时间。 */
export function SenderLine({ name, kind, time }: { name: string; kind: SenderKind; time: string }) {
  const src = SOURCE[kind];
  return (
    <div className="who">
      <span className="who-name">
        <span className="nm">{name}</span>
        <span className={`src-tag ${kind}`} title={src.title}>
          {src.label}
        </span>
      </span>
      <time>{time}</time>
    </div>
  );
}
