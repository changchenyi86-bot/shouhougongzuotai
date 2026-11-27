export type Channel = "wecom" | "dingtalk" | "feishu";

export const CHANNEL_LABEL: Record<Channel, string> = {
  wecom: "企业微信",
  dingtalk: "钉钉",
  feishu: "飞书",
};

export interface Message {
  id: string;
  channel: Channel;
  external_id: string;
  group_id: string;
  group_name: string | null;
  is_external_group: boolean;
  sender_id: string;
  sender_name: string | null;
  msg_type: string;
  content: string;
  sent_at: string;
  received_at: string;
  from_customer: boolean;
  attachment: Attachment | null;
  media_error: string | null;
}

export interface Attachment {
  id: string;
  filename: string;
  mime: string;
  size: number;
}

export const attachmentUrl = (id: string, download = false) =>
  `/api/attachments/${id}${download ? "?download=true" : ""}`;

export type SuggestionStatus =
  | "generating"
  | "ready"
  | "failed"
  | "adopted"
  | "edited"
  | "escalated"
  | "dismissed";

/** 生成建议时从售后数字人查到的一条参考知识。 */
export interface KnowledgeRef {
  ref: string;
  /** card 纠错卡 / manual 产品手册 / faq / ticket 历史工单 / bug 缺陷单 等 */
  kind: string;
  /** 适用分支：有数 / 知数 / 通用 */
  branch: string;
  title: string;
  source_id: string;
  url: string | null;
  /** 纠错卡状态，draft 表示技术支持还没确认 */
  card_status: string | null;
  snippet: string;
  /** 建议是否实际引用了这条 */
  cited: boolean;
}

export interface Suggestion {
  id: string;
  message_id: string;
  status: SuggestionStatus;
  content: string | null;
  can_answer: boolean | null;
  confidence: "high" | "medium" | "low" | null;
  reason: string | null;
  model: string | null;
  error: string | null;
  /** 参考知识，引用的排在前面 */
  knowledge: KnowledgeRef[];
  /** 知识库没查成的原因 */
  knowledge_error: string | null;
  final_content: string | null;
  delivery: string | null;
  send_status: SendStatus | null;
  send_error: string | null;
  sent_at: string | null;
  note: string | null;
  handled_by: string | null;
  handled_at: string | null;
}

export type SendStatus = "queued" | "sending" | "sent" | "failed" | "cancelled" | "manual";

export interface SendAgent {
  agent_id: string;
  last_seen: string;
  wecom_ready: boolean;
  detail: string | null;
  version: string | null;
}

export interface SendState {
  enabled: boolean;
  ready: boolean;
  agents: SendAgent[];
}

export interface InboxItem {
  message: Message;
  suggestion: Suggestion | null;
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      const d = (await res.json()).detail;
      // FastAPI 校验错误的 detail 是数组
      detail = Array.isArray(d) ? d.map((x: { msg?: string }) => x.msg ?? "").join("；") : d ?? detail;
    } catch {
      /* 非 JSON 错误 */
    }
    throw new Error(String(detail));
  }
  return res.json();
}

const post = <T>(url: string, body: unknown = {}) =>
  call<T>(url, { method: "POST", body: JSON.stringify(body) });

export const adopt = (id: string, content: string, operator: string, send = false) =>
  post<{ suggestion: Suggestion; delivery: string; detail: string }>(`/api/suggestions/${id}/adopt`, {
    content,
    operator: operator || null,
    send,
  });
export const fetchSendState = () => call<SendState>("/api/send/status");
export const escalate = (id: string, note: string, operator: string) =>
  post<Suggestion>(`/api/suggestions/${id}/escalate`, { note: note || null, operator: operator || null });
export const dismiss = (id: string, operator: string) =>
  post<Suggestion>(`/api/suggestions/${id}/dismiss`, { operator: operator || null });
export const regenerate = (id: string) => post<Suggestion>(`/api/suggestions/${id}/regenerate`);

export interface Group {
  channel: Channel;
  group_id: string;
  group_name: string | null;
  is_external_group: boolean;
  last_message_at: string;
  last_content: string;
  message_count: number;
  pending_count: number;
  escalated_count: number;
}

export interface SendTask {
  id: string;
  suggestion_id: string | null;
  content: string;
  attachments: Attachment[];
  status: SendStatus;
  error: string | null;
  created_by: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface Conversation {
  items: InboxItem[];
  has_more_before: boolean;
  has_more_after: boolean;
  outbox: SendTask[];
}

export type GroupView = "todo" | "escalated" | "all";

export function fetchGroups(view: GroupView = "all"): Promise<Group[]> {
  return call(`/api/groups?${new URLSearchParams({ view })}`);
}

export interface GroupNameResult {
  channel: Channel;
  group_id: string;
  name: string;
  duplicate: boolean;
}

/** 手动填写群名（企业微信查不到外部群群名时用），要和企业微信里显示的群名一致 */
export const setGroupName = (channel: Channel, group_id: string, name: string, operator: string) =>
  call<GroupNameResult>("/api/groups/name", {
    method: "PUT",
    body: JSON.stringify({ channel, group_id, name, operator: operator || null }),
  });

export async function uploadAttachment(file: File, operator: string): Promise<Attachment> {
  const form = new FormData();
  form.append("file", file);
  if (operator) form.append("operator", operator);
  const res = await fetch("/api/attachments", { method: "POST", body: form });
  if (!res.ok) {
    let detail = `${res.status}`;
    try {
      detail = (await res.json()).detail ?? detail;
    } catch {
      /* 非 JSON 错误 */
    }
    throw new Error(String(detail));
  }
  return res.json();
}

export const sendToGroup = (body: {
  channel: Channel;
  group_id: string;
  content: string;
  attachment_ids: string[];
  suggestion_id: string | null;
  operator: string;
}) => post<SendTask>("/api/conversations/send", { ...body, operator: body.operator || null });

export const resendTask = (id: string, operator: string) =>
  post<SendTask>(`/api/send-tasks/${id}/resend`, { operator: operator || null });
export const cancelTask = (id: string) => post<SendTask>(`/api/send-tasks/${id}/cancel`);
export const dismissTask = (id: string) => post<SendTask>(`/api/send-tasks/${id}/mark-manual-sent`);
export const fetchMedia = (messageId: string) => post<Message>(`/api/messages/${messageId}/fetch-media`);

export function fetchConversation(
  channel: Channel,
  groupId: string,
  opts: { before?: string; after?: string; anchor?: string; limit?: number } = {},
): Promise<Conversation> {
  const qs = new URLSearchParams({ channel, group_id: groupId, limit: String(opts.limit ?? 50) });
  for (const k of ["before", "after", "anchor"] as const) if (opts[k]) qs.set(k, opts[k]!);
  return call(`/api/conversation?${qs}`);
}
