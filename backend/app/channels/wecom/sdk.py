"""企业微信「会话内容存档」Finance SDK 的 ctypes 封装。

官方只提供 Linux / Windows 的 C 库（libWeWorkFinanceSdk_C.so），需自行从企业微信管理后台下载，
放到 settings.wecom_sdk_lib_path（默认 /opt/wecom/libWeWorkFinanceSdk_C.so），本仓库不包含该文件。
"""

import ctypes
import json
import threading
from typing import Protocol


class ArchiveError(Exception):
    pass


class ArchiveClient(Protocol):
    """会话存档拉取接口，便于测试时替换为假实现。"""

    def get_chat_data(self, seq: int, limit: int) -> list[dict]:
        """返回 chatdata 列表：[{seq, msgid, publickey_ver, encrypt_random_key, encrypt_chat_msg}]"""

    def decrypt(self, random_key: str, encrypt_chat_msg: str) -> str:
        """用 RSA 解出的随机密钥解密消息，返回明文 JSON 字符串。"""

    def get_media(self, sdkfileid: str, max_bytes: int) -> bytes:
        """下载图片、文件等媒体内容。超过 max_bytes 抛 ArchiveError。"""


class FinanceSdk:
    def __init__(self, lib_path: str, corp_id: str, secret: str, proxy: str = "", proxy_password: str = "",
                 timeout: int = 10):
        try:
            self._lib = ctypes.cdll.LoadLibrary(lib_path)
        except OSError as exc:
            raise ArchiveError(f"无法加载会话存档 SDK：{lib_path}（{exc}）") from exc
        lib = self._lib
        lib.NewSdk.restype = ctypes.c_void_p
        lib.Init.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        lib.GetChatData.argtypes = [ctypes.c_void_p, ctypes.c_ulonglong, ctypes.c_uint, ctypes.c_char_p,
                                    ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
        lib.DecryptData.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p]
        lib.NewSlice.restype = ctypes.c_void_p
        lib.FreeSlice.argtypes = [ctypes.c_void_p]
        lib.GetContentFromSlice.argtypes = [ctypes.c_void_p]
        lib.GetContentFromSlice.restype = ctypes.c_void_p
        lib.GetSliceLen.argtypes = [ctypes.c_void_p]
        lib.DestroySdk.argtypes = [ctypes.c_void_p]
        lib.NewMediaData.restype = ctypes.c_void_p
        lib.FreeMediaData.argtypes = [ctypes.c_void_p]
        lib.GetMediaData.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p,
                                     ctypes.c_char_p, ctypes.c_int, ctypes.c_void_p]
        for name in ("GetOutIndexBuf", "GetData"):
            getattr(lib, name).argtypes = [ctypes.c_void_p]
            getattr(lib, name).restype = ctypes.c_void_p
        for name in ("GetIndexLen", "GetDataLen", "IsMediaDataFinish"):
            getattr(lib, name).argtypes = [ctypes.c_void_p]
            getattr(lib, name).restype = ctypes.c_int

        self._sdk = lib.NewSdk()
        ret = lib.Init(self._sdk, corp_id.encode(), secret.encode())
        if ret != 0:
            lib.DestroySdk(self._sdk)
            raise ArchiveError(f"会话存档 SDK 初始化失败，错误码 {ret}")
        self._proxy = proxy.encode()
        self._proxy_password = proxy_password.encode()
        self._timeout = timeout
        self._lock = threading.Lock()

    def _read_slice(self, slice_ptr) -> str:
        length = self._lib.GetSliceLen(slice_ptr)
        return ctypes.string_at(self._lib.GetContentFromSlice(slice_ptr), length).decode()

    def get_chat_data(self, seq: int, limit: int) -> list[dict]:
        with self._lock:
            slice_ptr = self._lib.NewSlice()
            try:
                ret = self._lib.GetChatData(self._sdk, seq, limit, self._proxy, self._proxy_password,
                                            self._timeout, slice_ptr)
                if ret != 0:
                    raise ArchiveError(f"GetChatData 失败，错误码 {ret}")
                body = json.loads(self._read_slice(slice_ptr))
            finally:
                self._lib.FreeSlice(slice_ptr)
        if body.get("errcode"):
            raise ArchiveError(f"GetChatData 返回错误：{body.get('errcode')} {body.get('errmsg')}")
        return body.get("chatdata", [])

    def decrypt(self, random_key: str, encrypt_chat_msg: str) -> str:
        slice_ptr = self._lib.NewSlice()
        try:
            ret = self._lib.DecryptData(random_key.encode(), encrypt_chat_msg.encode(), slice_ptr)
            if ret != 0:
                raise ArchiveError(f"DecryptData 失败，错误码 {ret}")
            return self._read_slice(slice_ptr)
        finally:
            self._lib.FreeSlice(slice_ptr)

    def get_media(self, sdkfileid: str, max_bytes: int) -> bytes:
        """分片拉取媒体文件：每次带上一片返回的 indexbuf，直到 IsMediaDataFinish。"""
        lib, chunks, size, index = self._lib, [], 0, b""
        while True:
            with self._lock:
                media = lib.NewMediaData()
                try:
                    ret = lib.GetMediaData(self._sdk, index, sdkfileid.encode(), self._proxy, self._proxy_password,
                                           self._timeout, media)
                    if ret != 0:
                        raise ArchiveError(f"GetMediaData 失败，错误码 {ret}")
                    chunks.append(ctypes.string_at(lib.GetData(media), lib.GetDataLen(media)))
                    index = ctypes.string_at(lib.GetOutIndexBuf(media), lib.GetIndexLen(media))
                    finished = bool(lib.IsMediaDataFinish(media))
                finally:
                    lib.FreeMediaData(media)
            size += len(chunks[-1])
            if size > max_bytes:
                raise ArchiveError("文件太大，未下载")
            if finished:
                return b"".join(chunks)

    def close(self) -> None:
        if self._sdk:
            self._lib.DestroySdk(self._sdk)
            self._sdk = None
