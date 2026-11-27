"""售后数字人知识检索：生成回复建议前，到售后数字人（自建实例）的检索服务里查相关知识。

售后数字人是知识的唯一来源，工作台不另存一份。检索服务是 JSON-RPC 形式的 MCP 接口（POST /messages），
这里只用到其中几个只读工具：

- case_match / case_card：BI 纠错卡（技术支持对常答错问题的更正），命中且覆盖度够（candidate=true）才读全文
- search：按「限定手册、限定 FAQ、不限类型」三路检索（和知数里的售后数字人技能一致），合并去重
- read_article：前几篇读全文，其余只用摘要

检索服务地址留空表示不查；查询失败不影响生成建议，只是没有参考资料，并把原因记在建议上。
"""

import json
import logging
import re
from typing import Any

import httpx
from pydantic import BaseModel

from app.config import settings

log = logging.getLogger(__name__)

# 检索结果的来源类型 → 显示名
KIND_LABELS = {
    "card": "纠错卡",
    "manual": "产品手册",
    "faq": "FAQ",
    "ticket": "历史工单",
    "bug": "缺陷单",
    "sop": "处理规范",
    "wiki": "知识文章",
    # 工作台自己的相似问答（app/ai/similar.py）
    "workbench": "工作台知识",
    "history": "历史回复",
}
# 知识库的产品前缀 → 适用分支
BRANCHES = {"bi": "有数", "zs": "知数"}

# 纯寒暄、确认类消息不查知识库
_SMALL_TALK = re.compile(r"^(好的?|好滴|嗯+|哦+|收到|了解|明白|知道了|谢谢|多谢|感谢|辛苦了?|ok|okay|thx|thanks|👍)+$", re.I)
_MENTION = re.compile(r"@\S+")
_NOT_WORD = re.compile(r"[\W_]+")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n?", re.S)
_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")


class KnowledgeError(Exception):
    pass


class KnowledgeItem(BaseModel):
    """一条参考知识。content 只用于拼提示词，不保存；其余字段保存在建议上，供工作台显示引用。"""

    ref: str = ""  # 提示词里的编号，如 K1
    kind: str  # card / manual / faq / ticket / bug / sop / wiki
    branch: str  # 有数 / 知数 / 通用
    title: str
    source_id: str  # 纠错卡的 card_id，或文档文件名
    url: str | None = None
    card_status: str | None = None  # 纠错卡状态：draft 表示技术支持还没确认
    snippet: str = ""
    content: str = ""
    cited: bool = False
    # 相似问答才有：谁写的、哪个群、相似度
    author: str | None = None
    group_name: str | None = None
    score: float | None = None

    @property
    def kind_label(self) -> str:
        return KIND_LABELS.get(self.kind, self.kind)

    def to_prompt(self) -> str:
        head = f"[{self.ref}] {self.kind_label}｜{self.branch}"
        if self.kind == "workbench":
            return f"{head}｜技术支持已审核\n{self.content}"
        if self.kind == "history":
            where = f"在「{self.group_name}」" if self.group_name else ""
            return f"[{self.ref}] 历史回复｜技术支持{self.author or ''}以前{where}发出，未经审核\n{self.content}"
        if self.kind == "card":
            head += "｜草稿，技术支持还没确认" if self.card_status == "draft" else "｜技术支持已确认"
        return f"{head}\n标题：{self.title}\n{self.content or self.snippet}"

    def to_record(self) -> dict:
        return self.model_dump(exclude={"content"})


def search_query(text: str) -> str | None:
    """从客户消息里取检索用的问题；寒暄、过短的消息返回 None（不查）。"""
    q = _MENTION.sub(" ", text or "").strip()
    core = _NOT_WORD.sub("", q)
    if len(core) < 4 or _SMALL_TALK.match(core):
        return None
    return q[:300]


def skip_reason(text: str) -> str | None:
    """不查售后数字人的原因；要查时返回 None。"""
    if not settings.knowledge_mcp_url:
        return "没配置售后数字人知识库"
    if search_query(text) is None:
        return "寒暄、确认类消息，没查知识库"
    return None


def _branch(prefix_source: str) -> str:
    return BRANCHES.get((prefix_source or "").split("-", 1)[0].lower(), "通用")


def _clean_article(text: str) -> tuple[str, str | None]:
    """去掉文章头部元数据和图片，返回 (正文, 原文链接)。"""
    url = None
    m = _FRONTMATTER.match(text)
    if m:
        u = re.search(r"^source_url:\s*\"?([^\"\n]+)\"?\s*$", m.group(1), re.M)
        if u and u.group(1).startswith("http"):
            url = u.group(1).strip()
        text = text[m.end():]
    text = _IMAGE.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, url


