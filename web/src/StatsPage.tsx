import { useEffect, useState, type ReactNode } from "react";
import { ADOPTION_LABEL, fetchAdoptionStats, type Adoption, type AdoptionRow, type AdoptionWeek } from "./api";

const PARTS: Adoption[] = ["exact", "minor", "major", "unused"];
const WEEKS = 8;

const pct = (n: number, d: number) => (d ? `${Math.round((n / d) * 100)}%` : "—");

function weekLabel(iso: string, index: number) {
  const start = new Date(`${iso}T00:00:00`);
  const end = new Date(start);
  end.setDate(start.getDate() + 6);
  const md = (d: Date) => `${d.getMonth() + 1}月${d.getDate()}日`;
  const range = start.getMonth() === end.getMonth() ? `${md(start)}–${end.getDate()}日` : `${md(start)}–${md(end)}`;
  return index === 0 ? `本周 · ${range}` : index === 1 ? `上周 · ${range}` : range;
}

/** 一条回复的去向：原样采纳 → 小改 → 大改 → 未用，颜色由深到浅。 */
function SplitBar({ row, tall = false }: { row: AdoptionRow; tall?: boolean }) {
  const total = PARTS.reduce((n, k) => n + row[k], 0);
  if (!total) return <div className={`split ${tall ? "tall" : ""} empty`} />;
  return (
    <div
      className={`split ${tall ? "tall" : ""}`}
      role="img"
      aria-label={PARTS.map((k) => `${ADOPTION_LABEL[k]} ${row[k]} 条`).join("，")}
    >
      {PARTS.filter((k) => row[k] > 0).map((k) => (
        <span
          key={k}
          className={`seg ${k}`}
          style={{ flexGrow: row[k] }}
          title={`${ADOPTION_LABEL[k]} ${row[k]} 条（${pct(row[k], total)}）`}
        >
          {tall && row[k] / total >= 0.12 && pct(row[k], total)}
        </span>
      ))}
    </div>
  );
}

/** 近 8 周原样采纳率的小折线，本周的点用主色。 */
function Sparkline({ weeks }: { weeks: AdoptionWeek[] }) {
  const pts = [...weeks].reverse().map((w) => (w.total.replied ? w.total.exact / w.total.replied : null));
  const W = 120;
  const H = 32;
  const x = (i: number) => 4 + (i * (W - 8)) / Math.max(1, pts.length - 1);
  const y = (v: number) => H - 4 - v * (H - 8);
  const path = pts
    .map((v, i) => (v === null ? null : `${x(i).toFixed(1)},${y(v).toFixed(1)}`))
    .reduce<string[][]>((runs, p) => {
      if (p === null) runs.push([]);
      else runs[runs.length - 1].push(p);
      return runs;
    }, [[]])
    .filter((r) => r.length > 1)
    .map((r) => `M${r.join("L")}`)
    .join("");
  const last = pts.length - 1;
  return (
    <svg className="spark" viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden>
      <path d={path} fill="none" />
      {pts.map((v, i) =>
        v === null ? null : (
          <circle key={i} cx={x(i)} cy={y(v)} r={i === last ? 3.5 : 2.5} className={i === last ? "now" : ""}>
            <title>{`${weekLabel(weeks[last - i].week, -1)}：原样采纳 ${Math.round(v * 100)}%`}</title>
          </circle>
        ),
      )}
    </svg>
  );
}

function Tile({ label, value, sub, children }: { label: string; value: string; sub?: string; children?: ReactNode }) {
  return (
    <div className="tile">
      <span className="tile-label">{label}</span>
      <div className="tile-row">
        <b className="tile-value">{value}</b>
        {children}
      </div>
      {sub && <span className="tile-sub">{sub}</span>}
    </div>
  );
}

