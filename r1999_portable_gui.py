"""
r1999 便携版 GUI。

与开发版 gui.py 的差别：
- 只加载 r1999 一个游戏，不 import main.py / 其他游戏，避免误连另一套 MuMu；
- 配置、日志、temp、截图都走 core.runtime，不写项目目录；
- 打包后 exe 双击即用，MuMu 未启动时只给检查提示，不误操作。
"""

from __future__ import annotations

import logging
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Optional

from core import runtime
from core.action import request_stop, reset_stop
from core.instance_lock import LOCK_PATH, check_my_lock, release
from core.screenshot import find_window_rect

from games.r1999 import r1999 as engine

APP_TITLE = "r1999 街机挂机（便携版）"
STATE_TEXT = {
    "running": "运行中",
    "stopped": "已停止",
}

MODE_TEXT = {"hybrid": "混合（推荐）", "strafe": "左右交叉", "random": "随机"}


class _QueueLogHandler(logging.Handler):
    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q
        self.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))

    def emit(self, record):
        try:
            self.q.put(self.format(record))
        except Exception:
            pass


class PortableGui:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("980x760")
        self.root.minsize(900, 680)

        self.running = False
        self.loop = None
        self.worker: Optional[threading.Thread] = None
        self.stop_requested = False
        self.status_queue: queue.Queue = queue.Queue()
        self.log_queue: queue.Queue = queue.Queue()
        self.done_queue: queue.Queue = queue.Queue()
        self.preview_img = None

        runtime.ensure_runtime_dirs()
        runtime.write_default_settings()
        self._install_log_queue()
        self._build_ui()
        self._load_config_to_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._poll()

    # ── 日志入队 ─────────────────────────────────
    def _install_log_queue(self):
        handler = _QueueLogHandler(self.log_queue)
        for name in ("r1999loop", "core.action", "core.screenshot"):
            lg = logging.getLogger(name)
            if not any(isinstance(h, _QueueLogHandler) for h in lg.handlers):
                lg.addHandler(handler)
                if lg.level > logging.INFO:
                    lg.setLevel(logging.INFO)
        self.logger = logging.getLogger("r1999loop")

    # ── UI ───────────────────────────────────────
    def _build_ui(self):
        self.root.configure(bg="#f3f4f6")
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Sub.TLabel", foreground="#667085")
        style.configure("Big.TLabel", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("Start.TButton", font=("Microsoft YaHei UI", 12, "bold"), padding=(18, 8))
        style.configure("Stop.TButton", font=("Microsoft YaHei UI", 12, "bold"), padding=(18, 8))

        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="P12.6 正式版 · 仅操作 r1999 专用 MuMu · 空格急停",
            style="Sub.TLabel",
        ).pack(anchor="w", pady=(2, 10))

        # 状态卡
        status = ttk.LabelFrame(outer, text="运行状态", padding=10)
        status.pack(fill="x", pady=(0, 10))
        self.state_var = tk.StringVar(value="待机")
        self.runs_var = tk.StringVar(value="0 / 5")
        self.shops_var = tk.StringVar(value="当前 0 · 累计 0")
        self.score_var = tk.StringVar(value="0")
        self.reason_var = tk.StringVar(value="-")
        for i, (label, var) in enumerate((
            ("状态", self.state_var),
            ("局数", self.runs_var),
            ("商店", self.shops_var),
            ("积分", self.score_var),
            ("停止原因", self.reason_var),
        )):
            ttk.Label(status, text=label + "：").grid(row=0, column=i * 2, sticky="w", padx=(0 if i == 0 else 16, 2))
            ttk.Label(status, textvariable=var, style="Big.TLabel").grid(row=0, column=i * 2 + 1, sticky="w")

        # 参数
        param = ttk.LabelFrame(outer, text="本次目标（至少填一个；另一个填 0 表示不限）", padding=10)
        param.pack(fill="x", pady=(0, 10))
        self.rounds_var = tk.StringVar(value="5")
        self.score_target_var = tk.StringVar(value="0")
        self.mode_var = tk.StringVar(value="hybrid")
        ttk.Label(param, text="局数上限：").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(param, from_=0, to=9999, width=8, textvariable=self.rounds_var).grid(row=0, column=1, sticky="w", padx=(0, 16))
        ttk.Label(param, text="积分目标：").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(param, from_=0, to=999999, width=10, textvariable=self.score_target_var).grid(row=0, column=3, sticky="w", padx=(0, 16))
        ttk.Label(param, text="无红区模式：").grid(row=0, column=4, sticky="w")
        mode_box = ttk.Combobox(param, state="readonly", width=16, textvariable=self.mode_var,
                                values=["hybrid", "strafe", "random"])
        mode_box.grid(row=0, column=5, sticky="w")
        ttk.Label(param, text="hybrid=混合；strafe=左右交叉；random=随机", style="Sub.TLabel").grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(6, 0))

        # 环境
        env = ttk.LabelFrame(outer, text="环境（MuMu 未启动时可先自检；一般无需改）", padding=10)
        env.pack(fill="x", pady=(0, 10))
        self.window_var = tk.StringVar(value=engine.WINDOW)
        self.render_var = tk.StringVar(value=engine.RENDER_WINDOW)
        self.adb_var = tk.StringVar(value=engine.ADB)
        self.serial_var = tk.StringVar(value=engine.ADB_SERIAL)
        ttk.Label(env, text="MuMu 主窗：").grid(row=0, column=0, sticky="w")
        ttk.Entry(env, width=22, textvariable=self.window_var).grid(row=0, column=1, sticky="w", padx=(0, 12))
        ttk.Label(env, text="渲染窗：").grid(row=0, column=2, sticky="w")
        ttk.Entry(env, width=18, textvariable=self.render_var).grid(row=0, column=3, sticky="w", padx=(0, 12))
        ttk.Label(env, text="ADB serial：").grid(row=0, column=4, sticky="w")
        ttk.Entry(env, width=18, textvariable=self.serial_var).grid(row=0, column=5, sticky="w")
        ttk.Label(env, text="ADB：").grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(env, width=70, textvariable=self.adb_var).grid(row=1, column=1, columnspan=5, sticky="we", pady=(6, 0))
        ttk.Label(env, text=f"模型：{engine.MODEL_DIR}   数据：{runtime.data_home()}",
                  style="Sub.TLabel").grid(row=2, column=0, columnspan=6, sticky="w", pady=(6, 0))
        env.columnconfigure(5, weight=1)

        # 按钮
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(0, 10))
        self.start_btn = ttk.Button(buttons, text="开始挂机", style="Start.TButton", command=self._start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(buttons, text="停止", style="Stop.TButton", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", padx=(10, 0))
        ttk.Button(buttons, text="环境自检", command=self._self_check).pack(side="left", padx=(10, 0))
        ttk.Button(buttons, text="截图预览", command=self._preview).pack(side="left", padx=(10, 0))
        ttk.Button(buttons, text="打开数据目录", command=self._open_data).pack(side="left", padx=(10, 0))
        ttk.Button(buttons, text="清空日志", command=self._clear_log).pack(side="left", padx=(10, 0))

        # 主体：日志 + 预览
        body = ttk.Frame(outer)
        body.pack(fill="both", expand=True)
        log_frame = ttk.LabelFrame(body, text="运行日志", padding=6)
        log_frame.pack(side="left", fill="both", expand=True)
        self.log_text = tk.Text(log_frame, wrap="word", height=20, font=("Consolas", 9),
                                bg="#111827", fg="#e5e7eb", insertbackground="#e5e7eb")
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scroll.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scroll.set)

        preview_frame = ttk.LabelFrame(body, text="截图预览", padding=6)
        preview_frame.pack(side="right", fill="y", padx=(10, 0))
        self.preview_label = ttk.Label(preview_frame, text="点击「截图预览」\n查看 MuMu 当前画面",
                                       anchor="center", width=42)
        self.preview_label.pack(fill="both", expand=True)
        self._append_log(f"[便携版] 数据目录: {runtime.data_home()}")
        self._append_log(f"[便携版] 实例锁: {LOCK_PATH}")

    # ── 配置 ─────────────────────────────────────
    def _load_config_to_ui(self):
        cfg = runtime.load_settings()
        rcfg = cfg.get("r1999", {}) if isinstance(cfg.get("r1999"), dict) else {}
        self.rounds_var.set(str(rcfg.get("rounds", 5)))
        self.score_target_var.set(str(rcfg.get("score", 0)))
        mode = str(rcfg.get("mode", "hybrid")).lower()
        self.mode_var.set(mode if mode in MODE_TEXT else "hybrid")
        wcfg = cfg.get("window", {}) if isinstance(cfg.get("window"), dict) else {}
        dcfg = cfg.get("device", {}) if isinstance(cfg.get("device"), dict) else {}
        self.window_var.set(str(wcfg.get("main_title") or engine.WINDOW))
        self.render_var.set(str(wcfg.get("render_title") or engine.RENDER_WINDOW))
        adb = str(dcfg.get("adb_path") or "auto")
        serial = str(dcfg.get("adb_serial") or "auto")
        self.adb_var.set(str(runtime.default_adb_path()) if adb == "auto" else adb)
        self.serial_var.set(os.environ.get("R1999_ADB_SERIAL")
                            or (runtime.default_adb_serial() if serial == "auto" else serial))

    def _save_config(self, rounds: int, score: int, mode: str):
        cfg = runtime.load_settings()
        if not isinstance(cfg, dict):
            cfg = {}
        cfg.setdefault("window", {})
        cfg.setdefault("device", {})
        cfg.setdefault("r1999", {})
        cfg["window"]["main_title"] = self.window_var.get().strip() or engine.WINDOW
        cfg["window"]["render_title"] = self.render_var.get().strip() or engine.RENDER_WINDOW
        adb_text = self.adb_var.get().strip()
        cfg["device"]["adb_path"] = "auto" if not adb_text or adb_text == str(runtime.default_adb_path()) else adb_text
        serial_text = self.serial_var.get().strip()
        cfg["device"]["adb_serial"] = "auto" if not serial_text or serial_text == runtime.default_adb_serial() else serial_text
        cfg["r1999"]["rounds"] = rounds
        cfg["r1999"]["score"] = score
        cfg["r1999"]["mode"] = mode
        runtime.settings_path().write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")

    # ── 开始/停止 ─────────────────────────────────
    def _parse_int(self, var: tk.StringVar, name: str, default: int = 0) -> int:
        try:
            value = int(str(var.get()).strip())
        except ValueError:
            raise ValueError(f"{name}必须是整数")
        if value < 0:
            raise ValueError(f"{name}不能为负数")
        return value

    def _start(self):
        if self.running:
            return
        try:
            rounds = self._parse_int(self.rounds_var, "局数上限")
            score = self._parse_int(self.score_target_var, "积分目标")
        except ValueError as e:
            messagebox.showwarning("参数错误", str(e))
            return
        if rounds <= 0 and score <= 0:
            messagebox.showwarning("缺少目标", "局数上限和积分目标至少填一个；另一个填 0 表示不限。")
            return
        mode = self.mode_var.get().strip().lower()
        if mode not in MODE_TEXT:
            mode = "hybrid"

        window = self.window_var.get().strip() or engine.WINDOW
        render = self.render_var.get().strip() or engine.RENDER_WINDOW
        adb_text = self.adb_var.get().strip()
        serial = self.serial_var.get().strip() or runtime.default_adb_serial()
        if not adb_text:
            adb_text = str(runtime.default_adb_path())
        if not Path(adb_text).exists():
            messagebox.showerror("ADB 不存在", f"找不到 ADB：\n{adb_text}\n\n请检查路径，或恢复默认值。")
            return
        if not Path(engine.MODEL_DIR).is_dir():
            messagebox.showerror("OCR 模型缺失", f"找不到 OCR 模型目录：\n{engine.MODEL_DIR}")
            return

        self._save_config(rounds, score, mode)
        engine.configure_runtime(adb=adb_text, serial=serial, window=window,
                                 render_window=render, templates=engine.TPL,
                                 model_dir=engine.MODEL_DIR)
        os.environ["R1999_MAX_RUNS"] = str(rounds)
        os.environ["R1999_SCORE_TARGET"] = str(score)
        os.environ["R1999_NO_DANGER_MODE"] = mode
        os.environ["GAMEBOT_STOP_WINDOW"] = f"{window}|{render}"
        self.stop_requested = False
        self.running = True
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.reason_var.set("-")
        self.state_var.set("启动中")
        self._append_log(f"[启动] 局数上限={rounds or '不限'}，积分目标={score or '关闭'}，模式={mode}")
        self._append_log(f"[启动] 主窗={window}，渲染窗={render}，serial={serial}")
        reset_stop()
        self.worker = threading.Thread(target=self._run_loop, daemon=True)
        self.worker.start()

    def _run_loop(self):
        try:
            self.loop = engine.Loop()
            self.loop.status_cb = lambda data: self.status_queue.put(data)
            if self.stop_requested:
                self.loop.stop = True
            self.loop.run()
        except Exception as e:
            self.logger.exception("便携 GUI 运行异常")
            self.log_queue.put(f"[异常] {e!r}")
        finally:
            self.loop = None
            self.running = False
            self.done_queue.put(True)

    def _on_loop_finished(self):
        self.running = False
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self.state_var.set("已停止")
        self._append_log("[结束] 挂机线程已退出")

    def _stop(self):
        if not self.running and self.loop is None:
            return
        self.stop_requested = True
        if self.loop is not None:
            self.loop.stop = True
        request_stop()
        self._append_log("[停止] 已请求停止（最多等 3 秒收尾）")

    # ── 日志/状态轮询 ─────────────────────────────
    def _append_log(self, text: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        # 限制行数，避免长时间挂机吃内存
        if int(self.log_text.index("end-1c").split(".")[0]) > 4000:
            self.log_text.delete("1.0", "800.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll(self):
        while True:
            try:
                line = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self._append_log(line)
        while True:
            try:
                data = self.status_queue.get_nowait()
            except queue.Empty:
                break
            self._update_status(data)
        try:
            self.done_queue.get_nowait()
        except queue.Empty:
            pass
        else:
            self._on_loop_finished()
        self.root.after(120, self._poll)

    def _update_status(self, data: dict):
        state = data.get("state", "")
        self.state_var.set(STATE_TEXT.get(state, state or "待机"))
        max_runs = data.get("max_runs") or 0
        self.runs_var.set(f"{data.get('runs', 0)} / {max_runs if max_runs else '不限'}")
        self.shops_var.set(f"当前 {data.get('current_shops', 0)} · 累计 {data.get('total_shops', 0)}")
        self.score_var.set(str(data.get("score", 0)))
        reason = data.get("stop_reason")
        self.reason_var.set({
            "max_runs": "达到局数上限",
            "score_target": "达到积分目标",
            "escape": "异常脱出",
            "danger": "危险页面",
            "space": "空格急停",
            "max_runtime": "最长运行时间",
        }.get(reason, reason or "-"))

    # ── 自检/截图 ─────────────────────────────────
    def _self_check(self):
        self._append_log("[自检] 开始……")
        threading.Thread(target=self._run_self_check, daemon=True).start()

    def _run_self_check(self):
        lines = []
        ok = True
        def add(s):
            lines.append(s)
            self.log_queue.put(s)
        try:
            add(f"[自检] frozen={runtime.is_frozen()} app={runtime.app_home()}")
            add(f"[自检] data={runtime.data_home()}")
            tpl = Path(engine.TPL)
            if tpl.is_dir():
                add(f"[自检] 模板 OK: {len(list(tpl.glob('*.png')))} 个 PNG")
            else:
                ok = False
                add(f"[自检] 模板缺失: {tpl}")
            model = Path(engine.MODEL_DIR)
            need = [model / "det" / "ch_PP-OCRv4_det_infer",
                    model / "rec" / "ch_PP-OCRv4_rec_infer"]
            if all(p.is_dir() for p in need):
                add(f"[自检] OCR 模型 OK: {model}")
            else:
                ok = False
                add(f"[自检] OCR 模型缺失: {model}")
            adb = Path(self.adb_var.get().strip() or engine.ADB)
            if adb.exists():
                add(f"[自检] ADB 存在: {adb}")
                try:
                    p = subprocess.run([str(adb), "version"], capture_output=True, text=True,
                                       timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    first = (p.stdout or p.stderr or "").strip().splitlines()
                    add(f"[自检] adb version: {first[0] if first else '无输出'}")
                except Exception as e:
                    add(f"[自检] adb version 失败: {e!r}")
            else:
                ok = False
                add(f"[自检] ADB 缺失: {adb}")
            title = self.window_var.get().strip() or engine.WINDOW
            found = find_window_rect(title, bring_to_front=False)
            if found:
                rect, hwnd = found
                add(f"[自检] MuMu 窗口 OK: {rect} HWND={hwnd}")
            else:
                add(f"[自检] 未找到 MuMu 窗口 '{title}'（游戏未启动时正常，运行前必须打开）")
            try:
                import paddleocr  # noqa: F401
                add("[自检] paddleocr import OK")
            except Exception as e:
                ok = False
                add(f"[自检] paddleocr import 失败: {e!r}")
            try:
                import tkinter  # noqa: F401
                import cv2
                add(f"[自检] tkinter/cv2 OK (cv2 {cv2.__version__})")
            except Exception as e:
                ok = False
                add(f"[自检] GUI 依赖失败: {e!r}")
        except Exception as e:
            ok = False
            add(f"[自检] 异常: {e!r}")
        report = "\n".join(lines) + f"\n结果: {'PASS' if ok else 'CHECK'}\n"
        try:
            (runtime.log_dir() / "selfcheck.txt").write_text(report, encoding="utf-8")
            add(f"[自检] 报告已写入: {runtime.log_dir() / 'selfcheck.txt'}")
        except Exception as e:
            add(f"[自检] 报告写入失败: {e!r}")

    def _preview(self):
        try:
            from PIL import Image, ImageTk
            from core.screenshot import Screenshot
            title = self.window_var.get().strip() or engine.WINDOW
            ss = Screenshot(window_title=title)
            img = ss.capture_pil()
            max_w, max_h = 430, 260
            w, h = img.size
            ratio = min(max_w / w, max_h / h, 1.0)
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
            self.preview_img = ImageTk.PhotoImage(img)
            self.preview_label.configure(image=self.preview_img, text="")
            self._append_log(f"[截图] {title} {img.size[0]}x{img.size[1]}")
        except Exception as e:
            self._append_log(f"[截图失败] {e!r}")
            messagebox.showerror("截图失败", f"无法截取窗口:\n{e}")

    def _open_data(self):
        try:
            os.startfile(str(runtime.data_home()))  # type: ignore[attr-defined]
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    # ── 关闭 ─────────────────────────────────────
    def _on_close(self):
        if self.running:
            self._stop()
            if self.worker and self.worker.is_alive():
                self.worker.join(timeout=3)
        try:
            release()
        finally:
            self.root.destroy()


def main():
    check_my_lock()
    root = tk.Tk()
    PortableGui(root)
    root.mainloop()


if __name__ == "__main__":
    main()
