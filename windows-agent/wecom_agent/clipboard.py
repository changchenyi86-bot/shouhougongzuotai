"""Windows 剪贴板：放文字、图片、文件，读回文字。

发送时用粘贴把内容放进企业微信输入框：中文和换行比逐字输入可靠，图片和文件也只能这样放进去。
"""

import io
import struct
import time
from pathlib import Path

SENTINEL = "__wecom_agent_empty__"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}


class ClipboardError(Exception):
    pass


class Clipboard:
    def __init__(self):
        import win32clipboard
        import win32con

        self._cb, self._con = win32clipboard, win32con

    def _open(self) -> None:
        # 别的程序可能正占用剪贴板，稍等重试
        for _ in range(20):
            try:
                self._cb.OpenClipboard()
                return
            except Exception:  # noqa: BLE001
                time.sleep(0.05)
        raise ClipboardError("剪贴板被别的程序占用")

    def _set(self, fmt: int, data) -> None:
        self._open()
        try:
            self._cb.EmptyClipboard()
            self._cb.SetClipboardData(fmt, data)
        finally:
            self._cb.CloseClipboard()

    def set_text(self, text: str) -> None:
        # 不能直接把 str 交给 pywin32：它会先试着把内容当成「句柄编号」，于是「1233321」「666」这类
        # 纯数字的文字被当成一个不存在的句柄，报「句柄无效」（pywin32 #2132，真机联调时遇到）。
        # 自己编码成带结尾符的 UTF-16 字节就不会被误认：里面一定有 0 字节，不可能被当成数字。
        self._set(self._con.CF_UNICODETEXT, text.encode("utf-16-le") + b"\x00\x00")

    def get_text(self) -> str | None:
        """剪贴板里的文字；里面是图片、文件等非文字内容时返回 None。"""
        self._open()
        try:
            if not self._cb.IsClipboardFormatAvailable(self._con.CF_UNICODETEXT):
                return None
            return self._cb.GetClipboardData(self._con.CF_UNICODETEXT)
        finally:
            self._cb.CloseClipboard()

    def set_image(self, path: Path) -> None:
        """图片按位图放进剪贴板，粘贴到企业微信里是图片消息（不是文件）。"""
        from PIL import Image

        with Image.open(path) as img:
            buf = io.BytesIO()
            img.convert("RGB").save(buf, "BMP")
        self._set(self._con.CF_DIB, buf.getvalue()[14:])  # 去掉 BMP 文件头就是 DIB

    def set_files(self, paths: list[Path]) -> None:
        """和在资源管理器里「复制文件」一样（CF_HDROP），粘贴到企业微信里是文件消息。"""
        names = "".join(str(p.resolve()) + "\0" for p in paths) + "\0"
        dropfiles = struct.pack("<IiiII", 20, 0, 0, 0, 1)  # pFiles=20, pt=(0,0), fNC=0, fWide=1
        self._set(self._con.CF_HDROP, dropfiles + names.encode("utf-16-le"))


def is_image(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES
