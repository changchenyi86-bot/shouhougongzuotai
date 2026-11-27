"""这一轮问题属于哪个产品（有数 / 知数 / 数据中台），决定去售后数字人的哪个知识库查。

先后顺序：管理员给群指定的产品 > 群名里的产品标记（只带一个时）> AI 按这一轮内容判断 > 判断不出就几个都查。
群名标记：BI、有数、指标 → 有数；知数 → 知数；BD、DB、中台、平台（不含“指标平台”）→ 中台。
"""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Channel, GroupProduct, Staff, Suggestion, Message

CODES = ("bi", "zs", "ed")
LABEL = {"bi": "有数", "zs": "知数", "ed": "中台"}
CODE_OF = {"有数": "bi", "知数": "zs", "中台": "ed", "数据中台": "ed"}

_BI = re.compile(r"(?<![A-Za-z])BI(?![A-Za-z])|有数|指标", re.I)
_ZS = re.compile(r"知数")
_ED = re.compile(r"(?<![A-Za-z])(BD|DB)(?![A-Za-z])|中台|(?<!指标)平台", re.I)

# 给 AI 判断产品用：三个产品各自的模块名、界面和常见说法
PRODUCT_GUIDE = """三个产品怎么区分：
- 有数（有数 BI、YouData）：报表和可视化。模块有报告、大屏 / 大屏 Pro、数据表格、数据填报、数据门户、数据模型、数据连接、
  数据源、抽取 / 直连、自助取数、项目中心、权限管理、组织架构、推送 / 订阅、书签、筛选器、交叉表、计算字段、全局参数、
  指标平台（指标模型、指标加速）、资源迁移导入导出、web / tas 容器、youdata 业务库、MPP。界面顶部导航常见“数据源 主页 报告
  数据填报 数据表格 数据门户”，右上角“项目中心”。
- 知数：有数里的智能问数和 AI 助手。说法有智能问数、问数、业务助手、助手、问答、业务知识、模型描述 / 提示词、大模型配置
  （百炼、千问、DeepSeek、结构化输出）、MCP、skillUtil/ask、askData、助手集成（/dash/agent/chat）、AI 解读组件。
- 数据中台（EasyData、数据开发平台、数帆）：数据开发和治理。模块有离线开发（任务、工作流、调度、实例、补数据、依赖、
  开发模式 / 线上模式）、任务运维、数据传输（离线同步、实时同步、登记数据源）、实时计算（Flink、CDC、Sloth）、数据服务
  （API、资源组、服务编排）、数据资产 / 数据地图 / 元数据、数据质量（稽核、比对）、数据标准、数据治理、数据安全 / 数据保护、
  指标管理、标签、EasyOps（部署运维）、Mammut、项目队列 / Yarn；底层组件 Hive、Impala、Spark、HDFS、HBase、Kafka、
  Iceberg、Amoro、Kerberos、Ranger。
- 只说“指标”时两边都有（有数的指标平台、中台的指标管理），要看上下文；问 SQL、表、数据源连不上时也要看是在哪个产品里操作的。"""


@dataclass
class Decision:
    codes: list[str]  # 查哪些产品：bi / zs / ed
    source: str  # manual 管理员指定 / group 群名 / ai AI 判断 / all 判断不出几个都查 / override 客服改的
    reason: str = ""

    @property
    def label(self) -> str:
        return "、".join(LABEL[c] for c in self.codes)


_PATTERN = {"bi": _BI, "zs": _ZS, "ed": _ED}


def by_name(group_name: str | None) -> list[str]:
    """群名里带的产品标记（按有数、知数、中台的顺序）。"""
    name = group_name or ""
    return [c for c in CODES if _PATTERN[c].search(name)]


def normalize(codes) -> list[str]:
    out = [c for c in CODES if c in set(codes or [])]
    return out


