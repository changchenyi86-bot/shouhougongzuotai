"""用假的鼠标键盘、剪贴板和识字结果，检查 WeComDriver 的发送步骤和各项核对（真机测试见使用说明）。"""

import types
from pathlib import Path

import pytest

from wecom_agent import ui
from wecom_agent import __main__ as agent_cli
from wecom_agent.config import UIConfig
from wecom_agent.runner import SendResult
from wecom_agent.ui import OcrLine, Rect, WeComDriver

RECT = Rect(0, 0, 1000, 800)
CFG = UIConfig(search_x=0.15, search_y=0.05, input_x=0.6, input_y=0.9,
               header_left=0.3, header_top=0.0, header_right=0.8, header_bottom=0.1,
               results_left=0, results_top=0.07, results_right=0.3, results_bottom=1,
               require_member_count=True, calibrated=True, search_wait=0, open_wait=0)


HEADER = RECT.box(CFG.header_left, CFG.header_top, CFG.header_right, CFG.header_bottom)


class FakeChat:
    """模拟企业微信：搜索框、当前打开的群、各群输入框、已发出的消息。"""

    def __init__(self, groups):
        self.groups = groups  # 群名 -> 人数
        self.current = "别的群"
        self.focus = None
        self.search = ""
        self.inputs: dict[str, object] = {}  # 文字，或 ("图片"/"文件", 文件名)
        self.sent: list[tuple[str, object]] = []
        self.clipboard: tuple[str, object] = ("text", "")
        self.paste_media_broken = False
        self.selected = False
        self.input_broken = False
        self.send_broken = False
        self.switch_before_send = None
        self.seq = 0  # 剪贴板修改次数
        self.popup = None  # 搜索下拉面板在屏幕上的位置；None = 这个版本没有单独的面板
        self.list_rows: list[OcrLine] = []  # 左侧会话列表里、没被搜索面板盖住的行
        self.header_reads = 0  # 标题识字了几次
        # @ 群成员
        self.members: list[str] = []  # @ 成员列表里显示的各行（外部联系人后面带来源标签，如「张三@微信」）
        self.at_query: str | None = None  # 成员列表开着时：@ 后面已经输入的筛选文字
        self.at_keys = {"unicode", "shift2"}  # 哪种按键打出的 @ 能弹出成员列表
        self.at_space = "\u2005"  # 选中成员后企业微信加在名字后面的空格
        self.at_insert = None  # 选中成员后插进输入框的名字（None = 和列表里的名字一样）
        self.at_box = (500, 500, 700, 700)
        self.picked: list[str] = []  # 在成员列表里点中过谁

    # 鼠标键盘
    def click(self, x, y):
        if self.at_query is not None:
            for line in self.at_lines():
                if line.center == (x, y):
                    name = line.text.split("@")[0]
                    text = self.inputs.get(self.current, "")
                    typed = "@" + self.at_query
                    if text.endswith(typed):
                        text = text[: -len(typed)]
                    self.inputs[self.current] = text + "@" + (self.at_insert or name) + self.at_space
                    self.picked.append(name)
                    self.at_query = None
                    return
            self.at_query = None
        if (x, y) == RECT.point(CFG.search_x, CFG.search_y):
            self.focus = "search"
        elif (x, y) == RECT.point(CFG.input_x, CFG.input_y):
            self.focus = "input"
        else:
            for line in self.result_lines():
                if line.center == (x, y):
                    self.current, self.focus = line.text.split("(")[0], None
        self.selected = False

    def press(self, key):
        if key == "esc" and self.at_query is not None:
            self.at_query = None
            self.selected = False
            return
        if key == "backspace" and self.focus == "input":
            text = self.inputs.get(self.current, "")
            self.inputs[self.current] = "" if self.selected else text[:-1]
        if key == "delete" and self.selected:
            self._set("")
        if key == "enter" and self.focus == "input" and not self.send_broken:
            if self.switch_before_send:
                self.current = self.switch_before_send
            text = self.inputs.get(self.current, "")
            if text:
                self.sent.append((self.current, text))
                self.inputs[self.current] = ""
        if key == "esc":
            self.search = ""
        self.selected = False

    def type(self, text, way="unicode"):
        """逐字输入（Unicode 键盘消息，或 Shift+2 打出的 @）。"""
        if self.focus != "input":
            return
        text_now = self.inputs.get(self.current, "")
        if self.selected or not isinstance(text_now, str):
            text_now = ""
        self.selected = False
        for ch in text:
            if self.at_query is not None:
                self.at_query = None if ch.isspace() else self.at_query + ch
            text_now += ch
            if ch == "@" and way in self.at_keys:
                self.at_query = ""
        self.inputs[self.current] = text_now

    def at_lines(self):
        rows = [m for m in self.members if (self.at_query or "") in m]
        return [OcrLine(m, 510, 510 + 30 * i, 650, 530 + 30 * i) for i, m in enumerate(rows)]

    def hotkey(self, *keys):
        if keys == ("shift", "2"):
            self.type("@", way="shift2")
        elif keys == ("ctrl", "a"):
            self.selected = True
        elif keys == ("ctrl", "v"):
            kind, value = self.clipboard
            if kind == "text" and not (self.focus == "input" and self.input_broken):
                text_now = self.inputs.get(self.current, "")
                # 粘贴插在光标处（光标总在末尾）；全选时替换掉选中的内容
                if self.focus == "input" and not self.selected and isinstance(text_now, str):
                    value = text_now + value
                self._set(value)
            elif kind != "text" and self.focus == "input" and not self.paste_media_broken:
                self._set((kind, value))
        elif keys == ("ctrl", "c") and self.selected and self.focus == "input":
            value = self.inputs.get(self.current, "")
            if isinstance(value, tuple):
                self.clipboard = value
            elif value:
                self.clipboard = ("text", value)
            if value:
                self.seq += 1
        else:
            self.selected = False

    def _set(self, text):
        if self.focus == "search":
            self.search = text
        elif self.focus == "input":
            self.inputs[self.current] = text

    def result_lines(self):
        hits = [g for g in self.groups if self.search and self.search in g]
        label = {g: g + (f"({self.groups[g]})" if self.groups[g] else "") for g in hits}
        return [OcrLine(label[g], 20, 100 + 40 * i, 200, 120 + 40 * i) for i, g in enumerate(hits)]

    def frame(self, box):
        """这块区域现在的样子（代替截图）。"""
        if box == self.at_box:
            return ("成员列表", self.at_query, tuple(line.text for line in self.at_lines()))
        if box == HEADER:
            return ("标题", self.current)
        return (box, self.search, self.current, repr(self.inputs), len(self.sent))

    def copy(self, kind, value):
        self.clipboard = (kind, value)
        self.seq += 1

    def ocr(self, box, image=None, like=None):
        if box == self.at_box:
            return self.at_lines() if self.at_query is not None else []
        if box == HEADER:
            self.header_reads += 1
            count = self.groups.get(self.current)
            return [OcrLine(self.current + (f"({count})" if count else ""), 400, 20, 600, 40)]
        if box == self.popup:
            return self.result_lines()
        return self.result_lines() + self.list_rows  # 整列：搜索结果 + 露出来的会话列表


