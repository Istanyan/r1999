"""
游戏自动化脚本 — 主入口 & 流程编排。

本项目用于在 MuMu 模拟器中自动完成手游日常任务。
支持三国杀、重返未来1999、无期迷途等游戏的日常流程自动化。

运行方式
--------
    python main.py                    # 运行默认示例流程
    python main.py --game sanguosha  # 运行三国杀日常
"""

import argparse
import json
import logging
import sys
import os
import time
from typing import Optional, List

# 将项目根目录加入 path，方便直接运行
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.game_bot import GameBot
from core.task import Task
from core.game_script import GameScript
from core.action import StopRequested
from games.sanguosha import SanguoshaScript, HookLossError
from games.bbdc import BBDCScript
from games.deltaforce import DeltaForceScript, DF_WINDOW
from games.r1999 import R1999Script, R1999_WINDOW
from games.testkit import TestScript

# ──────────────────────────────────────────────
# 日志配置
# ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

logger = logging.getLogger("GameBot")

# ──────────────────────────────────────────────
# 游戏注册表（GUI 通过此表发现可用游戏）
# ──────────────────────────────────────────────

GAME_REGISTRY: dict[str, GameScript] = {
    "sanguosha": SanguoshaScript(),
    "bbdc": BBDCScript(),
    "deltaforce": DeltaForceScript(),
    "重返未来1999": R1999Script(),
    "测试": TestScript(),
}

# ═══════════════════════════════════════════════
# 任务执行引擎（通用，不包含任何游戏特定逻辑）
# ═══════════════════════════════════════════════

# 一次性任务连续失败达到该次数后，本次运行跳过（避免无限重试）
MAX_TASK_FAILURES = 3


def _mark_task_failed(task: Task, fail_counts: dict):
    """累计任务失败次数，达到上限则标记 done，本次运行不再重试。"""
    fail_counts[task.name] = fail_counts.get(task.name, 0) + 1
    if fail_counts[task.name] >= MAX_TASK_FAILURES:
        logger.warning(
            f"  {task.name} 连续失败 {fail_counts[task.name]} 次，本次运行跳过"
        )
        task.done = True


