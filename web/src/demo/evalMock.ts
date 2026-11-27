// 演示模式里的评测页数据：几道示例题、两轮跑完的评测；新开一轮会在十几秒里跑完。
import type { EvalArea, EvalBucket, EvalResult, EvalRun, EvalRunDetail, KnowledgeRef } from "../api";

type Case = Omit<EvalResult, "done" | "error" | "reply" | "can_answer" | "confidence" | "needs_reply" | "reason" | "knowledge" |
  "knowledge_error" | "retrieval_rank" | "verdict" | "verdict_reason" | "passed" | "previous_passed" | "seconds">;

const doc = (kind: string, branch: string, title: string, source_id: string, cited = false): KnowledgeRef => ({
  ref: "", kind, branch, title, source_id, url: `https://study.sf.163.com/documents/read/privatization/${source_id}`,
  card_status: null, snippet: title, cited,
});

const c = (code: string, kind: string, area: EvalArea, question: string, expected: string, more: Partial<Case> = {}): Case => ({
  case_id: code, code, kind, area, branch: area === "BI+知数" ? "有数" : area === "数据中台" ? "数据中台" : "通用", question,
  context: "", expected, must_not: "", reference: "", expect_reply: null, has_expected_docs: false,
  data: { source: "售后数字人问答记录（示例）", signal: "提问人点了赞" }, ...more,
});

const CASES: Case[] = [
  c("EV-001", "确认答对", "BI+知数", "怎么设置报告的缓存有效期", "在报告的刷新计划里设置缓存有效期；数据更新后可手动刷新缓存", { has_expected_docs: true }),
  c("EV-002", "确认答对", "BI+知数", "表格、交叉表如何设置条件格式", "选中字段 → 右键 → 条件格式，支持背景颜色、内容颜色、条形图、标记", { has_expected_docs: true }),
  c("EV-003", "多人问过", "BI+知数", "怎么把测试项目下的报告迁入到生产项目下", "用报告的导出导入迁移；导入前确认数据连接和模型在目标项目里存在", { has_expected_docs: true, data: { source: "售后数字人问答记录（示例）", signal: "3 个不同提问人问过", alt_questions: ["报告怎么迁移到另一个项目"] } }),
  c("EV-004", "确认答错", "BI+知数", "表格中样式里面勾选的序号功能是否可以导出？", "导出时不会带出序号", { has_expected_docs: true, must_not: "说序号可以一起导出", data: { source: "售后数字人问答记录（示例）", signal: "提问人点了踩，说明：导出时不会带入序号" } }),
  c("EV-005", "被追问纠正", "BI+知数", "报告怎么导出tar包", "先弄清是报告还是数据表格；用户纠正说：不是数据表格，是 BI 报告", { data: { source: "售后数字人问答记录（示例）", signal: "提问人下一句就纠正了回答" } }),
  c("EV-006", "数字人自认没覆盖", "数据中台", "starrocks版本是多少", "知识库没有直接答案：如实说明没有覆盖，不编造，建议转技术支持", { data: { source: "售后数字人问答记录（示例）", signal: "数字人回答开头就说明知识库没有覆盖" } }),
  c("EV-007", "工作台真实群", "数据中台", "多个任务依赖同一个上游任务的话，会不会重复调起上游任务呢", "不会。上游只按自己的调度运行一次，下游都等它完成后再运行", { context: "客户：我们的任务现在是这么编排的\n技术支持：上下游任务之间设置依赖呀", data: { source: "工作台企业微信客户群（示例）", signal: "技术支持回复：不会" } }),
  c("EV-008", "是否需要回复", "通用", "好的，谢谢", "不需要回复（寒暄、确认、致谢）", { expect_reply: false, data: { source: "工作台企业微信客户群（示例）", signal: "按群里上下文人工判断" } }),
];

