export type Channel = "wecom" | "dingtalk" | "feishu";

export const CHANNEL_LABEL: Record<Channel, string> = {
  wecom: "企业微信",
  dingtalk: "钉钉",
  feishu: "飞书",
};

/** 发送人来自哪里：本企业员工 / 微信用户 / 其他企业的企业微信用户 / 渠道分不出细类的外部人员 */
export type SenderKind = "internal" | "wechat" | "wecom" | "external";

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
  sender_kind: SenderKind;
  attachment: Attachment | null;
  media_error: string | null;
}

export type ExtractStatus = "pending" | "done" | "failed" | "skipped";

export interface Attachment {
  id: string;
  filename: string;
  mime: string;
  size: number;
  /** 客户发的图片、文件自动识别成文字的结果（给 AI 建议用）；技术支持上传的、上线前的老附件为 null */
  extract_status?: ExtractStatus | null;
  extracted_text?: string | null;
  extract_error?: string | null;
}

export const attachmentUrl = (id: string, download = false) =>
  `/api/attachments/${id}${download ? "?download=true" : ""}`;

export type SuggestionStatus =
  | "generating"
  | "ready"
  | "failed"
  | "adopted"
  | "edited"
  | "dismissed"
  /** 结束会话时还没回复，随会话一起结束 */
  | "closed"
  /** 客户接着又说了话，这条并进了同一轮后面那条建议（merged_into） */
  | "merged";

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
  /** 相似问答（工作台知识、历史回复）：写的人、当时的群、相似度 */
  author?: string | null;
  group_name?: string | null;
  score?: number | null;
}

/** 采纳情况：原样采纳 / 小改 / 大改 / 未用 */
export type Adoption = "exact" | "minor" | "major" | "unused";
export const ADOPTION_LABEL: Record<Adoption, string> = { exact: "原样采纳", minor: "小改", major: "大改", unused: "未用" };

export type CandidateStatus = "proposed" | "pending" | "approved" | "rejected" | "retired" | "declined" | "not_general";

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
  /** 没查售后数字人的原因（寒暄、没配置） */
  knowledge_note?: string | null;
  /** AI 判断是否需要回复 */
  needs_reply?: boolean | null;
  /** AI 判为无需回复、自动标记的 */
  auto_dismissed?: boolean;
  reopened_by?: string | null;
  adoption?: Adoption | null;
  adoption_score?: number | null;
  /** 回复是和 AI 对话润色出来的 */
  via_chat?: boolean;
  /** 生成时带给 AI 的客户档案：哪家客户、建议卡上显示的一句摘要 */
  profile?: { customer: string | null; summary: string } | null;
  /** 这条建议回的是哪几条客户消息（同一轮连发或几个人各说的），按时间顺序，最后一条是自己 */
  covers?: string[];
  /** 已并入的那条建议（status 为 merged 时） */
  merged_into?: string | null;
  /** AI 结合上下文补全的、这一轮要解决的问题 */
  question?: string | null;
  /** 查的是哪个产品的知识 */
  products?: ProductCode[] | null;
  /** 产品怎么定的：manual 管理员指定 / group 群名 / ai AI 判断 / all 几个都查 / override 技术支持改的 */
  product_source?: ProductSource | null;
  product_reason?: string | null;
  product_corrected_by?: string | null;
  /** 回复发出后 AI 判断要不要沉淀成知识 */
  candidate?: { id: string; status: CandidateStatus; question: string } | null;
  final_content: string | null;
  delivery: string | null;
  send_status: SendStatus | null;
  send_error: string | null;
  sent_at: string | null;
  note: string | null;
  handled_by: string | null;
  handled_at: string | null;
  /** AI 没答上：客服大改或没用 AI 建议、转给同事求助（escalated 是早期的转人工），进知识库的待补知识 */
  missed?: Missed | null;
  missed_by?: string | null;
  missed_at?: string | null;
}

export type Missed = "rewritten" | "own" | "transfer" | "escalated";
export const MISSED_LABEL: Record<Missed, string> = {
  rewritten: "大改了 AI 建议",
  own: "没用 AI 建议，自己写的",
  transfer: "转给同事求助",
  escalated: "转人工（旧）",
};

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

export const adopt = (id: string, content: string, send = false, viaChat = false, mentions: MentionRef[] = []) =>
  post<{ suggestion: Suggestion; delivery: string; detail: string }>(`/api/suggestions/${id}/adopt`, {
    content,
    send,
    via_chat: viaChat,
    mentions,
  });