@pytest.fixture(params=["快办法核对输入框", "慢办法核对输入框"])
def chat(monkeypatch, request):
    fake = FakeChat({"某客户-有数项目群": 23, "某客户-有数项目群二期": 8, "张三": None})
    gui = types.SimpleNamespace(click=fake.click, press=fake.press, hotkey=fake.hotkey,
                                FailSafeException=type("FailSafe", (Exception,), {}))
    clip = types.SimpleNamespace(
        set_text=lambda t: fake.copy("text", t),
        get_text=lambda: fake.clipboard[1] if fake.clipboard[0] == "text" else None,
        set_image=lambda p: fake.copy("图片", p.name),
        set_files=lambda ps: fake.copy("文件", ps[0].name),
        sequence=lambda: fake.seq,
    )
    clock = [0.0]
    monkeypatch.setattr(ui, "desktop_locked", lambda: False)
    monkeypatch.setattr(ui, "find_main_window", lambda cfg: (types.SimpleNamespace(pid=1, hwnd=1), "ok"))
    monkeypatch.setattr(ui, "type_unicode", fake.type)
    monkeypatch.setattr(ui, "list_popups",
                        lambda pid, main: {99: ("AtListWnd", fake.at_box)} if fake.at_query is not None else {})
    monkeypatch.setattr(ui, "popup_box", lambda hwnd: fake.at_box if fake.at_query is not None else None)
    monkeypatch.setattr(ui, "find_search_popup", lambda pid: fake.popup if fake.search else None)
    monkeypatch.setattr(ui, "window_alive", lambda win: True)
    monkeypatch.setattr(ui, "prepare_window", lambda win, cfg: RECT)
    monkeypatch.setattr(ui, "save_screen", lambda tag: None)
    monkeypatch.setattr(ui, "differs", lambda a, b: a != b)
    monkeypatch.setattr(ui, "same_pixels", lambda a, b: a == b)
    # 假时钟：等待不真的花时间，但“最多等多久”的逻辑照常走
    monkeypatch.setattr(ui.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))
    monkeypatch.setattr(ui.time, "monotonic", lambda: clock[0])
    driver = WeComDriver.__new__(WeComDriver)
    driver.gui, driver.cfg = gui, CFG
    driver.ocr = types.SimpleNamespace(read=fake.ocr, spent=0.0)
    driver.clip = clip
    driver._reset()
    driver._grab = lambda *boxes: [fake.frame(b) for b in boxes]
    # 快办法：认得出剪贴板是企业微信写的；慢办法：认不出，退回用占位文字读输入框
    driver._copied_by_wecom = lambda: request.param.startswith("快")
    fake.driver = driver
    fake.fast = request.param.startswith("快")
    return fake


