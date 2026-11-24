"""
三角洲行动（烽火地带）挂机脚本 — 大退-等待-重开 循环。

单轮流程（经用户确认 2026-11-04）：
    A. WeGame 点【启动】
    B. 游戏冷启动初始化（自适应：出现才做，超时跳过）：
       B1.【烽火】前往游玩·烽火地带 → 点击
       B2.【开始游戏】Tab 开始游戏 → 检测到后按 Tab 键
    C. 等大厅【行前备战】出现；期间出现【继续】→ 按空格
    D. 点【行前备战】
    E. 点【长弓溪谷】（固定地图）
    F. 点【开始行动】
    G.【确认配装】出现 → 点（匹配成功后出现，等待上限较长）
    H.【仍要继续】出现 → 点（可选）
    I.【出发】出现 → 点
    J. 出发后【取消行动】出现 → 轮询其消失（=匹配进入对局）→ 约 3 秒 → Alt+F4 大退
       （30 秒窗口未关 → taskkill 兜底）
    K. 等 38~40 分钟随机
    → 回到 A（当前版本不做重连；重连入局.png 留待后续版本）

注意：
- 必须以管理员运行（UIPI：否则发给游戏的合成输入被系统静默丢弃）
- 游戏须无边框窗口化（独占全屏下 mss 截图会黑屏）
- 输入均为真人级事件+随机抖动，无任何注入/内存操作；
  自动化违反游戏用户协议，封号风险自担
"""

import ctypes
import logging
import os
import random
import subprocess
import time
from typing import List, Optional

from core.action import Action, StopRequested
from core.matcher import TemplateMatcher
from core.screenshot import Screenshot, raise_to_foreground
from core.game_bot import GameBot
from core.task import Task
from core.game_script import GameScript

import pyautogui
# 挂机禁用"鼠标移到屏幕角落即急停"机制：夜间无人值守时鼠标误碰左上角会中断整晚
# 运行（2026-11-04 首跑实测：用户鼠标停在角落，烽火点击被拦，任务异常重启）。
# 急停手段保留：空格键（core.action 全局 hook）与 GUI 停止按钮。
pyautogui.FAILSAFE = False

logger = logging.getLogger(__name__)

# ══════════════════ TBC 待实测校准常量 ══════════════════
WE_WINDOW = "WeGame"            # WeGame 主窗口标题（实测：UI 窗口标题就是 "WeGame"）
DF_WINDOW = "三角洲行动"         # 游戏窗口标题（任务管理器：DeltaForce→子进程"三角洲行动"，2026-11-04 用户提供）
DF_PROCESS = "三角洲行动.exe"    # 游戏客户端进程名（大退兜底 taskkill 用；父进程 DeltaForce.exe 是启动器不动它）

TPL_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "templates", "三角洲")

MATCH_THRESHOLD = 0.82               # 模板匹配阈值（游戏 UI 色块大，0.82 起步）
WAIT_MIN_RANGE = (38.0, 40.0)        # 大退后等待分钟数（随机区间）
DEPART_GONE_GRACE = 3.0              # 取消行动消失后到 Alt+F4 的缓冲秒数
LOBBY_TIMEOUT = 300                  # 点启动后等大厅（含游戏启动+初始化）超时
CONFIRM_LOADOUT_TIMEOUT = 12 * 60    # 开始行动后等确认配装出现（含匹配时间）
STILL_CONTINUE_TIMEOUT = 20          # 确认配装后等仍要继续（可选步骤，不出现少白等）
DEPART_TIMEOUT = 120                 # 等出发按钮出现
CANCEL_APPEAR_TIMEOUT = 90           # 点出发后等取消行动出现
CANCEL_GONE_TIMEOUT = 30 * 60        # 等取消行动消失（匹配进入对局），极限半小时
QUIT_TIMEOUT = 30                    # Alt+F4 后等游戏窗口消失的超时
INIT_TIMEOUT = 120                   # 等初始化两步（烽火/开始游戏）出现的超时
RESUME_PROMPT_TIMEOUT = 60           # 大退等待结束后、恢复操作前的确认弹窗等待秒数（无响应默认继续）
MOUSE_PARK_RATIO = (0.5, 0.15)       # 点击/按键后鼠标停靠点（屏幕比例，上半部中性区）：
                                     # 避免悬停高亮改变按钮外观导致模板匹配失败
                                     # （2026-11-05 实测：WeGame 启动后鼠标恰好悬停烽火按钮，
                                     #  烽火模板因悬停态无法匹配）

