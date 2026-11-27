"""客户档案：从售后系统、运维门户的数据库只读同步客户资料，按群对上客户，给档案卡和 AI 建议用。

数据来源（都在同一个 MySQL 实例上，只读事务、只查下面固定的表和列）：

- 群对应哪家客户：售后系统群绑定 sw_admin.biz_qiyu_chat_bind（chat_id 就是企业微信群 id）为主，
  任务表库 ops_portal_prod.wecom_customer_groups 为辅
- 签约公司、等级、分组、销售、已购产品：sw_ticket.biz_customer_repurchase
- 商务状态：sw_admin.biz_crm_customer_profile；按产品线的维保：sw_admin.v_fpm_customer_line
- 环境和负责人：运维门户 customer_envs 及其下的环境实例、版本；售后系统环境表 biz_cus_env_info；
  集群类型 sw_ticket.sys_customer_cluster
- 服务记录：sw_ticket.biz_service_record_all（七鱼时代起，每次服务一句话小结），按群增量同步

运维门户的环境表里还存着账号密码、访问地址、服务器信息，这里一律不查。
"""

import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app import db
from app.config import settings
from app.models import (Channel, CustomerGroupLink, CustomerNote, CustomerProfile, GroupName, Message,
                        ServiceRecord, SyncCursor)

log = logging.getLogger(__name__)

SOURCE_TZ = timezone(timedelta(hours=8))  # 源库里的时间是北京时间
SYNCED_KEY, ERROR_KEY, MAINT_KEY = "customer_synced_at", "customer_sync_error", "customer_maint_updated_at"

LINE_LABELS = {"BI": "有数BI", "BD": "数据中台"}
BAD_MAINTENANCE = ("过期", "流失")
BAD_BUSINESS = ("过保", "流失")
RESOLVED = {0: "待跟进", 1: "已解决", 2: "部分解决"}

_SPLIT = re.compile(r"[,，、;；]")

# ---------- 读源库 ----------

QUERIES = {
    # 企业微信群 id（wr 开头）多数记在 chat_id，少数记在 group_id
    "bind": "SELECT chat_id, group_id, simple_name FROM sw_admin.biz_qiyu_chat_bind "
            "WHERE (chat_id LIKE 'wr%' OR group_id LIKE 'wr%') AND simple_name IS NOT NULL AND simple_name <> ''",
    "tasks": "SELECT chat_id, customer_name FROM ops_portal_prod.wecom_customer_groups",
    "repurchase": "SELECT simple_name, cus_company, cus_industry, vip_level, sale_name, purchased_sku "
                  "FROM sw_ticket.biz_customer_repurchase",
    "crm": "SELECT simple_name, bus_status, sale_name FROM sw_admin.biz_crm_customer_profile",
    "lines": "SELECT simple_name, product_line, line_status, status_struct, next_expire_date, expire_date "
             "FROM sw_admin.v_fpm_customer_line",
    "maint_updated": "SELECT MAX(updated_at) AS at FROM sw_admin.dws_fpm_maint_interval",
    "envs": "SELECT id, customer_name, product_type, tech_support, ops_support FROM ops_portal_prod.customer_envs",
    "ndh": "SELECT n.id, n.customer_env_id, n.env_type, n.env_name, n.platform_version, "
           "v.current_version, v.confirmed, v.confirmed_at, v.archived "
           "FROM ops_portal_prod.customer_env_ndh_instances n "
           "LEFT JOIN ops_portal_prod.ops_ed_environment_versions v ON v.instance_id = n.id",
    "bi": "SELECT id, customer_env_id, env_type, env_name FROM ops_portal_prod.customer_env_bi_instances",
    "envinfo": "SELECT simple_name, product_name, env_type, update_version, hotfix_version FROM sw_admin.biz_cus_env_info",
    "cluster": "SELECT simple_name, cluster_type FROM sw_ticket.sys_customer_cluster",
}
RECORD_COLUMNS = ("SELECT id, chat_id, date_id, category, service_desc, solution, is_resolved, emp_name, "
                  "record_status, updated_at FROM sw_ticket.biz_service_record_all")


class SourceError(Exception):
    pass


