"""电脑端发送程序中不依赖 Windows 的部分：群名核对、配置读写、主循环。"""

from wecom_agent import config as config_mod
from wecom_agent.config import AgentConfig
from wecom_agent.matching import find_title, match_title
from wecom_agent.runner import Runner, SendResult
from wecom_agent.server import Attachment, ServerError, ServerRejected, Task


# ---------- 群名核对 ----------


def test_match_title_exact_with_count_and_tags():
    assert match_title("某客户-有数项目群", "某客户-有数项目群").member_count is None
    assert match_title("某客户-有数项目群(23)", "某客户-有数项目群").member_count == 23
    assert match_title("某客户 - 有数项目群（23）外部", "某客户-有数项目群").member_count == 23  # 全角括号、空格
    assert match_title("某客户一有数项目群", "某客户-有数项目群")  # OCR 把横线认成「一」
    assert match_title("abc数据群", "ABC数据群")
    # 不做模糊匹配：多一个字、少一个字、前缀都不算
    for text in ("某客户-有数项目群2", "某客户-有数项目", "A某客户-有数项目群", "某客户-有数项目群(23)x"):
        assert match_title(text, "某客户-有数项目群") is None
    assert match_title("", "") is None


def test_find_title_split_and_require_count():
    assert find_title(["某客户群", "(12)"], "某客户群", require_count=True).member_count == 12
    assert find_title(["某客户群"], "某客户群", require_count=True) is None  # 同名的个人聊天没有人数
    assert find_title(["其他群(3)", "设置"], "某客户群") is None


# ---------- 配置 ----------


def test_config_roundtrip(tmp_path):
    path = tmp_path / "config.ini"
    cfg = config_mod.load(path)
    assert cfg.server.agent_id == "客服电脑1" and not cfg.ui.calibrated
    cfg.server.url, cfg.server.token = "http://10.0.0.5:8000/", "abc"
    cfg.ui.search_x, cfg.ui.calibrated, cfg.ui.window_width = 0.1234, True, 1400
    config_mod.save(cfg, path)
    loaded = config_mod.load(path)
    assert loaded.server.url == "http://10.0.0.5:8000" and loaded.server.token == "abc"
    assert loaded.ui.search_x == 0.1234 and loaded.ui.calibrated is True and loaded.ui.window_width == 1400


# ---------- 主循环 ----------


class FakeServer:
    def __init__(self):
        self.tasks: list[Task] = []
        self.reports: list[tuple[str, bool, str | None]] = []
        self.warnings: list[tuple[str, str]] = []
        self.fail_reports = 0
        self.reject_report = False
        self.claim_error: Exception | None = None
        self.retry_after = 0.0
        self.heartbeats: list[tuple[bool, str]] = []
        self.waits: list[float] = []
        self.long_poll = False
        self.clock = None

    def heartbeat(self, ready, detail):
        self.heartbeats.append((ready, detail))

    def claim(self, wait=0):
        self.waits.append(wait)
        if self.claim_error:
            raise self.claim_error
        if not self.tasks and self.long_poll and self.clock is not None:
            self.clock.t += wait  # 新版工作台：没有任务时留着请求，等满 wait 秒才回
        return (self.tasks.pop(0) if self.tasks else None), self.retry_after

    def download(self, att, folder):
        if att.id == "bad":
            raise ServerError("404")
        path = folder / att.filename
        path.write_bytes(b"x")
        return path

    def report(self, task_id, ok, error, warning=None):
        if warning:
            self.warnings.append((task_id, warning))
        if self.reject_report:
            raise ServerRejected("409")
        if self.fail_reports:
            self.fail_reports -= 1
            raise ServerError("network")
        self.reports.append((task_id, ok, error))


