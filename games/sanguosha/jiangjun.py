"""
三国杀脚本 — 合成大将军小游戏模块（合成大西瓜类）。

流程：进入百趣闲市 → 合成大将军 → 开始游戏1 → 锚定定位1/定位2
→ 主循环低频点击（警戒线出现切换落点，10s 冷却；检测到退出则退出）
→ 领奖1（0.99 阈值）有则 领奖1/2/3 并返回百趣闲市，无则重开（最多 3 局）
→ 第 3 局仍无领奖1 则退回百趣闲市并返回失败。

与 cake.py 的 CakeMixin 同构：Mixin 只提供方法，不持有持久状态；
所有常量收敛到 constants.py（JJ_ 前缀），鼠标操作一律走 bot 高层 API
（内部已注入窗口偏移），等待统一使用 bot.sleep（可中断）。
"""

import time
from typing import Dict, List, Optional, Tuple

from core.game_bot import GameBot

from .constants import (
    logger, _warn,
    JJ_ENTER_THRESHOLD, JJ_ANCHOR_THRESHOLD, JJ_ANCHOR_RETRIES,
    JJ_START_RETRIES, JJ_CLICK_INTERVAL, JJ_SWITCH_COOLDOWN,
    JJ_PLAY_TIMEOUT, JJ_CLAIM_THRESHOLD, JJ_MAX_ROUNDS, JJ_RETURN_MAX,
)

# 进入导航链路：活动场 → 百趣闲市 → 合成大将军
_JJ_NAV_TEMPLATES: List[Tuple[str, str]] = [
    ("活动场.png", "活动场"),
    ("百趣闲市.png", "百趣闲市"),
]