def _connect():
    import pymysql

    path = Path(settings.customer_db_config_file)
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
        conn = pymysql.connect(host=cfg["host"], port=int(cfg.get("port", 3306)), user=cfg["username"],
                               password=cfg["password"], charset="utf8mb4", connect_timeout=10, read_timeout=120,
                               cursorclass=pymysql.cursors.DictCursor, autocommit=False)
    except (OSError, ValueError, KeyError) as exc:
        raise SourceError(f"客户资料数据库连接配置读不了（{type(exc).__name__}）") from None
    except pymysql.MySQLError as exc:
        raise SourceError(f"连不上客户资料数据库（错误码 {exc.args[0] if exc.args else '?'}）") from None
    cur = conn.cursor()
    for sql in ("SET SESSION TRANSACTION READ ONLY", "SET SESSION MAX_EXECUTION_TIME=60000",
                "SET SESSION time_zone = '+08:00'", "START TRANSACTION READ ONLY"):
        cur.execute(sql)
    cur.execute("SELECT @@session.transaction_read_only AS ro")
    if cur.fetchone()["ro"] != 1:
        conn.close()
        raise SourceError("没能进入只读事务，不同步")
    return conn, cur


def fetch_source(chat_ids: list[str], record_cursor: datetime | None, fresh_chat_ids: list[str]) -> dict:
    """在一个只读事务里读出档案要用的全部数据。

    服务记录：fresh_chat_ids（工作台里新出现的群）取全部有效记录；其余的群只取 record_cursor 之后有改动的
    （含被改成闲聊、无实质内容的，同步时从工作台删掉）。
    """
    import pymysql

    conn, cur = _connect()
    try:
        out = {}
        for key, sql in QUERIES.items():
            cur.execute(sql)
            out[key] = cur.fetchall()
        records = []
        known = [c for c in chat_ids if c not in set(fresh_chat_ids)]
        if fresh_chat_ids:
            cur.execute(RECORD_COLUMNS + " WHERE record_status = 'normal' AND chat_id IN %s", (fresh_chat_ids,))
            records += cur.fetchall()
        if known and record_cursor is not None:
            since = record_cursor.astimezone(SOURCE_TZ).replace(tzinfo=None)
            cur.execute(RECORD_COLUMNS + " WHERE chat_id IN %s AND updated_at > %s", (known, since))
            records += cur.fetchall()
        out["records"] = records
        return out
    except pymysql.MySQLError as exc:
        raise SourceError(f"读客户资料出错（错误码 {exc.args[0] if exc.args else '?'}）") from None
    finally:
        conn.rollback()
        conn.close()


# ---------- 整理成档案 ----------


def _text(v) -> str:
    return "" if v is None else str(v).strip()


def _split(v) -> list[str]:
    seen: list[str] = []
    for part in _SPLIT.split(_text(v)):
        part = part.strip()
        if part and part not in seen:
            seen.append(part)
    return seen


def _date(v) -> str | None:
    s = _text(v)[:10]
    return s if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) and not s.startswith("0000") else None


def _source_time(v) -> datetime | None:
    if isinstance(v, datetime):
        return v.replace(tzinfo=SOURCE_TZ) if v.tzinfo is None else v
    try:
        return datetime.fromisoformat(_text(v)).replace(tzinfo=SOURCE_TZ) if _text(v) else None
    except ValueError:
        return None


def product_label(raw: str) -> str:
    """运维门户、售后系统里的产品写法（BI、有数BI、ED+NDH、NDH、数开平台、知数）统一成档案上的叫法。"""
    s = _text(raw)
    if not s:
        return "其他"
    if "知数" in s:
        return "知数"
    if "指标" in s:
        return "指标平台"
    if any(k in s.upper() for k in ("ED", "NDH", "EASYDATA")) or any(k in s for k in ("数开", "数据开发", "中台")):
        return "数据中台"
    if "BI" in s.upper():
        return "有数BI"
    return s


ENV_LABELS = {"PROD": "生产", "PRD": "生产", "TEST": "测试", "TST": "测试", "SIM": "仿真", "UAT": "验收",
              "DEV": "开发", "POC": "POC", "TRIAL": "试用", "DR": "灾备", "PRE": "预发布"}


