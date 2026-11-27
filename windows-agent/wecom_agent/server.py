"""和工作台后端通信（只用标准库）。"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from wecom_agent import VERSION
from wecom_agent.config import ServerConfig
from wecom_agent.mention import Mention

# 告诉工作台这个版本会做的事：mentions = 能在企业微信里真正 @ 人（旧版本只会把「@名字」当普通文字发）
FEATURES = ["mentions"]


class ServerError(Exception):
    """网络不通或服务端出错，稍后重试即可。"""


class ServerRejected(Exception):
    """服务端明确拒绝（口令错误、任务冲突等），重试也没用。"""


@dataclass
class Attachment:
    id: str
    filename: str


@dataclass
class Task:
    id: str
    group_name: str
    content: str
    attachments: list[Attachment] = field(default_factory=list)
    mentions: list[Mention] = field(default_factory=list)  # 文字里要真正 @ 的人，按出现的先后


class Server:
    def __init__(self, cfg: ServerConfig, timeout: float = 15):
        self.cfg, self.timeout = cfg, timeout

    def _post(self, path: str, body: dict, timeout: float | None = None) -> dict:
        raw = self._request(path, json.dumps(body, ensure_ascii=False).encode("utf-8"), timeout)
        return json.loads(raw.decode("utf-8") or "{}")

    def _request(self, path: str, data: bytes | None = None, timeout: float | None = None) -> bytes:
        req = urllib.request.Request(
            f"{self.cfg.url}{path}",
            data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.cfg.token}"},
            method="POST" if data is not None else "GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as exc:
            try:
                detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
            except Exception:  # noqa: BLE001
                detail = ""
            if exc.code in (401, 403, 404, 409, 422):
                raise ServerRejected(f"{exc.code} {detail}") from exc
            raise ServerError(f"{exc.code} {detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ServerError(f"连不上工作台 {self.cfg.url}：{exc}") from exc

    def heartbeat(self, ready: bool, detail: str) -> None:
        self._post("/api/agent/heartbeat",
                   {"agent_id": self.cfg.agent_id, "wecom_ready": ready, "detail": detail[:1000], "version": VERSION})

    def claim(self, wait: float = 0) -> tuple[Task | None, float]:
        """领取下一条任务。wait > 0 时请工作台最多留着这次请求 wait 秒：一有任务马上返回。"""
        data = self._post("/api/agent/claim", {"agent_id": self.cfg.agent_id, "wait": wait, "features": FEATURES},
                          timeout=self.timeout + wait)
        t = data.get("task")
        task = Task(id=t["id"], group_name=t["group_name"], content=t["content"],
                    attachments=[Attachment(a["id"], a["filename"]) for a in t.get("attachments") or []],
                    mentions=[Mention(m["name"], m.get("kind") or "", m.get("user_id") or "")
                              for m in t.get("mentions") or [] if m.get("name")]) if t else None
        return task, float(data.get("retry_after") or 0)

    def report(self, task_id: str, ok: bool, error: str | None, warning: str | None = None) -> None:
        self._post(f"/api/agent/tasks/{task_id}/result",
                   {"agent_id": self.cfg.agent_id, "ok": ok, "error": (error or "")[:2000] or None,
                    "warning": (warning or "")[:2000] or None})

    def download(self, att: Attachment, folder: Path) -> Path:
        """下载要发出的图片、文件，按原文件名保存（企业微信里显示的就是这个名字）。"""
        name = Path(att.filename.replace("\\", "/")).name or att.id
        path = folder / att.id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._request(f"/api/agent/attachments/{att.id}"))
        return path
