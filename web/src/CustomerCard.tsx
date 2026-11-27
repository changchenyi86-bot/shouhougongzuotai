import { useEffect, useState } from "react";
import {
  addCustomerNote,
  deleteCustomerNote,
  editCustomerNote,
  fetchServiceRecords,
  searchCustomers,
  setCustomerLink,
  syncCustomers,
  type CustomerCard as Card,
  type CustomerNote,
  type ServiceRecord,
  type Staff,
} from "./api";
import type { ChatTarget } from "./Conversations";

const LINK_SOURCE: Record<string, string> = { bind: "售后系统的群绑定", tasks: "任务表的群对照", manual: "手动选的" };
const RESOLVED: Record<number, string> = { 0: "待跟进", 1: "已解决", 2: "部分解决" };
const GOOD = ["在保"];
const BAD = ["过期", "流失"];

const shortDay = (d: string | null) => (d ? d.slice(5) : "");

function when(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const hm = d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false });
  if (d.toDateString() === today.toDateString()) return `今天 ${hm}`;
  return `${d.getMonth() + 1}-${String(d.getDate()).padStart(2, "0")} ${hm}`;
}

/** 维保日期在 90 天内到期标黄 */
function soon(date: string | null): boolean {
  if (!date) return false;
  const days = (new Date(date).getTime() - Date.now()) / 86_400_000;
  return days >= 0 && days <= 90;
}

function daysAgo(date: string | null): number {
  return date ? Math.floor((Date.now() - new Date(date).getTime()) / 86_400_000) : 0;
}

interface Props {
  target: ChatTarget;
  card: Card | null;
  error: string | null;
  me: Staff;
  onClose: () => void;
  onChanged: () => void;
  notify: (text: string) => void;
}

