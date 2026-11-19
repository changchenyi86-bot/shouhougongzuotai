"""和工作台后端通信（只用标准库）。"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from wecom_agent import VERSION
from wecom_agent.config import ServerConfig


class ServerError(Exception):
    """网络不通或服务端出错，稍后重试即可。"""


class ServerRejected(Exception):
    """服务端明确拒绝（口令错误、任务冲突等），重试也没用。"""


@dataclass
class Task:
    id: str
    group_name: str
    content: str


class Server:
    def __init__(self, cfg: ServerConfig, timeout: float = 15):
        self.cfg, self.timeout = cfg, timeout

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(
            f"{self.cfg.url}{path}",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.cfg.token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
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

    def claim(self) -> tuple[Task | None, float]:
        data = self._post("/api/agent/claim", {"agent_id": self.cfg.agent_id})
        t = data.get("task")
        task = Task(id=t["id"], group_name=t["group_name"], content=t["content"]) if t else None
        return task, float(data.get("retry_after") or 0)

    def report(self, task_id: str, ok: bool, error: str | None) -> None:
        self._post(f"/api/agent/tasks/{task_id}/result",
                   {"agent_id": self.cfg.agent_id, "ok": ok, "error": (error or "")[:2000] or None})