export const fetchSendState = () => call<SendState>("/api/send/status");
export const dismiss = (id: string) => post<Suggestion>(`/api/suggestions/${id}/dismiss`);
export const regenerate = (id: string) => post<Suggestion>(`/api/suggestions/${id}/regenerate`);
/** 把「无需回复」改回待回复 */
export const reopen = (id: string) => post<Suggestion>(`/api/suggestions/${id}/reopen`);

/** 和 AI 对话润色回复的一条 */
export interface ChatTurn {
  id: string;
  role: "staff" | "ai";
  content: string;
  /** AI 给技术支持的一句说明，不发给客户 */
  note: string | null;
  operator: string | null;
  created_at: string;
}
export const fetchChat = (suggestionId: string) => call<ChatTurn[]>(`/api/suggestions/${suggestionId}/chat`);
export const sendChat = (suggestionId: string, content: string) =>
  post<ChatTurn[]>(`/api/suggestions/${suggestionId}/chat`, { content });

export interface Group {
  channel: Channel;
  group_id: string;
  group_name: string | null;
  is_external_group: boolean;
  last_message_at: string;
  last_content: string;
  message_count: number;
  pending_count: number;
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

/** 回复里要真正 @ 的一个群成员（回复文字里写「@名字」） */
export interface MentionRef {
  user_id: string;
  name: string;
  kind: string;
}

/** 群里能 @ 的人 */
export interface Member {
  id: string;
  /** 群里显示的名字（群昵称优先），和企业微信 @ 列表里的一致 */
  name: string;
  kind: SenderKind;
  customer: boolean;
  last_spoke_at: string | null;
  /** 群里还有别人也叫这个名字 */
  duplicate: boolean;
}

export const fetchMembers = (channel: Channel, group_id: string) =>
  call<Member[]>(`/api/conversation/members?${new URLSearchParams({ channel, group_id })}`);

export interface SendTask {
  id: string;
  suggestion_id: string | null;
  content: string;
  attachments: Attachment[];
  mentions?: MentionRef[];
  status: SendStatus;
  error: string | null;
  /** 发出去了，但有没做到的（如有人没 @ 上） */
  warning?: string | null;
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

export type GroupView = "todo" | "unassigned" | "all";

/** 工作台有没有新变化（新消息、AI 建议、会话分配），版本号变了再去刷新列表和聊天记录 */
export const fetchUpdates = () => call<{ version: string }>("/api/updates");

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
  via_chat?: boolean;
  mentions?: MentionRef[];
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
  /** 设过密码；批量导入的成员要管理员设了密码才能登录 */
  has_password: boolean;
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

// ---------- 产品（有数 / 知数 / 数据中台）：查哪个产品的知识 ----------

export type ProductCode = "bi" | "zs" | "ed";
export type ProductSource = "manual" | "group" | "ai" | "all" | "override";
export const PRODUCT_LABEL: Record<ProductCode, string> = { bi: "有数", zs: "知数", ed: "中台" };
export const PRODUCT_CODES: ProductCode[] = ["bi", "zs", "ed"];
export const productLabel = (codes: ProductCode[] | null | undefined) =>
  (codes ?? []).map((c) => PRODUCT_LABEL[c] ?? c).join("、");

export interface GroupProduct {
  channel: Channel;
  group_id: string;
  group_name: string | null;
  /** 群名里带的产品标记 */
  by_name: ProductCode[];
  /** 管理员指定的产品 */
  manual: ProductCode[] | null;
  /** 现在怎么定：manual 指定 / group 按群名 / ai 每轮由 AI 判断 */
  effective: "manual" | "group" | "ai";
  /** 群名一个产品都不带，建议指定 */
  needs_setting: boolean;
  recommend: ProductCode[];
  recommend_basis: string;
  ai_stats: Partial<Record<ProductCode, number>>;
  owner: string | null;
  owner_team: string | null;
  customer: string | null;
  last_message_at: string | null;
  updated_by: string | null;
}

export const fetchGroupProducts = () => call<GroupProduct[]>("/api/groups/products");
export const setGroupProduct = (channel: Channel, group_id: string, products: ProductCode[] | null) =>
  put<void>("/api/groups/product", { channel, group_id, products });
/** 建议卡上改这条建议查知识的产品，按新产品重新生成 */
export const setSuggestionProduct = (id: string, products: ProductCode[]) =>
  post<Suggestion>(`/api/suggestions/${id}/product`, { products });

/** 把进行中的会话转给同事 */
export const assignConversation = (channel: Channel, group_id: string, staff_id: string) =>
  post<StaffBrief>("/api/conversations/assign", { channel, group_id, staff_id });

// ---------- 候选知识 ----------

export type Branch = "有数" | "知数" | "通用";
export type KnowledgeKind = "how_to" | "fact" | "troubleshoot" | "not_supported";
export const KIND_LABEL: Record<KnowledgeKind, string> = {
  how_to: "操作做法",
  fact: "结论说明",
  troubleshoot: "排查处理",
  not_supported: "不支持及替代",
};

export interface Candidate {
  id: string;
  suggestion_id: string;
  status: CandidateStatus;
  title: string;
  question: string;
  answer: string;
  branch: Branch;
  kind: KnowledgeKind;
  module: string;
  /** AI 判断是否通用的依据 */
  reason: string | null;
  customer_question: string;
  /** 客户实际收到的回复 */
  customer_reply: string;
  ai_suggestion: string | null;
  group_name: string | null;
  author: string | null;
  confirmed_by: string | null;
  confirmed_at: string | null;
  reviewer: string | null;
  reviewed_at: string | null;
  review_note: string | null;
  updated_by: string | null;
  /** 写入售后数字人的纠错卡编号；知数暂不支持写入 */
  card_id: string | null;
  synced_version: string | null;
  synced_status: string | null;
  synced_at: string | null;
  created_at: string;
  channel: Channel | null;
  group_id: string | null;
  message_id: string | null;
}

export type CandidateView = "proposed" | "pending" | "approved" | "rejected" | "skipped";

export interface CandidateEdit {
  title?: string;
  question?: string;
  answer?: string;
  branch?: Branch;
  kind?: KnowledgeKind;
  module?: string;
  note?: string;
}

export function fetchCandidates(view: CandidateView, author?: string): Promise<Candidate[]> {
  const qs = new URLSearchParams({ view });
  if (author) qs.set("author", author);
  return call(`/api/candidates?${qs}`);
}
/** 知识库角标：等我确认的、等我审核的 */
export const fetchCandidateCounts = () =>
  call<{ mine: number; pending: number; by_status: Record<string, number> }>("/api/candidates/counts");
export const candidateAction = (
  id: string,
  action: "confirm" | "decline" | "approve" | "reject" | "retire",
  edit: CandidateEdit = {},
) => post<Candidate>(`/api/candidates/${id}/${action}`, edit);

// ---------- 待补知识 ----------

export type GapState = "todo" | "filled" | "skipped";
export type GapView = GapState | "all";

/** AI 没答上的一轮客户问题 */
export interface Gap {
  suggestion_id: string;
  message_id: string;
  channel: Channel;
  group_id: string;
  group_name: string | null;
  /** 这一轮客户的原话 */
  customer_text: string;
  /** AI 结合上下文补全的问题 */
  question: string | null;
  ai_suggestion: string | null;
  can_answer: boolean | null;
  ai_reason: string | null;
  knowledge_found: number;
  knowledge_cited: number;
  /** 知识库没查成或没查的原因 */
  knowledge_problem: string | null;
  status: SuggestionStatus;
  final_content: string | null;
  handled_by: string | null;
  note: string | null;
  missed: Missed;
  missed_by: string | null;
  missed_at: string;
  candidate: { id: string; status: CandidateStatus; question: string } | null;
  /** todo 待补 / filled 已补上（整理的知识审核通过）/ skipped 不用补（个案、无需回复） */
  state: GapState;
}
export const fetchGaps = (view: GapView) =>
  call<{ items: Gap[]; counts: Record<GapState, number> }>(`/api/gaps?view=${view}`);

// ---------- 采纳统计 ----------

export interface AdoptionRow {
  operator: string;
  replied: number;
  exact: number;
  minor: number;
  major: number;
  unused: number;
  via_chat: number;
  /** AI 没答上（大改、自己写、转给同事求助） */
  missed: number;
  dismissed: number;
  knowledge_submitted: number;
  knowledge_approved: number;
  reviews: number;
}
export interface AdoptionWeek {
  /** 这周周一 YYYY-MM-DD */
  week: string;
  total: AdoptionRow;
  people: AdoptionRow[];
}
export const fetchAdoptionStats = (weeks = 8) => call<{ weeks: AdoptionWeek[] }>(`/api/stats/adoption?weeks=${weeks}`);

// ---------- 回复建议评测 ----------

/** 分块：分数按块分开算，数据中台的知识还没接进售后数字人时单独看 */
export type EvalArea = "BI+知数" | "数据中台" | "通用";
export const EVAL_AREAS: EvalArea[] = ["BI+知数", "数据中台", "通用"];

export interface EvalSet {
  name: string;
  total: number;
  areas: Partial<Record<EvalArea, number>>;
  kinds: Record<string, number>;
  updated_at: string | null;
  /** 标了待复核、暂不参与评测的题 */
  reviewing: number;
}

export interface EvalBucket {
  area: EvalArea;
  kind: string | null;
  total: number;
  done: number;
  passed: number;
  errors: number;
  /** 打分没成功的 */
  unjudged: number;
  /** 带依据文档的题数、检索查到依据的题数 */
  retrieval_total: number;
  retrieval_hit: number;
  /** 对 / 部分对 / 错 / 没答上；是否需要回复题为 判断对 / 判断错 */
  verdicts: Record<string, number>;
}

export type EvalStatus = "running" | "stopping" | "stopped" | "done";

export interface EvalRun {
  id: string;
  set_name: string;
  areas: EvalArea[] | null;
  status: EvalStatus;
  total: number;
  done: number;
  started_by: string | null;
  started_at: string;
  finished_at: string | null;
  model: string | null;
  note: string | null;
  summary: EvalBucket[];
}

export interface EvalRunDetail extends EvalRun {
  kinds: EvalBucket[];
  previous: { run_id: string; started_at: string; summary: EvalBucket[]; better: number; worse: number } | null;
}

export interface EvalResult {
  /** AI 结合上下文补全的问题（多轮对话题才有，和题目原文不同时显示） */
  understood_question?: string | null;
  case_id: string;
  code: string;
  kind: string;
  area: EvalArea;
  branch: string;
  question: string;
  context: string;
  expected: string;
  must_not: string;
  reference: string;
  expect_reply: boolean | null;
  data: { source?: string; signal?: string; basis?: string[]; alt_questions?: string[]; audience?: string; card?: string } | null;
  done: boolean;
  error: string | null;
  reply: string | null;
  can_answer: boolean | null;
  confidence: string | null;
  needs_reply: boolean | null;
  reason: string | null;
  knowledge: KnowledgeRef[] | null;
  knowledge_error: string | null;
  retrieval_rank: number | null;
  has_expected_docs: boolean;
  verdict: string | null;
  verdict_reason: string | null;
  passed: boolean | null;
  previous_passed: boolean | null;
  seconds: number | null;
}

export const fetchEvalSets = () => call<EvalSet[]>("/api/eval/sets");
export const fetchEvalRuns = () => call<EvalRun[]>("/api/eval/runs");
export const fetchEvalRun = (id: string) => call<EvalRunDetail>(`/api/eval/runs/${id}`);
export const fetchEvalResults = (id: string) => call<EvalResult[]>(`/api/eval/runs/${id}/results`);
export const startEval = (set_name: string, areas: EvalArea[] | null, note: string) =>
  post<EvalRun>("/api/eval/runs", { set_name, areas, note: note || null });
export const stopEval = (id: string) => post<EvalRun>(`/api/eval/runs/${id}/stop`);

export async function importEvalSet(name: string, file: File) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`/api/eval/sets/${encodeURIComponent(name)}/import`, { method: "POST", body: form });
  if (!res.ok) throw await failure(res);
  return (await res.json()) as { name: string; added: number; updated: number; retired: number; reviewing: number };
}

