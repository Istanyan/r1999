"""
单实例锁 — 心跳版。

机制:锁文件内容 = 最近心跳时间戳。
- acquire():锁存在且心跳新鲜(15s 内)→ 已有实例在跑,拒绝;
  锁过期/损坏/不存在 → 独占创建(O_EXCL 防竞争)接管。
- acquire 成功后启动守护线程每 5s 刷新心跳;进程退出(含 os._exit 强杀)
  心跳自然停止,锁 15s 后自动过期。
- 2026-11-14 重写:旧版用"PID 存活"判断,被 Windows PID 复用骗过
  (残留锁的 PID 被新进程占用 → 误判"实例在跑"),导致测试遗留实例时
  新实例启动互相冲突/两个 bot 互抢鼠标。心跳版不依赖 PID,天然免疫。
"""

import os
import sys
import threading
import time

from . import runtime

LOCK_NAME = (os.environ.get("GAMEBOT_LOCK_NAME") or "bot").strip() or "bot"
LOCK_PATH = str(runtime.temp_dir() / f"{LOCK_NAME}.lock")

_HB_INTERVAL = 5.0     # 心跳刷新间隔(秒)
_STALE_AFTER = 15.0    # 超过此秒数无心跳 → 视为陈旧锁,可接管
_hb_stop = None
_hb_thread = None
_owns_lock = False


def _write_now():
    with open(LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(str(time.time()))


def _heartbeat():
    while not _hb_stop.is_set():
        try:
            _write_now()
        except Exception:
            pass  # 心跳线程绝不能死
        _hb_stop.wait(_HB_INTERVAL)


def _start_heartbeat():
    global _hb_stop, _hb_thread
    if _hb_thread is not None and _hb_thread.is_alive():
        return
    _hb_stop = threading.Event()
    _hb_thread = threading.Thread(target=_heartbeat, daemon=True)
    _hb_thread.start()


def acquire() -> int:
    """
    尝试获取单实例锁。
    返回 0 = 获取成功(心跳已启动);
    返回 1 = 已有实例在运行(心跳新鲜,调用方应提示并退出)。
    """
    global _owns_lock
    os.makedirs(os.path.dirname(LOCK_PATH), exist_ok=True)
    if os.path.isfile(LOCK_PATH):
        try:
            ts = float(open(LOCK_PATH, encoding="utf-8").read().strip() or 0)
            if time.time() - ts < _STALE_AFTER:
                return 1
        except (ValueError, OSError):
            pass
        _release()
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(str(time.time()))
    except OSError:
        return 1
    _owns_lock = True
    _start_heartbeat()
    return 0


def release():
    """释放锁(仅当锁是自己持有的;防止误删其他实例的心跳)。"""
    global _owns_lock
    if not _owns_lock:
        return
    _owns_lock = False
    if _hb_stop is not None:
        _hb_stop.set()
    try:
        if os.path.isfile(LOCK_PATH):
            os.remove(LOCK_PATH)
    except OSError:
        pass


def _release():
    """无条件删除锁文件(陈旧锁清除用)。"""
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass


def check_my_lock():
    """进程启动时调用：已有实例则打印提示并退出进程。"""
    other = acquire()
    if other:
        print(f"已有 game_bot 实例正在运行（心跳锁 {LOCK_PATH} 新鲜），"
              "请先关闭它再启动。")
        sys.exit(1)