const REPLY: Record<string, [verdict: string, reply: string, why: string, rank: number | null, kn: KnowledgeRef[]]> = {
  "EV-001": ["对", "您好，报告的缓存有效期可以在报告的「刷新计划」里设置：打开报告 → 更多 → 刷新计划，选择缓存有效期即可。数据源有更新时也可以手动刷新缓存。", "和期望一致，给出了设置入口", 2, [doc("manual", "有数", "新建模型", "date20220123235709"), doc("faq", "有数", "有数的缓存与刷新", "lzs08", true)]],
  "EV-002": ["对", "您好，表格和交叉表的条件格式设置方式一样：选中要设置的字段，右键选择「条件格式」，可以设背景颜色、内容颜色、条形图和标记。", "要点齐全", 1, [doc("manual", "有数", "表格", "17table", true), doc("manual", "有数", "交叉表", "jiaochabiao")]],
  "EV-003": ["部分对", "您好，可以在测试项目里把报告导出，再到生产项目导入。", "给了导出导入，漏了导入前要先确认数据连接和模型", 4, [doc("manual", "有数", "指标设计", "zbsj"), doc("faq", "有数", "报告迁移导入注意事项", "bgdc07")]],
  "EV-004": ["对", "您好，表格样式里勾选的序号只在页面上显示，导出 Excel 时不会一起带出。", "结论正确", 1, [doc("card", "有数", "表格勾选显示序号后，导出不会带出序号", "bi-verified-table-index-export", true)]],
  "EV-005": ["错", "您好，数据表格可以在右上角导出为 tar 包……", "答成了数据表格，用户问的是报告", null, [doc("faq", "有数", "数据表格导出", "sjbg03")]],
  "EV-006": ["对", "您好，StarRocks 的版本需要看您环境的部署情况，我先帮您跟技术同学确认一下，稍后回复您。", "没有编造，说明需要确认", null, []],
  "EV-007": ["没答上", "您好，这个需要确认一下你们当前环境的任务依赖机制，麻烦发一下版本号……", "只追问了信息，没给出“不会”的结论", null, [doc("faq", "有数", "物化视图", "materialized")]],
  "EV-008": ["判断错", "不客气，有问题随时在群里说～", "期望不用回复，AI 判断要回复", null, []],
};

const BEFORE: Record<string, boolean> = { "EV-001": true, "EV-002": true, "EV-003": false, "EV-004": false, "EV-005": false, "EV-006": true, "EV-007": false, "EV-008": true };

function resultsOf(runId: string, done: number, previous: Record<string, boolean> | null): EvalResult[] {
  return CASES.map((cs, i) => {
    if (i >= done) return { ...cs, done: false, error: null, reply: null, can_answer: null, confidence: null, needs_reply: null, reason: null, knowledge: null, knowledge_error: null, retrieval_rank: null, verdict: null, verdict_reason: null, passed: null, previous_passed: previous ? previous[cs.code] ?? null : null, seconds: null };
    let [verdict, reply, why, rank, kn] = REPLY[cs.code];
    if (runId === "run-a") {
      // 上一轮：纠错卡还没写，序号那题答错了
      if (cs.code === "EV-004") [verdict, reply, why, rank] = ["错", "您好，序号可以随表格一起导出。", "说成了可以导出，和纠错卡相反", null];
      if (cs.code === "EV-008") [verdict, why] = ["判断对", "期望不用回复，AI 判断不用回复"];
    }
    const passed = verdict === "对" || verdict === "判断对";
    return {
      ...cs, done: true, error: null, reply, can_answer: verdict === "对", confidence: verdict === "对" ? "high" : "low",
      needs_reply: cs.expect_reply === null ? true : verdict === "判断对" ? cs.expect_reply : !cs.expect_reply,
      reason: why, knowledge: kn.map((k, n) => ({ ...k, ref: `K${n + 1}` })), knowledge_error: null,
      retrieval_rank: cs.has_expected_docs ? rank : null, verdict, verdict_reason: why, passed,
      previous_passed: previous ? previous[cs.code] ?? null : null, seconds: 9.6 + i,
    };
  });
}

