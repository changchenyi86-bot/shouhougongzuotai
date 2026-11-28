import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  EVAL_AREAS,
  fetchEvalResults,
  fetchEvalRun,
  fetchEvalRuns,
  fetchEvalSets,
  importEvalSet,
  startEval,
  stopEval,
  type EvalArea,
  type EvalBucket,
  type EvalResult,
  type EvalRun,
  type EvalRunDetail,
  type EvalSet,
  type Staff,
} from "./api";
import { KIND as KNOWLEDGE_KIND } from "./Knowledge";

const pct = (n: number, d: number) => (d ? `${Math.round((n / d) * 100)}%` : "—");
const rate = (b?: EvalBucket) => (b && b.done ? b.passed / b.done : null);
const AREA_LABEL: Record<EvalArea, string> = { "BI+知数": "有数 + 知数", 数据中台: "数据中台", 通用: "通用（是否需要回复）" };
const STATUS_LABEL: Record<string, string> = { running: "进行中", stopping: "正在停止", stopped: "已停止", done: "已完成" };
/** 题目里附的数字人当时的回答：答对类是被认可的，答错类是有问题的那条 */
const REFERENCE_LABEL: Record<string, string> = {
  确认答对: "数字人当时的回答（被认可）",
  多人问过: "数字人当时的回答（被认可）",
  确认答错: "数字人当时的回答（被点踩的那条）",
  被追问纠正: "数字人当时的回答（被纠正的那条）",
  数字人自认没覆盖: "数字人当时的回答",
};
const active = (r?: EvalRun | null) => !!r && (r.status === "running" || r.status === "stopping");