def test_sends_to_exact_group_only(chat):
    # 搜「某客户-有数项目群」会同时搜出「…二期」，只能点完全同名的那个
    r = chat.driver.send("某客户-有数项目群", "您好，\n请清理缓存")
    assert r.ok, r.error
    assert chat.sent == [("某客户-有数项目群", "您好，\n请清理缓存")]


def test_clears_old_draft(chat):
    chat.inputs["某客户-有数项目群"] = "别人没发的草稿"
    assert chat.driver.send("某客户-有数项目群", "回复").ok
    assert chat.sent == [("某客户-有数项目群", "回复")]


def test_group_not_found(chat):
    r = chat.driver.send("不存在的群", "回复")
    assert not r.ok and "搜不到" in r.error and chat.sent == []


def test_person_with_same_name_rejected(chat):
    # 标题没有人数：是个人聊天，不是群
    chat.groups = {"张三": None}
    r = chat.driver.send("张三", "回复")
    assert not r.ok and "不是「张三」" in r.error and chat.sent == []


def test_duplicate_results_rejected(chat, monkeypatch):
    monkeypatch.setattr(chat, "result_lines", lambda: [OcrLine("同名群(3)", 20, 100, 200, 120),
                                                        OcrLine("同名群(5)", 20, 140, 200, 160)])
    r = chat.driver.send("同名群", "回复")
    assert not r.ok and "2 个同名" in r.error and chat.sent == []


@pytest.mark.parametrize("similar_name", ["测试１群", "测试 1群"])
def test_group_name_requires_exact_characters(chat, monkeypatch, similar_name):
    monkeypatch.setattr(chat, "result_lines", lambda: [OcrLine(f"{similar_name}(2)", 20, 100, 200, 120)])

    with pytest.raises(ui.UIError, match="搜不到"):
        chat.driver.open_group("测试1群")
    assert chat.current == "别的群"

    chat.groups[similar_name] = 2
    chat.current = similar_name
    with pytest.raises(ui.UIError, match="当前打开的聊天不是"):
        chat.driver.verify_header(RECT, "测试1群")


def test_paste_failed_not_sent(chat):
    chat.input_broken = True
    r = chat.driver.send("某客户-有数项目群", "回复")
    assert not r.ok and "输入框" in r.error and chat.sent == []


def test_send_key_not_working_reported(chat):
    chat.send_broken = True
    r = chat.driver.send("某客户-有数项目群", "回复")
    assert not r.ok and "没有发出" in r.error and chat.sent == []
    assert chat.inputs["某客户-有数项目群"] == ""  # 留在输入框的内容已清掉，不会被误发


