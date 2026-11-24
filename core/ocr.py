"""
OCR 模块 — 基于 PaddleOCR，识别屏幕上的文字和位置。

依赖：paddleocr, paddlepaddle, numpy, Pillow
"""

import logging
import os
import shutil
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

from . import runtime

logger = logging.getLogger(__name__)


class OCR:
    """基于 PaddleOCR 的文字识别器。"""

    def __init__(self, lang: str = "ch", use_gpu: bool = False):
        """
        参数
        ----
        lang : str  语言，'ch' 中文 / 'en' 英文
        use_gpu : bool  是否使用 GPU 加速
        """
        self._lang = lang
        self._ocr = None
        self._use_gpu = use_gpu
        self._model_dir = os.environ.get("GAMEBOT_MODEL_DIR") or None

    @staticmethod
    def _redirect_home_if_needed() -> Optional[str]:
        """
        中文用户目录（如 C:\\Users\\言\\）会让 Paddle 推理引擎打不开模型文件
        （C++ 读不了非 ASCII 路径）。把主目录临时重定向到项目下的 ASCII 路径，
        并把已下载的模型迁移过去。返回原 USERPROFILE；未重定向返回 None。
        """
        home = os.path.expanduser("~")
        try:
            home.encode("ascii")
            return None  # 纯 ASCII，无需处理
        except UnicodeEncodeError:
            pass
        # 运行期可写目录下的 paddle_home；便携版优先用 GAMEBOT_DATA 指定位置。
        ascii_home = os.path.join(str(runtime.data_home()), "paddle_home")
        os.makedirs(ascii_home, exist_ok=True)
        # 迁移已下载的模型（新位置还没有时才复制）
        src = os.path.join(home, ".paddleocr")
        dst = os.path.join(ascii_home, ".paddleocr")
        if os.path.isdir(src) and not os.path.isdir(dst):
            try:
                shutil.copytree(src, dst)
                logger.info(f"PaddleOCR 模型已迁移到 ASCII 路径: {dst}")
            except Exception as e:
                logger.warning(f"模型迁移失败（首次会自动重新下载）: {e}")
        orig = os.environ.get("USERPROFILE")
        os.environ["USERPROFILE"] = ascii_home
        os.environ["HOME"] = ascii_home
        return orig

    @property
    def ocr(self):
        """懒加载 PaddleOCR 实例；有打包模型时直接传模型目录，避免联网下载。"""
        if self._ocr is None:
            model_dir = self._model_dir or str(runtime.model_dir())
            det = os.path.join(model_dir, "det", "ch_PP-OCRv4_det_infer")
            rec = os.path.join(model_dir, "rec", "ch_PP-OCRv4_rec_infer")
            cls = os.path.join(model_dir, "cls", "ch_ppocr_mobile_v2.0_cls_infer")
            has_models = os.path.isdir(det) and os.path.isdir(rec)
            orig_home = None if has_models else self._redirect_home_if_needed()
            # paddleocr 导入时会篡改 root logger 级别（实测 INFO→WARNING），
            # 导致初始化后本项目所有 INFO 日志静默丢失（2026-08-29 钓鱼
            # 连钓 6 条无日志、"计数与日志对不上"的根因），先记录后恢复
            root_logger = logging.getLogger()
            prev_level = root_logger.level
            try:
                from paddleocr import PaddleOCR
                root_logger.setLevel(prev_level)  # 导入即篡改，导入完立即恢复
                kwargs = dict(lang=self._lang, use_angle_cls=False, show_log=False)
                if has_models:
                    kwargs.update(det_model_dir=det, rec_model_dir=rec)
                    if os.path.isdir(cls):
                        kwargs["cls_model_dir"] = cls
                self._ocr = PaddleOCR(**kwargs)
                logger.info(f"PaddleOCR 初始化完成 (lang={self._lang}, 模型={model_dir if has_models else '默认'})")
            except Exception as e:
                logger.error(f"PaddleOCR 初始化失败: {e}")
                raise
            finally:
                root_logger.setLevel(prev_level)
                # 恢复环境变量，避免影响进程其他部分
                if orig_home is not None:
                    os.environ["USERPROFILE"] = orig_home
                    os.environ["HOME"] = orig_home
        return self._ocr

    def recognize(self, image: np.ndarray) -> List[dict]:
        """
        识别图像中的文字。

        参数
        ----
        image : np.ndarray  BGR 格式图像 (H×W×3)

        返回
        ----
        [
            {
                "text": "识别文字",
                "confidence": 0.98,
                "box": [(x1,y1), (x2,y2), (x3,y3), (x4,y4)],  # 四点坐标
                "center": (cx, cy)  # 中心坐标
            },
            ...
        ]
        """
        # PaddleOCR 期望 RGB 格式
        if image.shape[-1] == 3:
            rgb = image[:, :, ::-1]  # BGR → RGB
        else:
            rgb = image

        raw = self.ocr.ocr(rgb, cls=False)
        if raw is None or len(raw) == 0 or raw[0] is None:
            return []

        results: List[dict] = []
        for line in raw[0]:
            box = line[0]
            text = line[1][0]
            confidence = line[1][1]

            # 计算中心点
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            cx = sum(xs) / len(xs)
            cy = sum(ys) / len(ys)

            results.append({
                "text": text,
                "confidence": confidence,
                "box": box,
                "center": (int(cx), int(cy)),
            })

        return results

    def find_text(
        self,
        image: np.ndarray,
        target: str,
        fuzzy: bool = False,
    ) -> Optional[dict]:
        """
        在图像中查找目标文字。

        参数
        ----
        image : np.ndarray
        target : str  要查找的文字
        fuzzy : bool  是否模糊匹配（包含关系）

        返回
        ----
        dict 或 None（同 recognize 返回的单条结构）
        """
        results = self.recognize(image)
        for r in results:
            if fuzzy and (target in r["text"]):
                logger.info(f"模糊匹配到文字 '{target}' → '{r['text']}' @ {r['center']}")
                return r
            if r["text"] == target:
                logger.info(f"精确匹配到文字 '{target}' @ {r['center']}")
                return r
        logger.debug(f"未找到文字: '{target}'")
        return None

    def find_all_text(
        self,
        image: np.ndarray,
        target: str,
        fuzzy: bool = False,
    ) -> List[dict]:
        """查找所有匹配的文字位置。"""
        results = self.recognize(image)
        matched = []
        for r in results:
            if fuzzy and (target in r["text"]):
                matched.append(r)
            elif r["text"] == target:
                matched.append(r)
        return matched
