# game_bot 可移植性审计与解耦方案（2026-11-23）

> 本轮只做研究和方案，不改 `r1999.py` 的战斗逻辑，也不改 P12.6 行为基线。

## 1. 结论先说

当前项目**不是“改几个相对路径”就能移植**，而是三种耦合叠在一起：

1. **路径/数据耦合**：配置、日志、锁、截图缓存、OCR 模型、测试脚本都默认写在项目目录或固定盘符。
2. **设备/窗口耦合**：ADB 路径、ADB serial、MuMu 主窗标题、渲染窗标题、包名都写死在代码里。
3. **画面/分辨率耦合**：r1999 以 `1930×1139` 窗口、`1920×1080` 游戏画面、固定 ADB/mss 坐标为前提；换分辨率不是“路径问题”，而是校准问题。

推荐拆成两层目标，不要混在一起：

- **换 Windows 机器 / 换目录 / 换用户名 / 换 MuMu 安装位置**：可以做，风险低，建议先做。
- **换分辨率 / 换模拟器版本 / 多开 / 跨 macOS/Linux**：不属于同一量级，需要独立适配层，不能靠改路径解决。

**建议第一阶段只做“Windows + MuMu 12 + 1920×1080”的可移植化**：代码目录与用户数据目录分离，ADB/窗口/串号配置化，GUI 把用户填的窗口传给 r1999，启动时校验分辨率；战斗决策链一行不动。

**补充修正（2026-11-23）：r1999 与其他模块不是同一个 MuMu。** r1999 是专用模拟器，其他模块用的是另一套。因此配置不能设计成全局 `device`/`window`，必须按游戏绑定 `DeviceProfile`；GUI 里一个公共“窗口标题”输入框也不能服务所有游戏。否则会出现“r1999 把别的 MuMu 置前/截图/点错”的问题，这比单纯路径错误更危险。

## 2. 现状审计

### 2.1 精确的绝对路径字面量

全项目 `.py` 中 Windows 绝对路径字面量共 **29 处**，其中生产代码 **6 处**，其余是 `temp/r1999_versions` 归档版本和 `temp/tools` 测试脚本：

| 文件 | 位置 | 内容 | 影响 |
|---|---|---|---|
| `games/r1999/r1999.py` | 78 | `ADB = r"E:\Program Files\YXReverse1999-12.0\shell\adb.exe"` | 换机后 ADB 不存在，所有 adb 操作失败 |
| `games/r1999/r1999.py` | 190 | `local = r"E:\m\game_bot\temp\r1999_cap_adb.png"` | 换目录后截图缓存写不进去 |
| `games/r1999/r1999.py` | 495 | `dst = r"E:\paddle_models"` | OCR 模型目录固定 E 盘，换机后 OCR 预热失败 |
| `games/bbdc/bbdc.py` | 93-96 | `E:\MuMuPlayer\...\adb.exe` 等 3 个候选 | 只是候选，但本机路径混在代码里 |
| `启动r1999挂机.bat` | 4 | `cd /d E:\m\game_bot` | 换目录直接启动失败 |
| `启动GUI.bat` | 15 | `C:\Users\言\...\Python39\python.exe` | 换用户名/换 Python 安装路径失败；有 `python` 兜底但不保证依赖正确 |

另外 `core/ocr.py` 没有字面量，但用 `dirname(dirname(dirname(__file__)))` 推导到项目父目录，实际生成 `E:\m\paddle_home`；这是一个“隐式 E 盘路径”，换项目位置后会跟着变，且会写到项目外。

### 2.2 设备/窗口/串号耦合