def static(session: Session, channel: Channel, group_id: str, group_name: str | None) -> Decision | None:
    """不用 AI 就能定的：管理员指定的、群名只带一个产品的。定不了返回 None。"""
    row = session.get(GroupProduct, (channel, group_id))
    if row is not None and normalize(row.products):
        return Decision(normalize(row.products), "manual", "管理员给这个群指定的")
    marks = by_name(group_name)
    if len(marks) == 1:
        found = _PATTERN[marks[0]].search(group_name or "")
        return Decision(marks, "group", f"群名带「{found.group(0) if found else LABEL[marks[0]]}」")
    return None


def from_ai(guess: str | None, reason: str, group_name: str | None) -> Decision:
    code = CODE_OF.get((guess or "").strip())
    if code:
        return Decision([code], "ai", reason.strip() or "AI 按这一轮内容判断")
    marks = by_name(group_name)
    return Decision(marks or list(CODES), "all", "AI 判断不出是哪个产品，几个知识库一起查")


def mcp_products(codes: list[str]) -> list[str]:
    """产品 → 售后数字人检索服务的 product 参数。有数（bi）会自动带上知数的资料；只查知数时知数在前、再补有数
    （知数跑在有数上，很多问题有数的资料也能答）。"""
    out: list[str] = []
    for c in normalize(codes):
        if c == "zs":
            if "bi" not in codes:
                out += ["zs", "bi"]
        else:
            out.append(c)
    return list(dict.fromkeys(out)) or ["bi"]


def mentioned_teams(session: Session, text: str) -> list[str]:
    """这一轮里被 @ 的同事和他所在的组，帮 AI 判断产品（BI 组管有数、知数，中台组管数据中台）。"""
    if "@" not in (text or ""):
        return []
    out = []
    for s in session.scalars(select(Staff).where(Staff.team_id.is_not(None))):
        if s.team and f"@{s.name}" in text:
            out.append(f"{s.name}（{s.team.name}）")
    return out


# ---------- 群产品设置页：推荐值 ----------

def _purchased_codes(purchased: list | None) -> list[str]:
    out = []
    for p in purchased or []:
        p = str(p)
        if "知数" in p:
            out.append("zs")
        elif "BI" in p.upper() or "有数" in p or "指标" in p:
            out.append("bi")
        elif any(k in p for k in ("数开", "中台", "EasyData", "NDH", "离线", "数据治理", "数据资产", "数据传输", "数据开发")):
            out.append("ed")
    return normalize(out)


def ai_stats(session: Session, days: int = 30) -> dict[tuple, Counter]:
    """各群近 N 天 AI 判断（不含群名、指定决定的）的产品次数。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = session.execute(
        select(Message.channel, Message.group_id, Suggestion.products)
        .join(Suggestion, Suggestion.message_id == Message.id)
        .where(Suggestion.product_source == "ai", Suggestion.created_at >= since)
    )
    stats: dict[tuple, Counter] = {}
    for ch, gid, codes in rows:
        for c in normalize(codes):
            stats.setdefault((ch, gid), Counter())[c] += 1
    return stats


def recommend(marks: list[str], purchased: list | None, team: str | None,
              stats: Counter | None) -> tuple[list[str], str]:
    """给要人工指定的群推荐一个值，并写明依据。"""
    basis, votes = [], Counter()
    bought = _purchased_codes(purchased)
    if bought:
        basis.append("客户已购：" + "、".join(str(p) for p in purchased))
        votes.update({c: 1 for c in bought})
    if team:
        code = "ed" if "中台" in team else ("bi" if "BI" in team.upper() else None)
        if code:
            basis.append(f"群负责人在{team}")
            votes[code] += 1
    if stats:
        total = sum(stats.values())
        basis.append("近 30 天 AI 判断：" + "、".join(f"{LABEL[c]} {n} 次" for c, n in stats.most_common()))
        for c, n in stats.items():
            votes[c] += 2 * n / total
    if marks:
        basis.insert(0, "群名带：" + "、".join(LABEL[c] for c in marks))
        picked = [c for c in marks if votes.get(c)] or marks
    else:
        best = max(votes.values(), default=0)
        picked = normalize([c for c, v in votes.items() if v > best * 0.5]) if best else []
    return picked, "；".join(basis)
