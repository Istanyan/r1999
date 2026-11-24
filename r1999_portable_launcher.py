"""
r1999 便携版入口。

职责：
1. 在 import gui / r1999 之前，把资源目录、模型目录、ADB、数据目录写成环境变量；
2. 把默认配置写到 app/data/config/settings.json；
3. 双击启动 GUI；也支持 --self-test / --gui-smoke 做冻结包自检。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import traceback
from pathlib import Path


def _paths():
    if getattr(sys, "frozen", False):
        app_dir = Path(sys.executable).resolve().parent
        res_dir = Path(getattr(sys, "_MEIPASS", app_dir)).resolve()
    else:
        app_dir = Path(__file__).resolve().parent
        res_dir = app_dir
    return app_dir, res_dir


def _setting(data: dict, path: str, default=None):
    cur = data
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def _detect_emulator_serial(adb: str, configured: str) -> str:
    """只认本机模拟器串号，忽略 USB 连着的真机。"""
    if configured not in ("", "auto"):
        return configured
    try:
        p = subprocess.run([adb, "devices"], capture_output=True, text=True,
                           timeout=12, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        serials = []
        for line in (p.stdout or "").splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                s = parts[0]
                if s.startswith(("127.0.0.1:", "localhost:", "emulator-")):
                    serials.append(s)
        if serials:
            return serials[0]
    except Exception:
        pass
    # MuMu 12 默认 16384；再试几个常见本地端口，不碰 USB 真机。
    for port in (16384, 16416, 7555, 5555):
        serial = f"127.0.0.1:{port}"
        try:
            subprocess.run([adb, "connect", serial], capture_output=True, text=True,
                           timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            p = subprocess.run([adb, "devices"], capture_output=True, text=True,
                               timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in (p.stdout or "").splitlines():
                if line.startswith(serial) and "device" in line.split():
                    return serial
        except Exception:
            continue
    return "127.0.0.1:16384"


def bootstrap() -> tuple[Path, Path]:
    app_dir, res_dir = _paths()
    data_dir = app_dir / "data"
    os.environ.setdefault("GAMEBOT_HOME", str(app_dir))
    os.environ.setdefault("GAMEBOT_RESOURCES", str(res_dir))
    os.environ.setdefault("GAMEBOT_DATA", str(data_dir))
    os.environ.setdefault("GAMEBOT_TEMPLATES", str(res_dir / "templates"))
    os.environ.setdefault("GAMEBOT_MODEL_DIR", str(res_dir / "models" / "paddle"))
    os.environ.setdefault("GAMEBOT_ADB", str(res_dir / "adb" / "adb.exe"))

    # runtime 依赖 GAMEBOT_DATA；必须在 import 前设置
    from core import runtime
    runtime.ensure_runtime_dirs()
    runtime.write_default_settings()
    cfg = runtime.load_settings()

    window = str(_setting(cfg, "window.main_title", "MuMu模拟器12"))
    render = str(_setting(cfg, "window.render_title", "MuMuPlayer"))
    adb_cfg = str(_setting(cfg, "device.adb_path", "auto"))
    serial_cfg = str(_setting(cfg, "device.adb_serial", "auto"))
    model_cfg = str(_setting(cfg, "paths.model_dir", "auto"))

    adb = str(runtime.default_adb_path()) if adb_cfg == "auto" else adb_cfg
    serial = _detect_emulator_serial(adb, serial_cfg)
    model = str(runtime.model_dir()) if model_cfg in ("", "auto") else model_cfg
    if not Path(model).is_dir():
        model = str(runtime.model_dir())
    os.environ["GAMEBOT_MODEL_DIR"] = model

    os.environ["R1999_WINDOW"] = window
    os.environ["R1999_RENDER_WINDOW"] = render
    os.environ["R1999_ADB"] = adb
    os.environ["R1999_ADB_SERIAL"] = serial
    os.environ["R1999_MODEL_DIR"] = model
    os.environ["R1999_TEMPLATES"] = str(res_dir / "templates" / "r1999")
    os.environ["GAMEBOT_DEFAULT_GAME"] = "重返未来1999"
    os.environ["GAMEBOT_STOP_WINDOW"] = f"{window}|{render}"
    os.environ.setdefault("GAMEBOT_LOCK_NAME", "r1999_mumu")
    return app_dir, res_dir


def _write_report(name: str, text: str) -> Path:
    from core import runtime
    p = runtime.log_dir() / name
    p.write_text(text, encoding="utf-8")
    return p


def self_test(ocr_smoke: bool = False) -> int:
    lines = []
    ok = True

    def add(msg: str, good: bool = True):
        nonlocal ok
        if not good:
            ok = False
        lines.append(msg)

    try:
        from core import runtime
        summary = runtime.app_summary()
        add(f"[env] frozen={summary['frozen']} app={summary['app_home']}")
        add(f"[env] resources={summary['resource_home']}")
        add(f"[env] data={summary['data_home']}")
        add(f"[env] templates={summary['templates']}")
        add(f"[env] models={summary['models']}")
        add(f"[env] adb={summary['adb']}")
        add(f"[env] window={summary['window']} render={summary['render_window']} serial={summary['serial']}")

        tpl = Path(summary["templates"]) / "r1999"
        if tpl.is_dir():
            add(f"[tpl] OK: {len(list(tpl.glob('*.png')))} PNG", len(list(tpl.glob("*.png"))) > 0)
        else:
            add(f"[tpl] MISSING: {tpl}", False)

        model = Path(summary["models"])
        need = [model / "det" / "ch_PP-OCRv4_det_infer",
                model / "rec" / "ch_PP-OCRv4_rec_infer",
                model / "cls" / "ch_ppocr_mobile_v2.0_cls_infer"]
        for p in need:
            add(f"[model] {'OK' if p.is_dir() else 'MISSING'}: {p}", p.is_dir())

        adb = Path(summary["adb"])
        add(f"[adb] {'OK' if adb.exists() else 'MISSING'}: {adb}", adb.exists())
        if adb.exists():
            try:
                p = subprocess.run([str(adb), "version"], capture_output=True, text=True,
                                   timeout=12, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                out = (p.stdout or p.stderr or "").strip().splitlines()
                add(f"[adb] version: {out[0] if out else 'no output'}", p.returncode == 0)
            except Exception as e:
                add(f"[adb] version failed: {e!r}", False)

        try:
            import cv2
            add(f"[pkg] cv2 {cv2.__version__}")
        except Exception as e:
            add(f"[pkg] cv2 import failed: {e!r}", False)

        try:
            import paddleocr  # noqa: F401
            add("[pkg] paddleocr import OK")
        except Exception as e:
            add(f"[pkg] paddleocr import failed: {e!r}\n{traceback.format_exc()}", False)

        try:
            import tkinter  # noqa: F401
            add("[pkg] tkinter import OK")
        except Exception as e:
            add(f"[pkg] tkinter import failed: {e!r}", False)

        try:
            from r1999_portable_gui import PortableGui  # noqa: F401
            add("[pkg] portable GUI import OK")
        except Exception as e:
            add(f"[pkg] portable GUI import failed: {e!r}", False)

        try:
            from games.r1999 import r1999 as engine
            add(f"[engine] import OK ADB={engine.ADB}")
        except Exception as e:
            add(f"[engine] import failed: {e!r}", False)

        if ocr_smoke:
            try:
                from paddleocr import PaddleOCR
                import numpy as np
                from PIL import Image, ImageDraw
                img = np.zeros((120, 500, 3), dtype=np.uint8)
                img[:] = 255
                pil = Image.fromarray(img)
                ImageDraw.Draw(pil).text((20, 35), "12345", fill=(0, 0, 0))
                arr = np.array(pil)[:, :, ::-1]
                ocr = PaddleOCR(lang="ch", show_log=False,
                                det_model_dir=str(model / "det" / "ch_PP-OCRv4_det_infer"),
                                rec_model_dir=str(model / "rec" / "ch_PP-OCRv4_rec_infer"),
                                cls_model_dir=str(model / "cls" / "ch_ppocr_mobile_v2.0_cls_infer"))
                res = ocr.ocr(arr, cls=False)
                add(f"[ocr] inference OK: {bool(res)}", bool(res))
            except Exception as e:
                add(f"[ocr] inference failed: {e!r}", False)

        add(f"[result] {'PASS' if ok else 'CHECK'}")
    except Exception as e:
        ok = False
        add(f"[fatal] {e!r}\n{traceback.format_exc()}")

    report = "\n".join(lines) + "\n"
    try:
        path = _write_report("selfcheck.txt", report)
        report += f"[report] {path}\n"
    except Exception:
        pass
    try:
        if sys.stdout:
            print(report, flush=True)
    except Exception:
        pass
    return 0 if ok else 1


def gui_smoke() -> int:
    try:
        import tkinter as tk
        from r1999_portable_gui import PortableGui
        root = tk.Tk()
        root.withdraw()
        app = PortableGui(root)
        root.update()
        root.update_idletasks()
        root.destroy()
        _write_report("gui_smoke.txt", "GUI smoke PASS\n")
        return 0
    except Exception as e:
        try:
            _write_report("gui_smoke.txt", f"GUI smoke FAIL: {e!r}\n{traceback.format_exc()}\n")
        except Exception:
            pass
        return 1


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--self-test", action="store_true", help="检查打包依赖/路径，不启动 GUI")
    ap.add_argument("--ocr-smoke", action="store_true", help="自检时额外跑一次 PaddleOCR 推理")
    ap.add_argument("--gui-smoke", action="store_true", help="只创建 GUI 并立即销毁")
    args = ap.parse_args()
    bootstrap()
    if args.self_test:
        return self_test(args.ocr_smoke)
    if args.gui_smoke:
        return gui_smoke()
    from r1999_portable_gui import main as gui_main
    gui_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