| 文件 | 位置 | 固定值 | 说明 |
|---|---|---|---|
| `games/r1999/r1999.py` | 79 | `ADB_SERIAL = "127.0.0.1:16384"` | 换端口/多开/其他模拟器就失效 |
| `games/r1999/r1999.py` | 80 | `PKG = "com.shenlan.m.reverse1999"` | 包名固定；换渠道服/版本要改 |
| `games/r1999/r1999.py` | 82 | `WINDOW = "MuMu模拟器12"` | r1999 的 `Loop` 只认这个主窗 |
| `games/r1999/r1999.py` | 83 | `RENDER_WINDOW = "MuMuPlayer"` | 渲染窗标题固定；截图/点击坐标依赖它 |
| `games/r1999/r1999.py` | 524 | `FindWindowW(None, "MuMu模拟器12")` | 又重复写了一遍主窗标题 |
| `core/action.py` | 96 | `if "MuMu" not in buf.value` | 空格急停只认标题含 MuMu 的前台窗口；换模拟器标题后急停失效 |
| `games/bbdc/bbdc.py` | 98 | `ADB_SERIALS = (...16384, 7555, emulator-5554)` | 候选可用，但仍是代码内常量 |
| `games/deltaforce/deltaforce.py` | 52-54 | `WE_WINDOW/DF_WINDOW/DF_PROCESS` | 游戏窗口/进程名固定 |
| `main.py` | 418/435-446 | 默认 `--window MuMu`，按游戏名切窗口 | CLI 和 GUI 各有一份窗口映射 |

**关键前提：r1999 的 MuMu 是专用实例，和其他模块不是同一套。** 当前代码没有为这个事实建模：GUI 只有一个公共 `window_var`，`core/action` 用全局 `"MuMu"` 判断急停，`instance_lock` 只有一个全局锁。只要两套 MuMu 同时存在，就有把另一套置前、截图、误点或互相卡住的风险。

代码现状也印证了这一点：r1999 使用 `YXReverse1999-12.0\shell\adb.exe`，bbdc 的候选是 `MuMuPlayer\nx_device\...`，ADB 路径已经分开；但窗口标题、串号解析、单实例锁和 GUI 设备选择没有按实例分开。

**关键问题：GUI 的“窗口标题”输入框对 r1999 不起作用。**
`gui.py` 会把 `window_title` 传给 `GameBot`，但 `R1999Script._run_loop()` 直接 `Loop()`，`Loop.__init__` 又用模块级 `WINDOW`，所以用户在 GUI 里改成别的窗口名，r1999 仍然锚 `MuMu模拟器12`。这不是配置没暴露，而是参数根本没传进去。

### 2.3 数据写入位置（只读安装会坏）

以下内容当前写在项目目录内：

| 内容 | 当前位置 | 只读安装后果 |
|---|---|---|
| GUI 参数 | `r1999_gui.json`（项目根） | 保存失败，`_save_r1999_config` 只打印错误 |
| 任务配置 | `task_order.json`（项目根） | GUI 勾选/排序无法保存 |
| GUI 日志 | `temp/gui_log_YYYYMMDD.log` | 日志初始化失败（有 try 兜底，但丢日志） |
| r1999 日志 | `PROJECT_ROOT/temp/r1999_loop.log` | 文件 handler 创建失败，可能影响导入 |
| 单实例锁 | `temp/bot.lock` | `acquire()` 创建失败，启动被拒 |
| ADB 截图缓存 | `temp/r1999_cap_adb.png` | 写盘失败，`adb_cap()` 返回 None |
| 诊断输出 | `temp/diag_report/` | 诊断工具无法落盘 |
| bbdc 进度/失败截图 | `temp/bbdc_*` | 进度无法保存 |
| 三角洲调试截图 | `temp/df_debug_huohuo.png` | 写盘失败（有 try 但丢证据） |
| r1999 模板缓存 | `templates/r1999/*.npy` | 缓存写不进；`_load_tpl` 有 try，能跑但每次重解码 |
| OCR 模型 | `E:\paddle_models` 和 `E:\m\paddle_home` | 换机后不存在；OCR 不可用，危险页门禁降级 |

项目当前 `E:\paddle_models` 有 9 个文件/约 18 MB；`E:\m\paddle_home` 也有一份 9 个文件/约 18 MB 的模型。两套 OCR 路径机制并存：`core/ocr.py` 走 `USERPROFILE` 重定向，`r1999._warm_ocr()` 直接传 `E:\paddle_models`。移植时必须统一，否则会出现“GUI 里 OCR 能用、r1999 里 OCR 不能用”的分裂。

### 2.4 画面/分辨率耦合（不是路径问题）

r1999 大量逻辑以固定画面为前提：