def env_label(env_type: str, env_name: str = "") -> str:
    """PROD / PRD-生产 / 生产-PRD / TEST → 生产、测试；运维门户里起了名字的环境用它的名字。"""
    t = _text(env_type)
    name = _text(env_name)
    if "-" in t:  # 售后系统环境表的写法：PRD-生产，也有写反的 生产-PRD
        parts = [p for p in t.split("-", 1) if p]
        cjk = [p for p in parts if re.search(r"[\u4e00-\u9fff]", p)]
        return cjk[0] if cjk else ENV_LABELS.get(parts[0].upper(), t)
    label = ENV_LABELS.get(t.upper(), t)
    if name and name not in (f"{label}环境", label):
        return name
    return label or name or "环境"


def _environments(src: dict) -> dict[str, list[dict]]:
    """客户 → [{product, tech_support, ops_support, instances: [{env, version, confirmed_at}]}]"""
    by_env: dict[int, list[dict]] = {}
    for n in src["ndh"]:
        if n.get("archived") == 1:
            continue
        version = _text(n.get("current_version")) or _text(n.get("platform_version"))
        confirmed = n.get("confirmed_at") if n.get("confirmed") == 1 and version else None
        by_env.setdefault(n["customer_env_id"], []).append({
            "env": env_label(n.get("env_type"), n.get("env_name")), "version": version or None,
            "confirmed_at": _date(confirmed)})
    for b in src["bi"]:
        by_env.setdefault(b["customer_env_id"], []).append({
            "env": env_label(b.get("env_type"), b.get("env_name")), "version": None, "confirmed_at": None})

    result: dict[str, dict[str, dict]] = {}
    for e in src["envs"]:
        name = _text(e.get("customer_name"))
        if not name:
            continue
        product = product_label(e.get("product_type"))
        entry = result.setdefault(name, {}).setdefault(
            product, {"product": product, "tech_support": [], "ops_support": [], "instances": []})
        for key in ("tech_support", "ops_support"):
            for person in _split(e.get(key)):
                if person not in entry[key]:
                    entry[key].append(person)
        entry["instances"] += by_env.get(e["id"], [])

    # 售后系统环境表补版本：运维门户里同一个环境没写版本的补上，没有的环境加上
    for r in src["envinfo"]:
        name = _text(r.get("simple_name"))
        version = " ".join(x for x in (_text(r.get("update_version")), _text(r.get("hotfix_version"))) if x)
        if not name or not version:
            continue
        product = product_label(r.get("product_name"))
        entry = result.setdefault(name, {}).setdefault(
            product, {"product": product, "tech_support": [], "ops_support": [], "instances": []})
        env = env_label(r.get("env_type"))
        same = [i for i in entry["instances"] if i["env"] == env]
        if any(i["version"] for i in same):
            continue
        if same:
            same[0]["version"] = version
        else:
            entry["instances"].append({"env": env, "version": version, "confirmed_at": None})

    out: dict[str, list[dict]] = {}
    for name, products in result.items():
        items = []
        for entry in products.values():
            uniq, seen = [], set()
            for i in entry["instances"]:
                key = (i["env"], i["version"])
                if key not in seen:
                    seen.add(key)
                    uniq.append(i)
            entry["instances"] = sorted(uniq, key=lambda i: (i["env"] != "生产", i["env"]))
            entry["tech_support"] = "、".join(entry["tech_support"]) or None
            entry["ops_support"] = "、".join(entry["ops_support"]) or None
            items.append(entry)
        order = ["有数BI", "知数", "指标平台", "数据中台"]
        out[name] = sorted(items, key=lambda x: order.index(x["product"]) if x["product"] in order else 9)
    return out