/** 会话页右边的客户档案：同步来的资料只读，群对应哪家客户、团队补充、资料有误可以改。 */
export default function CustomerCard({ target, card, error, me, onClose, onChanged, notify }: Props) {
  const [picking, setPicking] = useState(false);
  const [draft, setDraft] = useState<{ kind: "note" | "error"; id?: string; text: string } | null>(null);
  const [allRecords, setAllRecords] = useState<ServiceRecord[] | null>(null);
  const [moreRecords, setMoreRecords] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setPicking(false);
    setDraft(null);
    setAllRecords(null);
  }, [target.channel, target.group_id]);

  const run = async (fn: () => Promise<unknown>, done?: string) => {
    setBusy(true);
    try {
      await fn();
      if (done) notify(done);
      onChanged();
      return true;
    } catch (e) {
      notify(`操作失败：${(e as Error).message}`);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const saveDraft = async () => {
    if (!draft || !draft.text.trim()) return;
    const ok = await run(() =>
      draft.id
        ? editCustomerNote(draft.id, draft.text)
        : addCustomerNote(target.channel, target.group_id, draft.kind, draft.text),
    );
    if (ok) setDraft(null);
  };

  const pick = async (customer: string | null) => {
    if (await run(() => setCustomerLink(target.channel, target.group_id, customer), customer ? `已对上「${customer}」` : "已标为不对应客户"))
      setPicking(false);
  };

  const showAll = async () => {
    try {
      const rows = await fetchServiceRecords(target.channel, target.group_id, 0);
      setAllRecords(rows);
      setMoreRecords(rows.length === 50);
    } catch (e) {
      notify(`加载失败：${(e as Error).message}`);
    }
  };
  const loadMoreRecords = async () => {
    if (!allRecords) return;
    try {
      const rows = await fetchServiceRecords(target.channel, target.group_id, allRecords.length);
      setAllRecords([...allRecords, ...rows]);
      setMoreRecords(rows.length === 50);
    } catch (e) {
      notify(`加载失败：${(e as Error).message}`);
    }
  };

  const mine = (n: CustomerNote) => me.role === "admin" || n.author_id === me.id;
  const p = card?.profile ?? null;
  // 管理员或群负责人手动标过「不对应客户」（内部群、测试群），不再列候选、不再催着选
  const noCustomer = !!card && !card.link.customer && card.link.source === "manual";
  const notes = card?.notes.filter((n) => n.kind === "note") ?? [];
  const errors = card?.notes.filter((n) => n.kind === "error") ?? [];
  const stale = card?.sync.maintenance_updated_on && daysAgo(card.sync.maintenance_updated_on) > 3;
  const records = allRecords ?? card?.records ?? [];

  const form = draft && (
    <div className="cc-edit">
      {draft.kind === "error" && (
        <>
          <h4>资料有误</h4>
          <p className="cc-hint">哪项不对、应该是什么。工作台不改售后系统的数据，管理员去源头改好后删掉这条；改好之前它会挂在档案上，AI 也以它为准。</p>
        </>
      )}
      <textarea
        autoFocus
        rows={3}
        maxLength={1000}
        value={draft.text}
        placeholder={draft.kind === "error" ? "如：11 月已续了数据中台维保，售后系统还没更新" : "如：生产环境只能周五 20:00 以后变更"}
        onChange={(e) => setDraft({ ...draft, text: e.target.value })}
        onKeyDown={(e) => e.key === "Escape" && setDraft(null)}
      />
      <div className="row">
        <button className="mini" onClick={() => setDraft(null)}>
          取消
        </button>
        <button className="mini primary" disabled={busy || !draft.text.trim()} onClick={saveDraft}>
          {draft.kind === "error" ? "提交" : "保存"}
        </button>
      </div>
    </div>
  );

  return (
    <section className="cust-card" aria-label="客户档案">
      <div className="cc-head">
        <b>客户档案</b>
        <button className="link cc-close" onClick={onClose} title="收起客户档案">
          收起 ›
        </button>
      </div>
      <div className="cc-body">
        {!card && !error && <p className="cc-empty">加载中…</p>}
        {error && <p className="cc-empty err">档案没加载出来：{error}</p>}
        {card && (
          <>
            {draft?.kind === "error" && <section className="cc-sec">{form}</section>}
            {errors.map((n) => (
              <div key={n.id} className="cc-error">
                <b>资料有误</b>
                {n.content}
                <span className="by">
                  {n.author} · {when(n.created_at)}
                  {mine(n) && (
                    <button className="link" disabled={busy} onClick={() => run(() => deleteCustomerNote(n.id))}>
                      {me.role === "admin" ? "已改好，删掉" : "删掉"}
                    </button>
                  )}
                </span>
              </div>
            ))}

            <section className="cc-sec cc-who">
              {p ? (
                <>
                  <div className="cc-name">
                    {p.name}
                    {p.level && <span className={`lvl ${p.level === "普通" ? "" : "imp"}`}>{p.level}</span>}
                  </div>
                  {p.companies.length > 0 && (
                    <div className="co">
                      {p.companies.join("、")}
                      <small>签约公司</small>
                    </div>
                  )}
                  <dl className="kv">
                    {p.industry && (
                      <>
                        <dt>分组</dt>
                        <dd>{p.industry}</dd>
                      </>
                    )}
                    {p.sales && (
                      <>
                        <dt>销售</dt>
                        <dd>{p.sales}</dd>
                      </>
                    )}
                    {p.purchased.length > 0 && (
                      <>
                        <dt>已购</dt>
                        <dd>{p.purchased.join("、")}</dd>
                      </>
                    )}
                  </dl>
                </>
              ) : card.link.customer ? (
                <div className="cc-name">
                  {card.link.customer}
                  <small className="muted-s">售后系统里还没有这家的资料</small>
                </div>
              ) : (
                <>
                  <h4>{noCustomer ? "这个群不对应客户" : "这个群还没对上客户"}</h4>
                  {noCustomer && !picking && (
                    <p className="cc-src">
                      {card.link.updated_by ? `${card.link.updated_by} 设的，` : ""}如内部群、测试群
                      {card.can_link && (
                        <button className="link" onClick={() => setPicking(true)}>
                          改
                        </button>
                      )}
                    </p>
                  )}
                  {!noCustomer && card.link.candidates.length > 0 && (
                    <div className="cc-cands">
                      <span>售后系统里这个群绑了几家：</span>
                      {card.link.candidates.map((c) =>
                        card.can_link ? (
                          <button key={c} className="mini" disabled={busy} onClick={() => pick(c)}>
                            {c}
                          </button>
                        ) : (
                          <span key={c} className="cand">{c}</span>
                        ),
                      )}
                    </div>
                  )}
                  {!card.can_link && !noCustomer && <p className="cc-empty">请群负责人或管理员选一下这个群对应的客户。</p>}
                </>
              )}
              {(picking || (card.can_link && !card.link.customer && !noCustomer)) && (
                <CustomerPicker
                  busy={busy}
                  onPick={pick}
                  onNone={noCustomer ? undefined : () => pick(null)}
                  onCancel={card.link.customer || noCustomer ? () => setPicking(false) : undefined}
                />
              )}
              {card.link.customer && !picking && (
                <p className="cc-src">
                  群对应：{card.link.source === "manual" && card.link.updated_by ? `${card.link.updated_by} ` : ""}
                  {LINK_SOURCE[card.link.source ?? ""] ?? "自动对上"}
                  {card.can_link && (
                    <button className="link" onClick={() => setPicking(true)}>
                      改
                    </button>
                  )}
                </p>
              )}
            </section>

            {p && (p.maintenance.length > 0 || p.business_status) && (
              <section className="cc-sec">
                <h4 title={`来自售后系统「维保总览」${card.sync.maintenance_updated_on ? `，最后计算于 ${card.sync.maintenance_updated_on}` : ""}`}>
                  维保
                  {stale && <span className="cc-stale">维保总览停在 {shortDay(card.sync.maintenance_updated_on)}</span>}
                </h4>
                {card.alerts.map((a) => (
                  <p key={a} className="cc-alert">{a}</p>
                ))}
                {p.maintenance.map((m) => {
                  const date = m.next_expire ?? m.expire;
                  const cls = BAD.includes(m.status ?? "") ? "st-bad" : GOOD.includes(m.status ?? "") ? "st-ok" : "st-half";
                  return (
                    <div key={m.line} className="mt">
                      <span className="ln">{m.label}</span>
                      <span className={cls}>{m.status ?? "状态未知"}</span>
                      <span className={`d ${soon(date) ? "soon" : ""}`}>
                        {!date ? "" : new Date(date).getTime() < Date.now() ? `${date} 到期` : `到 ${date}`}
                      </span>
                      {m.structure && <small>{m.structure}</small>}
                    </div>
                  );
                })}
                {p.business_status && <p className="cc-src">商务状态：{p.business_status}</p>}
                <p className="inner">只给内部看，AI 不会写进给客户的回复</p>
              </section>
            )}

            {p && (p.environments.length > 0 || p.cluster_type) && (
              <section className="cc-sec">
                <h4 title="来自运维门户「客户环境」和售后系统环境表；版本以运维门户确认的为准">环境</h4>
                {p.environments.map((e) => (
                  <div key={e.product} className="env">
                    <div className="p">
                      {e.product}
                      {e.product === "数据中台" && p.cluster_type && <span className="ct">{p.cluster_type}</span>}
                    </div>
                    {e.instances.length === 0 && <div className="e na">环境未登记</div>}
                    {e.instances.map((i, k) => (
                      <div key={`${i.env}-${k}`} className="e">
                        <span className="k">{i.env}</span>
                        {i.version ? <span className="v">{i.version}</span> : <span className="na">版本未登记</span>}
                        {i.confirmed_at && <span className="ok">✓ {shortDay(i.confirmed_at)} 确认</span>}
                      </div>
                    ))}
                    {(e.tech_support || e.ops_support) && (
                      <div className="ppl">
                        {e.tech_support && (
                          <>
                            {e.tech_support} <small>技术支持</small>
                          </>
                        )}
                        {e.tech_support && e.ops_support && " · "}
                        {e.ops_support && (
                          <>
                            {e.ops_support} <small>运维</small>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                ))}
                {!p.environments.some((e) => e.product === "数据中台") && p.cluster_type && (
                  <div className="env">
                    <div className="p">
                      集群类型<span className="ct">{p.cluster_type}</span>
                    </div>
                  </div>
                )}
              </section>
            )}

            <section className="cc-sec">
              <h4>
                团队补充
                {!draft && (
                  <button className="link r" onClick={() => setDraft({ kind: "note", text: "" })}>
                    ＋ 补充
                  </button>
                )}
              </h4>
              {draft?.kind === "note" && form}
              {notes.length === 0 && !draft && <p className="cc-empty">还没有补充。变更窗口、客户习惯这类注意事项可以记在这里，AI 写建议时也会参考。</p>}
              {notes.map((n) => (
                <div key={n.id} className="note">
                  {n.content}
                  <span className="by">
                    {n.author} · {when(n.updated_at)}
                    {mine(n) && !draft && (
                      <>
                        <button className="link" onClick={() => setDraft({ kind: "note", id: n.id, text: n.content })}>
                          改
                        </button>
                        <button className="link" disabled={busy} onClick={() => run(() => deleteCustomerNote(n.id))}>
                          删
                        </button>
                      </>
                    )}
                  </span>
                </div>
              ))}
            </section>

            {card.record_total > 0 && (
              <section className="cc-sec">
                <h4 title="售后系统里的服务记录（从七鱼时代起，每次服务的一句话小结）">
                  最近服务记录
                  {allRecords ? (
                    <button className="link r" onClick={() => setAllRecords(null)}>
                      收起
                    </button>
                  ) : (
                    card.record_total > records.length && (
                      <button className="link r" onClick={showAll}>
                        共 {card.record_total} 条 ›
                      </button>
                    )
                  )}
                </h4>
                <ul className="svc">
                  {records.map((r) => (
                    <li key={r.id} title={[r.description, r.solution && `处理：${r.solution}`].filter(Boolean).join("\n") || undefined}>
                      <time>{shortDay(r.served_on)}</time>
                      <span className="t">{r.description || r.category || "（没有描述）"}</span>
                      <span className="m">
                        {r.resolved !== null && r.resolved in RESOLVED && (
                          <span className={r.resolved === 0 ? "open" : ""}>{RESOLVED[r.resolved]}</span>
                        )}
                        {r.resolved !== null && r.resolved in RESOLVED && r.staff_name && " · "}
                        {r.staff_name}
                      </span>
                    </li>
                  ))}
                </ul>
                {allRecords && moreRecords && (
                  <button className="link" onClick={loadMoreRecords}>
                    加载更早的
                  </button>
                )}
              </section>
            )}
          </>
        )}
      </div>
      {card && (
        <div className="cc-foot">
          <span className={card.sync.error ? "err" : ""} title={card.sync.error ?? undefined}>
            {!card.sync.configured
              ? "没接售后系统，只显示团队补充"
              : card.sync.error
                ? "同步失败，显示的是上次的资料"
                : card.sync.synced_at
                  ? `售后系统、运维门户 · ${when(card.sync.synced_at)} 同步`
                  : "还没同步过"}
          </span>
          {me.role === "admin" && card.sync.configured && (
            <button className="link" disabled={busy} onClick={() => run(syncCustomers, "已开始同步，一两分钟后刷新")}>
              同步
            </button>
          )}
          {card.link.customer && !draft && (
            <button className="link" onClick={() => setDraft({ kind: "error", text: "" })}>
              资料有误
            </button>
          )}
        </div>
      )}
    </section>
  );
}

function CustomerPicker({
  busy,
  onPick,
  onNone,
  onCancel,
}: {
  busy: boolean;
  onPick: (c: string) => void;
  /** 标成「不对应客户」（内部群、测试群） */
  onNone?: () => void;
  onCancel?: () => void;
}) {
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<{ name: string; companies: string[] }[] | null>(null);

  useEffect(() => {
    const text = q.trim();
    if (!text) {
      setHits(null);
      return;
    }
    const t = setTimeout(() => {
      searchCustomers(text)
        .then(setHits)
        .catch(() => setHits([]));
    }, 250);
    return () => clearTimeout(t);
  }, [q]);

  return (
    <div className="cc-pick">
      <input
        value={q}
        autoFocus={!!onCancel}
        placeholder="输入客户简称或签约公司"
        onChange={(e) => setQ(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && onCancel?.()}
      />
      {hits && hits.length === 0 && (
        <p className="cc-empty">售后系统里没找到「{q.trim()}」。可能还没录入，或用的是别的简称，换个词试试。</p>
      )}
      {hits && hits.length > 0 && (
        <ul>
          {hits.map((h) => (
            <li key={h.name}>
              <button disabled={busy} onClick={() => onPick(h.name)}>
                <b>{h.name}</b>
                {h.companies.length > 0 && <small>{h.companies.join("、")}</small>}
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="cc-pick-foot">
        {onNone && (
          <button className="link" disabled={busy} onClick={onNone} title="内部群、测试群这类不对应任何客户的群">
            这个群不对应客户
          </button>
        )}
        {onCancel && (
          <button className="link" onClick={onCancel}>
            取消
          </button>
        )}
      </div>
    </div>
  );
}
