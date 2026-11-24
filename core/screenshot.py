"""
截图模块 — 基于 mss，支持截取指定窗口区域。

依赖：mss, numpy, Pillow
"""

import ctypes
import logging
from typing import Optional, Tuple

import mss
import numpy as np
from mss.exception import ScreenShotError
from PIL import Image

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# 窗口查找（Windows 平台）
# ──────────────────────────────────────────────

def raise_to_foreground(hwnd: int) -> bool:
    """
    强制把窗口带到前台（绕过 Windows 前台锁）。

    后台进程直接调 SetForegroundWindow 会被系统静默拒绝（只闪任务栏图标），
    此时截图截到的是覆盖在上面的其他窗口。组合两种经典绕过：
    1. 先模拟按下 ALT —— 系统会解除前台锁；
    2. AttachThreadInput 附加到当前前台窗口的输入线程，共享前台权限。
    最后 TOPMOST→NOTOPMOST 兜底确保 z 序可见。

    返回 True 表示窗口已确实成为前台窗口。
    """
    import ctypes
    import time as _t

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    if not user32.IsWindow(hwnd):
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        _t.sleep(0.3)

    if user32.GetForegroundWindow() == hwnd:
        return True

    fg = user32.GetForegroundWindow()
    fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    cur_tid = kernel32.GetCurrentThreadId()
    attached = False
    if fg_tid and fg_tid != cur_tid:
        attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
    try:
        user32.keybd_event(0x12, 0, 0, 0)   # ALT down：解除前台锁
        user32.SetForegroundWindow(hwnd)
        user32.keybd_event(0x12, 0, 2, 0)   # ALT up
    finally:
        if attached:
            user32.AttachThreadInput(cur_tid, fg_tid, False)

    # z 序兜底：TOPMOST 提到最上再撤掉（即使抢焦点失败，窗口也可见可截、可点）
    HWND_TOPMOST = -1
    HWND_NOTOPMOST = -2
    SWP_NOMOVE = 0x0002
    SWP_NOSIZE = 0x0001
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)
    user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE)

    ok = user32.GetForegroundWindow() == hwnd
    if not ok:
        logging.getLogger(__name__).warning(
            "窗口前置失败（可能被其他窗口遮挡，截图/点击可能落在别的窗口上）"
        )
    return ok


def find_window_rect(title_keyword: str,
                     bring_to_front: bool = True) -> Optional[Tuple[Tuple[int, int, int, int], int]]:
    """
    根据窗口标题关键字查找窗口矩形区域（DPI 感知）。

    参数
    ----
    title_keyword : str
        窗口标题中包含的关键字（如 "MuMu"）。
    bring_to_front : bool
        是否还原并置前窗口。环境自检传 False，避免仅检查就抢焦点。

    返回
    ----
    tuple (left, top, right, bottom) 或 None
    """
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32

    # 启用 DPI 感知，避免高 DPI 下坐标偏移
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass

    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    found: list = []

    def enum_windows_proc(hwnd, lParam):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if length == 0:
            return True
        buff = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buff, length + 1)
        title = buff.value
        if title_keyword.lower() in title.lower():
            if bring_to_front:
                # 如果窗口最小化，先还原
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    # 短暂等待窗口恢复
                    import time
                    time.sleep(0.3)
                # 置顶确保不被遮挡（带前台锁绕过，见 raise_to_foreground）
                raise_to_foreground(hwnd)
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            found.append(((rect.left, rect.top, rect.right, rect.bottom), hwnd))
            return False  # 找到即停止
        return True

    user32.EnumWindows(WNDENUMPROC(enum_windows_proc), 0)
    if found:
        rect, hwnd = found[0]
        logger.info(f"找到窗口 '{title_keyword}' → 区域 {rect} HWND={hwnd}")
        return rect, hwnd
    logger.warning(f"未找到包含 '{title_keyword}' 的可见窗口（请确认模拟器已启动且未被关闭）")
    return None


# ──────────────────────────────────────────────
# 截图
# ──────────────────────────────────────────────

