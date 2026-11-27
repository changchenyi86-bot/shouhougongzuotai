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
  | "dismissed"
  /** 结束会话时还没回复，随会话一起结束 */
  | "closed";

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

/** 登录过期或没登录：页面收到这个事件后回到登录页 */
export const AUTH_REQUIRED = "auth-required";

async function failure(res: Response): Promise<Error> {
  let detail = `${res.status}`;
  try {
    const d = (await res.json()).detail;
    // FastAPI 校验错误的 detail 是数组，去掉开头的 "Value error, "
    detail = Array.isArray(d)
      ? d.map((x: { msg?: string }) => (x.msg ?? "").replace(/^Value error, /, "")).join("；")
      : d ?? detail;
  } catch {
    /* 非 JSON 错误 */
  }
  if (res.status === 401 && !res.url.includes("/api/auth/")) window.dispatchEvent(new Event(AUTH_REQUIRED));
  return new Error(String(detail));
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) throw await failure(res);
  return res.status === 204 ? (undefined as T) : res.json();
}

const post = <T>(url: string, body: unknown = {}) =>
  call<T>(url, { method: "POST", body: JSON.stringify(body) });

export const adopt = (id: string, content: string, send = false) =>
  post<{ suggestion: Suggestion; delivery: string; detail: string }>(`/api/suggestions/${id}/adopt`, { content, send });
export const fetchSendState = () => call<SendState>("/api/send/status");
export const escalate = (id: string, note: string) =>
  post<Suggestion>(`/api/suggestions/${id}/escalate`, { note: note || null });
export const dismiss = (id: string) => post<Suggestion>(`/api/suggestions/${id}/dismiss`);
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
  /** 群负责人 */
  owner: StaffBrief | null;
  /** 进行中的会话分给了谁 */
  assignee: StaffBrief | null;
  /** 有进行中的会话 */
  conversation_open: boolean;
}

export interface StaffBrief {
  id: string;
  name: string;
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

/** 一次结束会话的记录 */
export interface ConversationEnd {
  id: string;
  ended_by: string | null;
  ended_at: string;
  /** 随会话结束的未回复客户消息数 */
  closed_count: number;
}

export interface Conversation {
  items: InboxItem[];
  has_more_before: boolean;
  has_more_after: boolean;
  outbox: SendTask[];
  /** 这个群最近的会话结束记录，从早到晚 */
  ends: ConversationEnd[];
  owner: StaffBrief | null;
  assignee: StaffBrief | null;
  conversation_open: boolean;
}

export type GroupView = "todo" | "escalated" | "unassigned" | "all";

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
export const setGroupName = (channel: Channel, group_id: string, name: string) =>
  call<GroupNameResult>("/api/groups/name", { method: "PUT", body: JSON.stringify({ channel, group_id, name }) });

export async function uploadAttachment(file: File): Promise<Attachment> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/api/attachments", { method: "POST", body: form });
  if (!res.ok) throw await failure(res);
  return res.json();
}

export const sendToGroup = (body: {
  channel: Channel;
  group_id: string;
  content: string;
  attachment_ids: string[];
  suggestion_id: string | null;
}) => post<SendTask>("/api/conversations/send", body);

/** 结束会话：没回复的客户消息不再算待处理，客户再发消息时重新回到待处理 */
export const endConversation = (channel: Channel, group_id: string) =>
  post<ConversationEnd>("/api/conversations/end", { channel, group_id });

export const resendTask = (id: string) => post<SendTask>(`/api/send-tasks/${id}/resend`);
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

// ---------- 登录、成员 ----------

export type Role = "admin" | "support";
export type StaffStatus = "online" | "away";

export const ROLE_LABEL: Record<Role, string> = { admin: "管理员", support: "技术支持" };

export interface Team {
  id: string;
  name: string;
}

export interface Staff {
  id: string;
  name: string;
  username: string;
  role: Role;
  team: Team | null;
  active: boolean;
  status: StaffStatus;
  /** 设成在线，并且最近打开着工作台 */
  online: boolean;
  last_seen: string | null;
  /** 手上进行中的会话数 */
  open_count: number;
}

const put = <T>(url: string, body: unknown) => call<T>(url, { method: "PUT", body: JSON.stringify(body) });

export const fetchAuthState = () => call<{ needs_setup: boolean }>("/api/auth/state");
export const fetchMe = () => call<Staff>("/api/auth/me");
export const login = (username: string, password: string) => post<Staff>("/api/auth/login", { username, password });
export const setupAdmin = (body: { name: string; username: string; password: string }) =>
  post<Staff>("/api/auth/setup", body);
export const logout = () => post<void>("/api/auth/logout");
export const setMyStatus = (status: StaffStatus) => put<Staff>("/api/auth/me/status", { status });
export const changePassword = (old_password: string, new_password: string) =>
  put<void>("/api/auth/me/password", { old_password, new_password });

export const fetchTeams = () => call<Team[]>("/api/teams");
export const fetchStaff = () => call<Staff[]>("/api/staff");
export const addStaff = (body: { name: string; username: string; password: string; role: Role; team_id: string | null }) =>
  post<Staff>("/api/staff", body);
export const editStaff = (
  id: string,
  body: Partial<{ name: string; role: Role; team_id: string; clear_team: boolean; active: boolean; password: string }>,
) => call<Staff>(`/api/staff/${id}`, { method: "PATCH", body: JSON.stringify(body) });

/** 指定或取消群负责人（管理员） */
export const setGroupOwner = (channel: Channel, group_id: string, owner_id: string | null) =>
  put<void>("/api/groups/owner", { channel, group_id, owner_id });

/** 把进行中的会话转给同事 */
export const assignConversation = (channel: Channel, group_id: string, staff_id: string) =>
  post<StaffBrief>("/api/conversations/assign", { channel, group_id, staff_id });