// ---------- 客户档案 ----------

export interface CustomerEnvInstance {
  /** 生产、测试，或运维门户里起的环境名 */
  env: string;
  version: string | null;
  /** 运维门户里确认版本的日期 */
  confirmed_at: string | null;
}

export interface CustomerEnv {
  /** 有数BI / 知数 / 指标平台 / 数据中台 */
  product: string;
  tech_support: string | null;
  ops_support: string | null;
  instances: CustomerEnvInstance[];
}

export interface CustomerMaintenance {
  line: string;
  /** 有数BI / 数据中台 */
  label: string;
  /** 在保 / 半过期 / 过期 / 流失 / 交付 */
  status: string | null;
  /** 如 1在保+2过期 */
  structure: string | null;
  next_expire: string | null;
  expire: string | null;
}

export interface CustomerProfile {
  name: string;
  /** 签约公司，可能有多家（含集成商、代理商） */
  companies: string[];
  level: string | null;
  industry: string | null;
  sales: string | null;
  purchased: string[];
  business_status: string | null;
  maintenance: CustomerMaintenance[];
  environments: CustomerEnv[];
  cluster_type: string | null;
  synced_at: string;
}

export interface CustomerNote {
  id: string;
  /** note 团队补充 / error 资料有误 */
  kind: "note" | "error";
  content: string;
  author: string;
  author_id: string | null;
  created_at: string;
  updated_at: string;
}

