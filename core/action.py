"""
操作模块 — 基于 pyautogui，支持点击、拖拽、等待、按键。

依赖：pyautogui, keyboard（可选，用于空格键紧急停止）
"""

import logging
import os
import sys
import time
import threading
import ctypes
from typing import Optional

import pyautogui

try:
    import keyboard as _keyboard
    _has_keyboard = True
except ImportError:
    _has_keyboard = False

logger = logging.getLogger(__name__)

# 安全设置：移动鼠标到屏幕角落可触发 pyautogui 紧急停止
pyautogui.FAILSAFE = True
# 操作间隔（秒）：设为0，避免点击后自动暂停0.1s导致蛋糕落点偏差
# （高vx蛋糕0.1s可移动80px+，是精度误差的最大来源）
# 需要等待的地方统一用显式 bot.sleep()
pyautogui.PAUSE = 0

# ── 空格紧急停止：hook 捕获按键 → 全局标志位 ──
_stop_flag = False
_stop_ack = False          # 任务线程已确认退出（防止 watchdog 误杀）
_task_active = False       # 是否有任务线程正在跑
_task_thread_ident = None  # 需要被中断的任务线程 id


def _on_space_press(e):
    """空格按下回调，设置全局停止标志。"""
    global _stop_flag
    _stop_flag = True
    logger.warning("检测到空格键，设置停止标志")


if _has_keyboard:
    _keyboard.on_press_key('space', _on_space_press, suppress=False)


def request_stop():
    """请求全局停止（GUI 停止按钮与空格键共用同一标志）。"""
    global _stop_flag
    _stop_flag = True


def reset_stop():
    """清除停止标志，新一轮任务开始前调用。"""
    global _stop_flag, _stop_ack
    _stop_flag = False
    _stop_ack = False


def register_task_thread():
    """标记当前线程为需要急停的任务线程（GUI worker / CLI 主线程）。"""
    global _task_thread_ident, _task_active, _stop_ack
    _task_thread_ident = threading.get_ident()
    _task_active = True
    _stop_ack = False


def ack_stop():
    """任务线程已真正退出，通知 watchdog 不要再强杀。"""
    global _stop_ack
    _stop_ack = True


def task_finished():
    """任务正常结束/异常退出后解除 watchdog 保护。"""
    global _task_active, _stop_ack
    _task_active = False
    _stop_ack = True


def _stop_window_keywords():
    raw = os.environ.get("GAMEBOT_STOP_WINDOW") or "MuMu"
    return [x.strip().lower() for x in raw.replace(";", "|").split("|") if x.strip()]


def _space_down():
    """不依赖 keyboard 包的空格检测（GetAsyncKeyState，0x20=VK_SPACE）。

    2026-11-14 加前台门控：全局空格检测会把用户在其他窗口（如聊天框）
    打的空格误判为急停，bot 被无谓杀死（实测三次）。仅当前设备 profile
    的主窗/渲染窗在前台时空格才生效；便携版通过 GAMEBOT_STOP_WINDOW 配置。"""
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if hwnd:
            buf = ctypes.create_unicode_buffer(64)
            user32.GetWindowTextW(hwnd, buf, 64)
            title = buf.value.lower()
            if not any(k in title for k in _stop_window_keywords()):
                return False
        return bool(user32.GetAsyncKeyState(0x20) & 0x8000)
    except Exception:
        return False


