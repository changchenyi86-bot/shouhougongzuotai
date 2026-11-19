"""命令行入口：

    python -m wecom_agent setup       填写工作台地址和口令
    python -m wecom_agent calibrate   校准企业微信窗口里各位置
    python -m wecom_agent test        试着打开一个群（可选发测试消息、图片和文件）
    python -m wecom_agent run         开始工作：自动发送工作台确认过的回复
"""

import logging
import logging.handlers
import sys
import tempfile
from pathlib import Path

from wecom_agent import VERSION
from wecom_agent import config as config_mod
from wecom_agent.config import LOG_DIR
from wecom_agent.server import Server, ServerError, ServerRejected


def _setup_logging() -> None:
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.handlers.RotatingFileHandler(LOG_DIR / "agent.log", maxBytes=2_000_000, backupCount=5,
                                                        encoding="utf-8")
    file_handler.setFormatter(fmt)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(fmt)
    logger = logging.getLogger("wecom_agent")
    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console)


def _ask(prompt: str, default: str) -> str:
    value = input(f"{prompt}" + (f"（直接回车用：{default}）" if default else "") + "：").strip()
    return value or default


def cmd_setup() -> int:
    cfg = config_mod.load()
    print("请按提示填写，填错了可以重新运行 1-install.bat 或直接编辑 config.ini。\n")
    cfg.server.url = _ask("工作台后端地址，例如 http://192.168.1.20:8000", cfg.server.url).rstrip("/")
    cfg.server.token = _ask("发送程序口令（和工作台服务器上 SEND_AGENT_TOKEN 的值一样）", cfg.server.token)
    cfg.server.agent_id = _ask("给这台电脑起个名字，会显示在工作台上", cfg.server.agent_id)
    config_mod.save(cfg)
    print(f"\n已保存到 {config_mod.CONFIG_FILE}。正在测试能否连上工作台…")
    try:
        Server(cfg.server).heartbeat(False, "发送程序已安装，等待校准")
        print("连接成功。")
        return 0
    except ServerRejected as exc:
        print(f"工作台拒绝了连接：{exc}\n请检查口令是否和服务器上的 SEND_AGENT_TOKEN 一致。")
    except ServerError as exc:
        print(f"连不上工作台：{exc}\n请检查地址是否正确、这台电脑能否访问工作台。")
    return 1


def cmd_calibrate() -> int:
    from wecom_agent import calibrate

    return 0 if calibrate.run(config_mod.load()) else 1


def cmd_test() -> int:
    from wecom_agent.ui import UIError, WeComDriver

    cfg = config_mod.load()
    if not cfg.ui.calibrated:
        print("还没有校准，请先运行 2-calibrate.bat")
        return 1
    driver = WeComDriver(cfg.ui)
    ready, detail = driver.status()
    print(f"企业微信状态：{detail}")
    if not ready:
        return 1
    name = input("请输入要测试的群名（建议用测试群）：").strip()
    try:
        driver.open_group(name)
    except UIError as exc:
        print(f"打开群失败：{exc}")
        return 1
    print(f"已打开并核对群「{name}」，这一步没有发送任何消息。")
    if input("要真的发一条测试消息到这个群吗？输入 y 并回车确认，直接回车跳过：").strip().lower() != "y":
        return 0
    result = driver.send(name, "【测试】这是售后工作台自动发送的测试消息，请忽略。")
    print("发送成功，请到企业微信群里看一下。" if result.ok else f"发送失败：{result.error}")
    if not result.ok:
        return 1
    if input("再试发一张测试图片和一个测试文件吗？输入 y 并回车确认，直接回车跳过：").strip().lower() != "y":
        return 0
    with tempfile.TemporaryDirectory(prefix="wecom-agent-test-") as tmp:
        files = _test_files(Path(tmp))
        result = driver.send(name, "【测试】上面是一张测试图片和一个测试文件，请忽略。", files)
    print("发送成功，请到群里看图片、文件和文字是否都收到了。" if result.ok else f"发送失败：{result.error}")
    return 0 if result.ok else 1


def _test_files(folder: Path) -> list[Path]:
    from PIL import Image, ImageDraw

    img = folder / "测试图片.png"
    im = Image.new("RGB", (360, 200), "#eef3ff")
    ImageDraw.Draw(im).rectangle([12, 12, 348, 188], outline="#3370ff", width=6)
    im.save(img)
    doc = folder / "测试文件.txt"
    doc.write_text("这是售后工作台自动发送的测试文件，请忽略。\n", encoding="utf-8")
    return [img, doc]


def cmd_run() -> int:
    from wecom_agent.runner import Runner
    from wecom_agent.ui import WeComDriver

    cfg = config_mod.load()
    if not cfg.server.token:
        print("还没有填写工作台地址和口令，请先运行 1-install.bat")
        return 1
    log = logging.getLogger("wecom_agent")
    log.info("发送程序 %s 已启动（%s → %s）", VERSION, cfg.server.agent_id, cfg.server.url)
    print("关闭这个窗口即停止发送。紧急停止：把鼠标快速甩到屏幕任意一个角落。")
    print("运行期间请不要使用这台电脑的鼠标键盘，也不要锁屏。\n")
    driver = WeComDriver(cfg.ui)
    ready, detail = driver.status()
    log.info("企业微信状态：%s", detail)
    try:
        Runner(Server(cfg.server), driver, cfg.agent).run()
    except KeyboardInterrupt:
        log.info("已停止")
    return 0


def main() -> int:
    _setup_logging()
    command = sys.argv[1] if len(sys.argv) > 1 else "run"
    commands = {"setup": cmd_setup, "calibrate": cmd_calibrate, "test": cmd_test, "run": cmd_run}
    if command not in commands:
        print(__doc__)
        return 2
    try:
        return commands[command]()
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
