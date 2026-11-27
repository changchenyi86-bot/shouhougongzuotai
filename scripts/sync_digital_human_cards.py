#!/usr/bin/env python3
"""把工作台审核通过的知识写进售后数字人（自建实例）的 BI 纠错卡库。

在能 SSH 到数字人服务器的电脑上运行（只用 Python 标准库）：

    python3 scripts/sync_digital_human_cards.py            # 同步
    python3 scripts/sync_digital_human_cards.py --dry-run  # 只看要改什么，不上传

做的事：
1. 从工作台取要放进卡库的卡片（审核通过的，和写入后又停用的），卡片编号以 bi-verified-wb- 开头；
2. 读服务器上数字人正在用的卡库（bi-case-cards-v*.json 里文件名排在最后的一个）；
3. 去掉里面旧的工作台卡片，换成工作台现在的，其余卡片（技术支持在运营看板审核的）原样保留；
4. 存成新版本 bi-case-cards-v<原版本>.wb<时间>.json 上传（先传临时文件再改名），数字人会自动重新加载；
5. 告诉工作台已写入，并经检索服务核对数字人确实读到了。

服务器和目录不写进仓库，读环境变量或 backend/.env：
  CARDS_SYNC_TOKEN          向工作台取卡片的口令（和后端用的是同一个）
  DIGITAL_HUMAN_CARDS_HOST  SSH 主机（~/.ssh/config 里的别名）
  DIGITAL_HUMAN_CARDS_DIR   卡库目录（数字人挂载的 case-cards 目录）
可选：WORKBENCH_URL（默认 http://localhost:8000）、DIGITAL_HUMAN_MCP_URL（核对用，默认 http://127.0.0.1:18967/messages）

注意：数字人那边以后发布新版本卡库时（版本号变大），工作台的卡会暂时不在新卡库里，再运行一次本脚本即可补上。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime

PREFIX = "bi-verified-wb-"
REQUIRED = ["card_id", "product", "module", "kind", "title", "question", "phrasings", "answer",
            "internal_note", "not_applicable", "must_not", "sources", "deprecate_docs", "versions",
            "visibility", "gaps", "status", "confidence", "author", "reviewer", "created", "review_by"]
ENUMS = {"product": {"bi"}, "kind": {"how_to", "fact", "troubleshoot", "not_supported"},
         "visibility": {"public", "internal"}, "status": {"draft", "confirmed", "retired"},
         "confidence": {"low", "medium", "high"}}
ROOT = pathlib.Path(__file__).resolve().parent.parent


def setting(name: str, default: str = "") -> str:
    if os.environ.get(name):
        return os.environ[name]
    env = ROOT / "backend" / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name and value.strip():
                return value.strip().strip('"').strip("'")
    return default


def http(method: str, url: str, body: dict | None = None, token: str = "") -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 本机地址不走代理
    with opener.open(req, timeout=30) as r:
        return json.loads(r.read().decode())


def ssh(host: str, command: str) -> str:
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", host, command],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        sys.exit(f"连服务器失败：{r.stderr.strip() or r.stdout.strip()}")
    return r.stdout


def validate(cards: list[dict]) -> list[str]:
    errs, seen = [], set()
    for c in cards:
        cid = c.get("card_id", "?")
        if cid in seen:
            errs.append(f"{cid}: 编号重复")
        seen.add(cid)
        errs += [f"{cid}: 缺字段 {k}" for k in REQUIRED if k not in c]
        errs += [f"{cid}: {k}={c.get(k)!r} 不合规" for k, ok in ENUMS.items() if k in c and c[k] not in ok]
        if not str(cid).startswith("bi-verified-"):
            errs.append(f"{cid}: 编号应以 bi-verified- 开头")
        if c.get("status") == "confirmed" and not c.get("reviewer"):
            errs.append(f"{cid}: 已确认的卡必须写审核人")
    return errs


def next_name(latest: str) -> str:
    base = latest[len("bi-case-cards-v"):-len(".json")].split(".wb")[0]
    name = f"bi-case-cards-v{base}.wb{datetime.now():%Y%m%d%H%M%S}.json"
    if sorted([latest, name])[-1] != name:  # 数字人按文件名排序取最后一个
        sys.exit(f"新文件名 {name} 排不到 {latest} 后面，没有上传")
    return name


def verify(mcp: str, card_id: str, retired: bool) -> str:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "case_card", "arguments": {"card_id": card_id}}}
    for _ in range(10):
        time.sleep(1.5)
        try:
            res = json.loads(http("POST", mcp, body)["result"]["content"][0]["text"])
        except Exception as exc:  # noqa: BLE001
            return f"没能核对（检索服务连不上：{exc}）"
        found = res.get("found", True) is not False and res.get("card_status") is not None
        if retired and (not found or res.get("card_status") == "retired"):
            return "数字人已经读到新卡库（停用的卡不再使用）"
        if not retired and found and res.get("card_status") == "confirmed":
            return "数字人已经读到新卡库"
    return "数字人还没读到新卡库，稍后再核对一次"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="只看要改什么，不上传")
    ap.add_argument("--force", action="store_true", help="工作台没有新变化也重新写一遍")
    args = ap.parse_args()

    host, folder = setting("DIGITAL_HUMAN_CARDS_HOST"), setting("DIGITAL_HUMAN_CARDS_DIR").rstrip("/")
    workbench = setting("WORKBENCH_URL", "http://localhost:8000").rstrip("/")
    mcp = setting("DIGITAL_HUMAN_MCP_URL", "http://127.0.0.1:18967/messages")
    token = setting("CARDS_SYNC_TOKEN")
    if not host or not folder or not token:
        sys.exit("没配置 CARDS_SYNC_TOKEN / DIGITAL_HUMAN_CARDS_HOST / DIGITAL_HUMAN_CARDS_DIR（写在 backend/.env）")

    try:
        mine = http("GET", f"{workbench}/api/digital-human/cards", token=token)
    except Exception as exc:  # noqa: BLE001
        sys.exit(f"连不上工作台（{workbench}）：{exc}")
    cards = mine["cards"]
    if not mine["unsynced"] and not args.force:
        print(f"工作台的 {len(cards)} 张卡都已写入，没有要同步的")
        return

    names = sorted(n for n in ssh(host, f"ls -1 {folder}").split() if n.startswith("bi-case-cards-v") and n.endswith(".json"))
    if not names:
        sys.exit(f"服务器 {folder} 里没有 bi-case-cards-v*.json，先由数字人那边发布一版卡库")
    latest = names[-1]
    current = json.loads(ssh(host, f"cat {folder}/{latest}"))
    if not isinstance(current, list):
        sys.exit(f"{latest} 不是卡片列表，没有改动")
    others = [c for c in current if not str(c.get("card_id", "")).startswith(PREFIX)]
    old = {c["card_id"]: c for c in current if str(c.get("card_id", "")).startswith(PREFIX)}
    merged = others + sorted(cards, key=lambda c: c["card_id"])
    errs = validate(merged)
    if errs:
        sys.exit("校验不通过，没有上传：\n  " + "\n  ".join(errs))

    added = [c["card_id"] for c in cards if c["card_id"] not in old]
    changed = [c["card_id"] for c in cards if c["card_id"] in old and old[c["card_id"]] != c]
    removed = [cid for cid in old if cid not in {c["card_id"] for c in cards}]
    name = next_name(latest)
    print(f"数字人现在用的卡库：{latest}（{len(current)} 张，其中工作台的 {len(old)} 张）")
    print(f"新卡库：{name}（{len(merged)} 张）：新增 {len(added)}、更新 {len(changed)}、移除 {len(removed)}，"
          f"其他来源的 {len(others)} 张原样保留")
    if args.dry_run:
        print("只是预览，没有上传")
        return

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=1)
        f.write("\n")
        tmp = f.name
    try:
        r = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", tmp, f"{host}:{folder}/.{name}.uploading"],
                           capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            sys.exit(f"上传失败：{r.stderr.strip()}")
    finally:
        os.unlink(tmp)
    # 临时文件是 600，数字人容器要能读，改成和其他卡库文件一样的 644
    ssh(host, f"chmod 644 {folder}/.{name}.uploading && mv -f {folder}/.{name}.uploading {folder}/{name}")
    version = name[len("bi-case-cards-"):-len(".json")]
    http("POST", f"{workbench}/api/digital-human/cards/synced",
         {"version": version, "card_ids": [c["card_id"] for c in cards]}, token=token)
    print(f"已上传 {name}，工作台已记下")
    if cards:
        last = cards[-1]
        print(verify(mcp, last["card_id"], last["status"] == "retired"))


if __name__ == "__main__":
    main()
