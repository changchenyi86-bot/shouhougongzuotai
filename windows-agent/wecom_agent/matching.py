"""核对 OCR 识别出的文字是不是目标群名。宁可认不出（报失败），也不能认错（发错群）。"""

import re
import unicodedata
from dataclasses import dataclass

# 群名后面的人数，如「某客户项目群(23)」
_COUNT = r"\((\d{1,5})\)"
# 群名后面可能出现的标签
_TAGS = ("外部", "内部", "全员", "@微信")


# 各种横线：OCR 常把群名里的「-」认成「一」「—」等
_DASHES = str.maketrans({c: "-" for c in "‐‑‒–—―−ー一"})


def normalize(text: str) -> str:
    """全角转半角、统一横线、英文转小写、去掉空白。OCR 对这些识别不稳定，群名和识别结果按同样规则处理后再比较。"""
    text = unicodedata.normalize("NFKC", text).translate(_DASHES).lower()
    return re.sub(r"\s+", "", text)


@dataclass
class TitleMatch:
    text: str
    member_count: int | None


def match_title(text: str, name: str, *, exact_name: bool = False) -> TitleMatch | None:
    """text 是否就是群名 name（允许后面带人数和标签）。不做模糊匹配。

    exact_name=True：群名部分逐字比较（发送和校准都用这个）。真机测试发现按 normalize 比较时，
    「测试１群」「测试 1群」会被当成「测试1群」，有打开相近名字的群的风险。代价是群名里有空格等
    识字认不准的字符时会核对不过、报失败，由人手动发送。
    """
    if exact_name:
        if not name or not text.startswith(name):
            return None
        # 群名逐字核对，显示尾缀里的括号、人数和标签单独处理。
        target, t = "", normalize(text[len(name):])
    else:
        target, t = normalize(name), normalize(text)
        if not target:
            return None
    tags = "|".join(re.escape(normalize(tag)) for tag in _TAGS)
    m = re.fullmatch(re.escape(target) + rf"(?:{_COUNT})?(?:{tags})?", t)
    if not m:
        return None
    return TitleMatch(text, int(m.group(1)) if m.group(1) else None)


def find_title(
    lines: list[str], name: str, require_count: bool = False, *, exact_name: bool = False
) -> TitleMatch | None:
    """在聊天窗口标题区域识别出的文字里找群名。

    OCR 可能把群名和人数拆成两段，所以也试一下把整行拼起来。
    require_count：校准时看到标题带人数（说明是群聊），发送时也要求带人数，避免打开了同名的个人聊天。
    """
    candidates = list(lines) + (["".join(lines)] if len(lines) > 1 else [])
    for text in candidates:
        m = match_title(text, name, exact_name=exact_name)
        if m and (m.member_count is not None or not require_count):
            return m
    return None
