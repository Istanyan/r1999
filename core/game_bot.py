"""
核心控制器 — 整合截图、匹配、OCR、操作模块，提供高层游戏自动化 API。

依赖：本项目的 screenshot, matcher, ocr, action 模块
"""

import logging
import os
import time
from typing import Optional, Tuple

from .screenshot import Screenshot
from .matcher import TemplateMatcher
from .ocr import OCR
from .action import Action

logger = logging.getLogger(__name__)


class GuardError(Exception):
    """画面守卫检测失败 —— 当前画面不是预期的游戏界面。"""


class GameBot:
    """
    游戏自动化核心控制器。

    使用示例
    --------
    >>> bot = GameBot(window_title="MuMu")
    >>> bot.click_if_found("templates/start.png")
    >>> bot.wait_and_click("templates/confirm.png", timeout=10)
    >>> bot.find_and_click_text("领取")
    """

    def __init__(
        self,
        window_title: Optional[str] = None,
        template_dir: str = "templates",
        match_threshold: float = 0.85,
        ocr_lang: str = "ch",
        game_name: Optional[str] = None,
        guard_template: Optional[str] = None,
        guard_threshold: float = 0.7,
    ):
        """
        参数
        ----
        window_title : str  模拟器窗口标题关键字，如 "MuMu"
        template_dir : str  模板图片存放目录
        match_threshold : float  模板匹配阈值 (0~1)
        ocr_lang : str  OCR 语言
        game_name : str  游戏名称，传入后 template_dir 自动设为 templates/{game_name}/
        guard_template : str  画面守卫模板文件名，用于验证当前截图确实是游戏画面
        guard_threshold : float  守卫模板匹配阈值 (0~1)，默认 0.7
        """
        self.template_dir = template_dir
        # game_name 自动指向 templates/{game_name}/
        import os as _os
        if game_name:
            _proj_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            self.template_dir = _os.path.join(_proj_root, "templates", game_name)
        # 确保 template_dir 是绝对路径
        elif not _os.path.isabs(self.template_dir):
            _proj_root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
            self.template_dir = _os.path.join(_proj_root, self.template_dir)
        self._screenshot = Screenshot(window_title=window_title)

        # 计算坐标偏移：截图区域左上角 ≠ 屏幕左上角时需补偿
        monitor = self._screenshot.monitor
        offset_x = monitor.get("left", 0)
        offset_y = monitor.get("top", 0)

        self._matcher = TemplateMatcher(threshold=match_threshold)
        self._ocr = OCR(lang=ocr_lang)
        # 点击防护网需要窗口尺寸（窗口相对坐标上界）
        self._action = Action(base_offset=(offset_x, offset_y),
                              window_size=(monitor.get("width", 0),
                                           monitor.get("height", 0)))

        self._guard_template = guard_template
        self._guard_threshold = guard_threshold

    # ── 属性 ────────────────────────────────────

    @property
    def action(self) -> Action:
        """暴露 Action 对象，便于直接调用底层操作。"""
        return self._action

    @property
    def screenshot(self) -> Screenshot:
        """暴露 Screenshot 对象。"""
        return self._screenshot

    # ── 内部工具方法 ─────────────────────────────

    def _template_path(self, filename: str) -> str:
        """构建模板完整路径。"""
        import os
        # 如果已经是绝对路径则直接返回
        if os.path.isabs(filename):
            return filename
        return os.path.join(self.template_dir, filename)

    def _capture(self):
        """获取当前窗口截图（自动执行空格停止检测与画面守卫检测）。"""
        from .action import Action
        Action.check_stop()
        # 窗口可能被用户拖动：截图前同步位置，并同步点击偏移量与窗口尺寸
        if self._screenshot.sync_window_rect():
            m = self._screenshot.monitor
            self._action.offset_x = m.get("left", 0)
            self._action.offset_y = m.get("top", 0)
            self._action.set_window_size(m.get("width", 0), m.get("height", 0))
        img = self._screenshot.capture()
        self._guard_check(img)
        return img

    def _guard_check(self, img):
        """
        画面守卫：验证当前截图是否匹配守卫模板。
        不匹配则抛出 GuardError，中断当前任务。
        """
        if self._guard_template is None:
            return
        path = self._template_path(self._guard_template)
        if not self._matcher.is_present(img, path, self._guard_threshold):
            raise GuardError(
                f"画面守卫触发：当前画面不匹配守卫模板 "
                f"'{self._guard_template}'（阈值 {self._guard_threshold}），"
                f"任务已暂停。请确保游戏窗口在前台后重试。"
            )

    def check_screen(self, template: Optional[str] = None, threshold: Optional[float] = None) -> bool:
        """
        手动检查当前画面是否匹配指定模板（绕过守卫异常）。
        用于任务脚本中判断当前处于哪个界面。
        """
        tpl = template or self._guard_template
        if tpl is None:
            return True
        thr = threshold or self._guard_threshold
        img = self._screenshot.capture()
        path = self._template_path(tpl)
        return self._matcher.is_present(img, path, thr)

    # ── 高层 API ────────────────────────────────

    def sleep(self, seconds: float):
        """等待指定秒数。"""
        self._action.sleep(seconds)

    # ── 模板匹配相关 ────────────────────────────

    def find(
        self,
        template: str,
        threshold: Optional[float] = None,
    ) -> Optional[Tuple[int, int, float]]:
        """
        在当前屏幕中查找模板，不执行点击。

        返回
        ----
        (center_x, center_y, confidence) 或 None
        """
        img = self._capture()
        path = self._template_path(template)
        return self._matcher.match_one(img, path, threshold)

    def find_region(
        self,
        template: str,
        threshold: Optional[float] = None,
    ) -> Optional[Tuple[int, int, int, int, float]]:
        """
        查找模板并返回完整边界框，用于在匹配区域内计算子坐标。

        返回
        ----
        (left, top, width, height, confidence) 或 None
        """
        import cv2
        from .matcher import _imread
        img = self._capture()
        path = self._template_path(template)
        th = threshold if threshold is not None else self._matcher.threshold
        tpl = _imread(path)
        if tpl is None:
            return None
        h, w = tpl.shape[:2]
        result = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val < th:
            return None
        return (max_loc[0], max_loc[1], w, h, max_val)

    def click(self, x: int, y: int, template: Optional[str] = None,
              allow_danger: bool = False):
        """
        在指定绝对坐标点击（窗口相对坐标）。

        template: 可选，点击所依据的模板名——传入时日志会记录"依据哪个模板"，
        裸坐标点击（不传 template）日志会打 [裸] 标记，便于事后排查误点
        （2026-09-06 点击防护网：run16 任务页页签误点 6 次后才被肉眼发现）。

        防护区规则（顶栏 y<100 / 越界）:
        - template 非 None → 放行（模板确认的可信点击）
        - allow_danger=True → 放行（调用方显式声明合法,如合成大将军对局页
          锚点 y≈67 落在顶栏区但对局页该区域是游戏区）
        - 都没有 → 拒绝（run10/11 误点商店的事故来源）
        """
        import inspect
        caller = inspect.stack()[1]
        src = f"{os.path.basename(caller.filename)}:{caller.lineno}"
        if template:
            logger.info(f"click: ({x}, {y}) [模板={template}] ← {src}")
        else:
            logger.info(f"click: ({x}, {y}) [裸坐标{'·放行' if allow_danger else ''}] ← {src}")
        self._action.click(x, y, allow_danger=allow_danger or template is not None)

    def scroll(self, x: int, y: int, clicks: int):
        """在窗口相对坐标 (x,y) 处滚动鼠标滚轮；clicks 正数向上、负数向下。"""
        self._action.scroll(x, y, clicks)

    def find_all(
        self,
        template: str,
        threshold: Optional[float] = None,
        max_results: int = 20,
    ) -> list:
        """查找所有匹配位置。"""
        img = self._capture()
        path = self._template_path(template)
        return self._matcher.match_all(img, path, threshold, max_results)

    def is_present(
        self,
        template: str,
        threshold: Optional[float] = None,
    ) -> bool:
        """判断模板是否出现在当前屏幕。"""
        img = self._capture()
        path = self._template_path(template)
        return self._matcher.is_present(img, path, threshold)

    def wait_and_click(
        self,
        template: str,
        timeout: float = 30,
        interval: float = 0.5,
        threshold: Optional[float] = None,
    ) -> bool:
        """
        等待模板出现并点击。

        参数
        ----
        template : str  模板文件名
        timeout : float  超时时间（秒）
        interval : float  检测间隔（秒）
        threshold : float  匹配置信度阈值

        返回
        ----
        bool : 是否成功点击
        """
        logger.info(f"wait_and_click: 等待模板 '{template}' (超时 {timeout}s) ...")
        start = time.time()
        while time.time() - start < timeout:
            result = self.find(template, threshold)
            if result is not None:
                x, y, conf = result
                self.click(x, y, template=template)
                logger.info(f"wait_and_click 成功: 点击 ({x}, {y})")
                return True
            self._action.sleep(interval)

        logger.warning(f"wait_and_click 超时: '{template}' 未在 {timeout}s 内出现")
        return False

    def click_if_found(
        self,
        template: str,
        threshold: Optional[float] = None,
    ) -> bool:
        """
        如果当前屏幕存在模板则点击。

        返回
        ----
        bool : 是否找到并点击
        """
        result = self.find(template, threshold)
        if result is not None:
            x, y, _ = result
            self.click(x, y, template=template)
            logger.info(f"click_if_found 成功: 点击 ({x}, {y})")
            return True
        logger.debug(f"click_if_found: '{template}' 未找到，跳过")
        return False

    def wait_until_gone(
        self,
        template: str,
        timeout: float = 30,
        interval: float = 0.5,
        threshold: Optional[float] = None,
    ) -> bool:
        """
        等待某个元素从屏幕消失。

        返回
        ----
        bool : True 表示已消失，False 表示超时
        """
        logger.info(f"wait_until_gone: 等待 '{template}' 消失 (超时 {timeout}s) ...")
        start = time.time()
        while time.time() - start < timeout:
            if not self.is_present(template, threshold):
                logger.info(f"'{template}' 已消失")
                return True
            self._action.sleep(interval)

        logger.warning(f"wait_until_gone 超时: '{template}' 仍然存在")
        return False

    # ── OCR 相关 ────────────────────────────────

    def recognize_text(self) -> list:
        """识别当前屏幕所有文字。"""
        img = self._capture()
        return self._ocr.recognize(img)

    def find_text(
        self,
        text: str,
        fuzzy: bool = False,
    ) -> Optional[dict]:
        """
        在屏幕中查找指定文字位置（不点击）。

        返回
        ----
        dict 或 None  {"text", "confidence", "box", "center"}
        """
        img = self._capture()
        return self._ocr.find_text(img, text, fuzzy=fuzzy)

    def find_and_click_text(
        self,
        text: str,
        fuzzy: bool = False,
    ) -> bool:
        """
        OCR 识别屏幕文字，找到后点击文字中心。

        返回
        ----
        bool : 是否找到并点击
        """
        result = self.find_text(text, fuzzy=fuzzy)
        if result is not None:
            cx, cy = result["center"]
            self.click(cx, cy, template=f"OCR'{text}'")
            logger.info(f"find_and_click_text 成功: '{text}' @ ({cx}, {cy})")
            return True
        logger.info(f"find_and_click_text: 未找到文字 '{text}'")
        return False

    def find_all_text(
        self,
        text: str,
        fuzzy: bool = False,
    ) -> list:
        """查找屏幕上所有匹配文字的位置。"""
        img = self._capture()
        return self._ocr.find_all_text(img, text, fuzzy=fuzzy)
