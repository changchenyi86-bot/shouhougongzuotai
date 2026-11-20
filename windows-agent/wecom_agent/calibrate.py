"""校准：记下企业微信窗口里搜索框、输入框、聊天标题、搜索结果的位置。

不同版本、不同屏幕缩放下企业微信的界面布局不一样，所以不写死坐标，而是在发送电脑上
让人指一下位置，再用一个真实的群自动核对一遍。
"""

import json
import time

from wecom_agent import config as config_mod
from wecom_agent.config import DIAG_DIR, Config
from wecom_agent.matching import find_title, match_title
from wecom_agent.ui import (DEFAULT_WINDOW_SIZE, OcrLine, Rect, UIError, WeComDriver, auto_window_size,
                            list_windows, screenshot, truncated_name)


def _ask_position(driver: WeComDriver, what: str, seconds: int = 5) -> tuple[int, int]:
    input(f"\n准备好后按回车，然后在 {seconds} 秒内把鼠标移到【{what}】上并停住（不要点击）…")
    for i in range(seconds, 0, -1):
        print(f"  {i}…", flush=True)
        time.sleep(1)
    x, y = driver.gui.position()
    print(f"  已记录位置 ({x}, {y})")
    return x, y


def _frac(rect: Rect, x: int, y: int) -> tuple[float, float]:
    fx, fy = (x - rect.left) / rect.width, (y - rect.top) / rect.height
    if not (0 <= fx <= 1 and 0 <= fy <= 1):
        raise UIError("鼠标不在企业微信窗口里，请重新运行校准")
    return round(fx, 4), round(fy, 4)