function buckets(rs: EvalResult[], byKind: boolean): EvalBucket[] {
  const map = new Map<string, EvalBucket>();
  for (const r of rs) {
    const key = byKind ? `${r.area}|${r.kind}` : r.area;
    const b = map.get(key) ?? { area: r.area, kind: byKind ? r.kind : null, total: 0, done: 0, passed: 0, errors: 0, unjudged: 0, retrieval_total: 0, retrieval_hit: 0, verdicts: {} };
    b.total += 1;
    if (r.done) {
      b.done += 1;
      b.passed += r.passed ? 1 : 0;
      if (r.verdict) b.verdicts[r.verdict] = (b.verdicts[r.verdict] ?? 0) + 1;
      if (r.has_expected_docs) {
        b.retrieval_total += 1;
        b.retrieval_hit += r.retrieval_rank ? 1 : 0;
      }
    }
    map.set(key, b);
  }
  return [...map.values()];
}

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const runs: (EvalRun & { startedMs: number })[] = [
  { id: "run-b", set_name: "v1", areas: null, status: "done", total: 8, done: 8, started_by: "李四", started_at: ago(50), finished_at: ago(20), model: "deepseek-flash", note: "加了 BI 纠错卡", summary: [], startedMs: 0 },
  { id: "run-a", set_name: "v1", areas: null, status: "done", total: 8, done: 8, started_by: "李四", started_at: ago(26 * 60), finished_at: ago(25 * 60), model: "deepseek-flash", note: "基线", summary: [], startedMs: 0 },
];

function progress(run: (typeof runs)[number]) {
  if (run.status === "running" || run.status === "stopping") {
    run.done = Math.min(run.total, Math.floor((Date.now() - run.startedMs) / 1500));
    if (run.done >= run.total || run.status === "stopping") {
      run.status = run.status === "stopping" ? "stopped" : "done";
      run.finished_at = new Date().toISOString();
    }
  }
  return run;
}

function previousOf(run: EvalRun) {
  return runs.find((r) => r.started_at < run.started_at && (r.status === "done" || r.status === "stopped"));
}

function resultsFor(run: EvalRun) {
  const prev = previousOf(run);
  const before = prev ? Object.fromEntries(resultsOf(prev.id, prev.done, null).map((r) => [r.code, !!r.passed])) : null;
  return resultsOf(run.id, run.done, prev ? (prev.id === "run-a" ? BEFORE : before) : null);
}

function withSummary(run: (typeof runs)[number]): EvalRun {
  progress(run);
  const { startedMs: _ignored, ...rest } = run;
  return { ...rest, summary: buckets(resultsFor(run), false) };
}

export function handleEval(path: string, method: string, data: Record<string, unknown>): unknown {
  if (path === "eval/sets")
    return [{ name: "v1", total: CASES.length, areas: { "BI+知数": 5, 数据中台: 2, 通用: 1 }, kinds: {}, updated_at: ago(60) }];
  if (/^eval\/sets\/[^/]+\/import$/.test(path)) return { name: "v1", added: 0, updated: CASES.length, retired: 0 };
  if (path === "eval/runs" && method === "POST") {
    const run = { id: `run-${Date.now()}`, set_name: "v1", areas: (data.areas as EvalArea[] | null) ?? null, status: "running" as const, total: CASES.length, done: 0, started_by: "李四", started_at: new Date().toISOString(), finished_at: null, model: "deepseek-flash", note: (data.note as string) || null, summary: [], startedMs: Date.now() };
    runs.unshift(run);
    return withSummary(run);
  }
  if (path === "eval/runs") return runs.map(withSummary);
  let hit = path.match(/^eval\/runs\/([^/]+)\/stop$/);
  if (hit) {
    const run = runs.find((r) => r.id === hit![1])!;
    if (run.status === "running") run.status = "stopping";
    return withSummary(run);
  }
  hit = path.match(/^eval\/runs\/([^/]+)\/results$/);
  if (hit) return resultsFor(progress(runs.find((r) => r.id === hit![1])!));
  hit = path.match(/^eval\/runs\/([^/]+)$/);
  if (hit) {
    const run = runs.find((r) => r.id === hit![1])!;
    const out = withSummary(run);
    const rs = resultsFor(run);
    const prev = previousOf(run);
    const detail: EvalRunDetail = {
      ...out,
      kinds: buckets(rs, true),
      previous: prev
        ? {
            run_id: prev.id, started_at: prev.started_at, summary: buckets(resultsFor(prev), false),
            better: rs.filter((r) => r.done && r.passed && r.previous_passed === false).length,
            worse: rs.filter((r) => r.done && !r.passed && r.previous_passed === true).length,
          }
        : null,
    };
    return detail;
  }
  return undefined;
}