def execute_tasks(bot: GameBot, tasks: List[Task], stop_flag=None,
                  after_task=None) -> dict:
    """
    按顺序执行任务列表（纯引擎，不含启动清理）。

    启动清理由调用方通过 game_script.startup(bot) 先行完成。

    after_task: 可选回调，每处理完一个一次性任务后调用（任务间隙钩子，
                2026-08-30 用户建议：任务完成后插入演武检测）。异常被吞掉，
                不影响引擎；抛出 StopRequested 时照常中断。

    规则：
    - repeat=True 的任务每次循环都检查
    - repeat=False 的任务执行成功后标记 done，不再重复
    - 所有非重复任务完成后脚本退出

    返回
    ----
    dict : {"completed": int, "skipped": int, "remaining": int}
    """

    def _run_after_task_hook():
        if after_task is None:
            return
        try:
            after_task(bot)
        except StopRequested:
            raise
        except Exception as e:
            logger.warning(f"任务间隙钩子异常（忽略）: {e!r}")

    logger.info("=" * 40)
    logger.info("任务引擎启动")
    logger.info("=" * 40)

    # 注册当前线程为可急停任务线程（空格/GUI 停止均依赖此标记）
    from core.action import register_task_thread, task_finished, ack_stop
    register_task_thread()

    completed = 0
    max_loops = 50
    fail_counts: dict = {}
    idle_waits = 0
    IDLE_MAX_WAITS = 5  # 一次性任务全完成后的最大空转轮数（在线礼包等待）

    for loop in range(max_loops):
        if stop_flag and stop_flag.is_set():
            logger.info("收到停止信号，退出循环")
            break

        # 先检查重复任务
        for task in tasks:
            if stop_flag and stop_flag.is_set():
                break
            if not task.repeat or not task.enabled:
                continue

            # 冷却检查
            if task.cooldown > 0:
                elapsed = time.time() - task._last_triggered
                if elapsed < task.cooldown:
                    continue

            # 回调型重复任务（如 r1999 街机挂机：整个挂机循环就是一个回调）
            if task.callback is not None:
                logger.info(f"[重复] {task.name} (复合流程) ...")
                try:
                    success = task.callback(bot)
                    if success:
                        logger.info(f"  ✓ {task.name} 本轮完成")
                        task._last_triggered = time.time()
                    else:
                        logger.error(f"  ✗ {task.name} 本轮失败")
                except StopRequested:
                    logger.info(f"检测到停止请求 — 已中断「{task.name}」")
                    ack_stop()
                    return {"completed": completed, "skipped": 0, "remaining": 0}
                except Exception as e:
                    logger.error(f"  ✗ {task.name} 异常: {e!r}")
                _run_after_task_hook()
                continue

            logger.info(f"[重复] {task.name} ...")
            if bot.click_if_found(task.template, threshold=task.threshold):
                logger.info(f"  ✓ {task.name}")
                task._last_triggered = time.time()
                bot.sleep(0.5)
            else:
                logger.debug(f"  - {task.name} 未出现")

        # 再检查一次性任务
        had_action = False
        for task in tasks:
            if stop_flag and stop_flag.is_set():
                break
            if task.repeat or task.done or not task.enabled:
                continue

            # 回调任务（复合流程）
            if task.callback is not None:
                logger.info(f"[任务] {task.name} (复合流程) ...")
                try:
                    success = task.callback(bot)
                    if success:
                        logger.info(f"  ✓ {task.name} 完成")
                        task.done = True
                        completed += 1
                        had_action = True
                    else:
                        logger.error(f"  ✗ {task.name} 执行失败")
                        _mark_task_failed(task, fail_counts)
                except StopRequested:
                    logger.info(f"检测到停止请求 — 已中断「{task.name}」")
                    ack_stop()
                    return {"completed": completed, "skipped": 0, "remaining": 0}
                except HookLossError as e:
                    logger.error(f"  ✗ {task.name} 鱼脱钩，终止本次钓鱼（其他任务继续）: {e}")
                    task.done = True  # 标记完成，避免引擎对钓鱼无限重试
                except Exception as e:
                    logger.error(f"  ✗ {task.name} 异常: {e}")
                    _mark_task_failed(task, fail_counts)
                _run_after_task_hook()
                continue

            # 模板任务
            logger.info(f"[任务] {task.name} ...")
            if bot.click_if_found(task.template, threshold=task.threshold):
                logger.info(f"  ✓ {task.name} 完成")
                task.done = True
                completed += 1
                had_action = True
                bot.sleep(0.5)
            else:
                logger.info(f"  ✗ {task.name} 未找到，稍后重试")
            _run_after_task_hook()

        # 如一次性任务全完成且无重复任务 → 退出
        repeat_tasks = [t for t in tasks if t.repeat and t.enabled]
        one_time_tasks = [t for t in tasks if not t.repeat and t.enabled]
        all_one_time_done = all(t.done for t in one_time_tasks)

        if all_one_time_done and not repeat_tasks:
            logger.info("所有一次性任务已完成，无重复任务，退出")
            break

        if not had_action and all_one_time_done:
            # 空转计数（2026-09-07 用户要求：在线礼包空转最多 5 次即退出，
            # 不再空等 50 轮）。重复任务一旦命中则清零重新计。
            idle_waits += 1
            if idle_waits >= IDLE_MAX_WAITS:
                logger.info(
                    f"一次性任务全部完成，连续 {idle_waits} 轮在线礼包未出现，退出"
                )
                break
            logger.info(
                f"一次性任务全部完成，等待重复任务..."
                f"（空转 {idle_waits}/{IDLE_MAX_WAITS}）"
            )
            try:
                bot.sleep(30)
            except StopRequested:
                logger.info("检测到停止请求 — 退出等待")
                break
        elif had_action:
            idle_waits = 0

    logger.info("=" * 40)
    remaining = sum(1 for t in tasks if t.enabled and not t.repeat and not t.done)
    logger.info(f"任务引擎结束: 完成 {completed}, 剩余 {remaining}")
    logger.info("=" * 40)

    task_finished()
    return {"completed": completed, "skipped": 0, "remaining": remaining}


