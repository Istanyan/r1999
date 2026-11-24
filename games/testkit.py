"""
测试工具脚本 — GUI「游戏选择=测试」时提供代码/模板测试任务。

任务1 模板识别测试（2026-10-24 用户需求）:
    扫描 games/ 下代码实际引用的全部模板（含 引用文件:行号），
    对当前画面逐一彩色匹配，输出 相似度 / 中心位置 / 引用处 / 建议阈值——
    用于确定每个模板该设多少阈值（先切到目标画面再执行）。
任务2 OCR 文字识别测试:
    对当前画面全屏 OCR，输出 文字 / 置信度 / 位置。

注意:动态拼接的模板名（f"{chest}.png" → 宝箱1-4、jl0-10、40/30/20 等）
无法静态扫描，会在报告中提示；需要测试它们时手动改名临时引用或看运行日志。
"""
import glob
import logging
import os
import re
import time

import cv2

from core.game_bot import GameBot
from core.task import Task
from core.game_script import GameScript
from core.matcher import _imread

logger = logging.getLogger("GameBot")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_GAMES_DIR = os.path.join(_ROOT, "games")
_TEMPLATES_DIR = os.path.join(_ROOT, "templates")

# 匹配 "xxx.png" / 'xxx.png'（含中文/字母数字/下划线/横杠的模板名）
_TPL_PAT = re.compile(r"""["']([\w\u4e00-\u9fff\-]+\.png)["']""")
# f"{var}.png" 之类的动态模板引用（只提示、不展开）
_DYN_PAT = re.compile(r"""f["'][^"']*\{[a-zA-Z_][\w]*\}[^"']*\.png["']""")


def _scan_template_refs() -> dict:
    """
    静态扫描 games/ 下所有 .py,收集模板引用。

    返回 {模板名: {"games": {游戏目录...}, "refs": ["文件:行", ...]}}
    """
    refs: dict = {}
    for path in glob.glob(os.path.join(_GAMES_DIR, "**", "*.py"), recursive=True):
        # 跳过工具/测试文件自身（文档示例中的模板名不是真实引用）
        if os.path.basename(path) == "testkit.py":
            continue
        rel = os.path.relpath(path, _GAMES_DIR).replace("\\", "/")
        parts = rel.split("/")
        game = parts[0] if len(parts) > 1 else ""  # 仅子目录算游戏(bbdc.py 顶层不算)
        try:
            with open(path, encoding="utf-8") as f:
                lines = f.read().splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            for m in _TPL_PAT.finditer(line):
                name = m.group(1)
                entry = refs.setdefault(name, {"games": set(), "refs": []})
                if game:
                    entry["games"].add(game)
                entry["refs"].append(f"{os.path.basename(path)}:{i}")
    return refs


def _verdict(conf) -> str:
    """按置信度给出建议阈值档位。"""
    if conf >= 0.99:
        return "✔ 0.99 可用"
    if conf >= 0.90:
        return "✔ 0.90 可用"
    if conf >= 0.80:
        return "✔ 0.85 可用"
    if conf >= 0.70:
        return "⚠ 0.70-0.79 偏低,建议 0.70 并观察"
    return "✗ 当前画面未命中(不在画面或模板失效)"