class Screenshot:
    """基于 mss 的截图工具。"""

    def __init__(self, window_title: Optional[str] = None):
        """
        参数
        ----
        window_title : str 或 None
            窗口标题关键字。传入则自动定位窗口区域；
            不传则截取主显示器全屏。
        """
        self._sct = mss.mss()
        self._monitor: Optional[dict] = None
        self._hwnd = None
        self._window_title = window_title

        if window_title:
            found = find_window_rect(window_title)
            if found is None:
                raise RuntimeError(
                    f"无法定位窗口: {window_title}（请确认模拟器已启动、游戏已打开）"
                )
            rect, self._hwnd = found
            self._monitor = {
                "left": rect[0],
                "top": rect[1],
                "width": rect[2] - rect[0],
                "height": rect[3] - rect[1],
            }
        else:
            # 默认截取主显示器
            self._monitor = self._sct.monitors[1]

    @property
    def monitor(self) -> dict:
        return self._monitor

    def _refind_window(self) -> bool:
        """窗口句柄失效（模拟器重启/窗口重建）后按标题重新定位。

        2026-11-13 r1999 run 实测：MuMu 窗口重建后 `_hwnd` 失效，
        `sync_window_rect` 直接返回 False，`capture` 持续抛
        `BitBlt: 无效的窗口句柄`，脚本空转 12 分钟。这里改为自动重找。
        """
        if not self._window_title:
            return False
        found = find_window_rect(self._window_title)
        if found is None:
            logger.warning(f"窗口 '{self._window_title}' 已失效且未能重新定位")
            return False
        rect, hwnd = found
        self._hwnd = hwnd
        self._monitor = {
            "left": rect[0],
            "top": rect[1],
            "width": rect[2] - rect[0],
            "height": rect[3] - rect[1],
        }
        logger.info(f"窗口句柄失效 → 已重新定位 HWND={hwnd} 区域={rect}")
        return True

    def sync_window_rect(self) -> bool:
        """
        同步窗口矩形：窗口被移动/调整大小后刷新截图区域。

        bot 在初始化时记录一次窗口位置，此后若窗口被用户拖动（MuMu 是
        浮动窗口，很容易被碰到），截图与点击会整体错位、全部匹配失败
        （2026-08-30 run9 实测）。每次截图前调用本方法，位置变化才更新。

        返回 True 表示位置相对上次发生了变化。
        """
        if self._hwnd is None:
            return False
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        if not user32.IsWindow(self._hwnd):
            return self._refind_window()
        rect = wintypes.RECT()
        user32.GetWindowRect(self._hwnd, ctypes.byref(rect))
        new = {
            "left": rect.left,
            "top": rect.top,
            "width": rect.right - rect.left,
            "height": rect.bottom - rect.top,
        }
        if new == self._monitor:
            return False
        logger.info(
            f"窗口位置变化: ({self._monitor['left']}, {self._monitor['top']}, "
            f"{self._monitor['width']}x{self._monitor['height']}) → "
            f"({new['left']}, {new['top']}, {new['width']}x{new['height']})，已同步"
        )
        self._monitor = new
        return True

    def _ensure_foreground(self):
        """将目标窗口置顶，确保截图不被其他窗口遮挡。

        若窗口已在前台且未最小化，直接返回 —— 避免每次截图都执行
        SetWindowPos/SetForegroundWindow 抖动并 sleep 0.1s。
        高频检测（如钓鱼主循环每秒十余帧截图）下，这会拖慢每一帧并造成卡顿。

        2026-08-30：改用 raise_to_foreground（ALT+AttachThreadInput 绕过前台锁）。
        实测用户在其他窗口前使用电脑时，游戏被盖住，裸 SetForegroundWindow
        被系统静默拒绝，bot 截到的是覆盖窗口的像素，全部模板匹配失败。
        """
        if self._hwnd is None:
            return
        if not ctypes.windll.user32.IsWindow(self._hwnd):
            return
        # 已在前台 → 无需重复置顶（前台窗口必然未被遮挡）
        if ctypes.windll.user32.GetForegroundWindow() == self._hwnd:
            return
        raise_to_foreground(self._hwnd)
        import time
        time.sleep(0.15)  # 等待窗口重绘稳定，避免截到半渲染帧

    def capture(self) -> np.ndarray:
        """
        截取当前屏幕区域。

        返回
        ----
        np.ndarray : BGR 格式图像（H×W×3, uint8）
        """
        self._ensure_foreground()
        try:
            img = self._sct.grab(self._monitor)
        except ScreenShotError:
            # 句柄失效/窗口重建：重找一次再截，避免整段循环空转报错
            if not self._refind_window():
                raise
            img = self._sct.grab(self._monitor)
        # mss 返回 BGRA，转为 BGR numpy 数组
        arr = np.array(img, dtype=np.uint8)
        # 去掉 alpha 通道，保留 BGR
        bgr = arr[:, :, :3]
        # mss BGRA 中的 BGR 顺序实际为 BGRA→BGR 直接取前三通道即可
        return bgr

    def capture_pil(self) -> Image.Image:
        """截取并返回 PIL Image（RGB）。"""
        bgr = self.capture()
        rgb = bgr[:, :, ::-1]  # BGR → RGB
        return Image.fromarray(rgb)

    def capture_region(self, x: int, y: int, w: int, h: int) -> np.ndarray:
        """
        截取当前窗口内的指定子区域（相对于窗口左上角）。

        参数
        ----
        x, y : int  左上角偏移
        w, h : int  宽高

        返回
        ----
        np.ndarray : BGR 格式
        """
        full = self.capture()
        y_end = min(y + h, full.shape[0])
        x_end = min(x + w, full.shape[1])
        return full[y:y_end, x:x_end]

    def save(self, filepath: str):
        """截屏并保存为文件。"""
        pil_img = self.capture_pil()
        pil_img.save(filepath)
        logger.info(f"截图已保存: {filepath}")