- `_normalize_window()` 强制窗口 `1930×1139`（`r1999.py` 473-486）。
- `_desktop_frame()` 固定抓 `1920×1080`，再贴到 `1139×1930` 画布（`518-535`）。
- `render_click_point()` 固定 `1920×1080` 和 `(5, 54)` 内容偏移（`617-633`）。
- `DPAD`、`DPAD_ADB`、`HALL_DPAD`、`MACHINE`、`SCORE_MACHINE`、`WARNING_TAP`、`TITLE_START_ADB` 等几十个坐标都是固定值。
- 模板全部按当前画面裁剪；换分辨率后模板尺寸/位置都不匹配。

**结论：** 可移植化不能承诺“换任何分辨率都能跑”。正确做法是把画面参数收进 `ScreenProfile`，启动时用 `adb shell wm size` / 窗口矩形做校验；不匹配就**明确报错并停止**，而不是静默缩放模板。自动缩放/自动校准可以作为后续独立课题。

### 2.5 启动器与依赖

- `启动GUI.bat` 第 15 行写死用户名下的 Python 3.9；`if not exist` 才退回 `python`。新机器上 `python` 可能是 3.13/无 cv2/Paddle，GUI 会在导入阶段崩。
- `启动r1999挂机.bat` 第 4 行写死 `E:\m\game_bot`。
- `requirements.txt` 只有 Paddle 锁版本，`opencv-python`、`numpy`、`mss`、`pyautogui` 都是下限，换机器可能装到不兼容版本。
- `keyboard` 在 `core/action.py` 里是可选导入，但 requirements 没列；新机器没装时只剩 `GetAsyncKeyState` 兜底。
- 没有 `pyproject.toml` / 锁文件 / 虚拟环境引导；目前是“本机能跑的源码目录”，不是可分发项目。

### 2.6 已确认可复用的部分

这些地方方向是对的，不需要重写：

- `core/screenshot.py` 的 `find_window_rect(title_keyword)` 已经接收标题参数，不是硬编码。
- `core/game_bot.py` 的 `template_dir` 已经能从项目根推导，且支持 `game_name` 子目录。
- `diagnose.py` 已有环境/模板/OCR 三段诊断，可以作为“换机自检”的基础。
- `r1999` 的 `PROJECT_ROOT`、`TPL` 已经是相对项目根，不是绝对路径。
- GUI 已有窗口标题输入框、r1999 局数/积分目标、状态回调；只差把窗口/ADB 真正注入 `Loop`。

## 3. 推荐架构

### 3.1 分层

```
┌──────────────────────────────────────────────┐
│ GUI / CLI / 测试脚本                         │
│ 只读 RuntimeConfig，不直接拼路径              │
└──────────────────────┬───────────────────────┘
                       │ 注入
┌──────────────────────▼───────────────────────┐
│ RuntimeConfig（全局路径/优先级）              │
│ APP/DATA/config/log/temp/models               │
└──────────────────────┬───────────────────────┘
                       │ 按游戏解析
┌──────────────────────▼───────────────────────┐
│ GameContext（当前游戏）                       │
│ game → device_profile → screen_profile        │
└───────┬───────────────┬───────────────┬──────┘
        │               │               │
┌───────▼──────┐ ┌──────▼──────┐ ┌──────▼───────┐
│ PathManager  │ │ DeviceProfile│ │ GameSpec     │
│ APP/DATA/    │ │ ADB/串号/    │ │ 模板/画面/   │
│ config/log/  │ │ 主窗/渲染窗  │ │ 脚本入口     │
│ temp/models  │ │ stop_keywords│ │              │
└──────────────┘ └──────────────┘ └──────────────┘
        │               │               │
┌───────▼───────────────▼───────────────▼──────┐
│ 适配器：AdbClient / WindowLocator /          │
│ ScreenSource / TemplateStore / OcrService    │
│ r1999 只通过专用 profile 取资源               │
└──────────────────────────────────────────────┘
```

### 3.2 路径模型

