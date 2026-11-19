// 演示模式：不连后台，接口全部用内置的假数据应答，只用来看界面效果。
import type { Attachment, Channel, Group, InboxItem, Message, SendTask, Suggestion } from "../api";

const at = (h: number, m: number) => {
  const d = new Date();
  d.setHours(h, m, 0, 0);
  return d.toISOString();
};

let seq = 0;
const key = (channel: string, groupId: string) => `${channel}:${groupId}`;

interface Room {
  group: Group;
  items: InboxItem[];
  outbox: SendTask[];
}

function room(channel: Channel, id: string, name: string, external: boolean, total: number): Room {
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
    },
    items: [],
    outbox: [],
  };
}

function say(r: Room, customer: string | null, h: number, m: number, content: string, extra: Partial<Message> = {}): Message {
  const id = `m${++seq}`;
  const message: Message = {
    id,
    channel: r.group.channel,
    external_id: `e${id}`,
    group_id: r.group.group_id,
    group_name: r.group.group_name,
    is_external_group: r.group.is_external_group,
    sender_id: customer ? `wmDEMO${customer}` : "lisi",
    sender_name: customer ? null : "李四",
    msg_type: "text",
    content,
    sent_at: at(h, m),
    received_at: at(h, m),
    from_customer: !!customer,
    attachment: null,
    media_error: null,
    ...extra,
  };
  r.items.push({ message, suggestion: null });
  return message;
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
]);

function build(): Room[] {
  const bank = room("wecom", "g1", "银行客户-有数项目群", true, 65);
  let m = say(bank, "x9Kq", 9, 58, "下周三我们准备升级到 6.2，有什么需要注意的吗？");
  suggest(bank, m, "adopted", { handled_by: "李四" });
  say(bank, null, 10, 1, "升级前建议先备份元数据库，6.2 的权限模型有调整，升级说明我稍后发群里。");
  m = say(bank, "x9Kq", 10, 3, "好的，升级前提醒我们一下");
  suggest(bank, m, "dismissed", { handled_by: "李四" });
  say(bank, "p3Lm", 10, 5, "[图片]", {
    msg_type: "image",
    attachment: { id: "demo-img", filename: "截图.png", mime: "image/png", size: 182000 },
  });
  say(bank, null, 10, 6, "收到，我看一下。方便把导出报错的日志也发一下吗？");
  say(bank, "p3Lm", 10, 8, "[文件]", {
    msg_type: "file",
    attachment: { id: "demo-log", filename: "导出报错日志.txt", mime: "text/plain", size: 2100 },
  });
  m = say(bank, "p3Lm", 10, 9, "你好，报表导出一直转圈怎么办？日志发上面了");
  suggest(bank, m, "ready", {
    content:
      "您好，从日志看是导出请求超时（504）。这张报表数据量较大，建议先在「导出设置」里把导出方式改成「后台导出」，导出完成后会在消息中心提醒您下载。如果仍然失败，麻烦告诉我们报表名称，我们帮您排查。",
    can_answer: true,
    confidence: "high",
    reason: "知识库命中：报表导出超时处理（3 次相似问题已解决）",
  });

  const factory = room("dingtalk", "g2", "制造客户-知数交付群", true, 212);
  say(factory, "t7Wd", 9, 40, "我们这边新来了两位同事，账号已经建好了");
  m = say(factory, "t7Wd", 9, 47, "新同事看不到看板，是不是权限没开？");
  suggest(factory, m, "ready", {
    content:
      "您好，新账号默认没有看板的查看权限。请管理员在「权限管理 → 资源授权」里，把对应看板授权给这两位同事或他们所在的角色，授权后重新登录即可看到。",
    can_answer: true,
    confidence: "medium",
    reason: "知识库命中：新用户看不到看板",
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
    content: "您好，这个问题我们需要技术同事确认一下，稍后回复您。",
    can_answer: false,
    confidence: "low",
    reason: "知识库里没有找到相似问题，建议转人工",
  });

  const broker = room("wecom", "g4", "券商客户-知数实施群", true, 340);
  m = say(broker, "q8Fs", 8, 40, "测试环境的许可证快到期了，能帮忙续一下吗？");
  suggest(broker, m, "adopted", { handled_by: "王五" });
  say(broker, "q8Fs", 8, 52, "好的，收到，谢谢");
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

  return [bank, factory, retail, broker, team];
}

const rooms = build();
const PENDING: Suggestion["status"][] = ["generating", "ready", "failed"];

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

const handled = (s: Suggestion, status: Suggestion["status"], operator: unknown) => {
  s.status = status;
  s.handled_by = typeof operator === "string" && operator ? operator : "李四";
  s.handled_at = new Date().toISOString();
  return s;
};

async function handle(path: string, query: URLSearchParams, method: string, body: unknown): Promise<unknown> {
  const data = (body && typeof body === "object" && !(body instanceof FormData) ? body : {}) as Record<string, unknown>;

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
      .filter((g) => (view === "todo" ? g.pending_count > 0 : view === "escalated" ? g.escalated_count > 0 : true))
      .sort((a, b) => b.last_message_at.localeCompare(a.last_message_at));
  }

  if (path === "conversation") {
    const r = rooms.find((x) => key(x.group.channel, x.group.group_id) === key(query.get("channel") ?? "", query.get("group_id") ?? ""));
    const paging = query.get("before") || query.get("after");
    return { items: r && !paging ? r.items : [], has_more_before: false, has_more_after: false, outbox: r?.outbox ?? [] };
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
    if (s) handled(s, "adopted", data.operator).final_content = String(data.content ?? "");
    const task: SendTask = {
      id: `t${++seq}`,
      suggestion_id: s?.id ?? null,
      content: String(data.content ?? ""),
      attachments: ((data.attachment_ids as string[]) ?? []).map((id) => uploaded.get(id)!).filter(Boolean),
      status: "queued",
      error: null,
      created_by: (data.operator as string) || "李四",
      created_at: new Date().toISOString(),
      finished_at: null,
    };
    r.outbox.push(task);
    deliver(r, task);
    return task;
  }

  let hit = path.match(/^suggestions\/([^/]+)\/(adopt|escalate|dismiss|regenerate)$/);
  if (hit) {
    const s = findSuggestion(hit[1])!;
    if (hit[2] === "adopt") {
      handled(s, "adopted", data.operator).final_content = String(data.content ?? "");
      return { suggestion: s, delivery: "copy", detail: "" };
    }
    if (hit[2] === "escalate") handled(s, "escalated", data.operator).note = (data.note as string) || null;
    if (hit[2] === "dismiss") handled(s, "dismissed", data.operator);
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