/** 采纳统计：每周每人 AI 建议用得怎么样，以后积分排名也用这些数据。 */
export default function StatsPage() {
  const [weeks, setWeeks] = useState<AdoptionWeek[]>([]);
  const [index, setIndex] = useState(0);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const load = () =>
      fetchAdoptionStats(WEEKS)
        .then((r) => {
          setWeeks(r.weeks);
          setError(null);
        })
        .catch((e: Error) => setError(e.message));
    load();
    const t = setInterval(load, 60_000);
    return () => clearInterval(t);
  }, []);

  if (error && !weeks.length) return <div className="page-panel"><p className="error pad">加载失败：{error}</p></div>;
  if (!weeks.length) return <div className="page-panel"><p className="empty pad">加载中…</p></div>;

  const week = weeks[index];
  const prev = weeks[index + 1];
  const t = week.total;
  const rate = t.replied ? t.exact / t.replied : null;
  const prevRate = prev?.total.replied ? prev.total.exact / prev.total.replied : null;
  const delta = rate !== null && prevRate !== null ? Math.round((rate - prevRate) * 100) : null;

  return (
    <div className="page-panel stats">
      <div className="stats-head">
        <div className="week-nav">
          <button className="link" disabled={index >= weeks.length - 1} onClick={() => setIndex(index + 1)} aria-label="上一周">‹</button>
          <b>{weekLabel(week.week, index)}</b>
          <button className="link" disabled={index === 0} onClick={() => setIndex(index - 1)} aria-label="下一周">›</button>
        </div>
      </div>

      <div className="tiles">
        <Tile
          label="原样采纳率"
          value={rate === null ? "—" : `${Math.round(rate * 100)}%`}
          sub={delta === null ? "AI 建议一字不改直接发出的比例" : `比上周${delta >= 0 ? "高" : "低"} ${Math.abs(delta)} 个百分点`}
        >
          <Sparkline weeks={weeks.slice(index, index + WEEKS)} />
        </Tile>
        <Tile label="回复客户" value={`${t.replied}`} sub={`小改 ${t.minor} · 大改 ${t.major} · 未用 ${t.unused}`} />
        <Tile label="经 AI 对话润色" value={`${t.via_chat}`} sub="客服说要点，AI 写成回复" />
        <Tile label="沉淀为知识" value={`${t.knowledge_approved}`} sub={`本周提交 ${t.knowledge_submitted} 条，审核 ${t.reviews} 次`} />
      </div>

      <section className="split-card">
        <div className="split-title">
          <span>回复里 AI 建议的去向</span>
          <ul className="legend">
            {PARTS.map((k) => (
              <li key={k}>
                <i className={`seg ${k}`} />
                {ADOPTION_LABEL[k]}
              </li>
            ))}
          </ul>
        </div>
        <SplitBar row={t} tall />
      </section>

      {week.people.length === 0 ? (
        <p className="empty pad">这周还没有处理记录</p>
      ) : (
        <div className="stats-table-wrap">
          <table className="stats-table">
            <thead>
              <tr>
                <th>处理人</th>
                <th className="num">回复</th>
                <th className="bar-col">分布</th>
                <th className="num">原样采纳</th>
                <th className="num">小改</th>
                <th className="num">大改</th>
                <th className="num">未用</th>
                <th className="num">原样采纳率</th>
                <th className="num">经 AI 对话</th>
                <th className="num" title="大改或没用 AI 建议、转给同事求助的，都进知识库的「待补知识」">AI 没答上</th>
                <th className="num">无需回复</th>
                <th className="num" title="自己的回复整理成的知识审核通过的 / 确认加入候选知识的">沉淀知识</th>
                <th className="num" title="审核过的候选知识">审核</th>
              </tr>
            </thead>
            <tbody>
              {week.people.map((r) => (
                <tr key={r.operator} className={r.operator === "AI" ? "ai-row" : ""}>
                  <td>{r.operator === "AI" ? "AI 自动标记" : r.operator}</td>
                  <td className="num">{r.replied || ""}</td>
                  <td className="bar-col">{r.replied > 0 && <SplitBar row={r} />}</td>
                  <td className="num">{r.exact || ""}</td>
                  <td className="num">{r.minor || ""}</td>
                  <td className="num">{r.major || ""}</td>
                  <td className="num">{r.unused || ""}</td>
                  <td className="num">{r.replied ? pct(r.exact, r.replied) : ""}</td>
                  <td className="num">{r.via_chat || ""}</td>
                  <td className="num">{r.missed || ""}</td>
                  <td className="num">{r.dismissed || ""}</td>
                  <td className="num" title={`通过 ${r.knowledge_approved} 条，提交 ${r.knowledge_submitted} 条`}>
                    {r.knowledge_approved || r.knowledge_submitted ? `${r.knowledge_approved}/${r.knowledge_submitted}` : ""}
                  </td>
                  <td className="num">{r.reviews || ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