```text
APP_HOME   = 代码/模板/默认配置所在目录（由 __file__ 推导，只读也可）
DATA_HOME  = 可写数据目录，默认可放 APP_HOME/data；不可写时回落
             %LOCALAPPDATA%\GameBot
  config/   r1999_gui.json, task_order.json, settings.json
  logs/     gui_log_*.log, r1999_loop.log
  temp/     bot.lock, r1999_cap_adb.png, diag_report/, bbdc_*, df_debug_*
  evidence/ 用户要保留的测试证据
  models/   paddle OCR 模型（或指向外部 ASCII 路径）
  cache/    模板 .npy 缓存（可选）
```

规则：

- **代码/模板** 放 `APP_HOME`，不写运行数据。
- **运行数据** 放 `DATA_HOME`，支持 `GAMEBOT_DATA` 覆盖。
- `DATA_HOME` 默认可写成 `APP_HOME/data`，这样整个项目文件夹复制走就是“便携模式”；如果检测到不可写，自动切 `%LOCALAPPDATA%\GameBot`，并在 GUI 显示实际路径。
- 旧版 `r1999_gui.json` / `task_order.json` 首次启动时从 `APP_HOME` 读取并迁移到 `DATA_HOME/config`；写入只写新位置，保留旧文件作只读回退。
- `GAMEBOT_HOME` 可覆盖 `APP_HOME`（用于打包/测试），`GAMEBOT_DATA` 覆盖 `DATA_HOME`。

### 3.3 配置模型（按游戏绑定设备）

建议新增 `config/settings.example.json`：

```json
{
  "paths": {
    "data_home": "auto",
    "model_dir": "auto"
  },
  "devices": {
    "r1999_mumu": {
      "label": "r1999 专用 MuMu",
      "adb_path": "auto",
      "adb_serial": "auto",
      "package": "com.shenlan.m.reverse1999",
      "window": {
        "main_title": "MuMu模拟器12",
        "render_title": "MuMuPlayer",
        "match": "exact",
        "stop_keywords": ["MuMu模拟器12", "MuMuPlayer"]
      },
      "screen": {
        "adb_size": [1920, 1080],
        "window_size": [1930, 1139],
        "content_offset": [5, 54],
        "strict": true
      }
    },
    "main_mumu": {
      "label": "其他模块 MuMu",
      "adb_path": "auto",
      "adb_serial": "auto",
      "package": "",
      "window": {
        "main_title": "MuMu安卓设备",
        "render_title": "MuMuNxDevice",
        "match": "exact",
        "stop_keywords": ["MuMu安卓设备", "MuMuNxDevice"]
      },
      "screen": {
        "strict": false
      }
    }
  },
  "games": {
    "r1999": {"device_profile": "r1999_mumu", "template_dir": "r1999"},
    "bbdc": {"device_profile": "main_mumu", "template_dir": "bbdc"},
    "deltaforce": {"device_profile": "pc", "template_dir": "三角洲"}
  }
}
```

关键规则：

- **先按游戏取 `device_profile`，再解析 ADB/串号/窗口**。不存在一个全局 `ADB` 或全局 `window` 适用于所有游戏。
- `r1999` 的 `device_profile="r1999_mumu"`，其他模块各自绑定自己的 profile；`r1999` 永远不能去“猜”另一个 MuMu。
- `window.match="exact"` 优先于 `contains`；r1999 不再用 `FindWindowW` 的模糊匹配去碰其他 MuMu 标题。
- `stop_keywords` 跟随设备 profile；空格急停只对当前游戏绑定的窗口生效，不再全局判断 `"MuMu"`。
- `instance_lock` 按 `device_profile` 分键（如 `bot_r1999_mumu.lock`、`bot_main_mumu.lock`），不要一个全局 `bot.lock` 卡住两套模拟器。

优先级：

```text
CLI 参数 > 环境变量 > 当前游戏的 device_profile + 用户配置 > 代码默认值
```

建议环境变量：

- `GAMEBOT_DATA`：数据目录
- `GAMEBOT_MODEL_DIR`：OCR 模型目录
- `GAMEBOT_GAME`：当前游戏（GUI/CLI 已有）
- `GAMEBOT_DEVICE`：强制指定 device_profile（测试用）
- `GAMEBOT_ADB` / `GAMEBOT_ADB_SERIAL` / `GAMEBOT_WINDOW` / `GAMEBOT_RENDER_WINDOW`：**只覆盖当前游戏 profile**，不要在两套模拟器同时运行时依赖全局环境变量