def build_profiles(src: dict, now: datetime) -> list[CustomerProfile]:
    rep = {_text(r["simple_name"]): r for r in src["repurchase"] if _text(r.get("simple_name"))}
    crm = {_text(r["simple_name"]): r for r in src["crm"] if _text(r.get("simple_name"))}
    lines: dict[str, list[dict]] = {}
    for r in src["lines"]:
        name = _text(r.get("simple_name"))
        if not name:
            continue
        code = _text(r.get("product_line")).upper()
        lines.setdefault(name, []).append({
            "line": code, "label": LINE_LABELS.get(code, "产品线待定"), "status": _text(r.get("line_status")) or None,
            "structure": _text(r.get("status_struct")) or None, "next_expire": _date(r.get("next_expire_date")),
            "expire": _date(r.get("expire_date"))})
    envs = _environments(src)
    cluster = {_text(r["simple_name"]): _text(r.get("cluster_type")) or None for r in src["cluster"]
               if _text(r.get("simple_name"))}
    names = set(rep) | set(crm) | set(lines) | set(envs) | set(cluster)
    names |= {_text(r["simple_name"]) for r in src["bind"]} | {_text(r["customer_name"]) for r in src["tasks"]}
    names.discard("")
    profiles = []
    for name in sorted(names):
        r, c = rep.get(name, {}), crm.get(name, {})
        profiles.append(CustomerProfile(
            name=name[:100], companies=_split(r.get("cus_company")) or None,
            level=_text(r.get("vip_level")) or None, industry=_text(r.get("cus_industry")) or None,
            sales=_text(r.get("sale_name")) or _text(c.get("sale_name")) or None,
            purchased=_split(r.get("purchased_sku")) or None,
            business_status=_text(c.get("bus_status")) or None,
            maintenance=sorted(lines.get(name, []), key=lambda x: (x["line"] != "BI", x["line"])) or None,
            environments=envs.get(name) or None, cluster_type=cluster.get(name), synced_at=now))
    return profiles


def resolve_link(bind: list[str], tasks: list[str]) -> tuple[str | None, str | None, list[str] | None]:
    """群对应哪家客户：(客户, 来源, 候选)。

    售后系统群绑定里只有一家就用它（任务表群对照里的简称有时和售后系统不一致，以售后系统为准）；
    绑了几家时，和任务表群对照里一致的那家；还定不了就把候选列出来，等人选。
    """
    bind, tasks = sorted(set(bind)), sorted(set(tasks))
    if len(bind) == 1:
        return bind[0], "bind", None
    if len(bind) > 1:
        both = [b for b in bind if b in tasks]
        if len(both) == 1:
            return both[0], "bind", None
        return None, "bind", bind
    if len(tasks) == 1:
        return tasks[0], "tasks", None
    if len(tasks) > 1:
        return None, "tasks", tasks
    return None, None, None


def apply(session: Session, src: dict, chat_ids: list[str]) -> None:
    """把读到的数据写进工作台（调用方 commit）。档案整体替换；人工选的群对应不动。"""
    now = datetime.now(timezone.utc)
    session.execute(delete(CustomerProfile))
    session.add_all(build_profiles(src, now))

    bind: dict[str, list[str]] = {}
    for r in src["bind"]:
        for key in {_text(r.get("chat_id")), _text(r.get("group_id"))}:
            if key.startswith("wr"):
                bind.setdefault(key, []).append(_text(r["simple_name"]))
    tasks: dict[str, list[str]] = {}
    for r in src["tasks"]:
        if _text(r.get("customer_name")):
            tasks.setdefault(_text(r["chat_id"]), []).append(_text(r["customer_name"]))
    for chat_id in chat_ids:
        row = session.get(CustomerGroupLink, (Channel.WECOM, chat_id))
        if row is not None and row.source == "manual":
            continue
        customer, source, candidates = resolve_link(bind.get(chat_id, []), tasks.get(chat_id, []))
        if source is None:
            if row is not None:
                session.delete(row)
            continue
        if row is None:
            row = CustomerGroupLink(channel=Channel.WECOM, group_id=chat_id)
            session.add(row)
        row.customer, row.source, row.candidates, row.updated_by = customer, source, candidates, None

    for r in src.get("records", []):
        rid = int(r["id"])
        existing = session.get(ServiceRecord, rid)
        # 被标成闲聊、无实质内容的不要（有的没打标记，但小结里写着「无实质服务事项」）
        if _text(r.get("record_status")) not in ("", "normal") or "无实质" in _text(r.get("service_desc")):
            if existing is not None:
                session.delete(existing)
            continue
        rec = existing or ServiceRecord(id=rid)
        rec.chat_id, rec.served_on = _text(r["chat_id"]), (_date(r.get("date_id")) or _text(r.get("date_id"))[:10])
        rec.category, rec.staff_name = _text(r.get("category")) or None, _text(r.get("emp_name")) or None
        rec.description, rec.solution = _text(r.get("service_desc")) or None, _text(r.get("solution")) or None
        rec.resolved = r.get("is_resolved")
        rec.source_updated_at = _source_time(r.get("updated_at"))
        if existing is None:
            session.add(rec)

    maint = src.get("maint_updated") or []
    _set_cursor(session, MAINT_KEY, _date(maint[0].get("at")) if maint and maint[0].get("at") else "")


