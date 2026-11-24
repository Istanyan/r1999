"""
不背单词（BBDC）脚本 — 按单词表批量搜索并收藏到单词本。

流程（2026-10-23 改为两轮，均为实测验证）：
    第1轮（collect_words 初次执行）：主界面下滑打开搜索页（输入框自动聚焦）
    → adb 注入单词 → OCR 校验第一个搜索结果 → 点该行星标 ☆
    → 弹层「将单词收藏至」→ 点「生词本」行星标 → 星标变橙色实心 ★ 即成功
    → 点搜索框内 × 清空 → 下一个词。未命中只记录、不重试。
    第2轮（引擎重试）：对失败词做两次尝试（重输原词、试原形），
    仍无精确命中 → 收藏第一条建议（temp/bbdc_debug_fallback_<词>.png 截图）。

运行方式
--------
    python main.py --game bbdc                        # 用项目根目录 words.txt
    python main.py --game bbdc --words 词表.txt       # 指定词表
    python main.py --game bbdc --limit 3              # 只处理前 3 个（试跑）
    python main.py --game bbdc --fresh                # 忽略历史进度重新收藏

词表格式（words.txt）
--------------------
一行一个单词；# 开头与空行忽略；自动清洗以下杂质：
    1. apple / 1、apple / - apple      → apple      （序号、项目符号）
    apple  n. 苹果                     → apple      （中文释义）

输入方式说明
------------
MuMu 对不背单词开启了按键映射（屏幕上有 Q/空格/E 映射提示），Windows 虚拟
按键会被拦截，pyautogui.typewrite 打不进字。因此输入/滑动走 MuMu 自带 adb：
    adb connect 127.0.0.1:16384 && adb shell input text/swipe ...
adb 缺失时退化 typewrite/窗口拖拽（按键映射下输入无效，仅保留兜底）。

窗口锚定说明
------------
必须锚定 MuMu 主窗口「MuMu安卓设备」（main.py 已在 --game bbdc 时自动切换）。
"MuMuNxDevice" 渲染窗口的 GetWindowRect 会与可见内容错位（用户拖动窗口后
实测偏移 250px），导致截图切掉搜索框、点击落进别的窗口。

界面坐标（均为实测：MuMu 窗口 1601x900，比例坐标随窗口缩放）
------------------------------------------------------------
    搜索框中心        (772,  65)   占位文字「输入要查询的英文或中文」
    搜索框内 × 清空   (1486, 64)   有输入内容时才出现；顶栏防护区，需 allow_danger
    结果行星标 x      (1487, 行y)  每行高约 71px，首行 y≈167
    弹层生词本星标    (1200, 生词本行y≈486)
    已收藏态          星标橙色实心（HSV 饱和度/明度高），空心为灰色

进度文件
--------
    temp/bbdc_done.txt    已成功收藏的单词（默认跳过，--fresh 忽略）
    temp/bbdc_failed.txt  本次运行失败的单词（可修正词表后重跑）
    temp/bbdc_debug_fallback_<词>.png  第2轮兜底收藏第一条建议时的现场截图
"""

import logging
import os
import re
import shutil
import subprocess
import time
from typing import List, Optional, Tuple

from core.game_bot import GameBot, GuardError
from core.task import Task
from core.game_script import GameScript
from core.action import StopRequested

logger = logging.getLogger(__name__)

# ── 界面坐标 ──
# 实测（2026-10-03）：MuMu 主窗口标题是 "MuMu安卓设备"（含标签栏 ~55px），
# 而 "MuMuNxDevice" 渲染窗口的矩形会与可见内容错位（用户拖动窗口后偏 250px），
# 截图/点击必须锚定主窗口。app 内容区坐标尽量走 OCR 定位，常量仅兜底。
SEARCH_BOX_XY = (0.50, 0.13)     # 搜索框中心兜底（优先 OCR 占位文字）
CLEAR_X_XY = (0.926, 0.13)       # × 清空兜底；首选 OCR「取消」按钮左移 68px
STAR_X_RATIO = 0.926             # 结果行收藏星标 x 兜底（y 取该行中心，OCR ☆ 优先）
SHEET_STAR_X_RATIO = 0.750       # 弹层生词本星标 x 兜底（OCR ☆ 优先）
RESULTS_TOP_RATIO = 0.16         # y 低于此比例的文字属于搜索框/顶栏，不算结果行
# 主界面下滑手势：安卓坐标系（adb input swipe 用），与窗口大小/位置/遮挡无关。
# MuMu 安卓分辨率 900x1600 竖屏、app 横屏显示 → 坐标空间 1600x900。
ADB_SWIPE_ANDROID = (800, 180, 800, 810, 1000)
# Windows 拖拽兜底（相对窗口比例）——要求起点终点都落在未被遮挡区域
SWIPE = ((0.50, 0.24), (0.50, 0.80), 1.0)

