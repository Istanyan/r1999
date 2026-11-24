"""
三国杀游戏脚本 — 启动清理 + 成长模块 + 日常任务。

由原 games/sanguosha.py 拆分重构为 games/sanguosha/ 包后，
本文件保留主类 SanguoshaScript，聚合各功能 Mixin 并保持对外接口不变
（main.py 的 GAME_REGISTRY 与 gui.py 的兼容性由 __init__.py 保证）。
"""

import time
from typing import Callable, List

from core.task import Task
from core.game_script import GameScript

from .constants import (
    logger,
    HOOK_LOSS_TIMEOUT as _HOOK_LOSS_TIMEOUT,
    FISHING_IDLE_POLL as _FISHING_IDLE_POLL,
    ENERGY_COOLDOWN as _ENERGY_COOLDOWN,
)
from .startup import StartupMixin
from .growth import GrowthMixin
from .zhengzhan import ZhengzhanMixin
from .yanwu import YanwuMixin
from .fishing import FishingMixin
from .cake import CakeMixin
from .jiangjun import JiangjunMixin


def _timed_task(name: str, fn: Callable) -> Callable:
    """
    任务计时包装：统一输出「任务完成/未完成 + 耗时」；
    异常时输出异常原因（不吞异常，交由引擎处理）。
    """
    def wrapper(bot):
        t0 = time.perf_counter()
        try:
            ok = fn(bot)
        except Exception as exc:
            logger.info(
                f"任务「{name}」异常结束: {exc!r}"
                f"（耗时 {time.perf_counter() - t0:.1f}s）"
            )
            raise
        logger.info(
            f"任务「{name}」{'完成' if ok else '未完成'}"
            f"，耗时 {time.perf_counter() - t0:.1f}s"
        )
        return ok
    return wrapper


class SanguoshaScript(
    StartupMixin,
    GrowthMixin,
    ZhengzhanMixin,
    YanwuMixin,
    FishingMixin,
    CakeMixin,
    JiangjunMixin,
    GameScript,
):
    """三国杀日常脚本。"""

    name = "sanguosha"

    # 鱼脱钩判定：收线+蓄力均消失，超过该秒数仍未恢复 → 判脱钩终止本次钓鱼。
    # 该窗口需覆盖终结一击的 QTE 恢复时间（实测 5s 仍偏短，整体放宽到 8s）。
    HOOK_LOSS_TIMEOUT = _HOOK_LOSS_TIMEOUT
    # 收线/蓄力均消失时的重新检测间隔（秒），越小收线重现后恢复越快
    FISHING_IDLE_POLL = _FISHING_IDLE_POLL
    # 蓄力冷却：蓄力不可见后暂停收线的额外缓冲（秒）。越小越接近"一直点"
    ENERGY_COOLDOWN = _ENERGY_COOLDOWN

    def __init__(self):
        """会话级状态。"""
        self._fishing_count = 0
        self._fishing_coins = 0  # 累计钓获鱼币

    def after_task_hook(self, bot):
        """任务间隙钩子——引擎在每个一次性任务完成后调用（用户建议：
        任务完成后插入演武检测，看看完没完）。实现为演武检测。"""
        self._yw_after_task_hook(bot)

    def get_tasks(self) -> List[Task]:
        # 2026-09-06 用户要求：征战天下一定要在成长前（急报战斗/体力清
        # 产出的成长类奖励需要在成长模块里领取，顺序反了会漏领一天）
        return [
            # ── 征战天下（三合一：演武 + 急报 + 体力清，必须在成长前） ──
            Task("征战天下", callback=_timed_task("征战天下", self.zhengzhan_task1)),

            # ── 成长模块（复合流程：成长+那年今日+我要奖励+浇水+元宝） ──
            Task("成长模块", callback=_timed_task("成长模块", self.growth_flow)),

            # ── 蛋糕小游戏（每日最多 2 局，领奖成功即完成）──
            Task("蛋糕小游戏", callback=_timed_task("蛋糕小游戏", self.cake_task)),

            # ── 合成大将军小游戏 ──
            Task("合成大将军", callback=_timed_task("合成大将军", self.jiangjun_game)),

            # ── 钓鱼小游戏 ──
            Task("钓鱼小游戏", callback=_timed_task("钓鱼小游戏", self.fishing_game)),

            # ── 在线时长奖励（重复触发，冷却 120 秒） ──
            Task("在线礼包", "zaixianlibao.png",
                 repeat=True, threshold=0.85, cooldown=120),
        ]