### 3.4 设备/窗口解析（r1999 专用实例）

`DeviceManager` 按 `DeviceProfile` 解析，不做全局自动发现：

1. **ADB 定位**：profile 指定路径 → 该 profile 的安装目录候选 → `shutil.which("adb")` → 报错。
   - `r1999_mumu` 只查 r1999 专用 MuMu 的 ADB，不去拿其他 MuMu 的 adb。
   - 两个 MuMu 都自带 adb 时，不能靠 PATH 决定用哪一个。
2. **串号解析**：profile 配置 serial → 用该 profile 的 adb 执行 `devices` → 只接受属于该实例的 serial → GUI 下拉选择。
   - 不能把“全局唯一 device”当成 r1999 的串号；另一套模拟器可能也在线。
3. **窗口定位**：profile 的 `main_title`/`render_title` + exact 匹配；必要时用进程路径/窗口句柄区分同名窗口；**不默认取第一个 MuMu 窗口**。
4. **前台/输入**：`raise_to_foreground`、点击、空格急停都只作用于当前 profile 的 HWND；不能因为标题含 `MuMu` 就把别的模拟器拉到前台。

r1999 的 `Loop` 增加运行时参数：

```python
Loop(runtime=RuntimeContext(device_profile="r1999_mumu"))
```

兼容做法：

- `R1999Script` 持有 `runtime`；GUI 启动 r1999 时注入 `r1999_mumu` 的窗口标题、ADB 路径/串号。
- `Loop.__init__` 内部调用 `configure_runtime(runtime)` 设置模块级别名（`ADB/WINDOW/RENDER_WINDOW`），这样现有 100 多处调用不需要一次性改完。
- `configure_runtime` 必须在 `Loop` 实例化前调用；不能依赖 GUI 进程启动时的全局环境变量，否则 GUI 先导入 `main.py` 时就已经把 r1999 的常量定死了。
- 第二阶段再把 `ADB` 全局调用收进 `AdbClient` 实例；`R1999Script` 和 `GameBot` 各自持自己的 `AdbClient`，避免两套模拟器串状态。

**并行边界：** 如果两套模拟器同时跑两个 bot，pyautogui/键盘/前台窗口仍是全局资源，路径解耦不能保证输入互不干扰。P0 先支持“按游戏选择正确实例、串行运行”；真要并行，需要输入后端隔离/串行化，单独做。

### 3.5 画面 profile

`ScreenProfile` 至少包含：

- `adb_size`（游戏画面，r1999 是 1920×1080）
- `window_size`（主窗，r1999 是 1930×1139）
- `content_offset`（画布内偏移 5/54）
- `strict`（不匹配时是否拒绝运行）
- `template_dir` / `game_id`

启动时校验：

```text
adb shell wm size            → 实际游戏分辨率
GetWindowRect(main_hwnd)     → 实际主窗尺寸
```

不一致时：

- `strict=true`：GUI 弹窗说明“当前 2560×1440，脚本需要 1920×1080，请先把 MuMu 分辨率调到 1920×1080”，不启动。
- 不允许静默 resize 模板或按比例改所有坐标；那会改变 P12.6 的命中率，属于新版本行为，必须单独测试。

后续要做“分辨率自适应”，应单独做 `ScreenProfile` 校准工具：用当前画面重新裁模板/求坐标，而不是在战斗循环里动态缩放。

### 3.6 游戏注册

当前 GUI 的 `game_options` 和 `GAME_REGISTRY` 是两份硬编码。可移植化不要求立刻重构成插件系统，但建议先抽 `GameSpec`：

```python
GameSpec(
    id="r1999",
    label="重返未来1999",
    template_dir="r1999",
    device_profile="r1999_mumu",          # 专用 MuMu，不是其他模块那套
    screen_profile="r1999_1920x1080",
    script_factory=lambda: R1999Script(),
)
```

`bbdc`/`deltaforce` 各自指向自己的 `device_profile`。GUI 从 `GameSpec` 生成下拉项，并按当前游戏显示对应的设备卡片；`r1999` 的窗口/画面 profile 不再散落在 `gui.py`、`main.py`、`r1999.py` 三处。

