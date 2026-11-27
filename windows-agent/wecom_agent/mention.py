"""回复里要 @ 的群成员：把回复拆成「文字、@某人、文字……」，在 @ 成员列表里认出要选的那一个。

只做纯文字处理，不碰界面，便于测试。真正在企业微信里打 @、选人的是 ui.WeComDriver。
宁可选不中（按普通文字发出并告诉工作台），也不能选错人。
"""

import re
import unicodedata
from dataclasses import dataclass

# 企业微信 @ 完一个人后，常在名字后面跟一个特殊空格；比较输入框内容时把这些都当成普通空格
_SPACES = re.compile(r"[  -​  　]")
# 成员列表里外部联系人名字后面的来源标签，如「张三@微信」「李四@某某公司」
_SOURCE_TAG = re.compile(r"^\s*[@＠]\s*(\S.*)$")
_DASHES = str.maketrans({c: "-" for c in "‐‑‒–—―−ー一"})
QUERY_MAX = 12  # 打 @ 后最多再输入多少个字来筛选成员


@dataclass
class Mention:
    name: str  # 群里显示的名字（群昵称优先），和企业微信 @ 列表里的一致
    kind: str = ""  # internal 本企业员工 / wechat 微信用户 / wecom 其他企业的企业微信用户
    user_id: str = ""


def split(content: str, mentions: list[Mention]) -> tuple[list[str | Mention], list[Mention]]:
    """按 mentions 的先后顺序在回复里找「@名字」，拆成文字和要 @ 的人交替的几段。

    返回 (各段, 回复里找不到的 @)。找不到的不 @，原文照常发出。
    """
    parts: list[str | Mention] = []
    missing: list[Mention] = []
    pos = 0
    for m in mentions:
        if not m.name:
            continue
        at = content.find("@" + m.name, pos)
        if at < 0:
            missing.append(m)
            continue
        parts.append(content[pos:at])
        parts.append(m)
        pos = at + 1 + len(m.name)
    parts.append(content[pos:])
    return [p for p in parts if p != ""], missing


def query_of(name: str) -> str:
    """打 @ 后接着输入哪几个字来筛选成员：名字里第一个空白之前的部分（企业微信遇到空格会收起列表）。"""
    return re.split(r"\s", name.strip(), maxsplit=1)[0][:QUERY_MAX]


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_DASHES)
    return re.sub(r"\s+", "", text).lower()


@dataclass
class Candidate:
    text: str
    tag: str | None  # 名字后面的来源标签（「微信」「某某公司」），没有时为 None
    exact: bool  # 名字逐字相同（不只是全角半角、横线、空格的差别）


def candidate(text: str, name: str) -> Candidate | None:
    """成员列表里识别出的一行是不是这个人（允许后面带来源标签）。不做模糊匹配。"""
    raw = text.strip()
    for exact, a, b in ((True, raw, name.strip()), (False, _norm(raw), _norm(name))):
        if not b or not a.startswith(b):
            continue
        rest = a[len(b):]
        if not rest.strip():
            return Candidate(raw, None, exact)
        tag = _SOURCE_TAG.match(rest)
        if tag:
            return Candidate(raw, tag.group(1).strip(), exact)
    return None


def _tag_fits(tag: str | None, kind: str) -> bool:
    if kind == "internal":
        return tag is None
    if kind == "wechat":
        return tag is not None and "微信" in tag and "企业" not in tag
    if kind == "wecom":
        return tag is not None and not ("微信" in tag and "企业" not in tag)
    return True


def pick(texts: list[str], m: Mention) -> tuple[int | None, str]:
    """在成员列表识别出的各行里找要 @ 的人。返回 (第几行, 说明)；找不到或分不清时第几行为 None。

    1. 只看名字和要 @ 的人一样的行（可以带来源标签）；逐字相同的优先，没有才看只差全角半角、横线、空格的。
    2. 同名的不止一个时，按来源（员工没有标签、微信用户带「@微信」、其他企业带公司名）再筛一次。
    3. 还剩不止一个，就分不清是哪一个，不选。
    """
    found = [(i, c) for i, t in enumerate(texts) if (c := candidate(t, m.name))]
    if not found:
        return None, f"成员列表里没有找到「{m.name}」"
    exact = [(i, c) for i, c in found if c.exact]
    found = exact or found
    if len(found) == 1:
        return found[0][0], ""
    fits = [(i, c) for i, c in found if _tag_fits(c.tag, m.kind)] if m.kind else []
    if len(fits) == 1:
        return fits[0][0], ""
    return None, f"群里有 {len(found)} 个叫「{m.name}」的人，分不清是哪一个"


def comparable(text: str, names: list[str]) -> str:
    """比较输入框内容时用：统一换行和特殊空格，去掉每个「@名字」后面紧跟的空白（企业微信会自己加一个）。"""
    text = _SPACES.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    for name in sorted(set(names), key=len, reverse=True):
        text = re.sub(re.escape("@" + name) + r"[ \t]*", "@" + name + " ", text)
    return re.sub(r" +\n", "\n", text).strip()


def same_text(got: str, expected: str, names: list[str]) -> bool:
    return comparable(got, names) == comparable(expected, names)
