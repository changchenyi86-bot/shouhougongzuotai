// 演示模式：不连后台，接口全部用内置的假数据应答，只用来看界面效果。
import type {
  AdoptionRow,
  Attachment,
  Candidate,
  Channel,
  ChatTurn,
  ConversationEnd,
  Group,
  InboxItem,
  KnowledgeRef,
  Message,
  SenderKind,
  SendTask,
  Staff,
  Suggestion,
  Team,
} from "../api";

const at = (h: number, m: number) => {
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d.toISOString();
};

let seq = 0;

// 演示里登录的是管理员李四
const TEAMS: Team[] = [
  { id: "team-bi", name: "BI 组" },
  { id: "team-zt", name: "中台组" },
];
const seen = (minutesAgo: number) => new Date(Date.now() - minutesAgo * 60_000).toISOString();
function member(name: string, username: string, role: Staff["role"], team: Team | null, online: boolean, extra: Partial<Staff> = {}): Staff {
  return { id: `u-${username}`, name, username, role, team, active: true, status: "online", online, last_seen: seen(online ? 0 : 300), open_count: 0, has_password: true, ...extra };
}
const STAFF: Staff[] = [
  member("李四", "lisi", "admin", null, true),
  member("姜瑜", "jiangyu", "support", TEAMS[0], true),
  member("宋奔", "songben", "support", TEAMS[0], false, { status: "away" }),
  member("唐已杰", "tangyijie", "support", TEAMS[1], true),
  member("陆宣任", "luxuanren", "support", TEAMS[1], false),
];
const ME = STAFF[0];
const brief = (name: string) => {
  const s = STAFF.find((x) => x.name === name)!;
  return { id: s.id, name: s.name };
};
const key = (channel: string, groupId: string) => `${channel}:${groupId}`;

interface Room {
  group: Group;
  items: InboxItem[];
  outbox: SendTask[];
  ends: ConversationEnd[];
}

function room(channel: Channel, id: string, name: string | null, external: boolean, total: number): Room {
  return {
    group: {
      channel,
      group_id: id,
      group_name: name,
      is_external_group: external,
      last_message_at: at(8, 0),
      last_content: "",
      message_count: total,
      pending_count: 0,
      escalated_count: 0,
      owner: null,
      assignee: null,
      conversation_open: false,
    },
    items: [],
    outbox: [],
    ends: [],
  };
}

/** 演示里的客户：群里的昵称和来源。没列出的客户模拟查不到昵称的情况。 */
const PEOPLE: Record<string, [name: string, kind: SenderKind]> = {
  x9Kq: ["银行-王磊", "wecom"],
  p3Lm: ["sln", "wechat"],
  t7Wd: ["赵敏", "external"],
  k2Rn: ["孙经理", "external"],
  h5Bz: ["周婷", "external"],
  q8Fs: ["券商_陈伟", "wechat"],
};
const ID_PREFIX: Record<SenderKind, string> = { wechat: "wm", wecom: "wo", external: "ou_", internal: "" };

function say(r: Room, customer: string | null, h: number, m: number, content: string, extra: Partial<Message> = {}): Message {
  const id = `m${++seq}`;
  const [name, kind]: [string | null, SenderKind] = customer ? PEOPLE[customer] ?? [null, "wechat"] : ["李四", "internal"];
  const message: Message = {
    id,
    channel: r.group.channel,
    external_id: `e${id}`,
    group_id: r.group.group_id,
    group_name: r.group.group_name,
    is_external_group: r.group.is_external_group,
    sender_id: customer ? `${ID_PREFIX[kind]}DEMO${customer}0000000000000000000000` : "lisi",
    sender_name: name,
    msg_type: "text",
    content,
    sent_at: at(h, m),
    received_at: at(h, m),
    from_customer: !!customer,
    sender_kind: kind,
    attachment: null,
    media_error: null,
    ...extra,
  };
  r.items.push({ message, suggestion: null });
  return message;
}