## 4. 分阶段实施

### P0：最小解耦（推荐先做）

目标：**换 Windows 机器/换目录能启动，P12.6 行为不变。**

改动：

1. 新增 `core/runtime.py`：`APP_HOME/DATA_HOME`、配置加载、`GAMEBOT_*` 环境变量、`resolve_device_profile(game)`、`find_adb(profile)/resolve_serial(profile)/find_window(profile)/screen_profile(game)`。**所有设备解析都必须先拿到当前游戏的 `device_profile`。**
2. 新增 `config/settings.example.json`，首次运行生成 `DATA_HOME/config/settings.json`；示例里至少包含 `r1999_mumu` 和 `main_mumu` 两个设备 profile。
3. `games/r1999/r1999.py` 只改边界：
   - 顶部 `ADB/ADB_SERIAL/PKG/TPL/WINDOW/RENDER_WINDOW` 改为从 `r1999_mumu` 的 runtime 取默认值；
   - `adb_cap()` 的 local 改 `runtime.temp_dir / "r1999_cap_adb.png"`；
   - `_warm_ocr()` 的模型目录改 `runtime.model_dir`（找不到 E:\paddle_models 时自动用 DATA_HOME/models）；
   - `_normalize_window()` / `render_click_point()` 的尺寸从 `ScreenProfile` 取，默认值与现在完全一致；
   - `Loop.__init__` 接受 `runtime`，`R1999Script._run_loop()` 传入；
   - **战斗决策链、Boss 逻辑、坐标算法不动。**
4. `gui.py`：
   - `r1999_gui.json` / `task_order.json` → `DATA_HOME/config`；
   - 日志 → `DATA_HOME/logs`；
   - 不再用一个公共 `window_var` 服务所有游戏：当前游戏选中 r1999 时，显示 `r1999_mumu` 设备卡片（专用 ADB/串号/主窗/渲染窗）；选中 bbdc/deltaforce 时显示各自的 profile；
   - 启动 r1999 时把 `r1999_mumu` 的 `RuntimeContext` 注入 `R1999Script`，而不是只传一个窗口标题；
   - 增加“环境自检”按钮（调 `diagnose`：当前 profile 的窗口、ADB、分辨率、模板、OCR）。
5. `main.py`：
   - `task_order.json` → `DATA_HOME/config`；
   - `run_example` 的 `templates/...` 用 `APP_HOME/templates`，不依赖 cwd；
   - 增加 `--device-profile`、`--adb`、`--adb-serial`、`--render-window`；默认从当前游戏的 profile 取，不再全局猜 MuMu。
6. `core/ocr.py`：模型目录从 runtime 取；不再把 `paddle_home` 写到项目父目录；`USERPROFILE` 重定向加锁，或改成直接传 `det_model_dir/rec_model_dir/cls_model_dir`。
7. `core/instance_lock.py` 改为按 `device_profile` 分锁（`bot_r1999_mumu.lock` / `bot_main_mumu.lock`），不要让 r1999 和其他模块互相卡住；`diagnose.py`、`bbdc.py`、`deltaforce.py` 的数据/输出路径 → `DATA_HOME`。
8. `core/action.py`：空格急停的前台窗口关键字从当前 profile 的 `stop_keywords` 取；不能再全局判断标题含 `"MuMu"`。
9. `启动GUI.bat` / `启动r1999挂机.bat`：
   - `cd /d "%~dp0"`；
   - 按顺序找 `.venv\Scripts\python.exe` → `py -3.9` → `python`；
   - 不再写死用户名路径；`启动r1999挂机.bat` 不再写死 `E:\m\game_bot`。
10. `temp/tools/*`：用 `Path(__file__).resolve().parents[2]` 推导项目根，不再写 `E:\m\game_bot`。

**P0 不做：** 不改 `_boss_*`、`battle_step`、走位/逃逸/商店逻辑；不改模板图；不改坐标数值；不引入自动缩放。

### P1：GUI 环境面板

