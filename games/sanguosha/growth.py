"""
三国杀脚本 — 成长模块。

由原 games/sanguosha.py 拆分而来：
GrowthMixin 承载原 SanguoshaScript 的成长模块相关方法
（growth_flow / _click_until_gone / _options_changed / _wait_found /
 _step_nnjr / _step_reward / _step_yuanbao）。
"""

import time

from core.game_bot import GameBot

from .constants import logger, _warn, GROWTH_TOPBAR_MIN_Y


class GrowthMixin:
    """成长模块（复合流程）：成长+那年今日+我要奖励+浇水+元宝。"""

    @staticmethod
    def _click_below_topbar(bot: GameBot, template: str, threshold: float,
                            min_y: int = GROWTH_TOPBAR_MIN_Y) -> bool:
        """
        带顶栏保护的点击：模板命中且中心 y ≥ min_y 才点击。

        宝箱1-4(阈0.5)/元宝yb1-2(阈0.6) 都是小图标模板，会在顶栏货币
        "加号"区域假匹配并误点开充值商店 → 体力购买框 → 商店页强制竖屏
        渲染，横屏模板全失效（2026-08-30 run10/11 连锁卡死的根因）。
        """
        r = bot.find_region(template, threshold=threshold)
        if r is None:
            return False
        cy = r[1] + r[3] / 2
        if cy < min_y:
            logger.debug(f"{template} 命中 y={cy:.0f} < {min_y}（顶栏误匹配），忽略")
            return False
        bot.click(int(r[0] + r[2] / 2), int(r[1] + r[3] / 2))
        return True

    def growth_flow(self, bot: GameBot) -> bool:
        """
        成长模块完整流程。

        步骤：
        1. 进入成长页面（chengzhang）
        2. 循环领取（lingqu）
        3. 那年今日（nnjr → 前往 → xuanxiang → 点击选项 → 回成长页 → 再领）
        4. 我要奖励（woyaojiangli → qianwang_red → jiaoshui）
        5. 点击元宝（yb1 / yb2）
        6. 返回主界面（fanhui）
        """
        # ── 1. 进入成长页面（相对坐标点击，不依赖红点模板） ──
        t0 = time.perf_counter()
        logger.info("─ 成长模块开始 ─")

        # 【2026-10-22】先确认在主界面：相对坐标点击依赖主界面布局，
        # 征战天下结束后可能停在演武/急报/任务页等子页面，直接点会进错页面。
        if not self._on_main_screen(bot):
            logger.info("[成长入口] 不在主界面，先清理弹窗/返回主界面")
            self.dismiss_popups(bot, max_rounds=5)
            if not self._on_main_screen(bot):
                _warn("[成长入口] 清理后仍不在主界面，尝试 fanhui 返回")
                for _ in range(3):
                    bot.click_if_found("fanhui.png", threshold=0.85)
                    bot.sleep(0.8)
                    if self._on_main_screen(bot):
                        break
            if not self._on_main_screen(bot):
                _warn("[成长入口] 无法回到主界面，跳过成长模块")
                logger.info(
                    f"成长模块未完成（不在主界面），耗时 {time.perf_counter() - t0:.1f}s"
                )
                return False
            logger.info("[成长入口] 已回到主界面")

        # 成长按钮在底部菜单栏，约 49% 宽 / 93% 高位置
        GROWTH_X_RATIO = 0.49
        GROWTH_Y_RATIO = 0.93

        entered = False
        for attempt in range(3):
            img = bot._capture()
            h, w = img.shape[:2]
            gx = int(w * GROWTH_X_RATIO)
            gy = int(h * GROWTH_Y_RATIO)
            logger.info(f"[成长入口] 相对坐标点击 ({gx}, {gy}) ← 尝试 {attempt + 1}/3")
            bot.click(gx, gy, allow_danger=True)
            bot.sleep(1.5)

            # 验证已进入成长页面
            for _ in range(5):
                if (bot.is_present("woyaojiangli.png", threshold=0.85)
                        or bot.is_present("lingqu.png", threshold=0.75)):
                    entered = True
                    break
                bot.sleep(0.5)
            if entered:
                break
            logger.debug(f"  未进入成长页，重试 ({attempt + 1}/3)")

        if not entered:
            _warn("相对坐标点击成长入口后未进入成长页面")
            logger.info(
                f"成长模块未完成（进入成长页失败），耗时 {time.perf_counter() - t0:.1f}s"
            )
            return False

        # ── 2. 循环领取 ──
        self._click_until_gone(bot, "lingqu.png", threshold=0.75)

        # ── 3. 那年今日 ──
        self._step_nnjr(bot)

        # ── 4. 我要奖励 → 浇水 → 元宝 → 宝箱 → 返回主界面 ──
        yb_ok = self._step_reward(bot)
        if not yb_ok:
            _warn("元宝树领取失败，成长模块未完成")
            logger.info(
                f"成长模块未完成（元宝树未领取），耗时 {time.perf_counter() - t0:.1f}s"
            )
            return False

        if not bot.is_present("标志.png", threshold=0.98):
            _warn("未检测到主界面（标志.png）")

        logger.info(
            f"成长模块完成，耗时 {time.perf_counter() - t0:.1f}s"
        )
        return True

    # ── 子步骤 ──────────────────────────────────

    def _click_until_gone(self, bot: GameBot, template: str, threshold: float):
        """
        循环点击模板直到不再出现（防误触版，2026-09-06 用户要求：lingqu
        循环一定要慢）。

        点中后：
        1. 等奖励结算浮窗出现并点确定关闭（有则关闭，无则跳过）
        2. 再等 4s 让动画/页面恢复稳定，才开始下一次检测
        未命中时：二次确认间隔拉长到 4s（防动画期间误判消失），
        且二次确认也未命中才退出，命中则继续循环。
        """
        logger.info(f"[循环点击] {template} ...")
        count = 0
        max_iter = 20
        for _ in range(max_iter):
            if bot.click_if_found(template, threshold=threshold):
                count += 1
                bot.sleep(1.0)
                # 领奖结算浮窗：出现则关闭，防止盖住下一次检测目标
                bot.click_if_found("确定.png", threshold=0.80)
                bot.sleep(4.0)
            else:
                # 二次确认：等动画播完再补查一次
                bot.sleep(4.0)
                if not bot.click_if_found(template, threshold=threshold):
                    break
                count += 1
                bot.sleep(1.0)
                bot.click_if_found("确定.png", threshold=0.80)
                bot.sleep(4.0)
        logger.info(f"[循环点击] {template} 共点击 {count} 次")

    @staticmethod
    def _options_changed(a: list, b: list, tol: int = 8) -> bool:
        """判断两组选项中心集合是否变化（用于识别是否进入新题目）。"""
        if len(a) != len(b):
            return True
        key = lambda p: (int(p[0]), int(p[1]))
        a_sorted = sorted(map(key, a))
        b_sorted = sorted(map(key, b))
        for (ax, ay), (bx, by) in zip(a_sorted, b_sorted):
            if abs(ax - bx) > tol or abs(ay - by) > tol:
                return True
        return False

    @staticmethod
    def _wait_found(bot: GameBot, template: str, threshold: float = 0.85,
                    retries: int = 2, interval: float = 0.4):
        """
        条件等待：立即检测模板，未找到则每 interval 秒重试，最多 retries 次。

        用于界面切换后等待新页面加载（新页面没渲染完就检测会误判停止）。
        返回 (cx, cy, conf) 或 None。
        """
        for attempt in range(retries + 1):
            result = bot.find(template, threshold)
            if result is not None:
                return result
            if attempt < retries:
                bot.sleep(interval)
        return None

    def _step_nnjr(self, bot: GameBot):
        """
        那年今日处理：
        nnjr → 点击框内"前往" → find_all 枚举「nanianjinri.png」所有选项，逐个尝试不同位置，
        答对进下一题、答错自动换下一个，直到出现 lingqu。
        """
        # 检测 nnjr（2026-11-17 run6：旧 1041x147 模板在页面上 0.98 也匹配不到，
        # 已用当天帧重裁同尺寸模板；阈值从 0.98 放宽到 0.85，兼容“已回答 0/1→1/1”）
        # 列表可能因连续领奖滚到下方，先把成长列表滚回顶部，
        # 否则「那年今日」在视口上方，模板匹配不到（run7 实测）。
        bot.scroll(1000, 500, 5)
        bot.sleep(0.5)

        region = bot.find_region("nnjr.png", threshold=0.85)
        if region is None:
            _warn("未检测到 nnjr（那年今日），跳过")
            return

        left, top, w, h, conf = region
        logger.info(f"[那年今日] 匹配，置信度 {conf:.2f}，准备点击框内「前往」")

        # 点击框内"前往"：中间偏下、右侧偏左 5%
        qw_x = int(left + w * 0.85)
        qw_y = int(top + h * 0.70)
        bot.click(qw_x, qw_y)
        bot.sleep(1.5)  # 等对话框完全弹出

        # ── 验证答题弹窗是否真的出现（防止弹窗未弹出时残留lingqu误判） ──
        first_options = bot.find_all("nanianjinri.png", threshold=0.60)
        if not first_options:
            _warn("[那年今日] 点击前往后答题选项未出现（弹窗未弹出），跳过答题")
            return

        # ── 循环答题：枚举所有选项，逐个尝试不同位置（答对进下一题，答错换下一个） ──
        max_iter = 60
        no_options = 0
        last_options = None   # 当前题目的选项中心集合
        tried = 0             # 已尝试到第几个选项
        nnjr_done = False     # 是否正常完成答题
        for i in range(max_iter):
            # 答完：出现领取，且答题选项已消失（排除残留lingqu误判）
            if bot.click_if_found("lingqu.png", threshold=0.85):
                still_options = bot.find_all("nanianjinri.png", threshold=0.60)
                if still_options:
                    # [2026-10-28 两次独立实测（run24+用户 GUI 日志）] lingqu
                    # 点击后弹窗关闭，背景页面误匹配 nanianjinri 0.60-0.73
                    # 全屏散点，被残留判定误当"仍在答题"导致乱点一分钟、
                    # 页面点乱后 woyaojiangli 失踪。真答题选项 conf 0.97-1.00，
                    # 散点最高仅 0.73 → 以最高置信度 0.85 为界区分真残留/已完成
                    best = max(p[2] for p in still_options)
                    if best >= 0.85:
                        logger.info(
                            f"[那年今日] lingqu出现但选项仍在"
                            f"（最高conf {best:.2f}，真残留），继续答题"
                        )
                        continue
                    logger.info(
                        f"[那年今日] lingqu后选项为背景误匹配"
                        f"（最高conf {best:.2f} < 0.85），判定答题完成"
                    )
                    nnjr_done = True
                    bot.sleep(0.5)
                    break
                logger.info("[那年今日] 检测到 lingqu（选项已消失），答题完成")
                nnjr_done = True
                bot.sleep(0.5)
                break

            options = bot.find_all("nanianjinri.png", threshold=0.60)
            if not options:
                # 过渡/暂无选项：连续多次都没有才放弃
                no_options += 1
                if no_options >= 4:
                    _warn("[那年今日] 长时间无选项也无 lingqu，退出")
                    break
                bot.sleep(0.8)
                continue
            no_options = 0

            # 选项集合变化 → 进入下一题，从头尝试
            if last_options is None or self._options_changed(options, last_options):
                last_options = options
                tried = 0

            # 轮换尝试不同位置
            if tried >= len(options):
                tried = 0
            cx, cy, conf = options[tried]
            bot.click(int(cx), int(cy))
            logger.info(
                f"[那年今日] 尝试选项 {tried + 1}/{len(options)} "
                f"({int(cx)}, {int(cy)}) 置信度 {conf:.2f}"
            )
            tried += 1
            bot.sleep(1.5)
        else:
            _warn("[那年今日] 达到最大循环次数，终止")

        # 【2026-10-22】异常退出（未正常答完）时清理弹窗，确保回到成长主页
        if not nnjr_done:
            _warn("[那年今日] 未正常完成，清理弹窗返回成长主页")
            for _ in range(3):
                if bot.is_present("woyaojiangli.png", threshold=0.85):
                    break
                bot.click_if_found("fanhui.png", threshold=0.85)
                bot.sleep(0.8)

        # ── 领取（兜底，循环内已点过则无操作） ──
        bot.sleep(0.5)
        bot.click_if_found("lingqu.png", threshold=0.85)
        bot.sleep(0.5)

        # 再次领取（「我要奖励」由 _step_reward 负责进入，这里不重复点）
        self._click_until_gone(bot, "lingqu.png", threshold=0.85)

        # [MODIFY 2026-09-04] nnjr完成后点击宝箱1-4
        # [MODIFY 2026-09-06] 改用 _claim_chests：点开宝箱弹出的奖励浮窗会
        # 盖住其余宝箱，需关浮窗+两轮扫描，否则常漏宝箱4
        self._claim_chests(bot, ("宝箱1", "宝箱2", "宝箱3", "宝箱4"))

    def _claim_chests(self, bot: GameBot,
                      chests: tuple = ("宝箱1", "宝箱2", "宝箱3", "宝箱4"),
                      threshold: float = 0.5,
                      min_y: int = 60) -> int:
        """
        点击宝箱（两轮扫描 + 奖励浮窗关闭 + 假匹配过滤）。

        1. 顶栏假匹配过滤：宝箱模板会在顶栏货币"加号"（y≈30）上假匹配，
           命中 y < min_y 一律忽略。真实宝箱在活跃度进度条上（y≈125），
           阈值设 60 只挡顶栏加号，不误伤真宝箱。
        2. 点开一个宝箱弹出的奖励浮窗会盖住其余宝箱：每次点击后先关浮窗
           （确定 → × 兜底）再继续；两轮扫描兜底补点第一轮被盖住的。
        """
        clicked = 0
        for pass_idx in range(2):
            found_any = False
            for chest in chests:
                r = bot.find_region(f"{chest}.png", threshold=threshold)
                if r is None:
                    continue
                cy = r[1] + r[3] / 2
                if cy < min_y:
                    logger.debug(f"{chest} 命中 y={cy:.0f} < {min_y}（假匹配），忽略")
                    continue
                bot.click(int(r[0] + r[2] / 2), int(r[1] + r[3] / 2))
                clicked += 1
                found_any = True
                bot.sleep(0.8)
                # 关闭宝箱奖励浮窗：不关会盖住后续宝箱导致漏点
                if not bot.click_if_found("确定.png", threshold=0.80):
                    bot.click_if_found("guanbi_x.png", threshold=0.80)
                bot.sleep(0.5)
            if not found_any:
                break
        if clicked:
            logger.info(f"[宝箱] 共点击 {clicked} 个")
        return clicked

    def _step_reward(self, bot: GameBot) -> bool:
        """
        我要奖励 → 左侧菜单按钮 → 元宝树卡片 → 浇水/元宝。三页跳转，逐页正向验证：
          页面A(成长主页) --点左侧"我要奖励"按钮中心--> 页面B(我要奖励列表)
          页面B --点元宝树卡片内前往--> 页面C(浇水/元宝页)
        不再用 qianwang.png 做页面验证（它在三页都存在，会误点任务卡片跳到排位赛）。
        返回 True 表示元宝树领取成功，False 表示失败。
        """
        logger.info("[我要奖励]")

        # ── 第一步：进入"我要奖励"列表页，验证元宝树卡片出现 ──
        entered_b = False
        for attempt in range(3):
            # 可能已经已在列表页：直接验证，避免重复点左侧菜单
            if self._wait_found(bot, "元宝树卡片.png", 0.85, retries=2, interval=0.8):
                entered_b = True
                break
            region = bot.find_region("woyaojiangli.png", threshold=0.85)
            if region is None:
                _warn("未检测到 woyaojiangli（我要奖励）")
                return False
            left, top, w, h, conf = region
            # [2026-11-17 run6 日志] woyaojiangli.png 是左侧菜单按钮，
            # 不是带“前往”的卡片；原代码点 85%/70% 落在按钮右下边缘，
            # 页面没切换。这里改为点按钮中心，先真正进入列表页。
            cx = int(left + w / 2)
            cy = int(top + h / 2)
            logger.info(
                f"[我要奖励] 匹配左侧按钮（置信度 {conf:.2f}），"
                f"点击中心 ({cx}, {cy})（尝试 {attempt + 1}/3）"
            )
            bot.click(cx, cy)
            bot.sleep(1.5)

            if self._wait_found(bot, "元宝树卡片.png", 0.85, retries=2, interval=0.8):
                entered_b = True
                break
            # run7 实测：首次点击可能被奖励浮层/页面过渡吃掉，页面仍停在
            # 成长主页。这里不 fanhui（fanhui 会直接回主界面，按钮消失），
            # 而是保留在成长页重点一次按钮中心。
            _warn(f"[我要奖励] 第{attempt+1}次未进入列表页（元宝树卡片未出现），重点一次")
            bot.sleep(0.5)

        if not entered_b:
            _warn("3次尝试均未进入我要奖励列表页，跳过")
            return False

        # ── 第二步：在页面B定位元宝树卡片，点其内部前往，验证进入页面C ──
        yb_card = bot.find_region("元宝树卡片.png", threshold=0.85)
        if yb_card is None:
            _warn("页面B未找到元宝树卡片")
            return False
        left, top, w, h, _ = yb_card
        yb_qw_x = int(left + w * 0.80)
        yb_qw_y = int(top + h * 0.50)
        logger.info(f"[元宝树] 定位卡片，点框内前往 ({yb_qw_x}, {yb_qw_y})")
        bot.click(yb_qw_x, yb_qw_y)
        bot.sleep(1.5)

        # 页面C独有元素：浇水按钮或元宝出现 = 进入了浇水页；
        # 今日已摘完时页面C只有“元宝摘完了”，也要算进入成功
        if not (self._wait_found(bot, "jiaoshui.png", 0.85, retries=2, interval=0.8)
                or self._wait_found(bot, "yb1.png", 0.60, retries=2, interval=0.8)
                or bot.is_present("元宝摘完了.png", threshold=0.85)):
            _warn("点元宝树前往后未进入浇水页（jiaoshui/yb均未出现），fanhui退出")
            bot.click_if_found("fanhui.png", threshold=0.85)
            bot.sleep(1.0)
            return False

        # ── 第三步：在页面C循环浇水/元宝直到摘完 ──
        max_iter = 60
        yb_done = False
        for _ in range(max_iter):
            if bot.is_present("元宝摘完了.png", threshold=0.85):
                logger.info("元宝摘完了")
                yb_done = True
                break

            if self._click_below_topbar(bot, "jiaoshui.png", 0.85):
                bot.sleep(2.0)
                continue

            for tpl in ("yb1.png", "yb2.png"):
                if self._click_below_topbar(bot, tpl, 0.60):
                    bot.sleep(1.5)
                    break
            else:
                bot.sleep(1.0)
        else:
            _warn(f"浇水/元宝循环超过 {max_iter} 次仍未出现元宝摘完了，退出")

        # 宝箱（1/2/3/4）：_claim_chests 带浮窗关闭+两轮扫描，防漏宝箱4
        self._claim_chests(bot, ("宝箱1", "宝箱2", "宝箱3", "宝箱4"))

        # 返回主界面（用标志.png判定，不依赖chengzhang模板）
        for _ in range(5):
            if bot.is_present("标志.png", threshold=0.98):
                break
            bot.click_if_found("fanhui.png", threshold=0.85)
            bot.sleep(0.5)
        else:
            _warn("元宝摘完后未回到主界面")

        return yb_done

    def _step_yuanbao(self, bot: GameBot):
        """循环点击 yb1 / yb2 元宝图标直到都不出现。"""
        logger.info("[元宝]")
        count = 0
        max_iter = 30
        for _ in range(max_iter):
            found = False
            for tpl in ["yb1.png", "yb2.png"]:
                if bot.click_if_found(tpl, threshold=0.85):
                    found = True
                    count += 1
                    bot.sleep(0.5)
                    break
            if not found:
                break
        logger.info(f"[元宝] 共点击 {count} 次")