function kn(
  ref: string, kind: string, branch: string, title: string, cited: boolean, url: string | null, card_status: string | null = null,
): KnowledgeRef {
  return { ref, kind, branch, title, source_id: `${ref}.md`, url, card_status, snippet: "", cited };
}

function suggest(r: Room, m: Message, status: Suggestion["status"], more: Partial<Suggestion> = {}) {
  const item = r.items.find((i) => i.message.id === m.id)!;
  item.suggestion = {
    id: `s-${m.id}`,
    message_id: m.id,
    status,
    content: null,
    can_answer: null,
    confidence: null,
    reason: null,
    model: "demo",
    error: null,
    knowledge: [],
    knowledge_error: null,
    final_content: null,
    delivery: null,
    send_status: null,
    send_error: null,
    sent_at: null,
    note: null,
    handled_by: null,
    handled_at: null,
    ...more,
  };
}

const SCREENSHOT = `<svg xmlns="http://www.w3.org/2000/svg" width="560" height="340" viewBox="0 0 560 340">
<rect width="560" height="340" fill="#fff"/><rect width="560" height="44" fill="#2f6bff"/>
<text x="20" y="28" font-family="sans-serif" font-size="15" fill="#fff">有数 · 报表中心</text>
<rect x="40" y="76" width="480" height="220" fill="#fff" stroke="#d9dce3"/>
<text x="64" y="118" font-family="sans-serif" font-size="14" fill="#e5484d">导出失败：请求超时 (504)</text>
<text x="64" y="150" font-family="sans-serif" font-size="13" fill="#333">报表：2026 年 Q3 网点经营日报</text>
<circle cx="280" cy="220" r="22" fill="none" stroke="#2f6bff" stroke-width="4" stroke-dasharray="100 40"/></svg>`;

/** 附件内容：演示里图片、文件都放在内存里。 */
const files = new Map<string, string>([
  ["demo-img", `data:image/svg+xml;charset=utf-8,${encodeURIComponent(SCREENSHOT)}`],
  ["demo-log", `data:text/plain;charset=utf-8,${encodeURIComponent("2026-11-19 10:07:12 ERROR export timeout (504)\n")}`],
  ...["demo-xlsx", "demo-docx", "demo-zip"].map((id): [string, string] => [id, `data:text/plain;charset=utf-8,${encodeURIComponent("演示文件")}`]),
]);

const candidates: Candidate[] = [];

function demoCandidate(r: Room, m: Message) {
  const s = r.items.find((i) => i.message.id === m.id)!.suggestion!;
  const c: Candidate = {
    id: `c-${m.id}`,
    suggestion_id: s.id,
    status: "proposed",
    title: "测试环境许可证快到期怎么续",
    question: "测试环境的许可证快到期了，怎么续期？",
    answer: "在「系统设置 → 许可证」里上传新的许可证文件，替换后重启服务即可生效。新的许可证文件由我们发到您的邮箱。",
    branch: "通用",
    kind: "how_to",
    module: "许可证",
    reason: "许可证续期的做法，别的客户也会问",
    customer_question: m.content,
    customer_reply: s.final_content ?? "",
    ai_suggestion: s.content,
    group_name: r.group.group_name,
    author: s.handled_by,
    confirmed_by: null,
    confirmed_at: null,
    reviewer: null,
    reviewed_at: null,
    review_note: null,
    updated_by: null,
    card_id: null,
    synced_version: null,
    synced_status: null,
    synced_at: null,
    created_at: at(8, 41),
    channel: r.group.channel,
    group_id: r.group.group_id,
    message_id: m.id,
  };
  candidates.push(c);
  s.candidate = { id: c.id, status: c.status, question: c.question };
}

const chats = new Map<string, ChatTurn[]>();

