"""操作官方企业微信 Windows 客户端。

只做一个人坐在电脑前会做的事：把企业微信窗口切到前面、点搜索框输入群名、点开搜索结果、
在输入框粘贴回复、按发送键。每一步都截屏识字核对，任何一步对不上就报失败，不发送。
不读写企业微信进程内存、不注入、不模拟通信协议。
"""

import ctypes
import logging
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from wecom_agent.config import LOG_DIR, UIConfig
from wecom_agent.matching import find_title, match_title, normalize
from wecom_agent.runner import SendResult

log = logging.getLogger("wecom_agent")

if sys.platform == "win32":
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                    ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                    ("rcNormalPosition", wintypes.RECT)]

SW_SHOW, SW_RESTORE = 5, 9
SWP_NOZORDER, SWP_NOACTIVATE = 0x0004, 0x0010
GA_ROOTOWNER = 3
MAIN_TITLE = "企业微信"
CLIP_SENTINEL = "__wecom_agent_empty__"


class UIError(Exception):
    """操作没完成、消息没有发出。文字直接显示给工作台的用户。"""


@dataclass
class Window:
    hwnd: int
    title: str
    class_name: str
    visible: bool
    width: int  # 还原状态下的大小（最小化时也能拿到）
    height: int


@dataclass
class Rect:
    left: int
    top: int
    width: int
    height: int

    def point(self, fx: float, fy: float) -> tuple[int, int]:
        return int(self.left + fx * self.width), int(self.top + fy * self.height)

    def box(self, l: float, t: float, r: float, b: float) -> tuple[int, int, int, int]:
        x0, y0 = self.point(l, t)
        x1, y1 = self.point(r, b)
        return x0, y0, x1, y1


@dataclass
class OcrLine:
    text: str
    x0: int  # 屏幕坐标
    y0: int
    x1: int
    y1: int

    @property
    def center(self) -> tuple[int, int]:
        return (self.x0 + self.x1) // 2, (self.y0 + self.y1) // 2


