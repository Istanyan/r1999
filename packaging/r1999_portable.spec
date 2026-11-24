# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir spec for the r1999 portable build.

Build script sets R1999_APP_ROOT / R1999_TEMPLATE_SRC / R1999_MODEL_SRC /
R1999_ADB_SRC. Keep this spec independent of the local install directory.
"""

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

APP_ROOT = Path(os.environ["R1999_APP_ROOT"]).resolve()
TEMPLATE_SRC = Path(os.environ["R1999_TEMPLATE_SRC"]).resolve()
MODEL_SRC = Path(os.environ["R1999_MODEL_SRC"]).resolve()
ADB_SRC = Path(os.environ["R1999_ADB_SRC"]).resolve()
LITE = os.environ.get("R1999_LITE") == "1"

datas = []
binaries = []
hiddenimports = []

# paddleocr.py adds its own directory to sys.path and imports top-level
# `tools` / `ppocr` / `ppstructure`. Add that directory during analysis too,
# otherwise PyInstaller misses their dependencies (pyclipper, shapely, ...).
try:
    import paddleocr
    _paddleocr_dir = str(Path(paddleocr.__file__).resolve().parent)
    if _paddleocr_dir not in sys.path:
        sys.path.insert(0, _paddleocr_dir)
except Exception:
    pass

# Paddle/PaddleOCR: collect runtime files and PaddleOCR submodules, but not
# every Paddle submodule. Collecting all of paddle pulls paddle.jit.sot, whose
# import currently crashes PyInstaller's isolated dependency scanner.
for pkg in ("paddle", "Cython", "shapely", "pyclipper", "lmdb", "rapidfuzz"):
    try:
        datas += collect_data_files(pkg)
        binaries += collect_dynamic_libs(pkg)
    except Exception:
        pass
try:
    # paddleocr.py appends its own directory to sys.path and imports top-level
    # `tools`, `ppocr`, `ppstructure`. In a frozen onedir those are not in the
    # PYZ under those names, so copy the .py files next to the package.
    datas += collect_data_files("paddleocr", include_py_files=True)
    binaries += collect_dynamic_libs("paddleocr")
except Exception:
    pass
try:
    hiddenimports += collect_submodules("paddleocr")
    hiddenimports += collect_submodules("Cython")
    # These are top-level names created by paddleocr.py's sys.path append.
    for name in ("tools", "ppocr", "ppstructure"):
        hiddenimports += collect_submodules(name)
except Exception:
    pass

hiddenimports += [
    "paddle",
    "paddle.base",
    "paddle.base.core",
    "paddle.framework",
    "paddle.inference",
    "paddle.inference.wrapper",
    "paddle.utils",
    "paddle.utils.cpp_extension",
    "paddle.device",
    "paddle.device.cuda",
    "paddle.nn",
    "paddle.nn.functional",
    "paddle.tensor",
    "paddle.static",
    "paddle.io",
    "pyclipper",
    "shapely",
    "lmdb",
    "rapidfuzz",
    "docx",
    "openpyxl",
    "lxml",
    "cv2",
    "numpy",
    "scipy",
    "PIL",
    "games.r1999.r1999",
    "r1999_portable_gui",
    "core.runtime",
    "core.game_script",
    "core.task",
    "core.game_bot",
    "core.screenshot",
    "core.matcher",
    "core.ocr",
    "core.action",
    "core.instance_lock",
    "pyscreeze",
    "pygetwindow",
    "pymsgbox",
    "mouseinfo",
    "mss.windows",
    "PIL._tkinter_finder",
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
]

# Resource trees are copied verbatim. models/paddle must keep det/rec/cls layout.
datas += [
    (str(TEMPLATE_SRC), "templates/r1999"),
    (str(MODEL_SRC), "models/paddle"),
    (str(ADB_SRC), "adb"),
]

# Base excludes: never fun pack heavy training/GUI stacks we do not use.
EXCLUDES = [
    "notebook",
    "IPython",
    "pytest",
    "torch",
    "torchvision",
    "torchaudio",
    "modelscope",
    "ctranslate2",
    "transformers",
    "paddle.jit.sot",
    "pandas",
    "matplotlib",
    "sympy",
    "skimage.tests",
    "scipy.tests",
    "numpy.tests",
]

# Lite build: PaddleOCR needs scipy/skimage, but not a desktop Qt stack,
# video codecs, or ONNX runtime. Excluding these removes ~300 MB of dead DLLs.
if LITE:
    EXCLUDES += [
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "imageio_ffmpeg",
        "av",
        "moviepy",
        "pydub",
        "soundfile",
        "onnxruntime",
        "sklearn",
        "tensorflow",
        "paddle2onnx",
    ]

a = Analysis(
    [str(APP_ROOT / "r1999_portable_launcher.py")],
    pathex=[str(APP_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="r1999",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="r1999",
)