class FakeDriver:
    def __init__(self, results=None):
        self.sent: list[tuple[str, str]] = []
        self.mentions: list = []
        self.results = results or []

    def status(self):
        return True, "企业微信已就绪"

    def send(self, group_name, content, files=None, mentions=None):
        self.sent.append((group_name, content) if not files else (group_name, content, [f.name for f in files]))
        self.mentions.append(mentions)
        r = self.results.pop(0) if self.results else SendResult(True)
        if isinstance(r, Exception):
            raise r
        return r


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def _runner(server, driver, **kw):
    clock = Clock()
    cfg = AgentConfig(poll_seconds=3, heartbeat_seconds=15, min_interval_seconds=5, **kw)
    return Runner(server, driver, cfg, sleep=lambda s: None, clock=clock), clock


def test_send_and_report():
    server, driver = FakeServer(), FakeDriver([SendResult(True), SendResult(False, "搜不到群")])
    server.tasks = [Task("t1", "群A", "你好"), Task("t2", "群B", "在的")]
    runner, clock = _runner(server, driver)
    assert runner.step() == 0
    assert driver.sent == [("群A", "你好")]
    # 两次发送之间至少间隔 5 秒
    assert runner.step() == 5 and server.reports == [("t1", True, None)]
    clock.t += 5
    runner.step()
    clock.t += 5
    assert runner.step() == 3  # 回报 t2，然后没有任务了
    assert server.reports[-1] == ("t2", False, "搜不到群")


def test_report_retried_until_accepted_and_no_new_claim_meanwhile():
    server, driver = FakeServer(), FakeDriver()
    server.tasks = [Task("t1", "群A", "你好"), Task("t2", "群B", "在的")]
    server.fail_reports = 2
    runner, clock = _runner(server, driver)
    runner.step()
    clock.t += 10
    assert runner.step() == 3 and runner.step() == 3  # 网络问题，回报失败，先不领新任务
    assert len(driver.sent) == 1
    runner.step()
    assert server.reports == [("t1", True, None)] and len(driver.sent) == 2


def test_rejected_report_dropped():
    server, driver = FakeServer(), FakeDriver()
    server.tasks = [Task("t1", "群A", "你好")]
    server.reject_report = True
    runner, clock = _runner(server, driver)
    runner.step()
    clock.t += 10
    runner.step()
    assert runner.pending is None


def test_driver_crash_reported_as_unknown():
    server, driver = FakeServer(), FakeDriver([RuntimeError("boom")])
    server.tasks = [Task("t1", "群A", "你好")]
    runner, clock = _runner(server, driver)
    runner.step()
    clock.t += 10
    runner.step()
    task_id, ok, error = server.reports[0]
    assert not ok and "确认" in error and "boom" in error


def test_claim_errors_and_rate_limit():
    server, driver = FakeServer(), FakeDriver()
    runner, _ = _runner(server, driver)
    server.retry_after = 7
    assert runner.step() == 7
    server.claim_error = ServerError("down")
    assert runner.step() == 10


def test_heartbeat():
    server, driver = FakeServer(), FakeDriver()
    runner, _ = _runner(server, driver)
    runner.heartbeat_once()
    assert server.heartbeats == [(True, "企业微信已就绪")]


def test_attachments_downloaded_and_passed():
    server, driver = FakeServer(), FakeDriver()
    server.tasks = [Task("t1", "群A", "看图", [Attachment("a1", "截图.png")]), Task("t2", "群A", "", [Attachment("bad", "x.pdf")])]
    runner, clock = _runner(server, driver)
    runner.step()
    assert driver.sent == [("群A", "看图", ["截图.png"])]
    clock.t += 10
    runner.step()
    clock.t += 10
    runner.step()
    assert len(driver.sent) == 1  # 附件下载失败，不发送
    assert server.reports[-1][1] is False and "下载" in server.reports[-1][2]


