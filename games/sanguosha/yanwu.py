"""
三国杀脚本 — 征战天下·演武模块。

YanwuMixin 承载演武玩法（与急报/体力清穿插执行的长时间任务）：

规则要点：
- 每日 10 次战斗机会：胜利扣 1、失败返还 1、"战斗中"临时占用 1
- 同一时间最多 5 个"战斗中"并发
- 军令数字（剩余机会数）可稳定识别，直接作为"还能发起几场"的依据，
  无需再识别胜利图标；连续三轮军令机会 ≤1（jl0/jl1）判定演武成功收尾
- 军令出现 = 本轮 10 次机会已耗尽（胜利扣 + 战斗中占用用满），
  此时不再发起新战斗，等待"战斗中"消化；失败返还机会后军令消失
- 穿插执行：_yw_round 只负责"点战斗/点自动战斗"，不等待战斗完成，
  由急报等其它任务的时间窗口消化战斗进度；专注补打用 _yw_round_until_done。
"""

import os
import time
from typing import List, Optional, Tuple

from core.game_bot import GameBot

from .constants import (
    logger, _warn,
    YW_BATTLE_THRESHOLD, YW_FIGHTING_THRESHOLD, YW_WIN_THRESHOLD,
    YW_AUTO_THRESHOLD, YW_REFRESH_THRESHOLD,
    YW_CLAIM_THRESHOLD, YW_ENTER_THRESHOLD,
    YW_MAX_CONCURRENT, YW_DAILY_LIMIT, YW_REFRESH_MAX,
    YW_CLICK_RETRIES, YW_AUTO_MISS_MAX, YW_CLAIM_MAX, YW_FOCUS_WAIT, YW_FOCUS_MAX_ROUNDS,
    YW_EXCLUDE_BATTLES,
    YW_JUNLING_LEFT, YW_JUNLING_ANCHOR_THRESHOLD, YW_JUNLING_DIGIT_THRESHOLD,
    YW_LOW_OPP_CONFIRM_GAP,
)


YW_GONGPO_TEN_TEMPLATE = "今日攻破10.png"
# 今日攻破数字区域（1601x900 窗口相对坐标）：只在这个小区域内匹配，
# 避免全屏里其他 "10" 误命中。
YW_GONGPO_REGION = (940, 190, 1080, 265)