def _template_test(bot: GameBot) -> bool:
    """任务1:模板识别测试。"""
    t0 = time.perf_counter()
    refs = _scan_template_refs()
    if not refs:
        logger.warning("[模板测试] 未在 games/ 代码中扫描到模板引用")
        return False
    logger.info(
        f"[模板测试] 代码共引用 {len(refs)} 个模板,开始对当前画面逐一匹配..."
    )
    img = bot.screenshot.capture()

    rows = []   # (name, conf, center, refs_str, status)
    missing = []
    for name in sorted(refs):
        entry = refs[name]
        # 模板文件定位:优先引用它的游戏目录,其次 templates/ 根
        path = None
        for g in sorted(entry["games"]):
            p = os.path.join(_TEMPLATES_DIR, g, name)
            if os.path.isfile(p):
                path = p
                break
        if path is None:
            p = os.path.join(_TEMPLATES_DIR, name)
            path = p if os.path.isfile(p) else None
        if path is None:
            missing.append((name, entry["refs"][:2]))
            continue
        tpl = _imread(path)
        if tpl is None:
            missing.append((name, entry["refs"][:2]))
            continue
        th, tw = tpl.shape[:2]
        if th > img.shape[0] or tw > img.shape[1]:
            rows.append((name, 0.0, None, ",".join(entry["refs"][:2]), "✗ 模板大于画面"))
            continue
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
        _, conf, _, loc = cv2.minMaxLoc(res)
        center = (loc[0] + tw // 2, loc[1] + th // 2)
        rows.append((name, float(conf), center, ",".join(entry["refs"][:2]), _verdict(conf)))

    # 按置信度降序输出
    rows.sort(key=lambda r: r[1], reverse=True)
    for name, conf, center, ref, status in rows:
        pos = f"中心({center[0]},{center[1]})" if center else "-"
        logger.info(
            f"[模板测试] {name}  conf={conf:.3f}  {pos}  ← {ref}  [{status}]"
        )
    for name, r in missing:
        logger.warning(f"[模板测试] {name}  模板文件缺失/损坏  ← {','.join(r)}")

    # 分档统计
    bands = {"≥0.99": 0, "0.90-0.99": 0, "0.80-0.90": 0, "0.70-0.80": 0, "<0.70": 0}
    for _, conf, *_ in rows:
        if conf >= 0.99:
            bands["≥0.99"] += 1
        elif conf >= 0.90:
            bands["0.90-0.99"] += 1
        elif conf >= 0.80:
            bands["0.80-0.90"] += 1
        elif conf >= 0.70:
            bands["0.70-0.80"] += 1
        else:
            bands["<0.70"] += 1
    logger.info(
        "[模板测试] 分档: " + "  ".join(f"{k}:{v}" for k, v in bands.items())
        + f"  缺失:{len(missing)}"
    )
    logger.info(
        "[模板测试] 提示: <0.70 的模板若应当出现在当前画面,说明模板已过期或被皮肤影响;"
        "若本就不该出现(如其他页面的按钮),属正常。阈值建议: 独有元素取实测值-0.02,"
        "易误匹配元素取高档(≥0.95)。"
    )
    # 动态模板提示
    dyn_files = set()
    for path in glob.glob(os.path.join(_GAMES_DIR, "**", "*.py"), recursive=True):
        try:
            with open(path, encoding="utf-8") as f:
                if _DYN_PAT.search(f.read()):
                    dyn_files.add(os.path.basename(path))
        except Exception:
            continue
    if dyn_files:
        logger.info(
            "[模板测试] 动态拼接模板(f-string,未收录): 宝箱1-4 / jl0-10 / 40/30/20 等,"
            f"见 {', '.join(sorted(dyn_files))}"
        )
    logger.info(f"[模板测试] 完成,耗时 {time.perf_counter() - t0:.1f}s")
    return True


def _ocr_test(bot: GameBot) -> bool:
    """任务2:OCR 文字识别测试。"""
    t0 = time.perf_counter()
    img = bot.screenshot.capture()
    try:
        items = bot._ocr.recognize(img)
    except Exception as e:
        logger.error(f"[OCR测试] 识别异常: {e!r}")
        return False
    if not items:
        logger.warning("[OCR测试] 当前画面未识别到任何文字")
        return True
    logger.info(f"[OCR测试] 共识别 {len(items)} 条(按置信度降序):")
    items.sort(key=lambda it: it["confidence"], reverse=True)
    for it in items:
        logger.info(
            f"[OCR测试] '{it['text']}'  conf={it['confidence']:.2f}  @ {it['center']}"
        )
    logger.info(
        "[OCR测试] 提示: 文字判定阈值建议取 实测conf-0.1(留波动余量),"
        "低于 0.6 的识别结果不建议作为判定依据。"
    )
    logger.info(f"[OCR测试] 完成,耗时 {time.perf_counter() - t0:.1f}s")
    return True


class TestScript(GameScript):
    """测试脚本:模板识别 / OCR 测试。不进游戏、不需要启动清理。"""

    @property
    def name(self) -> str:
        return "测试"

    def get_tasks(self):
        return [
            Task("模板识别测试", callback=_template_test),
            Task("OCR 文字识别测试", callback=_ocr_test),
        ]

    def startup(self, bot: GameBot) -> bool:
        """仅校验窗口/截图可用。"""
        try:
            bot.screenshot.capture()
            logger.info("[测试] 窗口与截图正常,请把游戏切到待检测画面后执行测试任务")
            return True
        except Exception as e:
            logger.error(f"[测试] 截图失败(窗口不存在?): {e!r}")
            return False
