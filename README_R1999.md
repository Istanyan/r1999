# r1999 源码运行与安装说明

> 这是 r1999 的源码仓库。Git clone 下来不能直接双击运行，需要先准备 Python、依赖、ADB 和 MuMu 12。
> 如果只想“打开即用”，请使用单独的便携包 zip（不提交在 Git 仓库里，通常放在 GitHub Releases）。

---

## 两种使用方式

### A. 普通使用：便携包（推荐）

适合只想挂机的机器：

1. 下载 `r1999_portable_lite_*.zip`（单独发布，不在本仓库）。
2. 解压到**纯英文/数字路径**，例如 `D:\r1999_portable`。
3. 启动 MuMu 模拟器 12，进入 r1999 大厅。
4. 双击 `r1999.exe`。
5. 不确定环境时先点“环境自检”，报告在 `data\logs\selfcheck.txt`。

便携包已带 Python、PaddleOCR、ADB 和模板，目标机不需要安装 Python。
但仍然需要 MuMu 12、r1999 已登录，默认 1920×1080 游戏画面。

### B. 开发/改代码：源码运行

适合需要修改逻辑、看日志、重新打包的机器：

```powershell
git clone https://github.com/Istanyan/r1999.git
cd r1999
```

然后按下面的“环境清单”和“一键安装”操作。

---

## 环境清单

| 项目 | 要求 | 说明 |
|---|---|---|
| 操作系统 | Windows 10/11 x64 | 不支持 32 位 Python |
| Python | **3.9.x x64 推荐**；3.10/3.11 可尝试 | 不要用 Microsoft Store 的 `python` 别名；安装时勾选“Add python.exe to PATH” |
| MuMu | MuMu 模拟器 12 | r1999 使用专用 MuMu，不要和别的模块共用实例 |
| 游戏 | 重返未来 1999，已登录到大厅 | 启动前手动进入大厅 |
| ADB | Android Platform Tools / MuMu 自带 adb | 包内或项目 `adb\adb.exe`；也可用 PATH 中的 adb |
| OCR 模型 | PaddleOCR PP-OCRv4 det/rec/cls | 首次 OCR 会尝试自动下载；也可用 `GAMEBOT_MODEL_DIR` 指定 |
| 分辨率 | 游戏画面 1920×1080，窗口约 1930×1139 | 当前坐标按该分辨率校准 |
| 磁盘 | 至少 500 MB 空余 | 依赖和模型会占空间 |
| 网络 | 首次安装依赖/下载模型需要 | 之后可离线运行 |

默认设备参数：

- MuMu 主窗口：`MuMu模拟器12`
- 渲染窗口：`MuMuPlayer`
- ADB serial：`127.0.0.1:16384`
- 单实例锁：`data\temp\r1999_mumu.lock`

窗口标题或端口不一致时，在 GUI 的“环境”栏修改，或使用下面的环境变量。

---

## 一键安装（推荐）

在仓库根目录双击：

```text
setup_r1999_env.bat
```

它会：

1. 检查 Python 3.9–3.12 x64；
2. 在项目下创建 `.venv`；
3. 安装 `requirements.txt` 中的依赖；
4. 尝试下载 Android platform-tools，并把 `adb.exe`、`AdbWinApi.dll`、`AdbWinUsbApi.dll` 放到 `adb\`；
5. 打印启动和自检命令。

只检查、不安装：

```text
setup_r1999_env.bat --check
```

脚本**不会**替你做这些事：

- 不会安装 Python 本身（Python 需要你先装好）；
- 不会安装 MuMu 或登录游戏；
- 不会强制下载 OCR 模型（首次 OCR 由 PaddleOCR 自动下载）；
- Google ADB 下载在国内可能失败，此时请用 MuMu 自带 adb，或手动放到 `adb\`。

---

## 手动安装

如果不想用 bat：

```powershell
# 1. 创建虚拟环境
py -3.9 -m venv .venv
.\.venv\Scripts\Activate.bat

# 2. 安装依赖
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

ADB 二选一：

```text
# 方式 1：放到项目 adb 目录
adb\adb.exe
adb\AdbWinApi.dll
adb\AdbWinUsbApi.dll

# 方式 2：已经在 PATH 里
adb version

# 方式 3：环境变量指定
set GAMEBOT_ADB=D:\platform-tools\adb.exe
```

OCR 模型二选一：

```text
# 方式 1：首次运行自动下载（需要网络）
python r1999_portable_launcher.py --self-test --ocr-smoke

# 方式 2：已有模型目录
set GAMEBOT_MODEL_DIR=D:\paddle_models
```

