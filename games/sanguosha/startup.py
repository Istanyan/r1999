"""
三国杀脚本 — 启动清理模块。

由原 games/sanguosha.py 拆分而来：
StartupMixin 承载原 SanguoshaScript.startup 方法（原文件"启动清理"小节）。

2026-08-29 重写：
- 活动期游戏会叠加多层全屏活动弹窗（如 签到组队 → 势·贺齐上线），
  顶层弹窗往往没有返回箭头，唯一关闭方式是底部中央的 ×；
  而下层页面的返回箭头会被上层遮罩吞掉点击，导致对着 0.99 匹配的
  fanhui 空点十几次也毫无作用。
- 因此清理策略改为 签到 → × → 返回 三手段轮换，每次点击后对比
  画面签名（缩略图差），点击无效立即换下一个手段。
- 主界面判定改用新 chengzhang 模板 + 活动场按钮兜底。
"""

import time

import cv2
import numpy as np

from core.game_bot import GameBot

from .constants import (
    logger,
    _warn,
    POPUP_CLOSE_X,
    POPUP_CLOSE_X_THRESHOLD,
    MAIN_SCREEN_THRESHOLD,
    MAIN_SCREEN_GRAY_THRESHOLD,
    CHENGZHANG_ALT,
    DIALOG_CANCEL,
    DIALOG_CANCEL_THRESHOLD,
    PURCHASE_CANCEL,
    PURCHASE_CANCEL_THRESHOLD,
)