# ════════════════════════════════════════════════════════

ES_CONTINUOUS = 0x80000000
ES_DISPLAY_REQUIRED = 0x00000001


def _prevent_sleep(on: bool):
    """禁用/恢复 屏保与显示器关闭（防夜间锁屏导致输入失效）。"""
    import ctypes
    if on:
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_DISPLAY_REQUIRED)
        logger.info("已临时禁用屏保与显示器关闭")
    else:
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
        logger.info("已恢复电源策略")


def _window_exists(title_keyword: str) -> bool:
    """仅检测窗口是否存在（不前置、不改变 z 序）。"""
    return _find_hwnd(title_keyword) is not None


def _find_hwnd(title_keyword: str) -> Optional[int]:
    """按标题关键字找可见顶层窗口句柄（不前置、不改变 z 序）。"""
    user32 = ctypes.windll.user32
    found = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

    def proc(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if title_keyword.lower() in buf.value.lower():
                    found.append(hwnd)
                    return False
        return True

    user32.EnumWindows(WNDENUMPROC(proc), 0)
    return found[0] if found else None


def _restore_if_minimized(hwnd: Optional[int]) -> bool:
    """
    窗口最小化时恢复显示，返回是否执行了恢复。

    最小化窗口的 GetWindowRect 是屏幕外坐标(-32000)，若不同步恢复就直接截图，
    截图区域无效 → mss 报 'Windows graphics function failed: BitBlt'
    （2026-11-04 二轮实测：WeGame 启动游戏后自动最小化，二轮 A 步连挂 3 次）。
    """
    if not hwnd:
        return False
    user32 = ctypes.windll.user32
    if user32.IsWindow(hwnd) and user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        time.sleep(0.3)
        logger.info(f"窗口已从最小化恢复 (hwnd={hwnd})")
        return True
    return False


def _park_mouse():
    """鼠标停靠到屏幕上半部中性点（按主屏比例），避免悬停效果干扰模板识别。"""
    pyautogui.moveTo(int(pyautogui.size()[0] * MOUSE_PARK_RATIO[0]),
                     int(pyautogui.size()[1] * MOUSE_PARK_RATIO[1]))


class _Pane:
    """
    单窗口上下文：截图 + 模板匹配 + 点击。

    窗口惰性绑定：大退后游戏窗口销毁，_ensure 会在窗口重新出现时自动重新挂钩，
    因此跨循环复用安全。
    """

    def __init__(self, title: str, matcher: TemplateMatcher):
        self.title = title
        self.matcher = matcher
        self.sc: Optional[Screenshot] = None
        self.act: Optional[Action] = None

    def _ensure(self):
        """窗口存在则恢复最小化并同步位置；未绑定或已销毁则(重)绑定。"""
        if self.sc is not None and _window_exists(self.title):
            # 先恢复最小化再同步矩形——最小化窗口的矩形是屏幕外坐标，污染截图区域
            _restore_if_minimized(self.sc._hwnd)
            if self.sc.sync_window_rect():
                m = self.sc.monitor
                self.act.offset_x = m["left"]
                self.act.offset_y = m["top"]
            return
        self.sc = Screenshot(window_title=self.title)  # 找不到 → RuntimeError
        m = self.sc.monitor
        self.act = Action(base_offset=(m["left"], m["top"]))
        # 关闭三国杀时代的"顶栏 y<100 拒点"防护：三角洲/WeGame 没有那个事故区，
        # 且本脚本所有点击均由模板定位（非裸坐标），启动按钮等顶部元素被拦会
        # 表现为静默不点击（实例属性覆盖类默认值 100）
        self.act.DANGER_TOP_Y = 0

    def shot(self):
        self._ensure()
        Action.check_stop()
        return self.sc.capture()

    def find(self, tpl_name: str, threshold: Optional[float] = None):
        """模板定位。窗口未就绪/未命中 → None（不抛错），便于轮询启动中的游戏。"""
        try:
            img = self.shot()
        except RuntimeError as e:
            logger.debug(f"[{self.title}] 窗口未就绪: {e}")
            return None
        path = os.path.join(TPL_DIR, tpl_name)
        return self.matcher.match_one(img, path, threshold)

    def ensure_focus(self):
        """
        确保本窗口获得前台焦点；失败重试，仍失败抛错（宁停勿撞）。

        背景（2026-11-08 实测）：游戏全屏可见但被其他窗口（如视频播放器）占用
        前台时，游戏 UI 会忽略一切鼠标点击——模板匹配照样成功、点击全是空点。
        所有点击/按键/Alt+F4 前必须验证焦点真的在手。
        """
        for _ in range(3):
            self._ensure()
            raise_to_foreground(self.sc._hwnd)
            if ctypes.windll.user32.GetForegroundWindow() == self.sc._hwnd:
                return
            Action.sleep(random.uniform(0.3, 0.6))
        raise RuntimeError(
            f"窗口 '{self.title}' 无法获得前台焦点——可能有其他窗口（视频播放器等）"
            f"在抢占前台。请关掉/最小化遮挡窗口后重跑。")

    def real_click(self, x: int, y: int):
        """带按压时长的点击：瞬时 down/up 可能被游戏输入采样丢弃
        （2026-11-08 烽火页实测：瞬时点击无效），按住 ~80ms 再松开。"""
        import pyautogui as _pag
        sx, sy = self.act._to_screen(x, y)
        _pag.moveTo(sx, sy)
        time.sleep(0.05)
        _pag.mouseDown()
        time.sleep(0.08)
        _pag.mouseUp()

    def click_tpl(self, tpl_name: str, timeout: float = 10, interval: float = 0.9,
                  threshold: Optional[float] = None, required: bool = True,
                  settle: tuple = (0.8, 1.6)) -> bool:
        """
        轮询等模板出现并点击。required=False 时超时只返回 False（自适应步骤）。
        settle: 点击成功后的等待秒数区间（界面切换需要渲染时间，大步骤给长一点）。
        """
        logger.info(f"等待 '{tpl_name}' (超时 {timeout:.0f}s)")
        deadline = time.time() + timeout
        while time.time() < deadline:
            r = self.find(tpl_name, threshold)
            if r is not None:
                self.ensure_focus()  # 游戏无焦点时 UI 忽略点击（空点），先验证焦点
                self.real_click(r[0], r[1])
                logger.info(f"点击 '{tpl_name}' @ ({r[0]}, {r[1]}) 置信度 {r[2]:.2f}")
                _park_mouse()  # 立即移开鼠标，防悬停高亮污染后续模板匹配
                Action.sleep(random.uniform(*settle))
                return True
            Action.sleep(interval)
        msg = f"'{tpl_name}' 超时未出现"
        if required:
            raise RuntimeError(msg)
        logger.info(msg + "（非必需，跳过）")
        return False

    def wait_appear(self, tpl_name: str, timeout: float = 60, interval: float = 1.0) -> bool:
        logger.info(f"等待 '{tpl_name}' 出现 (超时 {timeout:.0f}s)")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.find(tpl_name) is not None:
                logger.info(f"'{tpl_name}' 已出现")
                return True
            Action.sleep(interval)
        return False

    def wait_gone(self, tpl_name: str, timeout: float = 60, interval: float = 1.0) -> bool:
        logger.info(f"等待 '{tpl_name}' 消失 (超时 {timeout:.0f}s)")
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.find(tpl_name, MATCH_THRESHOLD * 0.9) is None:
                logger.info(f"'{tpl_name}' 已消失")
                return True
            Action.sleep(interval)
        return False

    def focus(self):
        self._ensure()
        raise_to_foreground(self.sc._hwnd)


class DeltaForceScript(GameScript):
    """三角洲烽火地带 大退-等待-重开 挂机。"""

    name = "deltaforce"

    def __init__(self, cycles: int = 0):
        """cycles: 轮数上限；0 = 不限（一晚上挂机），空格急停随时中断。"""
        self.cycles = cycles
        self.matcher = TemplateMatcher(threshold=MATCH_THRESHOLD)
        self.we: Optional[_Pane] = None
        self.df: Optional[_Pane] = None

    # ── GameScript 接口 ──────────────────────────

    def get_tasks(self) -> List[Task]:
        return [Task("烽火大退循环", callback=self.run_loop)]

    def _ensure_panes(self) -> bool:
        """构建/复用双窗口上下文。WeGame 必须已开；游戏窗口允许未开（惰性绑定）。"""
        if self.we is not None and self.df is not None:
            return True
        if not _window_exists(WE_WINDOW):
            logger.error("未找到 WeGame 窗口 — 请先打开 WeGame 并登录")
            return False
        self.we = _Pane(WE_WINDOW, self.matcher)
        self.df = _Pane(DF_WINDOW, self.matcher)  # 游戏窗口允许未开，点启动后惰性绑定
        logger.info(
            f"窗口上下文就绪：WeGame ✓；游戏窗口当前"
            f"{'存在' if _window_exists(DF_WINDOW) else '未开（由脚本启动）'}")
        return True

    def startup(self, bot: GameBot) -> bool:
        if not os.path.isdir(TPL_DIR):
            logger.error(f"模板目录不存在: {TPL_DIR}")
            return False
        _prevent_sleep(True)
        if not self._ensure_panes():
            _prevent_sleep(False)
            return False
        return True

    # ── 大退 ─────────────────────────────────────

    def hard_quit(self):
        """Alt+F4 大退游戏；窗口未消失则 taskkill 兜底。
        Alt+F4 发给的是前台窗口——焦点不在游戏上时会误关别的窗口（如视频），
        必须先确保游戏拿到焦点。"""
        logger.info("Alt+F4 大退游戏 ...")
        self.df.ensure_focus()
        Action.sleep(random.uniform(0.4, 0.9))
        self.df.act.hotkey("alt", "f4")
        deadline = time.time() + QUIT_TIMEOUT
        while time.time() < deadline:
            if not _window_exists(DF_WINDOW):
                logger.info("游戏窗口已关闭")
                return
            Action.sleep(1.0)
        logger.warning(f"Alt+F4 后游戏仍在，taskkill 强杀 {DF_PROCESS}")
        subprocess.run(["taskkill", "/F", "/IM", DF_PROCESS],
                       capture_output=True, text=True)
        Action.sleep(3.0)

    # ── 单轮流程 ─────────────────────────────────

    def _ensure_wegame_visible(self):
        """
        WeGame 启动游戏后会自动最小化/隐藏；每轮 A 步前确保其窗口正常显示。
        最小化 → 直接恢复；完全不可见（最小化到托盘）→ 用 wegame:// URL 协议唤起
        （WeGame 注册了该协议，唤起即恢复主窗口）。
        """
        hwnd = _find_hwnd(WE_WINDOW)
        if hwnd is not None:
            _restore_if_minimized(hwnd)
            return
        logger.info("WeGame 窗口不可见（可能最小化到托盘），尝试 wegame:// 唤起 ...")
        try:
            os.startfile("wegame://")
        except Exception as e:
            logger.warning(f"wegame:// 唤起失败: {e!r}")
        deadline = time.time() + 60
        while time.time() < deadline:
            hwnd = _find_hwnd(WE_WINDOW)
            if hwnd is not None:
                _restore_if_minimized(hwnd)
                logger.info("WeGame 窗口已恢复")
                return
            Action.sleep(2.0)
        raise RuntimeError("WeGame 窗口无法唤起 — 请手动打开 WeGame 后重跑")

    def _launch_and_enter_lobby(self):
        """A 启动 → B 烽火 → C 统一等待：继续[空格]/开始游戏[Tab] 逐个处理，直到大厅出现。
        实际顺序：烽火 → 继续[空格] → 开始游戏[Tab] → 大厅；自适应兼容缺步/乱序。
        A→B 约 1 分钟游戏启动时间，由轮询覆盖；每步点击后留界面渲染等待。"""
        self._ensure_wegame_visible()
        self.we.click_tpl("启动.png", timeout=30, required=True)

        # B1 烽火入口（冷启动初始化必经步骤；鼠标停靠机制防悬停干扰）
        self.df.click_tpl("烽火.png", timeout=INIT_TIMEOUT, required=True,
                          interval=5.0, settle=(1.5, 2.5))

        # B2~C 统一等待循环
        logger.info("等待大厅（轮询 烽火[重试点击]/继续[按空格]/开始游戏[按Tab]/行前备战[就绪]）...")
        deadline = time.time() + LOBBY_TIMEOUT
        last_hint = time.time()
        huohuo_retries = 0
        evidence_saved = False
        while time.time() < deadline:
            if self.df.find("行前备战.png") is not None:
                logger.info("大厅就绪")
                return
            if self.df.find("烽火.png") is not None:
                # 点击可能落在界面入场动画/输入未就绪的窗口期而落空，循环内自动重试
                r = self.df.find("烽火.png")
                logger.info(f"烽火选择页仍在，重试点击 @ ({r[0]}, {r[1]})")
                self.df.ensure_focus()
                self.df.real_click(r[0], r[1])
                _park_mouse()
                huohuo_retries += 1
                if huohuo_retries >= 3 and not evidence_saved:
                    # 连续 3 次点击无效 → 存现场截图，供分析按钮真实位置/遮挡情况
                    try:
                        img = self.df.sc.capture()
                        from PIL import Image as _Img
                        _Img.fromarray(img[:, :, ::-1]).save(
                            os.path.join(TPL_DIR, "..", "..", "temp", "df_debug_huohuo.png"))
                        logger.warning("烽火连续 3 次点击无效，已保存现场截图 temp/df_debug_huohuo.png")
                    except Exception:
                        pass
                    evidence_saved = True
                Action.sleep(random.uniform(2.5, 4.0))
                continue
            if self.df.find("继续.png") is not None:
                logger.info("出现【继续】，按空格")
                self.df.ensure_focus()
                Action.sleep(random.uniform(0.3, 0.6))
                self.df.act.press("space")
                _park_mouse()
                Action.sleep(random.uniform(1.5, 2.5))
                continue
            if self.df.find("开始游戏.png") is not None:
                logger.info("出现【开始游戏】，按 Tab")
                self.df.ensure_focus()
                Action.sleep(random.uniform(0.3, 0.6))
                self.df.act.press("tab")
                _park_mouse()
                Action.sleep(random.uniform(2.0, 3.0))
                continue
            Action.sleep(1.2)
            if time.time() - last_hint > 30:
                logger.info("仍在等待大厅（轮询 继续/开始游戏/行前备战）...")
                last_hint = time.time()
        raise RuntimeError("等待大厅超时：『行前备战』一直未出现")

    def _match_and_quit(self):
        """D~J：行前备战 → 长弓溪谷 → 开始行动 → 配装确认 → 出发 → 大退。
        D~I 每步点击后均留界面渲染等待（settle）。"""
        self.df.click_tpl("行前备战.png", timeout=10, required=True,
                          settle=(1.5, 2.5))
        self.df.click_tpl("长弓溪谷.png", timeout=15, required=True,
                          settle=(1.5, 2.5))
        self.df.click_tpl("开始行动.png", timeout=15, required=True,
                          settle=(1.5, 2.5))
        # G 确认配装：匹配成功后出现，等待上限含排队时间（排队期 5 秒一轮省 CPU）
        self.df.click_tpl("确认配装.png", timeout=CONFIRM_LOADOUT_TIMEOUT, required=True,
                          interval=5.0, settle=(1.5, 2.5))
        # H 仍要继续（可选）
        self.df.click_tpl("仍要继续.png", timeout=STILL_CONTINUE_TIMEOUT, required=False,
                          settle=(1.5, 2.5))
        # I 出发
        self.df.click_tpl("出发.png", timeout=DEPART_TIMEOUT, required=True,
                          settle=(1.5, 2.5))
        # J 取消行动出现 → 消失（=匹配进入）→ 3 秒缓冲 → 大退
        if not self.df.wait_appear("取消行动.png", timeout=CANCEL_APPEAR_TIMEOUT):
            raise RuntimeError("点出发后『取消行动』未出现，状态异常，中止本轮")
        if not self.df.wait_gone("取消行动.png", timeout=CANCEL_GONE_TIMEOUT):
            raise RuntimeError("『取消行动』长时间未消失（未匹配进入），中止本轮")
        Action.sleep(DEPART_GONE_GRACE + random.uniform(0, 1.5))
        self.hard_quit()

    # ── 恢复确认弹窗 ─────────────────────────────

    def _resume_prompt(self, next_cycle: int) -> bool:
        """
        大退等待结束后的恢复确认弹窗（置顶，用户可选继续/暂停）。
        RESUME_PROMPT_TIMEOUT 秒无响应视为用户不在电脑前 → 默认继续运行。
        返回 True=继续，False=用户选择暂停。

        用 Win32 MessageBoxW（无需 tkinter 主线程，CLI/GUI 通用），另起守护线程
        在超时后投递 WM_CLOSE 关闭弹窗；关闭路径由 timed_out 标记与用户点击取消区分。
        """
        import ctypes
        import threading
        title = "game_bot · 三角洲挂机"
        text = (f"第 {next_cycle} 轮等待结束，脚本即将恢复操作"
                f"（将接管鼠标键盘约 2~3 分钟）。\n\n"
                "确定 = 继续运行\n取消 = 暂停脚本\n\n"
                f"（{RESUME_PROMPT_TIMEOUT} 秒无响应默认继续）")
        MB_ICONWARNING = 0x30
        MB_TOPMOST = 0x40000
        MB_SETFOREGROUND = 0x10000
        IDOK = 1

        timed_out = threading.Event()

        def _auto_close():
            if timed_out.wait(RESUME_PROMPT_TIMEOUT):
                return  # 用户已手动响应，无需代关
            hwnd = ctypes.windll.user32.FindWindowW(None, title)
            if hwnd:
                ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE

        threading.Thread(target=_auto_close, daemon=True).start()
        rc = ctypes.windll.user32.MessageBoxW(
            0, text, title,
            MB_ICONWARNING | MB_TOPMOST | MB_SETFOREGROUND)  # 默认 OK/Cancel 两键
        if timed_out.is_set():
            logger.info("恢复弹窗无响应，默认继续运行")
            return True
        if rc == IDOK:
            logger.info("用户在恢复弹窗中选择继续")
            return True
        return False

    def run_loop(self, bot: GameBot) -> bool:
        """主循环。兼容单任务『执行』路径（不经 startup，这里自行确保窗口上下文）。"""
        if not self._ensure_panes():
            logger.error("窗口上下文构建失败（WeGame 未开？），任务终止")
            return False
        _prevent_sleep(True)  # 引擎对失败任务会重试重入，防睡眠在这里保证幂等开启
        _park_mouse()         # 清场：鼠标离开任何 UI，防悬停态干扰首轮模板匹配
        started = time.time()
        done_cycles = 0
        try:
            while True:
                if self.cycles and done_cycles >= self.cycles:
                    logger.info(f"已达到轮数上限 {self.cycles}，结束")
                    break
                logger.info("=" * 44)
                logger.info(f"第 {done_cycles + 1} 轮"
                            f"{'/' + str(self.cycles) if self.cycles else ''} 开始")
                self._launch_and_enter_lobby()
                self._match_and_quit()
                wait_min = random.uniform(*WAIT_MIN_RANGE)
                logger.info(f"大退完成，等待 {wait_min:.1f} 分钟后开始下一轮 ...")
                Action.sleep(wait_min * 60)
                # 恢复确认：弹窗 60 秒，用户可选暂停；无响应（人不在电脑前）默认继续
                if not self._resume_prompt(done_cycles + 1):
                    raise StopRequested("用户在恢复弹窗中选择暂停")
                _park_mouse()  # 用户点弹窗后鼠标位置未知，先停靠再恢复轮询
                done_cycles += 1
                logger.info(f"第 {done_cycles} 轮完成（累计 "
                            f"{(time.time() - started) / 3600:.1f} 小时）")
        except StopRequested:
            logger.info("检测到停止请求 — 空格急停，脚本结束")
        finally:
            _prevent_sleep(False)
        logger.info(f"挂机结束：共 {done_cycles} 轮，"
                    f"总时长 {(time.time() - started) / 3600:.1f} 小时")
        return True