@pytest.mark.parametrize("remaining", ["其他文字", ("图片", "未发.png")])
def test_text_send_requires_empty_input(chat, monkeypatch, remaining):
    original_press = chat.driver.gui.press

    def press(key):
        if key == "enter" and chat.focus == "input":
            chat.inputs[chat.current] = remaining
        else:
            original_press(key)

    monkeypatch.setattr(chat.driver.gui, "press", press)
    result = chat.driver.send("某客户-有数项目群", "回复")
    assert not result.ok and "输入框没有清空" in result.error
    assert chat.sent == []


def test_locked_screen(chat, monkeypatch):
    monkeypatch.setattr(ui, "desktop_locked", lambda: True)
    r = chat.driver.send("某客户-有数项目群", "回复")
    assert not r.ok and "锁定" in r.error


def test_images_and_files_then_text(chat):
    files = [Path("截图.png"), Path("说明.pdf")]
    r = chat.driver.send("某客户-有数项目群", "请看上面的截图", files)
    assert r.ok, r.error
    assert chat.sent == [("某客户-有数项目群", ("图片", "截图.png")), ("某客户-有数项目群", ("文件", "说明.pdf")),
                         ("某客户-有数项目群", "请看上面的截图")]


def test_only_files(chat):
    assert chat.driver.send("某客户-有数项目群", "", [Path("a.jpg")]).ok
    assert chat.sent == [("某客户-有数项目群", ("图片", "a.jpg"))]


@pytest.mark.parametrize("approved", range(5))
def test_cli_prompts_before_every_test_message(monkeypatch, approved):
    group = "指定测试群"
    image = Path("测试图片.png")
    document = Path("测试文件.txt")
    opened = []
    sent = []
    answers = iter([group, *["y"] * approved, ""])

    def send(name, content, files=None):
        sent.append((name, content, files))
        return SendResult(True)

    driver = types.SimpleNamespace(status=lambda: (True, "就绪"), open_group=opened.append, send=send)
    monkeypatch.setattr(ui, "WeComDriver", lambda cfg: driver)
    monkeypatch.setattr(agent_cli.config_mod, "load", lambda: types.SimpleNamespace(ui=CFG))
    monkeypatch.setattr(agent_cli, "_test_files", lambda folder: [image, document])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    assert agent_cli.cmd_test() == 0
    assert opened == [group]
    assert sent == [
        (group, "【测试】这是售后工作台自动发送的测试消息，请忽略。", None),
        (group, "", [image]),
        (group, "", [document]),
        (group, "【测试】上面是一张测试图片和一个测试文件，请忽略。", []),
    ][:approved]


def test_file_paste_failed_reports_progress(chat, monkeypatch):
    calls = []
    real = chat.driver.clip.set_files

    def flaky(paths):
        calls.append(paths)
        chat.paste_media_broken = True
        real(paths)

    chat.driver.clip.set_files = flaky
    r = chat.driver.send("某客户-有数项目群", "文字", [Path("a.png"), Path("b.zip")])
    assert not r.ok
    assert r.error.startswith("已发出：图片「a.png」；后面的没有发送。") and "文件「b.zip」" in r.error
    assert chat.sent == [("某客户-有数项目群", ("图片", "a.png"))]


def test_truncated_search_result_explained(chat, monkeypatch):
    # 窗口太窄时企业微信把长群名截断成「前半截…」：不能点，但要把原因说清楚
    monkeypatch.setattr(chat, "result_lines", lambda: [OcrLine("某客户-有数项...", 20, 100, 200, 120)])
    r = chat.driver.send("某客户-有数项目群", "回复")
    assert not r.ok and "显示不全" in r.error and "window_width" in r.error
    assert chat.sent == [] and chat.current == "别的群"


def test_truncated_name_only_matches_real_prefix():
    name = "测试1群-外部客户两个员工账号"
    assert ui.truncated_name(["测试1群-外部客户两个员工…"], name)
    assert ui.truncated_name(["测试1群-外部客户两"], name)  # 识字漏掉了省略号
    assert ui.truncated_name([name], name) is None  # 完整的群名不算
    assert ui.truncated_name(["测试"], name) is None  # 太短，可能是别的群
    assert ui.truncated_name(["测试2群-外部客户两个员工…"], name) is None


