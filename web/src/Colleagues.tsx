import { useCallback, useEffect, useState } from "react";
import {
  addColleague,
  fetchColleagueCandidates,
  fetchColleagues,
  removeColleague,
  type ColleagueAccount,
  type ColleagueCandidate,
} from "./api";

const shortId = (id: string) => `${id.slice(0, 6)}…${id.slice(-4)}`;
const day = (iso: string | null) => (iso ? new Date(iso).toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" }) : "");

/** 团队设置 · 同事的个人微信：有的同事用个人微信在客户群里回复，工作台会把他们当成客户。加进名单后按我方处理。 */
export default function Colleagues({ notify }: { notify: (text: string) => void }) {
  const [list, setList] = useState<ColleagueAccount[]>([]);
  const [cands, setCands] = useState<ColleagueCandidate[]>([]);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([fetchColleagues(), fetchColleagueCandidates()])
      .then(([a, b]) => {
        setList(a);
        setCands(b);
      })
      .catch((e: Error) => notify(`加载失败：${e.message}`));
  }, [notify]);

  useEffect(() => {
    load();
  }, [load]);

  const add = async (c: ColleagueCandidate) => {
    setBusy(c.sender_id);
    try {
      const out = await addColleague(c.channel, c.sender_id, c.name);
      notify(out.closed ? `已加入名单，以前把这个账号当成客户生成的 ${out.closed} 条建议已结束` : "已加入名单");
      load();
    } catch (e) {
      notify(`加入失败：${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const remove = async (a: ColleagueAccount) => {
    setBusy(a.sender_id);
    try {
      await removeColleague(a.channel, a.sender_id);
      notify("已移出名单，之后按客户处理");
      load();
    } catch (e) {
      notify(`移出失败：${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      <div className="card">
        <div className="card-head">
          <div>
            <h3>同事的个人微信 {list.length} 个</h3>
            <p className="sub">
              有的同事用个人微信在客户群里回复，工作台分不出来，会把他们当成客户。名单上的账号按我方处理：不给他们的话生成 AI 建议，他们发的回复算作「已回」。
            </p>
          </div>
        </div>
        <div className="table-wrap">
          <table className="t">
            <thead>
              <tr>
                <th>群里的名字</th>
                <th>账号</th>
                <th>谁加的</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {list.map((a) => (
                <tr key={`${a.channel}:${a.sender_id}`}>
                  <td>{a.name || <span className="muted">没有名字</span>}</td>
                  <td className="muted">{shortId(a.sender_id)}</td>
                  <td className="muted">{a.added_by ?? ""} {day(a.created_at)}</td>
                  <td className="row-actions">
                    <button disabled={busy === a.sender_id} onClick={() => remove(a)}>移出</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {list.length === 0 && <p className="empty pad">还没有。从下面的候选里加，或者等同事在群里说话后再来看</p>}
      </div>

      <div className="card">
        <div className="card-head">
          <div>
            <h3>可能是同事的 {cands.length} 个</h3>
            <p className="sub">名字里带「网易」「技术支持」等字样，或者在 3 个以上客户群里说过话的外部账号。确认是同事再加。</p>
          </div>
        </div>
        <div className="table-wrap">
          <table className="t">
            <thead>
              <tr>
                <th>群里的名字</th>
                <th>为什么列出</th>
                <th>说过话</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {cands.map((c) => (
                <tr key={`${c.channel}:${c.sender_id}`}>
                  <td>
                    {c.name || <span className="muted">没有名字</span>}
                    <div className="muted">{shortId(c.sender_id)}</div>
                  </td>
                  <td>{c.reason}</td>
                  <td className="muted">{c.groups} 个群 · {c.messages} 条 · 最近 {day(c.last_at)}</td>
                  <td className="row-actions">
                    <button className="primary" disabled={busy === c.sender_id} onClick={() => add(c)}>是同事，加入</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {cands.length === 0 && <p className="empty pad">没有候选了</p>}
      </div>
    </>
  );
}