# ── 节奏（秒）──
TYPE_SETTLE = 1.2           # adb 输入后等搜索结果刷新
SUGGEST_TIMEOUT = 8.0       # 等搜索结果出现的超时
SUGGEST_POLL = 0.8          # 等搜索结果的轮询间隔
SHEET_TIMEOUT = 4.0         # 点行星标后等弹层出现的超时
AFTER_COLLECT_SLEEP = 1.0   # 点生词本后等收藏动画/浮层
AFTER_CLEAR_SLEEP = 0.8     # 点 × 清空后等输入框复位
PAGE_SETTLE = 1.5           # 下滑后等画面稳定

# ── adb（文本注入）──
ADB_CANDIDATES = (
    r"E:\MuMuPlayer\nx_device\12.0\shell\adb.exe",
    r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
    r"C:\Program Files (x86)\NemuVMM\adb.exe",
)
ADB_SERIALS = ("127.0.0.1:16384", "127.0.0.1:7555", "emulator-5554")

# ── 词表清洗 ──
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LEAD_JUNK_RE = re.compile(
    r"^\s*(?:\d+\s*[.、)\]]\s*|\d+\s+(?=[A-Za-z])|[^\w\s]{1,2}\s+)"
)
_WORD_RE = re.compile(r"^[A-Za-z](?:[A-Za-z'\-]*\s*[A-Za-z])*$")
_NORM_STRIP_RE = re.compile(r"[\s·•.,!?;:'\"()（）\[\]【】\-—_/\\]")
# 词性标注缩写：出现在行尾时剔除（"apple n. 苹果" → "apple"）
_POS_TOKENS = {
    "n", "v", "vt", "vi", "adj", "adv", "prep", "conj", "pron",
    "aux", "abbr", "art", "num", "int", "pl", "sb", "sth", "etc",
}
# OCR 行内紧跟目标词的词性标注模式（点已被 _norm 去掉，如 "vown誓言" → rest="n誓言"）
_POS_START_RE = re.compile(
    r"^(?:vt|vi|adj|adv|prep|conj|pron|abbr|aux|art|comb|phr|num|pl|int|n|v|sb|sth|det)(?![a-z])",
    re.IGNORECASE,
)


def _norm(s: str) -> str:
    """规范化文本用于比较：去空白/标点、转小写（OCR 空格/连字符差异归一）。"""
    return _NORM_STRIP_RE.sub("", s).lower()


def _row_matches(text: str, word: str) -> bool:
    """
    OCR 文本是否是目标词所在行（结果行总是"词+词性+释义"粘连）。
    短词（≤3 字母）拒绝纯字母续接（king↛kin、vowel↛vow），
    但接受词性标注/中文释义续接（"vown.誓言" → rest="n誓言" ✓）。
    """
    t, w = _norm(text), _norm(word)
    if not t or not w or not t.startswith(w):
        return False
    rest = t[len(w):]
    if not rest:
        return True
    if len(w) <= 3:
        first = rest[0]
        if not first.isascii() or not first.isalpha():
            return True  # 中文/标点开头 → 是释义
        return bool(_POS_START_RE.match(rest))
    return True


