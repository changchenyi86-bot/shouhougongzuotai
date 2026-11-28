import { useCallback, useEffect, useState } from "react";
import { addStaff, editStaff, fetchStaff, fetchTeams, ROLE_LABEL, type Role, type Staff, type Team } from "./api";
import GroupOwners from "./GroupOwners";
import Colleagues from "./Colleagues";
import GroupProducts from "./GroupProducts";

interface Props {
  me: Staff;
  notify: (text: string) => void;
}

type Draft = { name: string; username: string; password: string; role: Role; team_id: string };
const EMPTY: Draft = { name: "", username: "", password: "", role: "support", team_id: "" };

function lastSeen(iso: string | null) {
  if (!iso) return "还没登录过";
  const d = new Date(iso);
  const time = d.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  return new Date().toDateString() === d.toDateString()
    ? `最近 ${time}`
    : `最近 ${d.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" })} ${time}`;
}

function StatusCell({ s }: { s: Staff }) {
  if (!s.active) return <span className="st muted">已停用</span>;
  if (!s.has_password) return <span className="st warn-text">还没设密码，登录不了</span>;
  if (s.online) return <span className="st"><i className="st-dot online" />在线</span>;
  if (s.status === "away") return <span className="st"><i className="st-dot away" />离开</span>;
  return <span className="st muted"><i className="st-dot off" />不在线 · {lastSeen(s.last_seen)}</span>;
}

/** 团队设置（管理员）：成员、群负责人、群产品、同事的个人微信。 */
export default function TeamSettings({ me, notify }: Props) {
  const [tab, setTab] = useState<"成员" | "群负责人" | "群产品" | "同事个人微信">("成员");
  return (
    <div className="settings">
      <div className="subtabs" role="tablist">
        {(["成员", "群负责人", "群产品", "同事个人微信"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
            {t}
          </button>
        ))}
      </div>
      {tab === "成员" ? (
        <Members me={me} notify={notify} />
      ) : tab === "群负责人" ? (
        <GroupOwners notify={notify} />
      ) : tab === "群产品" ? (
        <GroupProducts notify={notify} />
      ) : (
        <Colleagues notify={notify} />
      )}
    </div>
  );
}