def _set_cursor(session: Session, key: str, value: str) -> None:
    row = session.get(SyncCursor, key) or SyncCursor(key=key, value="")
    row.value = (value or "")[:128]
    session.add(row)


def _cursor(session: Session, key: str) -> str | None:
    row = session.get(SyncCursor, key)
    return row.value if row and row.value else None


_lock = threading.Lock()


def sync_once() -> str:
    """同步一次，返回结果说明。没配置时什么也不做。同一时间只跑一个。"""
    if not settings.customer_db_config_file:
        return "没配置客户资料数据库，跳过"
    if not _lock.acquire(blocking=False):
        return "正在同步"
    try:
        with db.SessionLocal() as session:
            chat_ids = list(session.scalars(
                select(Message.group_id).where(Message.channel == Channel.WECOM).distinct()))
            have = set(session.scalars(select(ServiceRecord.chat_id).distinct()))
            cursor = session.scalar(select(func.max(ServiceRecord.source_updated_at)))
        if cursor is not None and cursor.tzinfo is None:
            cursor = cursor.replace(tzinfo=timezone.utc)  # SQLite 读回不带时区
        try:
            src = fetch_source(chat_ids, cursor, [c for c in chat_ids if c not in have])
        except SourceError as exc:
            with db.SessionLocal() as session:
                _set_cursor(session, ERROR_KEY, str(exc))
                session.commit()
            log.warning("客户档案同步失败：%s", exc)
            return str(exc)
        with db.SessionLocal() as session:
            apply(session, src, chat_ids)
            _set_cursor(session, SYNCED_KEY, datetime.now(timezone.utc).isoformat())
            _set_cursor(session, ERROR_KEY, "")
            session.commit()
        msg = f"同步了 {len(src['repurchase'])} 家客户资料、{len(src['records'])} 条服务记录"
        log.info("客户档案%s", msg)
        return msg
    finally:
        _lock.release()


# ---------- 给档案卡和 AI 用 ----------


def group_name(session: Session, channel: Channel, group_id: str) -> str | None:
    row = session.get(GroupName, (channel, group_id))
    if row:
        return row.name
    return session.scalar(
        select(Message.group_name).where(Message.channel == channel, Message.group_id == group_id,
                                         Message.group_name.is_not(None))
        .order_by(Message.sent_at.desc()).limit(1))


def group_lines(name: str | None) -> set[str]:
    """从群名看这个群管哪条产品线：带 BD / DB / 中台 / 平台的是数据中台，带 BI / 知数 / 指标的是有数BI。"""
    n = (name or "").upper()
    lines = set()
    if re.search(r"BD|DB|中台|平台|NDH", n):
        lines.add("BD")
    if re.search(r"BI|知数|问数|指标|有数", n):
        lines.add("BI")
    return lines


def alerts(profile: CustomerProfile | None, lines: set[str]) -> list[str]:
    """只给内部看的提醒：这个群对应产品线的维保过期或流失。"""
    if profile is None:
        return []
    out = []
    relevant = [m for m in profile.maintenance or [] if not lines or m["line"] in lines]
    for m in relevant:
        if m.get("status") in BAD_MAINTENANCE:
            out.append(f"{m['label']}维保{m['status']}")
    if not relevant and profile.business_status in BAD_BUSINESS:
        out.append(f"商务状态：{profile.business_status}")
    return out


def notes_for(session: Session, channel: Channel, group_id: str, customer: str | None) -> list[CustomerNote]:
    cond = (CustomerNote.channel == channel) & (CustomerNote.group_id == group_id)
    if customer:
        cond = or_(cond, CustomerNote.customer == customer)
    return list(session.scalars(select(CustomerNote).where(cond).order_by(CustomerNote.created_at.desc())))


