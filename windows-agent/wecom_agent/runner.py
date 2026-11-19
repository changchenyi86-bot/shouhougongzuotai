"""主循环：心跳、领取任务、操作企业微信发送、回报结果。

不依赖 Windows，便于测试；真正操作界面的是 ui.WeComDriver。
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from wecom_agent.config import AgentConfig
from wecom_agent.server import Server, ServerError, ServerRejected, Task

log = logging.getLogger("wecom_agent")


@dataclass
class SendResult:
    ok: bool
    error: str | None = None


class Driver(Protocol):
    def status(self) -> tuple[bool, str]:
        """(企业微信是否可用, 说明)。"""

    def send(self, group_name: str, content: str) -> SendResult:
        """打开群、核对群名、发送。任何一步不确定都要返回失败，不能发错群。"""


class Runner:
    def __init__(self, server: Server, driver: Driver, cfg: AgentConfig, sleep=time.sleep, clock=time.monotonic):
        self.server, self.driver, self.cfg = server, driver, cfg
        self.sleep, self.clock = sleep, clock
        self.pending: tuple[str, SendResult] | None = None  # 已发送但还没成功回报的结果
        self.last_send: float | None = None
        self.stop = threading.Event()
        self.ui_lock = threading.Lock()  # 发送时不做状态检查，避免抢窗口

    # ---------- 心跳 ----------

    def heartbeat_once(self) -> None:
        with self.ui_lock:
            ready, detail = self.driver.status()
        try:
            self.server.heartbeat(ready, detail)
        except (ServerError, ServerRejected) as exc:
            log.warning("心跳失败：%s", exc)

    def heartbeat_loop(self) -> None:
        while not self.stop.is_set():
            try:
                self.heartbeat_once()
            except Exception:  # noqa: BLE001  心跳出错不能让程序退出
                log.exception("检查企业微信状态出错")
            self.stop.wait(self.cfg.heartbeat_seconds)

    # ---------- 发送 ----------

    def step(self) -> float:
        """处理一轮，返回下一轮前要等的秒数。"""
        if self.pending:
            task_id, result = self.pending
            try:
                self.server.report(task_id, result.ok, result.error)
            except ServerError as exc:
                log.warning("回报结果失败，稍后重试：%s", exc)
                return self.cfg.poll_seconds
            except ServerRejected as exc:
                log.error("服务端拒收结果，放弃回报：%s", exc)
            self.pending = None

        if self.last_send is not None:
            wait = self.cfg.min_interval_seconds - (self.clock() - self.last_send)
            if wait > 0:
                return wait

        try:
            task, retry_after = self.server.claim()
        except (ServerError, ServerRejected) as exc:
            log.warning("领取任务失败：%s", exc)
            return max(self.cfg.poll_seconds, 10)
        if task is None:
            return max(self.cfg.poll_seconds, retry_after)

        self.pending = (task.id, self._send(task))
        self.last_send = self.clock()
        return 0

    def _send(self, task: Task) -> SendResult:
        log.info("开始发送 %s 到「%s」", task.id, task.group_name)
        with self.ui_lock:
            try:
                result = self.driver.send(task.group_name, task.content)
            except Exception as exc:  # noqa: BLE001
                log.exception("发送出错")
                result = SendResult(False, f"发送程序出错，未确认是否发出，请到群里确认：{exc}")
        if result.ok:
            log.info("已发送 %s", task.id)
        else:
            log.warning("发送失败 %s：%s", task.id, result.error)
        return result

    def run(self) -> None:
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        while not self.stop.is_set():
            wait = self.step()
            if wait > 0:
                self.sleep(wait)