@pytest.mark.parametrize("scale, work, expected", [
    (1.0, (1920, 1040), (1600, 900)),
    (2.0, (2880, 1704), (2800, 1624)),  # 真机：2880×1800、缩放 200%，原来的 1280 太窄
    (1.5, (1920, 1040), (1840, 960)),
    (1.0, (1280, 720), (1280, 860)),  # 屏幕太小时不比原来的默认值更小
])
def test_fit_window_size(scale, work, expected):
    assert ui.fit_window_size(scale, *work, 40, 40) == expected


@pytest.mark.parametrize("text", ["1233321", "666", "测试 123", "", " 42 ", "１２３"])
def test_clipboard_text_never_looks_like_a_handle(monkeypatch, text):
    # 真机联调：回复「1233321」时 pywin32 把纯数字的文字当成句柄编号，报「句柄无效」。
    # 现在交给它的是带结尾符的 UTF-16 字节，int() 认不出来，就不会被误当成句柄。
    import sys

    from wecom_agent.clipboard import Clipboard

    stored = {}
    fake_cb = types.SimpleNamespace(OpenClipboard=lambda: None, EmptyClipboard=lambda: None,
                                    CloseClipboard=lambda: None,
                                    SetClipboardData=lambda fmt, data: stored.update(fmt=fmt, data=data))
    monkeypatch.setitem(sys.modules, "win32clipboard", fake_cb)
    monkeypatch.setitem(sys.modules, "win32con", types.SimpleNamespace(CF_UNICODETEXT=13, CF_DIB=8, CF_HDROP=15))
    Clipboard().set_text(text)
    data = stored["data"]
    assert stored["fmt"] == 13 and isinstance(data, bytes)
    assert data.decode("utf-16-le") == text + "\0"
    with pytest.raises(ValueError):
        int(data)


def test_error_before_send_key_reported_as_not_sent(chat, monkeypatch):
    real = chat.driver.clip.set_text

    def set_text(text):
        if text == "1233321":
            raise OSError(6, "SetClipboardData", "句柄无效。")
        real(text)

    monkeypatch.setattr(chat.driver.clip, "set_text", set_text)
    r = chat.driver.send("某客户-有数项目群", "1233321", [Path("a.png")])
    assert not r.ok and r.error.startswith("已发出：图片「a.png」；发送程序出错，后面的没有发送")
    assert chat.sent == [("某客户-有数项目群", ("图片", "a.png"))]
    r = chat.driver.send("某客户-有数项目群", "1233321")
    assert not r.ok and r.error.startswith("发送程序出错，没有发送") and "未确认" not in r.error


def test_error_after_send_key_reported_as_unknown(chat, monkeypatch):
    real = chat.driver.clip.set_text

    def set_text(text):
        if text == ui.CLIP_SENTINEL and chat.sent:
            raise OSError("剪贴板被占用")  # 按下发送键之后读输入框时出错
        real(text)

    monkeypatch.setattr(chat.driver.clip, "set_text", set_text)
    r = chat.driver.send("某客户-有数项目群", "回复")
    assert not r.ok and "未确认最后一条是否发出" in r.error


def test_chat_switched_before_send_key_not_sent(chat, monkeypatch):
    # 回复已经填进输入框、还没按发送键时，界面被切到了别的聊天：按发送键前的核对要拦住
    real = chat.driver.gui.hotkey

    def hotkey(*keys):
        real(*keys)
        if keys == ("ctrl", "end"):
            chat.current, chat.focus = "某客户-有数项目群二期", "input"

    monkeypatch.setattr(chat.driver.gui, "hotkey", hotkey)
    r = chat.driver.send("某客户-有数项目群", "回复")
    assert not r.ok and "当前打开的聊天不是" in r.error
    assert chat.sent == []


def test_header_checked_again_before_every_send_key(chat):
    # 每次按发送键前都重新截屏看标题：画面和刚识字核对过的一个像素都不差时不用重认，变了就必须重认
    assert chat.driver.send("某客户-有数项目群", "回复").ok
    assert chat.header_reads == 1

    frames = iter(range(100))
    real = chat.frame
    chat.frame = lambda box: ("标题", next(frames)) if box == HEADER else real(box)  # 标题区每次截屏都不一样
    chat.header_reads = 0
    assert chat.driver.send("某客户-有数项目群", "再回复").ok
    assert chat.header_reads >= 2


