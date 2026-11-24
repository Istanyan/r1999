"""
游戏自动化脚本 GUI 界面 — 基于 tkinter。

运行方式: python gui.py
18:24:36 [INFO] 演武：三次确认军令机会 ≤1，判定演武成功
18:24:37 [INFO] 演武专注补打结束：达成=True，机会=0，执行 5 轮，耗时 247.1s
18:24:41 [INFO] 演武：识别军令机会 1（置信度 1.000）
18:24:44 [INFO] 演武收尾：战报共点击领取 1 次
18:24:45 [INFO] 演武：判定成功后已立即领奖
18:24:45 [INFO] 第 2 轮扫描全部完成（急报清完/演武结束/体力清完）
这里演武完成后领奖的部分太糙了，需要优化。
"""

import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import queue
import logging
import sys
import os
import json

# ──────────────────────────────────────────────
# 将项目根目录加入 path
# ──────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.game_bot import GameBot
from core.task import Task
from core.game_script import GameScript
from core.action import (Action, StopRequested, request_stop, reset_stop,
                         register_task_thread, ack_stop, task_finished)

# 导入任务系统
try:
    from main import GAME_REGISTRY, execute_tasks, run_game
    from games.deltaforce import DF_WINDOW
    from games.r1999 import R1999_WINDOW
    _HAS_TASK_SYSTEM = True
except Exception:
    _HAS_TASK_SYSTEM = False
    GAME_REGISTRY = {}
    DF_WINDOW = "三角洲行动"
    R1999_WINDOW = "MuMu模拟器12"


# ──────────────────────────────────────────────
# GUI 默认匹配阈值
# ──────────────────────────────────────────────
DEFAULT_MATCH_THRESHOLD = 0.85


# ──────────────────────────────────────────────
# 日志队列处理器
# ──────────────────────────────────────────────
class QueueHandler(logging.Handler):
    def __init__(self, log_queue: queue.Queue):
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record):
        msg = self.format(record)
        self.log_queue.put(msg)


