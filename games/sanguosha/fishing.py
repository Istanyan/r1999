"""
三国杀脚本 — 钓鱼小游戏模块。

由原 games/sanguosha.py 拆分而来：
FishingMixin 承载原 SanguoshaScript 的钓鱼相关方法
（_return_to_main / _fishing_navigate / fishing_game / _fishing_session /
 _fishing_main_loop / _fishing_handle_event / _fishing_check_after_cast /
 _fishing_retry_click / _read_fish_coin / _fishing_claim_result）。
"""

import time
from typing import Optional

from core.game_bot import GameBot

from .constants import logger, _warn, HookLossError, MAIN_SCREEN_THRESHOLD


class FishingMixin:
    """钓鱼小游戏（含后台连点线程与脱钩判定）。"""

    def _return_to_main(self, bot: GameBot, max_clicks: int = 6) -> bool:
        """
        返回主界面。

        先用共享的 dismiss_popups 清掉可能叠加的活动弹窗
        （顶层弹窗会吞掉 fanhui 点击，遮罩下 chengzhang 也会误匹配），
        再在「开始钓鱼」界面用独特的「钓鱼fanhui」退出钓鱼界面，
        最后点普通返回直到看到成长入口（chengzhang）。
        """
        # 0. 清理活动弹窗（签到/×/返回 轮换，见 startup.dismiss_popups）
        if self.dismiss_popups(bot):
            return True
        # 1. 钓鱼界面 → 用钓鱼fanhui 退出
        for _ in range(3):
            if not bot.is_present("开始钓鱼.png", threshold=0.85):
                break
            if bot.click_if_found("钓鱼fanhui.png", threshold=0.85):
                bot.sleep(1.0)
                continue
            bot.sleep(0.5)  # 钓鱼fanhui 未出现，等一会再查
        # 2. 普通返回直到主界面
        for _ in range(max_clicks):
            if self._on_main_screen(bot):
                return True
            bot.click_if_found("fanhui.png", threshold=0.85)
            bot.sleep(0.5)
        _warn("未能回到主界面")
        return False

    def _fishing_navigate(self, bot: GameBot) -> bool:
        """
        钓鱼导航：回主界面 → 活动场 → 百趣闲市 → 钓鱼。

        每步用 _wait_found 等待按钮出现（页面没加载出来就重试，不误判停止）。
        """
        if not self._return_to_main(bot):
            return False
        for template, name in [
            ("活动场.png", "活动场"),
            ("百趣闲市.png", "百趣闲市"),
            ("钓鱼.png", "钓鱼"),
        ]:
            found = self._wait_found(bot, template, 0.85)
            if found is None:
                _warn(f"钓鱼导航：未找到 {name}（{template}）")
                return False
            bot.click(found[0], found[1])
            bot.sleep(1.0)  # 等页面切换
        return True

    def fishing_game(self, bot: GameBot, is_first: bool = True) -> bool:
        """
        钓鱼小游戏主流程（递归）。

        首次进入：先导航到钓鱼界面（回主界面 → 活动场 → 百趣闲市 → 钓鱼），
        会话结束后返回主界面。
        结束条件：抛竿后既检测不到"使用"也检测不到"提杆"。
        """
        t0 = time.perf_counter()
        logger.info("=== 钓鱼小游戏 ===")

        if is_first:
            self._fishing_count = 0  # 每次进入钓鱼会话时重置计数
            self._fishing_coins = 0
            # 已在钓鱼界面（开始钓鱼可见）则跳过导航，否则导航过去
            if not bot.is_present("开始钓鱼.png", threshold=0.85):
                if not self._fishing_navigate(bot):
                    _warn("钓鱼导航失败")
                    return False

        try:
            result = self._fishing_session(bot, is_first)
        except HookLossError:
            raise  # 脱钩：交给任务引擎处理，不额外回主界面
        if is_first:
            self._return_to_main(bot)
        logger.info(
            f"钓鱼小游戏结束：完成={result}，共钓上 {self._fishing_count} 条，"
            f"累计鱼币 {self._fishing_coins}，耗时 {time.perf_counter() - t0:.1f}s"
        )
        return result

    def _fishing_session(self, bot: GameBot, is_first: bool) -> bool:
        """
        钓鱼会话核心（不含导航）：开始钓鱼 → 抛竿 → 收线 → 再钓一次（循环，避免递归爆栈）。

        原实现用递归衔接"再钓一次"，钓获条数多时递归深度线性增长，
        存在超过 Python 递归上限的风险；改为外层 while 循环后深度恒为 1。
        """
        monitor = bot.screenshot.monitor
        drag_v = int(monitor["height"] * 0.1)
        drag_h = int(monitor["width"] * 0.1)

        # ── 开始钓鱼（仅首次） ──
        if is_first:
            if not self._fishing_retry_click(bot, "开始钓鱼.png"):
                _warn("未找到开始钓鱼")
                return False
            bot.sleep(1.0)

        # ── 连续钓竿循环（原递归的每一竿） ──
        while True:
            # ── 抛竿循环 ──
            while True:
                # 等抛竿按钮出现（再钓一次后留出加载时间），返回中心坐标
                rod = self._wait_found(bot, "抛竿.png", 0.85)
                if rod is None:
                    _warn("未找到抛竿")
                    return False
                cx, cy = int(rod[0]), int(rod[1])
                bot.action.drag(cx, cy, cx, cy - drag_v, duration=0.3)
                bot.sleep(5.6)

                # 检测状态（含 0.3s×5 次重试，排除画面延迟）
                result = self._fishing_check_after_cast(bot)
                if result == "recast":
                    continue
                if result == "proceed":
                    bot.sleep(1.0)
                    break

                logger.info("抛竿后无使用也无提杆，钓鱼结束")
                return True

            # ── 主循环 ──
            if not self._fishing_main_loop(bot, drag_v, drag_h, monitor):
                return False

            # ── 再钓一次 / 收线回退 ──
            for attempt in range(3):
                if bot.is_present("再钓一次.png", threshold=0.85):
                    # 先读鱼币：读到→正常钓获；读不到→脱钩导致的再钓一次
                    if self._fishing_claim_result(bot):
                        break  # 正常钓获 → 跳出 for，进入下一竿

                if bot.find_region("收线.png", threshold=0.5):
                    if self._fishing_main_loop(bot, drag_v, drag_h, monitor):
                        if bot.is_present("再钓一次.png", threshold=0.85):
                            if self._fishing_claim_result(bot):
                                break  # 正常钓获 → 跳出 for，进入下一竿
                        continue
                    return False

                logger.info(f"未检测到收线或再钓一次，5s 后重试 ({attempt + 1}/3)")
                bot.sleep(5.0)
            else:
                # for 正常走完（未 break）：3 次重试仍未检测到收线
                _warn("3 次重试仍未检测到收线，暂停")
                logger.info(
                    f"本次钓鱼结束，共钓上 {self._fishing_count} 条，"
                    f"累计鱼币 {self._fishing_coins}"
                )
                return False
            # for 被 break（已点再钓一次）→ 回到外层 while 继续下一竿

    def _fishing_main_loop(self, bot: GameBot, drag_v: int, drag_h: int,
                           monitor: dict, click_interval: float = 1 / 50) -> bool:
        """
        钓鱼主循环（后台线程有节奏连点收线）。

        实测结论：MuMu 中游戏不响应"按住"，但响应单击。
        因此用有节奏的连点模拟持续收线：
        收线可见且未冷却 → 后台线程按 click_interval 间隔连点收线中心；
        蓄力不可见 → 停止连点并进入短冷却（ENERGY_COOLDOWN）；
        事件（提杆/爆发）每 2s 检测一次，处理前先暂停连点避免干扰。

        收线+蓄力均消失时的分支：
          - 出现「再钓一次」→ 正常结束，返回 True，并累计钓鱼次数
          - 收线/蓄力重现 → 终结一击恢复，继续收线
          - 超过 HOOK_LOSS_TIMEOUT 秒未恢复 → 判定鱼脱钩，抛 HookLossError

        参数
        ----
        click_interval : float  连点间隔（秒），默认 1/50 ≈ 50 次/秒。
                                收线不够快可调小，出现卡顿可调大。
        """
        import ctypes
        import threading

        user32 = ctypes.windll.user32
        LEFTDOWN = 0x0002
        LEFTUP = 0x0004

        no_reel = 0
        stalled = 0
        pre_wait = 0            # 提杆后过渡期等待计数（未进入收线时）
        reeling_seen = False    # 是否已真正进入收线（区分过渡期与脱钩）
        energy_deadline = 0.0   # 蓄力冷却截止时间戳（现在 + 0.5s）
        hook_loss_deadline = 0.0  # 脱钩判定截止时间戳（首次消失时 + HOOK_LOSS_TIMEOUT）
        last_event_time = time.time() - 2.0  # 首轮立即检测一次事件
        clicking = threading.Event()
        stop = threading.Event()
        reel_pos = [0, 0]  # 屏幕绝对坐标（SetCursorPos 需要）

        def _update_reel_pos(reel):
            """记录收线区域中心的屏幕绝对坐标（窗口相对 + 左上角偏移）。"""
            reel_pos[0] = int(reel[0] + reel[2] / 2) + monitor["left"]
            reel_pos[1] = int(reel[1] + reel[3] / 2) + monitor["top"]

        def _clicker():
            while not stop.is_set():
                if clicking.is_set():
                    user32.SetCursorPos(reel_pos[0], reel_pos[1])
                    user32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
                    user32.mouse_event(LEFTUP, 0, 0, 0, 0)
                    stop.wait(click_interval)  # 有节奏连点，避免线程空转卡顿
                else:
                    stop.wait(0.001)

        worker = threading.Thread(target=_clicker, daemon=True)
        worker.start()

        try:
            while True:
                reel = bot.find_region("收线.png", threshold=0.5)
                has_energy = bot.is_present("蓄力.png", threshold=0.99)

                if reel:
                    _update_reel_pos(reel)

                # ── 收线+蓄力均消失 → 区分 正常结束 / 终结一击 / 鱼脱钩 ──
                if not reel and not has_energy:
                    # 收线+蓄力均消失 → 立即停止连点（终结一击等异常期间绝不能收线）
                    clicking.clear()
                    # 提杆后过渡期：尚未进入收线，只等待不计数，避免误判脱钩
                    if not reeling_seen:
                        pre_wait += 1
                        if pre_wait > 50:  # 约 10s 仍未进入收线 → 判脱钩
                            clicking.clear()
                            _warn("提杆后长时间未出现收线/蓄力，判定鱼脱钩，终止脚本")
                            raise HookLossError(
                                f"提杆后未进入收线（已钓 {self._fishing_count} 条）"
                            )
                        bot.sleep(self.FISHING_IDLE_POLL)
                        continue

                    # 正常结束：出现「再钓一次」
                    if bot.is_present("再钓一次.png", threshold=0.85):
                        self._fishing_count += 1
                        logger.info(
                            f"第 {self._fishing_count} 条鱼钓上，出现「再钓一次」"
                        )
                        clicking.clear()
                        return True

                    # 终结一击 / 脱钩：首次消失时记录恢复截止时间
                    if stalled == 0:
                        hook_loss_deadline = time.time() + self.HOOK_LOSS_TIMEOUT
                    stalled += 1

                    # 超过恢复窗口仍未恢复 → 判脱钩
                    if time.time() >= hook_loss_deadline:
                        clicking.clear()
                        _warn(
                            f"收线/蓄力超过 {self.HOOK_LOSS_TIMEOUT:.0f}s 未再出现，"
                            "判定鱼脱钩，终止本次钓鱼"
                        )
                        raise HookLossError(
                            f"鱼脱钩（已钓 {self._fishing_count} 条）"
                        )

                    # 终结一击恢复期：等待后重新检测
                    bot.sleep(self.FISHING_IDLE_POLL)
                    continue
                else:
                    stalled = 0
                    reeling_seen = True

                # ── 蓄力不可见 → 进入短冷却（停止连点，避免蓄力恢复后延迟过长） ──
                if not has_energy:
                    energy_deadline = time.time() + self.ENERGY_COOLDOWN

                # ── 事件检测（每 2s；暂停连点 → 处理 → 刷新位置） ──
                now = time.time()
                if now - last_event_time >= 2.0:
                    clicking.clear()
                    self._fishing_handle_event(bot, drag_v, drag_h, monitor)
                    last_event_time = now
                    reel = bot.find_region("收线.png", threshold=0.5)
                    if reel:
                        _update_reel_pos(reel)

                # ── 连点开关 ──
                if reel and now >= energy_deadline:
                    no_reel = 0
                    clicking.set()
                else:
                    clicking.clear()
                    if not reel:
                        no_reel += 1
                        if no_reel >= 5:
                            logger.info("连续多次未检测到收线，退出主循环")
                            return True

                bot.sleep(0.1)
        finally:
            stop.set()
            clicking.clear()
            worker.join(timeout=0.5)

    @staticmethod
    def _fishing_handle_event(bot: GameBot, drag_v: int, drag_h: int,
                              monitor: dict) -> bool:
        """
        检测并处理钓鱼事件。

        优先级：爆发 > 提杆1 > 提杆2。
        返回 True 表示处理了事件（调用方应跳过本轮收线）。
        """
        for template, dx, dy in [
            ("爆发.png", 0, -drag_v),
            ("提杆1.png", drag_h, 0),
            ("提杆2.png", -drag_h, 0),
        ]:
            r = bot.find_region(template, threshold=0.85)
            if r:
                cx = int(r[0] + r[2] / 2)
                cy = int(r[1] + r[3] / 2)
                bot.action.drag(cx, cy, cx + dx, cy + dy, duration=0.3)
                return True
        return False

    @staticmethod
    def _fishing_check_after_cast(bot: GameBot, retries: int = 5,
                                  interval: float = 0.3) -> str:
        """
        抛竿后的状态检测（排除画面延迟导致的误判）。

        首轮检测不到「使用/提杆」时，再以 interval 秒间隔重试 retries 次，
        期间任何一次检出即返回对应状态；全部未检出才判定钓鱼结束。

        返回
        ----
        'recast'  : 出现「使用」，需重新抛竿
        'proceed' : 出现「提杆」，进入收线主循环
        'ended'   : 多次检测均无 → 钓鱼结束
        """
        for attempt in range(retries + 1):
            if bot.click_if_found("使用.png", threshold=0.85):
                return "recast"
            if bot.click_if_found("提杆.png", threshold=0.85):
                return "proceed"
            if attempt < retries:
                bot.sleep(interval)
        return "ended"

    @staticmethod
    def _fishing_retry_click(bot: GameBot, template: str,
                             max_retries: int = 3) -> bool:
        """带重试的点击，最多 max_retries 次。"""
        for attempt in range(max_retries):
            if bot.click_if_found(template, threshold=0.85):
                return True
            if attempt < max_retries - 1:
                bot.sleep(1.0)
        return False

    # ═══════════════════════════════════════════════════════
    # 钓鱼结算：读取鱼币数字 + 判定脱钩
    # ═══════════════════════════════════════════════════════

    def _read_fish_coin(self, bot: GameBot) -> Optional[int]:
        """
        读取钓鱼结算界面（与「再钓一次」同界面）的鱼币数字。

        用「左边界」「右边界」两个锚点图标定位数字区域，裁剪后 OCR 识别。
        返回数字整数；未定位到锚点或未识别到数字返回 None。
        """
        import re
        try:
            left = bot.find_region("左边界.png", threshold=0.70)
            right = bot.find_region("右边界.png", threshold=0.70)
            if left is None or right is None:
                logger.info("未定位到鱼币数字锚点（左/右边界）")
                return None

            ll, lt, lw, lh, _ = left
            rl, rt, rw, rh, _ = right
            pad = 5
            l_icon = left if ll <= rl else right   # 靠左的图标
            r_icon = right if ll <= rl else left   # 靠右的图标
            x1 = l_icon[0] + l_icon[2] + pad
            x2 = r_icon[0] - pad
            y1 = min(lt, rt) - pad
            y2 = max(lt + lh, rt + rh) + pad
            if x2 <= x1:  # 图标顺序异常 → 退回两图标整体包围盒
                x1, x2 = min(ll, rl) - pad, max(ll + lw, rl + rw) + pad

            img = bot.screenshot.capture_region(x1, y1, x2 - x1, y2 - y1)
            items = bot._ocr.recognize(img)
            for it in items:
                nums = re.findall(r"\d+", it["text"])
                if nums:
                    return int(nums[0])
            return None
        except Exception as e:
            # OCR/截图异常按"未读到"处理，让调用方重试，而不是中断整个钓鱼会话
            logger.warning(f"读取鱼币异常（按未读到处理）: {e}")
            return None

    def _fishing_showcase_coin(self, bot: GameBot) -> Optional[int]:
        """
        特殊鱼种展示卡判定 + 读价。

        部分鱼种（如鲈鱼）钓上后进入展示卡页：整卡无 左/右边界 锚点、无返回按钮，
        只有"最终价格: N"字样（2026-08-29 实测第 7 条鱼因此被误判脱钩）。
        OCR 全屏找"最终价格/基础价格"字样并提取数字；非展示卡返回 None。
        """
        import re
        try:
            img = bot.screenshot.capture()
            for it in bot._ocr.recognize(img):
                t = it["text"]
                if "最终价格" in t or "基础价格" in t:
                    nums = re.findall(r"\d+", t)
                    if nums:
                        return int(nums[-1])
        except Exception as e:
            logger.warning(f"展示卡判定/读价异常（按未读到处理）: {e}")
        return None

    def _fishing_claim_result(self, bot: GameBot) -> bool:
        """
        处理钓鱼结果界面：先读鱼币数字，再点「再钓一次」。

        读到数字 → 累计鱼币，点击再钓一次，返回 True（继续下一竿）。
        读不到数字 → 先判断是否特殊鱼种展示卡（无锚点但有最终价格），
        是则读卡上价格计入鱼币后继续；都不是才判定为脱钩，抛 HookLossError 终止。
        """
        # 结算界面可能未渲染完，先等一会再读鱼币（与征战重进同理）
        bot.sleep(3.0)
        coin = None
        for _ in range(2):  # 重试一次，间隔拉长避免页面未加载好误判
            coin = self._read_fish_coin(bot)
            if coin is not None:
                break
            bot.sleep(1.0)
        if coin is None:
            showcase_coin = self._fishing_showcase_coin(bot)
            if showcase_coin is not None:
                self._fishing_coins += showcase_coin
                logger.info(
                    f"特殊鱼种展示卡钓获，鱼币 +{showcase_coin}，"
                    f"累计 {self._fishing_coins}"
                )
                bot.click_if_found("再钓一次.png", threshold=0.85)
                bot.sleep(1.5)  # 等新一竿的抛竿按钮加载
                return True
            _warn("再钓一次界面未读到鱼币数字，判定为脱钩")
            raise HookLossError(
                f"脱钩（已钓 {self._fishing_count} 条，累计鱼币 {self._fishing_coins}）"
            )
        self._fishing_coins += coin
        logger.info(f"本次钓获鱼币 +{coin}，累计 {self._fishing_coins}")
        bot.click_if_found("再钓一次.png", threshold=0.85)
        bot.sleep(1.5)  # 等新一竿的抛竿按钮加载
        return True