function demoStats(): AdoptionRow[] {
  const row = (operator: string, v: Partial<AdoptionRow>): AdoptionRow => ({
    operator, replied: 0, exact: 0, minor: 0, major: 0, unused: 0, via_chat: 0, escalated: 0, dismissed: 0,
    knowledge_submitted: 0, knowledge_approved: 0, reviews: 0, ...v,
  });
  return [
    row("李四", { replied: 18, exact: 9, minor: 4, major: 3, unused: 2, via_chat: 3, escalated: 1, dismissed: 2, knowledge_submitted: 2, knowledge_approved: 1, reviews: 1 }),
    row("姜瑜", { replied: 11, exact: 4, minor: 2, major: 2, unused: 3, via_chat: 1, escalated: 2, knowledge_submitted: 1, reviews: 2 }),
    row("AI", { dismissed: 7 }),
  ];
}

function build(): Room[] {
  const bank = room("wecom", "g1", "银行客户-有数项目群", true, 65);
  let m = say(bank, "x9Kq", 9, 58, "下周三我们准备升级到 6.2，有什么需要注意的吗？");
  suggest(bank, m, "adopted", { handled_by: "李四" });
  say(bank, null, 10, 1, "升级前建议先备份元数据库，6.2 的权限模型有调整，升级说明我稍后发群里。");
  m = say(bank, "x9Kq", 10, 3, "好的，升级前提醒我们一下");
  suggest(bank, m, "closed", { handled_by: "李四" });
  bank.ends.push({ id: "end-demo-1", ended_by: "李四", ended_at: at(10, 4), closed_count: 1 });
  say(bank, "p3Lm", 10, 5, "[图片]", {
    msg_type: "image",
    attachment: { id: "demo-img", filename: "截图.png", mime: "image/png", size: 182000 },
  });
  say(bank, null, 10, 6, "收到，我看一下。方便把导出报错的日志也发一下吗？");
  say(bank, "p3Lm", 10, 8, "[文件] 导出报错日志.txt", {
    msg_type: "file",
    attachment: { id: "demo-log", filename: "导出报错日志.txt", mime: "text/plain", size: 2100 },
  });
  say(bank, "x9Kq", 10, 8, "[文件] 浙江分行经营日报-BI报表.xlsx", {
    msg_type: "file",
    attachment: { id: "demo-xlsx", filename: "浙江分行经营日报-BI报表.xlsx", mime: "application/vnd.ms-excel", size: 99_300 },
  });
  say(bank, "x9Kq", 10, 8, "[文件] 6.2 升级方案.docx", {
    msg_type: "file",
    attachment: { id: "demo-docx", filename: "6.2 升级方案.docx", mime: "application/msword", size: 204_800 },
  });
  say(bank, "p3Lm", 10, 9, "[文件] 6231416b-e7b9-424f-a3c3-47c21ba522c3.zip", {
    msg_type: "file",
    attachment: { id: "demo-zip", filename: "6231416b-e7b9-424f-a3c3-47c21ba522c3.zip", mime: "application/zip", size: 19_503_000 },
  });
  say(bank, "p3Lm", 10, 9, "[文件] 元数据库全量备份.tar.gz", {
    msg_type: "file",
    media_error: "文件超过 20MB，未下载，请到企业微信里查看",
  });
  m = say(bank, "p3Lm", 10, 9, "你好，报表导出一直转圈怎么办？日志发上面了");
  suggest(bank, m, "ready", {
    content:
      "您好，从日志看是导出请求超时（504）。这张报表数据量较大，建议先在「导出设置」里把导出方式改成「后台导出」，导出完成后会在消息中心提醒您下载。如果仍然失败，麻烦告诉我们报表名称，我们帮您排查。",
    can_answer: true,
    confidence: "high",
    reason: "FAQ 里有导出超时的处理办法，日志里的 504 和它一致",
    knowledge: [
      kn("K2", "faq", "有数", "excel导出问题排查方法", true, "https://study.sf.163.com/documents/read/privatization/bgdc03"),
      kn("K1", "manual", "有数", "导出", true, "https://study.sf.163.com/documents/read/manual/daochu.md"),
      kn("K3", "card", "有数", "表格勾选显示序号后，导出不会带出序号", false, null, "draft"),
      kn("K4", "bug", "知数", "导出Excel失效", false, null),
    ],
  });

  const factory = room("dingtalk", "g2", "制造客户-知数交付群", true, 212);
  say(factory, "t7Wd", 9, 40, "我们这边新来了两位同事，账号已经建好了");
  m = say(factory, "t7Wd", 9, 47, "新同事看不到看板，是不是权限没开？");
  suggest(factory, m, "ready", {
    content:
      "您好，新账号默认没有看板的查看权限。请管理员在「权限管理 → 资源授权」里，把对应看板授权给这两位同事或他们所在的角色，授权后重新登录即可看到。",
    can_answer: true,
    confidence: "medium",
    reason: "纠错卡写明新账号要单独授权看板（卡片还是草稿）",
    knowledge: [kn("K1", "card", "知数", "新账号看不到看板：需要给账号或角色授权", true, null, "draft")],
  });

  const retail = room("feishu", "g3", "零售客户-有数运维群", true, 98);
  m = say(retail, "k2Rn", 9, 20, "昨天的数据和系统对不上，麻烦看下");
  suggest(retail, m, "escalated", {
    content: "您好，这个问题我们需要技术同事确认一下，稍后回复您。",
    can_answer: false,
    confidence: "low",
    reason: "涉及数据核对，建议转人工",
    note: "请数据组核对昨天的同步任务",
    handled_by: "李四",
  });
  m = say(retail, "k2Rn", 9, 31, "另外大屏今天早上打开特别慢");
  suggest(retail, m, "generating");
  m = say(retail, "h5Bz", 9, 35, "定时邮件今天没收到，是不是任务挂了？");
  suggest(retail, m, "ready", {
    content: "您好，定时邮件没收到一般是邮件服务或任务调度的问题，麻烦告诉我们是哪个订阅任务，我们查一下发送记录。",
    can_answer: false,
    confidence: "low",
    reason: "查到的资料讲的是订阅设置，和任务没发出不是一回事",
    knowledge: [
      kn("K1", "faq", "有数", "邮件订阅收不到邮件的排查", false, null),
      kn("K2", "manual", "有数", "订阅与推送", false, null),
    ],
  });

  const broker = room("wecom", "g4", "券商客户-知数实施群", true, 340);
  m = say(broker, "q8Fs", 8, 40, "测试环境的许可证快到期了，能帮忙续一下吗？");
  suggest(broker, m, "edited", {
    handled_by: "李四",
    content: "您好，许可证续期需要商务确认，我们这边先帮您申请。",
    final_content: "您好，测试环境许可证可以在「系统设置 → 许可证」里上传新的许可证文件，我们把文件发您邮箱，替换后重启服务即可生效。",
    adoption: "major",
  });
  demoCandidate(broker, m);
  m = say(broker, "q8Fs", 8, 52, "好的，收到，谢谢");
  suggest(broker, m, "dismissed", { auto_dismissed: true, needs_reply: false, reason: "客户在致谢，不需要回复", content: "不客气～" });
  broker.outbox.push({
    id: "t-demo-failed",
    suggestion_id: null,
    content: "新的许可证文件已经发到您邮箱，替换后重启服务即可生效。",
    attachments: [],
    status: "failed",
    error: "没有在企业微信里找到这个群，请确认客服账号还在群里",
    created_by: "王五",
    created_at: at(8, 55),
    finished_at: at(8, 56),
  });

  const team = room("wecom", "g5", "售后内部协作群", false, 41);
  say(team, null, 8, 20, "今天值班：李四、王五");

  // 企业微信查不到群名的外部客户群（没有客户联系查询权限），需要技术支持手动填写群名
  const unnamed = room("wecom", "wrOgQ1cDEMO7Fk3a", null, true, 3);
  m = say(unnamed, "u7Lp", 9, 48, "你好，看板分享出去的链接别人打不开，提示没有权限");
  suggest(unnamed, m, "ready", {
    content: "您好，分享链接需要对方有项目的查看权限。请在「项目设置 → 成员」里把对方加为查看者，或者改用公开链接分享。",
    can_answer: true,
    confidence: "high",
    reason: "常见权限问题",
  });

  // 负责人、处理人：宋奔离开了，零售群分给了同组的姜瑜；未命名的群还没有负责人，等分配
  const who = (r: Room, owner: string | null, assignee: string | null, open: boolean) =>
    Object.assign(r.group, { owner: owner ? brief(owner) : null, assignee: assignee ? brief(assignee) : null, conversation_open: open });
  who(bank, "姜瑜", "姜瑜", true);
  who(factory, "唐已杰", "唐已杰", true);
  who(retail, "宋奔", "姜瑜", true);
  who(broker, "唐已杰", null, false);
  who(unnamed, null, null, true);

  return [bank, factory, retail, broker, team, unnamed];
}

