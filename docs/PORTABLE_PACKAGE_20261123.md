# r1999 便携版打包记录（2026-11-23）

## 交付

- 便携目录：`E:\m\bao\temp\r1999_portable_20261123\r1999`
- 压缩包：`E:\m\bao\temp\r1999_portable_20261123\r1999_portable_20261123.zip`
- 入口：`r1999.exe`（onedir，PyInstaller 6.22.3）
- 包内说明：`README.txt`

## 包内已包含

- Python 3.9.12 运行时与项目依赖（无需目标机安装 Python）
- PaddleOCR 2.8.1 + Paddle 2.6.2 推理文件（det/rec/cls 模型）
- ADB 34.0.1（`adb.exe` + 依赖 DLL）
- r1999 模板 41 张 PNG
- 专用便携 GUI（只加载 r1999，不加载其他游戏模块）
- 默认 `data/config/settings.json`：`rounds=5`、`score=0`、`mode=hybrid`

## 已验证

1. 冻结包 `r1999.exe --self-test --ocr-smoke`：返回 0，`PASS`，PaddleOCR 推理 `True`。
2. 冻结包 `r1999.exe --gui-smoke`：返回 0，`GUI smoke PASS`。
3. zip 完整性：475.6 MB / 4120 条目；入口、README、settings、ADB、OCR 模型均存在；`ZipFile.testzip()` 返回 `None`（全部条目 CRC 通过）。
4. 内置 ADB：`Android Debug Bridge 1.0.41 / 34.0.1-9979309`。
5. 包内自检报告：`data/logs/selfcheck.txt`、`data/logs/gui_smoke.txt`。

## 构建信息

- EXE SHA256：`a361e1afc95ca5c0a7c560bd5c74511e68d2e6630e4de251c4961f2c154be656`
- `games/r1999/r1999.py` SHA256：`b2729b61600f7449021febf1306cdb16b0b10a2b6e12da92e01205ed2fdca144`
- 启动器 SHA256：`af043d38d25883ffff3db51b43e188fa7fe5ebc658e6fbee76a40a6b455d0d9c`
- 包体：3766 文件 / 1,303,648,688 bytes / 1243.3 MB
- 构建脚本：`E:\m\game_bot\packaging\build_r1999_portable.py`
- Spec：`E:\m\game_bot\packaging\r1999_portable.spec`

## 仍有限制

- 尚未在真实 r1999 专用 MuMu 上跑完一局；当前只验证了启动、导入、模型、ADB、GUI 创建。
- 尚未在另一台 Windows 电脑做 clean-machine 启动验证。
- 目标机仍需安装并启动 MuMu 12，r1999 需登录到大厅；默认窗口标题 `MuMu模拟器12`、渲染窗 `MuMuPlayer`、ADB serial `127.0.0.1:16384`。
- 中文/空格路径下 Paddle 模型的 ASCII 回退未实测；README 建议解压到纯英文/数字路径。
- 包体较大（目录 1.24 GB、zip 475.6 MB），当前未进一步裁剪。

## 复现构建

```powershell
D:\tool\venvs\r1999_build\Scripts\python.exe E:\m\game_bot\packaging\build_r1999_portable.py --zip
```