class KnowledgeClient:
    def __init__(self, url: str, timeout: float = 15, client: httpx.Client | None = None):
        self.url = url
        self._client = client or httpx.Client(timeout=timeout, trust_env=False)

    def call(self, tool: str, arguments: dict) -> Any:
        """调用检索服务的一个工具，返回解析后的结果（JSON 解析不了时返回原文）。"""
        body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": arguments}}
        try:
            r = self._client.post(self.url, json=body, headers={"X-Agent": "shouhou-workbench"})
            r.raise_for_status()
            data = r.json()
            if "error" in data:
                raise KnowledgeError(f"{tool} 返回错误：{str(data['error'])[:200]}")
            text = data["result"]["content"][0]["text"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise KnowledgeError(f"{tool} 调用失败：{exc}") from exc
        try:
            return json.loads(text)
        except ValueError:
            return text

    # ---- 纠错卡 ----
    def cards(self, query: str, product: str) -> list[KnowledgeItem]:
        res = self.call("case_match", {"query": query, "top_k": 3, "product": product})
        if not isinstance(res, dict) or res.get("status") != "ok":
            return []  # 该产品没有卡库时返回 unavailable，不算错误
        items = []
        for hit in res.get("hits") or []:
            if not hit.get("candidate") or not hit.get("card_id"):
                continue
            card = self.call("case_card", {"card_id": hit["card_id"]})
            if not isinstance(card, dict) or card.get("found") is False or not card.get("answer"):
                continue
            lines = [f"适用的问题：{card.get('question', '')}", f"答案：{card['answer']}"]
            if card.get("not_applicable"):
                lines.append(f"不适用：{card['not_applicable']}")
            must_not = card.get("must_not")
            if must_not:
                lines.append("不能说：" + ("；".join(map(str, must_not)) if isinstance(must_not, list) else str(must_not)))
            if card.get("internal_note"):
                lines.append(f"内部说明（只供判断，不要写进回复）：{card['internal_note']}")
            if card.get("versions"):
                lines.append(f"适用版本：{card['versions']}")
            if card.get("visibility") and card["visibility"] != "public":
                lines.append("这张卡标为内部，答案不要直接发给客户")
            items.append(KnowledgeItem(
                kind="card", branch=_branch(card.get("product") or hit["card_id"]),
                title=card.get("title") or hit.get("title") or hit["card_id"], source_id=hit["card_id"],
                card_status=card.get("card_status"), snippet=card["answer"][:200], content="\n".join(lines)))
        return items

    # ---- 手册、FAQ、工单 ----
    def documents(self, query: str, product: str, top: int, read_full: int, max_chars: int) -> list[KnowledgeItem]:
        routes = []
        for source_type in ("manual", "faq", ""):
            res = self.call("search", {"query": query, "top": top, "product": product, "type": source_type})
            routes.append((res.get("results") if isinstance(res, dict) else None) or [])
        # 三路轮流取，按文件去重
        merged, seen = [], set()
        for rank in range(max((len(r) for r in routes), default=0)):
            for results in routes:
                if rank < len(results):
                    r = results[rank]
                    f = r.get("file")
                    if f and f not in seen:
                        seen.add(f)
                        merged.append(r)
        items = []
        for i, r in enumerate(merged[:top]):
            item = KnowledgeItem(
                kind=r.get("source") or "wiki", branch=_branch(r.get("index") or r["file"]),
                title=(r.get("title") or r["file"]).strip(), source_id=r["file"],
                snippet=_IMAGE.sub("", r.get("snippet") or "").strip()[:200])
            # 每篇都读一下以取得原文链接，正文只给前 read_full 篇；单篇读不到时只用摘要
            try:
                text = self.call("read_article", {"file": r["file"]})
            except KnowledgeError as exc:
                log.warning("读售后数字人文章失败 %s：%s", r["file"], exc)
                text = None
            if isinstance(text, str) and not text.startswith("File not found"):
                body, item.url = _clean_article(text)
                if i < read_full:
                    item.content = body[:max_chars] + ("…（后略）" if len(body) > max_chars else "")
            items.append(item)
        return items


_client: KnowledgeClient | None = None


def get_client() -> KnowledgeClient | None:
    global _client
    if not settings.knowledge_mcp_url:
        return None
    if _client is None:
        _client = KnowledgeClient(settings.knowledge_mcp_url, settings.knowledge_timeout)
    return _client


def set_client(client: KnowledgeClient | None) -> None:
    global _client
    _client = client


def retrieve(text: str) -> list[KnowledgeItem]:
    """按客户消息查参考知识：纠错卡在前，然后是手册、FAQ、工单。没配置或不需要查时返回空。

    失败抛 KnowledgeError。
    """
    client = get_client()
    query = search_query(text)
    if client is None or query is None:
        return []
    product = settings.knowledge_product
    items = client.cards(query, product) + client.documents(
        query, product, settings.knowledge_top, settings.knowledge_read_full, settings.knowledge_article_chars)
    for i, item in enumerate(items, 1):
        item.ref = f"K{i}"
    return items
