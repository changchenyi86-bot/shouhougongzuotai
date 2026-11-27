"""客户档案：只读同步、群对应客户、档案卡、团队补充、AI 建议带上档案。"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import customers, db
from app.config import settings
from app.main import app
from app.models import Channel, CustomerGroupLink, CustomerProfile, ServiceRecord

CUSTOMER = "wmAbCdEfGhIjKlMnOpQrStUvWxYz012345"
T0 = datetime.now(timezone.utc) - timedelta(hours=1)
_n = iter(range(10_000))

GROUPS = {
    "wrJia": "甲方股份-网易-BD&BI-支持",
    "wrJiaBI": "甲方股份-网易-BI-支持",  # 售后系统里误绑了两家
    "wrDing": "丁方银行-网易-BI运维-支持",  # 两边简称不一样
    "wrYi": "乙方汽车-网易-BD-支持",  # 维保过期
    "wrWu": "戊方-网易-BI-支持",  # 两边都没有
}


def _msg(client, group, content=None, sender=CUSTOMER):
    n = next(_n)
    r = client.post("/api/messages", json={
        "channel": "wecom", "external_id": f"{group}-{n}", "group_id": group, "group_name": GROUPS.get(group, group),
        "is_external_group": True, "sender_id": sender, "content": content or f"问题{n}",
        "sent_at": (T0 + timedelta(seconds=n)).isoformat(),
    })
    assert r.status_code == 201, r.text


def _record(rid, chat_id, day, desc, resolved=1, status="normal", updated="2026-11-26 04:00:00"):
    return {"id": rid, "chat_id": chat_id, "date_id": day, "category": "任务排查", "service_desc": desc,
            "solution": "", "is_resolved": resolved, "emp_name": "楚展航", "record_status": status, "updated_at": updated}


def _source(records=None):
    return {
        "bind": [{"chat_id": "wrJia", "group_id": "ai_x", "simple_name": "甲方股份"},
                 {"chat_id": "wrJiaBI", "group_id": "ai_y", "simple_name": "甲方股份"},
                 {"chat_id": "wrJiaBI", "group_id": "ai_z", "simple_name": "丙方快递"},
                 {"chat_id": "ai_q", "group_id": "wrDing", "simple_name": "某金科-丁方银行"},
                 {"chat_id": "wrYi", "group_id": "", "simple_name": "乙方汽车"}],
        "tasks": [{"chat_id": "wrJiaBI", "customer_name": "甲方股份"}, {"chat_id": "wrDing", "customer_name": "丁方银行"}],
        "repurchase": [
            {"simple_name": "甲方股份", "cus_company": "甲方食品集团股份有限公司", "cus_industry": "通用", "vip_level": "重要",
             "sale_name": "王五", "purchased_sku": "数开平台、有数BI"},
            {"simple_name": "乙方汽车", "cus_company": "乙方汽车股份有限公司,乙方科技有限责任公司",
             "cus_industry": "通用", "vip_level": "普通", "sale_name": "赵六", "purchased_sku": "数开平台"},
            {"simple_name": "丙方快递", "cus_company": "丙方速递有限公司", "cus_industry": "通用", "vip_level": "普通",
             "sale_name": None, "purchased_sku": None}],
        "crm": [{"simple_name": "甲方股份", "bus_status": "售后", "sale_name": "王五"},
                {"simple_name": "乙方汽车", "bus_status": "过保", "sale_name": "赵六"},
                {"simple_name": "某金科-丁方银行", "bus_status": "售后", "sale_name": "钱七"}],
        "lines": [
            {"simple_name": "甲方股份", "product_line": "BI", "line_status": "半过期", "status_struct": "1在保+2过期",
             "next_expire_date": "2027-03-23", "expire_date": "2027-03-23"},
            {"simple_name": "甲方股份", "product_line": "BD", "line_status": "半过期", "status_struct": "1在保+3过期",
             "next_expire_date": "2027-03-23", "expire_date": "2027-03-23"},
            {"simple_name": "乙方汽车", "product_line": "BD", "line_status": "过期", "status_struct": "2过期",
             "next_expire_date": None, "expire_date": "2025-09-27"}],
        "maint_updated": [{"at": datetime(2026, 11, 2, 3, 5, 56)}],
        "envs": [{"id": 327, "customer_name": "甲方股份", "product_type": "ED+NDH", "tech_support": "楚展航", "ops_support": "李凌威"},
                 {"id": 525, "customer_name": "甲方股份", "product_type": "有数BI", "tech_support": "陆宣任", "ops_support": "胡耀民"},
                 {"id": 9, "customer_name": "乙方汽车", "product_type": "ED+NDH", "tech_support": "", "ops_support": "夏明"}],
        "ndh": [{"id": 1, "customer_env_id": 327, "env_type": "PROD", "env_name": "生产环境", "platform_version": "V10.0-Update08",
                 "current_version": "V10.0-Update08", "confirmed": 1, "confirmed_at": datetime(2026, 11, 11, 10, 11), "archived": 0},
                {"id": 2, "customer_env_id": 327, "env_type": "TEST", "env_name": "测试环境", "platform_version": "",
                 "current_version": "", "confirmed": 0, "confirmed_at": None, "archived": 0},
                {"id": 3, "customer_env_id": 327, "env_type": "POC", "env_name": "POC环境", "platform_version": "V9",
                 "current_version": "V9", "confirmed": 0, "confirmed_at": None, "archived": 1}],
        "bi": [{"id": 7, "customer_env_id": 525, "env_type": "PROD", "env_name": "生产环境"}],
        "envinfo": [{"simple_name": "甲方股份", "product_name": "有数BI", "env_type": "生产-PRD",
                     "update_version": "LTS_9.22", "hotfix_version": None},
                    {"simple_name": "乙方汽车", "product_name": "数开平台", "env_type": "PRD-生产",
                     "update_version": "【Update】EasyData-V7.0-Update16", "hotfix_version": ""}],
        "cluster": [{"simple_name": "甲方股份", "cluster_type": "NDH全家桶"}, {"simple_name": "乙方汽车", "cluster_type": "NDH全家桶"}],
        "records": records if records is not None else [
            _record(101, "wrJia", "2026-11-26", "Git 登记分支能否由 main 改为 dev", resolved=2),
            _record(102, "wrJia", "2026-11-25", "GitLab 代码库与任务归属查询", resolved=0),
            _record(103, "wrJia", "2026-11-20", "普通用户自行修改密码的入口"),
            _record(104, "wrJia", "2026-11-19", "调度任务失败排查"),
        ],
    }


@pytest.fixture()
def synced(client, monkeypatch):
    """几个群都有消息，然后同步一次。返回每次同步时 fetch_source 收到的参数。"""
    for g in GROUPS:
        _msg(client, g, sender="lisi")  # 员工发言：群出现在工作台，但不开会话、不生成建议
    calls = []
    state = {"src": _source()}

    def fake_fetch(chat_ids, cursor, fresh):
        calls.append((sorted(chat_ids), cursor, sorted(fresh)))
        return state["src"]

    monkeypatch.setattr(settings, "customer_db_config_file", "/tmp/not-used.json")
    monkeypatch.setattr(customers, "fetch_source", fake_fetch)
    assert "同步了" in customers.sync_once()
    return calls, state


def _card(c, group):
    r = c.get("/api/customers/card", params={"channel": "wecom", "group_id": group})
    assert r.status_code == 200, r.text
    return r.json()


def _member(client, name, owner_of=None):
    n = next(_n)
    teams = {t["name"]: t["id"] for t in client.get("/api/teams").json()}
    r = client.post("/api/staff", json={"name": name, "username": f"m{n}", "password": "secret-123",
                                        "role": "support", "team_id": teams["BI 组"]})
    assert r.status_code == 201, r.text
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": f"m{n}", "password": "secret-123"}).status_code == 200
    if owner_of:
        r = client.put("/api/groups/owner", json={"channel": "wecom", "group_id": owner_of, "owner_id": r.json()["id"]})
        assert r.status_code == 204, r.text
    return c


def test_sync_links_groups_and_builds_profiles(synced):
    calls, _ = synced
    assert calls[0][1] is None and calls[0][2] == sorted(GROUPS)  # 第一次：每个群的服务记录都全取
    with db.SessionLocal() as s:
        link = lambda g: s.get(CustomerGroupLink, (Channel.WECOM, g))  # noqa: E731
        assert (link("wrJia").customer, link("wrJia").source) == ("甲方股份", "bind")
        assert link("wrJiaBI").customer == "甲方股份"  # 绑了两家，和任务表一致的那家
        assert link("wrDing").customer == "某金科-丁方银行"  # 群 id 记在 group_id 里；简称以售后系统为准
        assert link("wrWu") is None
        p = s.get(CustomerProfile, "甲方股份")
        assert p.companies == ["甲方食品集团股份有限公司"] and p.level == "重要" and p.purchased == ["数开平台", "有数BI"]
        assert [m["label"] for m in p.maintenance] == ["有数BI", "数据中台"]
        envs = {e["product"]: e for e in p.environments}
        assert list(envs) == ["有数BI", "数据中台"]
        assert envs["数据中台"]["instances"] == [
            {"env": "生产", "version": "V10.0-Update08", "confirmed_at": "2026-11-11"},
            {"env": "测试", "version": None, "confirmed_at": None}]  # 归档的 POC 不要
        assert envs["有数BI"]["instances"] == [{"env": "生产", "version": "LTS_9.22", "confirmed_at": None}]  # 售后系统环境表补的
        assert envs["有数BI"]["tech_support"] == "陆宣任"
        assert s.get(CustomerProfile, "乙方汽车").companies == ["乙方汽车股份有限公司", "乙方科技有限责任公司"]
        assert s.query(ServiceRecord).count() == 4


def test_records_sync_incrementally(client, synced):
    calls, state = synced
    state["src"] = _source(records=[
        _record(101, "wrJia", "2026-11-26", "Git 分支改动（更正）", resolved=1, updated="2026-11-27 04:00:00"),
        _record(104, "wrJia", "2026-11-19", "调度任务失败排查", status="excluded_daily_chat", updated="2026-11-27 04:00:00"),
    ])
    customers.sync_once()
    chat_ids, cursor, fresh = calls[1]
    assert cursor is not None and "wrJia" not in fresh  # 已有记录的群只取更新过的
    card = _card(client, "wrJia")
    assert card["record_total"] == 3  # 被标成闲聊的删掉
    assert card["records"][0]["description"] == "Git 分支改动（更正）"


def test_card_shows_profile_alerts_and_sync_state(client, synced):
    card = _card(client, "wrJia")
    assert card["link"] == {"customer": "甲方股份", "source": "bind", "candidates": [], "updated_by": None}
    assert card["profile"]["cluster_type"] == "NDH全家桶" and card["alerts"] == []  # 半过期不提醒
    assert [r["id"] for r in card["records"]] == [101, 102, 103] and card["record_total"] == 4
    assert card["sync"]["configured"] and card["sync"]["synced_at"] and card["sync"]["maintenance_updated_on"] == "2026-11-02"
    assert card["can_link"] is True  # 管理员
    assert _card(client, "wrYi")["alerts"] == ["数据中台维保过期"]
    # 丁方银行售后系统里没有维保线，商务状态是售后：不提醒
    assert _card(client, "wrDing")["alerts"] == []
    fei = _card(client, "wrWu")
    assert fei["link"]["customer"] is None and fei["profile"] is None


def test_card_follows_group_visibility(client, synced):
    owner = _member(client, "楚展航", owner_of="wrJia")
    other = _member(client, "宋奔")
    assert _card(owner, "wrJia")["can_link"] is True  # 群负责人能改对应客户
    assert other.get("/api/customers/card", params={"channel": "wecom", "group_id": "wrJia"}).status_code == 403
    r = other.put("/api/customers/link", json={"channel": "wecom", "group_id": "wrJia", "customer": "乙方汽车"})
    assert r.status_code == 403


def test_manual_link_survives_sync_and_takes_group_notes(client, synced):
    r = client.post("/api/customers/notes", json={"channel": "wecom", "group_id": "wrWu", "content": "  客户只在晚上变更  "})
    assert r.status_code == 201 and r.json()["content"] == "客户只在晚上变更"
    assert client.get("/api/customers/search", params={"q": "丙方速递"}).json() == [
        {"name": "丙方快递", "companies": ["丙方速递有限公司"]}]
    r = client.put("/api/customers/link", json={"channel": "wecom", "group_id": "wrWu", "customer": "没有这家"})
    assert r.status_code == 422
    r = client.put("/api/customers/link", json={"channel": "wecom", "group_id": "wrWu", "customer": "丙方快递"})
    assert r.status_code == 200 and r.json()["source"] == "manual"
    r = client.put("/api/customers/link", json={"channel": "wecom", "group_id": "wrJiaBI", "customer": "丙方快递"})
    assert r.status_code == 200
    customers.sync_once()
    assert _card(client, "wrJiaBI")["link"]["customer"] == "丙方快递"  # 同步不覆盖人工选的
    card = _card(client, "wrWu")
    assert card["profile"]["name"] == "丙方快递" and card["link"]["updated_by"] == "李四"
    # 这家的另一个群也看得到这条补充（补充跟着客户走）
    assert [n["content"] for n in _card(client, "wrJiaBI")["notes"]] == ["客户只在晚上变更"]


def test_note_permissions(client, synced):
    owner = _member(client, "楚展航", owner_of="wrJia")
    mate = _member(client, "姜瑜", owner_of="wrJia")  # 负责人换成姜瑜，楚展航就看不到了
    _member(client, "宋奔")
    r = mate.post("/api/customers/notes", json={"channel": "wecom", "group_id": "wrJia", "kind": "error",
                                                "content": "11 月已续了数据中台维保，售后系统还没更新"})
    assert r.status_code == 201
    note = r.json()
    assert owner.post("/api/customers/notes", json={"channel": "wecom", "group_id": "wrJia", "content": "x"}).status_code == 403
    assert mate.patch(f"/api/customers/notes/{note['id']}", json={"content": "已续保"}).json()["content"] == "已续保"
    assert mate.patch(f"/api/customers/notes/{note['id']}", json={"content": "  "}).status_code == 422
    assert client.delete(f"/api/customers/notes/{note['id']}").status_code == 204  # 管理员改好源头后删掉
    assert _card(client, "wrJia")["notes"] == []


def test_suggestion_prompt_carries_profile(client, fake_llm, synced):
    client.post("/api/customers/notes", json={"channel": "wecom", "group_id": "wrJia", "content": "数据组很在意 Git 推送"})
    _msg(client, "wrJia", content="测试环境升到 V10.0-Update09 后 Git 推送还是不生效")
    system, prompt = fake_llm.calls[-1]
    assert "<customer_profile>" in system and "不要写进回复" in system
    block = prompt.split("<customer_profile>")[1].split("</customer_profile>")[0]
    assert "甲方股份" in block and "NDH全家桶" in block and "生产 V10.0-Update08" in block
    assert "有数BI 半过期，最近到期 2027-03-23" in block and "数据组很在意 Git 推送" in block
    assert "Git 登记分支能否由 main 改为 dev（部分解决）" in block
    items = client.get("/api/conversation", params={"channel": "wecom", "group_id": "wrJia"}).json()["items"]
    sug = next(i["suggestion"] for i in items if i["suggestion"])
    assert sug["profile"] == {"customer": "甲方股份", "summary": "甲方股份 · NDH全家桶 · 有数BI 生产 LTS_9.22"}
    # 没对上客户、也没有补充和服务记录的群：不带档案
    _msg(client, "wrNobody", content="报表打不开了")
    assert "<customer_profile>" not in fake_llm.calls[-1][1]


def test_sync_not_configured_or_failing(client, monkeypatch):
    assert customers.sync_once().startswith("没配置")
    assert _card_for_new_group(client)["sync"]["configured"] is False

    def broken(*_):
        raise customers.SourceError("连不上客户资料数据库（错误码 2003）")

    monkeypatch.setattr(settings, "customer_db_config_file", "/tmp/not-used.json")
    monkeypatch.setattr(customers, "fetch_source", broken)
    assert customers.sync_once() == "连不上客户资料数据库（错误码 2003）"
    assert _card_for_new_group(client)["sync"]["error"] == "连不上客户资料数据库（错误码 2003）"


def _card_for_new_group(client):
    _msg(client, "wrNew", sender="lisi")
    return _card(client, "wrNew")


@pytest.mark.parametrize(("name", "lines"), [
    ("甲方股份-网易-BD&BI-支持", {"BD", "BI"}), ("某某-网易-DB-支持", {"BD"}), ("某某-网易-知数-支持", {"BI"}),
    ("某某-网易-平台&BI-售前", {"BD", "BI"}), ("测试1群", set()),
])
def test_group_lines(name, lines):
    assert customers.group_lines(name) == lines


@pytest.mark.parametrize(("raw", "label"), [("BI", "有数BI"), ("有数BI", "有数BI"), ("ED+NDH", "数据中台"), ("NDH", "数据中台"),
                                            ("数开平台", "数据中台"), ("知数", "知数"), ("指标平台", "指标平台")])
def test_product_label(raw, label):
    assert customers.product_label(raw) == label


@pytest.mark.parametrize(("env_type", "name", "label"), [("PRD-生产", "", "生产"), ("生产-PRD", "", "生产"), ("信创-UAT", "", "信创"),
                                                     ("PROD", "生产环境", "生产"), ("TRIAL", "试用环境（EDSGF02105）", "试用环境（EDSGF02105）")])
def test_env_label(env_type, name, label):
    assert customers.env_label(env_type, name) == label


def test_long_source_values_fit_columns(client, monkeypatch):
    """源库里超长的字段按工作台的列宽截断（PostgreSQL 上超长会让整次同步失败）。"""
    _msg(client, "wrJia", sender="lisi")
    src = _source(records=[{**_record(201, "wrJia", "2026-11-26", "排查"), "category": "类" * 40, "emp_name": "人" * 80}])
    src["repurchase"][0].update(vip_level="级" * 30, sale_name="销" * 70, cus_industry="业" * 120)
    src["crm"][0]["bus_status"] = "状" * 30
    src["cluster"][0]["cluster_type"] = "群" * 50
    monkeypatch.setattr(settings, "customer_db_config_file", "/tmp/not-used.json")
    monkeypatch.setattr(customers, "fetch_source", lambda *a: src)
    assert "同步了" in customers.sync_once()
    with db.SessionLocal() as s:
        p = s.get(CustomerProfile, "甲方股份")
        assert (len(p.level), len(p.sales), len(p.industry), len(p.business_status), len(p.cluster_type)) == (20, 60, 100, 20, 40)
        r = s.get(ServiceRecord, 201)
        assert (len(r.category), len(r.staff_name)) == (32, 64)


def test_read_only_session_setup_error_is_reported(monkeypatch, tmp_path):
    """连上之后设只读会话出错（如源库不支持某个设置）：报同步失败、关掉连接，不往下读。"""
    import pymysql

    closed = []

    class Cursor:
        def execute(self, sql):
            if "MAX_EXECUTION_TIME" in sql:
                raise pymysql.err.OperationalError(1193, "Unknown system variable")

    class Conn:
        def cursor(self):
            return Cursor()

        def close(self):
            closed.append(True)

    cfg = tmp_path / "db.json"
    cfg.write_text('{"host": "h", "username": "u", "password": "p"}', encoding="utf-8")
    monkeypatch.setattr(settings, "customer_db_config_file", str(cfg))
    monkeypatch.setattr(pymysql, "connect", lambda **kw: Conn())
    with pytest.raises(customers.SourceError, match="1193"):
        customers._connect()
    assert closed == [True]
