# r1999 精简便携版（2026-11-24）

## 交付

- 精简目录：`E:\m\bao\temp\r1999_portable_lite_20261124\r1999`
- 精简 zip：`E:\m\bao\temp\r1999_portable_lite_20261124\r1999_portable_lite_20261124.zip`
- 入口：`r1999.exe`（PyInstaller 6.22.3，onedir）

## 体积对比

| 项目 | 完整版 20261123 | 精简版 20261124 |
|---|---:|---:|
| 解压目录 | 1243.3 MB | **663.2 MB** |
| zip | 475.6 MB | **243.1 MB** |
| 文件数 | 3766 | 3589 |

## 精简内容

- ADB 目录从 213.3 MB 降到 **5.8 MB**：只保留 `adb.exe`、`AdbWinApi.dll`、`AdbWinUsbApi.dll`，不再复制 MuMu 的 Qt/D3D/Poco DLL。
- 剔除 PyQt5 / QtWebEngine：约 130 MB。
- 剔除 ffmpeg / imageio_ffmpeg / av / moviepy / pydub / soundfile：约 160 MB。
- 剔除 onnxruntime、sklearn、tensorflow、paddle2onnx 等非运行依赖。
- **保留** PaddleOCR 实际需要的 imageio、scipy、skimage、Cython、Paddle、OpenCV。

## 验证结果

- `r1999.exe --self-test --ocr-smoke`：**PASS**，PaddleOCR 推理 `True`。
- `r1999.exe --gui-smoke`：**PASS**。
- 包内 ADB：`Android Debug Bridge version 1.0.41`，仅 3 个文件。
- zip 全部条目 CRC 校验通过，入口 `r1999/r1999.exe` 存在。
- 包内报告：`data/logs/selfcheck.txt`、`data/logs/gui_smoke.txt`。

## 构建信息

- EXE SHA256：`713448609d8f668fbdf6041d7e4a9c5bd7a5b5c228f441b1dd94ea5df19b49f0`
- `games/r1999/r1999.py` SHA256：`b2729b61600f7449021febf1306cdb16b0b10a2b6e12da92e01205ed2fdca144`
- 启动器 SHA256：`af043d38d25883ffff3db51b43e188fa7fe5ebc658e6fbee76a40a6b455d0d9c`
- 构建脚本：`E:\m\game_bot\packaging\build_r1999_lite.py`
- Spec：`E:\m\game_bot\packaging\r1999_portable.spec`（`R1999_LITE=1` 时启用精简排除）

## 仍有限制

- 尚未在真实 r1999 专用 MuMu 上跑完整一局；只验证了启动、导入、模型、ADB、OCR 推理和 GUI 创建。
- 尚未在另一台干净 Windows 电脑验证。
- 目标机仍需 MuMu 12、r1999 已登录大厅；默认窗口/ADB 端口不一致时在 GUI 环境栏修改。
- 解压建议使用纯英文/数字路径，避免 Paddle 中文模型路径问题。
