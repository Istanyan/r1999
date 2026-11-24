# -*- coding: utf-8 -*-
"""
r1999 实时走位 bot v5 — 模板重制 + 用户策略版。(v4 异常应对框架保留)

v5 变化（2026-11-13 实机探路 + 用户反馈）：
- 模板全部重裁并有清单：templates/r1999/TEMPLATES.md（每个文件写明用途/来源/坐标系）。
  用户添加的 7 张模板已整合：mainui(主界面徽章,重裁去动画光环)/关卡1-3机器/
  查看积分(噪点追踪机)/积分上下边界锚。
- 场景新增 pause(GAME PAUSED 暂停菜单→点取消)、mainui(主界面)。
- **主界面策略（用户指定）**：非恢复态检测到主界面/标题页 = 异常脱出小游戏
  → 存证 + 立即停止脚本防误触。恢复态（冻结/超时重启后）才允许走
  冷启动链：警告→剧情(BACK)→START→主界面→横幅→(续局弹窗/活动页)→大厅。
- 关卡选择：handle_hub 用关卡机器模板(user_stage1/2/3)自适应定位点击,
  默认 STAGE_LEVEL=2(当前最高解锁,难度奖励随编号增大)。
- 积分读取全 adb 系：噪点追踪机(模板定位)→对话进入→面板→上下边界锚定
  数字区→OCR,流程实测：点机器只出对话,须再点「进入」才见积分。
- 坐标系换算实测：adb = mss + (-5, -54),内容 1:1 无缩放。
- 商店模板待 bot 实测到店后重裁(旧 shop.png/shop_buy.png 暂留)。
"""
import os
import sys
import time
import random
import logging
import subprocess
import ctypes
from collections import deque

import cv2
import numpy as np
import pyautogui
import mss

try:
    # MuMu 窗口在 150% DPI 下，GetClientRect/ClientToScreen 必须是物理像素，
    # 否则 D-pad 的屏幕坐标会按逻辑像素缩小，点在按钮外。
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0    # 默认 0.1s!click 内部 3 次调用=每次按压凭空多 0.3s 延迟
from core import runtime
from core.game_script import GameScript
from core.screenshot import Screenshot, raise_to_foreground
from core.action import (Action, StopRequested, register_task_thread,
                         ack_stop, task_finished)
try:
    # action.py 私有实现已全局接管 time.sleep;这里给 r1999 一个可引用的名字
    from core.action import _interruptible_sleep as interruptible_sleep
except Exception:                                        # pragma: no cover
    def interruptible_sleep(seconds):
        time.sleep(seconds)

pyautogui.FAILSAFE = False

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))

def imread_u(path, flags=cv2.IMREAD_COLOR):
    """OpenCV 读中文路径模板（cv2.imread 在 Windows 下会静默失败）。"""
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), flags)


logger = logging.getLogger("r1999loop")
logger.setLevel(logging.INFO)
if not logger.handlers:      # 文件日志独立于 main.py 的 basicConfig,保证溯源
    _fh = logging.FileHandler(
        str(runtime.log_dir() / "r1999_loop.log"), encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(_fh)


def log(msg):
    print(msg, flush=True)
    logger.info(msg)


def configure_runtime(*, adb=None, serial=None, window=None,
                      render_window=None, templates=None, model_dir=None):
    """便携 GUI 在创建 Loop 前可覆盖设备/资源；只影响本模块全局常量。"""
    global ADB, ADB_SERIAL, WINDOW, RENDER_WINDOW, R1999_WINDOW, TPL, MODEL_DIR
    if adb and adb != "auto":
        ADB = str(adb)
    if serial and serial != "auto":
        ADB_SERIAL = str(serial)
    if window and window != "auto":
        WINDOW = str(window)
        R1999_WINDOW = WINDOW
    if render_window and render_window != "auto":
        RENDER_WINDOW = str(render_window)
    if templates:
        TPL = str(templates)
    if model_dir:
        MODEL_DIR = str(model_dir)


ADB = str(runtime.default_adb_path())
ADB_SERIAL = runtime.default_adb_serial()
PKG = os.environ.get("R1999_PKG", "com.shenlan.m.reverse1999")
TPL = os.environ.get("R1999_TEMPLATES") or str(runtime.templates_dir() / "r1999")
WINDOW = runtime.default_window_title()
RENDER_WINDOW = runtime.default_render_window_title()   # 真正承载游戏画面的渲染窗（无工具栏）
R1999_WINDOW = WINDOW        # 注册/GUI 用别名（必须锚主窗，不能锚渲染窗）
MODEL_DIR = os.environ.get("R1999_MODEL_DIR") or str(runtime.model_dir())
TEMP = str(runtime.temp_dir())
MAX_RUNTIME = 3 * 3600
CENTER_RADIUS = 6              # 中心区：|x-7| / |y-7| <= 6
DEFAULT_TEST_RUNS = 0          # 0=不限；测试时用 --rounds N
DEFAULT_SCORE_TARGET = 0       # 测试期临时关闭 2000 积分出口；0=不检查
UNKNOWN_LIMIT = 60          # 未知界面持续上限 → 重启游戏
SETTLE = 8                  # 未知界面静观期（过渡帧不动作）
RECOVER_MIN_GAP = 4         # 恢复动作最小间隔
DANGER_CHECK_GAP = 45       # 危险页 OCR 检查最小间隔
POST_WARNING_BACK = 12      # 警告页点击后，剧情页可用 BACK 退出的窗口
POST_BANNER_WINDOW = 30     # 点横幅后弹窗/活动页判定窗口
RESTART_BREAKER = 3         # 连续重启熔断次数
FREEZE_LIMIT = 8
MACHINE = (1040, 190)
SCORE_MACHINE = (1400, 150)
DPAD = {"up": (272, 760), "dn": (258, 988),
        "lf": (233, 886), "rt": (400, 880)}
HALL_DPAD = {"up": (218, 748), "dn": (218, 1040),
             "lf": (68, 893), "rt": (368, 893)}
GAMEOVER_BLANK = (960, 560)
WARNING_TAP = (965, 565)            # 警告页：点击任意处继续
TITLE_START_ADB = (957, 800)        # 标题页 START（adb，实测）
RESUME_CONFIRM_ADB = (1210, 665)    # 「返回未完成关卡」弹窗确认（adb，实测）
START_GAME_ADB = (1640, 875)        # 活动页开始游戏（adb，实测）
DIALOG_ADVANCE_ADB = (960, 990)     # 主界面剧情对话推进（adb，实测）
DANGER_WORDS = ("充值", "支付", "微信支付", "支付宝", "人民币", "订单", "余额")
SCENES = ("gameover", "battle", "entry", "blessing", "shop", "hub",
          "warning", "title", "pause", "mainui", "reward")
GAMEPLAY_SCENES = ("battle", "hub", "blessing", "shop", "entry", "gameover")
INPUT_MODE = "win"           # "win"=先置前MuMu主窗,再用真实鼠标按压; "adb"=仅作明确兜底
STAGE_LEVEL = 2               # 刷的关卡=机器编号(难度/奖励随编号增大;3未解锁)
SCORE_ENTER_ADB = (1600, 430)     # 机器对话「进入」按钮,所有机器通用(adb)
SCORE_CLOSE_ADB = (1855, 45)      # 积分面板右上角 X(adb)
PAUSE_CANCEL_MSS = (1300, 830)    # GAME PAUSED「取消」(mss)
STAGE_MACHINE_MSS = (795, 254)    # 关卡Ⅱ机器兜底固定点(mss)
# 战斗 D-Pad(adb 坐标,= mss DPAD - (5,54);按住=swipe 同点 duration)
DPAD_ADB = {"w": (267, 706), "s": (253, 934),
            "a": (145, 820), "d": (395, 826)}
# 对照实验已结束(detect 10 局 平均56s / blind 10 局 平均~95s,盲走胜出但
# 新躲避方案吸收了盲走经验)。正式模式:detect(新躲避) 常跑不自动停。
EXP_MODE_START = "detect"
EXP_RUNS_START = 10 ** 9


class DangerPage(Exception):
    """识别到危险页面（支付/充值类）——立即终止，绝不点击。"""


OPPOSITE = {"w": "s", "s": "w", "a": "d", "d": "a"}
# boss 战参数(2026-11-15 定稿):
# - 检测:只识别顶部 boss 血条(橙红长条,全窗像素 ROI,普通关全黑)
# - 模型:boss 本体占 3×3 九宫格;血条首次出现时按当前玩家下方 4 格
#   定位一次,之后固定,不随玩家移动重算,不可进入
# - 走位:与固定 boss 中心的 Chebyshev 距离 3 为优选,2/4 为次选
BOSS_BAR_Y = (100, 160)     # 血条 ROI 全窗 y 范围
BOSS_BAR_X = (560, 1370)    # 血条 ROI 全窗 x 范围
BOSS_BAR_MIN_RED = 5000     # 红色像素数阈值(血条约 3 万+,普通关≈0)


def boss_band_for_distance(nd):
    """Boss 环带等级：距离 3 优选(2)，距离 2/4 次选(1)，其余 0。"""
    if nd == 3:
        return 2
    if nd in (2, 4):
        return 1
    return 0
# ══ [DIAG-START] 测试/诊断插桩总开关 ═══════════════════════════
# 项目结束/正式发布时:改为 False 即关闭全部诊断输出与帧缓冲;
# 代码内所有 [DIAG] 标记块可按标记整段删除,不散落。
DIAG = True
# ══ [DIAG-END] ═════════════════════════════════════════════════


def _run_cmd(args, timeout=15):
    """可中断的 adb 调用：按 30ms 轮询停止标志，按空格立即 kill 子进程。"""
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    start = time.time()
    try:
        while p.poll() is None:
            Action.check_stop()
            if time.time() - start > timeout:
                p.kill()
                break
            time.sleep(0.03)
        out, err = p.communicate()
        return p.returncode, out, err
    except StopRequested:
        try:
            p.kill()
        except Exception:
            pass
        raise


def adb_tap(x, y):
    _run_cmd([ADB, "-s", ADB_SERIAL, "shell", "input", "tap", str(x), str(y)])


def adb_back():
    _run_cmd([ADB, "-s", ADB_SERIAL, "shell", "input", "keyevent", "4"])


def adb_cap():
    """ADB 截屏：落盘 + pull。实测锁屏/遮挡时 exec-out 会卡满超时，
    每帧拖到 16s；pull 稳定约 1.3s，优先保证战斗躲避的响应。"""
    remote = "/sdcard/r1999_cap.png"
    local = os.path.join(TEMP, "r1999_cap_adb.png")
    try:
        os.makedirs(os.path.dirname(local), exist_ok=True)
    except Exception:
        pass
    _run_cmd([ADB, "-s", ADB_SERIAL, "shell", "screencap", "-p", remote],
             timeout=10)
    rc, out, err = _run_cmd(
        [ADB, "-s", ADB_SERIAL, "pull", remote, local], timeout=10)
    if not os.path.exists(local):
        return None
    im = cv2.imdecode(np.fromfile(local, dtype=np.uint8), cv2.IMREAD_COLOR)
    return im


def adb_hold(x, y, ms):
    """同点 swipe = 按住(用于战斗移动,时长 ms)；空格可立即 kill。"""
    _run_cmd([ADB, "-s", ADB_SERIAL, "shell", "input", "swipe",
              str(x), str(y), str(x), str(y), str(ms)])


def adb_swipe(x1, y1, x2, y2, ms):
    """起点到终点的滑动；若十字键是摇杆而非独立按钮，必须这样推。"""
    _run_cmd([ADB, "-s", ADB_SERIAL, "shell", "input", "swipe",
              str(x1), str(y1), str(x2), str(y2), str(ms)])


def adb_keyevent(code):
    _run_cmd([ADB, "-s", ADB_SERIAL, "shell", "input", "keyevent", str(code)])


def adb_restart_game():
    log("恢复：adb 重启游戏 ...")
    subprocess.run([ADB, "-s", ADB_SERIAL, "shell", f"am force-stop {PKG}"],
                   capture_output=True, timeout=15)
    time.sleep(3)
    subprocess.run([ADB, "-s", ADB_SERIAL, "shell",
                    f"monkey -p {PKG} -c android.intent.category.LAUNCHER 1"],
                   capture_output=True, timeout=20)
    time.sleep(28)


# 战斗棋盘实测：15×15（截图每格约 60.3×60px；此前 10×10/13×13 都错）
GRID_N = 15
CELL_W = 60.3
CELL_H = 60.0
GRID_X0 = 515
GRID_Y0 = 145
# 2026-11-14 用户实测重校:游戏机制是"所有红区都在玩家脚下发起"(boss 除外),
# 站着不动=叠区等死。0.7s 节拍太慢,改 0.3s 移动间隔,检测每帧全速跑。
MOVE_INTERVAL = 0.3


def cell_center(i, j):
    return (int(GRID_X0 + (i + 0.5) * CELL_W),
            int(GRID_Y0 + (j + 0.5) * CELL_H))


def cell_of(x, y):
    i = max(0, min(GRID_N - 1, int((x - GRID_X0) / CELL_W)))
    j = max(0, min(GRID_N - 1, int((y - GRID_Y0) / CELL_H)))
    return (i, j)


def _load_tpl(name, flags=cv2.IMREAD_COLOR):
    """模板加载:.npy 缓存优先。

    2026-11-14 发现 cv2.imread 在本机时段性读出错误内容(同文件同代码,
    不同进程匹配结果 1.0 / -0.28 随机翻转,疑似杀软实时扫描干扰 PNG 解码)。
    .npy 是纯二进制 numpy 存储,绕开 PNG 解码器,彻底规避该类故障。
    缓存文件名带 PNG 的 mtime,模板更新后自动重建。"""
    png = os.path.join(TPL, f"{name}.png")
    stamp = int(os.path.getmtime(png)) if os.path.exists(png) else 0
    npy = os.path.join(TPL, f"{name}.{stamp}.npy")
    arr = None
    try:
        if os.path.exists(npy):
            arr = np.load(npy)
    except Exception:
        arr = None
    if arr is None:
        img = imread_u(png, flags)
        if img is None:
            img = cv2.imread(png, flags)
        if img is not None:
            arr = img
            try:
                np.save(npy, arr)
            except Exception:
                pass
    if arr is None:
        return None
    if flags == cv2.IMREAD_GRAYSCALE and arr.ndim == 3:
        return cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    if flags == cv2.IMREAD_COLOR and arr.ndim == 2:
        return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)
    return arr