def test_search_results_reread_until_stable(chat, monkeypatch):
    # 识字期间搜索结果又多出一条同名的群：必须按新的列表重认，发现重名后拒绝发送
    monkeypatch.setattr(chat.driver.cfg, "search_wait", 1.5)
    one = [OcrLine("同名群(3)", 20, 100, 200, 120)]
    two = one + [OcrLine("同名群(5)", 20, 140, 200, 160)]
    reads = []

    def result_lines():
        return two if reads else one

    real_ocr = chat.ocr

    def ocr(box, image=None, like=None):
        lines = real_ocr(box, image, like)
        if box != HEADER:
            reads.append(len(lines))  # 第一次识字完成后，第二条才显示出来
        return lines

    real_frame = chat.frame
    monkeypatch.setattr(chat, "result_lines", result_lines)
    monkeypatch.setattr(chat, "frame", lambda box: (real_frame(box), len(result_lines())))
    chat.driver.ocr.read = ocr
    r = chat.driver.send("同名群", "回复")
    assert reads == [1, 2]
    assert not r.ok and "2 个同名" in r.error and chat.sent == []


def test_slow_search_still_found(chat, monkeypatch):
    # 搜索结果出来得慢：没搜到时不立刻报错，等到列表出现变化再认（最多等 search_wait 秒）
    monkeypatch.setattr(chat.driver.cfg, "search_wait", 1.5)
    real_lines = chat.result_lines
    started = ui.time.monotonic()
    monkeypatch.setattr(chat, "result_lines", lambda: real_lines() if ui.time.monotonic() - started > 1.0 else [])
    real_frame = chat.frame
    monkeypatch.setattr(chat, "frame", lambda box: (real_frame(box), len(chat.result_lines())))
    r = chat.driver.send("某客户-有数项目群", "回复")
    assert r.ok, r.error
    assert chat.sent == [("某客户-有数项目群", "回复")]


def test_timing_logged(chat, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="wecom_agent"):
        assert chat.driver.send("某客户-有数项目群", "回复").ok
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("用时"))
    for step in ("窗口", "等搜索结果", "识别结果", "打开群", "填写并核对", "核对群名并按发送", "确认已发出"):
        assert step in line
    assert ("慢办法" in line) == (not chat.fast)


def test_window_placement_skipped_when_already_in_place(monkeypatch):
    calls = []
    rects = {7: (40, 40, 1280, 860)}
    fake32 = types.SimpleNamespace(
        IsWindowVisible=lambda h: True, IsIconic=lambda h: False, IsZoomed=lambda h: False,
        SetWindowPos=lambda h, z, x, y, w, hh, f: (calls.append("move"), rects.__setitem__(h, (x, y, w - 6, hh))),
        GetForegroundWindow=lambda: 7, GetAncestor=lambda h, f: h,
    )
    monkeypatch.setattr(ui, "user32", fake32, raising=False)
    monkeypatch.setattr(ui, "_window_rect", lambda h: rects[h])
    monkeypatch.setattr(ui, "_placed", {})
    monkeypatch.setattr(ui.time, "sleep", lambda s: calls.append("sleep"))
    win = ui.Window(7, "企业微信", "cls", True, 1280, 860, 99)
    cfg = UIConfig()
    assert ui.prepare_window(win, cfg) == Rect(40, 40, 1280, 860)
    assert calls == []  # 已经在指定位置、已经在最前面：不移动、不等待

    rects[7] = (300, 200, 900, 700)  # 窗口被人拖动过：摆回去。企业微信没完全接受指定的宽度
    assert ui.prepare_window(win, cfg) == Rect(40, 40, 1274, 860)
    assert calls == ["move", "sleep"]
    calls.clear()
    assert ui.prepare_window(win, cfg) == Rect(40, 40, 1274, 860)
    assert calls == []  # 停在上次摆好的位置：不用每次都再摆一遍