def test_long_poll_asks_again_immediately():
    # 新版工作台会留着领任务的请求，一有任务马上返回：它等满了还没有任务，就立刻接着问，不再空等 3 秒
    server, driver = FakeServer(), FakeDriver()
    runner, clock = _runner(server, driver)
    server.long_poll, server.clock = True, clock
    assert runner.step() == 0
    assert server.waits == [20]
    # 旧版工作台不会等，立刻回“没有任务”：还是每 3 秒问一次，不会变成不停地问
    server.long_poll = False
    assert runner.step() == 3


def test_long_poll_can_be_turned_off():
    server, driver = FakeServer(), FakeDriver()
    runner, _ = _runner(server, driver, long_poll_seconds=0)
    assert runner.step() == 3 and server.waits == [0]


def test_mentions_passed_to_driver_and_warning_reported():
    from wecom_agent.mention import Mention

    server = FakeServer()
    driver = FakeDriver([SendResult(True, warning="「张三」（群里有 2 个叫「张三」的人，分不清是哪一个）没 @ 上")])
    at = [Mention("张三", "wechat", "wmA")]
    server.tasks = [Task("t1", "群A", "@张三 你好", mentions=at)]
    runner, clock = _runner(server, driver)
    runner.step()
    assert driver.mentions == [at]
    clock.t += 5
    runner.step()
    assert server.reports == [("t1", True, None)]
    assert server.warnings == [("t1", "「张三」（群里有 2 个叫「张三」的人，分不清是哪一个）没 @ 上")]


def test_claim_says_it_can_mention_and_reads_mentions(monkeypatch):
    from wecom_agent.config import ServerConfig
    from wecom_agent.server import Server

    sent = []

    def post(path, body, timeout=None):
        sent.append((path, body))
        if path.endswith("/claim"):
            return {"task": {"id": "t1", "group_name": "群A", "content": "@张三 你好",
                             "mentions": [{"name": "张三", "kind": "wechat", "user_id": "wmA"}, {"name": ""}]},
                    "retry_after": 0}
        return {}

    server = Server(ServerConfig())
    monkeypatch.setattr(server, "_post", post)
    task, _ = server.claim(5)
    assert sent[0][1]["features"] == ["mentions"]
    assert [(m.name, m.kind, m.user_id) for m in task.mentions] == [("张三", "wechat", "wmA")]
    server.report("t1", True, None, "没 @ 上")
    assert sent[-1][1]["warning"] == "没 @ 上" and sent[-1][1]["error"] is None


# ---------- @ 群成员的文字处理 ----------


def test_split_mentions_in_order_and_repeated():
    from wecom_agent.mention import Mention, split

    a, b = Mention("张三"), Mention("李四")
    parts, missing = split("@张三 你好，@李四 和 @张三 看下", [a, b, a, Mention("王五")])
    assert parts == [a, " 你好，", b, " 和 ", a, " 看下"]
    assert [m.name for m in missing] == ["王五"]


def test_query_stops_at_space_and_is_short():
    from wecom_agent.mention import query_of

    assert query_of("John Smith") == "John"
    assert query_of("国投证券_朱燕军") == "国投证券_朱燕军"
    assert len(query_of("一" * 30)) == 12


def test_pick_prefers_exact_and_tolerates_width_differences():
    from wecom_agent.mention import Mention, pick

    m = Mention("国投证券_朱燕军", "wechat")
    # 识字把半角下划线认成全角：只差全角半角也认
    assert pick(["国投证券＿朱燕军@微信", "朱燕军"], m) == (0, "")
    # 逐字相同的优先
    assert pick(["国投证券＿朱燕军", "国投证券_朱燕军@微信"], m) == (1, "")
    # 名字只是开头一样的不算
    assert pick(["国投证券_朱燕军2"], m)[0] is None


def test_comparable_ignores_wecom_space_after_mention():
    from wecom_agent.mention import same_text

    assert same_text("@张三 您好", "@张三 您好", ["张三"])
    assert same_text("@张三 您好\r\n第二行", "@张三您好\n第二行", ["张三"])
    assert not same_text("@张三 您好", "@张三 你好", ["张三"])
