"""
运行期路径/配置（便携版与源码共用）。

目标：把“代码安装目录 / 可写数据目录 / 只读资源目录”分开。
- 源码运行：默认数据目录 = 项目根/data（若根目录不可写，退回 LOCALAPPDATA）。
- PyInstaller onedir：exe 所在目录可写，资源在 sys._MEIPASS(_internal)。
- 可用环境变量覆盖：GAMEBOT_DATA / GAMEBOT_TEMPLATES / GAMEBOT_MODEL_DIR /
  GAMEBOT_ADB / GAMEBOT_WINDOW / GAMEBOT_RENDER_WINDOW / GAMEBOT_LOCK_NAME。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Optional

_DEFAULT_SETTINGS = {
    "window": {
        "main_title": "MuMu模拟器12",
        "render_title": "MuMuPlayer",
    },
    "device": {
        "adb_path": "auto",
        "adb_serial": "auto",
    },
    "r1999": {
        "rounds": 5,
        "score": 0,
        "mode": "hybrid",
    },
}

_DATA_HOME: Optional[Path] = None


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_home() -> Path:
    """可执行文件/源码项目根。"""
    env = os.environ.get("GAMEBOT_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource_home() -> Path:
    """只读资源根。PyInstaller onedir 下为 _internal。"""
    env = os.environ.get("GAMEBOT_RESOURCES")
    if env:
        return Path(env).expanduser().resolve()
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass).resolve()
    return app_home()


def _writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".write_test_{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def data_home() -> Path:
    """可写数据根（配置/日志/temp/evidence）。"""
    global _DATA_HOME
    if _DATA_HOME is not None:
        return _DATA_HOME
    env = os.environ.get("GAMEBOT_DATA")
    if env:
        candidate = Path(env).expanduser()
        if not candidate.is_absolute():
            candidate = app_home() / candidate
    else:
        candidate = app_home() / "data"
    if _writable_dir(candidate):
        _DATA_HOME = candidate.resolve()
        return _DATA_HOME
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    candidate = Path(local) / "GameBot"
    candidate.mkdir(parents=True, exist_ok=True)
    _DATA_HOME = candidate.resolve()
    return _DATA_HOME


def config_dir() -> Path:
    p = data_home() / "config"
    p.mkdir(parents=True, exist_ok=True)
    return p


def log_dir() -> Path:
    p = data_home() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def temp_dir() -> Path:
    p = data_home() / "temp"
    p.mkdir(parents=True, exist_ok=True)
    return p


def evidence_dir() -> Path:
    p = data_home() / "evidence"
    p.mkdir(parents=True, exist_ok=True)
    return p


def templates_dir() -> Path:
    env = os.environ.get("GAMEBOT_TEMPLATES")
    if env:
        return Path(env).expanduser().resolve()
    return resource_home() / "templates"


def _is_ascii_path(path: Path) -> bool:
    try:
        str(path).encode("ascii")
        return True
    except UnicodeEncodeError:
        return False


def _ascii_fallback_model_dir(src: Path) -> Path:
    """Paddle C++ 引擎打不开中文路径；必要时把模型复制到纯 ASCII 目录。"""
    if _is_ascii_path(src):
        return src
    candidates = [
        Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData") / "GameBot" / "models",
        Path(r"C:\Users\Public\GameBotData") / "models",
    ]
    for dst in candidates:
        try:
            if not (dst / "det").exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                if dst.exists():
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            return dst
        except OSError:
            continue
    return src


def model_dir() -> Path:
    env = os.environ.get("GAMEBOT_MODEL_DIR")
    if env:
        p = Path(env).expanduser().resolve()
        return _ascii_fallback_model_dir(p)
    candidates = [
        resource_home() / "models" / "paddle",
        data_home() / "models" / "paddle",
        Path(r"E:\paddle_models"),
    ]
    for p in candidates:
        if p.exists():
            return _ascii_fallback_model_dir(p)
    return data_home() / "models" / "paddle"


def known_adb_candidates() -> list[Path]:
    return [
        resource_home() / "adb" / "adb.exe",
        data_home() / "adb" / "adb.exe",
        Path(r"E:\Program Files\YXReverse1999-12.0\shell\adb.exe"),
        Path(r"E:\MuMuPlayer\nx_device\12.0\shell\adb.exe"),
    ]


def default_adb_path() -> Path:
    env = os.environ.get("GAMEBOT_ADB") or os.environ.get("R1999_ADB")
    if env and env.lower() != "auto":
        return Path(env).expanduser()
    which = shutil.which("adb")
    if which:
        return Path(which)
    for p in known_adb_candidates():
        if p.exists():
            return p
    return resource_home() / "adb" / "adb.exe"


def default_window_title() -> str:
    return (os.environ.get("GAMEBOT_WINDOW")
            or os.environ.get("R1999_WINDOW")
            or "MuMu模拟器12")


def default_render_window_title() -> str:
    return (os.environ.get("GAMEBOT_RENDER_WINDOW")
            or os.environ.get("R1999_RENDER_WINDOW")
            or "MuMuPlayer")


def default_adb_serial() -> str:
    return (os.environ.get("GAMEBOT_ADB_SERIAL")
            or os.environ.get("R1999_ADB_SERIAL")
            or "127.0.0.1:16384")


def settings_path() -> Path:
    return config_dir() / "settings.json"


def load_settings() -> dict:
    p = settings_path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def get_setting(path: str, default: Any = None) -> Any:
    data = load_settings()
    cur: Any = data
    for key in path.split("."):
        if not isinstance(cur, dict) or key not in cur:
            return default
        cur = cur[key]
    return cur


def write_default_settings() -> Path:
    ensure_runtime_dirs()
    p = settings_path()
    if not p.exists():
        p.write_text(json.dumps(_DEFAULT_SETTINGS, ensure_ascii=False, indent=2) + "\n",
                     encoding="utf-8")
    return p


def ensure_runtime_dirs() -> None:
    for fn in (config_dir, log_dir, temp_dir, evidence_dir):
        fn()


def app_summary() -> dict:
    return {
        "frozen": is_frozen(),
        "app_home": str(app_home()),
        "resource_home": str(resource_home()),
        "data_home": str(data_home()),
        "templates": str(templates_dir()),
        "models": str(model_dir()),
        "adb": str(default_adb_path()),
        "window": default_window_title(),
        "render_window": default_render_window_title(),
        "serial": default_adb_serial(),
    }