class JiangjunMixin:
    """合成大将军（合成大西瓜类小游戏）。"""

    # ── 进入 ──────────────────────────────────────
    def _jj_enter(self, bot: GameBot) -> bool:
        """
        进入合成大将军。

        已检测到百趣闲市标志（节节高升）→ 直接点 合成大将军；
        否则走 活动场 → 百趣闲市 → 合成大将军（若当前不在主界面先返回主界面）。
        """
        # 已在百趣闲市：直接点入口
        if bot.is_present("节节高升.png", threshold=JJ_ENTER_THRESHOLD):
            if bot.click_if_found("合成大将军.png", threshold=JJ_ENTER_THRESHOLD):
                bot.sleep(1.0)
                return True
            _warn("合成大将军：已在百趣闲市但未找到合成大将军入口")
            return False

        # 不在百趣闲市：确保在主界面再走导航链路
        if not bot.is_present("活动场.png", threshold=JJ_ENTER_THRESHOLD):
            if not self._return_to_main(bot):
                _warn("合成大将军：返回主界面失败")
                return False

        for template, name in _JJ_NAV_TEMPLATES:
            found = self._wait_found(bot, template, JJ_ENTER_THRESHOLD)
            if found is None:
                _warn(f"合成大将军：未找到 {name}（{template}）")
                return False
            bot.click(found[0], found[1])
            bot.sleep(1.0)  # 等待页面切换

        found = self._wait_found(bot, "合成大将军.png", JJ_ENTER_THRESHOLD)
        if found is None:
            _warn("合成大将军：未找到合成大将军入口")
            return False
        bot.click(found[0], found[1])
        bot.sleep(1.0)
        return True

    # ── 开始游戏 + 锚定 ──────────────────────────
    @staticmethod
    def _jj_retry_click(
        bot: GameBot,
        template: str,
        max_retries: int = JJ_START_RETRIES,
        threshold: float = JJ_ENTER_THRESHOLD,
    ) -> bool:
        """带重试上限的点击，成功即返回。"""
        for attempt in range(max_retries):
            if bot.click_if_found(template, threshold=threshold):
                return True
            if attempt < max_retries - 1:
                bot.sleep(0.5)
        return False

    def _jj_anchor(self, bot: GameBot) -> Optional[Dict[str, Tuple[int, int]]]:
        """
        一次性锚定 定位1/定位2 的中心坐标（窗口相对坐标）。
        每个定位点最多重试 JJ_ANCHOR_RETRIES 次；任一失败返回 None。
        """
        anchors: Dict[str, Tuple[int, int]] = {}
        for name in ("定位1", "定位2"):
            region = None
            for _ in range(JJ_ANCHOR_RETRIES):
                region = bot.find_region(f"{name}.png", threshold=JJ_ANCHOR_THRESHOLD)
                if region is not None:
                    break
                bot.sleep(0.5)
            if region is None:
                _warn(f"合成大将军：锚定 {name} 失败（已重试 {JJ_ANCHOR_RETRIES} 次）")
                return None
            anchors[name] = (int(region[0] + region[2] / 2), int(region[1] + region[3] / 2))
        logger.info(
            f"合成大将军锚点已定位: 定位1={anchors['定位1']}, 定位2={anchors['定位2']}"
        )
        return anchors

    # ── 主循环 ──────────────────────────────────
    def _jj_play_loop(self, bot: GameBot, anchors: Dict[str, Tuple[int, int]]) -> str:
        """
        主循环：默认从定位1 低频点击（JJ_CLICK_INTERVAL）；
        警戒线「从无到有」且不在冷却期 → 切换落点到另一个定位点并进入 10s 冷却；
        检测到退出 → 点击退出并结束主循环。

        返回结束原因：'exit'（点击退出）或 'timeout'（超时保护）。
        """
        current: str = "定位1"
        switch_until: float = 0.0
        alarm_prev: bool = False
        loop_start: float = time.time()

        while True:
            # 总时长保护：明确结束信号之一
            if time.time() - loop_start > JJ_PLAY_TIMEOUT:
                logger.info("合成大将军：主循环超时保护退出")
                return "timeout"

            # 已离开对局守卫：对局结束/次数用尽后界面回到入口页或百趣闲市，
            # 此继续按旧锚点盲点会点到任意 UI（2026-08-30 run10 实测点开
            # 货币"+"→体力商店→购买确认框→商店页强制竖屏渲染，横屏模板全失效）
            if (bot.is_present("开始游戏1.png", threshold=JJ_ENTER_THRESHOLD)
                    or bot.is_present("节节高升.png", threshold=JJ_ENTER_THRESHOLD)):
                logger.info("合成大将军：已离开对局界面，结束主循环")
                return "exit"

            # 领奖检测优先于退出：分数达标弹出结算/领奖页时，页面上同时有
            # 退出按钮，先点退出会直接关掉领奖页导致"达标不领奖先返回"
            # （2026-08-30 用户实测）。看到领奖页立即停手，交由上层领奖。
            if bot.is_present("领奖1.png", threshold=JJ_CLAIM_THRESHOLD):
                logger.info("合成大将军：结算领奖页出现，结束对局进入领奖")
                return "claim"

            # 退出检测：不参与冷却，始终响应
            if bot.is_present("退出.png", threshold=JJ_ENTER_THRESHOLD):
                bot.click_if_found("退出.png", threshold=JJ_ENTER_THRESHOLD)
                logger.info("合成大将军：检测到退出，点击退出")
                return "exit"

            now: float = time.time()
            alarm: bool = bot.is_present("警戒线.png", threshold=JJ_ENTER_THRESHOLD)
            # 警戒线从无到有（上升沿）且不在冷却期 → 切换落点
            if alarm and not alarm_prev and now >= switch_until:
                current = "定位2" if current == "定位1" else "定位1"
                switch_until = now + JJ_SWITCH_COOLDOWN
                logger.info(
                    f"合成大将军：警戒线出现，切换落点至 {current}"
                    f"（冷却 {JJ_SWITCH_COOLDOWN:.0f}s）"
                )
            alarm_prev = alarm

            # 低频点击当前落点（对局页锚点经模板锚定确认,显式放行顶栏防护——
            # 定位2 的 y≈67 落在顶栏区但对局页该区域是合法游戏区）
            cx, cy = anchors[current]
            bot.click(cx, cy, allow_danger=True)
            bot.sleep(JJ_CLICK_INTERVAL)

    # ── 领奖 ──────────────────────────────────
    def _jj_click_leftmost(self, bot: GameBot, template: str, threshold: float) -> bool:
        """点击模板最左侧的匹配位置（一左一右并存时只点左边）。"""
        matches = bot.find_all(template, threshold=threshold)
        if not matches:
            return False
        left = min(matches, key=lambda m: m[0])  # center_x 最小即最左
        bot.click(left[0], left[1])
        logger.info(
            f"合成大将军：点击最左侧 '{template}' → ({left[0]}, {left[1]}) "
            f"置信度 {left[2]:.3f}"
        )
        return True

    def _jj_claim(self, bot: GameBot) -> None:
        """依次点击 领奖1 → 领奖2(取最左) → 领奖3。"""
        bot.click_if_found("领奖1.png", threshold=JJ_CLAIM_THRESHOLD)
        bot.sleep(0.8)
        self._jj_click_leftmost(bot, "领奖2.png", threshold=JJ_ENTER_THRESHOLD)
        bot.sleep(0.8)
        bot.click_if_found("领奖3.png", threshold=JJ_ENTER_THRESHOLD)
        bot.sleep(0.8)

    def _jj_back_to_market(self, bot: GameBot) -> bool:
        """
        循环点 返回1 直到检测到百趣闲市标志（节节高升），上限 JJ_RETURN_MAX 次。
        每轮优先检测 退出：主循环超时强制退出后，游戏结束模态（游戏结束+得分+退出）
        会吞掉返回1 的点击（2026-08-29 run5 实测卡死在此页），点退出关闭它。
        """
        for _ in range(JJ_RETURN_MAX):
            if bot.is_present("节节高升.png", threshold=JJ_ENTER_THRESHOLD):
                return True
            if bot.click_if_found("退出.png", threshold=JJ_ENTER_THRESHOLD):
                bot.sleep(1.0)
                continue
            bot.click_if_found("返回1.png", threshold=JJ_ENTER_THRESHOLD)
            bot.sleep(0.8)
        _warn("合成大将军：点击返回1 后未检测到百趣闲市标志（节节高升）")
        return False

    # ── 主流程 ──────────────────────────────────
    def jiangjun_game(self, bot: GameBot) -> bool:
        """
        合成大将军主流程。

        最多 JJ_MAX_ROUNDS 局；每局：开始游戏1 → 锚定 → 主循环 → 领奖检测。
        领奖1（0.99 阈值）命中：依次领奖1/2/3 后返回百趣闲市，返回 True。
        局内未领奖：单局返回百趣闲市（JJ_MAX_ROUNDS=1），重试由引擎兜底。
        进入失败时先 dismiss_popups 自救回主界面再试一次
        （引擎重试时界面可能遗留在百趣闲市等中间页面，
        2026-09-06 run16 实测重试三次全部"未找到活动场"1.4s 速败）。
        """
        logger.info("=== 合成大将军 ===")
        t0 = time.perf_counter()

        if not self._jj_enter(bot):
            logger.info("进入合成大将军失败，先清理弹窗/返回主界面后重试")
            self.dismiss_popups(bot)
            if not self._jj_enter(bot):
                logger.error(
                    f"合成大将军未开始（进入失败），耗时 {time.perf_counter() - t0:.1f}s"
                )
                return False

        claimed: bool = False
        rounds: int = 0
        reason: str = ""

        for rounds in range(1, JJ_MAX_ROUNDS + 1):
            # 点 开始游戏1（2026-11-04：入口点击后页面可能处于过渡动画，
            # 原 3 次×0.5s 窗口会扑空 → 先渐进等待 ~6s 再判定失败）
            found = self._wait_found(bot, "开始游戏1.png", JJ_ENTER_THRESHOLD,
                                     retries=5, interval=1.0)
            if found is None:
                reason = f"第{rounds}局未找到开始游戏1（页面加载慢或当日次数已用尽）"
                break
            if not self._jj_retry_click(bot, "开始游戏1.png"):
                reason = f"第{rounds}局开始游戏1 点击未生效"
                break
            bot.sleep(1.0)

            # 锚定定位1/定位2
            anchors = self._jj_anchor(bot)
            if anchors is None:
                reason = f"第{rounds}局锚定定位失败"
                break

            # 主循环（低频点击 / 警戒线切换 / 领奖页停手 / 退出）
            loop_reason = self._jj_play_loop(bot, anchors)
            bot.sleep(1.0)

            # 领奖检测（高置信度 0.99；主循环看到领奖页已停手，此处应能命中）
            if bot.is_present("领奖1.png", threshold=JJ_CLAIM_THRESHOLD):
                claimed = True
                reason = f"第{rounds}局领奖成功"
                self._jj_claim(bot)
                self._jj_back_to_market(bot)
                break

            reason = f"第{rounds}局未检测到领奖1（主循环终止原因={loop_reason}）"

        if not claimed:
            # 第 3 局仍无领奖1：点返回1 退回百趣闲市
            self._jj_back_to_market(bot)

        logger.info(
            f"合成大将军结束：完成={claimed}，执行 {rounds} 局，"
            f"终止原因={reason}，耗时 {time.perf_counter() - t0:.1f}s"
        )
        return claimed