const rooms = build();
const PENDING: Suggestion["status"][] = ["generating", "ready", "failed"];
const OPEN: Suggestion["status"][] = [...PENDING, "escalated"];

function summary(r: Room): Group {
  const last = r.items[r.items.length - 1]?.message;
  return {
    ...r.group,
    last_message_at: last?.sent_at ?? r.group.last_message_at,
    last_content: last?.content ?? "",
    pending_count: r.items.filter((i) => i.suggestion && PENDING.includes(i.suggestion.status)).length,
    escalated_count: r.items.filter((i) => i.suggestion?.status === "escalated").length,
  };
}

function findSuggestion(id: string): Suggestion | undefined {
  for (const r of rooms) for (const i of r.items) if (i.suggestion?.id === id) return i.suggestion;
}

function findTask(id: string): { room: Room; task: SendTask } | undefined {
  for (const r of rooms) {
    const task = r.outbox.find((t) => t.id === id);
    if (task) return { room: r, task };
  }
}

/** 模拟发送电脑：排队 → 发送中 → 已发出 → 出现在聊天记录里。 */
function deliver(r: Room, task: SendTask) {
  const step = (ms: number, fn: () => void) => setTimeout(() => r.outbox.includes(task) && task.status !== "cancelled" && fn(), ms);
  step(1500, () => (task.status = "sending"));
  step(3500, () => (task.status = "sent"));
  step(6500, () => {
    r.outbox = r.outbox.filter((t) => t !== task);
    const now = new Date().toISOString();
    const who = { sender_name: task.created_by ?? "李四" };
    for (const a of task.attachments) {
      const m = say(r, null, 0, 0, a.mime.startsWith("image/") ? "[图片]" : "[文件]", { ...who, attachment: a });
      m.sent_at = m.received_at = now;
    }
    if (task.content) {
      const m = say(r, null, 0, 0, task.content, who);
      m.sent_at = m.received_at = now;
    }
  });
}

