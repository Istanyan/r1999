"""
三国杀脚本 — 征战天下模块。

由原 games/sanguosha.py 拆分而来：
ZhengzhanMixin 承载原 SanguoshaScript 的征战天下相关方法
（_zz_* 共享组件、zhengzhan_task1 三合一主流程、zhengzhan_tili_clear 体力清）。

演武（YanwuMixin）与急报、体力清三合一穿插：
先进征战天下打一轮演武 → 急报递归每场前补一轮演武 →
急报清完专注补演武 → 体力清 → 再补演武 → 达标收尾领奖。
"""

import os
import time
from typing import Optional

from core.game_bot import GameBot

from .constants import (
    logger,
    _warn,
    ZZ_ENTRY_THRESHOLD,
    ZZ_MAIN_ENTRY_X_RATIO,
    ZZ_MAIN_ENTRY_Y_RATIO,
    ZZ_MAIN_ENTRY_ATTEMPTS,
    CAKE_MAIN_MARK,
)


class ZZLineupIncomplete(Exception):
    """急报选将未满 / 开战连续未启动：禁止开战，异常退出本次急报递归。"""


class ZhengzhanMixin:
    """征战天下：急报任务 + 体力清。"""

    def _zz_is_on_page(self, bot: GameBot) -> bool:
        """检测是否已在征战天下界面。

        通道: 演武/任务 模板(0.85) → 急报模板(0.70,原 0.50 过松)
        → OCR 兜底找"征战天下/演武/急报"任一文字（跨皮肤,run16 验证
        OCR conf 0.94+）。模板先行保证速度,OCR 兜底保证皮肤变化不误判。
        """
        if (bot.is_present("演武.png", threshold=ZZ_ENTRY_THRESHOLD) or
                bot.is_present("任务.png", threshold=0.85) or
                bot.is_present("急报.png", threshold=0.70)):
            return True
        return self._page_by_ocr(bot, ("征战天下", "演武", "急报"))

    def _page_by_ocr(self, bot: GameBot, texts, min_y: int = 0,
                     conf: float = 0.75) -> bool:
        """OCR 页面判定通用方法:截屏→OCR→命中任一目标文字即 True。

        结果缓存 10s（同页面连续调用不重复识别）;
        OCR 异常按未命中处理,不影响调用方流程。
        """
        import time as _t
        cache_key = f"ocr_{'_'.join(texts)}"
        cache = getattr(self, "_page_ocr_cache", None)
        if cache is None:
            cache = self._page_ocr_cache = {}
        cached = cache.get(cache_key)
        now = _t.time()
        if cached and now - cached["ts"] < 10.0:
            return cached["ok"]
        try:
            img = bot.screenshot.capture()
            if min_y:
                img = img[min_y:, :]
            items = bot._ocr.recognize(img)
        except Exception as e:
            logger.warning(f"OCR 页面判定异常（按未命中处理）: {e}")
            cache[cache_key] = {"ts": now, "ok": False}
            return False
        ok = any(any(t in it["text"] for t in texts) and it["confidence"] >= conf
                 for it in items)
        cache[cache_key] = {"ts": now, "ok": ok}
        return ok

    def _zz_on_entry_page(self, bot: GameBot) -> bool:
        """征战天下主页面的强判定（同一帧看演武/任务/急报三个入口）。

        只认页面内元素，不用 OCR 的“征战天下”：主界面右侧横幅本身就写着
        这四个字，OCR 会把主界面误判成征战页面（2026-11-17 发现）。
        """
        img = bot._capture()
        for tpl, th in (("演武.png", ZZ_ENTRY_THRESHOLD),
                        ("任务.png", 0.85),
                        ("急报.png", 0.70)):
            if bot._matcher.match_one(img, bot._template_path(tpl), th):
                return True
        return False

    def _zz_click_main_entry(self, bot: GameBot) -> bool:
        """从主界面用相对坐标点征战天下入口，点击后强判定是否落地。

        主界面背景图/活动横幅经常换，征战天下.png 模板已失效
        （2026-11-17 用户新主界面实测：彩色/灰度均无命中）。
        """
        # 主界面 OCR 兜底有 10s 缓存；点击失败重试时先失效，
        # 避免刚离开主界面仍拿旧 True 再点一次坐标（会点到征战页内容）。
        try:
            self._main_ocr_cache["ts"] = 0.0
        except Exception:
            pass
        if not self._on_main_screen(bot):
            return False
        img = bot._capture()
        h, w = img.shape[:2]
        x = int(w * ZZ_MAIN_ENTRY_X_RATIO)
        y = int(h * ZZ_MAIN_ENTRY_Y_RATIO)
        logger.info(
            f"[征战天下入口] 相对坐标点击 ({x}, {y}) "
            f"← 主界面背景变化，模板不可用"
        )
        bot.click(x, y, allow_danger=True)
        bot.sleep(1.2)
        for _ in range(8):
            if self._zz_on_entry_page(bot):
                return True
            bot.sleep(0.5)
        return False

    def _zz_enter_page(self, bot: GameBot) -> bool:
        """确保在征战天下界面。已在则跳过，否则从主界面坐标进入。"""
        if self._zz_on_entry_page(bot):
            return True
        for attempt in range(ZZ_MAIN_ENTRY_ATTEMPTS):
            # 上一轮清理/fanhui 可能已经回到征战页，每轮先复查再点
            if self._zz_on_entry_page(bot):
                return True
            if self._zz_click_main_entry(bot):
                return True
            logger.warning(
                f"[征战天下入口] 第 {attempt + 1}/{ZZ_MAIN_ENTRY_ATTEMPTS} "
                f"次坐标点击未落地"
            )
            if not self._on_main_screen(bot):
                # 可能点进了子页面/弹窗：退回主界面，下一轮再点
                self.dismiss_popups(bot, max_rounds=3)
                bot.click_if_found("fanhui.png", threshold=0.85)
                bot.sleep(0.8)
        _warn("坐标点击后仍未进入征战天下界面")
        return False

    @staticmethod
    def _zz_click_until_gone(bot: GameBot, template: str,
                              threshold: float = 0.70,
                              interval: float = 0.5):
        """通用：循环点击模板，直到屏幕上检测不到为止。"""
        while bot.click_if_found(template, threshold=threshold):
            bot.sleep(interval)

    # ── 对局状态保护 ──────────────────────────────
    # 对局进行中的界面标志：任一可见 = 还在战斗里。
    # 战斗内的“点击空白处关闭”提示层也会命中 空白处.png，
    # 不能用它单独判定战斗结束（2026-11-13 用户实测）。
    _ZZ_BATTLE_MARKERS = ("加号.png", "托管.png")

    # 急报选将：镜框类型 → 期望武将。单镜框固定张春华。
    _ZZ_LINEUP_HEROES = {
        "三镜框": ("袁绍", "荀彧", "华佗"),
        "双镜框": ("袁绍", "荀彧"),
        "单镜框": ("张春华",),
    }
    # 对局已结束的界面信号：战败后的开战按钮、胜利结算。
    # 结算层可能仍透出托管按钮，所以“已结束”优先于“还在战斗”判定。
    _ZZ_END_MARKERS = ("开战.png", "胜利.png")

    def _zz_in_battle(self, bot: GameBot) -> bool:
        """当前是否仍在对局中（有战斗标志且无结束信号）。"""
        if self._zz_battle_settled(bot):
            return False
        return any(bot.is_present(t, threshold=0.85)
                   for t in self._ZZ_BATTLE_MARKERS)

    def _zz_battle_settled(self, bot: GameBot) -> bool:
        """当前是否已出现对局结束信号（开战/胜利）。"""
        return any(bot.is_present(t, threshold=0.85)
                   for t in self._ZZ_END_MARKERS)

    def _zz_leave_battle_ui(self, bot: GameBot, max_rounds: int = 6) -> bool:
        """从急报的布阵/结算界面退回征战天下。

        只在对局确认结束后调用：对局进行中直接返回 False，绝不点返回，
        否则会把还没打完的对局退掉（2026-11-13 用户实测）。
        返回 True = 已在征战天下或主界面。
        """
        for _ in range(max_rounds):
            if self._zz_is_on_page(bot) or self._on_main_screen(bot):
                return True
            if self._zz_in_battle(bot):
                _warn("对局仍在进行，跳过返回/退出（避免退掉未结束的对局）")
                return False
            clicked = False
            for tpl in ("返回1.png", "fanhui.png", "退出.png"):
                if bot.click_if_found(tpl, threshold=0.85):
                    clicked = True
                    break
            if not clicked and bot.click_if_found("空白处.png", threshold=0.70):
                clicked = True
            bot.sleep(1.2 if clicked else 1.0)
        return self._zz_is_on_page(bot) or self._on_main_screen(bot)

    def _zz_wait_battle_end(self, bot: GameBot, max_wait: float = 360.0) -> bool:
        """在任务收尾前等对局自然结束（最多 max_wait 秒），不点任何退出。

        返回 True = 已不在对局中；False = 超时仍在打。
        """
        t0 = time.time()
        while time.time() - t0 < max_wait:
            if not self._zz_in_battle(bot):
                return True
            logger.info(
                f"对局仍在进行，等待结束（{int(time.time() - t0)}s/{int(max_wait)}s）"
            )
            bot.sleep(15.0)
        return not self._zz_in_battle(bot)

    def _zz_detect_painting(self, bot: GameBot) -> Optional[str]:
        """
        检测当前画面的画画类型。按最高置信度选。
        返回 '三镜框' / '双镜框' / '单镜框'，都未检出返回 None。
        """
        best = None
        best_conf = 0
        for kind in ("三镜框", "双镜框", "单镜框"):
            region = bot.find_region(f"{kind}.png", threshold=0.75)
            if region is not None and region[4] > best_conf:
                best = kind
                best_conf = region[4]
        if best:
            logger.debug(f"[画画] 检测到 {best}，置信度 {best_conf:.2f}")
        else:
            logger.debug("[画画] 未检测到任何画画类型")
        return best

    def _zz_select_characters(self, bot: GameBot, painting: str) -> int:
        """根据画画类型依次点击武将。返回成功点选的数量（选将不完全时
        开战会弹"敌人过于强大"确认框，调用方必须禁止开战）。"""
        mapping = self._ZZ_LINEUP_HEROES
        names = mapping.get(painting, ())
        placed = 0
        for name in names:
            hit = False
            # 页面刚切过来时武将卡可能还没渲染完：每个武将最多试 3 次，
            # 避免把“加载慢”误判成“没这张卡”而直接走异常出口。
            for _ in range(3):
                if bot.click_if_found(f"{name}.png", threshold=0.75):
                    hit = True
                    break
                bot.sleep(0.5)
            if hit:
                placed += 1
            bot.sleep(0.5)
        if placed < len(names):
            try:
                shot = f"temp/lineup_fail_{time.strftime('%H%M%S')}.png"
                bot.screenshot.capture_pil().save(shot)
                _warn(f"选将失败现场已存图: {shot}")
            except Exception:
                pass
            _warn(f"选将不完全: {painting} 应选 {len(names)} 名，仅点中 {placed} 名")
        return placed

    def _zz_cancel_weak_lineup(self, bot: GameBot) -> bool:
        """
        处理开战后的"敌人过于强大，确定不上阵副将?"确认框。

        镜框选满不会有此框，出现即说明本场阵容不完整（选将模板未命中，
        或游戏保留了上一场的阵容）。残阵点确定 → 战败 → 再战仍残阵 →
        无限拖长（2026-08-30 用户实测），因此一律点 取消 退出本场，
        由上层 fanhui 回退重新递归、重新完整选将。

        返回 True 表示检测到该框并已点取消。
        """
        if bot.is_present("quxiao.png", threshold=0.85):
            bot.click_if_found("quxiao.png", threshold=0.85)
            bot.sleep(1.0)
            _warn("检测到'敌人过于强大'确认框（=阵容未选满），已点取消退出本场")
            return True
        return False

    def _zz_leave_lineup(self, bot: GameBot, back: int = 3) -> bool:
        """选将未满/开战未启动时退出布阵页：只点取消和 fanhui，绝不点开战。"""
        if bot.is_present("quxiao.png", threshold=0.85):
            bot.click_if_found("quxiao.png", threshold=0.85)
            bot.sleep(0.8)
        ok = False
        for _ in range(back):
            if bot.click_if_found("fanhui.png", threshold=0.85):
                ok = True
                bot.sleep(0.8)
            else:
                break
        return ok

    def _zz_battle_flow(self, bot: GameBot,
                         poll_interval: float = 10.0,
                         blank_threshold: float = 0.70,
                         max_battles: int = 3) -> str:
        """完整战斗流程：开战 → 2s 残阵框检测 → 等加载 → 跳过 → 加号 → 托管 → 轮询等空白处。

        开战 2s 后检测"敌人过于强大"确认框（选满镜框不会有此框，出现即残阵）：
        一律点取消并返回 "weak"，由上层重进重选；绝不点确定强行开打。

        返回 "ok"（战斗完成/次数用尽）、"timeout"（轮询超时对局仍未结束，
        上层必须等待，绝不能当成已结束）、"weak"（残阵已取消）或
        "no_start"（开战连续 3 次仍未启动，异常出口，上层必须退出本场）。
        """
        bot.click_if_found("开战.png", threshold=0.85)
        # 开战 2s 首检 + 2s 复检：确认框一般即时弹出，复检防慢加载漏检
        for _ in range(2):
            bot.sleep(2.0)
            if self._zz_cancel_weak_lineup(bot):
                return "weak"

        # [2026-10-28 用户口径] 战斗开始检测：确定后战斗未必真正开始
        # （run25 卡死根因：右将未上阵→确认后战斗未启动→盲等"空白处"10 分钟）。
        # - 开战界面仍在 → 重试点开战（可能再次弹残阵确认框，一律取消返回 weak）
        # - 加号 出现 → 战斗正常进行中，跳出检测
        # - 都不在 → 加载过渡，等待
        start_retries = 0
        for _ in range(10):  # 最多约 40s
            if bot.is_present("加号.png", threshold=0.85):
                logger.info("战斗开始检测: 加号出现，战斗进行中")
                break
            if bot.is_present("开战.png", threshold=0.85):
                start_retries += 1
                if start_retries > 3:
                    _warn(
                        "战斗开始检测: 开战连续 3 次仍未启动，"
                        "异常出口 no_start（不再空点）"
                    )
                    return "no_start"
                _warn("战斗开始检测: 开战仍在（战斗未启动），重试点击")
                bot.click_if_found("开战.png", threshold=0.85)
                bot.sleep(2.0)
                if self._zz_cancel_weak_lineup(bot):
                    return "weak"
                bot.sleep(1.0)
                continue
            if bot.click_if_found("跳过.png", threshold=0.85):
                logger.info("战斗开始检测: 跳过出现，战斗进行中")
                break
            bot.sleep(3.0)

        bot.sleep(0.5)

        # [2026-11-17 run5 帧证据] 加号面板是逐项展开的，不是瞬间出现：
        # 点加号后约 0.9s 只显示"退出游戏/设置/背景"，
        # 约 1.2s 才补上"托管/暂停"。原来固定 0.5s 就点托管，
        # 此时按钮还没渲染，点击落在空白处，托管实际没开启。
        # 固定等 1.5s，确保面板完全展开后再点托管。
        if bot.click_if_found("加号.png", threshold=0.85):
            bot.sleep(1.5)

        bot.click_if_found("托管.png", threshold=0.90)

        # 轮询等待战斗结束（上限 60 次，约 10 分钟，防异常卡死）。
        # 战败重开上限 max_battles 次：敌方过强的急报可能打不赢，
        # 不设上限会在 战败→重开 循环里无限空转
        max_polls = 60
        battles_fought = 0
        polls = 0
        while True:
            polls += 1
            if polls > max_polls:
                _warn(f"战斗轮询超过 {max_polls} 次仍未结束，交由上层等待")
                return "timeout"
            bot.sleep(poll_interval)
            if bot.click_if_found("空白处.png", threshold=blank_threshold):
                bot.sleep(1.0)
                # 空白处也可能在对局中以“点击空白处关闭”的提示层出现；
                # 只有既无结束信号、又仍在战斗，才算“提示层”继续轮询
                if self._zz_in_battle(bot):
                    logger.debug("空白处命中但对局仍在进行（战斗内提示层），继续轮询")
                    continue
                logger.info("战斗结束")
                battles_fought += 1
                # 先判断是否已达重开上限，再决定要不要点“开战”。
                # 原顺序是先点后判：第 3 次战败后仍点了开战，等于新开一局
                # 然后把脚本抽走，对局就在后台继续跑（2026-11-13 用户实测）。
                if bot.is_present("开战.png", threshold=0.85):
                    if battles_fought >= max_battles:
                        _warn(
                            f"战斗连续失败 {battles_fought} 次，放弃本场"
                            f"（敌方可能过强），返回由上层继续"
                        )
                        # 退出布阵界面，不能在“开战”界面直接走
                        self._zz_leave_battle_ui(bot)
                        return "ok"
                    logger.info(
                        f"战斗失败，重新开战（第 {battles_fought}/{max_battles} 次）"
                    )
                    bot.click_if_found("开战.png", threshold=0.85)
                    bot.sleep(2.0)
                    # 再战沿用保存的阵容：残阵时在此拦截，不再硬打
                    # 同样 2s 首检 + 2s 复检防慢加载漏检
                    for _ in range(2):
                        bot.sleep(2.0)
                        if self._zz_cancel_weak_lineup(bot):
                            return "weak"
                    # 战斗开始检测（同首战）：开战仍在→重试；加号出现→战斗中
                    start_retries = 0
                    for _ in range(10):
                        if bot.is_present("加号.png", threshold=0.85):
                            break
                        if bot.is_present("开战.png", threshold=0.85):
                            start_retries += 1
                            if start_retries > 3:
                                _warn(
                                    "再战开始检测: 开战连续 3 次仍未启动，"
                                    "异常出口 no_start"
                                )
                                return "no_start"
                            _warn("战斗开始检测: 开战仍在（战斗未启动），重试点击")
                            bot.click_if_found("开战.png", threshold=0.85)
                            bot.sleep(2.0)
                            if self._zz_cancel_weak_lineup(bot):
                                return "weak"
                            bot.sleep(1.0)
                            continue
                        if bot.click_if_found("跳过.png", threshold=0.85):
                            break
                        bot.sleep(3.0)
                    bot.sleep(0.5)
                    bot.click_if_found("加号.png", threshold=0.85)
                    bot.sleep(0.5)
                    bot.click_if_found("托管.png", threshold=0.85)
                    continue
                # 没有开战按钮：本场真正结束，退回征战天下再返回
                self._zz_leave_battle_ui(bot)
                return "ok"

    def _zz_answer_quiz(self, bot: GameBot, max_attempts: int = 5):
        """
        答题：每轮检测答题选项，命中则点击，最多 max_attempts 次。
        任意一次未检出即提前退出。
        """
        for _ in range(max_attempts):
            result = bot.find_region("答题.png", threshold=0.85)
            if result is None:
                break
            lx, ly, w, h, _ = result
            bot.click(lx + w / 2, ly + h / 2)
            bot.sleep(3.0)

    def _zz_handle_no_painting(self, bot: GameBot):
        """无画画路径：跳过 → 答题 → 空白处退出。"""
        bot.click_if_found("跳过.png", threshold=0.85)
        bot.sleep(1.0)
        self._zz_answer_quiz(bot)
        bot.click_if_found("跳过.png", threshold=0.85)
        bot.sleep(0.5)
        bot.click_if_found("空白处.png", threshold=0.85)
        bot.sleep(1.0)

    # ═══════════════════════════════════════════════════════
    # 征战天下 — 任务入口
    # ═══════════════════════════════════════════════════════

    def _zz_process_emergency(self, bot: GameBot, count: int = 0,
                              max_depth: int = 20, weak_retries: int = 0) -> int:
        """
        递归处理急报：检测 → 前往 → 画画/战斗 → 递归下一场。
        每场急报前先打一轮演武（穿插执行，未达标才干活）。
        画画+跳过均未检出时 fanhui 回退重进后递归。
        max_depth: 递归深度上限，防止同一场急报反复处理导致无限递归。
        weak_retries: 当前这场急报因残阵确认框取消后已重进的次数（换场后归零）。
        选将未满 / 连续 3 次残阵 / 开战连续未启动时抛 ZZLineupIncomplete，
        由 zhengzhan_task1 捕获并跳过本次急报，不再强行开战。
        返回总处理场数。
        """
        # 深度上限保护：同一场急报处理失败时避免无限递归
        if count >= max_depth:
            _warn(f"急报处理达到深度上限（{max_depth}），停止递归")
            return count

        # 演武穿插：每场急报前点一轮战斗（达标/放弃后内部直接返回）
        self._yw_round(bot, exit_after=True)
        self._yw_claim_if_won(bot)  # 穿插轮次可能已达标，达标立即领奖（否则主循环被_done挡住漏领）

        if not bot.click_if_found("急报.png", threshold=0.50):
            return count

        count += 1
        logger.debug(f"处理第 {count} 场急报")
        bot.sleep(3.0)

        self._zz_click_until_gone(bot, "前往.png", threshold=0.85)

        # 等战斗/画画界面加载（镜框或跳过出现），防止检测过快误判
        for _ in range(6):  # 最多约 3s
            if (bot.find_region("跳过.png", threshold=0.85) is not None
                    or bot.find_region("三镜框.png", threshold=0.75) is not None
                    or bot.find_region("双镜框.png", threshold=0.75) is not None
                    or bot.find_region("单镜框.png", threshold=0.75) is not None):
                break
            bot.sleep(0.5)

        painting = self._zz_detect_painting(bot)

        if painting is None:
            if self._zz_in_battle(bot):
                # 前往后直接进了对局（加载慢/上一场未退干净）：先等它打完，
                # 此时点 fanhui 会把对局退掉（2026-11-13 用户实测）
                _warn("检测到对局已在运行，等待结束后再继续（不点返回/退出）")
                if not self._zz_wait_battle_end(bot):
                    self._zz_battle_pending = True
                    _warn("对局超时仍未结束，终止急报递归，等待对局结束")
                    return count
                self._zz_leave_battle_ui(bot)
            elif not bot.find_region("跳过.png", threshold=0.85):
                logger.info("画画/跳过均未检出，fanhui 回退重进后递归")
                for _ in range(3):
                    if not bot.click_if_found("fanhui.png", threshold=0.85):
                        break
                    bot.sleep(1.0)
                self._zz_enter_page(bot)
                # 重新进入后多等一会再检测，防止页面未加载好就识别（回主界面再点征战天下）
                bot.sleep(2.0)
                for _ in range(6):  # 最多约 3s 等急报出现
                    if bot.find_region("急报.png", threshold=0.50):
                        break
                    bot.sleep(0.5)
            else:
                self._zz_handle_no_painting(bot)
        else:
            placed = self._zz_select_characters(bot, painting)
            expected = len(self._ZZ_LINEUP_HEROES.get(painting, ()))
            if expected == 0 or placed < expected:
                _warn(
                    f"选将未满（{placed}/{expected}，{painting}），"
                    f"禁止开战，异常退出本场急报"
                )
                self._zz_leave_lineup(bot)
                raise ZZLineupIncomplete(
                    f"{painting} 选将 {placed}/{expected}"
                )
            status = self._zz_battle_flow(bot)

            if status == "no_start":
                _warn("开战连续未启动，异常退出本场急报（不再空点）")
                self._zz_leave_lineup(bot)
                raise ZZLineupIncomplete("开战未启动")

            if status == "timeout":
                # 轮询 10 分钟仍未结束：不点任何退出，交上层等待/停止，
                # 避免“脚本已结束、对局还在打”
                self._zz_battle_pending = True
                _warn("对局仍在进行（轮询超时），终止急报递归，等待对局结束")
                return count

            if status == "weak":
                # 残阵确认框已点取消。同一场急报最多重进 3 次；仍未选满
                # 就不再强行开战，走异常出口退出，避免残阵拖成无限循环。
                if weak_retries >= 3:
                    _warn(
                        f"本场急报连续 {weak_retries} 次残阵取消，仍未选满，"
                        f"异常退出（不再强行开战）"
                    )
                    self._zz_leave_lineup(bot)
                    raise ZZLineupIncomplete("连续 3 次残阵取消")
                logger.info(
                    f"残阵已取消，fanhui 回退重进后重新递归"
                    f"（第 {weak_retries + 1}/3 次重进）"
                )
                for _ in range(3):
                    if not bot.click_if_found("fanhui.png", threshold=0.85):
                        break
                    bot.sleep(1.0)
                self._zz_enter_page(bot)
                bot.sleep(2.0)
                return self._zz_process_emergency(
                    bot, count, max_depth, weak_retries + 1
                )

        # 递归/返回前必须确认已退出布阵或结算界面。对局还在打时绝不点返回，
        # 也不能让上层在战斗界面上继续找征战天下入口（2026-11-13 用户实测：
        # 对局还在继续，脚本却已经结束征战任务）。
        if not self._zz_leave_battle_ui(bot):
            if self._zz_in_battle(bot):
                self._zz_battle_pending = True
                _warn("对局仍在进行，终止急报递归（不退出对局）")
            else:
                _warn("未能退回征战天下界面，终止急报递归")
            return count

        bot.sleep(3.0)
        return self._zz_process_emergency(bot, count)

    def zhengzhan_task1(self, bot: GameBot) -> bool:
        """
        征战天下主流程（演武 + 急报 + 体力清 三合一）。

        外层最多 MAX_PASSES 轮扫描，每轮依次处理 演武穿插 → 急报 → 演武专注
        补打 → 体力清，三者全部完成才结束本任务——急报没清完/演武没达标就
        跳到下一任务是 2026-08-30 用户指出的 bug。演武战斗需要真实时间消化，
        轮与轮之间的急报战斗/体力清即为消化窗口。

        返回 True = 急报清完 且 演武结束(达标或机会耗尽) 且 体力清完成。
        """
        t0 = time.perf_counter()
        logger.info("=== 征战天下（演武 + 急报 + 体力清）===")

        # 上一轮对局还没结束：先等它自然结束，绝不带着对局继续扫描/清理
        if getattr(self, "_zz_battle_pending", False):
            if self._zz_in_battle(bot):
                _warn("对局仍在进行，等待结束后再继续征战任务")
                if not self._zz_wait_battle_end(bot, max_wait=600.0):
                    _warn("对局 10 分钟仍未结束，暂停本次征战任务（不退出对局）")
                    return False
                self._zz_leave_battle_ui(bot)
            self._zz_battle_pending = False

        if not self._zz_enter_page(bot):
            if self._zz_in_battle(bot):
                # 还在对局里：不能 dismiss_popups（会点 fanhui 退掉对局），
                # 先等对局结束再尝试进入征战天下
                _warn("对局仍在进行，等待结束后再进入征战天下")
                if not self._zz_wait_battle_end(bot):
                    self._zz_battle_pending = True
                    return False
                self._zz_leave_battle_ui(bot)
            else:
                logger.info("进入征战天下失败，先清理弹窗/返回主界面后重试")
                self.dismiss_popups(bot)
            if not self._zz_enter_page(bot):
                logger.error(
                    f"征战天下未开始（进入征战天下失败），"
                    f"耗时 {time.perf_counter() - t0:.1f}s"
                )
                return False

        # 演武会话重置
        self._yw_reset()

        jibao_done = False
        tili_done = False
        total = 0
        jibao_aborted = False
        MAX_PASSES = 3

        for pass_idx in range(1, MAX_PASSES + 1):
            logger.info(f"── 征战天下 第 {pass_idx}/{MAX_PASSES} 轮扫描 ──")

            if getattr(self, "_zz_battle_pending", False):
                if self._zz_in_battle(bot):
                    _warn("对局仍在进行，暂停本轮征战扫描（不退出对局）")
                    break
                self._zz_battle_pending = False

            if not self._zz_enter_page(bot):
                if self._zz_in_battle(bot):
                    # 战斗界面：dismiss_popups 里的 fanhui/退出会退掉对局，
                    # 这里只等待，不清理
                    _warn("对局仍在进行，跳过弹窗清理，等待对局结束")
                    bot.sleep(5.0)
                    continue
                logger.info("进入征战天下失败，先清理弹窗/返回主界面后重试")
                self.dismiss_popups(bot)
                if not self._zz_enter_page(bot):
                    _warn(f"第 {pass_idx} 轮无法进入征战天下界面")
                    continue

            # 演武穿插：未达标时每轮先打一轮，战斗由本轮后续步骤消化
            if self._zz_in_battle(bot):
                _warn("对局仍在进行，跳过本轮演武穿插")
            elif not self._yw_done:
                self._yw_round(bot, exit_after=True)
                self._yw_claim_if_won(bot)

            # 急报递归，复查前先确保回到征战天下界面——递归可能遗留在
            # 战斗/布阵/其他页面，在错误页面上检测不到急报会造成"已清完"误判
            # （2026-08-30 用户反馈：急报没清完就进行体力清）
            if not jibao_done:
                if bot.find_region("急报.png", threshold=0.50):
                    try:
                        total += self._zz_process_emergency(bot)
                    except ZZLineupIncomplete as exc:
                        jibao_aborted = True
                        _warn(
                            f"急报选将/开战异常出口（{exc}），"
                            f"结束本次征战任务（不再强行开战）"
                        )
                        break
                if getattr(self, "_zz_battle_pending", False) and self._zz_in_battle(bot):
                    _warn("对局仍在进行，暂停本轮后续步骤（不退出对局）")
                    continue
                if self._zz_enter_page(bot):
                    if bot.find_region("急报.png", threshold=0.50):
                        _warn(f"第 {pass_idx} 轮递归结束后仍检测到急报，下一轮继续")
                    else:
                        jibao_done = True
                        logger.info("急报已清完（征战天下界面复查无急报）")
                else:
                    _warn(
                        f"第 {pass_idx} 轮无法回到征战天下界面复查急报，"
                        f"下一轮继续"
                    )

            # 急报后跟一轮演武检测（用户口径 2026-08-31）：只打一轮、不等冷却，
            # 让下一项的体力清时间继续消化战斗；不做专注等待
            if self._zz_in_battle(bot):
                _warn("对局仍在进行，跳过急报后的演武检测")
            elif not self._yw_done:
                self._yw_round(bot, exit_after=True)
                self._yw_claim_if_won(bot)

            # 体力清（对局进行中不进入体力页，避免误点返回）
            if not tili_done and not self._zz_in_battle(bot):
                tili_done = self.zhengzhan_tili_clear(bot)

            # 演武专注补打（等待战斗中消化）：仅当急报+体力清都完成后才开始，
            # 此前的一切等待都穿插在任务时间里，不空耗
            if not self._yw_done and jibao_done and tili_done:
                self._yw_round_until_done(bot, wait_on_cooldown=True)
                self._yw_claim_if_won(bot)

            if jibao_done and self._yw_done and tili_done:
                self._yw_claim_if_won(bot)  # 兜底：确保达标后一定领奖
                logger.info(
                    f"第 {pass_idx} 轮扫描全部完成"
                    f"（急报清完/演武结束/体力清完）"
                )
                break
        else:
            _warn(f"{MAX_PASSES} 轮扫描后仍未全部完成，按当前状态结束本任务")

        if getattr(self, "_yw_maintenance", False):
            logger.info("演武维护中：本次已跳过演武环节（不影响急报/体力清）")
        elif self._yw_done and not self._yw_won:
            logger.info(
                f"演武未达标（机会 {self._yw_opportunity}），跳过收尾领奖"
            )

        # 收尾前先确保对局结束；对局还在打时绝不点 fanhui，否则会退掉对局
        if self._zz_in_battle(bot):
            _warn("征战天下收尾时对局仍在进行，等待结束后再回主界面")
            if not self._zz_wait_battle_end(bot, max_wait=600.0):
                self._zz_battle_pending = True
                _warn("对局 10 分钟仍未结束，暂停回主界面（不退出对局）")
            else:
                self._zz_leave_battle_ui(bot)
        if not self._zz_in_battle(bot):
            self._zz_battle_pending = False

        # 返回主界面：从征战天下逐级 fanhui，直到主界面标志可见
        if getattr(self, "_zz_battle_pending", False):
            _warn("对局仍在进行，跳过回主界面导航（避免退掉未结束的对局）")
        else:
            for _ in range(8):
                if self._on_main_screen(bot):
                    break
                bot.click_if_found("fanhui.png", threshold=0.85)
                bot.sleep(0.5)
            else:
                _warn("征战天下结束后未回到主界面")

        elapsed = time.perf_counter() - t0
        done = jibao_done and tili_done and self._yw_done and not jibao_aborted
        yw_state = ("维护跳过" if getattr(self, "_yw_maintenance", False)
                    else "已领奖" if self._yw_claimed else "未达标/已放弃")
        logger.info(
            f"征战天下结束：完成={done}，处理急报 {total} 场，"
            f"急报异常退出={jibao_aborted}，"
            f"演武机会 {self._yw_opportunity}（{yw_state}），"
            f"耗时 {elapsed:.1f}s"
        )
        if getattr(self, "_zz_battle_pending", False):
            logger.info("征战天下结束：对局仍在进行，已停止后续导航（等待对局结束）")
        return done

    # ═══════════════════════════════════════════════════════
    # 征战天下 — 体力清
    # ═══════════════════════════════════════════════════════

    def _zz_tili_sweep(self, bot: GameBot, number: str) -> bool:
        """
        针对指定数量（40/30/20）进行速战清扫。
        返回 True 表示该数量扫完，False 表示途中体力不足。
        调用前已确认该数字按钮存在。
        """
        bot.click_if_found(f"{number}.png", threshold=0.80)
        bot.sleep(0.5)

        while True:
            if bot.is_present("体力不足.png", threshold=0.55):
                return False
            if bot.click_if_found("速战.png", threshold=0.98):
                bot.sleep(0.5)
            else:
                break
        # 速战消失后再次确认体力不足
        if bot.is_present("体力不足.png", threshold=0.85):
            return False
        # 关闭结算弹窗，回到材料页
        self._zz_click_edge_return(bot, 1)
        return True

    def _zz_click_edge_return(self, bot: GameBot, times: int = 2):
        """点击屏幕右侧边缘返回（关闭体力不足等弹窗）。"""
        monitor = bot.screenshot.monitor
        edge_x = monitor["width"] - 10
        edge_y = monitor["height"] // 2
        for _ in range(times):
            bot.click(edge_x, edge_y)
            bot.sleep(0.5)

    def zhengzhan_tili_clear(self, bot: GameBot) -> bool:
        """
        体力清：唯一结束指标 = 体力不足弹窗出现。
        循环 40→30→20 直到体力不足；找不到数字按钮时检查界面并纠正。
        体力不足出现后 → 任务 → lingqu 循环 → 宝箱1/2 → 回征战天下。
        """
        logger.info("=== 征战天下 体力清 ===")
        t0 = time.perf_counter()

        # 用包裹检测是否已在征战天下界面
        if not bot.is_present("包裹.png", threshold=0.70):
            self._zz_enter_page(bot)
            if not self._wait_found(bot, "包裹.png", 0.70):
                _warn("无法进入征战天下界面")
                logger.error(
                    f"体力清未开始（无法进入征战天下界面），"
                    f"耗时 {time.perf_counter() - t0:.1f}s"
                )
                return False


        # 每日奖励
        if bot.click_if_found("每日奖励.png", threshold=0.85):
            bot.sleep(1.0)
            self._zz_click_edge_return(bot, 1)

        # 包裹 → 材料（等待页面加载后再点，避免加载慢导致点空）

        bot.click_if_found("包裹.png", threshold=0.70)
        self._wait_found(bot, "材料.png", 0.85)
        bot.click_if_found("材料.png", threshold=0.85)
        bot.sleep(0.5)

        # 循环 40→30→20 直到体力不足
        tili_exhausted = False
        max_loops = 20
        while not tili_exhausted:
            max_loops -= 1
            if max_loops <= 0:
                _warn("体力清超过 20 轮仍未结束，检查体力不足")
                if bot.is_present("体力不足.png", threshold=0.55):
                    tili_exhausted = True
                break

            for number in ("40", "30", "20"):
                if bot.find_region(f"{number}.png", threshold=0.80):
                    # 数字存在 → 正常速战
                    if not self._zz_tili_sweep(bot, number):
                        tili_exhausted = True
                        break
                else:
                    # 数字不在 → 在主界面则先坐标点征战天下，再统一进包裹→材料
                    if self._on_main_screen(bot):
                        logger.info("检测到主界面，相对坐标重新进入征战天下")
                        self._zz_click_main_entry(bot)
                        self._wait_found(bot, "包裹.png", 0.70)
                    bot.click_if_found("包裹.png", threshold=0.70)
                    self._wait_found(bot, "材料.png", 0.85)
                    bot.click_if_found("材料.png", threshold=0.85)
                    bot.sleep(0.5)
                    break  # 重新从 40 开始

        # 体力不足已出现 → 关弹窗 → fanhui 回征战天下 → 任务
        self._zz_click_edge_return(bot, 2)
        bot.sleep(0.5)
        bot.click_if_found("fanhui.png", threshold=0.85)
        bot.sleep(0.5)
        bot.click_if_found("任务.png", threshold=0.85)
        bot.sleep(5.5)  # 进入任务后多等 5s 再点领取

        # 任务页就绪确认（2026-09-06 用户反馈：lingqu 没进行 → 宝箱也没领到）。
        # lingqu 一个没匹配到时先确认真的没有（重进一次任务页排除加载问题），
        # 而不是直接跳到宝箱——宝箱奖励往往要 lingqu 领取后才出现。
        max_claims = 30
        claimed = 0
        for round_i in range(2):
            for _ in range(max_claims):
                if bot.click_if_found("lingqu.png", threshold=0.85):
                    claimed += 1
                    bot.sleep(3.0)
                    continue
                bot.sleep(3.0)
                if not bot.click_if_found("lingqu.png", threshold=0.85):
                    break
            else:
                _warn(f"任务领取超过 {max_claims} 轮仍未结束，退出")
            if claimed or round_i:
                break
            # 第一轮一个都没领到：可能页面未加载好，重进任务页再试一轮
            _warn("任务页第一轮未发现 lingqu，重进任务页再试")
            bot.click_if_found("fanhui.png", threshold=0.85)
            bot.sleep(1.0)
            bot.click_if_found("任务.png", threshold=0.85)
            bot.sleep(5.5)
        logger.info(f"任务页共领取 {claimed} 次")

        # 宝箱（2026-09-06 改用 _claim_chests：浮窗关闭+两轮扫描，防漏宝箱4；
        # 并过滤任务页顶部页签假匹配——run16 在任务页点 6 个"宝箱"全是页签）
        self._claim_chests(bot, ("宝箱1", "宝箱2", "宝箱3", "宝箱4"), threshold=0.5)

        bot.click_if_found("fanhui.png", threshold=0.85)
        bot.sleep(0.5)

        if not self._zz_is_on_page(bot):
            _warn("体力清结束后未检测到征战天下界面")

        logger.info(
            f"体力清完成：体力耗尽={tili_exhausted}，耗时 {time.perf_counter() - t0:.1f}s"
        )
        return True