- 显示 `APP_HOME`、`DATA_HOME`、当前游戏 profile 的实际 ADB、serial、主窗/渲染窗、分辨率。
- “环境自检”一键跑：当前 profile 的窗口存在 → 截图非黑 → 模板扫描 → OCR → 分辨率匹配。
- 每个 device_profile 的 ADB/窗口/串号支持“自动 / 手动”两种模式；同名窗口时下拉选择 HWND。
- 启动前校验当前游戏的分辨率；不匹配给出具体修改建议。

### P2：画面 profile / 校准（可选）

- 把 r1999 的坐标和模板尺寸收进 `r1999_profile.json`。
- 提供“重新校准”工具：在目标分辨率下重截模板、重算 D-pad/棋盘坐标。
- 校准时只改 profile，不改战斗代码；新旧 profile 可回退。

### P3：分发包（可选）

- `pyproject.toml` + `requirements.lock`；`python -m gamebot` / `gamebot-gui` 入口。
- `bootstrap.bat` 创建 `.venv` 并安装锁定依赖。
- 可选 PyInstaller 打包 GUI；**PaddleOCR 模型不强行塞进 exe**，首次运行下载或让用户指定 `GAMEBOT_MODEL_DIR`。
- 不建议把 MuMu/ADB 一起打包；ADB 应由配置指向本机模拟器安装目录，避免版本冲突。

## 5. 验收标准

### 静态