class StartupMixin:
    """启动清理：签到 → 关活动弹窗(×/返回) → 回主界面。"""

    # ── 画面签名（弹窗点击有效性检测）──────────────

    @staticmethod
    def _shot_signature(bot: GameBot) -> np.ndarray:
        """画面签名：缩略图。用于判断两次截图之间画面是否显著变化。"""
        img = bot.screenshot.capture()
        return cv2.resize(img, (96, 54), interpolation=cv2.INTER_AREA)

    @staticmethod
    def _signature_diff(a: np.ndarray, b: np.ndarray) -> float:
        """两张签名图的平均绝对差（0~255）。整屏切换通常 >20，
        顶部跑马灯滚动等局部动画通常 <1。"""
        return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())

    # 主界面 OCR 兜底结果缓存（10s 内复用，避免高频调用反复跑 OCR）
    _main_ocr_cache: dict = {"ts": 0.0, "ok": False}

    def _on_main_screen(self, bot: GameBot) -> bool:
        """是否在主界面：三通道任一命中。

        通道1/2: chengzhang 双态模板彩色匹配（0.85）+ 灰度辅助（0.90）
        ——当前皮肤下的可靠判据；
        通道3: OCR 双重确认（2026-09-06 run17 修订）——只看底部菜单"成长"
        会被透明遮罩式活动页骗过（活动页盖住主界面但菜单栏从遮罩下露出，
        run17 实测误判后点穿活动页进入充值页）。修订为:
          底部菜单"成长" AND 上半屏无全屏活动特征（"前往获取/立即参与"等
          活动按钮文字），二者同时成立才算主界面。
        OCR 每进程首次初始化较慢，结果缓存 10s 内复用。
        """
        import time as _t
        img = bot.screenshot.capture()
        matcher = bot._matcher

        # 【优先】标志.png 是主界面独有元素，跨皮肤稳定，命中即主界面。
        # 阈值 0.99（2026-10-24 用户要求：0.85 过低，跨皮肤元素必须近满分才可信）
        biaozhi_path = bot._template_path("标志.png")
        if matcher.match_one(img, biaozhi_path, 0.99) is not None:
            self._main_ocr_cache.update(ts=_t.time(), ok=True)
            return True

        for tpl in ("chengzhang.png", CHENGZHANG_ALT):
            path = bot._template_path(tpl)
            if matcher.match_one(img, path, MAIN_SCREEN_THRESHOLD) is not None:
                self._main_ocr_cache.update(ts=_t.time(), ok=True)
                return True
            if matcher.match_one_gray(img, path, MAIN_SCREEN_GRAY_THRESHOLD) is not None:
                logger.debug(f"主界面判定: {tpl} 灰度通道命中")
                self._main_ocr_cache.update(ts=_t.time(), ok=True)
                return True
        # 模板全未命中 → OCR 兜底（跨皮肤,双确认）
        now = _t.time()
        if now - self._main_ocr_cache["ts"] < 10.0:
            return self._main_ocr_cache["ok"]
        from .constants import MAIN_MENU_REGION_Y, ACTIVITY_MARKERS
        region = img[MAIN_MENU_REGION_Y:, :]
        try:
            items = bot._ocr.recognize(region)
        except Exception as e:
            logger.warning(f"主界面 OCR 兜底异常（按未命中处理）: {e}")
            self._main_ocr_cache.update(ts=now, ok=False)
            return False
        menu_ok = any("成长" in it["text"] and it["confidence"] >= 0.7
                      for it in items)
        # 双文字确认（2026-09-07 用户指正）：百趣闲市等子页面同样可能露出
        # 底部菜单（"成长"可命中），但只有主界面有 经典场/排位赛 大按钮——
        # 二者同时命中才算主界面
        from .constants import MAIN_SCREEN_VENUE_MARKERS
        venue_ok = any(any(m in it["text"] for m in MAIN_SCREEN_VENUE_MARKERS)
                       and it["confidence"] >= 0.7 for it in items)
        ok = menu_ok and venue_ok
        if menu_ok and not venue_ok:
            logger.info(
                "主界面判定: 底部'成长'命中但无 经典场/排位赛 按钮，判非主界面"
            )
        if menu_ok:
            # 双重确认:上半屏出现活动按钮文字 = 全屏活动页盖着,菜单只是露出
            try:
                upper = img[:MAIN_MENU_REGION_Y, :]
                upper_items = bot._ocr.recognize(upper)
                covered = any(any(m in it["text"] for m in ACTIVITY_MARKERS)
                              and it["confidence"] >= 0.75
                              for it in upper_items)
                if covered:
                    logger.info(
                        "主界面判定: 底部菜单可见但上半屏检测到活动页特征,判非主界面"
                    )
                    ok = False
            except Exception as e:
                logger.debug(f"上半屏活动特征检测异常（按无特征处理）: {e}")
        self._main_ocr_cache.update(ts=now, ok=ok)
        if ok:
            logger.info("主界面判定: OCR 底部菜单命中'成长'（模板跨皮肤失效兜底生效）")
        return ok

    # ── 弹窗清理（启动 / 回主界面共用）──────────────

    def dismiss_popups(self, bot: GameBot, max_rounds: int = 8) -> bool:
        """
        循环关闭弹窗直到回到主界面。

        每轮按 签到 → 弹窗× → 返回 顺序尝试；每次点击后对比画面签名，
        画面无变化说明点击被遮罩吞掉（按钮属于被盖住的下层页面），
        立即换下一个手段，避免对着不可点的按钮空点。

        返回 True 表示已回到主界面。
        """
        # 对局仍在进行时禁止清理：策略里有 fanhui/退出，点了会把对局退掉
        if (getattr(self, "_zz_battle_pending", False)
                and getattr(self, "_zz_in_battle", None)
                and self._zz_in_battle(bot)):
            logger.info("对局仍在进行，跳过弹窗清理（避免退掉未结束的对局）")
            return False
        strategies = (
            ("qiandao.png", 0.85, 1.5),                  # 签到弹窗
            (POPUP_CLOSE_X, POPUP_CLOSE_X_THRESHOLD, 1.2),  # 活动弹窗底部 ×
            ("fanhui.png", 0.85, 1.2),                   # 通用返回
            ("钓鱼fanhui.png", 0.85, 1.2),               # 钓鱼界面专属返回
            ("退出.png", 0.85, 1.2),                     # 合成大将军游戏结束/对局页（竖排退出）
            ("返回1.png", 0.85, 1.2),                    # 合成大将军/小游戏返回
            (DIALOG_CANCEL, DIALOG_CANCEL_THRESHOLD, 1.0),  # 未知确认弹窗（安全取消）
            (PURCHASE_CANCEL, PURCHASE_CANCEL_THRESHOLD, 1.2),  # 体力购买框（竖排取消）
            ("再钓一次.png", 0.85, 1.5),                 # 钓鱼展示卡（无返回键，点它回钓鱼场景）
        )
        for round_idx in range(max_rounds):
            main_now = self._on_main_screen(bot)
            acted = False
            # 每轮完整跑一遍策略扫描：主界面命中不能提前返回——
            # 中央小弹窗不遮底部菜单时 OCR 会误判主界面，若此时早退
            # 弹窗永远不被清理（2026-09-07 run22 用户实测的连锁失败根因）。
            # 只有 本轮无任何手段命中 且 主界面判定成立 才算真干净。
            for tpl, thr, wait in strategies:
                if not bot.is_present(tpl, threshold=thr):
                    continue
                before = self._shot_signature(bot)
                if not bot.click_if_found(tpl, threshold=thr):
                    continue
                bot.sleep(wait)
                after = self._shot_signature(bot)
                if self._signature_diff(before, after) > 2.0:
                    acted = True
                    logger.info(
                        f"弹窗清理: 点击 '{tpl}' 生效（第 {round_idx + 1} 轮）"
                    )
                    break
                logger.debug(f"弹窗清理: '{tpl}' 点击后画面无变化，换下一手段")
            if acted:
                continue  # 清掉了东西，下一轮重新确认主界面
            if main_now:
                return True  # 主界面 且 本轮无任何弹窗手段命中 → 真干净
            bot.sleep(0.8)
        return self._on_main_screen(bot)

    # ── 启动清理主流程 ──────────────────────────────

    def _ensure_main_screen(self, bot: GameBot, max_rounds: int = 6) -> bool:
        """任务结束后统一调用：确认回到主界面。

        每轮: 主界面判定 → True；弹窗清理(2轮)有清理动作且回到主界面 → True；
        都未命中 → 点一次 fanhui 兜底。轮数用尽按最终判定返回。
        供引擎在每个任务结束后调用，保证下一个任务从主界面开始
        （2026-11-04 用户反馈：成长失败停在子页面，毒化蛋糕/合成导航）。
        """
        # 对局仍在进行时不要点 fanhui/弹窗：会把未结束的对局退掉
        if (getattr(self, "_zz_battle_pending", False)
                and getattr(self, "_zz_in_battle", None)
                and self._zz_in_battle(bot)):
            logger.info("对局仍在进行，跳过回主界面清理（避免退掉未结束的对局）")
            return False
        for _ in range(max_rounds):
            if self._on_main_screen(bot):
                return True
            if self.dismiss_popups(bot, max_rounds=2):
                return True
            bot.click_if_found("fanhui.png", threshold=0.85)
            bot.sleep(0.8)
        return self._on_main_screen(bot)

    def startup(self, bot: GameBot) -> bool:
        """
        启动清理：等加载 → 循环关弹窗（签到/×/返回）→ 确认主界面。

        返回 False 表示尝试后仍未回到主界面，引擎中止本次运行
        （所有任务都依赖主界面导航，盲跑只会全链路失败）。
        """
        t0 = time.perf_counter()
        logger.info("--- 启动清理 ---")
        bot.sleep(2)

        on_main = self.dismiss_popups(bot)
        if on_main:
            logger.info("已确认：游戏主界面")
        else:
            _warn("弹窗清理后仍未回到主界面，再等 3 秒做最后确认")
            bot.sleep(3)
            on_main = self._on_main_screen(bot)
            if on_main:
                logger.info("已确认：游戏主界面（延迟确认）")

        logger.info(f"启动清理完成，耗时 {time.perf_counter() - t0:.1f}s")
        return on_main