def clean_word_line(line: str) -> Optional[str]:
    """清洗词表单行：去序号/项目符号/中文/词性标注，返回纯单词或 None。"""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    # @ 分隔格式："序号@单词@释义"（1@enrapture@vt. 使狂喜）/ "单词@释义"
    if "@" in s:
        for part in [p.strip() for p in s.split("@")[:2]]:
            if part and _WORD_RE.match(part):
                return part
    s = _LEAD_JUNK_RE.sub("", s, count=1)
    # 去中文释义（无论前后顺序），再按 token 剔除尾部词性标注
    tokens = _CJK_RE.sub(" ", s).split()
    while len(tokens) > 1 and tokens[-1].rstrip(".").lower() in _POS_TOKENS:
        tokens.pop()
    s = " ".join(tokens)
    if not _WORD_RE.match(s):
        return None
    return s


def load_words(path: str) -> List[str]:
    """读词表 → 清洗 → 按大小写不敏感去重（保持先后顺序）。"""
    words: List[str] = []
    seen = set()
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            w = clean_word_line(line)
            if w is None:
                continue
            key = _norm(w)
            if key in seen:
                continue
            seen.add(key)
            words.append(w)
    return words


class _Progress:
    """断点续跑：done 追加写入，failed 每次运行重写。键为规范化单词。"""

    def __init__(self, fresh: bool = False):
        proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        temp_dir = os.path.join(proj_root, "temp")
        os.makedirs(temp_dir, exist_ok=True)
        self.done_path = os.path.join(temp_dir, "bbdc_done.txt")
        self.fail_path = os.path.join(temp_dir, "bbdc_failed.txt")
        self.done: set = set()
        if not fresh and os.path.isfile(self.done_path):
            with open(self.done_path, "r", encoding="utf-8") as f:
                self.done = {_norm(w) for w in f.read().splitlines() if w.strip()}
        # 失败清单只反映本次运行
        self.failed: List[str] = []

    def mark_done(self, word: str):
        self.done.add(_norm(word))
        with open(self.done_path, "a", encoding="utf-8") as f:
            f.write(word + "\n")

    def mark_failed(self, word: str):
        self.failed.append(word)

    def write_failed(self):
        with open(self.fail_path, "w", encoding="utf-8") as f:
            f.writelines(w + "\n" for w in self.failed)


class _Adb:
    """MuMu adb 封装：定位 adb.exe → connect → input text / keyevent。"""

    def __init__(self):
        self.exe = self._find_exe()
        self.serial: Optional[str] = None
        if self.exe is None:
            logger.warning("未找到 adb（MuMu 自带 adb 不在默认路径且 PATH 里没有），"
                           "文本输入将退化 typewrite（MuMu 按键映射下大概率无效）")
            return
        for serial in ADB_SERIALS:
            out = self._run(["connect", serial])
            if out is not None and "connected" in out and self._is_device(serial):
                self.serial = serial
                logger.info(f"adb 已连接 {serial}（{self.exe}）")
                return
        logger.warning("adb 存在但无法连接 MuMu（试过 " + ", ".join(ADB_SERIALS) + "），"
                       "文本输入将退化 typewrite")

    @staticmethod
    def _find_exe() -> Optional[str]:
        which = shutil.which("adb")
        if which:
            return which
        for p in ADB_CANDIDATES:
            if os.path.isfile(p):
                return p
        return None

    def _run(self, args: List[str], timeout: float = 6.0) -> Optional[str]:
        try:
            cp = subprocess.run(
                [self.exe] + args, capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace",
            )
            return (cp.stdout or "") + (cp.stderr or "")
        except Exception as e:
            logger.debug(f"adb {' '.join(args)} 失败: {e!r}")
            return None

    def _is_device(self, serial: str) -> bool:
        out = self._run(["devices"])
        return bool(out) and any(
            line.split()[0] == serial and line.split()[-1] == "device"
            for line in (out or "").splitlines() if line.strip()
        )

    @property
    def ok(self) -> bool:
        return self.exe is not None and self.serial is not None

    def type_text(self, text: str) -> bool:
        """input text 注入英文（空格 → %s，撇号转义）。"""
        if not self.ok:
            return False
        dev = text.replace(" ", "%s").replace("'", "\\'")
        out = self._run(["-s", self.serial, "shell", f"input text {dev}"])
        return out is not None

    def key_del(self, count: int):
        """连按 DELETE 键 count 次（清空输入的兜底手段）。"""
        for _ in range(count):
            if self._run(["-s", self.serial, "shell", "input keyevent 67"]) is None:
                return

    def swipe_android(self, x1: int, y1: int, x2: int, y2: int, dur_ms: int = 800) -> bool:
        """安卓坐标系滑动（input swipe），不受 Windows 窗口位置/遮挡影响。"""
        if not self.ok:
            return False
        out = self._run(["-s", self.serial, "shell",
                         f"input swipe {x1} {y1} {x2} {y2} {dur_ms}"])
        return out is not None


