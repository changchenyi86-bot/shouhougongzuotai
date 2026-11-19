"""读写 config.ini。位置、口令等都在这里，不写进代码。"""

import configparser
from dataclasses import dataclass, field, fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "config.ini"
LOG_DIR = ROOT / "logs"
DIAG_DIR = ROOT / "diagnostics"


@dataclass
class ServerConfig:
    url: str = "http://localhost:8000"
    token: str = ""
    agent_id: str = "客服电脑1"


@dataclass
class AgentConfig:
    poll_seconds: float = 3  # 没有任务时多久问一次
    heartbeat_seconds: float = 15
    min_interval_seconds: float = 5  # 本机两次发送至少间隔（服务端也有限制）


@dataclass
class UIConfig:
    """企业微信窗口里各位置，用窗口宽高的比例表示（0~1），由校准程序写入。"""

    process_name: str = "WXWork.exe"
    # 每次发送前把企业微信窗口摆到固定位置和大小，保证校准的位置不变
    window_x: int = 40
    window_y: int = 40
    window_width: int = 1280
    window_height: int = 860
    min_main_width: int = 700  # 比这小的窗口视为登录窗口
    min_main_height: int = 450
    search_x: float = 0
    search_y: float = 0
    input_x: float = 0
    input_y: float = 0
    # 聊天标题区域
    header_left: float = 0
    header_top: float = 0
    header_right: float = 0
    header_bottom: float = 0
    # 搜索结果出现的区域
    results_left: float = 0
    results_top: float = 0
    results_right: float = 0
    results_bottom: float = 1
    require_member_count: bool = False
    send_key: str = "enter"  # 企业微信设置里如果改成了 Ctrl+Enter 发送，这里填 ctrl+enter
    search_wait: float = 1.5  # 输入群名后等搜索结果出来的秒数
    open_wait: float = 1.2  # 点开群后等聊天窗口切换的秒数
    calibrated: bool = False


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    ui: UIConfig = field(default_factory=UIConfig)


def _load_section(parser: configparser.ConfigParser, name: str, obj) -> None:
    if not parser.has_section(name):
        return
    for f in fields(obj):
        if not parser.has_option(name, f.name):
            continue
        raw = parser.get(name, f.name)
        if f.type in (bool, "bool"):
            value = raw.strip().lower() in ("1", "true", "yes", "on")
        elif f.type in (int, "int"):
            value = int(float(raw))
        elif f.type in (float, "float"):
            value = float(raw)
        else:
            value = raw.strip()
        setattr(obj, f.name, value)


def load(path: Path = CONFIG_FILE) -> Config:
    cfg = Config()
    parser = configparser.ConfigParser(interpolation=None)
    if path.exists():
        parser.read(path, encoding="utf-8-sig")  # 记事本保存的 UTF-8 可能带 BOM
    for name in ("server", "agent", "ui"):
        _load_section(parser, name, getattr(cfg, name))
    cfg.server.url = cfg.server.url.rstrip("/")
    return cfg


def save(cfg: Config, path: Path = CONFIG_FILE) -> None:
    parser = configparser.ConfigParser(interpolation=None)
    for name in ("server", "agent", "ui"):
        obj = getattr(cfg, name)
        parser[name] = {f.name: _fmt(getattr(obj, f.name)) for f in fields(obj)}
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# 电脑端发送程序配置。[ui] 部分由 2-calibrate.bat 自动填写，一般不用手动改。\n")
        parser.write(fh)


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".") or "0"
    return str(value)