export interface ServiceRecord {
  id: number;
  served_on: string;
  category: string | null;
  description: string | null;
  solution: string | null;
  /** 0 待跟进 / 1 已解决 / 2 部分解决 / null 未评定 */
  resolved: number | null;
  staff_name: string | null;
}

export interface CustomerCard {
  group_name: string | null;
  link: {
    customer: string | null;
    /** bind 售后系统群绑定 / tasks 任务表群对照 / manual 人工选 */
    source: string | null;
    candidates: string[];
    updated_by: string | null;
  };
  profile: CustomerProfile | null;
  /** 只给内部看的提醒，如这个群对应产品线的维保已过期 */
  alerts: string[];
  notes: CustomerNote[];
  records: ServiceRecord[];
  record_total: number;
  sync: { configured: boolean; synced_at: string | null; error: string | null; maintenance_updated_on: string | null };
  /** 能不能改这个群对应的客户（管理员、群负责人） */
  can_link: boolean;
}

const groupQs = (channel: Channel, groupId: string, extra: Record<string, string> = {}) =>
  new URLSearchParams({ channel, group_id: groupId, ...extra }).toString();

export const fetchCustomerCard = (channel: Channel, groupId: string) =>
  call<CustomerCard>(`/api/customers/card?${groupQs(channel, groupId)}`);
