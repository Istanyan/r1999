"""
构建 r1999 便携版（PyInstaller onedir）。

用法（Windows）：
  D:\\tool\\venvs\\r1999_build\\Scripts\\python.exe packaging\\build_r1999_portable.py

产物：
  E:\\m\\bao\\temp\\r1999_portable_20261123\\r1999\\r1999.exe
  E:\\m\\bao\\temp\\r1999_portable_20261123\\r1999_portable_20261123.zip
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path


PROJECT = Path(r"E:\m\game_bot")
TEMPLATE_SRC = PROJECT / "templates" / "r1999"
MODEL_SRC = Path(r"E:\paddle_models")
ADB_DIR = Path(r"E:\Program Files\YXReverse1999-12.0\shell")
OUT_ROOT = Path(r"E:\m\bao\temp\r1999_portable_20261123")
BUILD_ROOT = Path(r"E:\m\bao\temp\r1999_portable_build")
SPEC = PROJECT / "packaging" / "r1999_portable.spec"
PYI_PYTHON = Path(r"D:\tool\venvs\r1999_build\Scripts\python.exe")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_stats(root: Path) -> dict:
    files = 0
    size = 0
    for p in root.rglob("*"):
        if p.is_file():
            files += 1
            try:
                size += p.stat().st_size
            except OSError:
                pass
    return {"files": files, "bytes": size, "mb": round(size / 1024 / 1024, 1)}


def prepare_adb(stage: Path) -> Path:
    stage.mkdir(parents=True, exist_ok=True)
    copy = [
        "adb.exe",
        "AdbWinApi.dll",
        "AdbWinUsbApi.dll",
    ]
    for name in copy:
        src = ADB_DIR / name
        if not src.exists():
            raise FileNotFoundError(src)
        shutil.copy2(src, stage / name)
    # adb.exe 依赖的 VC/API DLL；只复制 DLL，不复制 7za.exe 等无关文件
    for src in ADB_DIR.glob("*.dll"):
        dst = stage / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
    return stage


def write_readme(pkg: Path) -> None:
    text = """r1999 街机挂机（便携版）
================================

双击 r1999.exe 即可启动。目标电脑不需要安装 Python。
但目标电脑需要已安装 MuMu 模拟器 12，并在运行前打开 r1999 游戏，登录到大厅。

默认假设
--------
- MuMu 主窗口标题：MuMu模拟器12
- 渲染窗口标题：MuMuPlayer
- ADB serial：127.0.0.1:16384
- 游戏画面：1920×1080（窗口约 1930×1139）
- 包内已带：r1999 模板、PaddleOCR 模型、ADB 34.0.1

第一次使用
----------
1. 解压到纯英文/数字路径，例如 D:\\r1999_portable。
   不要放在中文用户名下的深层目录；Paddle 的 C++ 引擎对中文模型路径敏感。
2. 启动 MuMu，进入 r1999 大厅。
3. 双击 r1999.exe。
4. 如果窗口标题/ADB 端口不同，在界面“环境”一栏改完后点“开始挂机”。
   不确定时先点“环境自检”，报告在 data\\logs\\selfcheck.txt。

数据与日志
----------
- data\\config\\settings.json：界面参数，下次启动自动恢复
- data\\logs\\r1999_loop.log：完整运行日志
- data\\temp：截图/死前录像等临时证据
- data\\logs\\selfcheck.txt：环境自检报告