function when(iso: string) {
  const d = new Date(iso);
  return `${d.getMonth() + 1}月${d.getDate()}日 ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function bucket(list: EvalBucket[] | undefined, area: EvalArea) {
  return list?.find((b) => b.area === area);
}

/** 和上一轮比，答对率高了还是低了几个百分点 */
function delta(now?: EvalBucket, before?: EvalBucket) {
  const a = rate(now);
  const b = rate(before);
  if (a === null || b === null) return null;
  return Math.round((a - b) * 100);
}

function VerdictBadge({ r }: { r: EvalResult }) {
  if (!r.done) return <span className="badge muted">未跑</span>;
  if (r.error) return <span className="badge err" title={r.error}>没生成</span>;
  if (!r.verdict) return <span className="badge muted" title={r.verdict_reason ?? ""}>没打分</span>;
  const tone = r.passed ? "ok" : r.verdict === "部分对" ? "warn" : r.verdict === "没答上" ? "muted" : "err";
  return <span className={`badge ${tone}`}>{r.verdict}</span>;
}

function Tile({ label, value, sub, tone }: { label: string; value: string; sub: string; tone?: string }) {
  return (
    <div className={`tile ${tone ?? ""}`}>
      <span className="tile-label">{label}</span>
      <b className="tile-value">{value}</b>
      <span className="tile-sub">{sub}</span>
    </div>
  );
}

function StartDialog({ set, onClose, onStarted }: {
  set: EvalSet;
  onClose: () => void;
  onStarted: (r: EvalRun) => void;
}) {
  const [area, setArea] = useState<EvalArea | "all">("all");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const count = area === "all" ? set.total : set.areas[area] ?? 0;
  // 每道题起草 + 打分大约 12 秒，同时跑 2 道
  const minutes = Math.max(1, Math.round((count * 6) / 60));

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onStarted(await startEval(set.name, area === "all" ? null : [area], note.trim()));
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="dialog-mask" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="dialog eval-dialog" onSubmit={submit}>
        <h3>开始一轮评测</h3>
        <fieldset className="eval-scope">
          <legend>跑哪些题</legend>
          <label className="radio">
            <input type="radio" checked={area === "all"} onChange={() => setArea("all")} />
            全部 <small>{set.total} 道</small>
          </label>
          {EVAL_AREAS.filter((a) => set.areas[a]).map((a) => (
            <label key={a} className="radio">
              <input type="radio" checked={area === a} onChange={() => setArea(a)} />
              只跑{AREA_LABEL[a]} <small>{set.areas[a]} 道</small>
            </label>
          ))}
        </fieldset>
        <label>
          这一轮改了什么（选填，方便以后对比）
          <input value={note} maxLength={500} placeholder="如：换成 deepseek-flash；补了中台知识" onChange={(e) => setNote(e.target.value)} />
        </label>
        <p className="eval-hint">
          在后台直接拿题目生成建议再打分，不发到任何群，也不算进采纳统计。每道题调两次模型，{count} 道题大约 {minutes} 分钟。
        </p>
        {error && <p className="auth-error">{error}</p>}
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>取消</button>
          <button type="submit" className="primary" disabled={busy || !count}>开始</button>
        </div>
      </form>
    </div>
  );
}

/** 刚拖选了文字（想复制题目）时，松开鼠标不算点这一行 */
const selecting = () => !!window.getSelection()?.toString();

function ResultDetail({ r }: { r: EvalResult }) {
  const docs = (r.knowledge ?? []).filter((k) => k.kind !== "workbench" && k.kind !== "history");
  const similar = (r.knowledge ?? []).filter((k) => k.kind === "workbench" || k.kind === "history");
  return (
    <div className="eval-detail">
      <div className="eval-cols">
        <section>
          <h4>题目</h4>
          {r.context && <pre className="eval-context">{r.context}</pre>}
          <p className="eval-q">{r.question}</p>
          {!!r.data?.alt_questions?.length && (
            <p className="eval-meta">其他人的问法：{r.data.alt_questions.join(" ／ ")}</p>
          )}
          {r.expect_reply !== null ? (
            <p><b>期望：</b>{r.expect_reply ? "需要回复" : "不需要回复（寒暄、确认、致谢）"}</p>
          ) : (
            <>
              <p><b>期望答案要点：</b>{r.expected || "（没有单独写要点，按下面数字人当时被认可的回答判）"}</p>
              {r.must_not && <p><b>不应出现：</b>{r.must_not}</p>}
              {r.reference && <p className="eval-ref"><b>{REFERENCE_LABEL[r.kind] ?? "数字人当时的回答"}：</b>{r.reference}</p>}
            </>
          )}
          <p className="eval-meta">
            {[r.data?.source, r.data?.signal].filter(Boolean).join(" · ")}
          </p>
          {!!r.data?.basis?.length && (
            <ul className="eval-basis">
              {r.data.basis.map((b) => {
                const m = b.match(/^《(.+)》(https?:\/\/\S+)$/);
                return <li key={b}>{m ? <a href={m[2]} target="_blank" rel="noreferrer">{m[1]}</a> : b}</li>;
              })}
            </ul>
          )}
        </section>
        <section>
          <h4>
            AI 这次的建议
            {r.done && !r.error && (
              <small>
                {r.can_answer ? "有把握直接回复" : "没把握"}
                {r.needs_reply === false && " · 判断不用回复"}
                {r.seconds ? ` · ${r.seconds} 秒` : ""}
              </small>
            )}
          </h4>
          {!r.done ? (
            <p className="muted">还没跑到这道题</p>
          ) : r.error ? (
            <p className="error">{r.error}</p>
          ) : (
            <>
              {r.understood_question && r.understood_question.trim() !== r.question.trim() && (
                <p className="ai-question">
                  <b>AI 理解的问题</b>
                  <span>{r.understood_question}</span>
                </p>
              )}
              <p className="eval-reply">{r.reply}</p>
              {r.verdict_reason && (
                <p className={`eval-why ${r.passed ? "ok" : ""}`}><b>判分：{r.verdict ?? "没打分"}。</b>{r.verdict_reason}</p>
              )}
              <h5>
                查到的知识
                {r.has_expected_docs && (
                  <small className={r.retrieval_rank ? "ok-text" : "warn-text"}>
                    {r.retrieval_rank ? `依据文档排第 ${r.retrieval_rank} 条` : "没查到依据文档"}
                  </small>
                )}
              </h5>
              {r.knowledge_error && <p className="kn-error">{r.knowledge_error}</p>}
              {docs.length + similar.length === 0 ? (
                <p className="muted">没有查到参考知识</p>
              ) : (
                <ul className="eval-kn">
                  {[...docs, ...similar].map((k) => (
                    <li key={`${k.kind}-${k.source_id}`} className={k.cited ? "cited" : ""}>
                      <span className={`kn-kind ${k.kind === "card" || k.kind === "workbench" ? "verified" : ""}`}>{KNOWLEDGE_KIND[k.kind] ?? k.kind}</span>
                      <span className="kn-branch">{k.branch}</span>
                      {k.url ? <a href={k.url} target="_blank" rel="noreferrer">{k.title}</a> : <span className="kn-title">{k.title}</span>}
                      {k.cited && <small>引用了</small>}
                    </li>
                  ))}
                </ul>
              )}
            </>
          )}
        </section>
      </div>
    </div>
  );
}

/** 回复建议评测：一键拿题库跑一遍生成建议的流程，按「有数 + 知数」「数据中台」分开看答对多少，和上一轮比。 */
export default function EvalPage({ me, notify }: { me: Staff; notify: (t: string) => void }) {
  const admin = me.role === "admin";
  const [sets, setSets] = useState<EvalSet[] | null>(null);
  const [setName, setSetName] = useState<string>("");
  const [runs, setRuns] = useState<EvalRun[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<EvalRunDetail | null>(null);
  const [results, setResults] = useState<EvalResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const [areaFilter, setAreaFilter] = useState<EvalArea | "all">("all");
  const [kindFilter, setKindFilter] = useState("");
  const [onlyFailed, setOnlyFailed] = useState(false);
  const [onlyChanged, setOnlyChanged] = useState(false);
  const [query, setQuery] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);

  const loadSets = useCallback(() => {
    fetchEvalSets()
      .then((s) => {
        setSets(s);
        setSetName((cur) => cur || s[s.length - 1]?.name || "");
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  // 没手动点过历次评测时，一直看最新的一轮（别人新开了一轮也跟过去）
  const followLatest = useRef(true);
  const loadRuns = useCallback(() => {
    fetchEvalRuns()
      .then((rs) => {
        setRuns(rs);
        setRunId((cur) => (followLatest.current ? rs[0]?.id ?? null : cur));
      })
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    loadSets();
    loadRuns();
    const t = setInterval(loadRuns, 20_000);
    return () => clearInterval(t);
  }, [loadSets, loadRuns]);

  // 选中的这一轮：进行中时每 5 秒刷新进度，每 20 秒刷新逐题结果
  useEffect(() => {
    if (!runId) {
      setRun(null);
      setResults([]);
      return;
    }
    let stop = false;
    let tick = 0;
    const load = (withResults: boolean) => {
      fetchEvalRun(runId)
        .then((d) => {
          if (stop) return;
          setRun(d);
          if (!active(d)) loadRuns();
        })
        .catch((e: Error) => setError(e.message));
      if (withResults)
        fetchEvalResults(runId)
          .then((rs) => !stop && setResults(rs))
          .catch((e: Error) => setError(e.message));
    };
    load(true);
    const t = setInterval(() => {
      tick += 1;
      setRun((cur) => {
        if (active(cur)) load(tick % 4 === 0);
        return cur;
      });
    }, 5000);
    return () => {
      stop = true;
      clearInterval(t);
    };
  }, [runId, loadRuns]);

  // 跑完的那一刻再取一次逐题结果
  const finished = run && !active(run);
  useEffect(() => {
    if (finished && runId) fetchEvalResults(runId).then(setResults).catch(() => {});
  }, [finished, runId]);

  const set = sets?.find((s) => s.name === setName);
  const running = runs.find(active);
  const prev = run?.previous;

  const kinds = useMemo(() => Array.from(new Set(results.map((r) => r.kind))), [results]);
  const shown = useMemo(() => {
    const q = query.trim();
    return results.filter(
      (r) =>
        (areaFilter === "all" || r.area === areaFilter) &&
        (!kindFilter || r.kind === kindFilter) &&
        (!onlyFailed || (r.done && !r.passed)) &&
        (!onlyChanged || (r.done && r.previous_passed !== null && r.previous_passed !== r.passed)) &&
        (!q || r.question.includes(q) || r.code.includes(q)),
    );
  }, [results, areaFilter, kindFilter, onlyFailed, onlyChanged, query]);

  const onImport = async (file: File) => {
    const name = (setName || "v1").trim();
    try {
      const r = await importEvalSet(name, file);
      notify(
        `题库 ${r.name}：新增 ${r.added} 道，更新 ${r.updated} 道${r.retired ? `，停用 ${r.retired} 道` : ""}` +
          (r.reviewing ? `；${r.reviewing} 道待复核，暂不算分` : ""),
      );
      setSetName(r.name);
      loadSets();
    } catch (e) {
      notify(`导入失败：${(e as Error).message}`);
    }
  };

  const onStop = async () => {
    if (!run) return;
    try {
      setRun({ ...run, ...(await stopEval(run.id)) });
      notify("手上这几道题跑完就停");
    } catch (e) {
      notify(`停止失败：${(e as Error).message}`);
    }
  };

  if (error && !sets) return <div className="page-panel"><p className="error pad">加载失败：{error}</p></div>;
  if (!sets) return <div className="page-panel"><p className="empty pad">加载中…</p></div>;

  // 这一轮还没跑到这块时不显示 0%
  const sub = (b: EvalBucket | undefined, text: (b: EvalBucket) => string) =>
    !b ? "这一轮没跑这块" : b.done ? text(b) : "还没跑到";
  const bi = bucket(run?.summary, "BI+知数");
  const ed = bucket(run?.summary, "数据中台");
  const general = bucket(run?.summary, "通用");
  const dBi = delta(bi, bucket(prev?.summary, "BI+知数"));

  return (
    <div className="page-panel stats eval">
      <div className="eval-head">
        <div className="eval-set">
          <span className="muted">题库</span>
          {sets.length ? (
            <select value={setName} onChange={(e) => setSetName(e.target.value)}>
              {sets.map((s) => <option key={s.name} value={s.name}>{s.name} · {s.total} 道</option>)}
            </select>
          ) : (
            <b>还没有题目</b>
          )}
          {set && (
            <small className="muted">
              {EVAL_AREAS.filter((a) => set.areas[a]).map((a) => `${AREA_LABEL[a]} ${set.areas[a]}`).join(" · ")}
              {set.reviewing > 0 && ` · 另有 ${set.reviewing} 道待复核，暂不算分`}
            </small>
          )}
        </div>
        {admin && (
          <div className="head-actions">
            <input ref={fileInput} type="file" accept=".jsonl,.json" hidden onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = "";
              if (f) onImport(f);
            }} />
            <button onClick={() => fileInput.current?.click()} title={`导入到题库「${setName || "v1"}」：同编号的题覆盖，文件里没有了的题停用`}>导入题目</button>
            <button className="primary" disabled={!set || !!running} title={running ? "已经有一轮在跑" : ""} onClick={() => setStarting(true)}>
              开始评测
            </button>
          </div>
        )}
      </div>

      {!sets.length && (
        <p className="empty pad">
          {admin ? "点右上角「导入题目」，选评测集文件（.jsonl），再开始评测。" : "管理员还没导入题目。"}
        </p>
      )}

      {run && active(run) && (
        <div className="eval-progress">
          <div>
            <b>{run.status === "stopping" ? "正在停止" : "正在跑"}</b>　{run.done} / {run.total} 道
            <small className="muted">　{when(run.started_at)} {run.started_by} 发起，不发到任何群</small>
          </div>
          <div className="eval-bar"><i style={{ width: `${run.total ? (run.done / run.total) * 100 : 0}%` }} /></div>
          {admin && run.status === "running" && <button className="link" onClick={onStop}>停止</button>}
        </div>
      )}

      {run && (
        <>
          <div className="tiles">
            <Tile
              label="有数 + 知数 · 答对"
              value={bi ? pct(bi.passed, bi.done) : "—"}
              sub={sub(bi, (b) => `${b.done} 道里答对 ${b.passed} 道${dBi === null ? "" : dBi === 0 ? "，和上一轮持平" : `，比上一轮${dBi > 0 ? "高" : "低"} ${Math.abs(dBi)} 个百分点`}`)}
            />
            <Tile
              label="有数 + 知数 · 查到依据"
              value={bi ? pct(bi.retrieval_hit, bi.retrieval_total) : "—"}
              sub={sub(bi, (b) => `${b.retrieval_total} 道有依据文档的题，查到 ${b.retrieval_hit} 道`)}
            />
            <Tile
              label="数据中台 · 答对"
              value={ed ? pct(ed.passed, ed.done) : "—"}
              sub={sub(ed, (b) => `${b.done} 道里答对 ${b.passed} 道；中台知识还没接上，单独看`)}
              tone="aside"
            />
            <Tile
              label="是否需要回复 · 判断对"
              value={general ? pct(general.passed, general.done) : "—"}
              sub={sub(general, (b) => `${b.done} 道里判断对 ${b.passed} 道`)}
            />
          </div>

          <section className="eval-section">
            <div className="split-title">
              <span>
                {when(run.started_at)} · {run.started_by} · {STATUS_LABEL[run.status]}
                {run.model && ` · 模型 ${run.model}`}
                {run.note && ` · ${run.note}`}
              </span>
              {prev && (
                <span className="muted">
                  对比上一轮（{when(prev.started_at)}）：变好 <b className="ok-text">{prev.better}</b> 道，变差 <b className="warn-text">{prev.worse}</b> 道
                </span>
              )}
            </div>
            <div className="stats-table-wrap">
              <table className="stats-table">
                <thead>
                  <tr>
                    <th>分块</th>
                    <th>题型</th>
                    <th className="num">已跑</th>
                    <th className="num">对</th>
                    <th className="num">部分对</th>
                    <th className="num">错</th>
                    <th className="num">没答上</th>
                    <th className="num" title="建议没生成出来，或打分没成功">出错</th>
                    <th className="num">答对率</th>
                    <th className="num" title="题目带依据文档时，工作台检索有没有查到">查到依据</th>
                  </tr>
                </thead>
                <tbody>
                  {run.kinds.map((b, i) => {
                    const first = i === 0 || run.kinds[i - 1].area !== b.area;
                    const v = b.verdicts;
                    return (
                      <tr key={`${b.area}-${b.kind}`} className={first && i ? "area-start" : ""}>
                        <td>{first ? AREA_LABEL[b.area] : ""}</td>
                        <td>{b.kind}</td>
                        <td className="num">{b.done === b.total ? b.total : `${b.done}/${b.total}`}</td>
                        <td className="num">{(v["对"] ?? 0) + (v["判断对"] ?? 0) || ""}</td>
                        <td className="num">{v["部分对"] || ""}</td>
                        <td className="num">{(v["错"] ?? 0) + (v["判断错"] ?? 0) || ""}</td>
                        <td className="num">{v["没答上"] || ""}</td>
                        <td className="num">{b.errors + b.unjudged || ""}</td>
                        <td className="num"><b>{pct(b.passed, b.done)}</b></td>
                        <td className="num">{b.retrieval_total ? `${b.retrieval_hit}/${b.retrieval_total}` : ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </section>

          <section className="eval-section">
            <div className="eval-filters">
              <div className="subtabs">
                {(["all", ...EVAL_AREAS] as const).map((a) => (
                  <button key={a} className={areaFilter === a ? "on" : ""} onClick={() => setAreaFilter(a)}>
                    {a === "all" ? "全部" : AREA_LABEL[a]}
                  </button>
                ))}
              </div>
              <select value={kindFilter} onChange={(e) => setKindFilter(e.target.value)}>
                <option value="">全部题型</option>
                {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
              </select>
              <label className="check"><input type="checkbox" checked={onlyFailed} onChange={(e) => setOnlyFailed(e.target.checked)} />只看没通过</label>
              {prev && (
                <label className="check"><input type="checkbox" checked={onlyChanged} onChange={(e) => setOnlyChanged(e.target.checked)} />只看和上一轮不同</label>
              )}
              <input className="eval-search" placeholder="搜问题或编号" value={query} onChange={(e) => setQuery(e.target.value)} />
              <small className="muted">{shown.length} 道</small>
            </div>
            <div className="stats-table-wrap">
              <table className="stats-table eval-table">
                <thead>
                  <tr>
                    <th>编号</th>
                    <th>题型</th>
                    <th className="q-col">问题</th>
                    <th>这一轮</th>
                    {prev && <th>上一轮</th>}
                    <th>查到依据</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((r) => (
                    <Fragment key={r.case_id}>
                      <tr className={`clickable ${open === r.case_id ? "open" : ""}`} onClick={() => !selecting() && setOpen(open === r.case_id ? null : r.case_id)}>
                        <td className="code">{r.code}</td>
                        <td>{r.kind}</td>
                        <td className="q-col"><span className="q-text">{r.question}</span></td>
                        <td><VerdictBadge r={r} /></td>
                        {prev && (
                          <td>
                            {r.previous_passed === null ? "" : r.previous_passed ? <span className="ok-text">通过</span> : <span className="muted">没通过</span>}
                          </td>
                        )}
                        <td>{!r.has_expected_docs || !r.done || r.error ? "" : r.retrieval_rank ? `第 ${r.retrieval_rank} 条` : <span className="warn-text">没查到</span>}</td>
                      </tr>
                      {open === r.case_id && (
                        <tr className="detail-row">
                          <td colSpan={prev ? 6 : 5}><ResultDetail r={r} /></td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                  {!shown.length && (
                    <tr><td colSpan={prev ? 6 : 5} className="empty">没有符合条件的题</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}

      {sets.length > 0 && !run && (
        <p className="empty pad">{admin ? "还没跑过评测，点右上角「开始评测」。" : "还没跑过评测。"}</p>
      )}

      {runs.length > 1 && (
        <section className="eval-section">
          <div className="split-title"><span>历次评测</span></div>
          <div className="stats-table-wrap">
            <table className="stats-table">
              <thead>
                <tr>
                  <th>开始时间</th>
                  <th>发起人</th>
                  <th>题库</th>
                  <th>范围</th>
                  <th>状态</th>
                  <th className="num">有数 + 知数</th>
                  <th className="num">数据中台</th>
                  <th className="num">是否需要回复</th>
                  <th>模型</th>
                  <th>说明</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  const cell = (a: EvalArea) => {
                    const b = bucket(r.summary, a);
                    return b && b.done ? pct(b.passed, b.done) : "";
                  };
                  return (
                    <tr key={r.id} className={`clickable ${r.id === runId ? "open" : ""}`} onClick={() => { followLatest.current = r.id === runs[0]?.id; setRunId(r.id); setOpen(null); }}>
                      <td>{when(r.started_at)}</td>
                      <td>{r.started_by}</td>
                      <td>{r.set_name}</td>
                      <td>{r.areas ? r.areas.map((a) => AREA_LABEL[a]).join("、") : "全部"}</td>
                      <td>{STATUS_LABEL[r.status]}{active(r) ? ` ${r.done}/${r.total}` : ""}</td>
                      <td className="num">{cell("BI+知数")}</td>
                      <td className="num">{cell("数据中台")}</td>
                      <td className="num">{cell("通用")}</td>
                      <td>{r.model}</td>
                      <td className="note-col">{r.note}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {starting && set && (
        <StartDialog
          set={set}
          onClose={() => setStarting(false)}
          onStarted={(r) => {
            setStarting(false);
            setRuns((rs) => [r, ...rs]);
            followLatest.current = true;
            setRunId(r.id);
            setOpen(null);
            notify("开始跑了，可以先去忙别的，跑完这里会更新");
          }}
        />
      )}
    </div>
  );
}