def test_keep_full_speed_asks_windows_not_to_throttle(monkeypatch):
    import ctypes

    calls = []

    class Kernel32:
        class _Fn:
            def __init__(self, fn):
                self.fn = fn

            def __call__(self, *args):
                return self.fn(*args)

        def __init__(self, known_masks):
            def set_info(handle, info_class, state, size):
                control, on = ctypes.cast(state, ctypes.POINTER(ctypes.c_uint32 * 3)).contents[1:]
                calls.append((info_class, control, on, size))
                return 1 if control in known_masks else 0

            self.GetCurrentProcess = self._Fn(lambda: -1)
            self.SetProcessInformation = self._Fn(set_info)

    fake_types = types.SimpleNamespace(ULONG=ctypes.c_uint32, HANDLE=ctypes.c_void_p, DWORD=ctypes.c_uint32,
                                       BOOL=ctypes.c_int)
    monkeypatch.setattr(ui, "wintypes", fake_types, raising=False)
    monkeypatch.setattr(ui.ctypes, "WinDLL", lambda name, use_last_error=False: Kernel32({0x5, 0x1}), raising=False)
    assert ui.keep_full_speed() is True
    assert calls == [(4, 0x5, 0, 12)]  # 4 = 省电限速这一项；状态为 0 = 运行速度和计时精度都不限

    calls.clear()  # 旧一些的系统不认识“计时精度”：只设运行速度
    monkeypatch.setattr(ui.ctypes, "WinDLL", lambda name, use_last_error=False: Kernel32({0x1}), raising=False)
    assert ui.keep_full_speed() is True
    assert [c[1] for c in calls] == [0x5, 0x1]

    monkeypatch.setattr(ui.ctypes, "WinDLL", lambda name, use_last_error=False: Kernel32(set()), raising=False)
    assert ui.keep_full_speed() is False  # 设不了也不报错，只是慢一些


POPUP = (10, 60, 320, 400)


def test_conversation_list_row_is_not_a_search_result(chat):
    # 真机上出现过：要发的群正好露在搜索面板下面的会话列表里，被数成了“2 个同名结果”。
    # 只在搜索面板里找结果，会话列表里的那一行就不算。
    chat.list_rows = [OcrLine("某客户-有数项目群(23)", 60, 500, 260, 520)]
    r = chat.driver.send("某客户-有数项目群", "回复")
    assert not r.ok and "2 个同名" in r.error  # 没有单独面板的版本：分不出来，宁可不发

    chat.popup = POPUP
    r = chat.driver.send("某客户-有数项目群", "回复")
    assert r.ok, r.error
    assert chat.sent == [("某客户-有数项目群", "回复")]


def test_two_same_name_groups_in_popup_still_rejected(chat, monkeypatch):
    chat.popup = POPUP
    monkeypatch.setattr(chat, "result_lines", lambda: [OcrLine("同名群(3)", 20, 100, 200, 120),
                                                        OcrLine("同名群(5)", 20, 140, 200, 160)])
    r = chat.driver.send("同名群", "回复")
    assert not r.ok and "2 个同名" in r.error and chat.sent == []


def test_search_failure_keeps_what_was_seen(chat, monkeypatch, caplog):
    import logging

    events = []
    real_press = chat.driver.gui.press
    monkeypatch.setattr(ui, "save_screen", lambda tag: events.append(tag) or f"{tag}.png")
    monkeypatch.setattr(chat.driver.gui, "press", lambda key: (events.append(key), real_press(key)))
    chat.popup = POPUP
    with caplog.at_level(logging.WARNING, logger="wecom_agent"):
        r = chat.driver.send("某客户-有数", "回复")  # 只搜得到名字更长的群
    assert not r.ok and "搜不到" in r.error
    assert events[:2] == ["search", "esc"]  # 先截图，再关搜索面板
    seen = next(rec.getMessage() for rec in caplog.records if "搜索没通过" in rec.getMessage())
    assert "搜索面板" in seen and "某客户-有数项目群(23)@(20,100)" in seen and "search.png" in seen


def test_timing_notes_when_popup_not_found(chat, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="wecom_agent"):
        assert chat.driver.send("某客户-有数项目群", "回复").ok
        chat.popup = POPUP
        assert chat.driver.send("某客户-有数项目群", "再回复").ok
    first, second = [r.getMessage() for r in caplog.records if r.getMessage().startswith("用时")]
    assert "没找到搜索面板" in first and "没找到搜索面板" not in second