def set_dpi_aware() -> None:
    """让截屏、窗口位置、鼠标坐标都用实际像素，避免缩放 125%/150% 时错位。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


# ---------------- 窗口 ----------------

DEFAULT_WINDOW_SIZE = (1280, 860)  # UIConfig 里没改过时的窗口大小
# 校准时自动选的窗口大小（缩放 100% 时的像素）。窗口窄了，搜索结果里长群名会显示不全（真机上
# 缩放 200%、宽 1280 像素时 15 个字的群名就被截断），所以尽量宽。
AUTO_WIDTH, AUTO_HEIGHT = 1600, 900


def fit_window_size(scale: float, work_width: int, work_height: int, x: int, y: int) -> tuple[int, int]:
    """按屏幕缩放比例算企业微信窗口的大小（实际像素），不超出屏幕可用区域。

    work_width/work_height 是窗口左上角 (x, y) 所在屏幕可用区域的宽高，x、y 是窗口左上角相对可用区域的位置。
    """
    margin_x, margin_y = max(x, 0), max(y, 0)
    width = min(round(AUTO_WIDTH * scale), work_width - 2 * margin_x)
    height = min(round(AUTO_HEIGHT * scale), work_height - 2 * margin_y)
    return max(width, DEFAULT_WINDOW_SIZE[0]), max(height, DEFAULT_WINDOW_SIZE[1])


def auto_window_size(cfg: UIConfig) -> tuple[int, int] | None:
    """读出窗口所在屏幕的缩放比例和可用区域，算出合适的窗口大小。读不到时返回 None（保持原来的大小）。"""
    try:
        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]

        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.POINTER(MONITORINFO)]
        monitor = user32.MonitorFromPoint(wintypes.POINT(cfg.window_x, cfg.window_y), 1)  # 找不到时用主屏
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        shcore = ctypes.WinDLL("shcore")
        shcore.GetDpiForMonitor.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(wintypes.UINT),
                                            ctypes.POINTER(wintypes.UINT)]
        dpi_x, dpi_y = wintypes.UINT(), wintypes.UINT()
        if shcore.GetDpiForMonitor(monitor, 0, ctypes.byref(dpi_x), ctypes.byref(dpi_y)) != 0 or not dpi_x.value:
            return None
        work = info.rcWork
        return fit_window_size(dpi_x.value / 96, work.right - work.left, work.bottom - work.top,
                               cfg.window_x - work.left, cfg.window_y - work.top)
    except Exception:  # noqa: BLE001  读不到就不调整，不影响校准
        log.exception("读取屏幕缩放比例失败，窗口大小保持不变")
        return None



def _text(func, hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    func(hwnd, buf, 512)
    return buf.value


def list_windows(process_name: str) -> tuple[bool, list[Window]]:
    """(进程是否在运行, 该进程的顶层窗口)。"""
    import psutil

    pids = set()
    for p in psutil.process_iter(["name"]):
        if (p.info.get("name") or "").lower() == process_name.lower():
            pids.add(p.pid)
    windows: list[Window] = []

    def callback(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            wp = WINDOWPLACEMENT()
            wp.length = ctypes.sizeof(WINDOWPLACEMENT)
            user32.GetWindowPlacement(hwnd, ctypes.byref(wp))
            r = wp.rcNormalPosition
            windows.append(Window(hwnd, _text(user32.GetWindowTextW, hwnd), _text(user32.GetClassNameW, hwnd),
                                  bool(user32.IsWindowVisible(hwnd)), r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return bool(pids), windows


def desktop_locked() -> bool:
    """屏幕锁定或切到了登录界面时，任何界面操作都不会生效。"""
    hdesk = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not hdesk:
        return True
    try:
        buf = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        ok = user32.GetUserObjectInformationW(hdesk, 2, buf, ctypes.sizeof(buf), ctypes.byref(needed))  # UOI_NAME
        return bool(ok) and buf.value.lower() != "default"
    finally:
        user32.CloseDesktop(hdesk)


def find_main_window(cfg: UIConfig) -> tuple[Window | None, str]:
    running, windows = list_windows(cfg.process_name)
    if not running:
        return None, "企业微信没有运行"
    big = [w for w in windows if w.width >= cfg.min_main_width and w.height >= cfg.min_main_height]
    visible = [w for w in big if w.visible]
    if visible:
        # 优先标题就是「企业微信」的主窗口（单独弹出的聊天窗口标题是群名）
        visible.sort(key=lambda w: (w.title != MAIN_TITLE, -(w.width * w.height)))
        return visible[0], "企业微信已就绪"
    hidden_main = [w for w in big if w.title == MAIN_TITLE]
    if hidden_main:
        return hidden_main[0], "企业微信已就绪（窗口在托盘里，发送时会自动打开）"
    if any(w.visible and w.width > 0 for w in windows):
        return None, "企业微信未登录（只看到了登录窗口），请在这台电脑上登录客服账号"
    return None, "企业微信窗口没有打开（可能缩到了托盘），请双击托盘图标打开"


def _foreground(hwnd: int) -> bool:
    # 先按一下 Alt，Windows 才允许后台程序把别的窗口切到前面
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    time.sleep(0.3)
    fg = user32.GetForegroundWindow()
    return fg == hwnd or user32.GetAncestor(fg, GA_ROOTOWNER) == hwnd


def prepare_window(win: Window, cfg: UIConfig) -> Rect:
    """把企业微信主窗口还原、摆到固定位置和大小、切到最前面。"""
    hwnd = win.hwnd
    if not win.visible:
        user32.ShowWindow(hwnd, SW_SHOW)
    if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.SetWindowPos(hwnd, 0, cfg.window_x, cfg.window_y, cfg.window_width, cfg.window_height,
                        SWP_NOZORDER | SWP_NOACTIVATE)
    time.sleep(0.3)
    if not _foreground(hwnd):
        raise UIError("没能把企业微信窗口切到最前面（屏幕可能锁定，或被别的窗口挡住），未发送")
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)


# ---------------- 截屏识字 ----------------


def screenshot(box: tuple[int, int, int, int] | None = None):
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=box, all_screens=True)


class Ocr:
    """离线识字（RapidOCR，不联网）。第一次使用时加载模型，约 1~2 秒。"""

    def __init__(self):
        self._engine = None

    def read(self, box: tuple[int, int, int, int]) -> list[OcrLine]:
        import numpy as np

        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
        img = screenshot(box)
        result, _ = self._engine(np.array(img.convert("RGB")))
        lines = []
        for points, text, _score in result or []:
            xs, ys = [p[0] for p in points], [p[1] for p in points]
            lines.append(OcrLine(text, box[0] + int(min(xs)), box[1] + int(min(ys)),
                                 box[0] + int(max(xs)), box[1] + int(max(ys))))
        return lines


def save_screen(tag: str) -> str | None:
    """失败时留一张截图在 logs/screens，方便排查。只保存在这台电脑上，保留最近 50 张。"""
    try:
        folder = LOG_DIR / "screens"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{datetime.now():%Y%m%d-%H%M%S}-{tag}.png"
        screenshot().save(path)
        for old in sorted(folder.glob("*.png"))[:-50]:
            old.unlink(missing_ok=True)
        return str(path)
    except Exception:  # noqa: BLE001
        log.exception("保存截图失败")
        return None


def _same_text(a: str, b: str) -> bool:
    return a.replace("\r\n", "\n").replace("\r", "\n").strip() == b.replace("\r\n", "\n").replace("\r", "\n").strip()


# ---------------- 发送 ----------------


def truncated_name(texts: list[str], name: str) -> str | None:
    """搜索结果里有没有一行是群名的开头一截（窗口太窄时企业微信用省略号截断长群名）。

    只用来把失败原因说清楚：截断的结果不能确认是哪个群，不会去点。
    """
    for text in texts:
        head = text.strip().rstrip(".…·。 ")
        if head != name and len(head) >= max(4, len(name) // 2) and name.startswith(head):
            return text.strip()
    return None


class WeComDriver:
    def __init__(self, cfg: UIConfig, clipboard=None):
        set_dpi_aware()
        import pyautogui

        from wecom_agent.clipboard import Clipboard

        pyautogui.FAILSAFE = True  # 紧急停止：把鼠标甩到屏幕任意一个角
        pyautogui.PAUSE = 0.08
        self.gui = pyautogui
        self.cfg = cfg
        self.ocr = Ocr()
        self.clip = clipboard or Clipboard()
        self._result_texts: list[str] = []

    # ---- 状态 ----

    def status(self) -> tuple[bool, str]:
        if not self.cfg.calibrated:
            return False, "发送程序还没有校准，请在发送电脑上运行 2-calibrate.bat"
        if desktop_locked():
            return False, "发送电脑的屏幕已锁定，无法操作企业微信（请设置为不锁屏）"
        win, detail = find_main_window(self.cfg)
        return win is not None, detail

    # ---- 基本操作 ----

    def _keys(self, combo: str) -> None:
        keys = [k.strip().lower() for k in combo.split("+") if k.strip()]
        if len(keys) == 1:
            self.gui.press(keys[0])
        else:
            self.gui.hotkey(*keys)

    def _paste(self, text: str) -> None:
        self.clip.set_text(text)
        time.sleep(0.1)
        self._keys("ctrl+v")

    def _read_input(self, x: int | None = None, y: int | None = None) -> str | None:
        """全选复制输入框里的内容。返回文字；空输入框返回 ""；里面有图片、文件等时返回 None。

        x, y 给出时先点一下输入框；输入框里已有图片或文件时不要点，以免点开预览。
        """
        self.clip.set_text(CLIP_SENTINEL)
        if x is not None:
            self.gui.click(x, y)
        self._keys("ctrl+a")
        self._keys("ctrl+c")
        time.sleep(0.25)
        text = self.clip.get_text()
        return "" if text == CLIP_SENTINEL else text

    def _clear_input(self, x: int, y: int) -> None:
        self.gui.click(x, y)
        self._keys("ctrl+a")
        self._keys("delete")

    # ---- 打开群并核对 ----

    def window(self) -> Rect:
        if desktop_locked():
            raise UIError("发送电脑的屏幕已锁定，无法操作企业微信，未发送")
        win, detail = find_main_window(self.cfg)
        if win is None:
            raise UIError(f"{detail}，未发送")
        return prepare_window(win, self.cfg)

    def header_lines(self, rect: Rect) -> list[OcrLine]:
        c = self.cfg
        return self.ocr.read(rect.box(c.header_left, c.header_top, c.header_right, c.header_bottom))

    def verify_header(self, rect: Rect, name: str) -> None:
        texts = [line.text for line in self.header_lines(rect)]
        if not find_title(texts, name, self.cfg.require_member_count, exact_name=True):
            seen = "、".join(texts) or "没有识别到文字"
            raise UIError(f"当前打开的聊天不是「{name}」（标题识别为：{seen}），未发送")

    def search_results(self, rect: Rect, name: str) -> list[OcrLine]:
        c = self.cfg
        lines = self.ocr.read(rect.box(c.results_left, c.results_top, c.results_right, c.results_bottom))
        self._result_texts = [line.text for line in lines]
        _, sy = rect.point(c.search_x, c.search_y)
        hx0, hy0, hx1, hy1 = rect.box(c.header_left, c.header_top, c.header_right, c.header_bottom)

        def is_result(line: OcrLine) -> bool:
            cx, cy = line.center
            return (bool(match_title(line.text, name, exact_name=True))
                    and not (line.y0 - 4 <= sy <= line.y1 + 4)  # 搜索框里就是刚输入的群名
                    and not (hx0 <= cx <= hx1 and hy0 <= cy <= hy1))  # 当前打开的聊天标题

        return [line for line in lines if is_result(line)]

    def open_group(self, name: str) -> Rect:
        rect = self.window()
        self.gui.click(*rect.point(self.cfg.search_x, self.cfg.search_y))
        time.sleep(0.3)
        self._keys("ctrl+a")
        self._paste(name)
        time.sleep(self.cfg.search_wait)
        hits = self.search_results(rect, name)
        if not hits:
            self._keys("esc")
            if cut := truncated_name(self._result_texts, name):
                raise UIError(f"搜索结果里群名显示不全（只看到「{cut}」），无法确认是不是「{name}」，未发送。"
                              "请在发送电脑的 config.ini 里把 window_width 调大后重新校准")
            raise UIError(f"在企业微信里搜不到群「{name}」（客服账号可能不在这个群里，或群名改了），未发送")
        if len(hits) > 1:
            self._keys("esc")
            raise UIError(f"搜索「{name}」有 {len(hits)} 个同名结果，无法确定是哪个群，未发送")
        self.gui.click(*hits[0].center)
        time.sleep(self.cfg.open_wait)
        self.verify_header(rect, name)
        return rect

    # ---- 发送 ----

    def send(self, group_name: str, content: str, files: list[Path] | None = None) -> SendResult:
        """先逐个发图片、文件（各算一条消息），再发文字。"""
        done: list[str] = []
        try:
            self._send(group_name, content, files or [], done)
            return SendResult(True)
        except UIError as exc:
            shot = save_screen("failed")
            log.warning("未发送：%s（截图 %s）", exc, shot)
            prefix = f"已发出：{'、'.join(done)}；后面的没有发送。" if done else ""
            return SendResult(False, prefix + str(exc))
        except self.gui.FailSafeException:
            return SendResult(False, "发送被人工中止（鼠标移到了屏幕角落），请到群里确认是否已发出")

    def _press_send(self, rect: Rect, name: str, ix: int, iy: int) -> None:
        """按发送键前再核对一次群名（防止期间有弹窗或切换），对不上就清空输入框。"""
        try:
            self.verify_header(rect, name)
        except UIError:
            self._clear_input(ix, iy)
            raise
        self._keys(self.cfg.send_key)

    def _send(self, name: str, content: str, files: list[Path], done: list[str]) -> None:
        from wecom_agent.clipboard import is_image

        rect = self.open_group(name)
        ix, iy = rect.point(self.cfg.input_x, self.cfg.input_y)
        self._clear_input(ix, iy)  # 清掉输入框里可能残留的草稿

        for path in files:
            label = f"{'图片' if is_image(path) else '文件'}「{path.name}」"
            if is_image(path):
                self.clip.set_image(path)
            else:
                self.clip.set_files([path])
            self.gui.click(ix, iy)
            self._keys("ctrl+v")
            time.sleep(0.8)
            # 核对：输入框里有东西了
            if self._read_input() == "":
                raise UIError(f"没能把{label}放进企业微信的输入框，未发送")
            self._press_send(rect, name, ix, iy)
            time.sleep(1.5)
            # 核对：输入框空了，说明发出去了
            if self._read_input() != "":
                self._clear_input(ix, iy)
                raise UIError(f"按了发送键，但{label}还留在输入框里，没有发出，未发送")
            done.append(label)

        if not content:
            return
        self._paste(content)
        time.sleep(0.4)
        # 核对 1：回复确实进了输入框（而不是别处）
        if not _same_text(self._read_input(ix, iy) or "", content):
            self._clear_input(ix, iy)
            raise UIError("没能把回复填进企业微信的输入框，未发送")
        self._keys("ctrl+end")  # 取消全选
        # 核对 2：按发送键前再确认一次还是这个群
        self._press_send(rect, name, ix, iy)
        time.sleep(1.0)
        # 核对 3：输入框清空了，说明消息发出去了
        if self._read_input(ix, iy) != "":
            self._clear_input(ix, iy)
            raise UIError("按了发送键，但输入框没有清空。请到群里确认消息有没有发出，再检查企业微信的发送快捷键设置")


__all__ = ["Ocr", "OcrLine", "Rect", "UIError", "WeComDriver", "find_main_window", "normalize", "save_screen"]