def _apply_persisted_task_config(game: str, tasks: List[Task]):
    """
    应用 GUI 保存的任务配置（勾选状态/阈值/重复/冷却），使 CLI 运行与 GUI
    行为一致（2026-08-30 用户反馈：UI 划掉的任务下次运行被重置）。
    配置文件缺失或损坏时静默忽略。失败不影响任务默认值。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "task_order.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        entry = data.get(game)
        settings = entry.get("settings", {}) if isinstance(entry, dict) else {}
        for t in tasks:
            cfg = settings.get(t.name)
            if not cfg:
                continue
            t.enabled = bool(cfg.get("enabled", t.enabled))
            try:
                t.threshold = float(cfg.get("threshold", t.threshold))
            except (TypeError, ValueError):
                pass
            t.repeat = bool(cfg.get("repeat", t.repeat))
            try:
                t.cooldown = float(cfg.get("cooldown", t.cooldown))
            except (TypeError, ValueError):
                pass
    except Exception:
        pass


def run_game(bot: GameBot, script: GameScript, tasks=None, stop_flag=None) -> bool:
    """
    运行一个游戏脚本：启动清理 → 任务执行。

    tasks: 可选，外部传入的任务列表（如 GUI 用户修改过 enabled）。
           不传则从 script.get_tasks() 获取，并应用 GUI 保存的任务配置。
    返回 True 表示执行成功。
    """
    logger.info(f"已加载游戏脚本: {script.name}")

    # 启动清理阶段也纳入急停线程（模板脚本里可能有裸 time.sleep）
    from core.action import register_task_thread, task_finished
    register_task_thread()

    # 阶段 1：启动清理
    if not script.startup(bot):
        logger.error(f"{script.name} 启动清理失败，中止")
        task_finished()
        return False

    # 阶段 2：任务执行
    if tasks is None:
        tasks = script.get_tasks()
        for t in tasks:
            t.reset()
        _apply_persisted_task_config(script.name, tasks)
        logger.info(
            "已启用任务: "
            + ", ".join(t.name for t in tasks if t.enabled)
        )
    # 任务间隙钩子（2026-11-04）：演武检测 + 统一回主界面——
    # 保证每个任务（含失败重试）都从主界面开始，失败不跨任务传染
    def _after_task(b):
        hook = getattr(script, "after_task_hook", None)
        if hook:
            hook(b)
        ens = getattr(script, "_ensure_main_screen", None)
        if ens:
            ens(b)

    execute_tasks(
        bot, tasks, stop_flag=stop_flag,
        after_task=_after_task,
    )
    return True


# ──────────────────────────────────────────────
# 游戏流程入口
# ──────────────────────────────────────────────

def run_sanguosha(bot: GameBot, stop_flag=None):
    """三国杀日常任务流程。"""
    return run_game(bot, SanguoshaScript(), stop_flag=stop_flag)


def run_bbdc(bot: GameBot, stop_flag=None, words_file=None, limit=None, fresh=False):
    """不背单词批量收藏流程。"""
    script = BBDCScript(words_file=words_file, limit=limit, fresh=fresh)
    return run_game(bot, script, stop_flag=stop_flag)


def run_example(bot: GameBot):
    """通用示例流程：演示各模块 API 的基础用法。"""
    logger.info("=" * 40)
    logger.info("示例流程开始")
    logger.info("=" * 40)

    bot.sleep(2)
    logger.info("[1] 模板匹配：查找并点击 '开始' 按钮")
    found = bot.click_if_found("templates/start_button.png")
    if not found:
        logger.info("   未找到，尝试等待...")
        bot.wait_and_click("templates/start_button.png", timeout=5)

    logger.info("[2] OCR 识别屏幕文字")
    texts = bot.recognize_text()
    for item in texts[:10]:
        logger.info(f"   OCR: '{item['text']}' 置信度 {item['confidence']:.2f} @ {item['center']}")

    logger.info("[3] OCR 查找并点击 '领取'")
    bot.find_and_click_text("领取", fuzzy=True)

    logger.info("[4] 等待 loading 画面消失")
    bot.wait_until_gone("templates/loading.png", timeout=10)

    logger.info("[5] 直接调用底层操作：按键 ESC 返回")
    bot.action.press("esc")

    logger.info("=" * 40)
    logger.info("示例流程完成")
    logger.info("=" * 40)


# ──────────────────────────────────────────────
# 命令行入口
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="游戏自动化脚本")
    parser.add_argument(
        "--game", type=str, default="example",
        choices=["example", "sanguosha", "bbdc", "deltaforce",
                 "重返未来1999", "r1999", "测试"],
        help="要执行的游戏流程 (default: example)；r1999=重返未来1999 的 ASCII 别名",
    )
    parser.add_argument(
        "--cycles", type=int, default=0,
        help="[deltaforce] 循环轮数上限，0=不限（一晚上挂机）",
    )
    parser.add_argument(
        "--words", type=str, default=None,
        help="[bbdc] 单词表路径，默认项目根目录 words.txt",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="[bbdc] 只处理前 N 个单词（试跑用）",
    )
    parser.add_argument(
        "--rounds", type=int, default=0,
        help="[r1999] 测试局数上限，达到 N 局 GAME OVER 后整脚本停止；0=不限",
    )
    parser.add_argument(
        "--score-target", type=int, default=0,
        help="[r1999] 积分超过该值后整脚本停止；0=不检查（测试期默认关闭 2000）",
    )
    parser.add_argument(
        "--no-danger-mode",
        choices=("strafe", "random", "hybrid"), default="strafe",
        help="[r1999] 无红区走位：strafe=左右交替（默认），"
             "random=受限随机，hybrid=交替+周期随机冲刺",
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="[bbdc] 忽略 temp/bbdc_done.txt 历史进度，全部重新收藏",
    )
    parser.add_argument(
        "--window", type=str, default="MuMu",
        help="模拟器窗口标题关键字 (default: MuMu)",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.85,
        help="模板匹配置信度阈值 (default: 0.85)",
    )
    args = parser.parse_args()
    if args.game == "r1999":
        args.game = "重返未来1999"   # ASCII 别名(供提权启动器/脚本使用)
    if args.game == "重返未来1999":
        # Loop 在实例化时读取这两个环境变量；CLI 参数只是方便本轮试跑。
        os.environ["R1999_MAX_RUNS"] = str(max(0, args.rounds or 0))
        os.environ["R1999_SCORE_TARGET"] = str(max(0, args.score_target or 0))
        os.environ["R1999_NO_DANGER_MODE"] = args.no_danger_mode

    logger.info(f"初始化 GameBot: window='{args.window}', game={args.game}")
    # bbdc 必须锚定 MuMu 主窗口（含标签栏）；"MuMuNxDevice" 渲染窗口的矩形
    # 会与可见内容错位（实测 2026-10-03），默认词改为 "MuMu安卓设备"
    window_title = args.window
    if args.game == "bbdc" and args.window == "MuMu":
        window_title = "MuMu安卓设备"
    elif args.game == "deltaforce" and args.window == "MuMu":
        # deltaforce 自管 WeGame/游戏双窗口（游戏窗口可能未开，惰性绑定），
        # GameBot 仅作占位 → 截主屏即可，避免因找不到游戏窗口而初始化失败
        window_title = None
    elif args.game == "重返未来1999" and args.window == "MuMu":
        # r1999 必须锚主窗「MuMu模拟器12」（渲染窗矩形会错位 250px）
        window_title = R1999_WINDOW
    # 测试脚本不依赖模板目录,game_name 传 None 避免 templates/测试/ 无效路径
    game_name = None if args.game in ("example", "测试") else args.game
    if game_name == "重返未来1999":
        game_name = "r1999"   # 模板子目录名（非中文标签）
    bot = GameBot(
        window_title=window_title,
        game_name=game_name,
        match_threshold=args.threshold,
    )

    if args.game == "sanguosha":
        run_sanguosha(bot)
    elif args.game == "bbdc":
        run_bbdc(bot, words_file=args.words, limit=args.limit, fresh=args.fresh)
    elif args.game == "deltaforce":
        run_game(bot, DeltaForceScript(cycles=args.cycles))
    elif args.game == "重返未来1999":
        run_game(bot, GAME_REGISTRY["重返未来1999"])
    elif args.game == "测试":
        run_game(bot, GAME_REGISTRY["测试"])
    else:
        run_example(bot)


if __name__ == "__main__":
    from core.instance_lock import check_my_lock, release
    check_my_lock()  # 单实例锁：防止两个 bot 同时操作模拟器（run27 教训）
    try:
        main()
    finally:
        release()