注意
----
- 空格急停只在 MuMu 主窗/渲染窗位于前台时生效。
- 只支持单实例；重复启动会被 data\\temp\\r1999_mumu.lock 拦住。
- 首次启动 Windows 可能弹 SmartScreen；这是未签名 PyInstaller 程序的常见提示。
- 本包只操作 r1999 专用 MuMu，不会主动连接其他游戏的模拟器。
"""
    (pkg / "README.txt").write_text(text, encoding="utf-8")


def write_settings(pkg: Path) -> None:
    cfg = {
        "window": {"main_title": "MuMu模拟器12", "render_title": "MuMuPlayer"},
        "device": {"adb_path": "auto", "adb_serial": "auto"},
        "paths": {"model_dir": "auto"},
        "r1999": {"rounds": 5, "score": 0, "mode": "hybrid"},
    }
    p = pkg / "data" / "config" / "settings.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def patch_paddleocr_packages(pkg: Path) -> None:
    """paddleocr 的 ppocr / tools/infer 是 PEP420 命名空间包，冻结后
    FrozenImporter 不认目录；同时 paddleocr.py 在冻结环境会把 __dir__
    算成 _internal，故把 tools/ppocr/ppstructure 顶层副本放到 _internal。"""
    src = pkg / "_internal" / "paddleocr"
    for name in ("tools", "ppocr", "ppstructure"):
        s = src / name
        d = pkg / "_internal" / name
        if s.is_dir() and not d.exists():
            shutil.copytree(s, d)
    for rel in (
        "_internal/paddleocr/ppocr/__init__.py",
        "_internal/paddleocr/tools/infer/__init__.py",
        "_internal/ppocr/__init__.py",
        "_internal/tools/infer/__init__.py",
    ):
        p = pkg / rel
        if p.parent.is_dir():
            p.write_text("", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-zip", action="store_true", help="不生成 zip（调试构建时用）")
    args = ap.parse_args()

    for p in (PYI_PYTHON, TEMPLATE_SRC, MODEL_SRC, ADB_DIR, SPEC):
        if not p.exists():
            print(f"缺少构建输入: {p}", file=sys.stderr)
            return 2

    if OUT_ROOT.exists():
        # 只清理本脚本产出的目录，避免误删用户文件
        pkg = OUT_ROOT / "r1999"
        if pkg.exists():
            shutil.rmtree(pkg)
        if (OUT_ROOT / "r1999_portable_20261123.zip").exists():
            (OUT_ROOT / "r1999_portable_20261123.zip").unlink()
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    BUILD_ROOT.mkdir(parents=True, exist_ok=True)
    adb_stage = prepare_adb(BUILD_ROOT / "adb")

    env = os.environ.copy()
    env.update({
        "R1999_APP_ROOT": str(PROJECT),
        "R1999_TEMPLATE_SRC": str(TEMPLATE_SRC),
        "R1999_MODEL_SRC": str(MODEL_SRC),
        "R1999_ADB_SRC": str(adb_stage),
    })
    cmd = [
        str(PYI_PYTHON), "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--distpath", str(OUT_ROOT),
        "--workpath", str(BUILD_ROOT / "work"),
        str(SPEC),
    ]
    log_path = OUT_ROOT / "build.log"
    print("运行:", subprocess.list2cmdline(cmd))
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(cmd, cwd=str(PROJECT), env=env, stdout=log,
                              stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        print(f"PyInstaller 构建失败，退出码 {proc.returncode}；日志: {log_path}", file=sys.stderr)
        return proc.returncode

    pkg = OUT_ROOT / "r1999"
    exe = pkg / "r1999.exe"
    if not exe.exists():
        print(f"构建结束但找不到 {exe}", file=sys.stderr)
        return 3
    write_readme(pkg)
    write_settings(pkg)
    patch_paddleocr_packages(pkg)
    info = {
        "built_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version,
        "pyinstaller": subprocess.run(
            [str(PYI_PYTHON), "-m", "PyInstaller", "--version"],
            capture_output=True, text=True).stdout.strip(),
        "exe_sha256": sha256(exe),
        "source_r1999_sha256": sha256(PROJECT / "games" / "r1999" / "r1999.py"),
        "launcher_sha256": sha256(PROJECT / "r1999_portable_launcher.py"),
        "package": tree_stats(pkg),
    }
    (pkg / "BUILD_INFO.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(info, ensure_ascii=False, indent=2))

    if not args.no_zip:
        zip_base = OUT_ROOT / "r1999_portable_20261123"
        zip_path = Path(shutil.make_archive(str(zip_base), "zip", root_dir=str(OUT_ROOT),
                                            base_dir="r1999"))
        print(f"zip: {zip_path} ({zip_path.stat().st_size / 1024 / 1024:.1f} MB)")

    # 构建工作目录不放进最终包；保留 build.log 到 OUT_ROOT
    shutil.rmtree(BUILD_ROOT, ignore_errors=True)
    print(f"完成: {pkg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