/** 成员、角色、分组。 */
function Members({ me, notify }: Props) {
  const [staff, setStaff] = useState<Staff[]>([]);
  const [teams, setTeams] = useState<Team[]>([]);
  const [adding, setAdding] = useState<Draft | null>(null);
  const [editing, setEditing] = useState<{ id: string; draft: Draft; active: boolean } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    Promise.all([fetchStaff(), fetchTeams()])
      .then(([s, t]) => {
        setStaff(s);
        setTeams(t);
      })
      .catch((e: Error) => notify(`加载失败：${e.message}`));
  }, [notify]);

  useEffect(() => {
    load();
    const t = setInterval(load, 20_000);
    return () => clearInterval(t);
  }, [load]);

  const run = async (fn: () => Promise<unknown>, done: string) => {
    setBusy(true);
    try {
      await fn();
      notify(done);
      load();
      return true;
    } catch (e) {
      notify((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  };

  const saveNew = async () => {
    if (!adding) return;
    const ok = await run(() => addStaff({ ...adding, team_id: adding.team_id || null }), `已添加${adding.name}，把账号和初始密码发给对方就能登录`);
    if (ok) setAdding(null);
  };

  const saveEdit = async () => {
    if (!editing) return;
    const { id, draft, active } = editing;
    const ok = await run(
      () =>
        editStaff(id, {
          name: draft.name,
          role: draft.role,
          ...(draft.team_id ? { team_id: draft.team_id } : { clear_team: true }),
          active,
          ...(draft.password ? { password: draft.password } : {}),
        }),
      draft.password ? "已保存，密码已重设" : "已保存",
    );
    if (ok) setEditing(null);
  };

  const field = (d: Draft, set: (d: Draft) => void, withUsername: boolean) => ({
    name: <input value={d.name} maxLength={32} placeholder="姓名" onChange={(e) => set({ ...d, name: e.target.value })} />,
    username: withUsername ? (
      <input value={d.username} maxLength={64} placeholder="登录账号" onChange={(e) => set({ ...d, username: e.target.value })} />
    ) : null,
    role: (
      <select value={d.role} onChange={(e) => set({ ...d, role: e.target.value as Role })}>
        {(Object.keys(ROLE_LABEL) as Role[]).map((r) => (
          <option key={r} value={r}>{ROLE_LABEL[r]}</option>
        ))}
      </select>
    ),
    team: (
      <select value={d.team_id} onChange={(e) => set({ ...d, team_id: e.target.value })}>
        <option value="">不分组</option>
        {teams.map((t) => (
          <option key={t.id} value={t.id}>{t.name}</option>
        ))}
      </select>
    ),
  });

  const activeCount = staff.filter((s) => s.active).length;

  return (
    <>
      <div className="card">
        <div className="card-head">
          <div>
            <h3>成员 {activeCount} 人</h3>
            <p className="sub">管理员能管成员和设置、看全部会话；技术支持处理分给自己的会话。负责人不在线时，会话分给同组的人。</p>
          </div>
          {!adding && (
            <button className="primary" onClick={() => { setAdding(EMPTY); setEditing(null); }}>添加成员</button>
          )}
        </div>
        <div className="table-wrap">
          <table className="t">
            <thead>
              <tr>
                <th>成员</th>
                <th>账号</th>
                <th>角色</th>
                <th>分组</th>
                <th>{adding || editing ? "状态 / 密码" : "状态"}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {adding && (() => {
                const f = field(adding, setAdding, true);
                return (
                  <tr className="editing">
                    <td>{f.name}</td>
                    <td>{f.username}</td>
                    <td>{f.role}</td>
                    <td>{f.team}</td>
                    <td>
                      <input type="password" value={adding.password} maxLength={128} autoComplete="new-password" placeholder="初始密码，至少 8 位" onChange={(e) => setAdding({ ...adding, password: e.target.value })} />
                    </td>
                    <td className="row-actions">
                      <button className="primary" disabled={busy || !adding.name.trim() || !adding.username.trim() || !adding.password} onClick={saveNew}>添加</button>
                      <button disabled={busy} onClick={() => setAdding(null)}>取消</button>
                    </td>
                  </tr>
                );
              })()}
              {staff.map((s) => {
                if (editing?.id === s.id) {
                  const f = field(editing.draft, (d) => setEditing({ ...editing, draft: d }), false);
                  return (
                    <tr key={s.id} className="editing">
                      <td>{f.name}</td>
                      <td className="muted">{s.username}</td>
                      <td>{f.role}</td>
                      <td>{f.team}</td>
                      <td>
                        <div className="edit-extra">
                          <input type="password" value={editing.draft.password} maxLength={128} autoComplete="new-password" placeholder={s.has_password ? "重设密码，不改就留空" : "设初始密码，至少 8 位"} onChange={(e) => setEditing({ ...editing, draft: { ...editing.draft, password: e.target.value } })} />
                          <label className="check">
                            <input type="checkbox" checked={!editing.active} disabled={s.id === me.id} onChange={(e) => setEditing({ ...editing, active: !e.target.checked })} />
                            停用这个账号
                          </label>
                        </div>
                      </td>
                      <td className="row-actions">
                        <button className="primary" disabled={busy || !editing.draft.name.trim()} onClick={saveEdit}>保存</button>
                        <button disabled={busy} onClick={() => setEditing(null)}>取消</button>
                      </td>
                    </tr>
                  );
                }
                return (
                  <tr key={s.id} className={s.active ? "" : "inactive"}>
                    <td>
                      <span className="who-cell">
                        <span className="ava s">{s.name.slice(0, 1)}</span>
                        {s.name}
                        {s.id === me.id && <small className="muted">（我）</small>}
                      </span>
                    </td>
                    <td className="muted">{s.username}</td>
                    <td><span className={`role ${s.role}`}>{ROLE_LABEL[s.role]}</span></td>
                    <td>{s.team?.name ?? <span className="muted">不分组</span>}</td>
                    <td><StatusCell s={s} /></td>
                    <td className="row-actions">
                      <button
                        className="link"
                        onClick={() => {
                          setAdding(null);
                          setEditing({
                            id: s.id,
                            active: s.active,
                            draft: { name: s.name, username: s.username, password: "", role: s.role, team_id: s.team?.id ?? "" },
                          });
                        }}
                      >
                        编辑
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
