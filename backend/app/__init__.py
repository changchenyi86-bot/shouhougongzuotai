"""售后技术支持工作台后端。

企业微信会话存档 SDK（libWeWorkFinanceSdk_C.so）自带一套旧版 C++ 运行库。如果数据库组件
（SQLAlchemy 依赖的 greenlet）先把系统新版的 libstdc++ 载入进程，两套在同一进程里冲突，
一调用 SDK 拉消息就会内存访问错误、整个后端崩溃。所以在导入其他模块之前先载入 SDK。
"""


def _preload_wecom_sdk() -> None:
    import ctypes
    import logging
    from pathlib import Path

    from app.config import settings

    path = settings.wecom_sdk_lib_path
    if not (settings.wecom_archive_enabled and path and Path(path).is_file()):
        return
    try:
        ctypes.CDLL(path)
    except OSError as exc:  # 载入失败时不影响启动，真正用到时 FinanceSdk 会再报错
        logging.getLogger(__name__).warning("预先载入会话存档 SDK 失败：%s", exc)


_preload_wecom_sdk()