模型目录结构必须是：

```text
paddle_models\
├── det\ch_PP-OCRv4_det_infer\
├── rec\ch_PP-OCRv4_rec_infer\
└── cls\ch_ppocr_mobile_v2.0_cls_infer\
```

---

## 启动

### 方式 1：bat

双击：

```text
启动r1999挂机.bat
```

bat 已改成使用自身所在目录，不再写死 `E:\m\game_bot`。
如果存在 `.venv`，会自动使用 `.venv\Scripts\python.exe`；否则使用系统 Python。

### 方式 2：命令行

```powershell
.\.venv\Scripts\python.exe r1999_portable_launcher.py
```

### 方式 3：自检

```powershell
.\.venv\Scripts\python.exe r1999_portable_launcher.py --self-test --ocr-smoke
```

自检报告：

```text
data\logs\selfcheck.txt
```

---

## 第一次运行检查清单

- [ ] Python 3.9.x x64 已安装，`python --version` 正常
- [ ] `setup_r1999_env.bat` 执行成功
- [ ] `adb version` 能输出 `Android Debug Bridge version`
- [ ] MuMu 12 已启动
- [ ] `adb connect 127.0.0.1:16384` 成功（端口不是这个时改成实际端口）
- [ ] r1999 已登录到大厅
- [ ] MuMu 窗口标题是 `MuMu模拟器12`，渲染窗是 `MuMuPlayer`
- [ ] 游戏画面 1920×1080
- [ ] `--self-test --ocr-smoke` 通过
- [ ] 启动 GUI 后先点“环境自检”，再开始挂机

---

## 环境变量

| 变量 | 默认 | 用途 |
|---|---|---|
| `GAMEBOT_DATA` | 项目根 `data` | 日志、配置、截图、锁文件 |
| `GAMEBOT_TEMPLATES` | 项目根 `templates` | 模板目录 |
| `GAMEBOT_MODEL_DIR` | `models\paddle` / 本地模型 | PaddleOCR 模型目录 |
| `GAMEBOT_ADB` | 项目 `adb\adb.exe` / PATH | ADB 可执行文件 |
| `GAMEBOT_WINDOW` | `MuMu模拟器12` | 主窗口标题 |
| `GAMEBOT_RENDER_WINDOW` | `MuMuPlayer` | 渲染窗口标题 |
| `GAMEBOT_ADB_SERIAL` | `127.0.0.1:16384` | ADB 串号 |
| `GAMEBOT_LOCK_NAME` | `r1999_mumu` | 单实例锁名称 |

项目里还有 `r1999_portable_launcher.py` 的默认配置；临时改参数时优先用环境变量或 GUI 的“环境”栏，不要改业务代码里的坐标。

---

## 常见问题

### `python` 不是内部或外部命令

Python 没装或没加入 PATH。安装 Python 3.9 x64，并勾选“Add python.exe to PATH”。不要用 Microsoft Store 的别名。

### `No module named ...`

先确认使用的是 `.venv\Scripts\python.exe`：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### ADB 找不到

```powershell
adb version
adb connect 127.0.0.1:16384
```

找不到就把 `adb.exe` 和两个 ADB DLL 放到项目 `adb\`，或设置 `GAMEBOT_ADB`。

### OCR 报模型/路径错误

- 先确认能联网，让 PaddleOCR 首次下载模型；
- 或设置 `GAMEBOT_MODEL_DIR`；
- 不要把项目放在中文用户名下的深层目录，Paddle 的 C++ 引擎对非 ASCII 路径敏感。

### 窗口找不到

MuMu 窗口标题不是 `MuMu模拟器12` 时，在 GUI“环境”栏改主窗和渲染窗标题；不要只改一个。

### 分辨率不对

当前坐标按 1920×1080 游戏画面校准。其他分辨率需要重新校准坐标，不是改一个窗口大小就能用。

---

## 仓库里有什么、没有什么

**有：**

- `games/r1999/r1999.py`
- `core/` 运行时、OCR、截图、输入、锁
- `templates/r1999/`
- `r1999_portable_gui.py`
- `r1999_portable_launcher.py`
- `packaging/` 打包配置
- `docs/` 审计、测试和打包记录

**没有：**

- Python 运行时
- `adb.exe`
- PaddleOCR 模型文件
- 243 MB 便携包 zip
- `data/` 运行日志和截图
- `temp/` 测试证据

这些运行产物已被 `.gitignore` 排除；便携包请通过 GitHub Releases 分发。