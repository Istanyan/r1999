"""
三国杀脚本 — 蛋糕下落小游戏模块。

由原 games/sanguosha.py 拆分而来：
CakeMixin 承载原 SanguoshaScript 的蛋糕小游戏相关类属性与方法
（GOOD_CAKES / BAD_CAKES / CAKE_TEMPLATES / ANCHOR_TEMPLATES /
 _init_cake_anchors / _detect_cake / _get_target_x / cake_game）。
"""

import os
import time
from collections import deque
from typing import Dict, Optional, Tuple

import cv2
import numpy as np

from core.game_bot import GameBot
from core.matcher import _imread

from .constants import (
    CAKE_ANCHOR_THRESHOLD,
    CAKE_AREA_BOTTOM_PAD,
    CAKE_ASPECT_LIMIT,
    CAKE_DETECT_INTERVAL,
    CAKE_EDGE_OFFSET,
    CAKE_END_CONFIRM_COUNT,
    CAKE_END_IDLE_TIMEOUT,
    CAKE_END_MARK,
    CAKE_ENTER_MARK,
    CAKE_EXIT_INTERVAL,
    CAKE_EXIT_TIMEOUT,
    CAKE_MAX_PLAYS,
    CAKE_CLAIM_ENTRY_THRESHOLD,
    CAKE_CLAIM_GET_THRESHOLD,
    CAKE_CLAIM_CONFIRM_THRESHOLD,
    CAKE_CLAIM_TIMEOUT,
    CAKE_CLAIM_INTERVAL,
    CAKE_FC_RETRY_INTERVAL,
    CAKE_FC_RETRY_TIMES,
    CAKE_GAME_TIMEOUT,
    CAKE_MAIN_MARK,
    CAKE_MATCH_THRESHOLD,
    CAKE_MISS_Y_OFFSET,
    CAKE_NAV_CLICK_WAIT,
    CAKE_NAV_FLOW,
    CAKE_NAV_INTERVAL,
    CAKE_NAV_TIMEOUT,
    CAKE_RELEASE_WAIT,
    CAKE_RELEASE_X_TOLERANCE,
    CAKE_RELEASE_DELAY,
    CAKE_RELEASE_T_EXTRA,
    CAKE_TARR_MAX_PRED_ERR,
    CAKE_TRACK_INTERVAL,
    CAKE_TRACK_DT_MIN,
    CAKE_TRACK_MAX_FRAMES,
    CAKE_TRACK_MAX_FRAMES_BAD,
    CAKE_TRACK_MAX_JUMP,
    CAKE_JUMP_MAX_PRED_ERR,
    CAKE_VX_STABLE_DELTA,
    logger,
)