const handled = (s: Suggestion, status: Suggestion["status"]) => {
  s.status = status;
  s.handled_by = ME.name;
  s.handled_at = new Date().toISOString();
  return s;
};

async function handle(path: string, query: URLSearchParams, method: string, body: unknown): Promise<unknown> {
  const data = (body && typeof body === "object" && !(body instanceof FormData) ? body : {}) as Record<string, unknown>;

  if (path === "auth/state") return { needs_setup: false };
  if (path === "auth/me" || path === "auth/login" || path === "auth/setup") return ME;
  if (path === "auth/me/status") {
    ME.status = data.status as Staff["status"];
    ME.online = ME.status === "online";
    return ME;
  }
  if (path === "teams") return TEAMS;
  if (path === "staff" && method === "GET")
    return STAFF.map((s) => ({
      ...s,
      open_count: rooms.filter((r) => r.group.conversation_open && r.group.assignee?.id === s.id).length,
    }));
  if (path === "staff" && method === "POST") {
    const team = TEAMS.find((t) => t.id === data.team_id) ?? null;
    const s = member(String(data.name), String(data.username), data.role as Staff["role"], team, false, { last_seen: null });
    STAFF.push(s);
    return s;
  }
  const edit = path.match(/^staff\/([^/]+)$/);
  if (edit) {
    const s = STAFF.find((x) => x.id === edit[1])!;
    if (typeof data.name === "string") s.name = data.name;
    if (data.role) s.role = data.role as Staff["role"];
    if (data.clear_team) s.team = null;
    else if (data.team_id) s.team = TEAMS.find((t) => t.id === data.team_id) ?? null;
    if (typeof data.active === "boolean") s.active = data.active;
    return s;
  }

  if (path === "updates") return { version: "demo" };

  if (path === "send/status")
    return {
      enabled: true,
      ready: true,
      agents: [{ agent_id: "客服电脑", last_seen: new Date().toISOString(), wecom_ready: true, detail: null, version: "demo" }],
    };

  if (path === "groups") {
    const view = query.get("view");
    return rooms
      .map(summary)
      .filter((g) =>
        view === "todo"
          ? g.pending_count > 0
          : view === "escalated"
            ? g.escalated_count > 0
            : view === "unassigned"
              ? g.conversation_open && !g.assignee
              : true,
      )
      .sort((a, b) => b.last_message_at.localeCompare(a.last_message_at));
  }

  if (path === "groups/name" && method === "PUT") {
    const r = rooms.find((x) => key(x.group.channel, x.group.group_id) === key(String(data.channel), String(data.group_id)))!;
    const name = String(data.name ?? "").trim();
    r.group.group_name = name;
    for (const i of r.items) i.message.group_name = name;
    const duplicate = rooms.some((x) => x !== r && x.group.group_name === name);
    return { channel: r.group.channel, group_id: r.group.group_id, name, duplicate };
  }

  if (path === "conversation") {
    const r = rooms.find((x) => key(x.group.channel, x.group.group_id) === key(query.get("channel") ?? "", query.get("group_id") ?? ""));
    const paging = query.get("before") || query.get("after");
    return {
      items: r && !paging ? r.items : [],
      has_more_before: false,
      has_more_after: false,
      outbox: r?.outbox ?? [],
      ends: r?.ends ?? [],
      owner: r?.group.owner ?? null,
      assignee: r?.group.assignee ?? null,
      conversation_open: r?.group.conversation_open ?? false,
    };
  }

  if (path === "groups/owner") {
    const r = rooms.find((x) => key(x.group.channel, x.group.group_id) === key(String(data.channel), String(data.group_id)))!;
    const owner = STAFF.find((x) => x.id === data.owner_id);
    r.group.owner = owner ? { id: owner.id, name: owner.name } : null;
    if (r.group.conversation_open && !r.group.assignee && owner) r.group.assignee = { id: owner.id, name: owner.name };
    return {};
  }

  if (path === "conversations/assign") {
    const r = rooms.find((x) => key(x.group.channel, x.group.group_id) === key(String(data.channel), String(data.group_id)))!;
    const to = STAFF.find((x) => x.id === data.staff_id)!;
    r.group.assignee = { id: to.id, name: to.name };
    return r.group.assignee;
  }

  if (path === "conversations/end") {
    const r = rooms.find((x) => key(x.group.channel, x.group.group_id) === key(String(data.channel), String(data.group_id)))!;
    const open = r.items.filter((i) => i.suggestion && OPEN.includes(i.suggestion.status));
    for (const i of open) handled(i.suggestion!, "closed");
    r.outbox = r.outbox.filter((t) => t.status !== "failed" && t.status !== "cancelled");
    const end: ConversationEnd = {
      id: `end${++seq}`,
      ended_by: ME.name,
      ended_at: new Date().toISOString(),
      closed_count: open.length,
    };
    r.ends.push(end);
    r.group.conversation_open = false;
    r.group.assignee = null;
    return end;
  }

  if (path === "attachments" && method === "POST" && body instanceof FormData) {
    const file = body.get("file") as File;
    const att: Attachment = { id: `up${++seq}`, filename: file.name, mime: file.type || "application/octet-stream", size: file.size };
    files.set(att.id, URL.createObjectURL(file));
    return att;
  }

  if (path === "conversations/send") {
    const r = rooms.find((x) => x.group.group_id === data.group_id)!;
    const s = typeof data.suggestion_id === "string" ? findSuggestion(data.suggestion_id) : undefined;
    if (s) handled(s, "adopted").final_content = String(data.content ?? "");
    const task: SendTask = {
      id: `t${++seq}`,
      suggestion_id: s?.id ?? null,
      content: String(data.content ?? ""),
      attachments: ((data.attachment_ids as string[]) ?? []).map((id) => uploaded.get(id)!).filter(Boolean),
      status: "queued",
      error: null,
      created_by: ME.name,
      created_at: new Date().toISOString(),
      finished_at: null,
    };
    r.outbox.push(task);
    deliver(r, task);
    return task;
  }

  let hit = path.match(/^suggestions\/([^/]+)\/chat$/);
  if (hit) {
    const turns = chats.get(hit[1]) ?? [];
    if (method === "POST") {
      const now = new Date().toISOString();
      const said = String(data.content ?? "");
      await new Promise((r) => setTimeout(r, 1200));
      turns.push({ id: `ct${++seq}`, role: "staff", content: said, note: null, operator: ME.name, created_at: now });
      turns.push({
        id: `ct${++seq}`,
        role: "ai",
        content: `您好，${said.replace(/^让(他|她|客户)/, "麻烦您")}。如果还是不行，麻烦把报错截图和操作步骤发我们，我们马上帮您排查。`,
        note: turns.length > 2 ? null : "按你说的方案写的，没有加别的步骤",
        operator: ME.name,
        created_at: now,
      });
      chats.set(hit[1], turns);
    }
    return turns;
  }

  hit = path.match(/^suggestions\/([^/]+)\/reopen$/);
  if (hit) {
    const s = findSuggestion(hit[1])!;
    Object.assign(s, { status: "ready", auto_dismissed: false, handled_by: null, reopened_by: ME.name });
    return s;
  }

  if (path === "candidates/counts")
    return {
      mine: candidates.filter((c) => c.status === "proposed" && c.author === ME.name).length,
      pending: candidates.filter((c) => c.status === "pending" && c.author !== ME.name && c.confirmed_by !== ME.name).length,
      by_status: {},
    };

  if (path === "candidates") {
    const view = query.get("view") ?? "pending";
    const statuses: Record<string, string[]> = { proposed: ["proposed"], pending: ["pending"], approved: ["approved"], rejected: ["rejected", "retired"], skipped: ["not_general", "declined"] };
    return candidates.filter((c) => statuses[view].includes(c.status) && (view !== "proposed" || c.author === query.get("author")));
  }

  hit = path.match(/^candidates\/([^/]+)\/(confirm|decline|approve|reject|retire)$/);
  if (hit) {
    const c = candidates.find((x) => x.id === hit![1])!;
    for (const k of ["title", "question", "answer", "branch", "kind", "module"] as const)
      if (typeof data[k] === "string") (c as unknown as Record<string, unknown>)[k] = data[k];
    const next = { confirm: "pending", decline: "declined", approve: "approved", reject: "rejected", retire: "retired" } as const;
    c.status = next[hit[2] as keyof typeof next];
    const now = new Date().toISOString();
    if (hit[2] === "confirm") Object.assign(c, { confirmed_by: ME.name, confirmed_at: now });
    if (hit[2] === "approve" || hit[2] === "reject")
      Object.assign(c, { reviewer: ME.name, reviewed_at: now, card_id: c.branch === "知数" ? null : `bi-verified-wb-${c.id}` });
    for (const r of rooms)
      for (const i of r.items)
        if (i.suggestion?.candidate?.id === c.id) i.suggestion.candidate = { id: c.id, status: c.status, question: c.question };
    return c;
  }

  if (path === "stats/adoption") {
    const weeks = Number(query.get("weeks") ?? 8);
    const monday = new Date();
    monday.setDate(monday.getDate() - ((monday.getDay() + 6) % 7));
    const zero = (): AdoptionRow => ({
      operator: "全部", replied: 0, exact: 0, minor: 0, major: 0, unused: 0, via_chat: 0, escalated: 0, dismissed: 0,
      knowledge_submitted: 0, knowledge_approved: 0, reviews: 0,
    });
    return {
      weeks: Array.from({ length: weeks }, (_, i) => {
        const d = new Date(monday);
        d.setDate(monday.getDate() - 7 * i);
        const people = i < 3 ? demoStats().map((p) => (i ? { ...p, exact: Math.max(0, p.exact - 2 * i), unused: p.unused + i } : p)) : [];
        const total = zero();
        for (const p of people) for (const k of Object.keys(total) as (keyof AdoptionRow)[]) if (k !== "operator") (total[k] as number) += p[k] as number;
        const week = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
        return { week, total, people };
      }),
    };
  }

  hit = path.match(/^suggestions\/([^/]+)\/(adopt|escalate|dismiss|regenerate)$/);
  if (hit) {
    const s = findSuggestion(hit[1])!;
    if (hit[2] === "adopt") {
      handled(s, "adopted").final_content = String(data.content ?? "");
      return { suggestion: s, delivery: "copy", detail: "" };
    }
    if (hit[2] === "escalate") handled(s, "escalated").note = (data.note as string) || null;
    if (hit[2] === "dismiss") handled(s, "dismissed");
    if (hit[2] === "regenerate") {
      s.status = "generating";
      setTimeout(() => s.status === "generating" && (s.status = "ready"), 2500);
    }
    return s;
  }

  hit = path.match(/^send-tasks\/([^/]+)\/(resend|cancel|mark-manual-sent)$/);
  if (hit) {
    const found = findTask(hit[1])!;
    const { room: r, task } = found;
    if (hit[2] === "resend") {
      task.status = "queued";
      task.error = null;
      deliver(r, task);
    } else if (hit[2] === "cancel") task.status = "cancelled";
    else r.outbox = r.outbox.filter((t) => t !== task);
    return task;
  }

  hit = path.match(/^messages\/([^/]+)\/fetch-media$/);
  if (hit) for (const r of rooms) for (const i of r.items) if (i.message.id === hit[1]) return i.message;

  return {};
}

