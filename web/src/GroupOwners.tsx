import { useCallback, useEffect, useState } from "react";
import { CHANNEL_LABEL, fetchGroups, fetchStaff, setGroupOwner, type Group, type Staff } from "./api";

const groupLabel = (g: Group) => g.group_name ?? `未命名的群 · ${g.group_id.slice(-4)}`;

/** 团队设置 · 群负责人：每个群指定一个负责人，新会话按负责人分。 */
export default function GroupOwners({ notify }: { notify: (text: string) => void }) {
  const [groups, setGroups] = useState<Group[]>([]);
  const [staff, setStaff] = useState<Staff[]>([]);
  const [search, setSearch] = useState("");
  const [onlyMissing, setOnlyMissing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([fetchGroups("all"), fetchStaff()])
      .then(([g, s]) => {
        setGroups(g);
        setStaff(s);
      })
      .catch((e: Error) => notify(`加载失败：${e.message}`));
  }, [notify]);

  useEffect(() => {
    load();
  }, [load]);

  const change = async (g: Group, ownerId: string) => {
    const key = `${g.channel}:${g.group_id}`;
    setBusy(key);
    try {
      await setGroupOwner(g.channel, g.group_id, ownerId || null);
      const owner = staff.find((s) => s.id === ownerId);
      notify(owner ? `「${groupLabel(g)}」的负责人改为${owner.name}` : `已取消「${groupLabel(g)}」的负责人`);
      load();
    } catch (e) {
      notify(`保存失败：${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const active = staff.filter((s) => s.active);
  const teamOf = (id: string | undefined) => staff.find((s) => s.id === id)?.team?.name;
  const missing = groups.filter((g) => !g.owner).length;
  const shown = groups.filter(
    (g) => (!onlyMissing || !g.owner) && (!search.trim() || groupLabel(g).includes(search.trim())),
  );

  return (
    <div className="card fill">
      <div className="card-head">
        <div>
          <h3>
            群负责人
            {missing > 0 && <span className="missing">{missing} 个群还没有负责人</span>}
          </h3>
          <p className="sub">
            新会话先给负责人；负责人不在线，给同组在线、手上会话最少的人；同组都不在线，还是给负责人。没有负责人的群进「待分配」，由管理员分。
          </p>
        </div>
      </div>
      <div className="owner-filters">
        <input className="group-search" placeholder="搜索群名" value={search} onChange={(e) => setSearch(e.target.value)} />
        <label className="check">
          <input type="checkbox" checked={onlyMissing} onChange={(e) => setOnlyMissing(e.target.checked)} />
          只看没有负责人的
        </label>
      </div>
      <div className="table-wrap">
        <table className="t">
          <thead>
            <tr>
              <th>群</th>
              <th>渠道</th>
              <th>负责人</th>
              <th>所在组</th>
              <th>现在</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((g) => {
              const key = `${g.channel}:${g.group_id}`;
              return (
                <tr key={key} className={g.owner ? "" : "missing-row"}>
                  <td className={`group-cell ${g.group_name ? "" : "muted"}`}>{groupLabel(g)}</td>
                  <td>
                    <span className={`tag ${g.channel}`}>{CHANNEL_LABEL[g.channel]}</span>
                  </td>
                  <td>
                    <select value={g.owner?.id ?? ""} disabled={busy === key} onChange={(e) => change(g, e.target.value)}>
                      <option value="">未指定</option>
                      {active.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                          {s.team ? `（${s.team.name}）` : ""}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>{teamOf(g.owner?.id) ?? <span className="muted">—</span>}</td>
                  <td>
                    {g.conversation_open ? (
                      g.assignee ? (
                        <span>{g.assignee.name} 处理中</span>
                      ) : (
                        <span className="warn-text">待分配</span>
                      )
                    ) : (
                      <span className="muted">没有进行中的会话</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {shown.length === 0 && <p className="empty pad">{groups.length ? "没有符合条件的群" : "还没有群"}</p>}
      </div>
    </div>
  );
}