def _dump_diagnostics(driver: WeComDriver, rect: Rect, lines: list[OcrLine] | None = None) -> None:
    """把窗口信息、截图、识字结果存到 diagnostics 文件夹，校准失败时发给开发人员看。"""
    DIAG_DIR.mkdir(exist_ok=True)
    screenshot((rect.left, rect.top, rect.left + rect.width, rect.top + rect.height)).save(DIAG_DIR / "window.png")
    _, windows = list_windows(driver.cfg.process_name)
    info = {"rect": rect.__dict__, "windows": [w.__dict__ for w in windows]}
    if lines is not None:
        info["ocr"] = [line.__dict__ for line in lines]
    (DIAG_DIR / "info.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        import uiautomation as auto

        root = auto.ControlFromHandle(windows[0].hwnd) if windows else None
        out, count = [], 0
        for control, depth in auto.WalkControl(root, includeTop=True, maxDepth=15) if root else []:
            out.append(f"{'  ' * depth}{control.ControlTypeName} name={control.Name!r} class={control.ClassName!r}")
            count += 1
            if count > 3000:
                break
        (DIAG_DIR / "uia.txt").write_text("\n".join(out), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001  诊断信息尽力而为
        (DIAG_DIR / "uia.txt").write_text(f"读取失败：{exc}", encoding="utf-8")


def run(cfg: Config) -> bool:
    ui = cfg.ui
    driver = WeComDriver(ui)
    print("=" * 60)
    print("企业微信发送程序 · 校准")
    print("校准过程中请不要移动或缩放企业微信窗口。如果这个黑色窗口挡住了企业微信，先把它拖到旁边。")
    print("=" * 60)

    # 窗口大小没有手动改过时，按这台电脑的屏幕缩放比例自动选（缩放 200% 的屏幕上默认大小太窄）
    if (ui.window_width, ui.window_height) == DEFAULT_WINDOW_SIZE and (size := auto_window_size(ui)):
        ui.window_width, ui.window_height = size
    rect = driver.window()
    print(f"\n第 1 步：已把企业微信窗口摆到固定位置（{rect.width}×{rect.height}）。")
    _dump_diagnostics(driver, rect)

    print("\n第 2 步：找到企业微信左上方的「搜索」框。")
    rect = driver.window()
    ui.search_x, ui.search_y = _frac(rect, *_ask_position(driver, "搜索框"))

    print("\n第 3 步：请在企业微信里手动点开一个群（建议用专门建的测试群），然后回到这个窗口。")
    input("点开群后按回车…")
    rect = driver.window()
    ui.input_x, ui.input_y = _frac(rect, *_ask_position(driver, "聊天输入框（打字的地方）"))

    name = input("\n第 4 步：请输入刚才点开的群的名字（和企业微信里显示的一字不差）：").strip()
    if any(ch.isspace() for ch in name):
        print("  注意：群名里有空格。截屏识字经常认不出空格，这个群可能核对不过；建议换一个名字里没有空格的群来校准。")
    rect = driver.window()
    full = (rect.left, rect.top, rect.left + rect.width, rect.top + rect.height)
    lines = driver.ocr.read(full)
    top_area = [ln for ln in lines if match_title(ln.text, name, exact_name=True)
                and ln.y1 < rect.top + rect.height * 0.3]
    if not top_area:
        _dump_diagnostics(driver, rect, lines)
        print(f"\n没有在窗口上方找到群名「{name}」。识别到的文字已存到 {DIAG_DIR}，请把这个文件夹发给开发人员。")
        return False
    title = max(top_area, key=lambda ln: ln.x0)  # 最右边的是聊天标题，左边的是会话列表
    ui.header_left = round(max(0, title.x0 - 30 - rect.left) / rect.width, 4)
    ui.header_top = round(max(0, title.y0 - 12 - rect.top) / rect.height, 4)
    ui.header_right = round(min(1, (title.x1 - rect.left) / rect.width + 0.25), 4)
    ui.header_bottom = round(min(1, (title.y1 + 12 - rect.top) / rect.height), 4)
    header_texts = [ln.text for ln in driver.header_lines(rect)]
    ui.require_member_count = find_title(header_texts, name, require_count=True, exact_name=True) is not None
    print(f"  已找到聊天标题：{'、'.join(header_texts)}"
          + ("（带人数，以后发送时会要求标题带人数，确保打开的是群聊）" if ui.require_member_count else ""))

    print("\n第 5 步：自动试一下搜索这个群…")
    ui.results_left, ui.results_right = 0.0, ui.header_left
    ui.results_top, ui.results_bottom = round(min(1, ui.search_y + 0.02), 4), 1.0
    rect = driver.window()
    driver.gui.click(*rect.point(ui.search_x, ui.search_y))
    time.sleep(0.3)
    driver._keys("ctrl+a")
    driver._paste(name)
    time.sleep(ui.search_wait)
    lines = driver.ocr.read(full)
    hx0, hy0, hx1, hy1 = rect.box(ui.header_left, ui.header_top, ui.header_right, ui.header_bottom)
    _, sy = rect.point(ui.search_x, ui.search_y)
    hits = [ln for ln in lines if match_title(ln.text, name, exact_name=True)
            and not (ln.y0 - 4 <= sy <= ln.y1 + 4)  # 搜索框本身
            and not (hx0 <= ln.center[0] <= hx1 and hy0 <= ln.center[1] <= hy1)]  # 聊天标题
    driver._keys("esc")
    if not hits:
        _dump_diagnostics(driver, rect, lines)
        if cut := truncated_name([ln.text for ln in lines if ln.center[0] < hx0], name):
            print(f"  搜索结果里群名显示不全（只看到「{cut}」）。请打开 config.ini，把 [ui] 里的 window_width 调大"
                  f"（现在是 {ui.window_width}），然后重新运行校准。")
        else:
            print(f"  搜索结果里没有识别到群名，识别到的文字已存到 {DIAG_DIR}，请把这个文件夹发给开发人员。")
        return False
    right = (max(h.x1 for h in hits) - rect.left) / rect.width + 0.05
    ui.results_right = round(min(1, max(ui.header_left, right)), 4)
    print("  搜索结果位置已记录。")

    print("\n第 6 步：完整试一遍「搜索 → 点开 → 核对群名」（不会发送任何消息）…")
    try:
        driver.open_group(name)
    except UIError as exc:
        _dump_diagnostics(driver, rect)
        print(f"  没通过：{exc}\n  诊断信息已存到 {DIAG_DIR}，可以再校准一次，或把这个文件夹发给开发人员。")
        return False
    ui.calibrated = True
    config_mod.save(cfg)
    print(f"\n校准完成，已保存到 {config_mod.CONFIG_FILE}。下一步可以运行 3-test.bat 试发一条消息。")
    return True
