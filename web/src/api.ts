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
}

export type SuggestionStatus =
  | "generating"
  | "ready"
  | "failed"
  | "adopted"
  | "edited"
  | "escalated"
  | "dismissed";

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

export type View = "todo" | "escalated" | "done" | "all";

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

export function fetchInbox(view: View, channel?: Channel): Promise<InboxItem[]> {
  const qs = new URLSearchParams({ view });
  if (channel) qs.set("channel", channel);
  return call(`/api/inbox?${qs}`);
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
export const resend = (id: string, operator: string) =>
  post<Suggestion>(`/api/suggestions/${id}/resend`, { operator: operator || null });
export const cancelSend = (id: string) => post<Suggestion>(`/api/suggestions/${id}/cancel-send`);
export const markManualSent = (id: string) => post<Suggestion>(`/api/suggestions/${id}/mark-manual-sent`);
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
}

export interface Conversation {
  items: InboxItem[];
  has_more_before: boolean;
  has_more_after: boolean;
}

export function fetchGroups(channel?: Channel): Promise<Group[]> {
  const qs = new URLSearchParams();
  if (channel) qs.set("channel", channel);
  return call(`/api/groups?${qs}`);
}

export function fetchConversation(
  channel: Channel,
  groupId: string,
  opts: { before?: string; after?: string; anchor?: string; limit?: number } = {},
): Promise<Conversation> {
  const qs = new URLSearchParams({ channel, group_id: groupId, limit: String(opts.limit ?? 50) });
  for (const k of ["before", "after", "anchor"] as const) if (opts[k]) qs.set(k, opts[k]!);
  return call(`/api/conversation?${qs}`);
}