def _watchdog():
    """急停 watchdog：轮询空格 → 中断任务线程；3s 内未退出则强杀进程。"""
    global _stop_flag
    injected = False
    while True:
        try:
            time.sleep(0.02)
            if not _task_active:
                injected = False
                continue
            if not _stop_flag and _space_down():
                _stop_flag = True
                logger.warning("检测到空格键，设置停止标志")
            if _stop_flag and not injected:
                injected = True
                ident = _task_thread_ident
                if ident:
                    try:
                        ctypes.pythonapi.PyThreadState_SetAsyncExc(
                            ctypes.c_ulong(ident),
                            ctypes.py_object(StopRequested))
                        logger.warning(f"已向任务线程 {ident} 注入 StopRequested")
                    except Exception as e:
                        logger.error(f"注入停止异常失败: {e!r}")
                # 给任务 3 秒收尾；仍未 ack 就硬退出（OCR/C 调用卡死兜底）
                for _ in range(60):
                    if _stop_ack or not _task_active:
                        break
                    time.sleep(0.05)
                if _task_active and not _stop_ack:
                    logger.critical("急停 3s 未生效 — 强制终止进程")
                    try:
                        sys.stdout.flush()
                        sys.stderr.flush()
                    except Exception:
                        pass
                    os._exit(1)
        except Exception:
            # watchdog 自己绝不能被异常打死
            time.sleep(0.05)


class StopRequested(Exception):
    """用户按下空格键请求停止当前任务。"""


