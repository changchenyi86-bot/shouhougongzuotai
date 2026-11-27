import { useCallback, useEffect, useState } from "react";
import { clearPrice, fetchUsage, savePrice, type ModelPrice, type UsageSummary, type UsageTotals } from "./api";

/** token 数写成「12.3 万」「1.25 亿」。 */
export function tokens(n: number): string {
  if (n >= 1e8) return `${(n / 1e8).toFixed(n >= 1e9 ? 1 : 2)} 亿`;
  if (n >= 1e4) return `${(n / 1e4).toFixed(n >= 1e6 ? 0 : 1)} 万`;
  return n.toLocaleString("zh-CN");
}

const REFRESH_MS = 30_000;
const hm = (d: Date) => d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });

const yuan = (n: number | null) => (n === null ? "—" : n < 0.01 && n > 0 ? "< ¥0.01" : `¥${n.toFixed(2)}`);
const pct = (n: number, d: number) => (d ? `${Math.round((n / d) * 100)}%` : "—");

function dayLabel(iso: string, today: string) {
  const d = new Date(`${iso}T00:00:00`);
  const md = `${d.getMonth() + 1}月${d.getDate()}日`;
  const diff = Math.round((new Date(`${today}T00:00:00`).getTime() - d.getTime()) / 86400000);
  return diff === 0 ? `今天 · ${md}` : diff === 1 ? `昨天 · ${md}` : md;
}

const shortDay = (iso: string) => {
  const d = new Date(`${iso}T00:00:00`);
  return `${d.getMonth() + 1}/${d.getDate()}`;
};

function costText(t: UsageTotals) {
  if (t.cost === null) return t.total_tokens ? "未填单价" : "—";
  return yuan(t.cost);
}