# ---------- @ 群成员 ----------

G = "某客户-有数项目群"


def at(name, kind=""):
    from wecom_agent.mention import Mention

    return Mention(name, kind)


def test_mention_picked_from_member_list(chat):
    # 筛「张三」时「张三丰」排在前面，要点的是「张三@微信」那一行
    chat.members = ["张三丰", "张三@微信", "李四"]
    r = chat.driver.send(G, "@张三 您好，请清理缓存", mentions=[at("张三", "wechat")])
    assert r.ok and r.warning is None, (r.error, r.warning)
    assert chat.picked == ["张三"]
    # 企业微信 @ 完自己加了空格，回复里名字后面的空格不再重复
    assert chat.sent == [(G, "@张三 您好，请清理缓存")]


def test_two_mentions_in_the_middle(chat):
    chat.members = ["李四", "张三@微信"]
    r = chat.driver.send(G, "好的 @李四 @张三 麻烦看下", mentions=[at("李四", "internal"), at("张三", "wechat")])
    assert r.ok and r.warning is None
    assert chat.picked == ["李四", "张三"]
    assert chat.sent == [(G, "好的 @李四 @张三 麻烦看下")]


def test_mention_when_wecom_adds_no_space(chat):
    chat.members = ["张三"]
    chat.at_space = ""
    r = chat.driver.send(G, "@张三 您好", mentions=[at("张三")])
    assert r.ok and r.warning is None and chat.sent == [(G, "@张三 您好")]


@pytest.mark.parametrize("kind, row", [("wechat", "张三@微信"), ("internal", "张三"), ("wecom", "张三@某某科技")])
def test_same_name_told_apart_by_source(chat, kind, row):
    chat.members = ["张三", "张三@微信", "张三@某某科技"]
    r = chat.driver.send(G, "@张三 您好", mentions=[at("张三", kind)])
    assert r.ok and r.warning is None
    assert chat.picked == [row.split("@")[0]] and len(chat.sent) == 1


def test_same_name_cannot_tell_sends_plain_text_and_warns(chat):
    chat.members = ["张三@微信", "张三@微信"]
    r = chat.driver.send(G, "@张三 您好", mentions=[at("张三", "wechat")])
    assert r.ok
    assert r.warning == "「张三」（群里有 2 个叫「张三」的人，分不清是哪一个）没 @ 上，按普通文字发出，对方收不到 @ 提醒"
    assert chat.picked == [] and chat.sent == [(G, "@张三 您好")]


def test_member_not_in_list_sends_plain_text(chat):
    chat.members = ["李四"]
    r = chat.driver.send(G, "@张三 您好", mentions=[at("张三")])
    assert r.ok and "没有找到「张三」" in r.warning
    assert chat.sent == [(G, "@张三 您好")]


def test_no_member_list_sends_plain_text(chat):
    chat.members = ["张三"]
    chat.at_keys = set()
    r = chat.driver.send(G, "@张三 您好", mentions=[at("张三")])
    assert r.ok and "没有弹出成员列表" in r.warning
    assert chat.sent == [(G, "@张三 您好")]


def test_at_typed_with_shift2_when_unicode_does_not_pop_up(chat):
    chat.members = ["张三"]
    chat.at_keys = {"shift2"}
    r = chat.driver.send(G, "@张三 您好", mentions=[at("张三")])
    assert r.ok and r.warning is None and chat.sent == [(G, "@张三 您好")]
    assert chat.driver._at_keys == "shift2"  # 记住了，下次直接用


def test_unexpected_input_falls_back_to_plain_text(chat):
    # 企业微信插进来的不是这个名字：整段按普通文字重新粘贴，所有人都算没 @ 上
    chat.members = ["张三"]
    chat.at_insert = "张三（销售）"
    r = chat.driver.send(G, "@张三 您好", mentions=[at("张三")])
    assert r.ok and "「张三」" in r.warning
    assert chat.sent == [(G, "@张三 您好")]


def test_mention_missing_from_text(chat):
    r = chat.driver.send(G, "您好", mentions=[at("王五")])
    assert r.ok and "找不到「@王五」" in r.warning and chat.sent == [(G, "您好")]
