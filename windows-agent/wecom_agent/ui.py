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
PASTE_SETTLE = 0.05  # 往剪贴板放好内容后稍等再粘贴，让系统里监听剪贴板的程序先读完
RESULTS_STABLE = 0.5  # 搜索结果识字后，至少要这么久没有再变化才去点


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
    pid: int = 0


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
                                  bool(user32.IsWindowVisible(hwnd)), r.right - r.left, r.bottom - r.top,
                                  pid.value))
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


def window_alive(win: Window) -> bool:
    """上次找到的企业微信主窗口还在不在（同一个窗口、同一个进程、标题没变、看得见）。

    还在就不用每次发送都把所有进程和窗口重新找一遍。
    """
    hwnd = win.hwnd
    if not win.pid or not user32.IsWindow(hwnd) or not user32.IsWindowVisible(hwnd):
        return False
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value == win.pid and _text(user32.GetWindowTextW, hwnd) == win.title


def _is_foreground(hwnd: int) -> bool:
    fg = user32.GetForegroundWindow()
    return fg == hwnd or user32.GetAncestor(fg, GA_ROOTOWNER) == hwnd


def _foreground(hwnd: int) -> bool:
    if _is_foreground(hwnd):
        return True
    # 先按一下 Alt，Windows 才允许后台程序把别的窗口切到前面
    user32.keybd_event(0x12, 0, 0, 0)
    user32.keybd_event(0x12, 0, 2, 0)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    deadline = time.monotonic() + 0.6
    while True:
        time.sleep(0.05)
        if _is_foreground(hwnd):
            time.sleep(0.15)  # 刚切到前面，等它画完
            return True
        if time.monotonic() >= deadline:
            return False


def _window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


_placed: dict[int, tuple[int, int, int, int]] = {}  # 窗口上次摆好后实际停在哪（企业微信不一定接受指定的大小）


def prepare_window(win: Window, cfg: UIConfig) -> Rect:
    """把企业微信主窗口还原、摆到固定位置和大小、切到最前面。已经摆好、已经在最前面时什么都不用等。"""
    hwnd = win.hwnd
    target = (cfg.window_x, cfg.window_y, cfg.window_width, cfg.window_height)
    shown = False
    if not user32.IsWindowVisible(hwnd):
        user32.ShowWindow(hwnd, SW_SHOW)
        shown = True
    if user32.IsIconic(hwnd) or user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        shown = True
    current = _window_rect(hwnd)
    if shown or (current != target and current != _placed.get(hwnd)):
        user32.SetWindowPos(hwnd, 0, *target, SWP_NOZORDER | SWP_NOACTIVATE)
        time.sleep(0.3)
        _placed[hwnd] = _window_rect(hwnd)
    if not _foreground(hwnd):
        raise UIError("没能把企业微信窗口切到最前面（屏幕可能锁定，或被别的窗口挡住），未发送")
    return Rect(*_window_rect(hwnd))


# ---------------- 截屏识字 ----------------


def screenshot(box: tuple[int, int, int, int] | None = None):
    from PIL import ImageGrab

    return ImageGrab.grab(bbox=box, all_screens=True)


def same_pixels(a, b) -> bool:
    """两张截图是不是一模一样（每个像素都相同）。"""
    return a.size == b.size and a.tobytes() == b.tobytes()


def differs(a, b) -> bool:
    """两张截图有没有看得出的差别。输入光标一闪一闪那样的小变化不算（不到万分之三的像素）。"""
    if a.size != b.size:
        return True
    from PIL import ImageChops

    diff = ImageChops.difference(a.convert("L"), b.convert("L")).point(lambda v: 255 if v > 32 else 0)
    return diff.histogram()[255] > a.size[0] * a.size[1] * 0.0003


