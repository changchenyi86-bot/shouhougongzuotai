// 演示模式里的客户档案：几家虚构的客户，群和客户的对应、团队补充都只存在内存里。
import type { CustomerCard, CustomerNote, CustomerProfile, ServiceRecord } from "../api";

const today = new Date().toISOString();
const day = (daysAgo: number) => new Date(Date.now() - daysAgo * 86_400_000).toISOString().slice(0, 10);

const PROFILES: CustomerProfile[] = [
  {
    name: "示例银行",
    companies: ["示例银行股份有限公司"],
    level: "重要",
    industry: "金融",
    sales: "王五",
    purchased: ["有数BI", "知数"],
    business_status: "售后",
    maintenance: [{ line: "BI", label: "有数BI", status: "在保", structure: "1在保", next_expire: day(-75), expire: day(-75) }],
    environments: [
      {
        product: "有数BI",
        tech_support: "姜瑜",
        ops_support: "唐已杰",
        instances: [
          { env: "生产", version: "V10.0-Update09", confirmed_at: day(12) },
          { env: "测试", version: "V10.0-Update10", confirmed_at: null },
        ],
      },
      { product: "知数", tech_support: "姜瑜", ops_support: null, instances: [] },
    ],
    cluster_type: null,
    synced_at: today,
  },
  {
    name: "示例证券",
    companies: ["示例证券股份有限公司", "示例信息技术有限公司"],
    level: "次重要",
    industry: "金融",
    sales: "赵六",
    purchased: ["数开平台", "知数"],
    business_status: "过保",
    maintenance: [{ line: "BD", label: "数据中台", status: "过期", structure: "2过期", next_expire: null, expire: day(40) }],
    environments: [
      {
        product: "数据中台",
        tech_support: "陆宣任",
        ops_support: "唐已杰",
        instances: [
          { env: "生产", version: "EasyData-V9.0-Update14", confirmed_at: day(30) },
          { env: "测试", version: null, confirmed_at: null },
        ],
      },
    ],
    cluster_type: "对接CDH/CDP",
    synced_at: today,
  },
  {
    name: "示例制造",
    companies: ["示例制造集团有限公司"],
    level: "普通",
    industry: "通用",
    sales: "钱七",
    purchased: ["知数"],
    business_status: "售后",
    maintenance: [{ line: "BI", label: "有数BI", status: "半过期", structure: "1在保+1过期", next_expire: day(-200), expire: day(-200) }],
    environments: [{ product: "知数", tech_support: "宋奔", ops_support: null, instances: [{ env: "生产", version: "V10.3", confirmed_at: null }] }],
    cluster_type: null,
    synced_at: today,
  },
];

const RECORDS: Record<string, ServiceRecord[]> = {
  g1: [
    { id: 3, served_on: day(1), category: "使用咨询", description: "报表导出 Excel 后打开提示文件损坏，指导清理缓存后重新导出", solution: "清理浏览器缓存后重新导出", resolved: 1, staff_name: "姜瑜" },
    { id: 2, served_on: day(6), category: "任务排查", description: "定时推送邮件没收到，核对收件人和推送日志", solution: "收件人配置有误，已更正", resolved: 2, staff_name: "姜瑜" },
    { id: 1, served_on: day(20), category: "运维服务", description: "测试环境升级到 V10.0-Update10 的时间安排", solution: "", resolved: 0, staff_name: "唐已杰" },
  ],
  g4: [
    { id: 4, served_on: day(3), category: "任务排查", description: "离线任务一直排队不执行，排查队列资源", solution: "", resolved: 0, staff_name: "陆宣任" },
  ],
};

// 群 → 客户：手动选的、自动对上的；没对上的群在这里没有
const LINKS: Record<string, CustomerCard["link"]> = {
  g1: { customer: "示例银行", source: "bind", candidates: [], updated_by: null },
  g2: { customer: "示例制造", source: "tasks", candidates: [], updated_by: null },
  g4: { customer: "示例证券", source: "bind", candidates: [], updated_by: null },
  wrOgQ1cDEMO7Fk3a: { customer: null, source: "bind", candidates: ["示例银行", "示例证券"], updated_by: null },
};

const NOTES: (CustomerNote & { customer: string | null; group: string })[] = [
  {
    id: "n1", kind: "note", content: "生产环境只能周五 20:00 以后变更，要提前一天发邮件给客户运维", author: "姜瑜", author_id: "u-jiangyu",
    created_at: today, updated_at: today, customer: "示例银行", group: "g1",
  },
];

let seq = 0;

function card(groupId: string, groupName: string | null): CustomerCard {
  const link = LINKS[groupId] ?? { customer: null, source: null, candidates: [], updated_by: null };
  const profile = PROFILES.find((p) => p.name === link.customer) ?? null;
  const alerts = (profile?.maintenance ?? []).filter((m) => m.status === "过期" || m.status === "流失").map((m) => `${m.label}维保${m.status}`);
  const records = RECORDS[groupId] ?? [];
  return {
    group_name: groupName,
    link,
    profile,
    alerts,
    notes: NOTES.filter((n) => n.group === groupId || (link.customer && n.customer === link.customer)),
    records: records.slice(0, 3),
    record_total: records.length,
    sync: { configured: true, synced_at: today, error: null, maintenance_updated_on: day(1) },
    can_link: true,
  };
}

/** 演示里的客户档案接口；不是客户档案的路径返回 undefined。 */
export function handleCustomers(
  path: string,
  query: URLSearchParams,
  method: string,
  data: Record<string, unknown>,
  groupName: (groupId: string) => string | null,
): unknown {
  const group = query.get("group_id") ?? String(data.group_id ?? "");
  if (path === "customers/card") return card(group, groupName(group));
  if (path === "customers/records") return Number(query.get("offset") ?? 0) > 0 ? [] : RECORDS[group] ?? [];
  if (path === "customers/search") {
    const q = query.get("q") ?? "";
    return PROFILES.filter((p) => p.name.includes(q) || p.companies.some((c) => c.includes(q))).map((p) => ({ name: p.name, companies: p.companies }));
  }
  if (path === "customers/link") {
    LINKS[group] = { customer: (data.customer as string) ?? null, source: "manual", candidates: [], updated_by: "李四" };
    for (const n of NOTES) if (n.group === group && !n.customer) n.customer = LINKS[group].customer;
    return LINKS[group];
  }
  if (path === "customers/notes" && method === "POST") {
    const note = {
      id: `n${100 + seq++}`, kind: data.kind as CustomerNote["kind"], content: String(data.content).trim(), author: "李四",
      author_id: "u-lisi", created_at: new Date().toISOString(), updated_at: new Date().toISOString(),
      customer: LINKS[group]?.customer ?? null, group,
    };
    NOTES.unshift(note);
    return note;
  }
  const hit = path.match(/^customers\/notes\/([^/]+)$/);
  if (hit) {
    const i = NOTES.findIndex((n) => n.id === hit[1]);
    if (method === "DELETE") {
      if (i >= 0) NOTES.splice(i, 1);
      return null;
    }
    if (i >= 0) Object.assign(NOTES[i], { content: String(data.content).trim(), updated_at: new Date().toISOString() });
    return NOTES[i];
  }
  if (path === "customers/sync") return { status: "started" };
  return undefined;
}