class YanwuMixin:
    """征战天下：演武（连续三轮军令机会≤1 判定达标收尾，与急报/体力清穿插）。"""

    # ── 状态管理 ──────────────────────────────────

    def _yw_reset(self) -> None:
        """重置演武会话状态（每次进入征战天下主流程时调用）。"""
        self._yw_used = 0            # 本轮已发起战斗次数（仅统计）
        self._yw_refreshes = 0       # 刷新次数
        self._yw_auto_miss = 0       # 连续未找到自动战斗按钮的次数
        self._yw_round_skipped = False  # 本轮是否因连续失败被跳过（专注补打检测用）
        self._yw_need_reenter = False   # 界面丢失（自动战斗未发起等）：已 fanhui 恢复，需重新进入演武界面
        self._yw_done = False        # 是否已结束（达标或放弃）
        self._yw_won = False         # 是否真正达标（连续三轮军令机会≤1），仅达标才走收尾领奖
        self._yw_claimed = False     # 是否已完成收尾领奖
        self._yw_opportunity: Optional[int] = None  # 最近一次进演武界面识别的剩余机会数（None=未读到）
        self._yw_low_opp_streak = 0  # 军令机会 ≤1 的确认次数（间隔达标才累计）
        self._yw_low_opp_first_time = 0.0  # 第一次 ≤1 确认的时间戳（间隔门控用）
        self._yw_maintenance = False  # 演武维护中（按钮灰色无法进入），跳过演武环节

    # ── 机会识别 ──────────────────────────────────

    def _yw_read_opportunity(self, bot: GameBot) -> Optional[int]:
        """
        识别演武界面的剩余机会数（军令数字）。

        用 jl0~jl10 模板在全窗口截图上逐一 match_one，取最高置信度，
        超过 YW_JUNLING_DIGIT_THRESHOLD 即作为机会数（替代 OCR，单字符 OCR 漏检）。
        读取前先等 0.8s（进界面数字未渲染完成会误读，用户确认的等待）；
        连续 3 次（间隔 0.8s）识别失败才返回 None（防加载导致的误读
        把 ≤1 确认序列清零）。
        """
        try:
            import os
            bot.sleep(0.8)  # 进界面先等数字渲染
            tmpl_dir = os.path.join(os.path.dirname(__file__), '..', '..', 'templates', 'sanguosha')
            for attempt in range(3):
                img = bot.screenshot.capture()
                best_digit, best_conf = None, 0.0
                for d in range(0, 11):
                    path = os.path.join(tmpl_dir, f"jl{d}.png")
                    if not os.path.exists(path):
                        continue
                    hit = bot._matcher.match_one(img, path, threshold=YW_JUNLING_DIGIT_THRESHOLD)
                    if hit is not None and hit[2] > best_conf:
                        best_conf = hit[2]
                        best_digit = d
                if best_digit is not None:
                    self._yw_opportunity = best_digit
                    logger.info(f"演武：识别军令机会 {best_digit}（置信度 {best_conf:.3f}）")
                    return best_digit
                if attempt < 2:
                    logger.debug("演武：军令数字未识别，0.8s 后重试（防加载未完成误读）")
                    bot.sleep(0.8)
            logger.debug("演武：军令数字连续 3 次未识别（按未读到处理）")
            return None
        except Exception as e:
            logger.warning(f"读取军令机会异常（按未读到处理）: {e}")
            return None

    def _yw_is_gongpo_ten(self, bot: GameBot) -> bool:
        """是否是"今日攻破=10"（只在数字区域匹配，避免其他 '10' 误命中）。

        用户口径：只有今日攻破=10 且军令=0 才允许跳过
        "军令≤1 连续三次确认"，其余情况仍按原规则走。
        """
        try:
            img = bot.screenshot.capture()
            x1, y1, x2, y2 = YW_GONGPO_REGION
            crop = img[y1:y2, x1:x2]
            path = os.path.join(bot.template_dir, YW_GONGPO_TEN_TEMPLATE)
            hit = bot._matcher.match_one(crop, path, 0.90)
            if hit is not None:
                logger.info(f"演武：今日攻破=10（置信度 {hit[2]:.3f}）")
                return True
            return False
        except Exception as e:
            logger.warning(f"识别今日攻破异常（按未达10处理）: {e}")
            return False

    # ── 位置工具 ──────────────────────────────────

    @staticmethod
    def _yw_same_positions(a: List[Tuple[int, int, float]],
                           b: List[Tuple[int, int, float]],
                           tol: int = 15) -> bool:
        """两组匹配结果是否互为同一批位置（容差 tol 像素，用于刷新前后对比）。

        数量不一致或任一点在对方找不到对应点，都判定不是同一批。
        """
        if len(a) != len(b):
            return False
        used = [False] * len(b)
        for ax, ay, _ in a:
            matched = False
            for i, (bx, by, _) in enumerate(b):
                if not used[i] and abs(ax - bx) <= tol and abs(ay - by) <= tol:
                    used[i] = True
                    matched = True
                    break
            if not matched:
                return False
        return True

    @staticmethod
    def _yw_filter_battles(battles: List[Tuple[int, int, float]]) -> List[Tuple[int, int, float]]:
        """过滤掉顶尖对手格（带"顶尖对手"横幅，与战斗模板不符，不可点）。"""
        return [b for b in battles
                if not any(abs(b[0] - ex[0]) <= 15 and abs(b[1] - ex[1]) <= 15
                           for ex in YW_EXCLUDE_BATTLES)]

    # ── 刷新 ──────────────────────────────────

    def _yw_try_refresh(self, bot: GameBot) -> bool:
        """
        点击刷新换一批战斗。按钮缺失 / 生效刷新超上限返回 False。
        刷新有十分钟冷却：点击刷新前先记录当前可点战斗位置，
        点击后若位置集合与刷新前完全一致（容差 15px），判定刷新未生效
        （冷却中），返回 False，由调用方继续下一步（急报/体力清/等待）。

        只有"生效"的刷新才计入上限；冷却中的无效刷新不计数、也不置终态
        （_yw_done），由后续任务间隙的演武检测在 CD 到期后重试。
        """
        if self._yw_refreshes >= YW_REFRESH_MAX:
            _warn(f"演武：生效刷新次数已达上限（{YW_REFRESH_MAX}），本轮停滞"
                  f"（非终态，后续任务间隙再试）")
            return False
        # 刷新前位置基线（点击前先拍一次，未换批对比用）
        before = bot.find_all("战斗.png", threshold=YW_BATTLE_THRESHOLD)
        before = self._yw_filter_battles(before)
        if not bot.click_if_found("刷新.png", threshold=YW_REFRESH_THRESHOLD):
            # 刷新按钮不可见 ≠ 刷新不可用：点击刷新后按钮进入约 10 分钟冷却态，
            # 外观变化导致模板不再匹配。把"未找到"计入放弃上限会把正常冷却
            # 误判为连续不可用而放弃整场演武（2026-08-30 run9 实测：
            # 首次点击成功后冷却，3 次"未找到"即放弃，还剩 5 次机会）。
            _warn("演武：未找到刷新按钮（可能冷却中/界面未就绪），本轮结束")
            return False
        bot.sleep(1.0)
        # 点击后对比可点战斗位置：与刷新前完全一致 → 未换批 → 刷新未生效
        after = bot.find_all("战斗.png", threshold=YW_BATTLE_THRESHOLD)
        after = self._yw_filter_battles(after)
        if self._yw_same_positions(before, after):
            # 冷却中的无效刷新不计数：否则 10 分钟 CD 内几次无效点击就耗尽上限、
            # 触发过早终态（2026-09-13 修复：演武过早放弃、后续不再穿插演武）
            _warn("演武：刷新未生效（可点战斗未换批，可能冷却中），本轮结束")
            return False
        self._yw_refreshes += 1  # 只统计生效的刷新
        logger.info(f"演武：刷新生效（{self._yw_refreshes}/{YW_REFRESH_MAX}）")
        if not after:
            _warn("演武：刷新后无可点战斗，本轮结束")
            return False
        return True

    # ── 界面进出 ──────────────────────────────────

    def _yw_enter(self, bot: GameBot) -> bool:
        """确保在演武界面：已在（演武特有元素可见）则跳过，否则点演武入口进入。
        进入后读一次军令机会数字（识别失败时 _yw_opportunity 保持旧值/None）。

        维护检测（2026-10-24 用户需求：演武小概率进入维护，按钮变灰无法打开）：
        - 点击入口两次、界面元素都未出现 → 判维护；
        - 入口不可见但征战天下页其他元素（急报/任务/OCR）在 → 判维护（灰按钮
          不匹配演武模板的情况）；
        判维护后置 _yw_maintenance/_yw_done，本次任务跳过演武环节，
        急报/体力清继续（不再是 RuntimeError 炸穿整个征战任务）。
        """
        if (bot.find_region("战斗.png", threshold=YW_BATTLE_THRESHOLD) is not None
                or bot.find_region("胜利.png", threshold=YW_WIN_THRESHOLD) is not None
                or bot.find_region(YW_JUNLING_LEFT, threshold=YW_JUNLING_ANCHOR_THRESHOLD) is not None
                or bot.find_region("刷新.png", threshold=YW_REFRESH_THRESHOLD) is not None
                or bot.find_region("战报.png", threshold=YW_CLAIM_THRESHOLD) is not None):
            self._yw_read_opportunity(bot)
            return True
        for attempt in range(2):
            if not bot.click_if_found("演武.png", threshold=YW_ENTER_THRESHOLD):
                if attempt == 0:
                    bot.sleep(1.0)  # 给一次页面加载机会再找
                    continue
                # 入口持续不可见：在征战天下页上（其他元素佐证）→ 判维护
                if (bot.is_present("急报.png", threshold=0.70)
                        or bot.is_present("任务.png", threshold=0.85)
                        or self._page_by_ocr(bot, ("征战天下",))):
                    self._declare_maintenance("征战天下页上演武入口持续不可见（疑似灰色维护）")
                return False
            bot.sleep(1.0)
            if self._yw_wait_interface(bot):
                self._yw_read_opportunity(bot)
                return True
        self._declare_maintenance("点击演武入口两次后界面元素仍未出现")
        return False

    def _declare_maintenance(self, reason: str) -> None:
        """演武维护终态：置维护标志并跳过本次任务的所有演武环节。"""
        self._yw_maintenance = True
        self._yw_done = True
        self._yw_won = False
        _warn(f"演武维护中：{reason}，本次跳过演武环节（急报/体力清继续）")

    def _yw_exit(self, bot: GameBot) -> bool:
        """从演武界面返回征战天下（点 fanhui 直到演武入口可见）。"""
        for _ in range(5):
            if bot.find_region("演武.png", threshold=YW_ENTER_THRESHOLD) is not None:
                return True
            bot.click_if_found("fanhui.png", threshold=YW_ENTER_THRESHOLD)
            bot.sleep(0.5)
        _warn("演武：退出演武界面失败")
        return False

    # ── 单轮演武（穿插版）─────────────────────────

    def _yw_round(self, bot: GameBot, exit_after: bool = False) -> None:
        """
        执行一轮演武（穿插版）：进入演武界面 → 打一轮 → 可选退出。
        exit_after=True 用于急报穿插：打完即退出回征战天下，不等待战斗完成。
        已达标或已放弃时直接返回。
        """
        if self._yw_done:
            return
        if not self._yw_enter(bot):
            return
        self._yw_round_core(bot)
        if exit_after:
            self._yw_exit(bot)

    def _yw_wait_interface(self, bot: GameBot, tries: int = 3) -> bool:
        """等待演武界面元素就绪（战斗/战斗中/胜利/军令1/刷新/战报任一可见）。
        界面加载中会全部检测不到，此时绝不能进入刷新判断。"""
        for _ in range(tries):
            if (bot.find_region("战斗.png", threshold=YW_BATTLE_THRESHOLD) is not None
                    or bot.find_region("战斗中.png", threshold=YW_FIGHTING_THRESHOLD) is not None
                    or bot.find_region("胜利.png", threshold=YW_WIN_THRESHOLD) is not None
                    or bot.find_region(YW_JUNLING_LEFT, threshold=YW_JUNLING_ANCHOR_THRESHOLD) is not None
                    or bot.find_region("刷新.png", threshold=YW_REFRESH_THRESHOLD) is not None
                    or bot.find_region("战报.png", threshold=YW_CLAIM_THRESHOLD) is not None):
                return True
            bot.sleep(1.0)
        return False

    def _yw_click_battles(self, bot: GameBot) -> Tuple[int, set]:
        """点满当前可点战斗（逐场点击 + 即时重检），返回 (成功发起数, 已点击位置集合)。"""
        clicked_ok = 0
        clicked_pos: set = set()
        for _ in range(YW_MAX_CONCURRENT):
            fighting = bot.find_all("战斗中.png", threshold=YW_FIGHTING_THRESHOLD)
            if len(fighting) >= YW_MAX_CONCURRENT:
                break
            # 剩余可发起机会：优先用识别到的军令数字（每次进界面读一次）。
            # 军令数字已含所有扣除（胜利扣 1、战斗中占用 1、失败返还 1），
            # 直接就是还能发起的次数；未读到则回退按总数减战斗中占用估算。
            # 已成功发起（clicked_ok）的场次同样占用机会，必须同步递减，
            # 否则机会=1 时循环会继续点下一场，真实中机会不足点击无效
            # 触发 auto_miss 误判跳过本轮。
            if self._yw_opportunity is not None:
                remaining = self._yw_opportunity - clicked_ok
            else:
                remaining = YW_DAILY_LIMIT - len(fighting)
            if remaining <= 0:
                # 不在这里直接判定完成：fighting=0经常是模板误匹配（切屏后未识别到），
                # 直接置_done会绕过3次确认机制导致过早停止（2026-10-21 实测只打5场就停）。
                # 机会≤0时只退出点击循环，由上层_round_core读军令走3次确认。
                logger.info("演武：剩余机会≤0，本轮不再发起新战斗（由3次确认判定是否完成）")
                break
            battles = bot.find_all("战斗.png", threshold=YW_BATTLE_THRESHOLD)
            battles = self._yw_filter_battles(battles)
            candidates = [b for b in battles
                          if (b[0], b[1]) not in clicked_pos]
            if not candidates:
                # 可点战斗已全部尝试过：若有过失败，视为连续失败达上限，
                # 由 _yw_round_core 跳过本轮，避免卡在 60s 空转重试同一批格子
                if self._yw_auto_miss > 0:
                    self._yw_auto_miss = YW_AUTO_MISS_MAX
                break
            bx, by, _ = candidates[0]
            clicked_pos.add((bx, by))
            bot.click(bx, by)
            bot.sleep(0.8)
            clicked = False
            for _ in range(YW_CLICK_RETRIES):
                if bot.click_if_found("自动战斗.png", threshold=YW_AUTO_THRESHOLD):
                    clicked = True
                    break
                bot.sleep(0.5)
            if clicked:
                clicked_ok += 1
                self._yw_used += 1
                self._yw_auto_miss = 0
            else:
                self._yw_auto_miss += 1
                _warn(f"演武：未找到自动战斗按钮（连续 {self._yw_auto_miss} 次），该场可能未发起")
                if self._yw_auto_miss >= YW_AUTO_MISS_MAX:
                    _warn(f"演武：连续 {YW_AUTO_MISS_MAX} 次未找到自动战斗按钮，跳过本轮")
                    logger.error("演武：连续检测不到自动战斗按钮，本轮跳过（异常）")
                    return clicked_ok, clicked_pos
            bot.sleep(0.5)
        return clicked_ok, clicked_pos

    def _yw_round_core(self, bot: GameBot) -> None:
        """
        演武单轮核心（假定在演武界面）：
        确认界面就绪 → 军令机会检测 → 点满可点战斗；
        仅当 0 战斗可点且 0 进行中（清空/全失败变回）才刷新，
        刷新后继续点新一批，不退出。刷新上限由 _yw_try_refresh 兜底。
        """
        # 0. 每轮重置连续失败/未找到自动战斗的计数与跳过标志，
        #    避免跨轮残留：穿插轮连续失败置 True 后，会误让后续正常轮次
        #    被 _yw_round_until_done 当作"连续失败"提前退出专注补打；
        #    刷新冷却标志同样每轮重置，由本轮刷新结果重新判定
        self._yw_auto_miss = 0
        self._yw_round_skipped = False
        self._yw_refresh_cd = False

        # 1. 界面就绪确认：加载中全部元素检测不到，绝不能进刷新判断
        if not self._yw_wait_interface(bot):
            _warn("演武：界面元素未就绪，本轮跳过（不刷新）")
            # [FIX 2026-08-31] 界面可能已丢失（自动战斗未发起/切走，战斗中与战斗均检测不到）：
            # 点一次 fanhui 恢复界面，由专注补打重新进入演武界面继续补打，不放弃任务。
            bot.click_if_found("fanhui.png", threshold=YW_ENTER_THRESHOLD)
            bot.sleep(0.5)
            self._yw_need_reenter = True
            return

        # 2. 机会检测：军令数字（剩余机会数）是唯一达标依据，
        #    连续两次 ≤1（jl0/jl1）且间隔足够 → 判定成功；
        #    间隔门控（YW_LOW_OPP_CONFIRM_GAP，2026-08-31 用户口径）：
        #    第二次确认靠 下一次任务间隙插入/专注补打轮 再读——中间的时间
        #    正好让"战斗中"消化，不做原地阻塞等待；仅当其他任务全完成
        #    剩演武时，专注补打的轮询循环原地等到间隔满足（这时候再等）。
        #    机会 0 且未达两次确认 → 本轮机会用尽，等待战斗中消化
        if self._yw_opportunity is None:
            self._yw_low_opp_streak = 0
        elif self._yw_opportunity <= 1:
            # [2026-11-17 用户口径] 只有"今日攻破=10 且军令=0"才允许
            # 跳过"军令≤1 连续三次确认"；其他 ≤1 情况仍必须走三次确认。
            if self._yw_opportunity == 0 and self._yw_is_gongpo_ten(bot):
                logger.info("演武：今日攻破=10 且军令=0，跳过三次确认，直接领奖收尾")
                self._yw_done = True
                self._yw_won = True
                self._yw_claim(bot)
                self._yw_claimed = True
                return
            now_ts = time.time()
            if self._yw_low_opp_streak == 0:
                self._yw_low_opp_streak = 1
                self._yw_low_opp_first_time = now_ts
                logger.info(f"演武：军令机会 {self._yw_opportunity}（第 1 次确认 ≤1）")
            elif now_ts - self._yw_low_opp_first_time >= YW_LOW_OPP_CONFIRM_GAP:
                # [MODIFY 2026-09-04] 连续确认从2次改为3次：每次确认递增并更新时间戳
                # [FIX 2026-09-06] 日志"距上次 0s"文案错误：更新时间戳前先算间隔
                gap = now_ts - self._yw_low_opp_first_time
                self._yw_low_opp_streak += 1
                self._yw_low_opp_first_time = now_ts
                logger.info(
                    f"演武：军令机会 {self._yw_opportunity}"
                    f"（第 {self._yw_low_opp_streak} 次确认 ≤1，"
                    f"距上次确认 {gap:.0f}s ≥ {YW_LOW_OPP_CONFIRM_GAP:.0f}s，战斗已消化）")
            else:
                logger.info(
                    f"演武：军令机会 {self._yw_opportunity}"
                    f"（距上次确认 ≤1 仅 {now_ts - self._yw_low_opp_first_time:.0f}s"
                    f" < {YW_LOW_OPP_CONFIRM_GAP:.0f}s，等战斗消化，本次不计数）"
                )
            if self._yw_low_opp_streak >= 3:
                logger.info("演武：三次确认军令机会 ≤1，判定演武成功")
                self._yw_done = True
                self._yw_won = True
                return
            if self._yw_opportunity <= 0:
                logger.info(
                    "演武：军令机会为 0，但今日攻破未到 10，"
                    "继续等待三次确认（不提前领奖）"
                )
                return
        else:
            self._yw_low_opp_streak = 0

        # 4. 点满可点战斗；无进展才刷新，刷新后继续点（不退出）
        for _ in range(YW_REFRESH_MAX + 1):
            if self._yw_auto_miss >= YW_AUTO_MISS_MAX:
                return
            clicked_ok, clicked_pos = self._yw_click_battles(bot)
            if clicked_ok:
                logger.info(f"演武：本轮发起 {clicked_ok} 场战斗")

            fighting_now = bot.find_all("战斗中.png", threshold=YW_FIGHTING_THRESHOLD)
            battles_now = bot.find_all("战斗.png", threshold=YW_BATTLE_THRESHOLD)
            battles_now = self._yw_filter_battles(battles_now)

            # 连续失败达上限（含可点战斗全部尝试失败）：跳过本轮，继续后续任务
            if self._yw_auto_miss >= YW_AUTO_MISS_MAX:
                self._yw_round_skipped = True
                _warn("演武：连续未发动战斗，本轮跳过（继续后续任务）")
                return

            # 还有可点战斗 → 说明槽位或机会已尽，本轮结束（穿插时由外层 fanhui 退出）
            if battles_now:
                return

            # 无可点战斗：算剩余机会与空余槽位，两者都有余量才刷新换批继续点
            remaining = YW_DAILY_LIMIT - len(fighting_now)
            slots = YW_MAX_CONCURRENT - len(fighting_now)
            if remaining <= 0 or slots <= 0:
                return
            if not self._yw_try_refresh(bot):
                # 刷新未生效（可能冷却中）或已放弃：本轮到此结束。
                # 置冷却标志，由专注补打决定退出等待还是留下等冷却消化
                self._yw_refresh_cd = True
                return
            continue

    # ── 专注补打（急报清完后）──────────────────────

    def _yw_round_until_done(self, bot: GameBot, wait_on_cooldown: bool = False) -> None:
        """
        专注补打：进入演武界面 → 连续执行直到达标/放弃 → 返回征战天下。
        两轮之间等待 YW_FOCUS_WAIT 秒让战斗消化。
        轮次上限 YW_FOCUS_MAX_ROUNDS 防止异常空转。

        wait_on_cooldown：刷新未生效（冷却中）时的处理方式。
        - False（默认）：刷新冷却中不空等，立即退出专注补打返回征战天下，
          由外层继续后续任务（急报/体力清等）消化冷却时间；
        - True：允许留在演武界面等待冷却消化后重试刷新
          （仅当急报/体力清等其它任务都已完成后才应传 True）。
        """
        if self._yw_done:
            return
        t0 = time.perf_counter()
        if not self._yw_enter(bot):
            _warn("演武：专注补打无法进入演武界面，跳过")
            return
        rounds = 0
        while not self._yw_done:
            rounds += 1
            if rounds > YW_FOCUS_MAX_ROUNDS:
                _warn(f"演武：专注补打 {YW_FOCUS_MAX_ROUNDS} 轮仍未达标，放弃")
                self._yw_done = True
                break
            self._yw_round_core(bot)
            # 本轮因连续失败被跳过：退出专注补打，继续后续任务（体力清等），不再空转等待
            if self._yw_round_skipped:
                _warn("演武：连续失败跳过本轮，退出专注补打")
                break
            # 界面丢失（自动战斗未发起/切走）：_yw_round_core 已点一次 fanhui 恢复，
            # 重新进入演武界面继续补打，不放弃任务
            if self._yw_need_reenter:
                self._yw_need_reenter = False
                if not self._yw_enter(bot):
                    _warn("演武：界面恢复后无法重新进入演武界面，退出补打")
                    break
                continue
            # 刷新未生效（冷却中）：后续还有任务（急报/体力清）时退出，不空等；
            # 仅当所有任务完成后（wait_on_cooldown=True）才留下等冷却
            if self._yw_refresh_cd and not wait_on_cooldown:
                _warn("演武：刷新未生效（冷却中），退出专注补打，由后续任务消化冷却")
                break
            if not self._yw_done:
                bot.sleep(YW_FOCUS_WAIT)
        self._yw_exit(bot)
        logger.info(
            f"演武专注补打结束：达成={self._yw_won}，"
            f"机会={self._yw_opportunity}，执行 {rounds} 轮，"
            f"耗时 {time.perf_counter() - t0:.1f}s"
        )

    # ── 收尾领奖 ──────────────────────────────────

    def _yw_claim(self, bot: GameBot) -> None:
        """
        达标收尾领奖（2026-09-04 用户口径调整）：
        1. 点开战报 → 循环点击领取直至没有
        2. 返回演武界面 → 点击宝箱1/2/3（阈值0.5，顶栏假匹配由_click_below_topbar过滤）
        3. fanhui 回到征战天下主界面
        """
        # [MODIFY 2026-09-04] 领奖流程：先战报领取，后宝箱(阈0.5)，再回征战天下
        if bot.click_if_found("战报.png", threshold=YW_CLAIM_THRESHOLD):
            bot.sleep(1.0)
            claimed = 0
            for _ in range(YW_CLAIM_MAX):
                matches = bot.find_all("领取.png", threshold=YW_CLAIM_THRESHOLD)
                if not matches:
                    break
                for mx, my, _ in matches:
                    bot.click(mx, my)
                    claimed += 1
                bot.sleep(1.0)
            logger.info(f"演武收尾：战报共点击领取 {claimed} 次")
            # 领完战报后返回演武界面再点宝箱
            bot.click_if_found("fanhui.png", threshold=YW_ENTER_THRESHOLD)
            bot.sleep(0.5)

        # 宝箱（2026-10-24 改用专属模板 演武宝箱1/2/3：新版"今日攻破"进度条
        # 小宝箱，旧 宝箱1-4 大宝箱图案已失配——run23 实测只误中 2 个且未领到。
        # 阈值 0.7：可领取状态实测 0.998+，已领取状态 0.618——自动跳过已领宝箱）
        self._claim_chests(
            bot, ("演武宝箱1", "演武宝箱2", "演武宝箱3"),
            threshold=0.7,
        )

        # [2026-10-24 用户口径] 宝箱领完后点一次 演武战报 横幅——
        # 战斗随时间逐场结束，奖励分批就绪；点击横幅会自动领取已完结战斗
        # 的奖励，覆盖宝箱阶段尚未就绪的部分
        bot.click_if_found("战报.png", threshold=YW_CLAIM_THRESHOLD)
        bot.sleep(1.0)

        # 回到征战天下主界面
        self._yw_exit(bot)

    def _yw_after_task_hook(self, bot: GameBot) -> None:
        """
        任务间隙演武检测（2026-08-30 用户建议）：每个任务完成后看一眼演武——
        在主界面时进入演武读军令、可发起则发起、达标即领奖，然后返回继续
        下一任务。不在主界面或已结束时静默跳过。异常一律吞掉，不影响引擎。
        演武会话状态未初始化时（征战天下尚未开始过）先惰性重置，
        保证钩子在任意任务后都能安全运行（2026-09-06 run16 实测 AttributeError）。
        """
        try:
            if not hasattr(self, "_yw_done"):
                self._yw_reset()
            if self._yw_done:
                return
            if not self._on_main_screen(bot):
                return
            logger.info("任务间隙演武检测")
            self._yw_round(bot, exit_after=True)
            self._yw_claim_if_won(bot)
        except Exception as e:
            logger.warning(f"任务间隙演武检测异常（忽略，继续后续任务）: {e!r}")

    def _yw_claim_if_won(self, bot: GameBot) -> None:
        """
        达标即领奖：演武判定成功（_yw_won）且未领奖时，立即进入演武界面领奖。
        由主流程在每次演武调用后调用，与急报/体力清解耦，不等后续任务。
        战果消化由 两次确认的间隔门控（YW_LOW_OPP_CONFIRM_GAP）保证：
        第二次 ≤1 确认距首次已隔足够时间，战斗中已消化完毕，无需额外等待。
        """
        if self._yw_won and not self._yw_claimed:
            if self._yw_enter(bot):
                self._yw_claim(bot)
                self._yw_claimed = True
                logger.info("演武：判定成功后已立即领奖")
            else:
                _warn("演武：领奖无法进入演武界面，跳过")