const uploaded = new Map<string, Attachment>();

export function installMock() {
  const real = window.fetch.bind(window);
  window.fetch = async (input, init) => {
    const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(raw, location.href);
    const hit = url.pathname.match(/\/api\/(.+)$/);
    if (!hit) return real(input, init);
    let body: unknown = init?.body;
    if (typeof body === "string") {
      try {
        body = JSON.parse(body);
      } catch {
        /* 不是 JSON 就按原样处理 */
      }
    }
    const result = await handle(hit[1], url.searchParams, init?.method ?? "GET", body);
    if (hit[1] === "attachments") uploaded.set((result as Attachment).id, result as Attachment);
    // 深拷贝一份，页面拿到的数据不会被后面的模拟步骤悄悄改掉
    return new Response(JSON.stringify(result), { status: 200, headers: { "Content-Type": "application/json" } });
  };

  // 图片和文件的地址本来指向后台，演示里换成内存里的内容
  const patch = (root: ParentNode) => {
    root.querySelectorAll<HTMLImageElement>('img[src*="/api/attachments/"]').forEach((img) => {
      const data = files.get(img.getAttribute("src")!.split("/api/attachments/")[1].split("?")[0]);
      if (data) img.src = data;
    });
    root.querySelectorAll<HTMLAnchorElement>('a[href*="/api/attachments/"]').forEach((a) => {
      const data = files.get(a.getAttribute("href")!.split("/api/attachments/")[1].split("?")[0]);
      if (data) a.href = data;
    });
  };
  new MutationObserver(() => patch(document)).observe(document.documentElement, { childList: true, subtree: true });
}