# ──────────────────────────────────────────────
# GUI 主类
# ──────────────────────────────────────────────
class GameBotGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("游戏自动化脚本助手")
        self.root.geometry("960x750")
        self.root.minsize(860, 600)

        # 状态
        self.bot: GameBot | None = None
        self.running = False
        self.stop_flag = threading.Event()
        self.log_queue = queue.Queue()
        self.r1999_status_queue = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.screenshot_img: tk.PhotoImage | None = None
        self.r1999_config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "r1999_gui.json")

        # 任务数据
        self.tasks: list = []
        self.task_vars: list[tk.BooleanVar] = []  # 每个任务的 enabled 状态变量（勾选=执行，不勾选=跳过）
        self.task_widgets: list[dict] = []  # 每个任务的 GUI 组件引用

        # 样式
        self._setup_style()
        # 日志（提前初始化，确保单任务执行也有日志）
        self._setup_log_handler()
        # 布局
        self._build_ui()
        self._load_r1999_config()
        self._reset_r1999_status()
        # 初始加载任务
        self._reload_tasks()
        # 日志轮询
        self._poll_log_queue()
        self._poll_r1999_status()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── 样式 ────────────────────────────────────
    def _setup_style(self):
        style = ttk.Style(self.root)
        available = style.theme_names()
        if "clam" in available:
            style.theme_use("clam")
        style.configure("Title.TLabel", font=("Microsoft YaHei", 11, "bold"))
        style.configure("Section.TLabelframe.Label", font=("Microsoft YaHei", 10, "bold"))
        style.configure("Task.TButton", padding=2)
        style.configure("Repeat.TLabel", font=("Microsoft YaHei", 8), foreground="#e67e22")
        style.configure("Settings.TButton", padding=1)

    # ── 日志初始化 ───────────────────────────────
    def _setup_log_handler(self):
        """初始化日志处理器：队列处理器（送 GUI Text 框）+ 文件处理器（落盘回溯）。

        业务模块（征战/成长/小游戏等）统一使用 logging.getLogger("GameBot")，
        两个 handler 挂在同一 logger 上，Text 框可见的日志文件中同样可查。
        文件按日期追加写入 temp/gui_log_YYYYMMDD.log，避免 GUI 运行历史只存内存、
        进程结束即丢失。
        """
        logger = logging.getLogger("GameBot")
        logger.setLevel(logging.DEBUG)
        for h in logger.handlers[:]:
            if isinstance(h, QueueHandler):
                logger.removeHandler(h)
            elif isinstance(h, logging.FileHandler):
                try:
                    h.close()
                except Exception:
                    pass
                logger.removeHandler(h)
        handler = QueueHandler(self.log_queue)
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S"))
        logger.addHandler(handler)

        # 文件落盘（初始化失败不阻断 GUI 启动）
        try:
            log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(log_dir, f"gui_log_{time.strftime('%Y%m%d')}.log")
            file_handler = logging.FileHandler(log_path, encoding="utf-8", mode="a")
            file_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            logger.addHandler(file_handler)
        except Exception as e:
            print(f"[gui] 文件日志初始化失败（不影响运行）: {e!r}")

    # ── 布局 ────────────────────────────────────
    def _build_ui(self):
        main_pw = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_pw.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # 左侧面板
        left_frame = ttk.Frame(main_pw)
        main_pw.add(left_frame, weight=3)

        # 右侧面板 — 任务列表
        right_frame = ttk.Frame(main_pw)
        main_pw.add(right_frame, weight=2)

        self._build_left(left_frame)
        self._build_right(right_frame)

    def _build_left(self, parent: ttk.Frame):
        # ── 游戏选择 + 参数配置 ──
        cfg_frame = ttk.LabelFrame(parent, text="配置", padding=10)
        cfg_frame.pack(fill=tk.X, pady=(0, 8))

        # 游戏选择（"测试"= 代码/模板测试任务,不进游戏流程）
        ttk.Label(cfg_frame, text="游戏选择：").grid(row=0, column=0, sticky=tk.W, pady=4)
        self.game_var = tk.StringVar(value="sanguosha")
        game_options = ["sanguosha", "bbdc", "deltaforce", "测试", "重返未来1999", "无期迷途", "自定义"]
        self.game_combo = ttk.Combobox(
            cfg_frame, textvariable=self.game_var, values=game_options,
            state="readonly", width=22
        )
        self.game_combo.grid(row=0, column=1, sticky=tk.W, pady=4, padx=(8, 0))
        self.game_combo.bind("<<ComboboxSelected>>", self._on_game_changed)

        # 窗口标题
        ttk.Label(cfg_frame, text="窗口标题：").grid(row=1, column=0, sticky=tk.W, pady=4)
        self.window_var = tk.StringVar(value="MuMu")
        ttk.Entry(cfg_frame, textvariable=self.window_var, width=24).grid(
            row=1, column=1, sticky=tk.W, pady=4, padx=(8, 0)
        )

        # 匹配阈值固定 0.85：模板级阈值已在各任务/页面内按实际卡面收紧，
        # 全局滑块不再参与，避免用户误调后整批模板一起漂移。
        # 这里留出空位，后续放运行模式/窗口探测等全局选项。

        # ── 按钮区 ──
        btn_frame = ttk.Frame(cfg_frame)
        btn_frame.grid(row=2, column=0, columnspan=2, pady=(12, 0))
        self.start_btn = ttk.Button(btn_frame, text="开始执行", command=self._start, width=11)
        self.start_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_btn = ttk.Button(btn_frame, text="停止", command=self._stop, width=8, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=(0, 8))
        self.screenshot_btn = ttk.Button(btn_frame, text="截图预览", command=self._screenshot_preview, width=10)
        self.screenshot_btn.pack(side=tk.LEFT)

        # ── 重返未来1999 专用参数 / 状态 ──
        # 只有选择「重返未来1999」时才显示；其他游戏保持原来的通用界面。
        self.r1999_frame = ttk.LabelFrame(
            parent, text="重返未来1999 — 挂机参数", padding=8)
        self._build_r1999_panel(self.r1999_frame)

        # 截图预览
        self.preview_frame = ttk.LabelFrame(parent, text="截图预览", padding=6)
        self.preview_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 8))
        self.preview_label = ttk.Label(self.preview_frame, text="点击「截图预览」获取模拟器画面", anchor=tk.CENTER)
        self.preview_label.pack(fill=tk.BOTH, expand=True)

        # 日志区
        log_frame = ttk.LabelFrame(parent, text="运行日志", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_frame, wrap=tk.WORD, state=tk.DISABLED,
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white", relief=tk.FLAT, padx=6, pady=6
        )
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self._update_r1999_visibility()

    def _build_r1999_panel(self, parent):
        """r1999 专用配置与状态；参数直接映射到 Loop 的环境变量。"""
        self.r1999_rounds_var = tk.StringVar(value="5")
        self.r1999_score_var = tk.StringVar(value="0")
        self.r1999_mode_var = tk.StringVar(value="hybrid")
        self.r1999_goal_var = tk.StringVar(value="rounds")
        self.r1999_state_var = tk.StringVar(value="待机")
        self.r1999_progress_var = tk.StringVar(value="局 0/5 · 当前店 0")
        self.r1999_total_var = tk.StringVar(value="累计店 0 · 积分 -")
        self.r1999_reason_var = tk.StringVar(value="停止原因：-")

        parent.columnconfigure(1, weight=1)

        ttk.Label(parent, text="停止目标：").grid(
            row=0, column=0, sticky=tk.W, pady=3)
        goal_frame = ttk.Frame(parent)
        goal_frame.grid(row=0, column=1, columnspan=2,
                        sticky=tk.W, padx=(8, 0), pady=3)
        for text, value in (("按局数", "rounds"),
                            ("按积分", "score"),
                            ("两者任一", "either")):
            ttk.Radiobutton(
                goal_frame, text=text, value=value,
                variable=self.r1999_goal_var,
                command=self._update_r1999_goal_state,
            ).pack(anchor=tk.W, pady=1)

        ttk.Label(parent, text="局数上限：").grid(
            row=1, column=0, sticky=tk.W, pady=3)
        self.r1999_rounds_box = ttk.Spinbox(
            parent, from_=0, to=999, width=8,
            textvariable=self.r1999_rounds_var)
        self.r1999_rounds_box.grid(
            row=1, column=1, sticky=tk.W, padx=(8, 0), pady=3)
        ttk.Label(parent, text="按局数时需 >0", foreground="#888").grid(
            row=1, column=2, sticky=tk.W, padx=(8, 0), pady=3)

        ttk.Label(parent, text="积分目标：").grid(
            row=2, column=0, sticky=tk.W, pady=3)
        self.r1999_score_entry = ttk.Entry(
            parent, textvariable=self.r1999_score_var, width=10)
        self.r1999_score_entry.grid(
            row=2, column=1, sticky=tk.W, padx=(8, 0), pady=3)
        ttk.Label(parent, text="例如 2000", foreground="#888").grid(
            row=2, column=2, sticky=tk.W, padx=(8, 0), pady=3)

        ttk.Label(parent, text="无红区走位：").grid(
            row=3, column=0, sticky=tk.W, pady=3)
        mode_box = ttk.Combobox(
            parent, textvariable=self.r1999_mode_var,
            values=("hybrid", "strafe", "random"),
            state="readonly", width=12)
        mode_box.grid(row=3, column=1, sticky=tk.W, padx=(8, 0), pady=3)
        ttk.Label(parent, text="hybrid = 左右交替 + 周期冲刺",
                  foreground="#888").grid(
            row=3, column=2, sticky=tk.W, padx=(8, 0), pady=3)

        ttk.Separator(parent, orient=tk.HORIZONTAL).grid(
            row=4, column=0, columnspan=3, sticky=tk.EW, pady=(8, 6))

        ttk.Label(parent, text="运行状态：").grid(
            row=5, column=0, sticky=tk.W, pady=2)
        ttk.Label(parent, textvariable=self.r1999_state_var,
                  foreground="#2e7d32").grid(
            row=5, column=1, columnspan=2, sticky=tk.W, padx=(8, 0), pady=2)
        ttk.Label(parent, text="进度：").grid(
            row=6, column=0, sticky=tk.W, pady=2)
        ttk.Label(parent, textvariable=self.r1999_progress_var).grid(
            row=6, column=1, columnspan=2, sticky=tk.W, padx=(8, 0), pady=2)
        ttk.Label(parent, text="累计：").grid(
            row=7, column=0, sticky=tk.W, pady=2)
        ttk.Label(parent, textvariable=self.r1999_total_var).grid(
            row=7, column=1, columnspan=2, sticky=tk.W, padx=(8, 0), pady=2)
        ttk.Label(parent, textvariable=self.r1999_reason_var,
                  foreground="#888").grid(
            row=8, column=0, columnspan=3, sticky=tk.W, pady=2)

        ttk.Button(parent, text="保存参数", command=self._save_r1999_config,
                   width=10).grid(row=9, column=0, columnspan=3,
                                  sticky=tk.W, pady=(6, 0))
        self._update_r1999_goal_state()

    def _update_r1999_goal_state(self):
        """按局数/按积分时禁用无关输入框，避免两个目标同时生效造成误解。"""
        goal = self.r1999_goal_var.get()
        rounds_state = tk.NORMAL if goal in ("rounds", "either") else tk.DISABLED
        score_state = tk.NORMAL if goal in ("score", "either") else tk.DISABLED
        try:
            self.r1999_rounds_box.configure(state=rounds_state)
            self.r1999_score_entry.configure(state=score_state)
        except Exception:
            pass

    def _validate_r1999_goal(self):
        """返回配置错误文案；None 表示可启动。"""
        rounds, score, _, goal = self._r1999_values()
        if goal == "rounds" and rounds <= 0:
            return "「按局数」停止时，局数上限必须大于 0。"
        if goal == "score" and score <= 0:
            return "「按积分」停止时，积分目标必须大于 0。"
        if goal == "either" and rounds <= 0 and score <= 0:
            return "「两者任一」停止时，至少设置一个大于 0 的目标。"
        return None

    def _load_r1999_config(self):
        """读取 r1999 GUI 参数；文件不存在时使用默认值。"""
        defaults = {"rounds": 5, "score": 0, "mode": "hybrid", "goal": "rounds"}
        try:
            if os.path.exists(self.r1999_config_path):
                with open(self.r1999_config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for k, v in defaults.items():
                    if k in data:
                        defaults[k] = data[k]
        except Exception:
            pass
        self.r1999_rounds_var.set(str(defaults.get("rounds", 5)))
        self.r1999_score_var.set(str(defaults.get("score", 0)))
        mode = str(defaults.get("mode", "hybrid"))
        self.r1999_mode_var.set(mode if mode in ("hybrid", "strafe", "random") else "hybrid")
        goal = str(defaults.get("goal", "rounds"))
        self.r1999_goal_var.set(goal if goal in ("rounds", "score", "either") else "rounds")
        self._update_r1999_goal_state()

    def _save_r1999_config(self):
        """保存 r1999 GUI 参数；损坏/非法值回落到安全默认。"""
        rounds, score, mode, goal = self._r1999_values()
        try:
            with open(self.r1999_config_path, "w", encoding="utf-8") as f:
                json.dump({"rounds": rounds, "score": score,
                           "mode": mode, "goal": goal},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _r1999_values(self):
        try:
            rounds = max(0, int(self.r1999_rounds_var.get().strip() or "0"))
        except ValueError:
            rounds = 5
        try:
            score = max(0, int(self.r1999_score_var.get().strip() or "0"))
        except ValueError:
            score = 0
        mode = self.r1999_mode_var.get()
        if mode not in ("hybrid", "strafe", "random"):
            mode = "hybrid"
        goal = self.r1999_goal_var.get()
        if goal not in ("rounds", "score", "either"):
            goal = "rounds"
        return rounds, score, mode, goal

    def _apply_r1999_env(self):
        """在启动 Loop 前写入环境变量；按停止目标只传需要的阈值。"""
        rounds, score, mode, goal = self._r1999_values()
        if goal == "rounds":
            score = 0          # 按局数：明确关闭积分出口
        elif goal == "score":
            rounds = 0         # 按积分：明确关闭局数出口
        # either：两个阈值都传，Loop 先满足哪个就停
        os.environ["R1999_MAX_RUNS"] = str(rounds)
        os.environ["R1999_SCORE_TARGET"] = str(score)
        os.environ["R1999_NO_DANGER_MODE"] = mode
        return rounds, score, mode, goal

    def _reset_r1999_status(self):
        rounds, score, _, goal = self._r1999_values()
        max_text = "不限" if goal == "score" else (rounds or "不限")
        self.r1999_state_var.set("待机")
        self.r1999_progress_var.set(f"局 0/{max_text} · 当前店 0")
        self.r1999_total_var.set("累计店 0 · 积分 -")
        self.r1999_reason_var.set("停止原因：-")

    def _update_r1999_visibility(self):
        """只在选择「重返未来1999」时显示专用面板。"""
        if self.game_var.get() == "重返未来1999":
            if not self.r1999_frame.winfo_manager():
                self.r1999_frame.pack(fill=tk.X, pady=(0, 8),
                                      before=self.preview_frame)
        else:
            if self.r1999_frame.winfo_manager():
                self.r1999_frame.pack_forget()

    def _poll_r1999_status(self):
        while True:
            try:
                data = self.r1999_status_queue.get_nowait()
            except queue.Empty:
                break
            self._update_r1999_status(data)
        self.root.after(200, self._poll_r1999_status)

    def _on_r1999_status(self, data):
        """Loop 线程回调：只入队，由主线程统一更新 Tk 控件。"""
        self.r1999_status_queue.put(data)

    def _update_r1999_status(self, data):
        state = data.get("state", "")
        state_text = {
            "running": "运行中",
            "stopped": "已停止",
        }.get(state, state or "待机")
        self.r1999_state_var.set(state_text)
        runs = int(data.get("runs", 0) or 0)
        max_runs = int(data.get("max_runs", 0) or 0)
        current = int(data.get("current_shops", 0) or 0)
        total = int(data.get("total_shops", 0) or 0)
        score = data.get("score")
        score_text = "-" if score is None else str(score)
        self.r1999_progress_var.set(
            f"局 {runs}/{max_runs or '不限'} · 当前店 {current}")
        self.r1999_total_var.set(f"累计店 {total} · 积分 {score_text}")
        reason = data.get("stop_reason") or "-"
        reason_text = {
            "max_runs": "达到局数上限",
            "score_target": "达到积分目标",
            "space": "空格 / GUI 停止",
            "danger": "危险页面终止",
            "escape": "异常脱出",
            "max_runtime": "运行超时",
        }.get(reason, reason)
        self.r1999_reason_var.set(f"停止原因：{reason_text}")

    def _build_right(self, parent: ttk.Frame):
        """右侧 — 任务列表面板。"""
        task_label_frame = ttk.LabelFrame(parent, text="任务列表", padding=6)
        task_label_frame.pack(fill=tk.BOTH, expand=True)

        # 顶部操作栏
        toolbar = ttk.Frame(task_label_frame)
        toolbar.pack(fill=tk.X, pady=(0, 6))

        self.task_count_label = ttk.Label(toolbar, text="共 0 个任务")
        self.task_count_label.pack(side=tk.LEFT)

        ttk.Button(toolbar, text="全部重置", command=self._reset_all_tasks, width=9).pack(side=tk.RIGHT, padx=(4, 0))
        ttk.Button(toolbar, text="全部执行", command=self._start, width=9).pack(side=tk.RIGHT)

        # Canvas + Scrollbar 实现可滚动的任务列表
        self.task_canvas = tk.Canvas(task_label_frame, borderwidth=0, highlightthickness=0)
        task_scroll = ttk.Scrollbar(task_label_frame, orient=tk.VERTICAL, command=self.task_canvas.yview)
        self.task_inner = ttk.Frame(self.task_canvas)

        self.task_inner.bind("<Configure>", lambda e: self.task_canvas.configure(
            scrollregion=self.task_canvas.bbox("all"))
        )
        self.task_canvas_window = self.task_canvas.create_window(
            (0, 0), window=self.task_inner, anchor=tk.NW
        )
        self.task_canvas.configure(yscrollcommand=task_scroll.set)

        # 让内层 frame 宽度跟随 canvas
        self.task_canvas.bind("<Configure>", self._on_canvas_resize)

        self.task_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        task_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 鼠标滚轮
        def _on_mousewheel(event):
            self.task_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        self.task_canvas.bind("<Enter>", lambda e: self.task_canvas.bind_all("<MouseWheel>", _on_mousewheel))
        self.task_canvas.bind("<Leave>", lambda e: self.task_canvas.unbind_all("<MouseWheel>"))

    def _on_canvas_resize(self, event):
        self.task_canvas.itemconfig(self.task_canvas_window, width=event.width)

    # ── 任务列表加载 ─────────────────────────────
    @property
    def _task_order_file(self):
        """任务排序配置文件路径。"""
        return os.path.join(os.path.dirname(__file__), "task_order.json")

    def _load_task_config(self, game: str):
        """
        读取任务配置，返回 (order_map, settings_map)。

        兼容两种格式：
        - 旧版: {"game": ["任务A", "任务B"]}（仅顺序）
        - 新版: {"game": {"order": [...], "settings": {"任务A": {...}}}}
          settings 每任务可含 enabled/threshold/repeat/cooldown
        """
        try:
            if os.path.exists(self._task_order_file):
                with open(self._task_order_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                entry = data.get(game)
                if isinstance(entry, dict):
                    order = entry.get("order", []) or []
                    settings = entry.get("settings", {}) or {}
                elif isinstance(entry, list):
                    order, settings = entry, {}
                else:
                    order, settings = [], {}
                return {n: i for i, n in enumerate(order)}, settings
        except Exception:
            pass
        return {}, {}

    def _save_task_config(self, game: str):
        """
        保存任务顺序与每个任务的设置（enabled/threshold/repeat/cooldown）。

        勾选状态、⚙ 弹窗修改、排序变化都会触发保存，重启 GUI 不再重置
        （2026-08-30 用户反馈：划掉的任务下次运行被重置）。
        """
        order_list = [task.name for task in self.tasks]
        settings = {
            task.name: {
                "enabled": bool(task.enabled),
                "threshold": float(task.threshold),
                "repeat": bool(task.repeat),
                "cooldown": float(task.cooldown),
            }
            for task in self.tasks
        }
        data = {}
        try:
            if os.path.exists(self._task_order_file):
                with open(self._task_order_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data[game] = {"order": order_list, "settings": settings}
            with open(self._task_order_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _apply_task_settings(self, settings_map: dict):
        """把保存的设置套用到当前任务对象上（未保存过的任务保持默认值）。"""
        for task in self.tasks:
            cfg = settings_map.get(task.name)
            if not cfg:
                continue
            task.enabled = bool(cfg.get("enabled", task.enabled))
            try:
                task.threshold = float(cfg.get("threshold", task.threshold))
            except (TypeError, ValueError):
                pass
            task.repeat = bool(cfg.get("repeat", task.repeat))
            try:
                task.cooldown = float(cfg.get("cooldown", task.cooldown))
            except (TypeError, ValueError):
                pass

    def _reload_tasks(self):
        """根据当前游戏加载任务列表。"""
        # 清空旧组件
        for w in self.task_widgets:
            for c in w.values():
                if hasattr(c, "destroy"):
                    c.destroy()
        self.task_widgets.clear()
        self.task_vars.clear()

        game = self.game_var.get()
        if _HAS_TASK_SYSTEM and game in GAME_REGISTRY:
            self.tasks = GAME_REGISTRY[game].get_tasks()
            # 应用保存的顺序与设置（勾选状态/阈值/冷却，重启不重置）
            order_map, settings_map = self._load_task_config(game)
            if order_map:
                # 按保存顺序排序，未记录的任务追加到末尾
                self.tasks.sort(key=lambda t: order_map.get(t.name, 9999))
            self._apply_task_settings(settings_map)
        else:
            self.tasks = []

        if not self.tasks:
            placeholder = ttk.Label(self.task_inner, text="（当前游戏无已定义任务）", foreground="gray")
            placeholder.pack(pady=20)
            self.task_count_label.configure(text="共 0 个任务")
            return

        self.task_count_label.configure(text=f"共 {len(self.tasks)} 个任务")

        for i, task in enumerate(self.tasks):
            self._add_task_row(task)

    def _add_task_row(self, task):
        """添加一个任务行到滚动区域。"""
        row_frame = ttk.Frame(self.task_inner)
        row_frame.pack(fill=tk.X, pady=2, padx=2)

        # ↑ ↓ 排序按钮
        btn_up = ttk.Button(row_frame, text="↑", width=3,
                            command=lambda t=task: self._move_task_up(t))
        btn_up.pack(side=tk.LEFT, padx=(2, 0))
        btn_down = ttk.Button(row_frame, text="↓", width=3,
                              command=lambda t=task: self._move_task_down(t))
        btn_down.pack(side=tk.LEFT, padx=(0, 4))

        # 完成状态复选框
        var = tk.BooleanVar(value=task.enabled)
        self.task_vars.append(var)
        cb = ttk.Checkbutton(
            row_frame, variable=var,
            command=lambda t=task, v=var: self._on_task_toggle(t, v)
        )
        cb.pack(side=tk.LEFT, padx=(4, 6))

        # 任务名称
        name_label = ttk.Label(row_frame, text=task.name, font=("Microsoft YaHei", 10))
        name_label.pack(side=tk.LEFT)

        # 模板名（灰色小字）
        tpl_label = ttk.Label(
            row_frame, text=f"({task.template})",
            font=("Consolas", 8), foreground="#888"
        )
        tpl_label.pack(side=tk.LEFT, padx=(6, 8))

        # 重复标记
        if task.repeat:
            repeat_badge = ttk.Label(
                row_frame, text="🔄 重复",
                style="Repeat.TLabel"
            )
            repeat_badge.pack(side=tk.LEFT, padx=(0, 8))

        # 阈值标记（如果不是默认 0.85）
        if task.threshold != 0.85:
            thr_label = ttk.Label(
                row_frame, text=f"[阈{task.threshold}]",
                font=("Consolas", 8), foreground="#aaa"
            )
            thr_label.pack(side=tk.LEFT, padx=(0, 8))

        # 冷却标记
        if task.cooldown > 0:
            cd_label = ttk.Label(
                row_frame, text=f"[冷却{int(task.cooldown)}s]",
                font=("Consolas", 8), foreground="#888"
            )
            cd_label.pack(side=tk.LEFT, padx=(0, 8))

        # 单独执行按钮
        exec_btn = ttk.Button(
            row_frame, text="执行",
            command=lambda t=task: self._execute_single_task(t),
            width=5, style="Task.TButton"
        )
        exec_btn.pack(side=tk.RIGHT, padx=(4, 2))

        # 设置按钮
        settings_btn = ttk.Button(
            row_frame, text="⚙",
            command=lambda t=task: self._edit_task(t),
            width=3, style="Settings.TButton"
        )
        settings_btn.pack(side=tk.RIGHT, padx=(0, 0))

        # 存储组件引用
        self.task_widgets.append({
            "frame": row_frame,
            "check": cb,
            "var": var,
            "name": name_label,
            "exec_btn": exec_btn,
        })

    def _on_task_toggle(self, task, var):
        """任务复选框状态变更 — 勾选=执行，不勾选=跳过。立即持久化。"""
        task.enabled = var.get()
        self._save_task_config(self.game_var.get())

    def _reset_all_tasks(self):
        """重置所有任务：全部勾选（enabled=True），done 归零。"""
        for task in self.tasks:
            task.enabled = True
            task.done = False
        for var in self.task_vars:
            var.set(True)

    def _on_game_changed(self, event=None):
        game = self.game_var.get()
        # deltaforce：窗口标题自动填游戏窗口名（预览用）；运行时脚本自管双窗口
        if game == "deltaforce":
            self.window_var.set(DF_WINDOW)
        # r1999 必须锚 MuMu 主窗，不能填通用 MuMu 或渲染窗
        if game == "重返未来1999":
            self.window_var.set(R1999_WINDOW)
        self._update_r1999_visibility()
        self._reload_tasks()

    def _move_task_up(self, task):
        """将任务上移一位。"""
        idx = self.tasks.index(task) if task in self.tasks else -1
        if idx <= 0:
            return
        self.tasks[idx], self.tasks[idx - 1] = self.tasks[idx - 1], self.tasks[idx]
        self._rebuild_task_list()
        self._save_task_config(self.game_var.get())

    def _move_task_down(self, task):
        """将任务下移一位。"""
        idx = self.tasks.index(task) if task in self.tasks else -1
        if idx < 0 or idx >= len(self.tasks) - 1:
            return
        self.tasks[idx], self.tasks[idx + 1] = self.tasks[idx + 1], self.tasks[idx]
        self._rebuild_task_list()
        self._save_task_config(self.game_var.get())

    def _rebuild_task_list(self):
        """重建任务列表 UI（保留数据状态）。"""
        for w in self.task_widgets:
            for c in w.values():
                if hasattr(c, "destroy"):
                    c.destroy()
        self.task_widgets.clear()
        self.task_vars.clear()
        for task in self.tasks:
            self._add_task_row(task)
        self._sync_vars_from_tasks()
        self.task_count_label.configure(text=f"共 {len(self.tasks)} 个任务")

    # ── 日志轮询 ─────────────────────────────────
    def _poll_log_queue(self):
        while True:
            try:
                msg = self.log_queue.get_nowait()
                self._append_log(msg)
            except queue.Empty:
                break
        self.root.after(150, self._poll_log_queue)

    def _append_log(self, msg: str):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # ── 开始执行 ─────────────────────────────────
    def _start(self):
        if self.running:
            messagebox.showwarning("提示", "脚本正在运行中")
            return

        game = self.game_var.get()
        window_title = self.window_var.get().strip()

        if not window_title:
            messagebox.showwarning("提示", "请输入窗口标题")
            return

        if game == "重返未来1999":
            error = self._validate_r1999_goal()
            if error:
                messagebox.showwarning("提示", error)
                return
            self._save_r1999_config()
            self._reset_r1999_status()
            self.r1999_state_var.set("启动中")

        self.running = True
        self.stop_flag.clear()
        reset_stop()  # 清除上次运行残留的全局停止标志
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

        # 重置一次性任务状态
        for task in self.tasks:
            if not task.repeat:
                task.done = False
        self._sync_vars_from_tasks()

        self.worker_thread = threading.Thread(
            target=self._run_script, args=(game, window_title), daemon=True
        )
        self.worker_thread.start()

    def _inject_interruptible_sleep(self, bot):
        """用可中断 sleep 替换 bot.sleep（GUI 停止 / 空格键可随时中断），返回原函数。"""
        original_sleep = bot.sleep

        def _sleep_with_stop(seconds: float):
            interval = 0.1
            elapsed = 0.0
            while elapsed < seconds:
                if self.stop_flag.is_set():
                    raise StopRequested("用户中断")
                Action.check_stop()  # 空格键 / GUI 停止共用标志
                time.sleep(min(interval, seconds - elapsed))
                elapsed += interval

        bot.sleep = _sleep_with_stop
        return original_sleep

    def _run_script(self, game: str, window_title: str):
        logger = logging.getLogger("GameBot")
        original_sleep = None
        register_task_thread()
        try:
            logger.info(f"初始化 GameBot: window='{window_title}', game={game}")

            # bbdc 必须锚定 MuMu 主窗口（NxDevice 渲染窗口矩形会与内容错位）
            if game == "bbdc" and window_title == "MuMu":
                window_title = "MuMu安卓设备"
            # deltaforce 自管 WeGame/游戏双窗口（游戏窗口可能未开，惰性绑定），
            # GameBot 仅作占位 → 无论输入框填什么都截主屏，避免初始化失败
            if game == "deltaforce":
                window_title = None
            # r1999(重返未来1999)：Loop 自锚 MuMu 主窗，GameBot 仅作占位；
            # 模板子目录名是 r1999 而非中文标签
            if game == "重返未来1999":
                if window_title == "MuMu":
                    window_title = R1999_WINDOW
                self._apply_r1999_env()
            game_name = None if game in ("自定义", "测试") else \
                {"重返未来1999": "r1999"}.get(game, game)
            self.bot = GameBot(
                window_title=window_title,
                game_name=game_name,
                match_threshold=DEFAULT_MATCH_THRESHOLD,
            )

            # 注入可中断 sleep
            original_sleep = self._inject_interruptible_sleep(self.bot)

            # 执行任务
            if _HAS_TASK_SYSTEM and self.tasks and game in GAME_REGISTRY:
                script = GAME_REGISTRY[game]
                if game == "重返未来1999":
                    script.status_cb = self._on_r1999_status
                # 用 run_game 以包含启动清理，传入用户修改过的 tasks
                run_game(self.bot, script, tasks=self.tasks, stop_flag=self.stop_flag)
            elif _HAS_TASK_SYSTEM and self.tasks:
                # 旧版兼容：无 GameScript 时只跑任务循环
                def _legacy_after(b):
                    hook = getattr(GAME_REGISTRY.get(game), "after_task_hook", None)
                    if hook:
                        hook(b)
                    ens = getattr(GAME_REGISTRY.get(game), "_ensure_main_screen", None)
                    if ens:
                        ens(b)
                result = execute_tasks(
                    self.bot, self.tasks, stop_flag=self.stop_flag,
                    after_task=_legacy_after,
                )
                logger.info(f"任务完成: {result}")
            else:
                self._run_builtin_example(self.bot)

        except StopRequested:
            ack_stop()
            logger.info("脚本已被用户停止")
        except InterruptedError:
            ack_stop()
            logger.info("脚本已被用户中断")
        except Exception as e:
            logger.error(f"执行出错: {e}")
        finally:
            if self.bot and original_sleep:
                self.bot.sleep = original_sleep
            task_finished()
            self._on_script_done()

    def _run_builtin_example(self, bot: GameBot):
        logger = logging.getLogger("GameBot")
        logger.info("=" * 40)
        logger.info("内置示例流程开始")
        logger.info("=" * 40)

        bot.sleep(2)
        logger.info("[1] 模板匹配：查找并点击按钮")
        bot.click_if_found("start_button.png")
        logger.info("[2] OCR 识别")
        texts = bot.recognize_text()
        for item in texts[:10]:
            logger.info(f"   OCR: '{item['text']}' @ {item['center']}")
        logger.info("[3] OCR 查找并点击 '领取'")
        bot.find_and_click_text("领取", fuzzy=True)

        logger.info("=" * 40)
        logger.info("内置示例流程完成")
        logger.info("=" * 40)

    def _on_script_done(self):
        self.running = False
        self.bot = None
        self.root.after(0, self._update_ui_after_run)

    def _update_ui_after_run(self):
        self.start_btn.configure(state=tk.NORMAL)
        self.stop_btn.configure(state=tk.DISABLED)
        if self.game_var.get() == "重返未来1999":
            self.r1999_state_var.set("已停止")
        self._sync_vars_from_tasks()

    def _sync_vars_from_tasks(self):
        """同步任务 enabled 状态到复选框。"""
        for i, task in enumerate(self.tasks):
            if i < len(self.task_vars):
                self.task_vars[i].set(task.enabled)

    # ── 停止 ─────────────────────────────────────
    def _stop(self):
        if not self.running:
            return
        self.stop_flag.set()
        request_stop()  # 同步触发截图/连点等深度中断
        self._append_log("[用户操作] 正在停止脚本...")
        if self.game_var.get() == "重返未来1999":
            self.r1999_state_var.set("停止中")

    # ── 任务设置对话框 ───────────────────────────
    def _edit_task(self, task):
        """弹出对话框编辑任务参数：阈值、重复、冷却。"""
        dialog = tk.Toplevel(self.root)
        dialog.title(f"任务设置 — {task.name}")
        dialog.geometry("340x250")
        dialog.resizable(False, False)
        dialog.transient(self.root)
        dialog.grab_set()

        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        # ── 阈值 ──
        ttk.Label(frame, text="匹配阈值：").grid(row=0, column=0, sticky=tk.W, pady=(0, 2))
        thr_var = tk.DoubleVar(value=task.threshold)
        thr_scale = ttk.Scale(frame, from_=0.50, to=1.0, variable=thr_var, length=200)
        thr_scale.grid(row=1, column=0, columnspan=2, sticky=tk.W)
        thr_label = ttk.Label(frame, text=f"{task.threshold:.2f}", width=5)
        thr_label.grid(row=1, column=2, sticky=tk.W, padx=(8, 0))
        thr_scale.configure(command=lambda v: thr_label.configure(text=f"{float(v):.2f}"))

        # ── 重复触发 ──
        ttk.Label(frame, text="重复触发：").grid(row=2, column=0, sticky=tk.W, pady=(12, 2))
        repeat_var = tk.BooleanVar(value=task.repeat)
        ttk.Checkbutton(frame, variable=repeat_var, text="启用（循环检测）").grid(
            row=2, column=1, sticky=tk.W, padx=(12, 0), pady=(12, 2)
        )

        # ── 冷却时间 ──
        ttk.Label(frame, text="冷却时间(秒)：").grid(row=3, column=0, sticky=tk.W, pady=(8, 2))
        cd_var = tk.StringVar(value=str(int(task.cooldown)) if task.cooldown > 0 else "0")
        cd_entry = ttk.Entry(frame, textvariable=cd_var, width=10)
        cd_entry.grid(row=3, column=1, sticky=tk.W, padx=(4, 0), pady=(8, 2))
        cd_hint = ttk.Label(frame, text="（重复任务才生效，0 = 无冷却）", font=("Microsoft YaHei", 8), foreground="#888")
        cd_hint.grid(row=4, column=0, columnspan=3, sticky=tk.W, pady=(0, 8))

        def _on_save():
            try:
                task.threshold = thr_var.get()
            except Exception:
                pass
            task.repeat = repeat_var.get()
            try:
                task.cooldown = max(0, float(cd_var.get()))
            except ValueError:
                task.cooldown = 0
            dialog.destroy()
            self._rebuild_task_list()
            self._save_task_config(self.game_var.get())

        def _on_cancel():
            dialog.destroy()

        btn_frame = ttk.Frame(frame)
        btn_frame.grid(row=5, column=0, columnspan=3, pady=(12, 0))
        ttk.Button(btn_frame, text="保存", command=_on_save, width=10).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_frame, text="取消", command=_on_cancel, width=10).pack(side=tk.LEFT)

    # ── 单任务执行 ───────────────────────────────
    def _execute_single_task(self, task):
        """手动执行单个任务（后台线程运行，GUI 停止按钮实时可用）。"""
        window_title = self.window_var.get().strip()
        if not window_title:
            messagebox.showwarning("提示", "请输入窗口标题")
            return
        if self.running:
            messagebox.showwarning("提示", "脚本正在运行中")
            return
        if self.game_var.get() == "重返未来1999":
            error = self._validate_r1999_goal()
            if error:
                messagebox.showwarning("提示", error)
                return

        self.running = True
        self.stop_flag.clear()
        reset_stop()
        self.start_btn.configure(state=tk.DISABLED)
        self.stop_btn.configure(state=tk.NORMAL)
        if self.game_var.get() == "重返未来1999":
            self._save_r1999_config()
            self._reset_r1999_status()
            self.r1999_state_var.set("启动中")

        def runner():
            logger = logging.getLogger("GameBot")
            original_sleep = None
            game = self.game_var.get()
            if game == "重返未来1999":
                self._apply_r1999_env()
                if _HAS_TASK_SYSTEM:
                    script = GAME_REGISTRY.get(game)
                    if script is not None:
                        script.status_cb = self._on_r1999_status
            try:
                if self.bot is None:
                    # bbdc 必须锚定 MuMu 主窗口（NxDevice 渲染窗口矩形会错位）
                    wt = window_title
                    if game == "bbdc" and wt == "MuMu":
                        wt = "MuMu安卓设备"
                    if game == "deltaforce":
                        wt = None  # deltaforce 自管双窗口，GameBot 占位即可
                    if game == "重返未来1999" and wt == "MuMu":
                        wt = R1999_WINDOW
                    game_name = None if game in ("自定义", "测试") else \
                        {"重返未来1999": "r1999"}.get(game, game)
                    self.bot = GameBot(
                        window_title=wt,
                        game_name=game_name,
                        match_threshold=DEFAULT_MATCH_THRESHOLD,
                    )

                original_sleep = self._inject_interruptible_sleep(self.bot)

                # 复合流程（callback）任务
                if task.callback is not None:
                    logger.info(f"[手动] 执行复合任务: {task.name}")
                    success = task.callback(self.bot)
                    if success:
                        logger.info(f"  ✓ {task.name} 完成")
                        task.done = True
                    else:
                        logger.error(f"  ✗ {task.name} 执行失败")
                    return

                # 模板匹配任务
                logger.info(f"[手动] 执行任务: {task.name} ({task.template})")
                if self.bot.click_if_found(task.template, threshold=task.threshold):
                    logger.info(f"  ✓ {task.name} 点击成功")
                    task.done = True
                else:
                    logger.info(f"  ✗ {task.name} 未找到模板")
                    task.done = False
            except StopRequested:
                logger.info("[手动] 任务已被停止")
            except Exception as e:
                logger.error(f"[手动] 执行出错: {e}")
                self.root.after(0, lambda e=e: messagebox.showerror("执行失败", str(e)))
            finally:
                if self.bot and original_sleep:
                    self.bot.sleep = original_sleep
                self._on_script_done()

        threading.Thread(target=runner, daemon=True).start()

    # ── 截图预览 ─────────────────────────────────
    def _screenshot_preview(self):
        window_title = self.window_var.get().strip()
        if self.game_var.get() == "deltaforce":
            window_title = DF_WINDOW  # deltaforce 预览游戏窗口
        if not window_title:
            messagebox.showwarning("提示", "请先输入窗口标题")
            return

        try:
            from core.screenshot import Screenshot, find_window_rect
            from PIL import Image, ImageTk
            import ctypes

            rect, target_hwnd = find_window_rect(window_title)
            if rect is None:
                raise RuntimeError(f"未找到包含 '{window_title}' 的窗口")

            user32 = ctypes.windll.user32
            user32.ShowWindow(target_hwnd, 9)
            user32.SetForegroundWindow(target_hwnd)
            time.sleep(0.3)

            ss = Screenshot(window_title=window_title)
            img = ss.capture_pil()

            max_w, max_h = 540, 360
            w, h = img.size
            ratio = min(max_w / w, max_h / h, 1.0)
            new_size = (int(w * ratio), int(h * ratio))
            img_resized = img.resize(new_size, Image.LANCZOS)

            self.screenshot_img = ImageTk.PhotoImage(img_resized)
            self.preview_label.configure(image=self.screenshot_img, text="")

        except Exception as e:
            messagebox.showerror("截图失败", f"无法截取窗口画面:\n{e}")

    # ── 关闭窗口 ─────────────────────────────────
    def _on_close(self):
        if self.running:
            self.stop_flag.set()
            request_stop()
            if self.worker_thread and self.worker_thread.is_alive():
                self.worker_thread.join(timeout=2)
        self.root.destroy()


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────
if __name__ == "__main__":
    from core.instance_lock import check_my_lock, release
    check_my_lock()  # 单实例锁：防止两个 bot 同时操作模拟器（run27 教训）
    try:
        root = tk.Tk()
        app = GameBotGUI(root)
        root.mainloop()
    finally:
        release()
