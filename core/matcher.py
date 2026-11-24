"""
模板匹配模块 — 基于 opencv-python，支持单模板/多模板匹配。

依赖：opencv-python, numpy
"""

import logging
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _to_gray(img: np.ndarray) -> np.ndarray:
    """BGR/灰度统一转灰度。灰度匹配对底色/遮罩的色相变化不敏感，
    只看形状与亮度结构（2026-09-06 用户要求：chengzhang 检测忽略颜色）。"""
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def _match_gray(screenshot: np.ndarray,
                template: np.ndarray,
                threshold: float) -> Optional[Tuple[int, int, float]]:
    """灰度域 TM_CCOEFF_NORMED 匹配，返回 (left, top, conf) 或 None。"""
    g_img = _to_gray(screenshot)
    g_tpl = _to_gray(template)
    result = cv2.matchTemplate(g_img, g_tpl, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    if max_val < threshold:
        return None
    return (max_loc[0], max_loc[1], float(max_val))


def _imread(path: str):
    """兼容中文路径的图片读取（cv2.imread 不支持中文）。"""
    try:
        with open(path, "rb") as f:
            data = np.frombuffer(f.read(), dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    except Exception as e:
        logger.error(f"无法读取图片: {path}, 错误: {e}")
        return None


class TemplateMatcher:
    """基于 OpenCV 的模板匹配器。"""

    def __init__(self, threshold: float = 0.85):
        """
        参数
        ----
        threshold : float
            匹配置信度阈值（0~1），低于此值的匹配结果将被丢弃。
        """
        self.threshold = threshold

    # ── 单模板匹配 ──────────────────────────────

    def match_one_gray(
        self,
        screenshot: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
    ) -> Optional[Tuple[int, int, float]]:
        """
        灰度域单模板匹配（忽略颜色，仅形状/亮度结构）。

        用于底色易变的元素（如成长按钮的红点态/空闲态/顶栏横幅扫过），
        彩色匹配会随底色掉分，灰度不受色相影响。返回同 match_one。
        """
        th = threshold if threshold is not None else self.threshold
        template = _imread(template_path)
        if template is None:
            logger.error(f"无法读取模板: {template_path}")
            return None
        if template.shape[0] > screenshot.shape[0] or template.shape[1] > screenshot.shape[1]:
            logger.warning(f"模板尺寸({template.shape[1]}x{template.shape[0]})大于截图，跳过匹配")
            return None
        hit = _match_gray(screenshot, template, th)
        if hit is None:
            logger.debug(f"灰度匹配 '{template_path}' 未找到 (< {th})")
            return None
        x, y, conf = hit
        h, w = template.shape[:2]
        center_x = x + w // 2
        center_y = y + h // 2
        logger.info(f"灰度匹配成功 '{template_path}' → 中心({center_x}, {center_y}) 置信度 {conf:.3f}")
        return (center_x, center_y, conf)

    def match_one(
        self,
        screenshot: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
    ) -> Optional[Tuple[int, int, float]]:
        """
        在截图中查找单个模板的最佳匹配位置。

        参数
        ----
        screenshot : np.ndarray  BGR 截图
        template_path : str     模板图片路径
        threshold : float       覆盖实例默认阈值

        返回
        ----
        (center_x, center_y, confidence) 或 None
        """
        th = threshold if threshold is not None else self.threshold
        template = _imread(template_path)
        if template is None:
            logger.error(f"无法读取模板: {template_path}")
            return None

        if template.shape[0] > screenshot.shape[0] or template.shape[1] > screenshot.shape[1]:
            logger.warning(f"模板尺寸({template.shape[1]}x{template.shape[0]})大于截图，跳过匹配")
            return None

        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < th:
            logger.debug(f"模板 '{template_path}' 未找到 (置信度 {max_val:.3f} < {th})")
            return None

        h, w = template.shape[:2]
        center_x = max_loc[0] + w // 2
        center_y = max_loc[1] + h // 2
        logger.info(f"匹配成功 '{template_path}' → 中心({center_x}, {center_y}) 置信度 {max_val:.3f}")
        return (center_x, center_y, max_val)

    # ── 多模板匹配 ──────────────────────────────

    def match_all(
        self,
        screenshot: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
        max_results: int = 20,
    ) -> List[Tuple[int, int, float]]:
        """
        查找截图中模板的**所有**匹配位置（降序按置信度）。

        参数
        ----
        screenshot : np.ndarray
        template_path : str
        threshold : float
        max_results : int  最多返回数量

        返回
        ----
        [(center_x, center_y, confidence), ...]
        """
        th = threshold if threshold is not None else self.threshold
        template = _imread(template_path)
        if template is None:
            logger.error(f"无法读取模板: {template_path}")
            return []

        result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
        h, w = template.shape[:2]

        locations = []
        # 将匹配结果中高于阈值的位置全部收集，并用 NMS 去重
        ys, xs = np.where(result >= th)
        for y, x in zip(ys, xs):
            confidence = float(result[y, x])
            locations.append((x, y, confidence))

        # 按置信度降序
        locations.sort(key=lambda p: p[2], reverse=True)

        # 简单 NMS：按左上角坐标比较，重叠的只保留最高置信度候选
        kept_tl: List[Tuple[int, int, float]] = []
        for loc in locations:
            overlap = False
            for k in kept_tl:
                if abs(loc[0] - k[0]) < w * 0.5 and abs(loc[1] - k[1]) < h * 0.5:
                    overlap = True
                    break
            if not overlap:
                kept_tl.append((loc[0], loc[1], loc[2]))
            if len(kept_tl) >= max_results:
                break

        # 转为中心坐标返回
        kept = [(x + w // 2, y + h // 2, c) for x, y, c in kept_tl]

        logger.info(f"多模板匹配 '{template_path}' → 找到 {len(kept)} 个 (阈值 {th})")
        return kept

    # ── 是否匹配 ────────────────────────────────

    def is_present(
        self,
        screenshot: np.ndarray,
        template_path: str,
        threshold: Optional[float] = None,
    ) -> bool:
        """判断模板是否出现在截图中。"""
        return self.match_one(screenshot, template_path, threshold) is not None