def _star_filled(img, x: int, y: int, r: int = 10) -> bool:
    """星标小patch颜色饱和度高 → 已是收藏态橙色实心★（空心为灰色低饱和）。"""
    import cv2
    h, w = img.shape[:2]
    x0, x1 = max(0, x - r), min(w, x + r)
    y0, y1 = max(0, y - r), min(h, y + r)
    patch = img[y0:y1, x0:x1]
    if patch.size == 0:
        return False
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    _, sat, val = cv2.mean(hsv)[:3]
    return sat > 80 and val > 90


class BBDCScript(GameScript):
    """不背单词批量收藏脚本。"""

    name = "bbdc"

    def __init__(self, words_file: Optional[str] = None,
                 limit: Optional[int] = None, fresh: bool = False):
        """
        参数
        ----
        words_file : 词表路径，默认项目根目录 words.txt
        limit      : 只处理前 N 个单词（试跑用）
        fresh      : 忽略 temp/bbdc_done.txt 历史进度
        """
        proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.words_file = words_file or os.path.join(proj_root, "words.txt")
        self.limit = limit
        self.fresh = fresh
        # 与 GameBot(game_name="bbdc") 的模板目录规则保持一致
        self.template_dir = os.path.join(proj_root, "templates", self.name)
        self._input_pos: Optional[Tuple[int, int]] = None  # 搜索框像素坐标缓存
        self._adb = _Adb()
        # collect_words 执行轮次：第1轮每词只搜一次、失败只记录；
        # 引擎重试（第2轮）时再做两次尝试，仍无则收藏第一条建议
        self._collect_round = 0

    # ── 几何 / 模板工具 ──────────────────────────

    @staticmethod
    def _size(bot: GameBot) -> Tuple[int, int]:
        m = bot.screenshot.monitor
        return m["width"], m["height"]

    def _pt(self, bot: GameBot, rx: float, ry: float) -> Tuple[int, int]:
        w, h = self._size(bot)
        return (int(w * rx), int(h * ry))

    def _tpl(self, name: str) -> Optional[str]:
        """模板存在则返回完整路径，否则 None（模板均为可选）。"""
        path = os.path.join(self.template_dir, name)
        return path if os.path.isfile(path) else None

    # ── 界面导航 ─────────────────────────────────

    def _locate_search_box(self, bot: GameBot, use_cache: bool = True) -> Optional[Tuple[int, int]]:
        """
        定位搜索框中心：模板 > OCR 占位文字（「输入要查询的英文或中文」）> None。
        结果缓存（窗口相对坐标不受窗口拖动影响，仅窗口缩放才失效）。
        """
        if use_cache and self._input_pos:
            return self._input_pos
        if self._tpl("search_box.png"):
            r = bot.find("search_box.png")
            if r is not None:
                self._input_pos = (r[0], r[1])
                return self._input_pos
        w, h = self._size(bot)
        try:
            results = bot.recognize_text()
        except Exception as e:
            logger.debug(f"OCR 定位搜索框失败: {e!r}")
            return None
        for r in results:
            cx, cy = r["center"]
            if cy > h * 0.35:
                continue
            if any(kw in r["text"] for kw in ("输入", "查询单词", "搜索")):
                self._input_pos = (cx, cy)
                logger.info(f"OCR 定位到搜索框占位文字 '{r['text']}' @ ({cx}, {cy})")
                return self._input_pos
        return None

    def _open_search(self, bot: GameBot) -> bool:
        """
        进入搜索页：先检测（可能已在搜索页）→ adb 安卓坐标下滑 → Windows 拖拽兜底。
        adb 滑动不受窗口位置/遮挡影响，是首选；Windows 拖拽要求起止点都可见。
        """
        if self._locate_search_box(bot, use_cache=False) is not None:
            logger.info("已在搜索页，无需下滑")
            return True
        w, h = self._size(bot)
        for attempt in range(3):
            logger.info(f"主界面下滑打开搜索（第 {attempt + 1} 次）...")
            if self._adb.ok:
                x1, y1, x2, y2, dur = ADB_SWIPE_ANDROID
                self._adb.swipe_android(x1, y1, x2, y2, dur)
            else:
                (x1, y1), (x2, y2), dur = SWIPE
                bot.action.drag(int(w * x1), int(h * y1), int(w * x2), int(h * y2),
                                duration=dur)
            bot.sleep(PAGE_SETTLE)
            if self._locate_search_box(bot, use_cache=False) is not None:
                return True
        logger.warning("下滑后未检测到搜索框（可能不在主界面），继续尝试")
        return True

    def _click_search_box(self, bot: GameBot):
        """点击搜索框确保输入焦点（顶栏防护区 → allow_danger）。"""
        pos = self._locate_search_box(bot) or self._pt(bot, *SEARCH_BOX_XY)
        logger.info(f"点击搜索框 @ {pos} (allow_danger)")
        bot.action.click(*pos, allow_danger=True)
        bot.sleep(0.3)

    def _top_texts(self, bot: GameBot) -> List[str]:
        """OCR 顶部区域（搜索框一带）的文本。"""
        w, h = self._size(bot)
        img = bot.screenshot.capture()
        try:
            results = bot._ocr.recognize(img)
        except Exception as e:
            logger.debug(f"顶部 OCR 失败: {e!r}")
            return []
        return [r["text"] for r in results if r["center"][1] < h * RESULTS_TOP_RATIO]

    def _clear_input(self, bot: GameBot, word: str):
        """
        清空搜索框：首选 OCR「取消」按钮左移 68px 定位 ×（与窗口尺寸无关，
        实测校准）；点 × 无效则 adb DEL 兜底，最后核验。
        """
        qx = None
        try:
            w, h = self._size(bot)
            for r in bot.recognize_text():
                if r["text"].strip() == "取消" and r["center"][1] < h * 0.3:
                    qx = r["center"]
                    break
        except Exception:
            pass
        if qx:
            x, y = qx[0] - 68, qx[1]
            logger.info(f"点击 × 清空搜索框 @ ({x}, {y})（依据 OCR『取消』{qx}）")
            bot.action.click(x, y, allow_danger=True)
            bot.sleep(AFTER_CLEAR_SLEEP)
        elif self._tpl("clear_x.png") and bot.click_if_found("clear_x.png"):
            bot.sleep(AFTER_CLEAR_SLEEP)
        else:
            x, y = self._pt(bot, *CLEAR_X_XY)
            logger.info(f"点击 × 清空搜索框 @ ({x}, {y}) (allow_danger, 兜底坐标)")
            bot.action.click(x, y, allow_danger=True)
            bot.sleep(AFTER_CLEAR_SLEEP)
        # 核验：顶部还有目标词 → adb DEL 兜底
        joined = "".join(self._top_texts(bot))
        if _norm(word) in _norm(joined):
            logger.info("× 清空未生效，adb DEL 键兜底")
            if self._adb.ok:
                self._adb.key_del(len(word) + 3)
                bot.sleep(AFTER_CLEAR_SLEEP)
            joined = "".join(self._top_texts(bot))
            if _norm(word) in _norm(joined):
                logger.warning(f"清空后输入框仍疑似残留 '{word}'，继续（下个词输入会叠加，请留意）")

    # ── 搜索结果校验与收藏 ───────────────────────

    def _find_first_result(self, bot: GameBot, word: str) -> Optional[dict]:
        """
        轮询 OCR 等搜索结果，返回目标词所在行（取最靠上的一条，即第一个结果）。
        搜索框里已输入的词也会被 OCR 读到，按 y 过滤掉顶部区域。
        """
        _, h = self._size(bot)
        top_limit = int(h * RESULTS_TOP_RATIO)
        deadline = time.time() + SUGGEST_TIMEOUT
        while time.time() < deadline:
            results = bot.recognize_text()
            cands = [
                r for r in results
                if r["center"][1] > top_limit and _row_matches(r["text"], word)
            ]
            if cands:
                return min(cands, key=lambda r: r["center"][1])
            bot.sleep(SUGGEST_POLL)
        return None

    def _debug_shot(self, bot: GameBot, tag: str):
        """失败现场截图（temp/bbdc_debug_<tag>.png），便于事后排查。"""
        try:
            proj_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            path = os.path.join(proj_root, "temp", f"bbdc_debug_{tag}.png")
            bot.screenshot.save(path)
            logger.info(f"  调试截图: {path}")
        except Exception:
            pass

    def _collect_row(self, bot: GameBot, row_y: int) -> bool:
        """
        收藏 row_y 所在行：点行星标 ☆ → 弹层「将单词收藏至」→ 点「生词本」星标。
        已收藏（橙色实心★）则直接返回成功，避免点成取消。

        注意：弹层里「将单词收藏至」是标题（y≈384），可点的是「生词本」行
        （y≈486）右侧的星标——按行文本定位，绝不能用标题行的 y。
        """
        w, h = self._size(bot)
        img = bot.screenshot.capture()
        star_x = int(w * STAR_X_RATIO)
        if _star_filled(img, star_x, row_y):
            logger.info(f"  ☆ ({star_x}, {row_y}) 已是收藏态，跳过点击")
            return True

        # 星标位置：OCR ☆（取离目标行最近的）> 固定 x
        click_x = star_x
        try:
            stars = [r for r in bot.recognize_text()
                     if r["text"].strip() in ("☆", "★")]
            if stars:
                s = min(stars, key=lambda r: abs(r["center"][1] - row_y))
                click_x = s["center"][0]
        except Exception:
            pass
        logger.info(f"点击结果行星标 @ ({click_x}, {row_y})")
        bot.action.click(click_x, row_y)
        bot.sleep(1.2)

        for attempt in (1, 2):
            # 等弹层出现并定位「生词本」行（标题出现但行没渲染完时继续等）
            sheet_row = None
            deadline = time.time() + SHEET_TIMEOUT
            while time.time() < deadline and sheet_row is None:
                results = bot.recognize_text()
                sheet_row = next(
                    (r for r in results
                     if "生词本" in r["text"] and h * 0.35 < r["center"][1] < h * 0.75),
                    None,
                )
                if sheet_row is None:
                    # 弹层没出但已出现「已收藏至」浮窗 → 自动收藏模式，直接成功
                    if any("已收藏至" in r["text"] for r in results):
                        logger.info("  已收藏至（自动收藏模式）")
                        bot.sleep(AFTER_COLLECT_SLEEP)
                        return True
                    bot.sleep(0.5)
            if sheet_row is not None:
                # 星标 x：OCR ☆（取离生词本行最近的）> 固定比例兜底
                sx = int(w * SHEET_STAR_X_RATIO)
                sy = sheet_row["center"][1]
                try:
                    stars = [r for r in results if r["text"].strip() in ("☆", "★")]
                    if stars:
                        s = min(stars, key=lambda r: abs(r["center"][1] - sy))
                        if abs(s["center"][1] - sy) < 30:
                            sx = s["center"][0]
                except Exception:
                    pass
                logger.info(f"点击弹层「生词本」星标 @ ({sx}, {sy})")
                bot.action.click(sx, sy)
                bot.sleep(AFTER_COLLECT_SLEEP)
            else:
                logger.warning("  未检测到收藏弹层的「生词本」行")
                self._debug_shot(bot, f"sheet_{attempt}")

            # 校验：星标应变橙色实心
            img = bot.screenshot.capture()
            if _star_filled(img, star_x, row_y):
                return True
            logger.warning(f"  收藏后星标仍非实心态（第 {attempt} 次尝试）")
            if attempt == 1:
                bot.action.click(click_x, row_y)  # 重开弹层再试
                bot.sleep(1.2)
        self._debug_shot(bot, "collect_fail")
        return False

    def _type_into_box(self, bot: GameBot, word: str):
        """聚焦搜索框并输入单词（adb 优先）。"""
        self._click_search_box(bot)
        if self._adb.ok:
            if not self._adb.type_text(word):
                logger.warning(f"adb input text 失败，退化 typewrite: '{word}'")
                bot.action.typewrite(word, interval=0.03)
        else:
            bot.action.typewrite(word, interval=0.03)
        bot.sleep(TYPE_SETTLE)

    @staticmethod
    def _singular(word: str) -> Optional[str]:
        """猜测名词原形（schemes→scheme / levies→levy / wolves→wolf）。"""
        low = word.lower()
        if len(low) > 4 and low.endswith("ies"):
            return word[:-3] + "y"
        if len(low) > 3 and low.endswith("es") and low.endswith(("ses", "xes", "zes", "ches", "shes")):
            return word[:-2]
        if len(low) > 3 and low.endswith("s") and not low.endswith("ss"):
            return word[:-1]
        return None

    def _guard_search_page(self, bot: GameBot):
        """画面守卫：搜索框必须可见，否则 App 不在前台/被遮挡/不在搜索页，
        此时点击会落到其他窗口上（2026-10-03 实测事故：误点用户正在看的视频），
        立即中止整个任务而不是盲点下去。"""
        if self._locate_search_box(bot, use_cache=False) is None:
            self._debug_shot(bot, "guard_searchpage")
            raise GuardError(
                "当前画面找不到不背单词搜索框（App 不在前台、窗口被遮挡或不在搜索页）。"
                "批量收藏已中止——请让 MuMu 完整可见并停在搜索页/主界面后重跑。"
            )

    def _do_word_once(self, bot: GameBot, word: str) -> bool:
        """
        第1轮处理：守卫 → 输入 → 只搜一次。
        精确命中则收藏；未命中只返回 False（不重输、不试原形、不截图），
        由 collect_words 记录，留待引擎重试轮处理。
        """
        self._guard_search_page(bot)
        self._type_into_box(bot, word)
        hit = self._find_first_result(bot, word)
        if hit is None:
            logger.info(f"  未找到精确结果，记录待重试（本轮不重试）")
            return False
        cx, cy = hit["center"]
        logger.info(
            f"  ✓ 首个结果 '{hit['text']}' (置信度 {hit['confidence']:.2f}) "
            f"@ ({cx}, {cy})"
        )
        return self._collect_row(bot, cy)

    def _first_suggestion_row(self, bot: GameBot) -> Optional[int]:
        """兜底：返回当前搜索结果中第一条建议行的 y（无精确匹配时收藏它）。"""
        _, h = self._size(bot)
        top_limit = int(h * RESULTS_TOP_RATIO)
        try:
            results = bot.recognize_text()
        except Exception as e:
            logger.debug(f"兜底读取建议行失败: {e!r}")
            return None
        rows = [r for r in results if r["center"][1] > top_limit]
        if not rows:
            return None
        return min(rows, key=lambda r: r["center"][1])["center"][1]

    def _retry_word(self, bot: GameBot, word: str) -> bool:
        """
        引擎重试轮处理：
        尝试1 清空残留后重输原词 → 尝试2 试单数/原形；
        两次都无精确命中 → 收藏第一条建议（截图一张），按成功处理。
        """
        self._guard_search_page(bot)

        # 尝试1：清空可能残留的词后重输原词
        if _norm(word) in _norm("".join(self._top_texts(bot))):
            self._clear_input(bot, word)
        self._type_into_box(bot, word)
        hit = self._find_first_result(bot, word)

        # 尝试2：单数/原形
        if hit is None:
            sing = self._singular(word)
            if sing:
                logger.info(f"  未找到 '{word}'，尝试原形 '{sing}'")
                if _norm(sing) not in _norm("".join(self._top_texts(bot))):
                    self._clear_input(bot, word)
                self._type_into_box(bot, sing)
                hit = self._find_first_result(bot, sing)
                if hit is not None:
                    logger.info(f"  ✓ 原形命中: '{hit['text'][:30]}'")

        if hit is not None:
            cx, cy = hit["center"]
            logger.info(
                f"  ✓ 结果 '{hit['text']}' (置信度 {hit['confidence']:.2f}) "
                f"@ ({cx}, {cy})"
            )
            return self._collect_row(bot, cy)

        # 兜底：两次尝试仍无精确结果 → 收藏第一条建议，先存调试截图
        logger.warning(f"  '{word}' 两次尝试仍无精确结果，收藏第一条建议")
        self._debug_shot(bot, f"fallback_{word}")
        first_y = self._first_suggestion_row(bot)
        if first_y is None:
            logger.warning(f"  无任何建议结果可收藏")
            return False
        return self._collect_row(bot, first_y)

    # ── GameScript 接口 ──────────────────────────

    def get_tasks(self) -> List[Task]:
        return [
            Task("批量收藏单词", callback=self.collect_words),
        ]

    def startup(self, bot: GameBot) -> bool:
        """校验词表可读 → 主界面下滑进入搜索页。"""
        self._collect_round = 0  # GUI 单例：每次新运行重置轮次计数
        if not os.path.isfile(self.words_file):
            logger.error(f"词表不存在: {self.words_file}")
            logger.error("请把单词一行一个写进 words.txt，或用 --words 指定路径")
            return False
        words = load_words(self.words_file)
        if not words:
            logger.error(f"词表中没有有效单词: {self.words_file}")
            return False
        w, h = self._size(bot)
        logger.info(f"词表加载 {len(words)} 词，窗口 {w}x{h}，"
                    f"adb={'已连接 ' + str(self._adb.serial) if self._adb.ok else '不可用'}，"
                    f"模板目录 templates/bbdc/")
        have_tpl = [n for n in ("search_box.png", "clear_x.png", "collect_star.png")
                    if self._tpl(n)]
        logger.info(f"可用模板: {have_tpl or '无（纯坐标+OCR 模式）'}")
        return self._open_search(bot)

    # ── 主流程 ───────────────────────────────────

    def collect_words(self, bot: GameBot) -> bool:
        self._collect_round += 1
        round_no = self._collect_round
        words = load_words(self.words_file)
        if self.limit:
            words = words[:self.limit]
        progress = _Progress(fresh=self.fresh)
        todo = [w for w in words if _norm(w) not in progress.done]
        skipped = len(words) - len(todo)
        if round_no == 1:
            logger.info(
                f"[第1轮] 共 {len(words)} 词，跳过已完成 {skipped}，"
                f"本次待处理 {len(todo)}（未命中只记录、不重试）"
            )
        else:
            logger.info(
                f"[第{round_no}轮·引擎重试] 待处理失败词 {len(todo)}："
                f"每词两次尝试，仍无则收藏第一条建议"
            )

        collected = 0
        for i, word in enumerate(todo, 1):
            logger.info(f"[{i}/{len(todo)}] {word}")
            try:
                if round_no == 1:
                    ok = self._do_word_once(bot, word)
                else:
                    ok = self._retry_word(bot, word)
            except StopRequested:
                logger.info("检测到停止请求 — 中断收藏流程")
                raise
            except Exception as e:
                logger.error(f"  ✗ '{word}' 异常: {e!r}")
                ok = False
            # 无论成败都清空搜索框，保证下一个词从头输入
            try:
                self._clear_input(bot, word)
            except StopRequested:
                raise
            if ok:
                progress.mark_done(word)
                collected += 1
            else:
                progress.mark_failed(word)

        failed = len(progress.failed)
        logger.info("=" * 40)
        logger.info(
            f"第{round_no}轮结束: 成功 {collected}, 失败 {failed}, "
            f"跳过(已完成) {skipped} / 总 {len(words)}"
        )
        if failed:
            progress.write_failed()
            logger.info(f"失败单词已写入 {progress.fail_path}，修正词表后可重跑")
        elif os.path.isfile(progress.fail_path):
            # 全部成功：清空历史失败清单，避免残留
            with open(progress.fail_path, "w", encoding="utf-8") as f:
                f.write("")
        logger.info("=" * 40)
        return failed == 0 and collected + skipped > 0
