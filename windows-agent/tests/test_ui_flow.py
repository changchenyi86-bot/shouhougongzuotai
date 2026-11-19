"""用假的鼠标键盘、剪贴板和识字结果，检查 WeComDriver 的发送步骤和各项核对（真机测试见使用说明）。"""

import sys
import types

import pytest

from wecom_agent import ui
from wecom_agent.config import UIConfig
from wecom_agent.ui import OcrLine, Rect, WeComDriver

RECT = Rect(0, 0, 1000, 800)
CFG = UIConfig(search_x=0.15, search_y=0.05, input_x=0.6, input_y=0.9,
               header_left=0.3, header_top=0.0, header_right=0.8, header_bottom=0.1,
               results_left=0, results_top=0.07, results_right=0.3, results_bottom=1,
               require_member_count=True, calibrated=True, search_wait=0, open_wait=0)


class FakeChat:
    """模拟企业微信：搜索框、当前打开的群、各群输入框、已发出的消息。"""

    def __init__(self, groups):
        self.groups = groups  # 群名 -> 人数
        self.current = "别的群"
        self.focus = None
        self.search = ""
        self.inputs: dict[str, str] = {}
        self.sent: list[tuple[str, str]] = []
        self.clipboard = ""
        self.selected = False
        self.input_broken = False
        self.send_broken = False
        self.switch_before_send = None

    # 鼠标键盘
    def click(self, x, y):
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

    def hotkey(self, *keys):
        if keys == ("ctrl", "a"):
            self.selected = True
        elif keys == ("ctrl", "v"):
            if not (self.focus == "input" and self.input_broken):
                self._set(self.clipboard)
        elif keys == ("ctrl", "c") and self.selected and self.focus == "input":
            self.clipboard = self.inputs.get(self.current, "") or self.clipboard
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

    def ocr(self, box):
        if box == RECT.box(CFG.header_left, CFG.header_top, CFG.header_right, CFG.header_bottom):
            count = self.groups.get(self.current)
            return [OcrLine(self.current + (f"({count})" if count else ""), 400, 20, 600, 40)]
        return self.result_lines()


@pytest.fixture()
def chat(monkeypatch):
    fake = FakeChat({"某客户-有数项目群": 23, "某客户-有数项目群二期": 8, "张三": None})
    gui = types.SimpleNamespace(click=fake.click, press=fake.press, hotkey=fake.hotkey,
                                FailSafeException=type("FailSafe", (Exception,), {}))
    clip = types.SimpleNamespace(copy=lambda t: setattr(fake, "clipboard", t), paste=lambda: fake.clipboard)
    monkeypatch.setitem(sys.modules, "pyperclip", clip)
    monkeypatch.setattr(ui, "desktop_locked", lambda: False)
    monkeypatch.setattr(ui, "find_main_window", lambda cfg: (object(), "ok"))
    monkeypatch.setattr(ui, "prepare_window", lambda win, cfg: RECT)
    monkeypatch.setattr(ui, "save_screen", lambda tag: None)
    monkeypatch.setattr(ui.time, "sleep", lambda s: None)
    driver = WeComDriver.__new__(WeComDriver)
    driver.gui, driver.cfg = gui, CFG
    driver.ocr = types.SimpleNamespace(read=fake.ocr)
    fake.driver = driver
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


def test_paste_failed_not_sent(chat):
    chat.input_broken = True
    r = chat.driver.send("某客户-有数项目群", "回复")
    assert not r.ok and "输入框" in r.error and chat.sent == []


def test_send_key_not_working_reported(chat):
    chat.send_broken = True
    r = chat.driver.send("某客户-有数项目群", "回复")
    assert not r.ok and "没有发出" in r.error and chat.sent == []
    assert chat.inputs["某客户-有数项目群"] == ""  # 留在输入框的内容已清掉，不会被误发


def test_locked_screen(chat, monkeypatch):
    monkeypatch.setattr(ui, "desktop_locked", lambda: True)
    r = chat.driver.send("某客户-有数项目群", "回复")
    assert not r.ok and "锁定" in r.error