- 生产代码 `.py` 中 `E:\|C:\|D:\` 绝对路径为 0（归档版本和测试数据除外）。
- `Grep` 生产代码中的 `MuMu模拟器12`、`127.0.0.1:16384`、`E:\paddle_models` 只出现在默认配置/示例中，不出现在业务函数里。
- 所有模块 `py_compile` 通过。

### 运行

1. **同机回归**：不配置任何环境变量，默认值等价当前行为；GUI 启动、截图预览正常。
2. **换目录**：把整个项目复制到 `D:\portable_test\game_bot`，用 `GAMEBOT_DATA=D:\portable_test\data` 启动 GUI；不出现任何 `E:\m\game_bot` 路径错误。
3. **换用户名/Python**：用 `py -3.9` 或 `.venv` 启动；不依赖 `C:\Users\言\...`。
4. **换 ADB/串号（r1999 专用）**：把 `r1999_mumu` 的 adb 指向 r1999 专用 MuMu 的 adb、serial 指向该实例端口；GUI 环境自检显示的实际路径/串号必须是这一套，不能串到其他 MuMu。
5. **换窗口标题**：只改 `r1999_mumu` 的主窗/渲染窗标题，r1999 的 `Loop` 实际使用该标题（日志可验证），不再锚 `MuMu模拟器12`，也不影响其他游戏的 profile。
6. **两套 MuMu 同时存在**：r1999 启动/截图/点击/置前时，另一套 MuMu 不得被置前、截图或误点；窗口句柄和 ADB 串号必须可追溯到 `r1999_mumu`。
7. **分辨率不匹配**：故意把 r1999 专用 MuMu 调到非 1920×1080，启动应明确拒绝并提示，不应静默跑偏。
8. **只读目录**：把代码目录设为只读（或用 Program Files 模拟），`DATA_HOME` 应自动回落 `%LOCALAPPDATA%`，GUI 仍能保存配置/日志。
9. **行为回归**：P0 完成后先跑 1 局确认能进游戏/截图/走位；再按用户要求跑 5 局对照。P0 不应改变店数分布。

### 新机器验收（如果有第二台 Windows）

- 只复制项目 + 安装 Python 依赖，不复制 `E:\paddle_models`；首次启动按提示下载/指定模型。
- r1999 专用 MuMu 与其他 MuMu 的安装路径不同、窗口标题相同/不同各测一次。
- 100%/125%/150% DPI 各测一次截图与点击是否偏移。

## 6. 风险与边界

| 风险 | 说明 | 处理 |
|---|---|---|
| 改 r1999 边界时误伤战斗 | 3,299 行单文件，模块级常量被大量引用 | 只改顶部/初始化/截图/OCR/窗口尺寸；不碰 `battle_step`、Boss、走位 |
| 自动缩放模板改变命中率 | P12.6 的店数依赖精确模板/坐标 | P0 不做自动缩放；分辨率不匹配直接拒绝 |
| PaddleOCR 中文路径 | C++ 引擎读不了非 ASCII 模型路径 | 模型目录强制 ASCII 候选；不可用时明确提示并允许降级 |
| DPI/多显示器 | pyautogui/mss 坐标随缩放漂移 | 保持 `SetProcessDpiAwareness(2)`；新机器必须做截图/点击校验 |
| 两套 MuMu 同时存在 | 全局 `find_window_rect`/ADB/锁可能取错实例；r1999 专用机可能把另一套置前 | 每个游戏绑定 `device_profile`，exact 标题 + 进程/HWND 校验，按 profile 分 ADB/串号/锁；P0 先支持串行运行，并行输入隔离另做 |
| 管理员权限 | 当前 bat 自动提权 | 保留可选提权，但不因提权失败阻断 GUI；日志说明 |
| 跨 macOS/Linux | `ctypes.windll`、pyautogui、窗口 API、MuMu 都是 Windows 专用 | 不在本轮范围；如要做，需独立平台层，不能靠路径解耦 |

## 7. 建议的下一步

**先做 P0，不做 P2/P3。** P0 的边界清晰、可回退，且不改变 P12.6 的任何游戏行为。完成后用“复制到另一个目录 + GUI 环境自检 + 1 局回归”验证；确认无行为变化后，再决定是否做分辨率 profile 或分发包。

需要用户拍板的有两件事：

1. **移植目标是否就是“Windows + MuMu 12 + 1920×1080，换机器/换目录能跑”**？
   - 如果是：按 P0 做，核心改动集中在 8 个高频模块（runtime/ocr/lock/action/r1999/gui/main/diagnose），bbdc、deltaforce 和启动器属于外围适配，风险低。
   - 如果还要求“换分辨率也能跑”：P0 只能做到“检测并拒绝”，真正的自适应要加 P2 校准，工作量和测试量都会明显上升。
   - 如果要求跨 macOS/Linux：需要另立项目，不建议在当前 r1999 上直接改。
2. **r1999 专用 MuMu 和其他模块的 MuMu 是否需要同时运行两个 bot？**
   - 如果只串行：P0 按 profile 分锁即可，输入层仍按当前前台窗口串行。
   - 如果同时跑：路径解耦不够，pyautogui/键盘/前台窗口是全局资源，必须再做输入隔离或串行化；这会显著增加 P0 范围，建议单列。

## 8. 关键文件索引

- `E:\m\game_bot\games\r1999\r1999.py`：r1999 主逻辑；顶部 56-120 行常量、186-202 行 adb_cap、289-316 行 Loop 初始化、472-535 行窗口/截图、610-633 行坐标换算、3258-3295 行 R1999Script。
- `E:\m\game_bot\gui.py`：85-86 配置路径、147 日志目录、185 游戏下拉、195 默认窗口、510-512 task_order、716-720/835-851/1042-1058 游戏分支。
- `E:\m\game_bot\main.py`：22 sys.path、257 task_order、351/365 示例模板、418-446 窗口映射。
- `E:\m\game_bot\core\ocr.py`：32-61 USERPROFILE 重定向/模型迁移。
- `E:\m\game_bot\core\instance_lock.py`：19-20 锁路径。
- `E:\m\game_bot\core\action.py`：84-98 空格急停前台窗口判断。
- `E:\m\game_bot\core\screenshot.py`：79-140 窗口查找；147-206 Screenshot 初始化。
- `E:\m\game_bot\core\game_bot.py`：36-67 模板目录推导。
- `E:\m\game_bot\games\bbdc\bbdc.py`：92-98 ADB 候选、185-190 进度路径、211-238 ADB 封装。
- `E:\m\game_bot\games\deltaforce\deltaforce.py`：52-58 窗口/模板、394-397 调试截图。
- `E:\m\game_bot\启动GUI.bat`：4、15、29 行。
- `E:\m\game_bot\启动r1999挂机.bat`：4、7 行。
- `E:\m\game_bot\temp\tools\*.py`：测试脚本绝对路径。
- `E:\m\game_bot\README.md`：156 行 E:\MuMuPlayer 说明。