class Loop:
    def __init__(self):
        self._normalize_window()
        # 阻止屏保/显示器关闭(三角洲同方案):屏保下截屏与输入全部失效
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        # 进程优先级提到 ABOVE_NORMAL:战斗中 MuMu 渲染吃满 CPU,
        # bot 的感知/决策等待会被拉长 1.5-2 倍(2026-11-14 插桩实测)
        try:
            ctypes.windll.kernel32.SetPriorityClass(
                ctypes.windll.kernel32.GetCurrentProcess(), 0x8000)
        except Exception:
            pass
        # 退出 Windows 效率模式(EcoQoS):隐藏窗口的后台进程会被自动降频
        # (疑似循环 3 倍慢的元凶),显式关闭执行限速
        try:
            class _PTState(ctypes.Structure):
                _fields_ = [("ControlMask", ctypes.c_ulong),
                            ("StateMask", ctypes.c_ulong)]
            pt = _PTState(0x1, 0x0)   # 关闭 EXECUTION_SPEED 限速
            ctypes.windll.kernel32.SetProcessInformation(
                ctypes.windll.kernel32.GetCurrentProcess(), 4,
                ctypes.byref(pt), ctypes.sizeof(pt))
        except Exception:
            pass
        self.sc = Screenshot(window_title=WINDOW)
        m = self.sc.monitor
        self.act = Action(base_offset=(m["left"], m["top"]))
        self.tpls = {}
        for s in SCENES:
            p = os.path.join(TPL, f"{s}.png")
            t = _load_tpl(s)
            assert t is not None, p
            self.tpls[s] = cv2.resize(t, None, fx=0.5, fy=0.5)
        self.btns = {}
        for b in ("entry_ok", "blessing_ok", "shop_buy", "banner"):
            p = os.path.join(TPL, f"{b}.png")
            t = _load_tpl(b)
            assert t is not None, p
            self.btns[b] = t
        self.machines = {}
        for lv in (1, 2, 3):
            p = os.path.join(TPL, f"user_stage{lv}.png")
            t = _load_tpl(f"user_stage{lv}")
            assert t is not None, p
            self.machines[lv] = t
        # 十字键：判断"还在小游戏内"的硬标志（大厅/战斗都有，菜单类页面没有）
        # dpad_cross.png 只保留十字内芯、四角背景透明；避免左侧边缘背景变化导致
        # 整块模板匹配分数骤降（用户实测走到左边缘后“不动/不躲”）。
        self.dpad_tpl = cv2.resize(
            _load_tpl("dpad"), None, fx=0.5, fy=0.5,
            interpolation=cv2.INTER_AREA)
        self.dpad_cross = _load_tpl("dpad_cross", cv2.IMREAD_UNCHANGED)
        if self.dpad_cross is not None and self.dpad_cross.shape[2] == 4:
            bgr = cv2.resize(self.dpad_cross[:, :, :3], None, fx=0.5, fy=0.5,
                             interpolation=cv2.INTER_AREA)
            alpha = cv2.resize(self.dpad_cross[:, :, 3], None, fx=0.5, fy=0.5,
                               interpolation=cv2.INTER_NEAREST)
            self.dpad_cross_tpl = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            self.dpad_cross_mask = (alpha > 127).astype(np.uint8) * 255
        else:
            self.dpad_cross_tpl = None
            self.dpad_cross_mask = None
        self._last_dpad_score = 0.0
        # 商店伤害词条模板（用户提供；中文文件名必须走 imread_u）
        self.shop_attr_tpls = {
            name: _load_tpl(name, cv2.IMREAD_GRAYSCALE)
            for name in ("长度", "宽度", "伤害增幅", "暴击", "伤害")}
        # 祝福优先模板
        self.bless_tpls = {
            name: _load_tpl(name)
            for name in ("祝福1", "祝福2")}
        # 关卡清场过渡页（无十字键、右上角有「返回界面」）
        self.return_tpl = _load_tpl("返回界面")
        # 结算统计页（有时 GAME OVER 大字还没浮现，只有「通关数量」统计）
        self.gameover_stats_tpl = _load_tpl("gameover_stats")
        self.adb_tpls = {}
        for a in ("adb_score_title", "adb_score_icon", "user_score_top",
                  "user_score_bottom", "user_score_btn", "claim"):
            p = os.path.join(TPL, f"{a}.png")
            t = _load_tpl(a)
            assert t is not None, p
            self.adb_tpls[a] = t
        self.start = time.time()
        self.runs = 0
        self.stop_reason = None
        # GUI 可注入实时状态回调；CLI 保持 None。
        self.status_cb = None
        try:
            self.max_test_runs = max(
                0, int(os.environ.get("R1999_MAX_RUNS",
                                      str(DEFAULT_TEST_RUNS)) or 0))
        except (TypeError, ValueError):
            self.max_test_runs = DEFAULT_TEST_RUNS
        try:
            self.score_target = max(
                0, int(os.environ.get("R1999_SCORE_TARGET",
                                      str(DEFAULT_SCORE_TARGET)) or 0))
        except (TypeError, ValueError):
            self.score_target = DEFAULT_SCORE_TARGET
        self.last_score = None
        self.pending_score = False
        self._startup_ghost = False
        # 异常应对状态
        self.unrecognized_since = None
        self.last_rec_action = 0.0
        self.last_danger_check = 0.0
        self.stage_until = {"post_warning": 0.0, "post_banner": 0.0}
        self.restart_streak = 0
        self.recovery_mode = False   # 冻结/超时重启后的冷启动链通行证
        self.shop_done = False       # 本次商店是否已买过（防止重复购买）
        self.shop_no_exit = 0        # 商店无可靠出口的连续次数
        self.shop_entry_shot = False # 本次商店入口截图是否已保存
        self.shop_leave_until = 0.0  # 点过放弃后的等待窗口，避免连点
        self.stage_clear_pending = False  # 刚从清场页进商店：出口用「放弃」继续下一关
        self._adb_mode_until = 0.0   # mss 截错窗口时，短时改用 ADB 画面
        self.last_pc = None          # 上一帧玩家格（检测偶发丢失时沿用）
        self.battle_start_pc = None  # 本场战斗开始坐标
        self.battle_moves = []       # 本场战斗移动记录
        self._logged_pc = None       # 上一次已写日志的玩家格
        self.battle_intro_done = False  # 进场动画期间不可操作，等它结束
        self._edge_target = None        # 边缘静止怪专用追击目标
        self._edge_hist = {}            # 边缘怪连续出现跟踪
        self._edge_blacklist = set()    # 多次攻击不消失的假目标
        self._edge_attempts = {}        # 同一目标已尝试攻击次数
        self._edge_since = 0.0          # 当前边缘目标的锁定起点
        self._mob_tpls = []             # 像素生物模板(顶边/左边实拍)
        for _tpl_name in ("monster_top", "monster_left"):
            _p = os.path.join(TPL, _tpl_name + ".png")
            _tpl = cv2.imread(_p)
            if _tpl is not None:
                self._mob_tpls.append(_tpl)
        self._boss_tpl = None
        self._boss_fallback_cell = None
        self._input_focused = False
        self._last_monitor_t = 0.0   # 最近一张实时战斗帧保存时间
        self._input_fail_streak = 0  # 连续“按了但角色不换格”的次数
        self.dpad_geom = None        # 启动/换场景时定位的十字键 bbox 与四向中心
        self._last_dpad_geom = None  # 最近一次成功定位，过渡帧兜底
        self.strafe_dir = "d"        # 无危险时左右交叉移动的方向
        self._danger_mem = {}        # 危险格短期记忆：倒计时期间别又走回去
        self.dpad_missing = 0        # 十字键连续缺失计数（脱出保护）
        self._ocr = None
        self._ocr_broken = False
        self._warm_ocr()
        # 对照实验(用户设计):detect=红区检测躲避 / blind=不检测纯移动绕圈
        # 各 10 局,指标=每局存活时长+商店遇见次数(每店=过一关),自动切换
        # 2026-11-14: detect 组已跑完(平均存活56s 商店2),从 blind 剩余 5 局续跑
        self.exp_mode = EXP_MODE_START
        self.exp_runs_left = EXP_RUNS_START
        self.exp_stats = {"detect": [], "blind": []}   # [(存活秒, 商店次数)]
        self.no_danger_mode = os.environ.get(
            "R1999_NO_DANGER_MODE", "strafe").strip().lower()
        if self.no_danger_mode not in ("strafe", "random", "hybrid"):
            self.no_danger_mode = "strafe"
        self.hybrid_safe_steps = 0
        self.hybrid_next_burst = random.choice((3, 4, 5))
        self._mob_hist = {}          # 怪物候选格连续出现跟踪：{(i,j): (t,count)}
        self.mode_stats = {}        # {"strafe"/"random"/"hybrid": [(存活秒, 商店次数)]}
        self.run_start_t = None
        self.run_shops = 0
        self.blind_up_left = 5      # 盲走开局:先上移 5 格
        self.blind_corner = 0       # 盲走环路当前目标角索引
        self.last_burst_dir = None  # 上一次逃逸方向(不回头规则)
        self.last_burst_box = None
        self.last_burst_until = 0.0
        self.random_prev_dirs = []   # B 组：最近两次随机方向，禁止连续复用
        self.escape_commit = None   # 逃逸承诺 (dir, box, until)——跨帧锁方向
        self.momentum = None        # 逃逸惯性 (dir, 剩余格数)——逃出后顺势连跑
        # P12 屏幕红区闩锁：一旦从屏幕红区开始逃，锁方向连续走，不再每帧被
        # screen_escape 清空后重规划。left 走到 0 或目标格不安全才释放。
        self.screen_exit_dir = None
        self.screen_exit_left = 0
        self.screen_exit_until = 0.0
        self.boss_seen_until = 0.0  # boss 血条命中后的短时模式(2s)
        self.last_commit_dir = None
        self.last_commit_box = None
        self.last_commit_until = 0.0
        self._stuck_n = 0
        self._stuck_ref = None
        # [DIAG] 死前录像缓冲:战斗帧环形队列(约 5s),死亡时落盘
        self._frame_buf = deque(maxlen=12)
        log("loop v4 初始化完成")

    # ── 窗口尺寸守卫：全部模板/坐标以 1930×1139 为前提 ──
    def _normalize_window(self):
        import ctypes
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, WINDOW)
        if not hwnd:
            return
        rect = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if (w, h) != (1930, 1139):
            log(f"窗口尺寸 {w}×{h} ≠ 1930×1139 → 自动恢复标准尺寸")
            user32.MoveWindow(hwnd, max(0, rect.left), max(0, rect.top),
                              1930, 1139, True)
            time.sleep(1.2)

    # ── OCR（积分读取 + 危险页门禁共用；启动时预热）──
    def _warm_ocr(self):
        try:
            from paddleocr import PaddleOCR
            # 模型放纯 ASCII 路径:paddle C++ 引擎打不开中文用户名下的
            # ~/.paddleocr 缓存路径(2026-11-13 实测 NotFound)
            dst = MODEL_DIR
            self._ocr = PaddleOCR(
                lang="ch", show_log=False,
                det_model_dir=os.path.join(dst, "det", "ch_PP-OCRv4_det_infer"),
                rec_model_dir=os.path.join(dst, "rec", "ch_PP-OCRv4_rec_infer"),
                cls_model_dir=os.path.join(dst, "cls", "ch_ppocr_mobile_v2.0_cls_infer"))
            log(f"OCR 预热完成(模型路径: {dst})")
        except Exception as e:
            self._ocr_broken = True
            log(f"OCR 不可用（危险页门禁降级为定时器判定）: {e!r}")

    def ocr_lines(self, img):
        """返回文本行列表；OCR 不可用/失败返回 None。"""
        if self._ocr is None:
            return None
        try:
            res = self._ocr.ocr(img, cls=False)
        except Exception as e:
            log(f"OCR 失败: {e!r}")
            return None
        lines = (res[0] or []) if res and res[0] else []
        return [l[1][0] for l in lines]

    def _desktop_frame(self):
        """前台时直接截游戏客户区（物理像素），比 ADB 快一个数量级。
        被浏览器遮挡时返回 None，由 ADB 兜底，避免截错窗口。"""
        user32 = ctypes.windll.user32
        fg = user32.GetForegroundWindow()
        render = user32.FindWindowW(None, RENDER_WINDOW)
        main = user32.FindWindowW(None, WINDOW)
        if fg not in (render, main) and not getattr(self, "_force_fast", False):
            return None
        try:
            sx, sy = self.render_click_point(5, 54)   # canvas 游戏画面左上角
            # mss 实例持久复用:每帧新建实例的 DC 初始化要 ~0.3-0.5s,
            # 是战斗节拍的最大瓶颈(2026-11-14 盲走实测 1.35s/步)
            if not getattr(self, "_sct_fast", None):
                self._sct_fast = mss.mss()
            raw = np.array(self._sct_fast.grab({"left": sx, "top": sy,
                                                "width": 1920, "height": 1080}))
            if raw.shape[0] != 1080 or raw.shape[1] != 1920:
                raw = cv2.resize(raw, (1920, 1080), interpolation=cv2.INTER_AREA)
            canvas = np.zeros((1139, 1930, 3), np.uint8)
            canvas[54:54 + 1080, 5:5 + 1920] = raw[:, :, :3]
            return canvas
        except Exception:
            return None

    def cap(self):
        # 2026-11-13 实测：MuMu 被浏览器遮住时 mss 截到的是别的窗口，
        # 会误判成 hub/shop 并乱点（run30 从商店却识别成 hub）。
        # 前台优先走桌面快速截屏；被遮挡时回退 ADB，保证识别仍正确。
        self._normalize_window()
        if self.sc.sync_window_rect():
            self.dpad_geom = None
        m = self.sc.monitor
        self.act.offset_x, self.act.offset_y = m["left"], m["top"]
        fast = self._desktop_frame()
        if fast is None:
            # MuMu 不在前台(用户看日志/聊天)→ adb_frame 每帧 765ms,
            # 躲避全部慢半拍。节流拉回前台后再试一次快截。
            if time.time() - getattr(self, "_last_fg_pull", 0) > 5.0:
                self._last_fg_pull = time.time()
                self.ensure_foreground()
            fast = self._desktop_frame()
        if fast is not None:
            if not getattr(self, "_fast_logged", False):
                self._fast_logged = True
                log("截屏: 前台桌面快速帧")
            return fast
        img = self.adb_frame()
        if img is not None:
            return img
        return np.ascontiguousarray(self.sc.capture())

    def locate_dpad(self, img):
        """在左下固定区域找米黄色十字键，取 bbox 中心，再推导上下左右四向中心。
        坐标即 mss 画布坐标；窗口移动/缩放后缓存失效会重新定位。"""
        roi = img[620:1139, 20:500]
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        m = ((hsv[:, :, 2] > 120) & (hsv[:, :, 1] < 120)).astype(np.uint8) * 255
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        n, lab, st, ce = cv2.connectedComponentsWithStats(m)
        best = None
        for i in range(1, n):
            area, w, h = int(st[i, 4]), int(st[i, 2]), int(st[i, 3])
            if area < 3000 or not (200 <= w <= 420 and 200 <= h <= 420):
                continue
            if not (0.72 <= w / max(1, h) <= 1.38):
                continue
            if best is None or area > best[0]:
                best = (area, w, h, st[i, 0] + 20, st[i, 1] + 620)
        if not best:
            return None
        _, w, h, x0, y0 = best
        cx, cy = x0 + w // 2, y0 + h // 2
        geom = {
            "bbox": (x0, y0, x0 + w, y0 + h),
            "center": (cx, cy),
            "w": (cx, y0 + int(h * 0.10)),
            "s": (cx, y0 + int(h * 0.90)),
            "a": (x0 + int(w * 0.10), cy),
            "d": (x0 + int(w * 0.90), cy),
        }
        self.dpad_geom = geom
        self._last_dpad_geom = geom
        log(f"十字键定位 bbox=({x0},{y0})..({x0+w},{y0+h}) 中心=({cx},{cy}) "
            f"上={geom['w']} 下={geom['s']} 左={geom['a']} 右={geom['d']}")
        return geom

    def adb_frame(self):
        """ADB 截屏贴到 mss 画布 (5,54)，不依赖窗口前台/遮挡。"""
        im = adb_cap()
        if im is None:
            return None
        canvas = np.zeros((1139, 1930, 3), np.uint8)
        h, w = im.shape[:2]
        if h != 1080 or w != 1920:
            im = cv2.resize(im, (1920, 1080), interpolation=cv2.INTER_AREA)
        canvas[54:54 + 1080, 5:5 + 1920] = im
        return canvas

    def render_click_point(self, x, y):
        """mss 画布坐标 → MuMuPlayer 渲染窗客户区屏幕坐标。
        游戏画面在渲染窗，不在带工具栏的主窗；这是 D-pad 点不中的根因。"""
        import ctypes.wintypes as wt
        user32 = ctypes.windll.user32
        hwnd = user32.FindWindowW(None, RENDER_WINDOW)
        if not hwnd:
            return self.act._to_screen(x, y)
        c = wt.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(c))
        pt = wt.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(pt))
        cw, ch = c.right - c.left, c.bottom - c.top
        adb_x, adb_y = x - 5, y - 54          # mss 画布 → ADB 1920×1080
        sx = pt.x + int(adb_x * cw / 1920)
        sy = pt.y + int(adb_y * ch / 1080)
        return sx, sy

    def ensure_foreground(self):
        """把 MuMu 主窗置前；游戏无焦点时会吞点击（其他脚本踩过）。"""
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, WINDOW)
            if not hwnd:
                return False
            if user32.GetForegroundWindow() == hwnd:
                return True
            ok = raise_to_foreground(hwnd)
            time.sleep(0.2)
            return ok or user32.GetForegroundWindow() == hwnd
        except Exception as e:
            log(f"窗口置前失败: {e!r}")
            return False

    def focus_game_input(self):
        """给游戏真正的键盘焦点：只 SetForegroundWindow/点标题栏不够，
        实测必须点一次游戏画布，MuMu 才会把方向键转发进游戏。"""
        if INPUT_MODE != "win":
            return True
        import pyautogui
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, WINDOW)
            if not hwnd:
                return False
            if user32.GetForegroundWindow() != hwnd:
                raise_to_foreground(hwnd)
                time.sleep(0.15)
            # 实测：点棋盘内部空白（不是标题栏、不是 D-pad）后方向键才会被转发。
            try:
                sx, sy = self.render_click_point(525, 284)  # raw ≈ (520,230)
            except Exception:
                sx, sy = 900, 300
            pyautogui.click(sx, sy)
            time.sleep(0.18)
            return user32.GetForegroundWindow() == hwnd
        except Exception as e:
            log(f"键盘焦点获取失败: {e!r}")
            return False

    def real_click(self, x, y):
        if INPUT_MODE == "adb":
            adb_tap(x - 5, y - 54)   # mss→adb 换算(实测 1:1,偏移 -5,-54)
            return
        # 2026-11-13 实测结论：MuMu 前台 + 80ms 真实按压才可靠；
        # 瞬时 click 会被输入采样丢掉（deltaforce 同结论）。
        self.ensure_foreground()
        sx, sy = self.render_click_point(x, y)
        pyautogui.moveTo(sx, sy)
        time.sleep(0.04)
        pyautogui.mouseDown()
        time.sleep(0.08)
        pyautogui.mouseUp()

    # ── 场景分类：各模板取最高分，过各自阈值才算命中 ──
    TH = {s: (0.68 if s == "battle" else 0.75 if s == "mainui" else 0.72)
          for s in SCENES}

    def classify(self, img, want_loc=False):
        half = cv2.resize(img, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
        best, bv, bloc = None, -1.0, (0, 0)
        for s in SCENES:
            res = cv2.matchTemplate(half, self.tpls[s], cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(res)
            if mv > bv:
                best, bv, bloc = s, mv, ml
        if bv < self.TH[best]:
            best = "unrecognized"
        return (best, bv, bloc) if want_loc else best

    def save_evidence(self, img, tag):
        ts = time.strftime("%H%M%S")
        p = os.path.join(TEMP, f"r1999_{tag}_{ts}.png")
        cv2.imwrite(p, img)
        log(f"存证: {p}")
        return p

    def click_btn(self, img, name, timeout=5, required=True):
        """全分辨率模板匹配 + 方差/橙色护栏 + 点击（tpls/btns 双查找）。"""
        t = self.btns.get(name)
        if t is None:
            t = self.tpls.get(name)
        full = _load_tpl(name)
        if full is None:
            full = t
        th, tw = full.shape[:2]
        deadline = time.time() + timeout
        while time.time() < deadline:
            cur = self.cap()
            res = cv2.matchTemplate(cur, full, cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(res)
            if mv >= 0.8:
                region = cur[ml[1]:ml[1] + th, ml[0]:ml[0] + tw]
                rb = float(region[:, :, 2].mean() - region[:, :, 0].mean())
                if float(region.std()) < 32 or rb < 30:
                    time.sleep(0.5)
                    continue
                self.real_click(ml[0] + tw // 2, ml[1] + th // 2)
                log(f"点击 {name} conf={mv:.2f}")
                return True
            time.sleep(0.4)
        if required:
            log(f"{name} 超时")
        return False

    # ── 危险页门禁 ──
    def danger_check(self):
        """OCR 检查危险页。命中 → 抛 DangerPage（绝不点击）。节流。"""
        if self._ocr is None:
            return
        if time.time() - self.last_danger_check < DANGER_CHECK_GAP:
            return
        self.last_danger_check = time.time()
        img = adb_cap()
        if img is None:
            return
        lines = self.ocr_lines(img) or []
        txt = "".join(lines)
        for w in DANGER_WORDS:
            if w in txt:
                self.save_evidence(img, "danger")
                try:
                    import winsound
                    for _ in range(3):
                        winsound.Beep(2200, 400)
                        time.sleep(0.15)
                except Exception:
                    pass
                raise DangerPage(f"画面出现危险关键词「{w}」，立即终止: {txt[:80]}")

    # ── 状态化恢复链（每步先验证再动作，无盲点击）──
    def recovery_step(self, img):
        """未知界面稳定后调用：按状态执行一步验证过的动作。"""
        now = time.time()
        if now - self.last_rec_action < RECOVER_MIN_GAP:
            return
        self.last_rec_action = now

        # 1) 警告页点击后的窗口：剧情动画页 → BACK 退出（实测）
        if now < self.stage_until["post_warning"]:
            log("恢复链：剧情页 → adb BACK")
            adb_back()
            time.sleep(2)
            return

        # 2) 点横幅后的窗口：弹窗 / 活动页判定（OCR 优先）
        if now < self.stage_until["post_banner"]:
            self.danger_check()
            img2 = adb_cap()
            lines = self.ocr_lines(img2) if img2 is not None else None
            txt = "".join(lines) if lines else ""
            if txt:
                if "未完成" in txt:
                    log("恢复链：检测到「返回未完成关卡」弹窗 → 确认")
                    self.save_evidence(img2, "resume_popup")
                    adb_tap(*RESUME_CONFIRM_ADB)
                    time.sleep(6)
                    return
                if "开始游戏" in txt:
                    log("恢复链：活动页「开始游戏」→ 进入")
                    self.save_evidence(img2, "activity_page")
                    adb_tap(*START_GAME_ADB)
                    time.sleep(6)
                    return
                log(f"恢复链:OCR 未命中弹窗/按钮（{txt[:40]}）,继续等待")
            elif self._ocr is None and \
                    now > self.stage_until["post_banner"] - (POST_BANNER_WINDOW - 8):
                # OCR 不可用：弹窗是横幅点击后唯一已知阻塞态，前置条件
                # （我们刚点过横幅）已模板验证 → 定时器兜底确认
                log("恢复链:OCR 不可用,定时器兜底确认续局弹窗")
                adb_tap(*RESUME_CONFIRM_ADB)
                time.sleep(6)
            return

        # 3) 其他未知界面：什么都不点，交给静观/超时重启
        self.danger_check()

    # ── 积分读取(全 adb 系:机器→对话→进入→面板→下锚+宽带OCR)──
    def _score_panel_state(self, img, reload=False):
        """积分面板状态:下锚(「向投币"」文字行,跨面板状态位置稳定)确认,
        并扫描「领取」按钮。返回 (面板确认?, [(x,y)...], 下锚坐标或 None)。

        旧模板只覆盖早期积分面板；当前「噪点追踪机」奖励页布局变化后
        下锚模板只有约 0.44，不能直接判死。模板失败时用 OCR 标题兜底。
        """
        bot = self.adb_tpls["user_score_bottom"]
        if reload:
            bot = _load_tpl("user_score_bottom")
        res = cv2.matchTemplate(img, bot, cv2.TM_CCOEFF_NORMED)
        mv, _, ml, _ = cv2.minMaxLoc(res)
        if mv >= 0.75:
            claims = []
            ct = self.adb_tpls.get("claim")
            if ct is not None:
                res2 = cv2.matchTemplate(img, ct, cv2.TM_CCOEFF_NORMED)
                ys, xs = np.where(res2 >= 0.75)
                for x, y in zip(xs, ys):
                    if all(abs(x - px) > 60 or abs(y - py) > 60
                           for px, py in claims):
                        claims.append((x, y))
            return True, claims, ml
        # 新面板 OCR 兜底：大字标题「噪点追踪机」是面板存在的强判据。
        lines = self.ocr_lines(img) or []
        text = "".join(lines)
        if "噪点追踪机" in text or "单向投币" in text:
            return True, [], None
        return False, [], None

    def read_score(self, panel_open=False):
        if self._ocr is None:
            return None
        if not panel_open:
            # 回大厅后镜头有复位动画，先等画面稳定再点机器
            interruptible_sleep(2.5)
            img = adb_cap()
            if img is None:
                return None
            btn = self.adb_tpls["user_score_btn"]
            res = cv2.matchTemplate(img, btn, cv2.TM_CCOEFF_NORMED)
            _, mv, _, ml = cv2.minMaxLoc(res)
            if mv >= 0.75:
                bh, bw = btn.shape[:2]
                log(f"噪点追踪机 conf={mv:.2f} @ {ml} → 点击")
                adb_tap(ml[0] + bw // 2, ml[1] + bh // 2 - 30)
            else:
                log("噪点追踪机未定位 → 跳过本次积分读取（不盲点固定坐标）")
                return None
            # 机器点击可能落在镜头复位动画上(2026-11-13 晚 7 次全落空):
            # 确认对话真的弹出（底部橙色「进入」条 R-B>60）才继续
            dlg_ok = False
            for attempt in range(2):
                interruptible_sleep(1.8)
                dlg = adb_cap()
                if dlg is None:
                    continue
                roi = dlg[395:480, 1360:1880]
                rb = float(roi[:, :, 2].mean() - roi[:, :, 0].mean())
                if rb > 60:
                    dlg_ok = True
                    break
                if attempt == 0:
                    log(f"机器对话未弹出(R-B={rb:.0f}) → 重试点击噪点追踪机")
                    adb_tap(ml[0] + bw // 2, ml[1] + bh // 2 - 30)
            if not dlg_ok:
                log("积分: 机器对话两次未弹出 → 放弃本次读分（不盲点「进入」）")
                return None
            adb_tap(*SCORE_ENTER_ADB)   # 机器对话「进入」(各机器按钮同位)
            time.sleep(2.5)
        img = adb_cap()
        if img is None:
            return None
        ok, claims, mlb = self._score_panel_state(img)
        if not ok:
            # 防御:环境幽灵曾让模板读旧 → 重载重试一次
            log("积分面板下锚首试未命中 → 重载模板重试")
            ok, claims, mlb = self._score_panel_state(img, reload=True)
        if not ok:
            self.save_evidence(img, "score_panel")
            log("积分面板下锚未命中,跳过读分（面板可能未开,不点 X）")
            return None
        if claims:
            # 积分跨档后的免费奖励(2026-11-14 实测一次跨三档)
            log(f"积分面板:发现可领奖励 ×{len(claims)} → 逐一领取")
            for (cx, cy) in claims:
                adb_tap(cx + 82, cy + 67)
                interruptible_sleep(1.0)
            interruptible_sleep(1.5)
            img = adb_cap()
            if img is None:
                return None
        # 数字读取带(自适应面板位移):下锚上溯 190px,两种实测布局
        # (数字 y235-325 / y290-345)均被覆盖;下锚位置跨状态稳定(1.000)
        ok2, _, mlb2 = self._score_panel_state(img)
        if not ok2:
            self.save_evidence(img, "score_panel")
            log("积分面板下锚复核未命中,跳过读分")
            adb_tap(*SCORE_CLOSE_ADB)
            time.sleep(1.5)
            return None
        if mlb2 is None:
            # 新面板没有旧下锚：数字固定在标题左下角，直接读这个区域。
            crop = img[200:340, 110:430]
        else:
            bx, by = mlb2
            x0 = max(0, bx - 95)
            x1 = min(img.shape[1], bx + 210)
            crop = img[max(0, by - 190):max(0, by - 6), x0:x1]
        cv2.imwrite(os.path.join(TEMP, "r1999_score.png"), crop)
        cv2.imwrite(os.path.join(TEMP, "r1999_score_panel.png"),
                    img)
        lines = self.ocr_lines(crop) or []
        score = None
        for ln in lines:
            tok = ln.replace(",", "").replace(".", "").strip()
            if tok.isdigit():
                score = int(tok)
                break
        if score is not None:
            delta = f"（{score - self.last_score:+d}）" if self.last_score else ""
            log(f"★ 积分: {score} {delta}")
            self.last_score = score
        else:
            log(f"积分读取失败 raw={lines[:3]!r}")
        adb_tap(*SCORE_CLOSE_ADB)
        time.sleep(1.5)
        return score

    # ── 十字键守卫（用户指定：大厅/战斗必有十字键，菜单类页面没有）──
    def has_dpad(self, img, half=None):
        if half is None:
            half = cv2.resize(img, None, fx=0.5, fy=0.5,
                              interpolation=cv2.INTER_AREA)
        # 只在左下角预期区域找，不把整张图当背景；十字模板只带十字内芯，
        # 左侧边缘/战斗背景变化不会影响分数。
        if self.dpad_cross_tpl is not None:
            roi = half[280:540, 20:270]
            if (roi.shape[0] >= self.dpad_cross_tpl.shape[0] and
                    roi.shape[1] >= self.dpad_cross_tpl.shape[1]):
                gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
                res = cv2.matchTemplate(
                    gray, self.dpad_cross_tpl, cv2.TM_CCOEFF_NORMED,
                    mask=self.dpad_cross_mask)
                self._last_dpad_score = float(cv2.minMaxLoc(res)[1])
                return self._last_dpad_score >= 0.62
        res = cv2.matchTemplate(half, self.dpad_tpl, cv2.TM_CCOEFF_NORMED)
        self._last_dpad_score = float(cv2.minMaxLoc(res)[1])
        return self._last_dpad_score >= 0.65

    def _danger_escape_axis(self, danger, pc):
        """倒计时红区不一定是正方形（常见 3×5）。

        四方向计算走出红区(到框外一格)所需步数,选最短;
        落点出界的方向直接剔除(2026-11-14 19:51 局复盘:玩家被
        "逃逸a"反复钉死在 x=0 边缘,旧版只比远近不看出界)。
        返回 (方向, 轴名, 红区外接框)，无可行方向返回 (None,None,框)。
        """
        if not danger:
            return None, None, None
        # 取最大连通块，避免零散红字把外接框拉大
        comps = []
        seen = set()
        for c in danger:
            if c in seen:
                continue
            stack, comp = [c], []
            seen.add(c)
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nb = (cur[0] + di, cur[1] + dj)
                    if nb in danger and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
            comps.append(comp)
        comp = max(comps, key=len)
        xs = [c[0] for c in comp]
        ys = [c[1] for c in comp]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        cands = []          # 落点离边≥1(同 _escape_from_box 两级约束)
        cands_loose = []    # 严格界内(退让级)
        for d, cur, lo, hi in (
            ("a", pc[0], minx, maxx),
            ("d", pc[0], minx, maxx),
            ("w", pc[1], miny, maxy),
            ("s", pc[1], miny, maxy),
        ):
            steps = (cur - lo + 1) if d in ("a", "w") else (hi - cur + 1)
            steps = max(1, steps)          # 玩家不在框内时也至少走 1 步远离
            end = cur - steps if d in ("a", "w") else cur + steps
            if not (0 <= end < GRID_N):
                continue                    # 逃出框前就出界 → 方向不可用
            cands_loose.append((steps, d))
            if 1 <= end <= GRID_N - 2:
                cands.append((steps, d))
        pool = cands
        if pool:
            pool.sort()
            d = pool[0][1]
            return d, ("x" if d in ("a", "d") else "y"), \
                   (minx, maxx, miny, maxy)
        # 四方向全不可行(全图框/贴角) → 不给方向,交回常规走位
        return None, None, (minx, maxx, miny, maxy)

    # ── 10×10 走位评分：优先远离危险，其次别贴边，再远离怪物 ──
    def _best_path(self, pc, danger, occupied, max_steps=3, mobs=None,
                   mob_first=False, escape_dir=None, prefer_mobs=False,
                   soft=None):
        dirs = {"w": (0, -1), "s": (0, 1), "a": (-1, 0), "d": (1, 0)}
        q = deque([(pc, [])])
        seen = {pc}
        best_path, best_score = [], None
        start_dd = min(abs(pc[0] - x) + abs(pc[1] - y)
                       for x, y in danger) if danger else 99
        # 预警可能覆盖很大一片：只要落点不在预警格即可，不能强求
        # “必须比现在更远”。旧版 required_dd=start_dd 会让 BFS 在
        # x=10/y=10 找不到向左的安全路径，只剩向右贴边，随后来回摆。
        required_dd = 1
        while q:
            cur, path = q.popleft()
            if path:
                dd = min(abs(cur[0] - x) + abs(cur[1] - y)
                         for x, y in danger) if danger else 99
                edge = min(cur[0], GRID_N - 1 - cur[0],
                           cur[1], GRID_N - 1 - cur[1])
                center = GRID_N // 2
                off_center = max(abs(cur[0] - center), abs(cur[1] - center))
                # 用户反复强调不要贴边：离边 1 格惩罚 3、2 格惩罚 2，
                # 比“远离危险/沿逃逸轴”更优先；否则会被 BFS 推到 x=13。
                edge_bad = 3 if edge <= 1 else 2 if edge <= 2 else \
                    1 if off_center > CENTER_RADIUS else 0
                # 白怪/白色攻击区只做软代价：不并入 danger，不封路。
                # 同一路径优先选择“白格少、白邻格少”的落点；没有安全
                # 替代时仍然允许穿过，避免把整块白区当成硬墙绕死。
                # 注意要检查路径上的每一格，而不只是终点：否则 BFS
                # 可能选一条首步踩白格、终点干净的路径。
                soft_bad = 0
                if soft:
                    # path 存的是方向名；回放成格子，检查路径上每一步，
                    # 而不是只看终点。起点本身不计软代价。
                    path_cells = [pc]
                    cx, cy = pc
                    for pd in path:
                        sdi, sdj = dirs[pd]
                        cx += sdi
                        cy += sdj
                        path_cells.append((cx, cy))
                    for sc in set(path_cells[1:]):
                        if sc in soft:
                            soft_bad += 9
                        for sdi, sdj in dirs.values():
                            if (sc[0] + sdi, sc[1] + sdj) in soft:
                                soft_bad += 3
                md = min(abs(cur[0] - x) + abs(cur[1] - y)
                         for x, y in mobs) if mobs else 99
                axis_good = 1 if (escape_dir and path and path[0] == escape_dir) else 0
                if mob_first:
                    # 追怪：md 是到目标怪的距离，必须越小越好；旧版用
                    # (md,...) 且取最大值，实际每次都在“远离怪物”，
                    # 这就是明明看到静止怪却一直绕开的直接原因。
                    score = (-md, -soft_bad, edge, min(dd, 2), -len(path))
                else:
                    # 规避预警：先别贴边，再保证 dd 达标，最后沿红区短轴。
                    # 旧版把 dd 放在第一位，会在 x=11 为了多远离一格而继续
                    # 往 x=13 走，直接贴边；这里把 edge_bad 提到第一位。
                    # hybrid 在同样安全的前提下把“靠近怪物”作为末位加分，
                    # 避免每次危险规避都反而跑到离怪最远的一侧。
                    # 白怪软代价优先于贴边/离心，但不改变硬危险和路径可达性。
                    score = (-soft_bad, -edge_bad,
                             1 if dd >= required_dd else 0,
                             axis_good, -len(path), dd, edge,
                             -md if prefer_mobs else md)
                if best_score is None or score > best_score:
                    best_score, best_path = score, path
            if len(path) >= max_steps:
                continue
            for name, (di, dj) in dirs.items():
                nxt = (cur[0] + di, cur[1] + dj)
                if (0 <= nxt[0] < GRID_N and 0 <= nxt[1] < GRID_N
                        and nxt not in seen and nxt not in danger
                        and nxt not in occupied):
                    seen.add(nxt)
                    q.append((nxt, path + [name]))
        return best_path, best_score

    def _pick_strafe(self, pi, pj, danger, soft=None):
        """无紧迫危险时的横向移动：真正左右交替，每步反向。

        不允许连续同方向走到中心 ±6 外；靠近边界时下一步向内。
        soft 只用于白怪格软避让：优先选白格/白邻格更少的横向落点，
        但绝不因为 soft 把路封死；两侧都差时仍按原交替方向走。
        """
        center = GRID_N // 2
        limit = CENTER_RADIUS
        pref = "a" if self.strafe_dir == "d" else "d"
        opp = "d" if pref == "a" else "a"

        def _valid(d):
            x = pi + (1 if d == "d" else -1)
            return 0 <= x < GRID_N and abs(x - center) <= limit, x

        def _soft_risk(x):
            if not soft:
                return 0
            risk = 3 if (x, pj) in soft else 0
            for sdi, sdj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if (x + sdi, pj + sdj) in soft:
                    risk += 1
            return risk

        ok_pref, xp = _valid(pref)
        if not ok_pref:
            pref = opp
            ok_pref, xp = _valid(pref)
            if not ok_pref:
                return None
        ok_opp, xo = _valid(opp)
        if soft and ok_opp and _soft_risk(xo) < _soft_risk(xp):
            pref = opp
            xp = xo
        # 下一步强制反向，形成“左一下右一下”的攻击节奏。
        self.strafe_dir = "a" if pref == "d" else "d"
        return pref

    def _step_toward(self, pi, pj, target, danger, occupied):
        """朝目标走一格；先长轴后短轴，危险/占用格不踩。"""
        di = target[0] - pi
        dj = target[1] - pj
        cands = []
        if abs(di) >= abs(dj):
            if di:
                cands.append((("d" if di > 0 else "a"),))
            if dj:
                cands.append((("s" if dj > 0 else "w"),))
        else:
            if dj:
                cands.append((("s" if dj > 0 else "w"),))
            if di:
                cands.append((("d" if di > 0 else "a"),))
        for d in [c[0] for c in cands]:
            delta = {"w": (0, -1), "s": (0, 1),
                     "a": (-1, 0), "d": (1, 0)}[d]
            nxt = (pi + delta[0], pj + delta[1])
            if (0 <= nxt[0] < GRID_N and 0 <= nxt[1] < GRID_N
                    and nxt not in danger and nxt not in occupied):
                return d
        return None

    def _pick_hybrid(self, pi, pj, danger, occupied, soft=None):
        """第三种走位：主体仍左右交替，每隔 3~5 个安全步插入
        一次 2~3 格受限随机冲刺。

        只在“无红区/无逃逸/不贴边/不离心”的最低优先级分支调用，
        因此危险规避、边缘回中、BFS 绕行仍然全部高于本模式。
        白怪软区存在时不放随机冲刺，避免冲刺直接踩进成片白格。
        """
        self.hybrid_safe_steps += 1
        if not soft and self.hybrid_safe_steps >= self.hybrid_next_burst:
            pick = self._pick_random_walk(pi, pj, danger, occupied,
                                          safe_end=True)
            if pick:
                self.hybrid_safe_steps = 0
                self.hybrid_next_burst = random.choice((3, 4, 5))
                return pick
        d = self._pick_strafe(pi, pj, danger, soft=soft)
        return (d, 1) if d else None

    def _pick_hunt(self, pc, danger, occupied, mobs, allow_edge=False, soft=None):
        """无危险时向最近的稳定怪物靠近。

        只作为 hybrid 的可选子步骤：危险格/边缘/离心优先级仍然高于追击；
        路径终点必须仍在中心区且离边≥2，避免为了追怪再次贴边。
        """
        if not mobs:
            return None
        # 防御：调用方偶尔会把单个格点 (i,j) 当成“怪物列表”传进来，
        # 遍历后会得到 int。统一成二维 tuple 列表，避免整轮 TypeError。
        if isinstance(mobs, tuple) and len(mobs) == 2 \
                and isinstance(mobs[0], (int, np.integer)):
            mobs = [mobs]
        mobs = [tuple(c) for c in mobs
                if isinstance(c, (tuple, list, np.ndarray)) and len(c) == 2]
        if not mobs:
            return None
        center = GRID_N // 2
        targets = sorted(mobs, key=lambda c: abs(c[0] - pc[0]) +
                         abs(c[1] - pc[1]))
        for target in targets[:3]:
            path, _score = self._best_path(
                pc, danger, occupied, max_steps=3,
                mobs=[target], mob_first=True, soft=soft)
            if not path:
                continue
            end = (pc[0] + sum({"w": 0, "s": 0, "a": -1, "d": 1}[d]
                               for d in path),
                   pc[1] + sum({"w": -1, "s": 1, "a": 0, "d": 0}[d]
                               for d in path))
            end_edge = min(end[0], GRID_N - 1 - end[0],
                           end[1], GRID_N - 1 - end[1])
            if end_edge < (1 if allow_edge else 2):
                continue
            if not allow_edge and max(abs(end[0] - center),
                                     abs(end[1] - center)) > CENTER_RADIUS:
                continue
            return path
        return None

    def _pick_random_walk(self, pi, pj, danger, occupied, safe_end=False):
        """受限随机：随机方向 + 2~3 格随机距离。

        B 组规则：本次方向不能和前两次相同；按用户要求不逐格检查
        红区/怪物，只检查棋盘边界和中心区上限，起步后连续执行 2~3 格。
        全部方向都不行才返回 None，由调用方退回左右交替。
        """
        center = GRID_N // 2
        limit = CENTER_RADIUS
        choices = []
        for d, (di, dj) in (("w", (0, -1)), ("s", (0, 1)),
                            ("a", (-1, 0)), ("d", (1, 0))):
            if d in self.random_prev_dirs:
                continue
            max_dist = 0
            for k in (2, 3):
                nxt = (pi + di * k, pj + dj * k)
                if not (0 <= nxt[0] < GRID_N and 0 <= nxt[1] < GRID_N):
                    break
                # 用户“不用每步都检查”：这里不查 danger/occupied，
                # 只保证终点仍在中心区，避免随机直接漂到边缘。
                if abs(nxt[0] - center) > limit or abs(nxt[1] - center) > limit:
                    break
                if safe_end and (nxt in danger or nxt in occupied):
                    continue
                max_dist = k
            if max_dist >= 2:
                choices.append((d, random.randint(2, max_dist)))
        if not choices:
            return None
        d, n = random.choice(choices)
        self.random_prev_dirs = (self.random_prev_dirs + [d])[-2:]
        return d, n

    # ── 商店 ──
    def _read_shop_coins(self, img):
        """金币数在「金币左」图标右侧、查看战场左侧。"""
        crop = img[104:169, 1455:1605]
        crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        for ln in (self.ocr_lines(crop) or []):
            tok = ln.replace(",", "").replace(".", "").strip()
            if tok.isdigit():
                return int(tok)
        return None

    def _read_price(self, img, left):
        crop = img[834:894, left + 130:left + 300]
        crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        for ln in (self.ocr_lines(crop) or []):
            tok = ln.replace(",", "").replace(".", "").strip()
            if tok.isdigit():
                return int(tok)
        return None

    def _shop_layout(self, img):
        """判定 4 卡 / 3 卡布局，避免把幽灵 left 当成第二张卡。

        4 卡 left=45/520/970/1420；3 卡 left=480/1005/1450。
        用首卡左边框条带的亮度判断：真实卡框亮，幽灵位置落在暗背景。
        """
        for lefts in ((45, 520, 970, 1420), (480, 1005, 1450)):
            first = lefts[0]
            strip = img[300:900, first:first + 20]
            if strip.size == 0:
                continue
            if float(strip.mean()) >= 55 or int(strip.max()) >= 170:
                return lefts
        return (45, 520, 970, 1420)

    def _shop_candidates(self, img):
        """返回 [(idx, 颜色, 颜色rank, 最佳词条, 词条rank, 词条分, 卡左)]。
        词条文字位置随截图裁剪/窗口缩放变化，不能再用固定 y=530..645；
        改为在每张卡列内做模板搜索，再用词条实际 y 反推颜色取样位置。"""
        lefts = self._shop_layout(img)
        attr_rank = {"长度": 5, "宽度": 4, "伤害增幅": 3, "暴击": 2, "伤害": 1}
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        H, W = gray.shape[:2]
        raw = []
        for idx, left in enumerate(lefts, 1):
            x1 = max(0, left + 20)
            x2 = min(W, left + 430)
            if x2 <= x1:
                continue
            y1, y2 = 250, max(260, min(H - 40, 900))
            attrs = {}
            for name, tpl in self.shop_attr_tpls.items():
                th, tw = tpl.shape[:2]
                if th >= y2 - y1 or tw >= x2 - x1:
                    continue
                res = cv2.matchTemplate(gray[y1:y2, x1:x2], tpl,
                                        cv2.TM_CCOEFF_NORMED)
                mv = float(res.max())
                if mv >= 0.88:
                    loc = cv2.minMaxLoc(res)[3]
                    cy = y1 + loc[1] + th // 2
                    attrs[name] = (mv, cy)
            if "伤害增幅" in attrs:
                attrs.pop("伤害", None)       # 伤害会命中「伤害增幅」内部
            if not attrs:
                continue
            # 颜色用卡面主体均值判断，比左上角小 patch 稳：
            # 实测金色卡 S≈165~176、H≈24~34；白金色卡 S≈36~44、H≈86~99。
            best_attr = max(attrs, key=lambda k: attr_rank[k])
            attr_y = attrs[best_attr][1]
            py = max(0, min(H - 160, attr_y - 220))
            patch = img[py:py + 160, left + 60:left + 340]
            if patch.size == 0:
                continue
            hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV).reshape(-1, 3)
            mean_s = float(hsv[:, 1].mean())
            mean_h = float(hsv[:, 0].mean())
            if mean_s >= 100 and 10 <= mean_h <= 45:
                color, c_rank = "金色", 2
            elif mean_s < 70:
                color, c_rank = "白金色", 1
            elif mean_s < 140 and mean_h >= 50:
                # 彩虹/幻彩边框（实测 S≈110~123、H≈77~90）
                # 颜色优先级：幻彩 > 金色 > 白金色。
                color, c_rank = "幻彩", 3
            else:
                color, c_rank = "其他", 0
            log(f"  词条卡 第{idx}张 {color}(S={mean_s:.0f},H={mean_h:.0f}) "
                f"{best_attr} conf={attrs[best_attr][0]:.2f} y={attr_y}")
            raw.append((idx, color, c_rank, best_attr, attr_rank[best_attr],
                        attrs[best_attr][0], left))
        # 3 卡布局下，幽灵 left（落在同一张卡内部）会重复命中；按分数保留每簇最高
        raw.sort(key=lambda c: -c[5])
        out = []
        for c in raw:
            if all(abs(c[6] - o[6]) >= 110 for o in out):
                out.append(c)
        return out

    def _leave_shop(self):
        """商店出口：点「放弃」继续下一关，绝不点「查看战场」。

        实测「查看战场」只是回到已清场的战场，再点「返回界面」又回商店，
        形成 商店↔清场页 死循环（run4 空转 10 分钟）。点「放弃」会进入
        下一关（有十字键的新战斗，目标「坚持44回合」）；若某些商店语境
        下「放弃」会退到主界面，落点验证会立即停止，不会继续乱点。
        """
        img = self.cap()
        if self.has_dpad(img):
            log("商店出口：十字键已出现（已在战斗/大厅），不再点击")
            return
        log("商店出口：点「放弃」继续下一关（查看战场已确认无意义，禁用）")
        adb_tap(140, 80)          # 放弃/继续（adb）
        self.shop_leave_until = time.time() + 5.0
        time.sleep(2.5)
        self.stage_clear_pending = False
        # 验证落点：十字键是“仍在小游戏内”的硬判据。
        # 若「放弃」实际退出到活动主界面/标题（无十字键且不是商店/祝福/结算），
        # 立即停止，绝不在外部页面继续点。
        try:
            img2, scene2 = self.cap(), "unrecognized"
            for attempt in range(2):
                scene2 = self.classify(img2)
                if self.has_dpad(img2) or scene2 in (
                        "battle", "hub", "blessing", "shop", "entry", "gameover"):
                    break
                if attempt == 0:
                    log(f"商店放弃后落点 {scene2} 无十字键 → 再等 2s 复核")
                    time.sleep(2)
                    img2 = self.cap()
                else:
                    log(f"商店放弃后已在外部页面（{scene2}，无十字键）→ 停止防乱点")
                    self.stop_escape(img2, "商店放弃后离开小游戏")
                    return
            log(f"商店出口落点: {scene2}")
            if scene2 in ("mainui", "title"):
                self.stop_escape(img2, "商店点放弃后退到主界面/标题")
        except StopRequested:
            raise
        except Exception as e:
            log(f"商店出口验证异常: {e!r}")

    def handle_shop(self, img):
        if not self.shop_entry_shot:
            # 刚进商店时卡片仍在滑入/淡入，模板和词条都会漏识别；
            # 等待画面稳定后再截第一张商店图和做候选扫描。
            interruptible_sleep(1.3)
            img = self.cap()
            self.shop_entry_shot = True
            self.save_evidence(img, "shop_enter")
        if self.shop_done:
            if time.time() < self.shop_leave_until:
                time.sleep(1)
                return
            self.shop_no_exit += 1
            if self.shop_no_exit >= 2:
                self.stop_escape(img, "商店点放弃后仍未离开")
                return
            log("商店购买后仍在商店 → 再尝试一次「放弃」")
            self._leave_shop()
            return
        coins = self._read_shop_coins(img)
        # 金币 OCR 偶尔会失败；先重读 2 次。仍读不到说明很可能不是
        # 小游戏内的商店（例如游戏活动页的商店），不做刷新/放弃盲操作。
        for _ in range(2):
            if coins is not None:
                break
            time.sleep(0.6)
            img = self.cap()
            coins = self._read_shop_coins(img)
        log(f"商店金币: {coins if coins is not None else '未知'}")
        if coins is None:
            # 死亡/清场转场会在 GAME OVER 大字出现前短暂落入 shop 模板；
            # 先复核当前帧，避免把结算页误当外部商店直接停脚本。
            scene_now = self.classify(img)
            if scene_now != "shop":
                # 误入 shop 模板时已经在场景切换处记过 1 次商店，这里必须撤销，
                # 否则 GAME OVER 结算页会把“0 店”伪装成“1 店”。
                self.run_shops = max(0, self.run_shops - 1)
                log(f"商店复核为 {scene_now} → 不按商店处理，撤销误计商店，交回主循环")
                return
            log("商店金币持续无法识别 → 停止，避免把外部商店当小游戏商店乱点")
            self.stop_escape(img, "商店金币无法识别")
            return
        cands = self._shop_candidates(img)
        # 连买(用户 2026-11-14:商店可以买多件符合要求的):买完一件
        # 重扫候选+重读金币,同批还有符合的接着买;买空/刷尽才出店。
        # 无目标词条 或 目标词条都买不起 → 只要有余钱就刷新换一批
        # (2026-11-14 用户反馈:有余钱却不刷新就走了,不合理)
        refresh_left = 2
        bought = 0
        while True:
            affordable = []
            for c in cands:
                price = self._read_price(img, c[6])
                log(f"  第{c[0]}张 价格={price} 金币={coins}")
                if coins is None or price is None or price <= coins:
                    affordable.append(c)
            if not affordable:
                if refresh_left <= 0:
                    log(f"刷新次数用尽仍无可买(金币={coins},已买{bought}件) → 返回战斗")
                    break
                refresh_left -= 1
                log(f"商店无可买(候选{len(cands)},金币={coins}) → 刷新 ({2 - refresh_left}/2)")
                adb_tap(623, 994)       # 刷新按钮（adb）
                time.sleep(2)
                img = self.cap()
                self.save_evidence(img, f"shop_refresh{2 - refresh_left}")
                cands = self._shop_candidates(img)
                continue
            if bought >= 10:
                # 保险丝:候选识别抖动可能导致重复买同一张,买 10 件必出店
                log(f"已买{bought}件 → 达保险丝上限,出店")
                break
            best = max(affordable, key=lambda c: (c[2], c[4], c[5]))
            idx, color, _, attr, _, _, left = best
            left_adb = left - 5
            bought += 1
            log(f"商店购买: 第{idx}张 {color} 词条={attr} 金币={coins} (本店第{bought}件)")
            self.save_evidence(img, f"shop_before_buy_{idx}")
            adb_tap(left_adb + 205, 500)   # 选中卡片
            time.sleep(0.6)
            adb_tap(1272, 1020)            # 购买
            time.sleep(1.6)
            img = self.cap()
            coins = self._read_shop_coins(img)
            cands = self._shop_candidates(img)
        self._leave_shop()
        self.shop_done = True

    # ── 祝福 ──
    def handle_blessing(self, img):
        picked = False
        for name, tpl in self.bless_tpls.items():
            res = cv2.matchTemplate(img, tpl, cv2.TM_CCOEFF_NORMED)
            mv = float(cv2.minMaxLoc(res)[1])
            if mv >= 0.90:
                loc = cv2.minMaxLoc(res)[3]
                cx = loc[0] + tpl.shape[1] // 2
                cy = loc[1] + tpl.shape[0] // 2
                log(f"祝福优先选 {name} conf={mv:.2f} → ({cx},{cy})")
                self.real_click(cx, cy)
                time.sleep(0.8)
                picked = True
                break
        if not picked:
            log("祝福无优先项 → 直接点选择")
        if not self.click_btn(img, "blessing_ok", timeout=5, required=False):
            # 不再盲点固定坐标：模板没命中就等下一帧，避免点到卡片/空白
            log("祝福「选择」按钮未命中 → 不盲点，等待下一帧")

    # ── 红区几何 ──
    def _zone_boxes(self, cells):
        """连通红区 → 外接框列表 [(minx,maxx,miny,maxy)]。"""
        boxes = []
        seen = set()
        for c0 in cells:
            if c0 in seen:
                continue
            stack, comp = [c0], []
            seen.add(c0)
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nb = (cur[0] + di, cur[1] + dj)
                    if nb in cells and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
            xs = [x for x, _ in comp]
            ys = [y for _, y in comp]
            boxes.append((min(xs), max(xs), min(ys), max(ys)))
        return boxes

    def _box_containing(self, boxes, pc):
        for b in boxes:
            if b[0] <= pc[0] <= b[1] and b[2] <= pc[1] <= b[3]:
                return b
        return None

    def _escape_from_box(self, pc, box):
        """从矩形框内向最短边逃:返回 (dir, axis)。

        四方向计算走出框(到框外一格)的步数取最短,落点出界的方向
        剔除(2026-11-14 19:51 局复盘:玩家贴 x=0 时旧版仍选 a,
        承诺连走出界后钉死边缘反复吃爆炸)。"""
        bx0, bx1, by0, by1 = box
        cands = []          # 落点离边≥1(不把自己逃进死角)
        cands_loose = []    # 严格界内(退让级)
        for d, cur, lo, hi in (
            ("a", pc[0], bx0, bx1),
            ("d", pc[0], bx0, bx1),
            ("w", pc[1], by0, by1),
            ("s", pc[1], by0, by1),
        ):
            steps = max(1, (cur - lo + 1) if d in ("a", "w")
                        else (hi - cur + 1))
            end = cur - steps if d in ("a", "w") else cur + steps
            if not (0 <= end < GRID_N):
                continue                    # 逃出框前就出界 → 不可用
            cands_loose.append((steps, d))
            if 1 <= end <= GRID_N - 2:
                cands.append((steps, d))
        # 两级约束(20:09 局复盘:落点 x=0 虽"合法"但下一波框左移即无路)
        # 不再回退到贴边落点：宁可返回 None 交给 BFS/常规走位，
        # 也不要把角色送到 x=0/14 或 y=0/14 的边缘（用户反复要求不贴边）。
        pool = cands
        if pool:
            pool.sort()
            d = pool[0][1]
            return d, ("x" if d in ("a", "d") else "y")
        # 全不可行(全图框/贴角):不给方向,调用方不建承诺,交回常规走位。
        # 旧版退回"近侧语义"会给越界方向(20:25 boss缩圈局死循环40+次)
        return None, None

    def _escape_oob(self, pc, box, d):
        """沿 d 方向走出 box 的落点是否出界(供'不回头'让位边界约束)。"""
        bx0, bx1, by0, by1 = box
        steps = max(1, (pc[0] - bx0 + 1) if d == "a" else
                    (bx1 - pc[0] + 1) if d == "d" else
                    (pc[1] - by0 + 1) if d == "w" else (by1 - pc[1] + 1))
        end = (pc[0] - steps if d == "a" else
               pc[0] + steps if d == "d" else
               pc[1] - steps if d == "w" else pc[1] + steps)
        return end < 0 or end >= GRID_N

    # ── 战斗 ──
    def _find_player(self, img):
        """找玩家：只取两个黄色 L 形括号（不是金币/伤害数字）。"""
        bx0, by0 = 515, 145
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        bh = hsv[by0:by0 + 900, bx0:bx0 + 905]
        yellow = cv2.inRange(bh, np.array([14, 150, 220]), np.array([30, 255, 255]))
        n, labels, stats, cents = cv2.connectedComponentsWithStats(
            yellow.astype(np.uint8))
        blobs = []
        for i in range(1, n):
            area = int(stats[i, cv2.CC_STAT_AREA])
            w = int(stats[i, cv2.CC_STAT_WIDTH])
            h = int(stats[i, cv2.CC_STAT_HEIGHT])
            # 玩家角括号：25×25 左右、面积约 150~400、填充率低；金币/数字被过滤
            if 120 <= area <= 450 and 15 <= w <= 45 and 15 <= h <= 45:
                blobs.append((cents[i][0], cents[i][1], area))
        if not blobs:
            return None
        arr = np.array([[b[0], b[1]] for b in blobs])
        used = np.zeros(len(arr), bool)
        for i in range(len(arr)):
            if used[i]:
                continue
            grp = [i]
            for j in range(len(arr)):
                if j != i and not used[j] and \
                        abs(arr[i][0] - arr[j][0]) < 160 and \
                        abs(arr[i][1] - arr[j][1]) < 160:
                    grp.append(j)
                    used[j] = True
            used[i] = True
            if len(grp) >= 2:
                return (int(arr[grp, 0].mean()) + bx0,
                        int(arr[grp, 1].mean()) + by0)
        return None

    def _screen_player_cell(self, img):
        """玩家黄括号所在的屏幕格。

        普通关/清怪关的棋盘是玩家为画面锚点的局部视图：红区、白色倒计时环、
        伤害区都是在“屏幕格”里识别的，必须用屏幕上的玩家格去对齐，
        不能拿虚拟世界坐标 (pi,pj) 去查屏幕危险集。否则玩家明明站在
        画面中央的红带里，程序却在查另一个格子，永远看不见脚下红区。
        """
        pt = self._find_player(img)
        if not pt:
            return None
        i = int((pt[0] - GRID_X0) // CELL_W)
        j = int((pt[1] - GRID_Y0) // CELL_H)
        if 0 <= i < GRID_N and 0 <= j < GRID_N:
            return (i, j)
        return None

    @staticmethod
    def _screen_red_escape(danger, spc, max_steps=6, soft=None):
        """屏幕坐标系下的红区安全步。

        只走一格，下一帧用新红区重算；不再锁 3 步惯性。玩家已在红区时
        BFS 找最近的安全格；玩家不在红区但红区贴到邻格时，选离红区
        最远的邻格。屏幕边缘不当作可去的安全格，避免被赶到画面外。
        两格安全校验：第一格落点还要有一个不踩红区的下一格，
        避免刚走一格就被新红区封死；白格只作软偏好，不参与硬出口判断。
        """
        if not danger or spc is None:
            return None
        near = min(max(abs(spc[0] - x), abs(spc[1] - y))
                   for x, y in danger)
        if near > 1:
            return None
        dirs = (("w", (0, -1)), ("s", (0, 1)),
                ("a", (-1, 0)), ("d", (1, 0)))

        def _soft_risk(cell):
            if not soft:
                return 0
            risk = 3 if cell in soft else 0
            for sdi, sdj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                if (cell[0] + sdi, cell[1] + sdj) in soft:
                    risk += 1
            return risk

        def _in_bounds(cell):
            return 1 <= cell[0] <= GRID_N - 2 and \
                1 <= cell[1] <= GRID_N - 2

        if (spc[0], spc[1]) not in danger:
            best, best_score = None, None
            for d, (di, dj) in dirs:
                nxt = (spc[0] + di, spc[1] + dj)
                if not _in_bounds(nxt):
                    continue
                if nxt in danger:
                    continue
                md = min(max(abs(nxt[0] - x), abs(nxt[1] - y))
                         for x, y in danger)
                score = (md, -_soft_risk(nxt), d)
                if best_score is None or score > best_score:
                    best, best_score = d, score
            return best
        q = deque([(spc, [])])
        seen = {spc}
        fallback = []
        while q:
            cur, path = q.popleft()
            if path and cur not in danger:
                # 第一格安全之外，再确认它至少有一个安全出口；否则继续
                # 找更完整的路径，避免一步走进死角后下一帧无路可走。
                exit_ok = any(
                    _in_bounds((cur[0] + di, cur[1] + dj))
                    and (cur[0] + di, cur[1] + dj) not in danger
                    for _d, (di, dj) in dirs)
                if len(path) >= 2 or exit_ok:
                    return path[0]
                fallback.append((path[0], cur))
            if len(path) >= max_steps:
                continue
            for d, (di, dj) in dirs:
                nxt = (cur[0] + di, cur[1] + dj)
                if not _in_bounds(nxt):
                    continue
                if nxt in danger or nxt in seen:
                    continue
                seen.add(nxt)
                q.append((nxt, path + [d]))
        # 四周全红：选下一格离红区最远、白怪软邻格最少的，哪怕要穿过一格。
        best, best_score = None, None
        for d, (di, dj) in dirs:
            nxt = (spc[0] + di, spc[1] + dj)
            if not _in_bounds(nxt):
                continue
            md = min(max(abs(nxt[0] - x), abs(nxt[1] - y))
                     for x, y in danger)
            score = (md, -_soft_risk(nxt), d)
            if best_score is None or score > best_score:
                best, best_score = d, score
        if best is None and fallback:
            best = fallback[0][0]
        return best

    def _press_dpad(self, d, geom=None):
        """点 D-pad 对应箭头。必须先让进程 DPI 感知，否则物理坐标会被缩小。
        实测键盘方向键只会触发画面动画/不换格；桌面鼠标点 D-pad 可以推进回合。
        ADB 模式不依赖窗口前台：锁屏/聊天窗遮挡时仍能直接向模拟器发触摸。
        """
        geom = geom or self.dpad_geom
        if geom is None:
            geom = self.locate_dpad(self.cap())
        if not geom:
            # 过渡帧/动画帧容易定位失败；上一帧成功的位置仍是同一套 UI，
            # 直接复用，避免“D-pad 未定位 → 0/2 格 → 原地等死”。
            geom = self._last_dpad_geom
        if not geom or d not in geom:
            log(f"✗ D-pad 未定位，无法执行方向 {d}")
            return False
        import pyautogui
        if not self._input_focused:
            self._input_focused = self.focus_game_input()
            interruptible_sleep(0.30)
        sx, sy = self.render_click_point(*geom[d])
        pyautogui.click(sx, sy)
        time.sleep(0.12)          # 给游戏一帧响应，避免连点被合并
        log(f"  鼠标 D-pad {d.upper()} @ ({sx},{sy})")
        return True

    def _move_one(self, d, img=None):
        """按一次方向键，并按该方向推进虚拟坐标。
        战斗残影会遮住黄色括号，逐帧识别玩家既慢又不可靠；按用户要求改为
        战斗开始记录起点，之后每次移动直接维护 x/y，输入只发一次不等截图。
        """
        before = self.last_pc if self.last_pc is not None else (7, 7)
        ok = self._press_dpad(d, self.dpad_geom)
        if not ok:
            # D-pad 没定位/没点到时不能推进虚拟坐标，否则会凭空漂移，
            # 后续逃逸会朝错误方向一路走到地图边缘。
            log(f"  D-pad {d.upper()} 未执行 → 虚拟坐标保持 {before}")
            return False
        delta = {"w": (0, -1), "a": (-1, 0), "s": (0, 1), "d": (1, 0)}[d]
        nx = max(0, min(GRID_N - 1, before[0] + delta[0]))
        ny = max(0, min(GRID_N - 1, before[1] + delta[1]))
        self.last_pc = (nx, ny)
        self.battle_moves.append((nx, ny))
        if len(self.battle_moves) > 200:
            self.battle_moves = self.battle_moves[-200:]
        edge = min(nx, GRID_N - 1 - nx, ny, GRID_N - 1 - ny)
        log(f"  虚拟坐标 {before}→({nx},{ny}) 离边={edge} 目标方向={d}")
        return True

    def battle_step(self, img):
        # 进场动画（“地下迷宫/击败怪物/即将刷新像素生物”）约 6s，期间输入会被吞；
        # 先等完再开始读格/走位，否则连续两次无效就会误判 D-pad 坏了。
        if not self.battle_intro_done:
            self.battle_intro_done = True
            log("战斗开场动画：等待 3.2s 后开始移动")
            interruptible_sleep(3.2)
            self._input_focused = self.focus_game_input()
            interruptible_sleep(0.35)
            img = self.cap()
        t0 = time.time()
        if DIAG:
            self._frame_buf.append((t0, img))   # [DIAG] 死前录像缓冲
        bx0, by0 = 515, 145
        # 屏幕玩家格只用于“脚下红区/邻格红区”对齐。普通关画面以玩家
        # 为锚点，危险格是屏幕坐标；虚拟世界坐标只负责回合位移和 Boss 距离。
        spc = self._screen_player_cell(img)
        spc_detected = spc is not None
        if spc is None:
            spc = getattr(self, "_screen_pc", (7, 7))
        else:
            self._screen_pc = spc
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        bh = hsv[by0:by0 + 900, bx0:bx0 + 905]
        # 棋盘是固定画面，玩家黄括号在屏幕格上的位置就是真实格位；
        # 普通关按 D-pad 增量维护的虚拟坐标会被攻击/特效带漂。每帧用
        # 检测到的屏幕玩家格校正虚拟坐标；检测失败时保留原虚拟坐标，
        # 绝不能用上一帧的缓存值强行重定位。
        if self.last_pc is not None:
            pi, pj = self.last_pc
        else:
            pi, pj = 7, 7
            self.last_pc = (pi, pj)
        if spc_detected and (pi, pj) != spc:
            log(f"视觉重定位 ({pi},{pj}) → 屏幕格{spc}")
            pi, pj = spc
            self.last_pc = (pi, pj)

        # 每场战斗记录起点，每次移动更新坐标；同时把“离边距离”写进日志。
        # 这样能直接回看是不是又走到 0/9 边缘后才掉血。
        if self.battle_start_pc is None:
            self.battle_start_pc = (pi, pj)
            self.battle_moves = [(pi, pj)]
            self._logged_pc = (pi, pj)
            self._screen_pc = (7, 7)
            # [DIAG] 死前录像只保留本场战斗帧；旧版从不清空，会把
            # 上一场（甚至上一次商店前）的末尾 12 帧带到 GAME OVER，
            # 名义“死前录像”其实是旧战斗。每场战斗开始清空一次。
            if DIAG:
                self._frame_buf.clear()
            self._edge_target = None
            self._edge_hist = {}
            self._edge_blacklist = set()
            self._edge_attempts = {}
            self._edge_since = 0.0
            self._boss_fallback_cell = None
            edge0 = min(pi, GRID_N - 1 - pi, pj, GRID_N - 1 - pj)
            log(f"战斗开始坐标=({pi},{pj}) 棋盘={GRID_N}×{GRID_N} 离边={edge0}")
            if edge0 <= 0:
                log(f"⚠ 战斗起点已贴边({pi},{pj}) → 下一步强制回中")
        elif self._logged_pc != (pi, pj):
            self._logged_pc = (pi, pj)
            self.battle_moves.append((pi, pj))
            if len(self.battle_moves) > 200:
                self.battle_moves = self.battle_moves[-200:]
            edge_now = min(pi, GRID_N - 1 - pi, pj, GRID_N - 1 - pj)
            log(f"战斗坐标更新: 起点{self.battle_start_pc} → 当前({pi},{pj}) 离边={edge_now}")
            if edge_now <= 0:
                log(f"⚠ 已到边缘({pi},{pj})，下一步必须往场内走")

        # 倒计时预警：只认棋盘内“白色/灰色断环”，不再用红色 HSV。
        # 红 HSV 会把木框、玩家红披风、伤害数字全算成危险区；用户实拍确认，
        # 真正要躲的是格内白色倒计时环（例如 3×5 的成片环）。
        _bs_det0 = time.time()
        ring_mask = cv2.inRange(
            hsv, np.array([0, 0, 185]), np.array([180, 70, 255]))
        # 2026-11-15 关键修正:inRange 输出是 0/255,integral 会把每个白像素
        # 按 255 累加。旧阈值 70~700 实际只相当于 0.3~2.7 个白像素，几乎
        # 每个格子的白点/格线都会被当成“倒计时环”；而 >900 实际也只相当于
        # 3.5 个白像素，日志里才会出现 2~130 个“白怪格”剧烈跳变。
        # 先归一化成 0/1 再积分，让阈值恢复为真实像素数。
        ring_mask = (ring_mask > 0).astype(np.uint8)
        ring_counts = np.zeros((GRID_N, GRID_N), np.int32)
        integ = cv2.integral(ring_mask)
        for ci in range(GRID_N):
            for cj in range(GRID_N):
                cx, cy = cell_center(ci, cj)
                x0, x1 = max(0, cx - 24), min(ring_mask.shape[1], cx + 24)
                y0, y1 = max(0, cy - 24), min(ring_mask.shape[0], cy + 24)
                ring_counts[ci, cj] = (
                    integ[y1, x1] - integ[y0, x1]
                    - integ[y1, x0] + integ[y0, x0])
        # 单格白点多为玩家/怪物/伤害数字；真实预警环是细白环，
        # 48×48 内通常 100~500 个白像素。白色像素生物会占满整格，
        # 同一统计能到 900~2100（实拍 5×6 白色怪群验证），必须设上限，
        # 否则整片白怪被当成红区，玩家只会逃、永远不清怪。
        ring_cells = {(i, j) for i in range(GRID_N) for j in range(GRID_N)
                      if 70 <= ring_counts[i, j] <= 700}
        # 成片白色圆形攻击区/白色怪群：单格白像素 900+，先只做诊断；
        # 不再整片并入 danger。实测 54 格白区一旦并入 danger，会让机器人
        # 把整块白区当成红区逃到边缘，反而更快死亡。
        white_cells = {(i, j) for i in range(GRID_N) for j in range(GRID_N)
                       if ring_counts[i, j] > 900 and (i, j) != (pi, pj)}
        # 大面积红色伤害区（实拍 f06：15×3 红格 + 数字5）必须并入危险；
        # 旧版因 HSV 红阈值会把木框/披风误判而整块删掉，结果角色站在红区里
        # 被每秒5点磨死。这里改成按“格中心均值 + 连通块≥4格”识别：
        # 红区整片 60+ 格，玩家披风/伤害数字不会形成 4 格连通块。
        red_cells = set()
        for i in range(GRID_N):
            for j in range(GRID_N):
                cx, cy = cell_center(i, j)
                roi = img[max(0, cy - 20):cy + 20, max(0, cx - 20):cx + 20]
                if not roi.size:
                    continue
                b, g, r = [float(x) for x in roi.reshape(-1, 3).mean(axis=0)]
                # 亮红爆炸区：原来的判定保留。
                # 暗红倒计时格（清怪关常见红底黑字“1/2/3”）：整格均值
                # 的红色分量只比绿/蓝高 8~20，亮度也低，旧 r>155 完全漏掉。
                # 实拍死亡帧验证：r-g>6 能抓住玩家脚下红带，而普通棋盘
                # 格 r-g 基本 ≤4；仍用连通块≥4 过滤单格伤害飘字。
                if (r > 155 and r - g > 60 and r - b > 60) or \
                        (r > 80 and r - g > 20 and r - b > 20):
                    red_cells.add((i, j))
        red_zone = set()
        seen_red = set()
        for c in red_cells:
            if c in seen_red:
                continue
            stack, comp = [c], []
            seen_red.add(c)
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nb = (cur[0] + di, cur[1] + dj)
                    if nb in red_cells and nb not in seen_red:
                        seen_red.add(nb)
                        stack.append(nb)
            if len(comp) >= 4:
                red_zone.update(comp)
        # 最后清场关地板是棕色，实测整块 15×15 会被格均值判成 225 格红区；
        # 普通红区最大常见 3×7=21、15×3=45 格。超过 80 格整帧丢弃，
        # 既不漏 r-g=29 的真实暗红，也不会把棕色地板当红区绕圈。
        if len(red_zone) > 80:
            log(f"红区面积异常 {len(red_zone)} 格 → 视为地板误判，忽略本帧红区")
            red_zone = set()
        danger = set()
        seen = set()
        for c in ring_cells:
            if c in seen:
                continue
            stack, comp = [c], []
            seen.add(c)
            while stack:
                cur = stack.pop()
                comp.append(cur)
                for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nb = (cur[0] + di, cur[1] + dj)
                    if nb in ring_cells and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
            if len(comp) >= 3:
                danger.update(comp)
        danger |= red_zone
        if red_zone:
            log(f"红区识别 {len(red_zone)} 格并入危险")
        # P12 红区快速路径：玩家两格内有红区时，模板怪检测让位给逃逸。
        # 模板匹配是检测段最重的一步；短直线计数 1 只给一帧窗口，不能再
        # 等 2.5s 的场景/模板链跑完。只跳过 tpl_mobs，不跳过红区/占位。
        fast_red = bool(red_zone and spc is not None and any(
            max(abs(spc[0] - x), abs(spc[1] - y)) <= 2
            for x, y in red_zone))
        if fast_red:
            log("红区快速路径: 两格内有红区，跳过模板怪检测")
        # 屏幕玩家格与屏幕危险集对齐：玩家脚下/邻格有红区时，给一个
        # 只走一格的安全方向。它会在所有常规/Boss 决策之后强制覆盖，
        # 防止“虚拟坐标说安全、画面里其实站在红带中央”。
        # 注意：screen_escape 必须在剔除玩家自身格之后再算；玩家的白/黄
        # 标记本身会命中 ring_mask，旧顺序会让它每帧都误判“脚下红区”。
        screen_escape = None
        _bs_det = time.time()

        # 当前帧危险区 = 逃逸方向判断的唯一依据;1.6s 记忆只做 BFS 障碍
        # (闪烁间隙不往回走),不与当前帧合并——否则记忆格桥接新旧红区,
        # 外接框越滚越大直到全棋盘(2026-11-14 实测 15×13)。
        live_danger = danger
        # 玩家自己的白色/黄色标记也会命中 ring_mask，尤其站在格心时
        # 会在玩家格形成一个“假预警”；之前日志里反复出现“脚下红区”
        # 就是它导致的。玩家格不能算危险格，否则每一步都强制逃逸。
        # 但必须先记住“玩家此刻是否真的站在红区里”，再做 discard；
        # 否则下面 in_live 永远为 False，机器人会一直在红区里踱步。
        player_in_danger = (pi, pj) in live_danger
        live_danger.discard((pi, pj))
        # 屏幕逃生只使用剔除玩家自身标记后的危险集；真实红色格仍保留，
        # 避免把玩家披风/黄色括号当成“需要逃离的红区”。
        danger_for_screen = set(live_danger)
        danger_for_screen.discard(spc)
        if spc in red_zone:
            danger_for_screen.add(spc)
        screen_escape = (self._screen_red_escape(danger_for_screen, spc, soft=white_cells)
                         if spc_detected else None)
        live_boxes = self._zone_boxes(live_danger)
        # 空心环逼近检测:环收缩到玩家切比雪夫距离 ≤2 内 → 提前触发逃逸
        # (等环踩到脚下就晚了——缩圈死亡复盘 2026-11-14)
        near2 = any(max(abs(c[0] - pi), abs(c[1] - pj)) <= 2
                    for c in live_danger)

        now_t = time.time()
        for c in live_danger:
            # P12 回区惩罚：红区格记忆覆盖一次 3×7 的 4 计数周期
            # （1.6s 太短，红区消失后脚本会立刻回中心，下一次刷新正好
            # 又踩回去）。普通白区仍保持 1.6s，避免过度占用路径。
            ttl = 4.2 if c in red_zone else 1.6
            self._danger_mem[c] = now_t + ttl
        memory_cells = {c for c, t in self._danger_mem.items() if t > now_t}
        if len(self._danger_mem) > 200:
            self._danger_mem = {c: t for c, t in self._danger_mem.items()
                                if t > now_t}
        danger_all = live_danger | memory_cells

        pc = (pi, pj)
        occupied = set()
        samples = {}
        for i in range(GRID_N):
            for j in range(GRID_N):
                cx, cy = cell_center(i, j)
                roi = img[max(0, cy - 22):cy + 22, max(0, cx - 22):cx + 22]
                if roi.size:
                    samples[(i, j)] = roi.reshape(-1, 3).mean(axis=0)
        for par in (0, 1):
            cells_p = [k for k in samples if (k[0] + k[1]) % 2 == par]
            if not cells_p:
                continue
            base = np.median(np.array([samples[k] for k in cells_p]), axis=0)
            for k in cells_p:
                if np.abs(samples[k] - base).mean() > 45:
                    occupied.add(k)
        # P12.6 清场出口：怪物清空后，棋盘最外圈会出现粉色出口箭头。
        # 旧版没有出口目标，只按 hybrid 在场内绕，可能无限停在“击败所有怪物”。
        # 这里只在最外圈找高饱和粉色格，作为出口并从 occupied 中移除，
        # 避免机器人把出口箭头当成墙而不踩上去。
        exit_cells = set()
        for i in range(GRID_N):
            for j in range(GRID_N):
                if i not in (0, GRID_N - 1) and j not in (0, GRID_N - 1):
                    continue
                cx, cy = cell_center(i, j)
                roi = img[max(0, cy - 18):cy + 18, max(0, cx - 18):cx + 18]
                if not roi.size:
                    continue
                bb, gg, rr = [float(v) for v in roi.reshape(-1, 3).mean(axis=0)]
                if rr > 150 and rr - gg > 45 and bb > 80 and gg < 140:
                    exit_cells.add((i, j))
        exit_target = None
        if exit_cells:
            exit_target = min(exit_cells,
                              key=lambda c: abs(c[0] - pi) + abs(c[1] - pj))
            occupied.difference_update(exit_cells)
            log(f"清场出口候选 {sorted(exit_cells)} → 目标{exit_target}")
        # 白色像素生物/白色攻击区：单格白像素 900+，既不是倒计时细环
        # (70~700)，也不是伤害飘字（几十~200）。这里保留诊断，但不并入
        # occupied；其中连通块≥4 的白色区域已在上方并入 danger 临时避让。
        white_mobs = white_cells
        white_soft = white_cells
        if white_mobs:
            log(f"白怪格候选 {len(white_mobs)} 个（仅软代价，不并入 occupied）")

        # 实拍模板识别像素生物：整格均值对贴边怪只有 1/3 个身位，45
        # 阈值必漏；而模板匹配对顶边/左边、甚至画面中心的怪都能稳定命中
        # （用 long_battle/stability 截图验证 0.80~0.88）。只在棋盘+边框内
        # 搜索，避免把 HUD/木框当成怪。
        tpl_mobs = set()
        if self._mob_tpls and not fast_red:
            sx0, sy0 = 480, 90
            search = img[sy0:1120, sx0:1450]
            for tpl in self._mob_tpls:
                if search.shape[0] < tpl.shape[0] or \
                        search.shape[1] < tpl.shape[1]:
                    continue
                res = cv2.matchTemplate(search, tpl, cv2.TM_CCOEFF_NORMED)
                for _ in range(8):
                    _mn, mx, _ml, loc = cv2.minMaxLoc(res)
                    if mx < 0.80:
                        break
                    cx = sx0 + loc[0] + tpl.shape[1] / 2.0
                    cy = sy0 + loc[1] + tpl.shape[0] / 2.0
                    i = int(round((cx - GRID_X0 - CELL_W / 2.0) / CELL_W))
                    j = int(round((cy - GRID_Y0 - CELL_H / 2.0) / CELL_H))
                    if 0 <= i < GRID_N and 0 <= j < GRID_N:
                        tpl_mobs.add((i, j))
                    yy0 = max(0, loc[1] - 24)
                    yy1 = min(res.shape[0], loc[1] + 24)
                    xx0 = max(0, loc[0] - 24)
                    xx1 = min(res.shape[1], loc[0] + 24)
                    res[yy0:yy1, xx0:xx1] = -1.0
        if tpl_mobs:
            occupied |= tpl_mobs

        edge_mobs = []
        edge_hist = {}
        for c in tpl_mobs:
            if c == (pi, pj):
                continue
            prev = self._edge_hist.get(c)
            count = (prev[1] if prev and now_t - prev[0] <= 1.5 else 0) + 1
            edge_hist[c] = (now_t, count)
            if min(c[0], GRID_N - 1 - c[0],
                   c[1], GRID_N - 1 - c[1]) <= 1:
                edge_mobs.append(c)
        self._edge_hist = {c: v for c, v in edge_hist.items()
                           if now_t - v[0] <= 2.5}
        edge_mobs = [c for c in edge_mobs
                     if self._edge_hist.get(c, (0, 0))[1] >= 2
                     and c not in self._edge_blacklist
                     and abs(c[0] - pi) + abs(c[1] - pj) <= 4]
        # 目标锁定：模板每帧可能只匹配到一部分边缘怪，若每帧都选“最近”，
        # 目标会在 (0,4)/(0,9)/(0,12) 之间来回改，角色被拉成沿左边抖动。
        # 只要旧目标还在，就继续打它；打完消失后再换下一个。
        if self._edge_target and self._edge_target not in edge_mobs:
            self._edge_target = None
            self._edge_attempts = {}
        if self._edge_target is None and edge_mobs:
            self._edge_target = min(
                edge_mobs, key=lambda c: abs(c[0] - pi) + abs(c[1] - pj))
            self._edge_attempts = {}
            self._edge_since = time.time()
        if self._edge_target and time.time() - self._edge_since > 6.0:
            log(f"边怪目标 {self._edge_target} 追击6s未到/未清 → 放弃回中")
            self._edge_blacklist.add(self._edge_target)
            self._edge_target = None
            self._edge_attempts = {}
            self._edge_since = 0.0

        # boss 检测(2026-11-14 修订):顶部 boss 血条是唯一可靠标志
        # (橙红长条+血量数字"855/1012",全窗 y≈105-155 x≈570-1360)。
        # 判据 R>140 且 R-G>110 且 R-B>110:血条是深橙红(B≈49),
        # 普通关棋盘上边框是粉红(B≈123)会被 R-G/R-B 双>110 排除。
        # 24 帧离线验证:boss 局 16000+ 全检出,普通局最高 3120 全排除。
        # 旧"中央 5×5 ≥3 格 occupied 聚簇"两端都翻车:路过中央的小怪群
        # 误判成 boss(17:18 局开局 17s 误触发,环带走位错误接管 22s 后
        # 58s 速死);深色 boss 本体对比不足又漏检(334s 局 1012 血条
        # 正在被打但零触发)。全部废弃。
        bar = img[BOSS_BAR_Y[0]:BOSS_BAR_Y[1], BOSS_BAR_X[0]:BOSS_BAR_X[1]]
        _r = bar[:, :, 2].astype(int)
        _g = bar[:, :, 1].astype(int)
        _b = bar[:, :, 0].astype(int)
        bar_red = int(((_r > 140) & (_r - _g > 110) & (_r - _b > 110)).sum())
        if bar_red > BOSS_BAR_MIN_RED:
            if now_t >= getattr(self, "boss_seen_until", 0.0):
                log(f"boss 血条检出({bar_red}红px) → 进入 boss 模式")
            self.boss_seen_until = now_t + 2.0
        boss_mode = now_t < getattr(self, "boss_seen_until", 0.0)
        boss_block = set()
        if boss_mode:
            # boss 战期间不再追边缘小怪：边怪优先级会压过 boss，导致
            # 在 boss 脚下绕圈却一直打边角。boss 战只做 boss 环带。
            self._edge_target = None
            self._edge_hist = {}
            self._edge_blacklist = set()
            self._edge_attempts = {}
            # 用户 2026-11-15 定稿：不做 boss 本体模板；血条第一次出现时
            # 只固定一次。实拍复核：战争型火炮的炮身中心在棋盘中心，
            # 旧版用本场玩家起点会在玩家从上方进场时把环带压到上缘，
            # 机器人沿错误环带走位。Boss 不移动，中心固定为棋盘中心。
            if self._boss_fallback_cell is None:
                self._boss_fallback_cell = (GRID_N // 2, GRID_N // 2)
                log(f"boss 血条首次出现 → 固定 Boss 中心="
                    f"{self._boss_fallback_cell}（棋盘中心）")
        # Boss 本体 3×3 是永久禁区：一旦定位，即使 2s boss 模式暂时
        # 结束，也不能解除封锁，否则后半段会走进 boss 身体。
        if self._boss_fallback_cell is not None:
            bpx, bpy = self._boss_fallback_cell
            boss_block = {(bpx + dx, bpy + dy)
                          for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                          if 0 <= bpx + dx < GRID_N and 0 <= bpy + dy < GRID_N}
            danger_all = danger_all | boss_block

        _bs_occ = time.time()
        mobs = [c for c in occupied if c != pc and c not in danger_all]
        # 怪物候选稳定跟踪：伤害数字/爆炸特效只出现一两帧，真正的静止怪
        # 会连续留在同一格。连续 >=2 帧才允许作为追怪目标，避免把数字当怪。
        for c in occupied:
            if c == pc:
                continue
            prev = self._mob_hist.get(c)
            count = (prev[1] if prev and now_t - prev[0] <= 1.5 else 0) + 1
            self._mob_hist[c] = (now_t, count)
        self._mob_hist = {c: v for c, v in self._mob_hist.items()
                          if now_t - v[0] <= 2.5}
        mobs_stable = [c for c in mobs
                       if self._mob_hist.get(c, (0, 0))[1] >= 2]
        # 红区跟踪玩家刷:邻格有红区就提前启动规避(只提前一格,不会把角色
        # 赶到边角)。
        near_danger = pc in danger_all or any(
            c in danger_all for c in ((pi + 1, pj), (pi - 1, pj),
                                      (pi, pj + 1), (pi, pj - 1)))
        log(f"玩家格({pi},{pj}) 屏幕格{spc} 活区{len(live_boxes)}个{sorted(live_danger)} "
            f"怪物{len(mobs)}(稳定{len(mobs_stable)}) 边怪{len(edge_mobs)}")
        # 实时监控帧：每约 1 秒覆盖一张，便于直接看当前红区/玩家/血条
        if now_t - self._last_monitor_t >= 1.0:
            self._last_monitor_t = now_t
            try:
                cv2.imwrite(os.path.join(TEMP,
                                         "r1999_battle_latest.png"), img)
            except Exception:
                pass

        move_dirs = []
        used_pick_strafe = False
        center = GRID_N // 2
        off_center = max(abs(pi - center), abs(pj - center))
        escape_dir, esc_axis, danger_box = self._danger_escape_axis(
            danger_all, pc)
        in_live = player_in_danger
        adjacent = any(c in danger_all for c in (
            (pi + 1, pj), (pi - 1, pj), (pi, pj + 1), (pi, pj - 1)))
        # boss 环带走位(用户 2026-11-15 定稿):
        # Boss 中心在血条首次出现时固定为棋盘中心（实拍炮身中心）；
        # 环带只按与 Boss 的 Chebyshev 距离（3 优选，2/4 次选），
        # 不考虑贴边/回棋盘中心。
        # 红区优先级更高：玩家脚下有红区时跳过本分支，先逃红区。
        if boss_mode and not in_live:
            self.escape_commit = None
            self.momentum = None
            # boss 模式必须完全接管常规走位：否则后面的 hybrid/strafe 会
            # 把环带方向改写成随机冲刺，日志里 a→d 原地打架，boss 战
            # 等于没走位。
            used_pick_strafe = True
            if self._boss_fallback_cell is None:
                # 理论上血条首次出现时已建立；这里只做兜底。
                self._boss_fallback_cell = (GRID_N // 2, GRID_N // 2)
            bpx, bpy = self._boss_fallback_cell
            cands = []
            for d, (di, dj) in (("w", (0, -1)), ("s", (0, 1)),
                                ("a", (-1, 0)), ("d", (1, 0))):
                nxt = (pi + di, pj + dj)
                if not (0 <= nxt[0] < GRID_N and 0 <= nxt[1] < GRID_N):
                    continue
                if nxt in danger_all:
                    continue          # 危险格(当前帧+1.6s 记忆)不进
                if nxt in boss_block:
                    continue          # boss 九宫格不进
                ndx = abs(nxt[0] - bpx)
                ndy = abs(nxt[1] - bpy)
                nd = max(ndx, ndy)
                band = boss_band_for_distance(nd)
                # 朝 boss 方向的一步才可能打到 boss；纯横移在 boss
                # 正下方时永远打空。
                if abs(bpy - pj) >= abs(bpx - pi):
                    toward_d = "s" if bpy > pj else "w"
                else:
                    toward_d = "d" if bpx > pi else "a"
                toward = 1 if (d == toward_d and nd >= 3) else 0
                cands.append((band, toward, d))
            if cands:
                # 同 band 时先朝 boss 打；不做贴边/回中心评分。
                cands.sort(key=lambda c: (-c[0], -c[1], c[2]))
                move_dirs = [cands[0][2]]
                log(f"boss 环带走位 → {cands[0][2]} band={cands[0][0]} "
                    f"toward={cands[0][1]} "
                    f"boss相对=({bpx - pi},{bpy - pj})")
            else:
                # 四邻全危险:朝危险质心反方向硬走一步(穿越未爆格);
                # boss 九宫格不进(20:01 局"硬走s"×6 撞 boss 自杀复盘)
                if danger_all:
                    dcx = sum(c[0] for c in danger_all) / len(danger_all)
                    dcy = sum(c[1] for c in danger_all) / len(danger_all)
                    best, bs = None, None
                    for d, (di, dj) in (("w", (0, -1)), ("s", (0, 1)),
                                        ("a", (-1, 0)), ("d", (1, 0))):
                        c = (pi + di, pj + dj)
                        if not (0 <= c[0] < GRID_N and 0 <= c[1] < GRID_N):
                            continue
                        if c in boss_block:
                            continue
                        dd = abs(c[0] - dcx) + abs(c[1] - dcy)
                        s = (dd, d)
                        if bs is None or s > bs:
                            best, bs = d, s
                    if best:
                        move_dirs = [best]
                        log(f"boss 环带全堵 → 硬走 {best}")
        # 优先级(2026-11-14 用户定稿):脚下有活红区→逃逸承诺锁方向连走出框;
        # 邻格有危险→BFS 绕行;贴边→回中;其余→左右交叉。永不停止。
        elif self.escape_commit:
            d, box, until = self.escape_commit[:3]
            # 承诺里记录剩余步数，走到框外就停；旧版只靠 free()，
            # 红区记忆/邻格危险会让它越过外接框一路走到地图边缘。
            left = self.escape_commit[3] if len(self.escape_commit) > 3 else 99
            # P12.5：只要脚下不在当前活红区就算“出区”，不能要求同时没有
            # 相邻红格。旧版 free 条件过严，角色出了 3×7 红区仍被判不安全，
            # 继续沿逃逸方向跑到边缘，随后又被新的最短边拉回红区。
            free = not in_live
            if free:
                # 出区后立即停止，不再惯性跑 3 格；惯性会把角色带进下一块
                # 红区，形成“出了红区又转头回去”。
                log(f"逃逸完成(方向{d}) → 停止惯性，恢复常规走位")
                self.momentum = None
                self.escape_commit = None
            elif left <= 0:
                log(f"逃逸承诺 {d} 步数用尽 → 重新评估")
                self.escape_commit = None
            elif time.time() > until:
                log("逃逸承诺超时(仍处危险) → 重新规划")
                self.escape_commit = None
            else:
                di, dj = {"w": (0, -1), "s": (0, 1),
                          "a": (-1, 0), "d": (1, 0)}[d]
                nxt = (pi + di, pj + dj)
                if not (0 <= nxt[0] < GRID_N and 0 <= nxt[1] < GRID_N):
                    # 承诺连走中途撞边(19:51 局"继续a"×4钉死 x=0) → 终止重规划
                    log(f"逃逸承诺 {d} 下一格出界 → 终止重新规划")
                    self.escape_commit = None
                elif nxt in live_danger:
                    # 2026-11-15 关键修正:承诺方向来自旧红区框，红区会移动。
                    # 旧版只检查边界，不检查下一格，于是会沿旧方向一头撞进
                    # 当前红区；随后又被“不回头”拉回，形成 a/d 来回踱步。
                    # 下一格是当前帧活红区时立即终止承诺，交给下面重新规划。
                    log(f"逃逸承诺 {d} 下一格进入当前红区 → 终止重新规划")
                    self.escape_commit = None
                else:
                    move_dirs = [d]
                    self.escape_commit = (d, box, until, left - 1)
                    log(f"逃逸承诺:继续 {d}(剩{left - 1}步)")
        elif self.momentum:
            # 逃逸惯性优先级必须在“脚下红区重新规划”之前。
            # 旧版把惯性放在 danger 分支后面，逃逸刚完成、in_live/near2
            # 仍为真时会立刻被新逃逸承诺覆盖，看起来就是走两步又走回去。
            md, mc = self.momentum
            di, dj = {"w": (0, -1), "s": (0, 1),
                      "a": (-1, 0), "d": (1, 0)}[md]
            nxt = (pi + di, pj + dj)
            if mc > 0 and 0 <= nxt[0] < GRID_N and 0 <= nxt[1] < GRID_N \
                    and nxt not in live_danger:
                self.momentum = (md, mc - 1)
                move_dirs = [md]
                log(f"逃逸惯性:继续 {md}(剩{mc - 1})")
            else:
                self.momentum = None
        elif not self.escape_commit and (in_live or near2):
            box = self._box_containing(live_boxes, pc)
            if box is None and not live_danger:
                # 只有玩家脚下这一格被判危险（红区闪烁/单格残留）：
                # 不能对空集合取 min/max，直接以当前格建 1 格框，下一步出格。
                box = (pi, pi, pj, pj)
            if box is None:
                xs = [c[0] for c in live_danger]
                ys = [c[1] for c in live_danger]
                box = (min(xs), max(xs), min(ys), max(ys))
            d, axis = self._escape_from_box(pc, box)
            # 先尝试真正的安全路径：从当前格出发，BFS 走到最近的非危险格，
            # 取路径第一步。旧版只按“红区外接框最短边”选一个方向，红区
            # 一偏移就会选到反方向，形成来回踱步；这里用路径而不是轴。
            path, _ = self._best_path(pc, danger_all, occupied, max_steps=12,
                                      escape_dir=None, soft=white_soft)
            if path:
                d = path[0]
                axis = ("x" if d in ("a", "d") else "y")
                log(f"逃离红区 BFS 路径={'/'.join(path)} → 首步 {d}")
            elif d:
                # P12.5：BFS 找不到安全路径时，旧版直接信任“最短边”方向，
                # 会连续把角色按进当前红区。这里改成在四个相邻格中做低风险
                # 落点：优先不踩当前活红区；如果脚下已经在红区，则允许穿一格，
                # 但必须选离最近安全格最近、且危险邻格最少的方向。
                dirs = {"w": (0, -1), "s": (0, 1),
                        "a": (-1, 0), "d": (1, 0)}
                safe_cells = [(x, y) for x in range(GRID_N)
                              for y in range(GRID_N)
                              if (x, y) not in danger_all and (x, y) != (pi, pj)]
                cands = []
                for fd, (di, dj) in dirs.items():
                    nx, ny = pi + di, pj + dj
                    if not (0 <= nx < GRID_N and 0 <= ny < GRID_N):
                        continue
                    if (nx, ny) in occupied:
                        continue
                    nd = min((abs(nx - x) + abs(ny - y)
                              for x, y in safe_cells), default=99)
                    live_risk = 1 if (nx, ny) in live_danger else 0
                    mem_risk = 1 if (nx, ny) in danger_all else 0
                    near_risk = sum(
                        1 for (qi, qj) in ((nx + 1, ny), (nx - 1, ny),
                                           (nx, ny + 1), (nx, ny - 1))
                        if (qi, qj) in danger_all)
                    edge = min(nx, GRID_N - 1 - nx, ny, GRID_N - 1 - ny)
                    # 第一优先：不踩当前活红区；第二：离安全格近；
                    # 第三：危险邻格少；最后才看贴边。
                    cands.append(((live_risk, nd, near_risk, edge), fd, nx, ny))
                if cands:
                    best = min(cands, key=lambda c: c[0])
                    if best[1] != d:
                        log(f"逃离红区无安全路径 → 低风险落点 {best[1]} "
                            f"(原最短边 {d}, 落点({best[2]},{best[3]}))")
                    d = best[1]
                else:
                    log(f"逃离红区无安全路径 → 四邻全堵，无低风险落点")
                    d = None
            if d is not None:
                axis = ("x" if d in ("a", "d") else "y")
            if d is None:
                # 全图框/贴角无界内逃逸方向(boss 缩圈战常见):不建承诺,
                # 交回常规走位。旧版此处退回"近侧语义"会给出界方向,
                # 20:25 局"建a→终止→建a"死循环40+次钉死(0,7)空按70次。
                log("  逃逸无界内方向(全图框/贴角) → 不建承诺,走常规走位")
            else:
                # 不回头只在“同一个红区框”内生效。实测红区框每 1–2 步就会
                # 移动；旧版无条件保持上次方向，会让 a/d 在旧框和新框之间
                # 来回切换，形成明显的踱步，反而踩进新红区。
                if self.last_commit_dir and time.time() < self.last_commit_until \
                        and d == OPPOSITE.get(self.last_commit_dir) \
                        and self.last_commit_box == box:
                    if self._escape_oob(pc, box, self.last_commit_dir):
                        log("  上次方向出界 → 放弃不回头,走边界可行方向")
                    else:
                        d = self.last_commit_dir
                        log("  不回头:保持上次逃逸方向")
                bx0, bx1, by0, by1 = box
                if axis == "x":
                    steps = (pc[0] - (bx0 - 1)) if d == "a" else ((bx1 + 1) - pc[0])
                else:
                    steps = (pc[1] - (by0 - 1)) if d == "w" else ((by1 + 1) - pc[1])
                # BFS 路径可能在中途拐弯，不能锁死方向；先只走首步，下一帧
                # 按新路径继续。只有“框最短边”方案才允许连续锁定。
                n = 1 if path else max(1, min(int(steps), 6))
                self.escape_commit = (d, box, now_t + 4.0, n)   # 4s 安全网；n 步后必停
                self.last_commit_dir = d
                self.last_commit_box = box
                self.last_commit_until = self.escape_commit[2]
                move_dirs = [d]
                log(f"脚下红区 → 逃逸承诺 {d} ×{n}步 框={box}")
        elif not self.escape_commit and near_danger:
            # 邻格有危险时，先做纯危险规避，不再先追怪。
            # 旧版在这里让 hybrid 先尝试追怪，实测会出现“怪在红区边、
            # 机器人先追怪再踩红区”的优先级倒挂；保命优先，追怪交给
            # 无危险分支。
            path, score = self._best_path(
                pc, danger_all, occupied, max_steps=3, mobs=mobs,
                escape_dir=escape_dir,
                prefer_mobs=(self.no_danger_mode == "hybrid"),
                soft=white_soft)
            if path:
                move_dirs = path
                log(f"危险规避 → {','.join(path)} score={score}")
            else:
                if escape_dir:
                    move_dirs = [escape_dir]
                    log(f"规避无路 → 短轴硬逃 {escape_dir}")
                else:
                    fd = self._pick_strafe(pi, pj, danger_all, soft=white_soft)
                    if fd:
                        move_dirs = [fd]
                        log(f"规避无路 → 强制横移 {fd}")
        elif not self.escape_commit and not mobs and exit_target \
                and not in_live and not near_danger:
            # 清场完成但没有怪：朝粉色出口箭头走，不再 hybrid 绕圈。
            step = self._step_toward(pi, pj, exit_target,
                                     danger_all, occupied)
            if step:
                move_dirs = [step]
                log(f"清场出口目标{exit_target} → 移动 {step}")
            else:
                log(f"清场出口目标{exit_target} 被阻挡 → 常规走位")
        elif not self.escape_commit and self._edge_target:
            # 顶边/左边静止怪：整格均值漏检后这里用暗像素单独找。它本来
            # 就在边缘，若还套用“离边≥2”的常规护栏永远打不到；因此只在
            # 已确认边缘怪时开一条临时通道，危险格仍由前面逃逸分支优先处理。
            hunt = self._pick_hunt((pi, pj), danger_all, occupied,
                                   [self._edge_target], allow_edge=True,
                                   soft=white_soft)
            if hunt:
                move_dirs = hunt
                used_pick_strafe = True
                log(f"边怪追击 {self._edge_target} → {','.join(hunt)}")
            elif abs(self._edge_target[0] - pi) + \
                    abs(self._edge_target[1] - pj) == 1:
                d = ("d" if self._edge_target[0] > pi else
                     "a" if self._edge_target[0] < pi else
                     "s" if self._edge_target[1] > pj else "w")
                n_atk = self._edge_attempts.get(self._edge_target, 0) + 1
                self._edge_attempts[self._edge_target] = n_atk
                if n_atk >= 3:
                    # 贴脸按了 3 次仍不消失，基本是木框/背景误匹配或
                    # 不可击杀装饰；再站在边角打只会被红区磨死。
                    log(f"边怪目标 {self._edge_target} 攻击{n_atk}次未消失 → 拉黑")
                    self._edge_blacklist.add(self._edge_target)
                    self._edge_target = None
                    self._edge_attempts = {}
                else:
                    move_dirs = [d]
                    used_pick_strafe = True
                    log(f"边怪贴身攻击 {self._edge_target} → {d} "
                        f"({n_atk}/3)")
            else:
                d = self._step_toward(pi, pj, self._edge_target,
                                      danger_all, occupied)
                if d:
                    move_dirs = [d]
                    used_pick_strafe = True
                    log(f"边怪逼近 {self._edge_target} → {d}")
        elif not self.escape_commit and \
                min(pi, GRID_N - 1 - pi, pj, GRID_N - 1 - pj) <= 3:
            # 已贴边（离边≤3）必须往场内走，不再横跳；这里优先回中心方向。
            cands = []
            if pi < center:
                cands.append(("d", center - pi))
            elif pi > center:
                cands.append(("a", pi - center))
            if pj < center:
                cands.append(("s", center - pj))
            elif pj > center:
                cands.append(("w", pj - center))
            for d, dist in sorted(cands, key=lambda x: -x[1]):
                di, dj = {"w": (0, -1), "s": (0, 1),
                          "a": (-1, 0), "d": (1, 0)}[d]
                nxt = (pi + di, pj + dj)
                if (0 <= nxt[0] < GRID_N and 0 <= nxt[1] < GRID_N
                        and nxt not in danger_all):
                    move_dirs = [d]
                    log(f"贴边({pi},{pj}) 离边≤2 → 强制回中 {d}")
                    break
        elif self.momentum:
            # 逃逸惯性(用户定稿):对跟踪施法的怪,逃出后沿同方向再跑几格,
            # 让下一发施法落在身后;绝不回原地抖动
            md, mc = self.momentum
            di, dj = {"w": (0, -1), "s": (0, 1), "a": (-1, 0), "d": (1, 0)}[md]
            nxt = (pi + di, pj + dj)
            if mc > 0 and 0 <= nxt[0] < GRID_N and 0 <= nxt[1] < GRID_N \
                    and nxt not in danger_all:
                self.momentum = (md, mc - 1)
                move_dirs = [md]
                log(f"逃逸惯性:继续 {md}(剩{mc - 1})")
            else:
                self.momentum = None
        elif not self.escape_commit and off_center > CENTER_RADIUS:
            # 中心(7,7)；|x-7| 或 |y-7| > CENTER_RADIUS 时先回中心，避免贴边吃伤害。
            cands = []
            if pi < center:
                cands.append(("d", center - pi))
            elif pi > center:
                cands.append(("a", pi - center))
            if pj < center:
                cands.append(("s", center - pj))
            elif pj > center:
                cands.append(("w", pj - center))
            for d, dist in sorted(cands, key=lambda x: -x[1]):
                di, dj = {"w": (0, -1), "s": (0, 1),
                          "a": (-1, 0), "d": (1, 0)}[d]
                nxt = (pi + di, pj + dj)
                if (0 <= nxt[0] < GRID_N and 0 <= nxt[1] < GRID_N
                        and nxt not in danger_all and nxt not in occupied):
                    move_dirs = [d]
                    log(f"中心偏移({pi},{pj}) dist={off_center}>{CENTER_RADIUS} → 回中心 {d}")
                    break
            if not move_dirs:
                fd = self._pick_strafe(pi, pj, danger_all, soft=white_soft)
                if fd:
                    move_dirs = [fd]
                    used_pick_strafe = True
                    log(f"回中心路径被堵 → 横向移动 {fd}")
        elif not self.escape_commit and not boss_mode:
            # 无紧迫红区：默认左右交叉，每一步都是一次攻击。
            # 对照模式 random 才在此低优先级分支做受限随机；危险/回中/
            # 贴边仍全部优先于它。
            if self.no_danger_mode == "random":
                pick = self._pick_random_walk(pi, pj, danger_all, occupied)
                if pick:
                    d, n = pick
                    move_dirs = [d] * n
                    used_pick_strafe = True
                    log(f"无危险 → 受限随机 {d}×{n}")
                else:
                    d = self._pick_strafe(pi, pj, danger_all, soft=white_soft)
                    if d:
                        move_dirs = [d]
                        used_pick_strafe = True
                        log(f"无危险随机无路 → 左右交叉 {d}")
            elif self.no_danger_mode == "hybrid":
                target_mobs = mobs_stable if mobs_stable else (
                    mobs if len(mobs) == 1 else [])
                hunt = self._pick_hunt((pi, pj), danger_all, occupied,
                                       target_mobs, soft=white_soft)
                if hunt:
                    move_dirs = hunt
                    used_pick_strafe = True
                    log(f"hybrid 安全追怪 → {','.join(hunt)}")
                else:
                    pick = self._pick_hybrid(pi, pj, danger_all, occupied, soft=white_soft)
                    if pick:
                        d, n = pick
                        move_dirs = [d] * n
                        used_pick_strafe = True
                        log(f"无危险 → hybrid {'随机冲刺 ' if n > 1 else '交替 '}"
                            f"{d}×{n}")
            else:
                d = self._pick_strafe(pi, pj, danger_all, soft=white_soft)
                if d:
                    move_dirs = [d]
                    used_pick_strafe = True
                    log(f"无危险 → 左右交叉 {d}")
        # 绝对边缘护栏：用户两次大扣血都在贴边区。只要离边≤3，
        # 任何朝外方向（包括红区逃逸承诺）都改成朝场内；先站稳场内再躲。
        if not boss_mode and not self._edge_target \
                and not self.escape_commit and not self.momentum \
                and not in_live and not near2 and not near_danger:
            ex = min(pi, GRID_N - 1 - pi)
            ey = min(pj, GRID_N - 1 - pj)
            if ex <= 2 or ey <= 2:
                center = GRID_N // 2
                if ex <= ey:
                    inward = "d" if pi < center else "a"
                else:
                    inward = "s" if pj < center else "w"
                di, dj = {"w": (0, -1), "s": (0, 1),
                          "a": (-1, 0), "d": (1, 0)}[inward]
                if (pi + di, pj + dj) in danger_all:
                    # 红区优先于回中：不能为了离边而往当前红区里钻。
                    log(f"边缘护栏({pi},{pj}) → 朝内{inward}是红区，跳过回中")
                else:
                    outward = OPPOSITE[inward]
                    if not move_dirs or move_dirs[0] == outward:
                        log(f"边缘护栏({pi},{pj}) 离边≤2 → 取消朝外{outward}，强制 {inward}")
                        move_dirs = [inward]
                        self.escape_commit = (inward, (pi, pj, pi, pj),
                                              time.time() + 1.2, 2)
                        self.momentum = None
        if not move_dirs:
            # 兜底：只要还在战斗、左右有一格可走，就必须点一次，不能停住。
            fd = self._pick_strafe(pi, pj, danger_all, soft=white_soft)
            if fd:
                move_dirs = [fd]
                used_pick_strafe = True
                log(f"路径为空 → 强制横向移动 {fd}")
        # 屏幕红区安全层：清怪关以玩家为画面锚点，虚拟坐标可能因攻击
        # 不位移而漂移；只要屏幕玩家格脚下/邻格是红区，就以屏幕坐标
        # 选出的安全方向为准，覆盖上面所有常规/Boss 走位。
        # P12：不再每帧清空方向，而是形成跨帧“逃逸闩锁”。短直线计数 1
        # 和 3×7 的 4 计数周期都需要连续走，不能走一步就重新规划。
        screen_in_red = bool(spc is not None and spc in red_zone)
        if not screen_escape and not screen_in_red:
            self.screen_exit_dir = None
            self.screen_exit_left = 0
        if screen_escape:
            now = time.time()
            d = screen_escape
            # 旧闩锁的下一格仍安全就沿用；只有红区移动/封路才重选。
            if (self.screen_exit_dir and self.screen_exit_left > 0
                    and now <= self.screen_exit_until):
                cand = self.screen_exit_dir
                di, dj = {"w": (0, -1), "s": (0, 1),
                          "a": (-1, 0), "d": (1, 0)}[cand]
                nxt = (spc[0] + di, spc[1] + dj)
                if (0 <= nxt[0] < GRID_N and 0 <= nxt[1] < GRID_N
                        and nxt not in danger_all and nxt not in occupied):
                    d = cand
                else:
                    log(f"屏幕红区闩锁 {cand} 下一格不安全 → 重选")
                    self.screen_exit_left = 0
            di, dj = {"w": (0, -1), "s": (0, 1),
                      "a": (-1, 0), "d": (1, 0)}[d]
            screen_target = (spc[0] + di, spc[1] + dj)
            if not (0 <= screen_target[0] < GRID_N
                    and 0 <= screen_target[1] < GRID_N
                    and screen_target not in danger_all
                    and screen_target not in occupied):
                log(f"屏幕红区优先跳过: {d} 目标"
                    f"{screen_target} 仍危险/占用")
                self.screen_exit_left = 0
            else:
                if self.screen_exit_left <= 0 or now > self.screen_exit_until:
                    self.screen_exit_dir = d
                    self.screen_exit_left = 3 if screen_in_red else 2
                    # 当前循环约 3.3s/步，闩锁有效期必须覆盖 3 步
                    self.screen_exit_until = now + 12.0
                    log(f"屏幕红区闩锁 {d} ×{self.screen_exit_left}步")
                else:
                    log(f"屏幕红区闩锁继续 {d}(剩{self.screen_exit_left})")
                move_dirs = [d]
                used_pick_strafe = True
                self.escape_commit = None
                self.momentum = None
                self.screen_exit_left -= 1
        _bs_dec = time.time()
        # 只要这一轮不是危险规避，就翻转下一轮方向，形成左一下右一下。
        # _pick_strafe 自己已经翻转；不要再翻一次，否则会连续同向两步。
        if not near_danger and not used_pick_strafe:
            self.strafe_dir = "a" if self.strafe_dir == "d" else "d"

        # 每一次都走“鼠标按压 + 抓帧确认”。不再用键盘 tap：
        # 实测游戏 D-pad 不接受瞬时/键盘，且必须确认角色换格。
        moved_ok = 0
        _p0 = time.time()
        for k, d in enumerate(move_dirs):
            if k > 0:
                # 连按间隔 <300ms 会被游戏合并成一次(2026-11-14 死亡复盘:
                # 两次 W 相隔 129ms,虚拟坐标 +2 实际只走 1 格 → 漂移)
                time.sleep(0.3)
            if self._move_one(d, img):
                moved_ok += 1
            else:
                break
        if move_dirs:
            log(f"执行移动 {','.join(move_dirs)} → 确认 {moved_ok}/{len(move_dirs)} 格 "
                f"耗时{(time.time()-_p0)*1000:.0f}ms")
        if DIAG:
            log(f"⏱BS 检测={(_bs_det-_bs_det0)*1000:.0f}ms "
                f"取样={(_bs_occ-_bs_det)*1000:.0f}ms "
                f"决策={(_bs_dec-_bs_occ)*1000:.0f}ms "
                f"按={(time.time()-_bs_dec)*1000:.0f}ms "
                f"总={(time.time()-_bs_det0)*1000:.0f}ms")

        # 不再因为“抓帧没定位到玩家”就停脚本：战斗残影/伤害数字会遮住黄色括号，
        # 误停反而让角色站着挨打。只有十字键彻底丢失才由场景层停止。
        if moved_ok > 0:
            self._input_fail_streak = 0
        # 防卡死保险丝:每 5 步对比画面,角色没动 → 换轴转向+重定位十字键
        # (虚拟坐标只管"按了没",不知道"走没走",这是盲走唯一的理论弱点)
        self._stuck_n += 1
        if self._stuck_n % 5 == 0:
            if self._stuck_ref is not None:
                d5 = float(np.abs(cv2.cvtColor(self._stuck_ref, cv2.COLOR_BGR2GRAY)
                                  .astype(int)
                                  - cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                                  .astype(int)).mean())
                if d5 < 2.0:
                    log("⚠ 防卡死:5 步画面几乎无变化 → 换轴转向+重定位十字键")
                    self.strafe_dir = "a" if self.strafe_dir == "d" else "d"
                    self.dpad_geom = None
            self._stuck_ref = img
        # 0.3s 移动节拍(用户重校:红区全部脚下发起,站着=叠区等死,
        # 角色必须持续在动;检测每帧全速跑)
        pad = MOVE_INTERVAL - (time.time() - t0)
        if pad > 0:
            interruptible_sleep(pad)
        self._bs_end_t = time.time()
        self._prev_bs_end = time.time()

    # ── 对照组 blind:不检测红区,纯移动(用户设计)──
    def blind_step(self):
        """开局上移 5 格 → 绕中心方形环路(四角 4/10)顺时针,
        每段 4-6 格、90° 转弯不走回头路,0.3s 节拍,完全不检测红区。
        依据:红区全部从脚下发起,持续长跑让新区永远刷在身后。"""
        if not self.battle_intro_done:
            self.battle_intro_done = True
            log("盲走:战斗开场动画等待 3.2s")
            interruptible_sleep(3.2)
            self._input_focused = self.focus_game_input()
            interruptible_sleep(0.35)
        t0 = time.time()
        if self.last_pc is None:
            self.last_pc = (7, 7)
        if self.blind_up_left > 0:
            self.blind_up_left -= 1
            log(f"盲走开局上移(剩{self.blind_up_left})")
            self._move_one("w")
        else:
            d = self._blind_next_dir()
            ta = time.time()
            self._move_one(d)
            log(f"盲走 {self.last_pc} press={((time.time()-ta)*1000):.0f}ms "
                f"step={((time.time()-t0)*1000):.0f}ms")
        pad = MOVE_INTERVAL - (time.time() - t0)
        if pad > 0:
            interruptible_sleep(pad)

    def _blind_next_dir(self):
        """朝当前目标角走(先走距离大的轴,每段自然 4-6 格);
        到角后顺时针换下一角,永不回头。"""
        corners = [(4, 4), (10, 4), (10, 10), (4, 10)]
        tx, ty = corners[self.blind_corner % 4]
        pi, pj = self.last_pc if self.last_pc else (7, 7)
        if (pi, pj) == (tx, ty):
            self.blind_corner = (self.blind_corner + 1) % 4
            tx, ty = corners[self.blind_corner % 4]
        dx, dy = tx - pi, ty - pj
        if dx == 0 and dy == 0:
            self.blind_corner = (self.blind_corner + 1) % 4
            return self._blind_next_dir()
        if abs(dx) >= abs(dy):
            return "d" if dx > 0 else "a"
        return "s" if dy > 0 else "w"

    # ── 大厅 ──
    def handle_hub(self):
        if self.pending_score:
            self.pending_score = False
            # 每局回大厅读一次积分；读到目标值就结束整轮（不再进关卡）。
            if self.score_target > 0:
                score = self.read_score()
                if score is not None and score > self.score_target:
                    log(f"★ 积分 {score} > 目标 {self.score_target} → 停止整轮")
                    self.stop_reason = "score_target"
                    self.stop = True
                    return
            else:
                log("大厅积分读取：已关闭（R1999_SCORE_TARGET=0）")
        img = self.cap()
        # 进入对话开着?(classify 的 entry 分支是主路径,这里是兜底)
        roi = img[470:560, 1380:1860]
        dlg_rb = float(roi[:, :, 2].mean() - roi[:, :, 0].mean())
        if dlg_rb > 60:
            log(f"进入弹窗开着 (R-B={dlg_rb:.0f}) → 点进入")
            self.real_click(1620, 507)
            time.sleep(2.5)
            return
        # 模板定位关卡机器(STAGE_LEVEL),点到即进,不依赖固定坐标
        mt = self.machines.get(STAGE_LEVEL)
        if mt is None:
            mt = self.machines[1]
        res = cv2.matchTemplate(img, mt, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(res)
        if mv >= 0.75:
            mh, mw = mt.shape[:2]
            log(f"关卡{STAGE_LEVEL}机器 conf={mv:.2f} @ {ml} → 点击")
            self.real_click(ml[0] + mw // 2, ml[1] + mh // 2 - 20)
            time.sleep(2)
            return
        # 机器不在视野 → 上移找;撞墙仍无 → 固定点兜底
        a = img
        self.real_click(*HALL_DPAD["up"])
        time.sleep(0.9)
        b = self.cap()
        moved = float(np.abs(
            cv2.cvtColor(a, cv2.COLOR_BGR2GRAY).astype(int) -
            cv2.cvtColor(b, cv2.COLOR_BGR2GRAY).astype(int)).mean())
        log(f"大厅上移 diff={moved:.1f}")
        if moved < 2.0:
            res = cv2.matchTemplate(b, mt, cv2.TM_CCOEFF_NORMED)
            _, mv2, _, ml2 = cv2.minMaxLoc(res)
            if mv2 >= 0.75:
                mh, mw = mt.shape[:2]
                log(f"上移后找到关卡{STAGE_LEVEL}机器 conf={mv2:.2f} → 点击")
                self.real_click(ml2[0] + mw // 2, ml2[1] + mh // 2 - 20)
            else:
                # 不再盲点固定坐标：机器模板没命中就下一帧继续找
                log("机器未定位 → 不盲点固定坐标，等待下一帧")
            time.sleep(1.5)

    # ── 冻结唤醒 ──
    def nudge_window(self):
        import ctypes
        hwnd = self.sc._hwnd
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 6)
        time.sleep(0.5)
        user32.ShowWindow(hwnd, 9)
        time.sleep(1.2)
        log("窗口最小化/恢复以唤醒渲染")

    # ── 主循环 ──
    def _emit_status(self, state):
        """把当前局数/商店/停止原因推给 GUI；回调异常不影响游戏循环。"""
        cb = getattr(self, "status_cb", None)
        if cb is None:
            return
        total_shops = sum(c for st in self.exp_stats.values() for _, c in st)
        total_shops += self.run_shops
        data = {
            "state": state,
            "runs": self.runs,
            "max_runs": self.max_test_runs,
            "current_shops": self.run_shops,
            "total_shops": total_shops,
            "stop_reason": self.stop_reason,
            "score_target": self.score_target,
            "score": self.last_score,
            "no_danger_mode": self.no_danger_mode,
        }
        try:
            cb(data)
        except Exception:
            pass

    def run(self):
        log("=== r1999 loop v4 启动 ===")
        log(f"测试出口: 轮数上限={self.max_test_runs or '不限'} "
            f"积分目标={self.score_target or '关闭'}")
        log(f"无红区走位模式: {self.no_danger_mode}")
        register_task_thread()
        self._emit_status("running")
        frozen_count = 0
        last_cap = None
        prev_scene = None
        self.stop = False
        self.stop_reason = None
        while time.time() - self.start < MAX_RUNTIME and not self.stop:
            try:
                # 战斗帧强制走桌面快速截屏；战斗结束后恢复正常前台判断。
                _lt = time.time()
                self._force_fast = (prev_scene == "battle")
                img = self.cap()
                _t_cap = time.time() - _lt
                if getattr(self, "_prev_bs_end", None):
                    _gap = time.time() - self._prev_bs_end
                    if DIAG and _gap > 1.0:
                        log(f"⏱GAP bs_end→场景={_gap*1000:.0f}ms "
                            f"(cap={_t_cap*1000:.0f}ms)")
                self._prev_bs_end = None
                scene = None
                # 战斗帧快速路径：十字键 + 战斗 HUD 就无需全量模板分类，
                # 帧耗时从 ~0.5s 降到 ~0.15s（倒计时躲预警靠这个抢时间）
                if prev_scene == "battle":
                    half = cv2.resize(img, None, fx=0.5, fy=0.5,
                                      interpolation=cv2.INTER_AREA)
                    if self.has_dpad(img, half):
                        pv = float(cv2.minMaxLoc(cv2.matchTemplate(
                            half, self.tpls["pause"], cv2.TM_CCOEFF_NORMED))[1])
                        bv = float(cv2.minMaxLoc(cv2.matchTemplate(
                            half, self.tpls["battle"], cv2.TM_CCOEFF_NORMED))[1])
                        if pv >= 0.85:
                            scene = "pause"
                        elif bv >= 0.78:
                            scene = "battle"
                if scene is None:
                    scene = self.classify(img)
                # 无十字键时补一次 GAME OVER 模板确认：结算页可能因动画/
                # 过渡帧被 classify 判成 unrecognized，错过后会“卡住不动”。
                if scene == "unrecognized" and not self.has_dpad(img):
                    try:
                        half_g = cv2.resize(img, None, fx=0.5, fy=0.5,
                                            interpolation=cv2.INTER_AREA)
                        gv = float(cv2.minMaxLoc(cv2.matchTemplate(
                            half_g, self.tpls["gameover"],
                            cv2.TM_CCOEFF_NORMED))[1])
                        sv = 0.0
                        if self.gameover_stats_tpl is not None:
                            sh = cv2.resize(self.gameover_stats_tpl, None,
                                            fx=0.5, fy=0.5,
                                            interpolation=cv2.INTER_AREA)
                            sv = float(cv2.minMaxLoc(cv2.matchTemplate(
                                half_g, sh, cv2.TM_CCOEFF_NORMED))[1])
                        if max(gv, sv) >= 0.82:
                            log(f"补判 GAME OVER 模板 conf={max(gv, sv):.2f}")
                            scene = "gameover"
                    except Exception:
                        pass
                if scene == "unrecognized" and time.time() < self._adb_mode_until:
                    try:
                        af = self.adb_frame()
                        if af is not None:
                            asc = self.classify(af)
                            if asc != "unrecognized":
                                img, scene = af, asc
                    except StopRequested:
                        raise
                    except Exception as e:
                        log(f"ADB 画面兜底失败: {e!r}")

                if last_cap is not None and last_cap.shape == img.shape:
                    d = float(np.abs(cv2.cvtColor(last_cap, cv2.COLOR_BGR2GRAY).astype(int) -
                                     cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(int)).mean())
                    frozen_count = frozen_count + 1 if d < 0.05 else 0
                else:
                    frozen_count = 0
                last_cap = img
                if frozen_count >= FREEZE_LIMIT:
                    log("⚠ 画面持续冻结 — 不动作，等待人工/空格停止（不再 adb 重启）")
                    frozen_count = 0

                log(f"场景: {scene}")
                # 全相位分解:上一步 bs 尾→本次循环顶(防卡死+节拍+sleep)
                # →cap→场景→dispatch,四段各自计时,定位全部幽灵时间
                _now2 = time.time()
                _loop_dt = _now2 - getattr(self, "_last_loop_end", _lt - 1.0)
                _prev_bs = getattr(self, "_bs_end_t", None)
                _p1 = (_lt - _prev_bs) if _prev_bs else 0.0
                _disp = (getattr(self, "_bs_end_t", 0.0) -
                         getattr(self, "_disp0", 0.0)) \
                    if getattr(self, "_disp0", 0.0) else 0.0
                if DIAG and _loop_dt > 0.8:
                    log(f"⏱ 循环{_loop_dt*1000:.0f}ms "
                        f"[bs尾→循环头={_p1*1000:.0f}ms cap={_t_cap*1000:.0f}ms "
                        f"场景={(_now2-_lt-_t_cap)*1000:.0f}ms "
                        f"dispatch={_disp*1000:.0f}ms]")
                self._last_loop_end = _now2
                self._disp0 = _now2
                # 十字键 = 仍在小游戏内的硬标志；连续缺失就停，不再乱点
                if scene in ("battle", "hub"):
                    if not self.has_dpad(img):
                        # 关卡清场/结算过渡：没有十字键，但右上角有「返回界面」
                        if scene == "battle" and self.return_tpl is not None:
                            res = cv2.matchTemplate(
                                img, self.return_tpl, cv2.TM_CCOEFF_NORMED)
                            if float(cv2.minMaxLoc(res)[1]) >= 0.85:
                                log("关卡清场（无十字键+返回界面可见）→ 点返回界面进商店")
                                self.stage_clear_pending = True
                                adb_tap(1735, 105)
                                time.sleep(2.5)
                                self.dpad_missing = 0
                                continue
                        self.dpad_missing += 1
                        log(f"⚠ 十字键缺失({self.dpad_missing}/25, score={self._last_dpad_score:.2f}) — 可能过渡帧/已离开小游戏")
                        if self.dpad_missing >= 25:
                            self.stop_escape(img, "十字键丢失")
                            continue
                        time.sleep(0.6)
                        continue
                    self.dpad_missing = 0
                    # 已回到有十字键的关卡/大厅：清场→商店的出口状态结束
                    self.stage_clear_pending = False
                else:
                    self.dpad_missing = 0
                if scene != "shop":
                    self.shop_done = False
                    self.shop_no_exit = 0
                    self.shop_entry_shot = False
                if scene == "hub" and prev_scene in ("battle", "gameover"):
                    # 战斗结束回到大厅(死亡或通关)→ 读一次积分看刷分效率
                    self.pending_score = True
                if scene == "battle" and self.run_start_t is None \
                        and prev_scene in ("blessing", "entry", "hub", "shop"):
                    # 实验指标:一局的存活计时从第一场战斗开始
                    self.run_start_t = time.time()
                if scene == "shop" and not self.shop_entry_shot:
                    # 实验指标:每遇见一次商店 = 过了一关
                    self.run_shops += 1
                    self._emit_status("running")
                if scene != prev_scene:
                    # 大厅/战斗的十字键位置和大小不同，换场景必须重新定位
                    self.dpad_geom = None
                    if scene == "battle":
                        self.battle_intro_done = False
                        self._input_focused = False
                        self.last_pc = None
                if prev_scene is None:
                    # 首帧若停在上一局残留的结算页，标记为不计新局。
                    self._startup_ghost = (scene == "gameover")
                prev_scene = scene
                if scene != "unrecognized":
                    self.unrecognized_since = None
                    self.restart_streak = 0
                    if scene in GAMEPLAY_SCENES:
                        self.recovery_mode = False
                    if scene != "warning":
                        self.stage_until["post_warning"] = 0.0

                if scene == "unrecognized":
                    self.handle_unrecognized(img)
                    continue

                if scene == "battle":
                    if self.exp_mode == "blind":
                        self.blind_step()
                    else:
                        self.battle_step(img)
                    _g_a = time.time() - getattr(self, "_bs_end_t", 0.0)
                    time.sleep(random.uniform(0.02, 0.06))
                    # [DIAG] 幽灵节拍定位:实测循环 1.48s 但各段合计仅 ~0.45s,
                    # 差 ~1s 在 bs 返回之后。sleep 前后分段计时找元凶。
                    _g_b = time.time() - getattr(self, "_bs_end_t", 0.0)
                    if DIAG and _g_b > 0.4:
                        log(f"⏱GHOST bs尾→sleep前={_g_a*1000:.0f}ms "
                            f"→sleep后={_g_b*1000:.0f}ms")
                else:
                    # 离开战斗场景就清空本场起点/移动记录，下一场重新记录。
                    self.battle_start_pc = None
                    self.battle_moves = []
                    self._logged_pc = None
                    self.momentum = None
                if scene == "battle":
                    # 战斗已经在上面的 scene == "battle" 分支执行过；
                    # 这里显式吃掉 battle，避免落到场景链末尾的
                    # else: time.sleep(1)，造成每轮 1 秒的“幽灵”空档。
                    pass
                elif scene == "blessing":
                    self.handle_blessing(img)
                elif scene == "shop":
                    self.handle_shop(img)
                elif scene == "entry":
                    self.click_btn(img, "entry_ok")
                    time.sleep(2.5)
                elif scene == "gameover":
                    if getattr(self, "_startup_ghost", False):
                        self._startup_ghost = False
                        log("启动即 GAME OVER（上一局残留）→ 不计入新局")
                        self.real_click(*GAMEOVER_BLANK)
                        time.sleep(2)
                        continue
                    self.runs += 1
                    self._emit_status("running")
                    if self.max_test_runs > 0 and self.runs >= self.max_test_runs:
                        log(f"测试轮数达到 {self.max_test_runs} 局 → 停止整轮")
                        self.stop_reason = "max_runs"
                        self.stop = True
                    # 实验记账:存活时长 + 商店次数
                    dur = (time.time() - self.run_start_t) \
                        if self.run_start_t else 0.0
                    self.exp_stats[self.exp_mode].append(
                        (round(dur), self.run_shops))
                    self.mode_stats.setdefault(self.no_danger_mode, []).append(
                        (round(dur), self.run_shops))
                    log(f"📊 实验[{self.exp_mode}] 局{self.runs} "
                        f"存活{dur:.0f}s 商店{self.run_shops}次 "
                        f"走位[{self.no_danger_mode}]")
                    if self.max_test_runs > 0 and \
                            self.runs >= self.max_test_runs:
                        st = self.mode_stats.get(self.no_danger_mode, [])
                        if st:
                            avg = sum(s for s, _ in st) / len(st)
                            shops = sum(c for _, c in st)
                            log(f"📊📊 走位[{self.no_danger_mode}] "
                                f"完成 {len(st)} 局: 平均存活 {avg:.0f}s, "
                                f"商店合计 {shops}, 明细={st}")
                    self.run_start_t = None
                    self.run_shops = 0
                    self._emit_status("running")
                    self.exp_runs_left -= 1
                    if self.exp_runs_left <= 0:
                        st = self.exp_stats[self.exp_mode]
                        if st:
                            avg = sum(s for s, _ in st) / len(st)
                            shops = sum(c for _, c in st)
                            log(f"📊📊 组[{self.exp_mode}] 完成 {len(st)} 局: "
                                f"平均存活 {avg:.0f}s, 商店合计 {shops}, "
                                f"明细={st}")
                        if self.exp_mode == "detect":
                            self.exp_mode = "blind"
                            self.exp_runs_left = 10
                            self.blind_up_left = 5
                            self.blind_corner = 0
                            log("📊 切换对照组 → blind(不检测红区,纯移动绕圈)")
                        else:
                            log("📊 两组各 10 局完成 — 实验结束")
                            self.stop = True
                    if DIAG and self._frame_buf:
                        # [DIAG] 死前录像落盘:缓冲的全部战斗帧(JPG q85)
                        import datetime as _dt
                        ts = _dt.datetime.now().strftime("%H%M%S")
                        d = os.path.join(TEMP,
                                         f"r1999_death_{ts}")
                        os.makedirs(d, exist_ok=True)
                        for i, (t_f, fr) in enumerate(self._frame_buf):
                            cv2.imwrite(os.path.join(d, f"f{i:02d}_{t_f:.1f}.jpg"),
                                        fr, [cv2.IMWRITE_JPEG_QUALITY, 85])
                        log(f"[DIAG] 死前录像 {len(self._frame_buf)} 帧 → "
                            f"{os.path.basename(d)}/")
                        self._frame_buf.clear()
                    log(f"GAME OVER（第 {self.runs} 局结束）→ 点空白关闭")
                    self.real_click(*GAMEOVER_BLANK)
                    time.sleep(3)
                elif scene == "warning":
                    log("恢复链：癫痫警告页 → 点击继续")
                    self.real_click(*WARNING_TAP)
                    self.stage_until["post_warning"] = time.time() + POST_WARNING_BACK
                    time.sleep(2.5)
                elif scene == "title":
                    if self.recovery_mode:
                        log("恢复链：标题页 → adb 点 START")
                        adb_tap(*TITLE_START_ADB)
                        time.sleep(8)
                    else:
                        self.stop_escape(img, "标题页")
                elif scene == "mainui":
                    # 主界面:恢复链中=点横幅回小游戏;否则=异常脱出,停止防误触
                    if self.recovery_mode:
                        self.handle_banner()
                    else:
                        self.stop_escape(img, "主界面")
                elif scene == "pause":
                    log("恢复链：GAME PAUSED → 点取消恢复战斗")
                    self.real_click(*PAUSE_CANCEL_MSS)
                    time.sleep(2)
                elif scene == "reward":
                    # 积分跨过奖励档位后弹出的「自我之鉴」等展示页:点右下 X 关闭
                    log("恢复链：奖励展示页(积分跨档解锁) → 点右下 X 关闭")
                    adb_tap(1806, 1011)
                    time.sleep(2.5)
                elif scene == "hub":
                    self.handle_hub()
                else:
                    time.sleep(1)
            except DangerPage:
                logger.critical("危险页面终止", exc_info=True)
                log("⛔⛔ 检测到支付/充值类危险页面 — 已终止,绝不点击。请人工处理 ⛔⛔")
                self.stop_reason = "danger"
                break
            except StopRequested:
                log("空格急停")
                self.stop_reason = "space"
                break
            except Exception as e:
                logger.error(f"异常: {e!r}", exc_info=True)
                time.sleep(2)
        if self.stop_reason is None:
            self.stop_reason = "max_runtime"
        log(f"=== 结束：{self.runs} 局，运行 {(time.time()-self.start)/3600:.1f}h ===")
        self._emit_status("stopped")
        ack_stop()
        task_finished()

    # ── 未知界面：先静观，稳定后才恢复 ──
    def handle_unrecognized(self, img):
        now = time.time()
        # 先排除「截图截错窗口」：mss 截到的是别的窗口时，ADB 截屏仍是真实游戏画面
        try:
            af = self.adb_frame()
            if af is not None:
                # 积分面板残留(带领取按钮/上次未关)→ 走读分流程领奖+关闭
                ok, claims, _ = self._score_panel_state(af)
                if ok:
                    log("识别到积分面板(adb) → 领奖+读分+关闭")
                    self.unrecognized_since = None
                    self.read_score(panel_open=True)
                    return
                ascn = self.classify(af)
                if ascn != "unrecognized":
                    log(f"mss 未知但 ADB 画面识别为 {ascn} → 采用 ADB 画面继续")
                    self.unrecognized_since = None
                    self._last_adb_img = af
                    self._last_adb_scene = ascn
                    self._adb_mode_until = time.time() + 20
                    return
        except StopRequested:
            raise
        except Exception as e:
            log(f"ADB 画面复核失败: {e!r}")
        # 再尝试把 MuMu 置前（用户切到别的窗口时 mss 会截到桌面/聊天）
        if self.ensure_foreground():
            try:
                fg = self.cap()
                fscn = self.classify(fg)
                if fscn != "unrecognized":
                    log(f"窗口置前后识别为 {fscn} → 继续")
                    self.unrecognized_since = None
                    self._adb_mode_until = 0.0
                    return
            except StopRequested:
                raise
            except Exception as e:
                log(f"置前复截失败: {e!r}")
        if self.unrecognized_since is None:
            self.unrecognized_since = now
            self.save_evidence(img, "unknown")
            log(f"未知界面（可能是过渡帧），静观 {SETTLE}s ...")
            time.sleep(SETTLE)
            return
        stuck = now - self.unrecognized_since
        if stuck > UNKNOWN_LIMIT:
            # 不再 adb 重启：未知页可能是游戏结算/菜单，重启属于破坏性操作
            logger.error("未知界面持续超限 — 停止脚本，请人工检查")
            self.stop_escape(img, "未知界面持续超限")
            return
        if stuck > SETTLE:
            # 未知界面且没有十字键：多半已离开小游戏/菜单过渡，绝不乱点
            if not self.has_dpad(img):
                log("未知界面且无十字键 → 不动作；若持续在外部页面则停止")
                if stuck > SETTLE + 4:
                    self.stop_escape(img, "未知界面(无十字键)")
                    return
            else:
                self.recovery_step(img)
        time.sleep(1.2)

    # ── 主界面（banner 场景）：横幅 → 弹窗/活动页 ──
    def handle_banner(self):
        log("恢复链：游戏主界面 → 定位活动横幅")
        if not self.click_btn(self.cap(), "banner", timeout=6, required=False):
            # 横幅可能被每日剧情对话盖住 → 推进对话（实测坐标）再试一次
            log("横幅未命中 → 推进主界面对话后重试")
            adb_tap(*DIALOG_ADVANCE_ADB)
            time.sleep(3)
            if not self.click_btn(self.cap(), "banner", timeout=5, required=False):
                log("横幅仍未命中，等待下轮")
                return
        self.stage_until["post_banner"] = time.time() + POST_BANNER_WINDOW
        self.unrecognized_since = time.time()   # 让下轮立刻进入 post_banner 判定
        time.sleep(3)

    # ── 异常脱出 → 停止(用户策略:主界面/标题出现=离开小游戏,防误触)──
    def stop_escape(self, img, where):
        self.save_evidence(img, "escape")
        logger.error(f"异常脱出: {where} — 停止脚本防误触")
        log(f"⛔ 检测到{where}(非恢复流程) — 脚本停止,请人工检查")
        self.stop_reason = "escape"
        self.stop = True

    # ── 重启 + 熔断（熔断触发时置 self.stop，由主循环退出）──
    def do_restart(self):
        self.restart_streak += 1
        if self.restart_streak >= RESTART_BREAKER:
            log(f"⛔ 连续 {self.restart_streak} 次重启未回到已知场景 — 熔断终止")
            self.stop = True
            return
        self.recovery_mode = True   # 冷启动链(警告→标题→主界面→横幅)放行
        adb_restart_game()


class R1999Script(GameScript):
    """r1999 街机肉鸽挂机 — 实时循环型脚本（自管窗口，不走任务模板引擎）。

    GameBot 仅作占位（与 deltaforce 同模式）；唯一的 repeat 任务回调里
    跑完整 Loop().run()（单次受 MAX_RUNTIME 约束，返回后引擎立即再次
    调度 = 整夜连续挂机）。急停：空格键（全局 hook）。
    """

    def __init__(self):
        self._loop = None
        # GUI 可注入实时状态回调；CLI 保持 None。
        self.status_cb = None

    @property
    def name(self) -> str:
        return "r1999"

    def startup(self, bot) -> bool:
        # 无启动清理；窗口锚定/恢复全部由 Loop 自己负责
        return True

    def get_tasks(self):
        from core.task import Task
        # 注意：此处不得实例化 Loop（GUI 会在 MuMu 未启动时就调 get_tasks）
        return [Task(name="街机挂机", template="", repeat=True,
                     callback=self._run_loop)]

    def _run_loop(self, bot) -> bool:
        # 每次会话新建 Loop（全新 MAX_RUNTIME 计时与状态）
        self._loop = Loop()
        self._loop.status_cb = self.status_cb
        self._loop.run()
        # 以下停止原因是“整脚本退出”，不能只结束当前 Loop 后被 repeat=True
        # 又拉起来；抛 StopRequested 让任务引擎在同一轮真正收尾。
        if self._loop.stop_reason in (
                "max_runs", "score_target", "escape", "danger", "space"):
            raise StopRequested(f"r1999 结束：{self._loop.stop_reason}")
        return True


if __name__ == "__main__":   # 直接运行（需 cwd=项目根）：python games/r1999/r1999.py
    Loop().run()