export const fetchServiceRecords = (channel: Channel, groupId: string, offset: number, limit = 50) =>
  call<ServiceRecord[]>(`/api/customers/records?${groupQs(channel, groupId, { offset: String(offset), limit: String(limit) })}`);
export const searchCustomers = (q: string) =>
  call<{ name: string; companies: string[] }[]>(`/api/customers/search?${new URLSearchParams({ q })}`);
export const setCustomerLink = (channel: Channel, group_id: string, customer: string | null) =>
  put<CustomerCard["link"]>("/api/customers/link", { channel, group_id, customer });
export const addCustomerNote = (channel: Channel, group_id: string, kind: "note" | "error", content: string) =>
  post<CustomerNote>("/api/customers/notes", { channel, group_id, kind, content });
export const editCustomerNote = (id: string, content: string) =>
  call<CustomerNote>(`/api/customers/notes/${id}`, { method: "PATCH", body: JSON.stringify({ content }) });
export const deleteCustomerNote = (id: string) => call<void>(`/api/customers/notes/${id}`, { method: "DELETE" });
export const syncCustomers = () => post<{ status: string }>("/api/customers/sync");

// ---------- 模型用量（token） ----------

export interface UsageTotals {
  calls: number;
  failed: number;
  estimated: number;
  input_tokens: number;
  output_tokens: number;
  cached_tokens: number;
  total_tokens: number;
  cost: number | null;
  unpriced_tokens: number;
}

export interface ModelPrice {
  model: string;
  input_price: number;
  output_price: number;
  cached_price: number | null;
  updated_by?: string | null;
  updated_at?: string | null;
}

export interface UsageSummary {
  day: string;
  today: string;
  online: UsageTotals;
  eval: UsageTotals;
  purposes: { key: string; label: string; online: UsageTotals; eval: UsageTotals }[];
  models: { model: string; priced: boolean; totals: UsageTotals }[];
  groups: { group_name: string; totals: UsageTotals }[];
  days: { date: string; online: UsageTotals; eval: UsageTotals }[];
  prices: ModelPrice[];
  tracking_since: string | null;
}

export const fetchUsage = (day?: string, days = 7) =>
  call<UsageSummary>(`/api/usage?days=${days}${day ? `&day=${day}` : ""}`);
export const savePrice = (model: string, p: { input_price: number; output_price: number; cached_price: number | null }) =>
  call<ModelPrice>(`/api/usage/prices/${encodeURIComponent(model)}`, { method: "PUT", body: JSON.stringify(p) });
export const clearPrice = (model: string) =>
  call<void>(`/api/usage/prices/${encodeURIComponent(model)}`, { method: "DELETE" });