/** 近几天每天的消耗：线上（深）+ 评测（浅）叠在一起，点柱子看那天明细。 */
function Trend({ data, onPick }: { data: UsageSummary; onPick: (d: string) => void }) {
  const max = Math.max(1, ...data.days.map((d) => d.online.total_tokens + d.eval.total_tokens));
  return (
    <section className="usage-card">
      <div className="split-title">
        <span>近 {data.days.length} 天每天消耗</span>
        <ul className="legend">
          <li><i className="seg online" />线上</li>
          <li><i className="seg eval" />评测</li>
        </ul>
      </div>
      <div className="trend" role="list">
        {data.days.map((d) => {
          const total = d.online.total_tokens + d.eval.total_tokens;
          const tip = `${dayLabel(d.date, data.today)}：共 ${tokens(total)} token（线上 ${tokens(d.online.total_tokens)}，评测 ${tokens(d.eval.total_tokens)}）`;
          return (
            <button key={d.date} role="listitem" className={`trend-col ${d.date === data.day ? "on" : ""}`} title={tip}
              aria-label={tip} onClick={() => onPick(d.date)}>
              <span className="trend-value">{total ? tokens(total) : ""}</span>
              <span className="trend-bar">
                {d.eval.total_tokens > 0 && <i className="seg eval" style={{ height: `${(d.eval.total_tokens / max) * 100}%` }} />}
                {d.online.total_tokens > 0 && <i className="seg online" style={{ height: `${(d.online.total_tokens / max) * 100}%` }} />}
              </span>
              <span className="trend-day">{d.date === data.today ? "今天" : shortDay(d.date)}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function PriceEditor({ price, onSaved, notify }: { price: ModelPrice; onSaved: () => void; notify: (m: string) => void }) {
  const [input, setInput] = useState(String(price.input_price || ""));
  const [cached, setCached] = useState(price.cached_price === null ? "" : String(price.cached_price));
  const [output, setOutput] = useState(String(price.output_price || ""));
  const [busy, setBusy] = useState(false);
  const known = !!price.updated_at;
  const num = (s: string) => (s.trim() === "" ? NaN : Number(s));

  const save = async () => {
    const i = num(input), o = num(output), c = cached.trim() === "" ? null : num(cached);
    if (!(i >= 0) || !(o >= 0) || (c !== null && !(c >= 0))) {
      notify("单价要填数字（元 / 百万 token），缓存命中价可以空着");
      return;
    }
    setBusy(true);
    try {
      await savePrice(price.model, { input_price: i, output_price: o, cached_price: c });
      notify(`已保存 ${price.model} 的单价`);
      onSaved();
    } catch (e) {
      notify(`保存失败：${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <tr>
      <td className="mono">{price.model}</td>
      <td><input className="price-input" inputMode="decimal" value={input} onChange={(e) => setInput(e.target.value)} placeholder="0" aria-label="输入单价" /></td>
      <td><input className="price-input" inputMode="decimal" value={cached} onChange={(e) => setCached(e.target.value)} placeholder="同输入" aria-label="缓存命中单价" /></td>
      <td><input className="price-input" inputMode="decimal" value={output} onChange={(e) => setOutput(e.target.value)} placeholder="0" aria-label="输出单价" /></td>
      <td className="note-col">{known ? `${price.updated_by ?? ""} 填的` : "还没填"}</td>
      <td className="num">
        <button className="price-save" disabled={busy} onClick={save}>保存</button>
        {known && (
          <button className="link" disabled={busy} onClick={async () => {
            await clearPrice(price.model).catch(() => null);
            onSaved();
          }}>清除</button>
        )}
      </td>
    </tr>
  );
}

/** 模型用量：工作台自己调用模型用掉的 token（不含 Claude / Codex 的额度）。只有管理员能看。 */
export default function UsagePage({ notify }: { notify: (m: string) => void }) {
  const [data, setData] = useState<UsageSummary | null>(null);
  const [day, setDay] = useState<string | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [showPrices, setShowPrices] = useState(false);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(() => {
    setRefreshing(true);
    return fetchUsage(day)
      .then((r) => {
        setData(r);
        setError(null);
        setLoadedAt(new Date());
      })
      .catch((e: Error) => setError(e.message))
      .finally(() => setRefreshing(false));
  }, [day]);

  // 停在这页时每 30 秒刷新一次；切到别的标签页时不刷，切回来马上刷
  useEffect(() => {
    load();
    const t = setInterval(() => {
      if (!document.hidden) load();
    }, REFRESH_MS);
    const onVisible = () => {
      if (!document.hidden) load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      clearInterval(t);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  if (error && !data) return <div className="page-panel"><p className="error pad">加载失败：{error}</p></div>;
  if (!data) return <div className="page-panel"><p className="empty pad">加载中…</p></div>;

  const on = data.online;
  const ev = data.eval;
  const all = on.total_tokens + ev.total_tokens;
  const idx = data.days.findIndex((d) => d.date === data.day);
  const unpriced = on.unpriced_tokens + ev.unpriced_tokens;
  const anyCost = on.cost !== null || ev.cost !== null;
  const totalCost = (on.cost ?? 0) + (ev.cost ?? 0);
  const estimated = on.estimated + ev.estimated;
  // 单价表按这天用得多少排，没用到的放后面
  const rank = (m: string) => {
    const i = data.models.findIndex((x) => x.model === m);
    return i < 0 ? data.models.length : i;
  };
  const maxPurpose = Math.max(1, ...data.purposes.map((p) => p.online.total_tokens + p.eval.total_tokens));

  return (
    <div className="page-panel stats usage">
      <div className="stats-head">
        <div className="week-nav">
          <button className="link" disabled={idx <= 0} onClick={() => setDay(data.days[idx - 1]?.date)} aria-label="前一天">‹</button>
          <b>{dayLabel(data.day, data.today)}</b>
          <button className="link" disabled={data.day === data.today} onClick={() => {
            const next = data.days[idx + 1]?.date;
            setDay(!next || next === data.today ? undefined : next);
          }} aria-label="后一天">›</button>
        </div>
        <div className="usage-head-right">
          <span className="usage-note">
            只算工作台自己调用的模型（回复建议、识图、评测等），不含 Claude / Codex 的额度
            {data.tracking_since && `，从 ${new Date(data.tracking_since).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })} 开始记`}
          </span>
          <span className="usage-refresh" title="停在这页时每 30 秒自动刷新">
            {error ? <span className="error">刷新失败</span> : loadedAt && `${hm(loadedAt)} 更新`}
            <button className="refresh-btn" disabled={refreshing} onClick={() => load()}>
              {refreshing ? "刷新中…" : "刷新"}
            </button>
          </span>
        </div>
      </div>

      <div className="tiles">
        <div className="tile hero">
          <span className="tile-label">{data.day === data.today ? "今天一共" : "这天一共"}</span>
          <b className="tile-value">{tokens(all)}<small> token</small></b>
          <span className="tile-sub">输入 {tokens(on.input_tokens + ev.input_tokens)} · 输出 {tokens(on.output_tokens + ev.output_tokens)}
            {on.cached_tokens + ev.cached_tokens > 0 && ` · 输入里命中缓存 ${pct(on.cached_tokens + ev.cached_tokens, on.input_tokens + ev.input_tokens)}`}</span>
        </div>
        <div className="tile">
          <span className="tile-label">线上（服务客户）</span>
          <b className="tile-value">{tokens(on.total_tokens)}</b>
          <span className="tile-sub">{on.calls} 次调用{on.failed > 0 && `，失败 ${on.failed} 次`}</span>
        </div>
        <div className="tile">
          <span className="tile-label">评测</span>
          <b className="tile-value">{tokens(ev.total_tokens)}</b>
          <span className="tile-sub">{ev.calls ? `${ev.calls} 次调用，占全天 ${pct(ev.total_tokens, all)}` : "这天没跑评测"}</span>
        </div>
        <div className="tile">
          <span className="tile-label">估算费用</span>
          <b className="tile-value">{anyCost ? yuan(totalCost) : "—"}</b>
          <span className="tile-sub">
            {anyCost ? `线上 ${costText(on)} · 评测 ${costText(ev)}` : "还没填模型单价"}
            {unpriced > 0 && anyCost && `，另有 ${tokens(unpriced)} token 没填单价`}
            {" "}<button className="link inline" onClick={() => setShowPrices((v) => !v)}>{showPrices ? "收起单价" : "填单价"}</button>
          </span>
        </div>
      </div>

      {showPrices && (
        <section className="usage-card">
          <div className="split-title"><span>模型单价（元 / 百万 token，用来估算费用）</span></div>
          <div className="stats-table-wrap">
            <table className="stats-table">
              <thead>
                <tr><th>模型</th><th>输入</th><th>输入命中缓存</th><th>输出</th><th /><th /></tr>
              </thead>
              <tbody>
                {data.prices.length === 0 ? (
                  <tr><td colSpan={6} className="empty">还没有调用记录</td></tr>
                ) : (
                  [...data.prices].sort((a, b) => rank(a.model) - rank(b.model)).map((p) => <PriceEditor key={`${p.model}-${p.updated_at ?? ""}`} price={p} onSaved={load} notify={notify} />)
                )}
              </tbody>
            </table>
          </div>
          <p className="usage-note">按公司模型网关的计价填；自己部署的向量模型（bge-m3）不花钱可以不填。</p>
        </section>
      )}

      <Trend data={data} onPick={(d) => setDay(d === data.today ? undefined : d)} />

      <div className="usage-grid">
        <section className="usage-card">
          <div className="split-title"><span>按用途</span></div>
          {data.purposes.length === 0 ? (
            <p className="empty pad">这天没有调用记录</p>
          ) : (
            <div className="stats-table-wrap">
              <table className="stats-table">
                <thead>
                  <tr>
                    <th>用途</th>
                    <th className="bar-col">占比</th>
                    <th className="num">线上</th>
                    <th className="num">评测</th>
                    <th className="num">调用</th>
                    <th className="num">费用</th>
                  </tr>
                </thead>
                <tbody>
                  {data.purposes.map((p) => {
                    const t = p.online.total_tokens + p.eval.total_tokens;
                    const cost = p.online.cost === null && p.eval.cost === null ? null : (p.online.cost ?? 0) + (p.eval.cost ?? 0);
                    return (
                      <tr key={p.key}>
                        <td>{p.label}</td>
                        <td className="bar-col">
                          <span className="hbar" title={`占这天 ${pct(t, all)}`}>
                            {p.online.total_tokens > 0 && <i className="seg online" style={{ width: `${(p.online.total_tokens / maxPurpose) * 100}%` }} />}
                            {p.eval.total_tokens > 0 && <i className="seg eval" style={{ width: `${(p.eval.total_tokens / maxPurpose) * 100}%` }} />}
                          </span>
                          <small>{pct(t, all)}</small>
                        </td>
                        <td className="num">{p.online.total_tokens ? tokens(p.online.total_tokens) : ""}</td>
                        <td className="num">{p.eval.total_tokens ? tokens(p.eval.total_tokens) : ""}</td>
                        <td className="num" title={p.online.failed + p.eval.failed ? `失败 ${p.online.failed + p.eval.failed} 次` : undefined}>
                          {p.online.calls + p.eval.calls}
                        </td>
                        <td className="num">{cost === null ? "" : yuan(cost)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        <section className="usage-card">
          <div className="split-title"><span>按模型</span></div>
          {data.models.length === 0 ? (
            <p className="empty pad">这天没有调用记录</p>
          ) : (
            <div className="stats-table-wrap">
              <table className="stats-table">
                <thead>
                  <tr>
                    <th>模型</th>
                    <th className="num">输入</th>
                    <th className="num">输出</th>
                    <th className="num">合计</th>
                    <th className="num">费用</th>
                  </tr>
                </thead>
                <tbody>
                  {data.models.map((m) => (
                    <tr key={m.model}>
                      <td className="mono">{m.model}</td>
                      <td className="num" title={m.totals.cached_tokens ? `其中命中缓存 ${tokens(m.totals.cached_tokens)}` : undefined}>{tokens(m.totals.input_tokens)}</td>
                      <td className="num">{m.totals.output_tokens ? tokens(m.totals.output_tokens) : ""}</td>
                      <td className="num"><b>{tokens(m.totals.total_tokens)}</b></td>
                      <td className="num">{m.priced ? yuan(m.totals.cost) : <span className="muted">未填单价</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      {data.groups.length > 0 && (
        <section className="usage-card">
          <div className="split-title"><span>线上消耗最多的群</span></div>
          <div className="stats-table-wrap">
            <table className="stats-table">
              <thead>
                <tr><th>群</th><th className="num">token</th><th className="num">调用</th><th className="num">占线上</th></tr>
              </thead>
              <tbody>
                {data.groups.map((g) => (
                  <tr key={g.group_name}>
                    <td>{g.group_name}</td>
                    <td className="num">{tokens(g.totals.total_tokens)}</td>
                    <td className="num">{g.totals.calls}</td>
                    <td className="num">{pct(g.totals.total_tokens, on.total_tokens)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}

      {estimated > 0 && (
        <p className="usage-note">有 {estimated} 次调用接口没返回用量，按字数估算（识图没返回时只记次数）。</p>
      )}
    </div>
  );
}