class CakeMixin:
    """蛋糕下落小游戏：锚点定位 + 追踪释放。"""

    # 好蛋糕 → 中间落点（中心对齐堆叠）
    GOOD_CAKES = {"mdg", "xdg", "ddg"}
    # 坏蛋糕 → 边缘落点（不能叠在好蛋糕上）
    BAD_CAKES = {"hdg", "zd"}

    # 五种蛋糕模板名
    CAKE_TEMPLATES = ["mdg", "xdg", "ddg", "hdg", "zd"]

    # 一次性地标模板
    ANCHOR_TEMPLATES = ["zuo", "you", "fc", "zdg"]

    # ── 快速匹配（模板缓存 + up_area 区域截图）──
    _tpl_cache: Dict[str, Optional[np.ndarray]] = {}

    def _tpl(self, bot: GameBot, name: str) -> Optional[np.ndarray]:
        """读取并缓存模板（BGR），避免每帧 find_region 重复读盘。"""
        if name in self._tpl_cache:
            return self._tpl_cache[name]
        path = bot._template_path(f"{name}.png")
        tpl = _imread(path)
        self._tpl_cache[name] = tpl
        return tpl

    def _match_in_area(
        self, bot: GameBot, anchors: Dict, kind: str, threshold: float
    ) -> Optional[Tuple[int, int, int, int, float]]:
        """
        在 up_area 区域内匹配指定蛋糕模板（区域截图 + 模板缓存）。
        返回 (lx, ly, w, h, conf) 窗口相对坐标，未命中返回 None。
        比 bot.find_region（全屏截图 + 每帧读盘）快 2~4 倍，用于高频追踪。
        """
        za = anchors["up_area"]
        za_lx, za_ly, za_w, za_h = int(za[0]), int(za[1]), int(za[2]), int(za[3])
        tpl = self._tpl(bot, kind)
        if tpl is None:
            return None
        h, w = tpl.shape[:2]
        if w > za_w or h > za_h:
            return None
        img = bot.screenshot.capture_region(za_lx, za_ly, za_w, za_h)  # BGR
        res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
        _, maxv, _, maxloc = cv2.minMaxLoc(res)
        if maxv < threshold:
            return None
        return (za_lx + maxloc[0], za_ly + maxloc[1], w, h, float(maxv))

    # ── 阶段0：进入导航 ──────────────────────────

    def _wait_click(self, bot: GameBot, template: str, timeout: float = CAKE_NAV_TIMEOUT) -> bool:
        """等待模板出现并点击（带超时重试），返回是否成功。"""
        return bot.wait_and_click(
            template,
            timeout=timeout,
            interval=CAKE_NAV_INTERVAL,
            threshold=CAKE_MATCH_THRESHOLD,
        )

    def _enter_cake_game(self, bot: GameBot) -> bool:
        """
        进入蛋糕小游戏（阶段0 导航）。

        三条路径汇聚到 普通模式 → 继续：
        路径0：已在蛋糕模式选择界面（普通模式可见）→ 直接走汇聚流；
        路径A：已在百趣闲市（检测到节节高升）→ 直接点击进入；
        路径B：主界面 → 活动场 → 百趣闲市 → 节节高升；
        路径C：已在蛋糕游戏内（检测到 zdg 释放按钮）→ 直接返回，跳过入口导航。
        """
        # 路径C：已在蛋糕小游戏内（zdg 释放按钮可见），跳过入口导航
        if bot.is_present("zdg.png", threshold=CAKE_ANCHOR_THRESHOLD - 0.05):
            logger.info("已在蛋糕小游戏内，跳过入口导航")
            return True
        # 路径0：已在蛋糕小游戏选择界面（普通模式可见）
        if bot.is_present("普通模式.png", threshold=CAKE_MATCH_THRESHOLD):
            logger.info("已在蛋糕小游戏选择界面，跳过入口导航")
        # 路径A：已在百趣闲市界面
        elif bot.is_present(CAKE_ENTER_MARK, threshold=CAKE_MATCH_THRESHOLD):
            logger.info("已在百趣闲市，点击节节高升进入")
            bot.click_if_found(CAKE_ENTER_MARK, threshold=CAKE_MATCH_THRESHOLD)
            bot.sleep(CAKE_NAV_CLICK_WAIT)
        else:
            # 路径B：从主界面进入
            if not bot.is_present(CAKE_MAIN_MARK, threshold=CAKE_MATCH_THRESHOLD):
                logger.warning("未检测到主界面标志（chengzhang），尝试直接导航")
            for tpl in CAKE_NAV_FLOW:
                if not self._wait_click(bot, tpl):
                    logger.error(f"导航点击失败: {tpl}")
                    return False
                bot.sleep(CAKE_NAV_CLICK_WAIT)

        # 汇聚流：普通模式 → 继续
        if not self._wait_click(bot, "普通模式.png"):
            logger.error("点击普通模式失败")
            return False
        bot.sleep(CAKE_NAV_CLICK_WAIT)
        # [2026-11-17 用户口径 + 实跑截图] 点普通模式后弹出"单价：100银币/
        # 限购10/100"的体力购买框，说明本活动不能直接开局，任务已失败；
        # 这里直接终止，不再等 继续.png，也不再跑第二局。
        if bot.is_present("蛋糕体力购买.png", threshold=0.85):
            logger.error("蛋糕：普通模式触发体力购买弹窗，任务失败，不再重试")
            self._cake_fatal = True
            return False
        if not self._wait_click(bot, "继续.png"):
            # 兜底：弹窗可能比 1.0s 晚出现，等继续超时后再查一次购买框，
            # 避免慢弹窗被当成普通“继续失败”而进入第二局。
            if bot.is_present("蛋糕体力购买.png", threshold=0.85):
                logger.error("蛋糕：普通模式触发体力购买弹窗，任务失败，不再重试")
                self._cake_fatal = True
            else:
                logger.error("点击继续失败")
            return False
        bot.sleep(CAKE_NAV_CLICK_WAIT)
        logger.info("已进入蛋糕小游戏")
        return True

    def _init_cake_anchors(self, bot: GameBot) -> Optional[Dict]:
        """
        启动时一次性定位所有地标（窗口相对坐标）。
        返回包含各锚点坐标的字典，失败返回 None。
        fc 为悬浮窗锚点（新模板，代替原 fc1/fc2 互备方案），以 fc 的 y 轴作为释放线。
        """
        anchors = {}

        # ── 固定锚点：zuo / you / zdg ──
        for name in ("zuo", "you", "zdg"):
            result = bot.find_region(f"{name}.png", threshold=CAKE_ANCHOR_THRESHOLD)
            if result is None:
                # 进入动画未完成时地标可能延迟出现，重试（同 fc 的等待参数）
                for _ in range(CAKE_FC_RETRY_TIMES):
                    bot.sleep(CAKE_FC_RETRY_INTERVAL)
                    result = bot.find_region(f"{name}.png", threshold=CAKE_ANCHOR_THRESHOLD)
                    if result:
                        break
            if result is None:
                logger.error(f"无法定位地标: {name}")
                return None
            anchors[name] = result

        # ── 悬浮窗锚点：fc（新模板）──
        fc_result = bot.find_region("fc.png", threshold=CAKE_ANCHOR_THRESHOLD)
        if fc_result is None:
            logger.info("等待通知消失以定位 fc...")
            for _ in range(CAKE_FC_RETRY_TIMES):
                bot.sleep(CAKE_FC_RETRY_INTERVAL)
                fc_result = bot.find_region("fc.png", threshold=0.80)
                if fc_result:
                    break

        if fc_result is None:
            logger.error("无法定位悬浮窗锚点: fc")
            return None
        anchors["fc"] = fc_result

        # ── 中心落点：fc 中心 x（好蛋糕落中间）──
        anchors["center_x"] = fc_result[0] + fc_result[2] / 2

        # ── 边缘落点：边缘1 / 边缘2（用户更新的精确边缘标记）──
        # 取两模板中心 x，小的为左边缘、大的为右边缘
        edge_xs = []
        for name in ("边缘1", "边缘2"):
            r = bot.find_region(f"{name}.png", threshold=CAKE_ANCHOR_THRESHOLD)
            if r is None:
                # 进入动画未完成时边缘标记可能延迟出现，重试
                for _ in range(CAKE_FC_RETRY_TIMES):
                    bot.sleep(CAKE_FC_RETRY_INTERVAL)
                    r = bot.find_region(f"{name}.png", threshold=CAKE_ANCHOR_THRESHOLD)
                    if r:
                        break
            if r is None:
                logger.error(f"无法定位边缘标记: {name}")
                return None
            edge_xs.append(r[0] + r[2] / 2)
        edge_xs.sort()
        anchors["left_x"] = edge_xs[0]
        anchors["right_x"] = edge_xs[1]
        # 释放线：以 fc 的 y 轴（模板中心 y）作为释放线
        anchors["release_y"] = fc_result[1] + fc_result[3] / 2
        # zdg 按钮中心：定位一次即记住，释放时直接点固定位置，不每次模板匹配
        zdg = anchors["zdg"]
        anchors["zdg_center"] = (zdg[0] + zdg[2] / 2, zdg[1] + zdg[3] / 2)

        # ── 上方区域：zuo 与 you 之间、边界标记上方的矩形 ──
        # （不依赖"上方区域"模板，由 zuo/you 动态计算，避免模板过宽匹配失败）
        up_lx = anchors["zuo"][0]
        up_rx = anchors["you"][0] + anchors["you"][2]
        up_ly = 0.0  # 游戏区域顶部
        # 下边界：边界标记 y + 外扩量。蛋糕模板半高最大 ~64px，
        # 若只到边界标记 y（185），区域匹配会裁掉蛋糕底部导致匹配分暴跌
        up_ry = anchors["zuo"][1] + CAKE_AREA_BOTTOM_PAD
        anchors["up_area"] = (up_lx, up_ly, up_rx - up_lx, up_ry - up_ly)

        logger.info(
            f"蛋糕锚点已定位 (悬浮窗=fc): center_x={anchors['center_x']:.0f}, "
            f"left_x={anchors['left_x']:.0f}, right_x={anchors['right_x']:.0f}, "
            f"release_y={anchors['release_y']:.0f}, "
            f"zdg_center=({anchors['zdg_center'][0]:.0f},{anchors['zdg_center'][1]:.0f}), "
            f"up_area=({up_lx:.0f},{up_ly:.0f},{up_rx - up_lx:.0f},{up_ry - up_ly:.0f})"
        )
        return anchors

    def _detect_cake(self, bot: GameBot, anchors: Dict) -> Optional[Tuple[str, float, float]]:
        """
        在 up_area 区域内匹配五种蛋糕（区域截图 + 模板缓存，比全屏 find_region 快数倍）。
        返回 (kind, center_x_w, center_y_w) — 均为窗口相对坐标。
        选择策略：优先返回距离其对应 target 最近的蛋糕（而非置信度最高），
        避免远处的高置信度蛋糕阻塞近处可释放的蛋糕。
        """
        za = anchors["up_area"]
        za_lx, za_ly, za_w, za_h = int(za[0]), int(za[1]), int(za[2]), int(za[3])
        img = bot.screenshot.capture_region(za_lx, za_ly, za_w, za_h)  # BGR

        matches = []  # (dist_to_target, conf, kind, cx, cy)

        for kind in self.CAKE_TEMPLATES:
            tpl = self._tpl(bot, kind)
            if tpl is None:
                continue
            h, w = tpl.shape[:2]
            if w > za_w or h > za_h:
                continue
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            _, maxv, _, maxloc = cv2.minMaxLoc(res)
            if maxv < CAKE_MATCH_THRESHOLD:
                continue
            # 宽高比过滤：超过 CAKE_ASPECT_LIMIT 视为通知文字横幅
            aspect = w / h if h > 0 else 999
            if aspect > CAKE_ASPECT_LIMIT:
                continue
            cx = za_lx + maxloc[0] + w / 2
            cy = za_ly + maxloc[1] + h / 2
            target_x = self._get_target_x(kind, anchors)
            dist = abs(cx - target_x)
            matches.append((dist, maxv, kind, cx, cy))

        if not matches:
            return None

        # 按距离 target 排序，优先选最近的可释放蛋糕
        matches.sort(key=lambda m: m[0])
        _, _, kind, cx, cy = matches[0]
        return (kind, cx, cy)

    def _get_target_x(self, kind: str, anchors: Dict) -> float:
        """根据蛋糕种类返回目标落点 x 坐标。坏蛋糕外扩确保落在堆叠外。"""
        if kind in self.GOOD_CAKES:
            return anchors["center_x"]
        if kind == "hdg":
            return anchors["left_x"] - CAKE_EDGE_OFFSET  # 左边缘外扩，避开堆叠
        if kind == "zd":
            return anchors["right_x"] + CAKE_EDGE_OFFSET  # 右边缘外扩，避开堆叠
        return anchors["center_x"]  # 兜底

    def _observe_landing(self, bot: GameBot, kind: str, target_x: float, anchors: Dict) -> None:
        """
        释放后观测蛋糕实际落点（仅 CAKE_TRACE 模式输出 [LAND] 日志），
        用于标定 CAKE_RELEASE_DELAY；非 trace 模式仅按原等待时间休息。
        """
        if not os.environ.get("CAKE_TRACE"):
            bot.sleep(CAKE_RELEASE_WAIT)
            return
        bot.sleep(0.3)
        r2 = bot.find_region(f"{kind}.png", threshold=0.65)
        if r2 is not None:
            lx2, ly2, w2, h2, _conf2 = r2
            cx2 = lx2 + w2 / 2
            cy2 = ly2 + h2 / 2
            up_bottom = anchors["up_area"][1] + anchors["up_area"][3]
            if cy2 > up_bottom:
                logger.debug(
                    f"[LAND] {kind} 落点 x={cx2:.0f} y={cy2:.0f} "
                    f"target={target_x:.0f} 偏差={cx2 - target_x:+.0f}"
                )
        bot.sleep(CAKE_RELEASE_WAIT)

    # ── 观测辅助（CAKE_OBSERVE=1 时启用：血条状态连拍 + 释放前后连拍）──
    def _obs_shot(self, bot: GameBot, tag: str, seq: int = 0) -> None:
        """保存一张观测截图到 CAKE_OBSERVE_DIR（未启用或失败时静默跳过）。"""
        d = os.environ.get("CAKE_OBSERVE_DIR")
        if not d:
            return
        fname = f"{tag}_{seq:04d}_{int(time.monotonic() * 1000)}.png"
        try:
            bot.screenshot.save(os.path.join(d, fname))
        except Exception as exc:
            logger.debug(f"观测截图失败 {fname}: {exc!r}")

    def _release_and_observe(
        self,
        bot: GameBot,
        zdg_x: float,
        zdg_y: float,
        kind: str,
        last_x: float,
        vx: float,
        pred_x: float,
        target_x: float,
        reason: str,
        anchors: Dict,
    ) -> None:
        """
        统一释放入口：点击 zdg →（观测模式）释放前后连拍 → 落点观测。
        连拍用于确认释放瞬间蛋糕真实位置与下落落点，与 [TRACE]/[LAND] 对齐。
        """
        if os.environ.get("CAKE_OBSERVE"):
            for i in range(2):
                self._obs_shot(bot, f"pre_{reason}_{kind}", i)
                bot.sleep(0.05)
        bot.click(zdg_x, zdg_y)
        if os.environ.get("CAKE_OBSERVE"):
            for i in range(10):
                self._obs_shot(bot, f"post_{reason}_{kind}", i)
                bot.sleep(0.08)
        self._observe_landing(bot, kind, target_x, anchors)

    def cake_game(self, bot: GameBot) -> bool:
        """
        蛋糕下落小游戏主流程。

        阶段0 进入导航（百趣闲市/主界面两条路径汇聚到 普通模式→继续）
        阶段1 锚点定位（一次性，记录 fc 位置与释放线，后续不再重复检测）
        阶段2 主循环：只在上方区域检测蛋糕；追踪蛋糕（蛋糕不释放就一直在
              天空水平移动）直到 x 到达目标落点 → 点击 zdg 释放；
              好蛋糕落中间、坏蛋糕（zd/hdg）落屏幕边缘。
        结束：90s 硬超时 或 连续检测不到蛋糕且检测不到 zdg（中断）。
        阶段3 点击 fanhui 返回百趣闲市界面。
        """
        logger.info("=== 蛋糕小游戏启动 ===")
        t0 = time.perf_counter()

        # ── 阶段0：进入导航 ──
        if not self._enter_cake_game(bot):
            logger.error(
                f"进入蛋糕小游戏失败，耗时 {time.perf_counter() - t0:.1f}s"
            )
            return False

        # ── 阶段1：锚点定位（一次性） ──
        anchors = self._init_cake_anchors(bot)
        if anchors is None:
            logger.error("锚点定位失败，跳过蛋糕小游戏")
            return False

        # ── 阶段2：主循环 ──
        game_start = time.time()
        released = 0
        idle_count = 0  # 连续检测到结算页标志计数 → 游戏结束
        idle_start = None  # 连续"无蛋糕且无结算页"起始时间 → 异常中断判定
        last_obs_t = 0.0  # 状态截图节流（0.5s 一张，观测血条与游戏进程）
        end_reason = "unknown"  # 结束性质：timeout_full / settle_full / early_settle / abnormal
        # timeout_full=90s跑满；settle_full=结算页且接近90s（正常完成）；
        # early_settle=结算页但远早于90s（血量耗尽=落点精度不足，需修）；
        # abnormal=既无蛋糕也无结算页超时（弹窗/崩溃等异常中断）

        if os.environ.get("CAKE_OBSERVE"):
            logger.info(f"CAKE_OBSERVE 开启，观测目录: {os.environ.get('CAKE_OBSERVE_DIR')}")
            self._obs_shot(bot, "init")

        while True:
            # 90s 硬超时（跑满整局）
            if time.time() - game_start > CAKE_GAME_TIMEOUT:
                end_reason = "timeout_full"
                logger.info(f"{CAKE_GAME_TIMEOUT:.0f}s 到达，跑满整局。共释放 {released} 个")
                # 保存结算页截图（含得分）
                self._obs_shot(bot, f"settle_timeout_full_r{released}")
                break

            # 状态观测：每 0.5s 一张（血条变化 / 游戏画面）
            if os.environ.get("CAKE_OBSERVE"):
                now_mono = time.monotonic()
                if now_mono - last_obs_t >= 0.5:
                    last_obs_t = now_mono
                    self._obs_shot(bot, f"state_{released}")

            cake = self._detect_cake(bot, anchors)

            # 无蛋糕：区分"提前结算（精度失败）"、"正常结算"与"异常中断"。
            # 1) 结算页标志（"再来一局"）连续命中 → 游戏结束，按用时区分性质：
            #    - 远早于 90s → 血量耗尽提前结束 = 落点精度不足，logger 标红暴露；
            #    - 接近 90s   → 正常完成；
            # 2) 既无蛋糕、也无结算页标志持续超过 CAKE_END_IDLE_TIMEOUT →
            #    判定游戏被中断（弹窗/崩溃/退出），退出防卡死。
            # 不用 zdg 消失判定结束：zdg 模板会匹配到移动中的蛋糕，
            # 蛋糕移出区域/姿态变化时 zdg 检测失败会误判提前退出
            # （run6 的 63s 伪"自然结算"即因此产生）。
            if cake is None:
                if bot.is_present(CAKE_END_MARK, threshold=CAKE_MATCH_THRESHOLD):
                    # === [MODIFY 2026-08-26] 结算页校验增加zdg消失判定 ===
                    # 问题："再来一局"模板在游戏进行中频繁误匹配到左右两侧常驻UI
                    #   （左侧(149,618)置信度0.84-0.86，右侧(1357,615)置信度0.77-0.79），
                    #   当蛋糕短暂不识别（hdg/xdg等）时cake=None，idle_count误累积到3，
                    #   导致游戏被提前误判结束（实际还在进行中）。
                    # 修复：结算页时zdg释放按钮消失，游戏进行中zdg存在。
                    #   检测到"再来一局"后额外校验zdg是否消失，排除误匹配。
                    # 验证：run30日志中游戏进行时"再来一局"误匹配15次，但因蛋糕识别率高
                    #   未触发；今日hdg/xdg不识别时即触发误判结束。
                    if bot.is_present("zdg.png", threshold=CAKE_ANCHOR_THRESHOLD - 0.05):
                        idle_count = 0  # zdg还在 → 游戏进行中 → "再来一局"是误匹配
                    else:
                        idle_count += 1
                    # === [END MODIFY 2026-08-26] ===
                    if idle_count >= CAKE_END_CONFIRM_COUNT:
                        elapsed = time.time() - game_start
                        if elapsed < CAKE_GAME_TIMEOUT - 10.0:
                            end_reason = "early_settle"
                            logger.error(
                                f"检测到结算页，但用时 {elapsed:.0f}s << 90s："
                                f"血量耗尽提前结束 = 落点精度不足！共释放 {released} 个"
                            )
                        else:
                            end_reason = "settle_full"
                            logger.info(
                                f"检测到结算页，用时 {elapsed:.0f}s 接近 90s，"
                                f"正常完成。共释放 {released} 个"
                            )
                        # 保存结算页截图（含得分），用于记录每局最终得分
                        self._obs_shot(bot, f"settle_{end_reason}_r{released}")
                        break
                else:
                    idle_count = 0
                    if idle_start is None:
                        idle_start = time.time()
                    elif time.time() - idle_start > CAKE_END_IDLE_TIMEOUT:
                        end_reason = "abnormal"
                        logger.warning(
                            f"连续 {CAKE_END_IDLE_TIMEOUT:.0f}s 无蛋糕且未进入结算页，"
                            f"判定游戏异常中断，退出防卡死。共释放 {released} 个"
                        )
                        break
                bot.sleep(CAKE_DETECT_INTERVAL)
                continue

            idle_count = 0
            idle_start = None
            kind, cx, cy = cake
            target_x = self._get_target_x(kind, anchors)
            zdg_x, zdg_y = anchors["zdg_center"]  # 记录好的 zdg 按钮位置

            # ── 追踪：蛋糕不释放就一直在天空（上方区域）水平移动，
            #    等 x 到达目标落点 → 点击 zdg 释放（好蛋糕落中间、坏蛋糕落边缘）──
            # 坏蛋糕需横穿到边缘，追踪上限放宽
            max_frames = (
                CAKE_TRACK_MAX_FRAMES_BAD
                if kind in self.BAD_CAKES
                else CAKE_TRACK_MAX_FRAMES
            )
            last_x, last_y = cx, cy
            released_flag = False
            prev_t = None        # 上一帧时间戳（perf_counter，精确计时）
            vx = 0.0             # 蛋糕当前水平速度估算（px/s）
            vx_hist = deque(maxlen=4)  # (t, x) 轨迹队列：最近 3~4 点最小二乘测速，缩短窗口降低折返/减速滞后
            prev_pred = None     # 上一帧预测落点：预测跨越判定用
            last_pred_x = None   # 上一帧算出的 pred_x（跨越判定基准）
            prev_vx = None       # 上一帧 vx：速度稳定性门槛用
            vx_delta = 0.0       # 相邻帧 vx 变化量
            for frame_idx in range(max_frames):
                result = self._match_in_area(bot, anchors, kind, CAKE_MATCH_THRESHOLD)
                if result is None:
                    # 蛋糕消失：按预测落点（含延迟位移）判断是否补点释放
                    pred_x = last_x + vx * CAKE_RELEASE_DELAY
                    if abs(pred_x - target_x) <= CAKE_RELEASE_X_TOLERANCE * 2:
                        logger.debug("蛋糕消失前接近目标，补点释放")
                        self._release_and_observe(bot, zdg_x, zdg_y, kind, last_x,
                                                  vx, pred_x, target_x, "lost", anchors)
                        released += 1
                        released_flag = True
                    break

                lx, ly, w, h, conf = result
                prev_x = last_x
                last_x, last_y = lx + w / 2, ly + h / 2  # 窗口相对坐标

                # 精确帧间隔 → 估算水平速度（蛋糕匀速水平移动）
                now = time.perf_counter()
                vx_hist.append((now, last_x))
                if len(vx_hist) >= 3:
                    t_mean = sum(p[0] for p in vx_hist) / len(vx_hist)
                    x_mean = sum(p[1] for p in vx_hist) / len(vx_hist)
                    den = sum((p[0] - t_mean) ** 2 for p in vx_hist)
                    if den > 1e-9:
                        vx_ls = sum(
                            (p[0] - t_mean) * (p[1] - x_mean) for p in vx_hist
                        ) / den
                        # 残差过滤：最新点与拟合线偏差 > 45px 视为反弹/异常采样，
                        # 清空轨迹重建，避免把反弹前的历史混入新方向测速。
                        # 阈值从25提高到45：高vx蛋糕单帧位移40-50px，原25px
                        # 会导致vx被频繁重置为0，始终无法触发释放。
                        x_pred_line = x_mean + vx_ls * (now - t_mean)
                        if abs(last_x - x_pred_line) > 45:
                            vx_hist.clear()
                            vx_hist.append((now, last_x))
                            vx = 0.0
                        else:
                            vx = vx_ls
                elif len(vx_hist) == 2:
                    # 仅 2 个点：直接差分（帧间隔精确计时），起步阶段不空白
                    (t0_, x0_), (t1_, x1_) = vx_hist
                    dt = t1_ - t0_
                    if dt > CAKE_TRACK_DT_MIN:  # 帧间隔下限：过短视为匹配噪声，不更新 vx
                        vx = (x1_ - x0_) / dt
                prev_t = now

                # 速度稳定性：相邻帧 vx 变化量。起步加速（521→770）与折返加速
                # （-15→-550）阶段 pred 不可靠，须等 vx 稳定后再判释放。
                vx_delta = abs(vx - prev_vx) if prev_vx is not None else 0.0
                prev_vx = vx

                if os.environ.get("CAKE_TRACE"):
                    logger.debug(
                        f"[TRACE] {kind} f={frame_idx} x={last_x:.1f} y={last_y:.1f} "
                        f"vx_ls={vx:.0f} hist={len(vx_hist)}"
                    )

                # 预测落点 = 当前位置 + 释放延迟内的位移
                # （检测截图/匹配 + 点击 + 游戏响应的总耗时里蛋糕仍在移动，
                #   提前在预测落点到达目标时释放，让蛋糕在下落时正好滑到落点）
                pred_x = last_x + vx * CAKE_RELEASE_DELAY
                # 上一帧预测位置：直接复用上一帧算出的 pred_x。
                # 若用当前 vx 重算 prev_pred，vx 变化会把 prev_pred 拉偏，
                # 导致 pred 跳过容差窗时 pcross 跨越判定失真（f=25 pred=811.5
                # 差 0.5px 未命中，f=26 pred=768 已过窗，中间未被捕捉）。
                prev_pred = last_pred_x
                last_pred_x = pred_x

                # 首帧的 prev_x 来自外层检测（间隔一次全屏匹配，速度不可靠），
                # vx 尚未算出（hist=1），只按实际位置判断一次，不做预测/跨越判定。
                if frame_idx == 0:
                    if abs(last_x - target_x) <= CAKE_RELEASE_X_TOLERANCE:
                        logger.debug(
                            f"释放 {kind} @ x={last_x:.0f} y={last_y:.0f} "
                            f"target_x={target_x:.0f}"
                        )
                        self._release_and_observe(bot, zdg_x, zdg_y, kind, last_x,
                                                  vx, last_x, target_x, "f0", anchors)
                        released += 1
                        released_flag = True
                        break
                    bot.sleep(CAKE_TRACK_INTERVAL)
                    continue

                # 速度稳定性门槛：仅拦截方向反转（折返加速，pred 方向完全错）。
                # 同向加速（起步建立 521→770）放行：t_arr 用当前 vx 计算，
                # 加速期 t_arr 偏大不会提前释放，vx 稳定后自然触发，不产生错误预测。
                if (
                    vx_delta > CAKE_VX_STABLE_DELTA
                    and vx != 0.0
                    and prev_vx is not None
                    and vx * prev_vx < 0
                ):
                    # 折返瞬间：水平速度刚从 0 换向，落点≈当前位置。
                    # 高 vx 蛋糕（xdg 等 600+px/s）预测架构释放点在视野外无法命中，
                    # 若折返点恰好贴近 target，此刻释放是唯一低风险机会。
                    if abs(last_x - target_x) <= 80:
                        logger.debug(
                            f"释放 {kind}（折返点）@ x={last_x:.0f} vx={vx:.0f} "
                            f"target_x={target_x:.0f}"
                        )
                        self._release_and_observe(
                            bot, zdg_x, zdg_y, kind, last_x,
                            0.0, last_x, target_x, "turn", anchors,
                        )
                        released += 1
                        released_flag = True
                        break
                    # === [MODIFY 2026-08-25] 反弹后清空vx_hist重建 ===
                    # 问题：蛋糕碰墙反弹时 vx 方向反转（如 +1015→-15），
                    #   但 vx_hist 仍保留反弹前的高速点，最小二乘拟合出的 vx
                    #   严重失真（偏差可达 50%+），导致高vx蛋糕落点偏 70~158px，
                    #   血量快速耗尽（run29 仅 49 个/69s 提前结束）。
                    # 修复：方向反转且不在折返点释放时，清空 vx_hist 从当前帧重建。
                    #   反弹后蛋糕从边缘横穿到中间需 ~0.45s≈7 帧，
                    #   足够积累 3~4 个干净点再做精准释放，不会错过窗口。
                    # 验证：run30 释放 60 个得 1440 分（vs run27 同 60 个仅 1075 分），
                    #   精度提升 34%，完整跑完 90s 正常结束。
                    logger.debug(
                        f"[BOUNCE] {kind} 反弹清空vx_hist "
                        f"x={last_x:.0f} prev_vx={prev_vx:.0f} vx={vx:.0f}"
                    )
                    vx_hist.clear()
                    vx_hist.append((now, last_x))
                    vx = 0.0
                    prev_vx = None
                    bot.sleep(CAKE_TRACK_INTERVAL)
                    continue
                    # === [END MODIFY 2026-08-25] ===

                # 到达时间判定：蛋糕到 target 的预估时间 t_arr=|x-target|/|vx|。
                # pred 命中窗口 = ±15/|vx| 秒（850px/s 时仅 ±18ms，帧间隔 50-100ms
                # 可能整帧跳过）；t_arr 提前窗口固定 T_EXTRA，vx 一确认即可命中，
                # 对向左蛋糕（vx<0 时 pred_x 落后实际位置，pred 判定失效）尤其关键。
                # 仅当蛋糕仍朝 target 靠近（未过 target）时生效；已过的由 pcross/jump 兜底。
                if abs(vx) > 1.0:
                    toward = (vx < 0 and last_x > target_x) or (
                        vx > 0 and last_x < target_x
                    )
                    if toward:
                        t_arr = abs(last_x - target_x) / abs(vx)
                        if (
                            t_arr <= CAKE_RELEASE_DELAY + CAKE_RELEASE_T_EXTRA
                            and abs(pred_x - target_x) <= CAKE_TARR_MAX_PRED_ERR
                        ):
                            logger.debug(
                                f"释放 {kind}（到达时间）@ x={last_x:.0f} vx={vx:.0f} "
                                f"t_arr={t_arr:.3f} pred_x={pred_x:.0f} target_x={target_x:.0f}"
                            )
                            self._release_and_observe(
                                bot, zdg_x, zdg_y, kind, last_x,
                                vx, pred_x, target_x, "t_arr", anchors,
                            )
                            released += 1
                            released_flag = True
                            break

                # 到达判定：基于预测落点
                if abs(pred_x - target_x) <= CAKE_RELEASE_X_TOLERANCE:
                    logger.debug(
                        f"释放 {kind}（预测补偿）@ x={last_x:.0f} vx={vx:.0f} "
                        f"pred_x={pred_x:.0f} target_x={target_x:.0f}"
                    )
                    self._release_and_observe(bot, zdg_x, zdg_y, kind, last_x,
                                              vx, pred_x, target_x, "pred", anchors)
                    released += 1
                    released_flag = True
                    break

                # 预测跨越：上一帧 pred 在容差窗一侧、本帧 pred 已跳到另一侧
                # （vx 低估导致 pred 错过容差窗时，仍按 pred 越界及时释放，
                #   误差 ≤ 单帧预测增量，远小于等实际位置跨越的偏差）
                if prev_pred is not None and abs(pred_x - prev_pred) <= CAKE_TRACK_MAX_JUMP:
                    if (prev_pred < target_x - CAKE_RELEASE_X_TOLERANCE
                            and pred_x > target_x + CAKE_RELEASE_X_TOLERANCE) \
                            or (prev_pred > target_x + CAKE_RELEASE_X_TOLERANCE
                                and pred_x < target_x - CAKE_RELEASE_X_TOLERANCE):
                        logger.debug(
                            f"释放 {kind}（预测跨越）@ x={last_x:.0f} vx={vx:.0f} "
                            f"pred_x={pred_x:.0f} prev_pred={prev_pred:.0f} "
                            f"target_x={target_x:.0f}"
                        )
                        self._release_and_observe(bot, zdg_x, zdg_y, kind, last_x,
                                                  vx, pred_x, target_x, "pcross", anchors)
                        released += 1
                        released_flag = True
                        break

                # 跨越检测：蛋糕加速后一帧可能跳过容差窗口，
                # 从目标左侧跨到右侧（或反向）也视为到达 → 释放
                # 单帧位移必须 ≤ CAKE_TRACK_MAX_JUMP，超距视为异常采样，不触发。
                # 注意：点击 zdg 后蛋糕仍会继续飞 CAKE_RELEASE_DELAY 才下落，
                # 实际落点 = last_x + vx*DELAY = pred_x。因此快速跨越仅当 pred_x
                # 仍接近 target 时才有意义（vx 很小时）；高 vx 跨越时 pred_x 已
                # 远离 target（偏差≈|vx|*DELAY，可到 300px+），释放必落偏，禁止。
                jump = abs(last_x - prev_x)
                if jump <= CAKE_TRACK_MAX_JUMP and (
                        (prev_x < target_x - CAKE_RELEASE_X_TOLERANCE
                         and last_x > target_x + CAKE_RELEASE_X_TOLERANCE)
                        or (prev_x > target_x + CAKE_RELEASE_X_TOLERANCE
                            and last_x < target_x - CAKE_RELEASE_X_TOLERANCE)):
                    pred_err = abs((last_x + vx * CAKE_RELEASE_DELAY) - target_x)
                    if pred_err <= CAKE_JUMP_MAX_PRED_ERR:
                        logger.debug(
                            f"释放 {kind}（快速跨越）@ x={last_x:.0f} "
                            f"prev_x={prev_x:.0f} target_x={target_x:.0f} pred_err={pred_err:.0f}"
                        )
                        self._release_and_observe(bot, zdg_x, zdg_y, kind, last_x,
                                                  vx, last_x + vx * CAKE_RELEASE_DELAY,
                                                  target_x, "jump", anchors)
                        released += 1
                        released_flag = True
                        break
                    logger.debug(
                        f"跳过快速跨越 {kind} @ x={last_x:.0f} target={target_x:.0f} "
                        f"pred_err={pred_err:.0f}px 过大（落点将偏 {pred_err}px）"
                    )

                bot.sleep(CAKE_TRACK_INTERVAL)

            if not released_flag:
                logger.debug(f"追踪 {kind} 未释放（超帧/丢失/放弃），继续检测下一个")

        # ── 阶段3：领奖 + 返回百趣闲市 ──
        claimed = self._cake_claim(bot)
        logger.info(
            f"蛋糕小游戏结束：end_reason={end_reason}，释放蛋糕 {released} 个，"
            f"领奖={'成功' if claimed else '未检测到领奖g'}，"
            f"用时 {time.time() - game_start:.1f}s（整局 {CAKE_GAME_TIMEOUT:.0f}s），"
            f"总耗时 {time.perf_counter() - t0:.1f}s"
        )
        return claimed

    def _cake_claim(self, bot: GameBot) -> bool:
        """
        阶段4 领奖：检测 领奖g → 领取g → 确定，然后点击两次 fanhui 回到百趣闲市。

        领取g 点中即奖励已发放 = 任务完成（2026-08-30 用户确认：完成的条件是
        领取了奖励就行）；其后 确定/返回 弹窗只是收尾，点不到不算失败。
        未检测到 领奖g 时只点击一次 fanhui 回选择界面，返回 False（供外层重试）。
        """
        # 领奖g 仅结算后出现；若检测不到 → 未出领奖，回选择界面等待再来一局
        if not bot.wait_and_click(
            "领奖g.png",
            timeout=CAKE_CLAIM_TIMEOUT,
            interval=CAKE_CLAIM_INTERVAL,
            threshold=CAKE_CLAIM_ENTRY_THRESHOLD,
        ):
            logger.warning("未检测到 领奖g，本次不领奖，点击 fanhui 回选择界面")
            bot.wait_and_click(
                "fanhui.png",
                timeout=CAKE_EXIT_TIMEOUT,
                interval=CAKE_EXIT_INTERVAL,
                threshold=CAKE_MATCH_THRESHOLD,
            )
            bot.sleep(CAKE_NAV_CLICK_WAIT)
            return False

        logger.info("检测到 领奖g，开始领奖流程")
        bot.sleep(CAKE_NAV_CLICK_WAIT)
        if not bot.wait_and_click(
            "领取g.png",
            timeout=CAKE_CLAIM_TIMEOUT,
            interval=CAKE_CLAIM_INTERVAL,
            threshold=CAKE_CLAIM_GET_THRESHOLD,
        ):
            logger.warning("未检测到 领取g，领奖流程中断")
            bot.wait_and_click(
                "fanhui.png",
                timeout=CAKE_EXIT_TIMEOUT,
                interval=CAKE_EXIT_INTERVAL,
                threshold=CAKE_MATCH_THRESHOLD,
            )
            bot.sleep(CAKE_NAV_CLICK_WAIT)
            return False

        # 领取g 已点中 = 奖励已到账；确定 弹窗点不到不影响领取结果
        bot.sleep(CAKE_NAV_CLICK_WAIT)
        if not bot.wait_and_click(
            "确定.png",
            timeout=CAKE_CLAIM_TIMEOUT,
            interval=CAKE_CLAIM_INTERVAL,
            threshold=CAKE_CLAIM_CONFIRM_THRESHOLD,
        ):
            logger.warning("未检测到 确定 弹窗（不影响领取结果），继续收尾")

        bot.sleep(CAKE_NAV_CLICK_WAIT)
        # 两次 fanhui 回到百趣闲市界面
        for i in range(2):
            if bot.wait_and_click(
                "fanhui.png",
                timeout=CAKE_EXIT_TIMEOUT,
                interval=CAKE_EXIT_INTERVAL,
                threshold=CAKE_MATCH_THRESHOLD,
            ):
                logger.info(f"已点击 fanhui（{i + 1}/2）")
            else:
                logger.warning(f"未检测到 fanhui（{i + 1}/2）")
            bot.sleep(CAKE_NAV_CLICK_WAIT)
        return True

    def cake_task(self, bot: GameBot) -> bool:
        """
        蛋糕小游戏每日任务：最多进行 CAKE_MAX_PLAYS 局，
        任一一局检测到 领奖g 并完成领奖即成功；两局都未检测到记为失败。

        结束后回到主界面（蛋糕小游戏结束时停在百趣闲市），保证任务间隙的
        演武复查钩子（after_task_hook）能正常触发。
        """
        try:
            self._cake_fatal = False
            for i in range(1, CAKE_MAX_PLAYS + 1):
                logger.info(f"=== 蛋糕小游戏 第 {i}/{CAKE_MAX_PLAYS} 局 ===")
                if self.cake_game(bot):
                    logger.info(f"蛋糕小游戏第 {i} 局领奖成功，任务完成")
                    return True
                if getattr(self, "_cake_fatal", False):
                    logger.error("蛋糕：体力购买弹窗已确认任务失败，按用户口径不再尝试下一局")
                    return False
                logger.warning(f"蛋糕小游戏第 {i} 局未检测到领奖g")
            logger.error(f"蛋糕小游戏连续 {CAKE_MAX_PLAYS} 局未检测到领奖g，记为失败")
            return False
        finally:
            # 回主界面（百趣闲市 → 活动场 → 主界面），复用已验证的 _return_to_main：
            # 内部先 dismiss_popups 清弹窗，再 fanhui 直到成长入口可见
            self._return_to_main(bot)