class Ocr:
    """离线识字（RapidOCR，不联网）。模型在发送程序启动时加载好（约 1~2 秒），不占第一条消息的时间。"""

    def __init__(self):
        self._engine = None
        self._kwargs: dict = {}
        self.spent = 0.0  # 累计识字用时（秒），用于分步计时
        self.fallbacks = 0  # 用了几次“放大后再认一遍”的慢办法

    def _load(self):
        if self._engine is None:
            import inspect

            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
            # 屏幕上的字都是正的，不需要“判断文字是否倒置”这一步
            if "use_cls" in inspect.signature(self._engine.__call__).parameters:
                self._kwargs = {"use_cls": False}
        return self._engine

    def warm(self) -> None:
        """提前加载模型并空跑一次，第一条消息就不用等。"""
        import numpy as np

        self._load()(np.full((96, 320, 3), 255, dtype=np.uint8), **self._kwargs)

    def read(self, box: tuple[int, int, int, int], image=None,
             like: tuple[int, int] | None = None) -> list[OcrLine]:
        """认出屏幕上一块区域里的文字。image 是这块区域已经截好的图，没给就现截。

        like 是另一块区域（搜索结果区）的宽高。识字组件找字之前，会把窄的图放大到短边 736 像素：
        聊天标题那样的窄条会被放大好几倍，一次要 1 秒左右，是整个发送过程里最慢的一步，而且没有必要——
        标题和搜索结果在同一个窗口里，字一样大。给了 like，就按搜索结果区会被放大的倍数来处理这张图。
        """
        import numpy as np

        engine = self._load()
        img = image if image is not None else screenshot(box)
        started = time.monotonic()
        det = getattr(engine, "text_det", None)
        default = getattr(det, "limit_side_len", None)
        tuned = False
        if (like and min(like) > 0 and isinstance(default, (int, float))
                and getattr(det, "limit_type", None) == "min"):
            ratio = max(1.0, default / min(like))
            want = int(round(min(img.size) * ratio))
            if 32 <= want < default:
                det.limit_side_len, tuned = want, True
        try:
            result, _ = engine(np.array(img.convert("RGB")), **self._kwargs)
        finally:
            if tuned:
                det.limit_side_len = default
        self.spent += time.monotonic() - started
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
        # 每次点击、按键后的停顿。等界面反应靠下面各步“等到就绪再继续”，不靠这个停顿
        pyautogui.PAUSE = 0.02
        self.gui = pyautogui
        self.cfg = cfg
        self.ocr = Ocr()
        self.clip = clipboard or Clipboard()
        self._reset()

    def _reset(self) -> None:
        self._result_texts: list[str] = []
        self._pressed = False
        self._win: Window | None = None  # 上次找到的企业微信主窗口
        self._header_seen: tuple[str, object] | None = None  # (群名, 已经识字核对过的标题截图)
        self._marks: list[tuple[str, float]] = []  # 分步计时
        self._notes: list[str] = []

    # ---- 状态 ----

    def status(self) -> tuple[bool, str]:
        if not self.cfg.calibrated:
            return False, "发送程序还没有校准，请在发送电脑上运行 2-calibrate.bat"
        if desktop_locked():
            return False, "发送电脑的屏幕已锁定，无法操作企业微信（请设置为不锁屏）"
        win, detail = find_main_window(self.cfg)
        return win is not None, detail

    # ---- 分步计时 ----

    def _mark(self, label: str) -> None:
        self._marks.append((label, time.monotonic()))

    def timing(self) -> str:
        """这次发送每一步用了多久，写进日志，方便看时间花在哪。"""
        if len(self._marks) < 2:
            return ""
        steps = [f"{label} {t - self._marks[i][1]:.2f}" for i, (label, t) in enumerate(self._marks[1:])]
        total = self._marks[-1][1] - self._marks[0][1]
        extra = f"；其中识字 {self.ocr.spent:.2f}" if getattr(self.ocr, "spent", 0) else ""
        notes = f"；{'、'.join(self._notes)}" if self._notes else ""
        return f"共 {total:.2f} 秒：{'｜'.join(steps)}{extra}{notes}"

    # ---- 基本操作 ----

    def _keys(self, combo: str) -> None:
        keys = [k.strip().lower() for k in combo.split("+") if k.strip()]
        if len(keys) == 1:
            self.gui.press(keys[0])
        else:
            self.gui.hotkey(*keys)

    def _grab(self, *boxes: tuple[int, int, int, int]) -> list:
        """同一时刻截下屏幕上的几块区域（只截一次屏）。"""
        left, top = min(b[0] for b in boxes), min(b[1] for b in boxes)
        full = screenshot((left, top, max(b[2] for b in boxes), max(b[3] for b in boxes)))
        return [full.crop((b[0] - left, b[1] - top, b[2] - left, b[3] - top)) for b in boxes]

    def _wait_changed(self, box, before, timeout: float) -> bool:
        """等屏幕上这块区域和 before 相比有了变化。最多等 timeout 秒。"""
        deadline = time.monotonic() + timeout
        while True:
            time.sleep(0.05)
            if differs(self._grab(box)[0], before):
                return True
            if time.monotonic() >= deadline:
                return False

    def _paste(self, text: str) -> None:
        self.clip.set_text(text)
        time.sleep(PASTE_SETTLE)
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
        # 输入框是空的时候剪贴板不会变，只能等满这段时间才能下结论；有内容时一复制出来就返回
        deadline = time.monotonic() + 0.25
        while True:
            time.sleep(0.03)
            if self.clip.get_text() != CLIP_SENTINEL:
                time.sleep(0.03)  # 等它把剪贴板写完整再读
                return self.clip.get_text()
            if time.monotonic() >= deadline:
                return ""

    def _clear_input(self, x: int, y: int) -> None:
        self.gui.click(x, y)
        self._keys("ctrl+a")
        self._keys("delete")

    def _copied_by_wecom(self) -> bool:
        """剪贴板里现在的内容是不是企业微信自己放进去的。"""
        try:
            pid = self.clip.owner_pid()
            if not pid:
                return False
            import psutil

            return psutil.Process(pid).name().lower() == self.cfg.process_name.lower()
        except Exception:  # noqa: BLE001  查不出来就当作不是，走慢一点的核对办法
            return False

    def _paste_and_check(self, expect: str | None, slow_wait: float,
                         x: int | None = None, y: int | None = None) -> bool:
        """把剪贴板里的内容粘贴进输入框，并核对确实进去了。expect 是要发的文字；图片、文件传 None。

        粘贴后紧接着按全选、复制。这三个按键企业微信是按顺序处理的，所以一看到企业微信往剪贴板里
        写了东西，就知道粘贴已经做完，不用固定等一段时间；它复制出来的就是输入框里现在的全部内容，
        和要发的文字一样才算通过（图片、文件：复制出了东西就说明输入框里有东西）。
        认不出是不是企业微信写的剪贴板时，退回原来的办法：等够时间，再用占位文字读一遍输入框。
        """
        seq = self.clip.sequence()
        started = time.monotonic()
        self._keys("ctrl+v")
        while self.clip.sequence() == seq and time.monotonic() - started < 1.0:
            self._keys("ctrl+a")
            self._keys("ctrl+c")
            # 大图片粘贴得慢：这一轮没复制出东西就再全选、复制一轮
            waited = time.monotonic()
            while self.clip.sequence() == seq and time.monotonic() - waited < 0.2:
                time.sleep(0.02)
        if self.clip.sequence() != seq and self._copied_by_wecom():
            if expect is None or _same_text(self.clip.get_text() or "", expect):
                return True
        if "输入框核对用了慢办法" not in self._notes:
            self._notes.append("输入框核对用了慢办法")
        left = slow_wait - (time.monotonic() - started)
        if left > 0:
            time.sleep(left)
        got = self._read_input(x, y)
        return got != "" if expect is None else _same_text(got or "", expect)

    def _wait_sent(self, x: int | None, y: int | None, pane, before) -> bool:
        """按发送键后核对输入框清空了（说明消息发出去了）。

        先等聊天区的画面有变化——企业微信对发送键有反应了（最多等 1 秒），再读输入框；
        还没清空就隔一会儿再读，总共最多约 3 秒。
        """
        self._wait_changed(pane, before, 1.0)
        time.sleep(0.1)
        deadline = time.monotonic() + 2.0
        while True:
            if self._read_input(x, y) == "":
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.2)

    # ---- 打开群并核对 ----

    def window(self) -> Rect:
        if desktop_locked():
            raise UIError("发送电脑的屏幕已锁定，无法操作企业微信，未发送")
        win = self._win if self._win is not None and window_alive(self._win) else None
        if win is None:
            win, detail = find_main_window(self.cfg)
            if win is None:
                raise UIError(f"{detail}，未发送")
            self._win = win
        return prepare_window(win, self.cfg)

    def _header_box(self, rect: Rect) -> tuple[int, int, int, int]:
        c = self.cfg
        return rect.box(c.header_left, c.header_top, c.header_right, c.header_bottom)

    def _results_box(self, rect: Rect) -> tuple[int, int, int, int]:
        c = self.cfg
        return rect.box(c.results_left, c.results_top, c.results_right, c.results_bottom)

    def _pane_box(self, rect: Rect) -> tuple[int, int, int, int]:
        """聊天区：标题下面的消息和输入框。"""
        return rect.box(self.cfg.header_left, self.cfg.header_bottom, 1, 1)

    def header_lines(self, rect: Rect, frame=None, tuned: bool = True) -> list[OcrLine]:
        box = self._header_box(rect)
        x0, y0, x1, y1 = self._results_box(rect)
        return self.ocr.read(box, image=frame, like=(x1 - x0, y1 - y0) if tuned else None)

    def _header_is(self, rect: Rect, name: str, frame, slow: bool = True) -> tuple[bool, list[str]]:
        """截好的标题图里是不是这个群。先用快办法识字；没对上再用原来的慢办法认一遍，两种都对不上才算不是。"""
        texts = [line.text for line in self.header_lines(rect, frame)]
        if find_title(texts, name, self.cfg.require_member_count, exact_name=True):
            return True, texts
        if slow:
            again = [line.text for line in self.header_lines(rect, frame, tuned=False)]
            if find_title(again, name, self.cfg.require_member_count, exact_name=True):
                self.ocr.fallbacks = getattr(self.ocr, "fallbacks", 0) + 1
                if "标题识字用了慢办法" not in self._notes:
                    self._notes.append("标题识字用了慢办法")
                return True, again
        return False, texts

    def verify_header(self, rect: Rect, name: str, frame=None) -> None:
        """核对当前打开的聊天就是这个群，不是就报错。"""
        if frame is None:
            frame = self._grab(self._header_box(rect))[0]
        seen = self._header_seen
        if seen is not None and seen[0] == name and same_pixels(frame, seen[1]):
            return  # 和这次发送里刚识字核对过的画面一个像素都不差，认出来的字不会变
        ok, texts = self._header_is(rect, name, frame)
        if not ok:
            seen_text = "、".join(texts) or "没有识别到文字"
            raise UIError(f"当前打开的聊天不是「{name}」（标题识别为：{seen_text}），未发送")
        self._header_seen = (name, frame)

    def _wait_header(self, rect: Rect, name: str) -> None:
        """点开搜索结果后，等聊天标题变成这个群。每一次都是截屏识字后严格核对，最多等 open_wait 秒。"""
        box = self._header_box(rect)
        deadline = time.monotonic() + self.cfg.open_wait
        last, slow_tried = None, False
        while True:
            time.sleep(0.08)
            frame = self._grab(box)[0]
            changed = last is None or not same_pixels(frame, last)
            if changed or not slow_tried:
                # 画面刚变：用快办法认。画面不再变了还没对上：再用慢办法认一遍
                ok, _ = self._header_is(rect, name, frame, slow=not changed)
                slow_tried = not changed
                if ok:
                    self._header_seen = (name, frame)
                    return
            last = frame
            if time.monotonic() >= deadline:
                break
        self.verify_header(rect, name)  # 最后再完整核对一次，对不上就报错

    def search_results(self, rect: Rect, name: str, frame=None) -> list[OcrLine]:
        c = self.cfg
        lines = self.ocr.read(self._results_box(rect), image=frame)
        self._result_texts = [line.text for line in lines]
        _, sy = rect.point(c.search_x, c.search_y)
        hx0, hy0, hx1, hy1 = self._header_box(rect)

        def is_result(line: OcrLine) -> bool:
            cx, cy = line.center
            return (bool(match_title(line.text, name, exact_name=True))
                    and not (line.y0 - 4 <= sy <= line.y1 + 4)  # 搜索框里就是刚输入的群名
                    and not (hx0 <= cx <= hx1 and hy0 <= cy <= hy1))  # 当前打开的聊天标题

        return [line for line in lines if is_result(line)]

    def _settled_results(self, rect: Rect, name: str, before) -> list[OcrLine]:
        """等搜索结果出齐，再识字找出和群名完全一样的结果。

        原来是输入群名后固定等 search_wait 秒再识字。现在：
        1. 等结果区变了样、并且连续两次截屏一样，就开始识字，不用等满；
        2. 识字完再截一次屏：列表在这期间又变了（比如又加载出一条）就按新的重认一遍；
        3. 还没搜到就接着等，直到列表有变化或者等满 search_wait 秒（和原来一样久）才算搜不到。
        所以去点之前，认的那一版列表至少有约半秒没有变化；等满 search_wait 秒后和原来的做法完全一样。
        """
        box = self._results_box(rect)
        deadline = time.monotonic() + self.cfg.search_wait
        last = None
        while True:
            time.sleep(0.08)
            frame = self._grab(box)[0]
            if last is not None and not differs(frame, last) and differs(frame, before):
                break
            if time.monotonic() >= deadline:
                break
            last = frame
        self._mark("等搜索结果")
        while True:
            started = time.monotonic()
            hits = self.search_results(rect, name, frame)
            if time.monotonic() >= deadline:
                break
            left = RESULTS_STABLE - (time.monotonic() - started)
            if left > 0:
                time.sleep(left)
            latest = self._grab(box)[0]
            if differs(latest, frame):
                frame = latest
                continue
            if hits or not self._wait_changed(box, frame, deadline - time.monotonic()):
                break
            frame = self._grab(box)[0]
        self._mark("识别结果")
        return hits

    def open_group(self, name: str) -> Rect:
        self._header_seen = None
        rect = self.window()
        self._mark("窗口")
        before = self._grab(self._results_box(rect))[0]
        self.gui.click(*rect.point(self.cfg.search_x, self.cfg.search_y))
        time.sleep(0.15)
        self._keys("ctrl+a")
        self._paste(name)
        hits = self._settled_results(rect, name, before)
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
        self._wait_header(rect, name)
        time.sleep(0.1)  # 标题换过来了，稍等输入框也换好
        self._mark("打开群")
        return rect

    # ---- 发送 ----

    def send(self, group_name: str, content: str, files: list[Path] | None = None) -> SendResult:
        """先逐个发图片、文件（各算一条消息），再发文字。"""
        done: list[str] = []
        self._pressed = False  # 是否已按下发送键、还没核对结果
        self._marks, self._notes = [("开始", time.monotonic())], []
        self.ocr.spent = 0.0
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
        except Exception as exc:  # noqa: BLE001  程序自身出错（如剪贴板写入失败）
            shot = save_screen("error")
            log.exception("发送出错（截图 %s）", shot)
            prefix = f"已发出：{'、'.join(done)}；" if done else ""
            if self._pressed:
                return SendResult(False, f"{prefix}发送程序出错，未确认最后一条是否发出，请到群里确认：{exc}")
            # 还没按发送键就出错了，这一条肯定没有发出去
            return SendResult(False, f"{prefix}发送程序出错，{'后面的' if done else ''}没有发送：{exc}")
        finally:
            if timing := self.timing():
                log.info("用时%s", timing)

    def _press_send(self, rect: Rect, name: str, ix: int, iy: int):
        """按发送键前再核对一次群名（防止期间有弹窗或切换），对不上就清空输入框。

        返回按发送键前一刻聊天区的截图，用来判断企业微信对发送键有没有反应。
        """
        header, pane = self._grab(self._header_box(rect), self._pane_box(rect))
        try:
            self.verify_header(rect, name, header)
        except UIError:
            self._clear_input(ix, iy)
            raise
        self._pressed = True
        self._keys(self.cfg.send_key)
        return pane

    def _send(self, name: str, content: str, files: list[Path], done: list[str]) -> None:
        from wecom_agent.clipboard import is_image

        rect = self.open_group(name)
        ix, iy = rect.point(self.cfg.input_x, self.cfg.input_y)
        pane = self._pane_box(rect)
        self._clear_input(ix, iy)  # 清掉输入框里可能残留的草稿

        for path in files:
            kind = "图片" if is_image(path) else "文件"
            label = f"{kind}「{path.name}」"
            if is_image(path):
                self.clip.set_image(path)
            else:
                self.clip.set_files([path])
            self.gui.click(ix, iy)
            time.sleep(0.1)
            # 核对：输入框里有东西了
            if not self._paste_and_check(None, 0.8):
                raise UIError(f"没能把{label}放进企业微信的输入框，未发送")
            before = self._press_send(rect, name, ix, iy)
            # 核对：输入框空了，说明发出去了
            if not self._wait_sent(None, None, pane, before):
                self._clear_input(ix, iy)
                raise UIError(f"按了发送键，但{label}还留在输入框里，没有发出，未发送")
            done.append(label)
            self._pressed = False
            self._mark(f"发{kind}")

        if not content:
            return
        self.clip.set_text(content)
        time.sleep(PASTE_SETTLE)
        # 核对 1：回复确实进了输入框（而不是别处）
        if not self._paste_and_check(content, 0.4, ix, iy):
            self._clear_input(ix, iy)
            raise UIError("没能把回复填进企业微信的输入框，未发送")
        self._keys("ctrl+end")  # 取消全选
        self._mark("填写并核对")
        # 核对 2：按发送键前再确认一次还是这个群
        before = self._press_send(rect, name, ix, iy)
        self._mark("核对群名并按发送")
        # 核对 3：输入框清空了，说明消息发出去了
        if not self._wait_sent(ix, iy, pane, before):
            self._clear_input(ix, iy)
            raise UIError("按了发送键，但输入框没有清空。请到群里确认消息有没有发出，再检查企业微信的发送快捷键设置")
        self._mark("确认已发出")


__all__ = ["Ocr", "OcrLine", "Rect", "UIError", "WeComDriver", "find_main_window", "normalize", "save_screen"]