def recent_records(session: Session, channel: Channel, group_id: str, limit: int) -> list[ServiceRecord]:
    if channel != Channel.WECOM:
        return []
    return list(session.scalars(
        select(ServiceRecord).where(ServiceRecord.chat_id == group_id)
        .order_by(ServiceRecord.served_on.desc(), ServiceRecord.id.desc()).limit(limit)))


def record_count(session: Session, channel: Channel, group_id: str) -> int:
    if channel != Channel.WECOM:
        return 0
    return session.scalar(select(func.count(ServiceRecord.id)).where(ServiceRecord.chat_id == group_id)) or 0


def link_of(session: Session, channel: Channel, group_id: str) -> CustomerGroupLink | None:
    return session.get(CustomerGroupLink, (channel, group_id))


def sync_status(session: Session) -> dict:
    synced = _cursor(session, SYNCED_KEY)
    at = datetime.fromisoformat(synced) if synced else None
    return {"configured": bool(settings.customer_db_config_file), "synced_at": at,
            "error": _cursor(session, ERROR_KEY), "maintenance_updated_on": _cursor(session, MAINT_KEY)}


def _env_text(env: dict) -> str:
    parts = [f"{i['env']} {i['version'] or '版本未登记'}" for i in env.get("instances") or []]
    return "；".join(parts) or "环境未登记"


def summary(profile: CustomerProfile) -> str:
    """建议卡上「参考了客户档案」后面那一句：客户 + 集群类型 + 第一个登记了版本的环境。"""
    parts = [profile.name]
    if profile.cluster_type:
        parts.append(profile.cluster_type)
    for env in profile.environments or []:
        inst = next((i for i in env.get("instances") or [] if i.get("version")), None)
        if inst:
            parts.append(f"{env['product']} {inst['env']} {inst['version']}")
            break
    return " · ".join(parts)


def prompt_context(session: Session, channel: Channel, group_id: str) -> tuple[str, dict] | None:
    """生成建议时带给 AI 的客户档案（全部是内部信息），和建议卡上显示的摘要。没有可带的返回 None。"""
    link = link_of(session, channel, group_id)
    customer = link.customer if link else None
    profile = session.get(CustomerProfile, customer) if customer else None
    notes = notes_for(session, channel, group_id, customer)
    records = recent_records(session, channel, group_id, settings.customer_records_in_prompt)
    if profile is None and not notes and not records:
        return None
    lines = []
    if profile is not None:
        head = [f"客户：{profile.name}"]
        if profile.companies:
            head.append(f"签约公司 {'、'.join(profile.companies)}")
        if profile.level:
            head.append(f"等级 {profile.level}")
        lines.append("；".join(head))
        if profile.purchased:
            lines.append(f"已购产品：{'、'.join(profile.purchased)}")
        if profile.cluster_type:
            lines.append(f"集群类型：{profile.cluster_type}")
        if profile.environments:
            lines.append("环境（运维门户、售后系统登记，可能不是最新）：")
            lines += [f"- {e['product']}：{_env_text(e)}" for e in profile.environments]
        maint = [f"{m['label']} {m['status'] or '状态未知'}"
                 + (f"，最近到期 {m['next_expire'] or m['expire']}" if (m.get("next_expire") or m.get("expire")) else "")
                 for m in profile.maintenance or []]
        if maint or profile.business_status:
            lines.append("维保：" + "；".join(maint + ([f"商务状态 {profile.business_status}"]
                                                     if profile.business_status else [])))
    elif customer:
        lines.append(f"客户：{customer}（售后系统里还没有这家的资料）")
    team = [n for n in notes if n.kind == "note"]
    errors = [n for n in notes if n.kind == "error"]
    if team:
        lines.append("团队补充：")
        lines += [f"- {n.content}" for n in team[:10]]
    if errors:
        lines.append("技术支持指出档案里这些资料有误（以这里为准）：")
        lines += [f"- {n.content}" for n in errors[:5]]
    if records:
        lines.append("这个群最近的服务记录（以前的客服小结，只作背景）：")
        lines += [f"- {r.served_on[5:]} {r.description or r.category or ''}"
                  + (f"（{RESOLVED[r.resolved]}）" if r.resolved in RESOLVED else "") for r in records]
    shown = summary(profile) if profile is not None else (customer or "团队补充和服务记录")
    return "\n".join(lines), {"customer": customer, "summary": shown}