class Action:
    """模拟操作封装。"""

    # 危险区域黑名单（窗口相对坐标，2026-09-06 点击防护网）：
    # - 顶栏 y<100：货币加号/检查更新等误点会开商店或弹窗（run10/11 实测）
    # 命中即拒绝并 WARNING。确需点击顶栏元素时应通过模板匹配确认后由
    # 调用方显式绕过（allow_danger=True）。
    DANGER_TOP_Y = 100

    def __init__(self, base_offset: tuple = (0, 0),
                 window_size: tuple = (0, 0)):
        """
        参数
        ----
        base_offset : (offset_x, offset_y)
            全局偏移量。若截图来自窗口局部而非全屏，
            需传入窗口左上角坐标作为偏移，确保点击坐标正确。
        window_size : (width, height)
            窗口尺寸（窗口相对坐标的上界），用于越界点击拒绝。
        """
        self.offset_x, self.offset_y = base_offset
        self.window_w, self.window_h = window_size

    def set_window_size(self, width: int, height: int):
        """窗口尺寸变化时由 GameBot 同步更新。"""
        self.window_w, self.window_h = width, height

    # ── 坐标转换 ────────────────────────────────

    def _to_screen(self, x: int, y: int) -> tuple:
        """将窗口内相对坐标转换为屏幕绝对坐标。"""
        return (x + self.offset_x, y + self.offset_y)

    def _danger_check(self, x: int, y: int) -> bool:
        """危险区域检查（入参为窗口相对坐标）。返回 True 表示坐标被拒绝。"""
        if y < self.DANGER_TOP_Y:
            logger.warning(
                f"点击防护: 拒绝顶栏坐标 窗口相对({x}, {y}) "
                f"——顶栏误点会开商店/弹窗（run10/11 事故区）。"
                f"如确需点击顶栏元素,请先用模板匹配确认并在调用处说明。"
            )
            return True
        if self.window_w and (x < 0 or x > self.window_w
                              or y < 0 or (self.window_h and y > self.window_h)):
            logger.warning(
                f"点击防护: 拒绝越界坐标 窗口相对({x}, {y}) "
                f"(窗口 {self.window_w}x{self.window_h})"
            )
            return True
        return False

    # ── 鼠标操作 ────────────────────────────────

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1,
              allow_danger: bool = False):
        """
        点击指定坐标。

        参数
        ----
        x, y : int  窗口内相对坐标
        button : str  'left' / 'right' / 'middle'
        clicks : int  点击次数
        allow_danger : bool  显式允许点击防护区（顶栏/越界），默认拒绝
        """
        if not allow_danger and self._danger_check(x, y):
            return
        sx, sy = self._to_screen(x, y)
        pyautogui.click(sx, sy, button=button, clicks=clicks)
        logger.info(f"点击 ({sx}, {sy}) [{button}]")

    def double_click(self, x: int, y: int):
        """双击。"""
        self.click(x, y, clicks=2)

    def drag(
        self, x1: int, y1: int, x2: int, y2: int,
        duration: float = 0.5, button: str = "left",
    ):
        """从 (x1,y1) 拖拽到 (x2,y2)。"""
        s1 = self._to_screen(x1, y1)
        s2 = self._to_screen(x2, y2)
        pyautogui.moveTo(*s1)
        pyautogui.drag(s2[0] - s1[0], s2[1] - s1[1], duration=duration, button=button)
        logger.info(f"拖拽 {s1} → {s2}")

    def move_to(self, x: int, y: int, duration: float = 0.2):
        """移动鼠标到指定坐标。"""
        sx, sy = self._to_screen(x, y)
        pyautogui.moveTo(sx, sy, duration=duration)

    def scroll(self, x: int, y: int, clicks: int):
        """在指定窗口坐标处滚动鼠标滚轮；clicks 正数向上、负数向下。"""
        sx, sy = self._to_screen(x, y)
        pyautogui.moveTo(sx, sy)
        pyautogui.scroll(clicks)
        logger.info(f"滚动 ({sx}, {sy}) clicks={clicks}")

    # ── 键盘操作 ────────────────────────────────

    def press(self, key: str, presses: int = 1):
        """按下并释放某个键。常见键名: enter, esc, space, tab, f1-f12 等。"""
        pyautogui.press(key, presses=presses)
        logger.info(f"按键 '{key}' × {presses}")

    def hotkey(self, *keys: str):
        """组合键。例如 hotkey('ctrl', 'c')。"""
        pyautogui.hotkey(*keys)
        logger.info(f"组合键 {'+'.join(keys)}")

    def typewrite(self, text: str, interval: float = 0.05):
        """输入文字。"""
        pyautogui.typewrite(text, interval=interval)
        logger.info(f"输入文字: '{text}'")

    # ── 等待 ────────────────────────────────────

    @staticmethod
    def sleep(seconds: float):
        """等待指定秒数，期间每 0.1s 检测停止标志以支持紧急停止。"""
        logger.info(f"等待 {seconds:.1f} 秒...")
        interval = 0.1
        elapsed = 0.0
        while elapsed < seconds:
            chunk = min(interval, seconds - elapsed)
            time.sleep(chunk)
            elapsed += chunk
            Action.check_stop()

    @staticmethod
    def check_stop():
        """检查停止标志，已设置则抛出 StopRequested。"""
        global _stop_flag
        if _stop_flag:
            raise StopRequested("检测到停止请求，任务已停止")

    # ── 截图（调试用）────────────────────────────

    @staticmethod
    def screenshot(filepath: Optional[str] = None):
        """全屏截图（用于调试）。"""
        img = pyautogui.screenshot()
        if filepath:
            img.save(filepath)
            logger.info(f"调试截图已保存: {filepath}")
        return img


# ── 全局可中断 sleep：任何模块里的 time.sleep 都按 50ms 分片检查空格 ──
# 2026-11-13：r1999/其他脚本大量裸 time.sleep，原来的 Action.sleep 覆盖不到，
# 导致按空格不能即停。这里直接 monkeypatch time.sleep；只对已注册的任务线程
# 抛 StopRequested，GUI 主线程/其他线程不受影响。
_orig_sleep = time.sleep


def _interruptible_sleep(seconds):
    if threading.get_ident() != _task_thread_ident:
        return _orig_sleep(seconds)
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        remain = deadline - time.monotonic()
        if remain <= 0:
            return
        _orig_sleep(min(0.05, remain))
        Action.check_stop()


time.sleep = _interruptible_sleep

# 空格轮询 watchdog：keyboard hook 失效时用 GetAsyncKeyState 兜底；
# 任务线程 3s 内不退出则硬终止（OCR/C 调用卡死的最后手段）。
threading.Thread(target=_watchdog, daemon=True, name="stop-watchdog").start()
