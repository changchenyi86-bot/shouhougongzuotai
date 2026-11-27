import { useCallback, useEffect, useState } from "react";
import {
  CHANNEL_LABEL,
  fetchGroupProducts,
  productLabel,
  setGroupProduct,
  type GroupProduct,
  type ProductCode,
} from "./api";

const groupLabel = (g: GroupProduct) => g.group_name ?? `未命名的群 · ${g.group_id.slice(-4)}`;

// 可选的组合（按有数、知数、中台的顺序）
const CHOICES: ProductCode[][] = [["bi"], ["zs"], ["ed"], ["bi", "zs"], ["bi", "ed"], ["zs", "ed"], ["bi", "zs", "ed"]];
const keyOf = (codes: ProductCode[] | null | undefined) => (codes ?? []).join("+");

const HOW: Record<GroupProduct["effective"], string> = {
  manual: "按指定",
  group: "按群名",
  ai: "每轮由 AI 判断",
};

/** 团队设置 · 群产品：每个群查哪个产品的知识。群名一个产品都不带的要指定；带多个的每轮由 AI 判断，也可以指定。 */
export default function GroupProducts({ notify }: { notify: (text: string) => void }) {
  const [groups, setGroups] = useState<GroupProduct[]>([]);
  const [search, setSearch] = useState("");
  const [onlyTodo, setOnlyTodo] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchGroupProducts()
      .then(setGroups)
      .catch((e: Error) => notify(`加载失败：${e.message}`));
  }, [notify]);

  useEffect(() => {
    load();
  }, [load]);

  const save = async (g: GroupProduct, codes: ProductCode[] | null) => {
    const key = `${g.channel}:${g.group_id}`;
    setBusy(key);
    try {
      await setGroupProduct(g.channel, g.group_id, codes);
      notify(codes ? `「${groupLabel(g)}」指定为${productLabel(codes)}` : `已取消「${groupLabel(g)}」的指定`);
      load();
    } catch (e) {
      notify(`保存失败：${(e as Error).message}`);
    } finally {
      setBusy(null);
    }
  };

  const todo = groups.filter((g) => g.needs_setting && !g.manual).length;
  const shown = groups.filter(
    (g) =>
      (!onlyTodo || g.effective !== "group") && (!search.trim() || groupLabel(g).includes(search.trim())),
  );

  return (
    <div className="card fill">
      <div className="card-head">
        <div>
          <h3>
            群产品
            {todo > 0 && <span className="missing">{todo} 个群还要指定</span>}
          </h3>
          <p className="sub">
            AI 写建议时查哪个产品的知识。先看这里的指定，没指定看群名（带 BI 是有数、带知数是知数、带 BD 是中台）；群名带多个或都不带的，每轮由
            AI 按聊天内容、截图和被 @ 的同事判断，判断不出就几个一起查。推荐值来自客户已购产品、群负责人所在组和近 30 天 AI 的判断。
          </p>
        </div>
      </div>
      <div className="owner-filters">
        <input className="group-search" placeholder="搜索群名" value={search} onChange={(e) => setSearch(e.target.value)} />
        <label className="check">
          <input type="checkbox" checked={onlyTodo} onChange={(e) => setOnlyTodo(e.target.checked)} />
          只看群名定不了的
        </label>
      </div>
      <div className="table-wrap">
        <table className="t">
          <thead>
            <tr>
              <th>群</th>
              <th>群名标记</th>
              <th>指定</th>
              <th>推荐</th>
              <th>现在</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((g) => {
              const key = `${g.channel}:${g.group_id}`;
              const canApply = g.recommend.length > 0 && keyOf(g.recommend) !== keyOf(g.manual);
              return (
                <tr key={key} className={g.needs_setting && !g.manual ? "missing-row" : ""}>
                  <td className={`group-cell ${g.group_name ? "" : "muted"}`}>
                    {groupLabel(g)}
                    <span className={`tag ${g.channel} product-ch`}>{CHANNEL_LABEL[g.channel]}</span>
                  </td>
                  <td>{g.by_name.length ? productLabel(g.by_name) : <span className="muted">都不带</span>}</td>
                  <td>
                    <select
                      value={keyOf(g.manual)}
                      disabled={busy === key}
                      onChange={(e) =>
                        save(g, e.target.value ? (e.target.value.split("+") as ProductCode[]) : null)
                      }
                    >
                      <option value="">不指定</option>
                      {CHOICES.map((c) => (
                        <option key={keyOf(c)} value={keyOf(c)}>
                          {productLabel(c)}
                        </option>
                      ))}
                    </select>
                    {g.updated_by && g.manual && <span className="muted product-by">{g.updated_by}指定</span>}
                  </td>
                  <td className="product-rec">
                    {g.recommend.length ? (
                      <>
                        <span className="product-rec-name">{productLabel(g.recommend)}</span>
                        {canApply && (
                          <button className="link" disabled={busy === key} onClick={() => save(g, g.recommend)}>
                            按推荐指定
                          </button>
                        )}
                        {g.recommend_basis && <span className="product-basis">{g.recommend_basis}</span>}
                      </>
                    ) : (
                      <span className="muted">没有可依据的信息</span>
                    )}
                  </td>
                  <td>{HOW[g.effective]}</td>
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
