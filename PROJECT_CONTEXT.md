# r1999 项目上下文摘要（当前状态 2026-11-23）

> 2026-11-23 整理：正式版已切到 **P12.6 + GUI 状态钩子**；下方第 0-9 节为 2026-11-20 历史交接，其中 P15/5a 的“当前主文件”结论已过时，仅作历史参考。

## 当前状态（2026-11-23，优先看本节）

- 当前主文件（便携化后）：`E:\m\game_bot\games\r1999\r1999.py`，168,678 bytes，SHA256 `b2729b61600f7449021febf1306cdb16b0b10a2b6e12da92e01205ed2fdca144`；已做运行时路径/ADB/窗口/串号适配，**battle 逻辑仍为 P12.6**。
- 便携包（2026-11-23）：`E:\m\bao\temp\r1999_portable_20261123\r1999_portable_20261123.zip`（475.6 MB）；冻结 exe SHA256 `a361e1afc95ca5c0a7c560bd5c74511e68d2e6630e4de251c4961f2c154be656`；自检/OCR smoke/GUI smoke 均 PASS，详见 `docs\PORTABLE_PACKAGE_20261123.md`。
- 精简便携包（2026-11-24）：`E:\m\bao\temp\r1999_portable_lite_20261124\r1999_portable_lite_20261124.zip`（243.1 MB，解压 663.2 MB）；自检/OCR smoke/GUI smoke 均 PASS；构建脚本 `packaging\build_r1999_lite.py`，详见 `docs\PORTABLE_PACKAGE_LITE_20261124.md`。
- 行为基线：**P12.6 最高店数版**；GUI 钩子只增加 `status_cb` / `_emit_status`，不改变游戏行为。
- 旧主文件回退包：`temp\r1999_versions\r1999_7br6_pretest_20261123.py`（178,525 bytes，SHA1 `87ef6c7d4dc811b30fbe71f59e0d7e17b25972d2`）。
- 原始 P12.6 基线：`temp\r1999_versions\r1999_p12_6_exit_20261119.py`（166,486 bytes，SHA1 `257cb9567eb8583ac88b1d3735991fb308f4bfa5`）。
- 最新测试：P12.6 十局 **51 店 / 10 局 = 5.10 店/局**，平均存活 504s，0 店 0/10，≥5 店 4/10；十局墙钟约 1.46h，按每店 10 分约 510 分，约 **349 分/小时**。
- 稳定性判断：P12.6 仍是当前最高均值，但 CV 约 53.7%，**不能称为稳定 5 店**；用于 2000 分目标可以，按均值约 39 局 / 5.7 小时。
- GUI：`gui.py` 未改；仍支持按局数/积分目标停止，`r1999_gui.json` 当前 `rounds=5, score=0, mode=hybrid, goal=rounds`。
- 正式报告：`docs\reports\P12_6_10局稳定性对照_20261123.md`、`r1999_P12_6_vs_7bR6_10局对照_20261123.md`、`r1999_P12_6_GUI_vs_dynboss_5局对照_20261123.md`。
- 测试总记录：`temp\r1999_test_records.md`。
- 关键证据与积分 CSV：`temp\evidence\`；旧日志、商店截图、死亡录像和旧探针已清理，详见 `docs\CLEANUP_20261123.md`。
- 版本目录只保留正式版、原始 P12.6、7b+R6 回退包和少量历史基线；不要再把 P15/P14 补丁链当候选。

## 0. 一分钟结论（历史，2026-11-20）


- 当前主文件：`E:\m\game_bot\games\r1999\r1999.py`，183,145 bytes，约 3,600 行，当前内容是 **P15（含 P14）**。
- 当前 GUI：`E:\m\game_bot\gui.py`，50,329 bytes；默认 5 局，`goal=rounds`，`score=0`，`mode=hybrid`。
- 最近测试 **5a（P14+P15，5 局）**：`186s/1店、142s/1店、466s/4店、471s/4店、64s/0店`，合计 **10 店 / 5 局 = 2.0 店/局**，明显退步。
- 历史最高：**P12.6，41 店 / 7 局 = 5.86 店/局，单局最高 14 店**。但它没有 GUI 状态钩子，不能直接裸回滚到 GUI 使用。
- GUI 兼容的第二高：**P13，35 店 / 7 局 = 5.00 店/局**。
- 当前最需要修的两个根因：**出红区后过早解除逃逸，下一帧又走回去**；**边缘护栏被红区/逃逸条件绕过，且使用虚拟坐标而不是屏幕玩家格**。
- 5a 之后没有修改主代码；P16 仍是建议，未实现。

## 1. 目标、验收与硬约束

### 目标

- 单局尽量多店，稳定达到 **平均 5 店/局**，避免 0 店短局。
- 每局必须跑到 GAME OVER，禁止中途停止、边跑边改。
- GUI 可选择：按局数停止、按积分停止、按任一条件停止；测试当前默认 5 局。
- GUI 中 `hybrid` 表示左右交替 + 周期冲刺；另支持 `strafe`、`random`。
- Boss 逻辑：
  - Boss1 保留环带打法，可以叠加屏幕红区逃生。
  - Boss2/3 不用 Boss1 环带，不搞固定 3×3 中心禁区，禁区跟随 Boss 本体移动。
  - Boss3 有左/中/右三条冰带，冰上不被火技能烧到；当前代码按列 `1/8/13` 处理。

### 用户明确拒绝

- 不做血量侦测。
- 不给 Boss2/3 套 Boss1 的环带或固定中心逻辑。
- 不做无目标词条盲买。
- 不中途停测、不在测试中改代码。
- 不再继续在 P14/P15 上无依据地叠补丁。

## 2. 运行环境

- 系统：Windows；模拟器窗口：`MuMu模拟器12`。
- **重要：r1999 使用专用 MuMu 实例，与其他模块（bbdc/deltaforce 等）的 MuMu 不是同一套；设备配置必须按游戏绑定 `DeviceProfile`，不能共用全局窗口/ADB/串号。**
- ADB：`127.0.0.1:16384`；ADB 与 MuMu 路径在本机多套，实际使用项目内封装的 `adb_tap/adb_frame`。
- Python：必须用 `C:\Users\言\AppData\Local\Programs\Python\Python39\python.exe`（已确认 cv2 4.11.0）；系统默认 `python` 可能没有 cv2。
- 画面基准：约 `1930×1139`；棋盘 15×15，格宽约 60.3、格高 60，棋盘左上 `x=515, y=145`。
- 当前代码已通过 `py_compile`：`r1999.py`、`gui.py`、`run_gui_5_round.py`。

启动 GUI：

```powershell
cd E:\m\game_bot
& 'C:\Users\言\AppData\Local\Programs\Python\Python39\python.exe' gui.py
```

GUI 参数保存在 `E:\m\game_bot\r1999_gui.json`，当前：

```json
{"rounds": 5, "score": 0, "mode": "hybrid", "goal": "rounds"}
```

## 3. 代码结构

### 文件职责

| 文件 | 作用 |
|---|---|
| `games\r1999\r1999.py` | r1999 主逻辑，`Loop` 感知/决策/执行；`R1999Script` 接任务引擎 |
| `gui.py` | 通用 GUI；r1999 参数面板、状态队列、启动/停止 |
| `main.py` | CLI/任务入口；GUI 之外可用 |
| `r1999_gui.json` | GUI 参数持久化 |
| `templates\` | 场景、按钮、D-pad、商店词条、Boss 等模板 |
| `core\` | 通用截图、动作、任务引擎 |
| `temp\` | 测试日志、死亡录像、版本归档、临时脚本 |

### 主逻辑关键常量与环境变量

- `MAX_RUNTIME = 3 * 3600`；`DEFAULT_TEST_RUNS = 0`；`DEFAULT_SCORE_TARGET = 0`。
- `R1999_MAX_RUNS`：局数出口；`R1999_SCORE_TARGET`：积分出口；`R1999_NO_DANGER_MODE`：`hybrid/strafe/random`。
- `R1999_WINDOW = "MuMu模拟器12"`。

### `Loop` 主要状态

- `max_test_runs` / `score_target`：GUI 注入的停止目标。
- `runs` / `run_shops` / `mode_stats`：逐局统计。
- `status_cb`：GUI 实时状态回调；CLI 为 `None`。
- `escape_commit`：跨帧逃逸承诺 `(dir, box, until, left)`。
- `momentum`：逃逸后的惯性。
- `screen_exit_dir/left/until`：屏幕红区闩锁。
- `last_commit_dir/box/until`：旧逃逸方向/框/有效期。
- `_boss_fight_count` / `_boss_encounter_active`：本局 Boss 序号。
- `_boss_dynamic_bbox`：Boss2/3 动态本体禁区。
- `spc` / `last_pc` / `battle_moves`：屏幕玩家格、上次虚拟坐标、移动记录。

### `battle_step` 决策链（当前实际顺序）

1. 截屏、识别屏幕玩家格 `spc`；成功则重定位虚拟 `pi,pj`。
2. 检测白色环/白格、红色格、危险区、玩家脚下红区。
3. Boss 模式：Boss1 固定中心 `(7,7)` + 环带；Boss2/3 动态本体；Boss3 冰带。
4. `escape_commit`：逃逸承诺；当前 `free = not in_live`。
5. `momentum`：逃逸惯性。
6. 脚下/附近红区：BFS 逃逸，建新承诺。
7. 邻格危险：危险规避。
8. 常规：`hybrid`/`strafe`/`random` 走位。
9. 边缘护栏：只在 `not in_live and not near2 and not near_danger and not escape_commit and not momentum` 时执行。
10. 屏幕红区安全层：最后覆盖 `move_dirs`。
11. `_move_one` 按压 + 抓帧确认。
12. DIAG 死前录像。

### 关键函数

| 函数 | 作用 |
|---|---|
| `battle_step` | 战斗单帧主决策，包含所有安全层 |
| `_best_path` | BFS 路径；考虑危险、怪物、白格软代价 |
| `_pick_strafe` / `_pick_hybrid` / `_pick_hunt` / `_pick_random_walk` | 常规走位、追怪、随机 |
| `_screen_player_cell` | 找屏幕玩家黄括号格 |
| `_red_short_axis_dir` | 3×7/7×3 窄长红块短轴出口 |
| `_screen_red_escape` | 屏幕坐标红区安全首步 |
| `_move_one` | ADB D-pad + 帧确认 + 虚拟坐标更新 |
| `handle_shop` | 商店 OCR、买卡、刷新、出店 |
| `handle_hub` / `handle_unrecognized` / `handle_banner` | 大厅、异常、公告处理 |
| `run` | 场景循环、逐局统计、停止目标、死前录像 |
| `R1999Script._run_loop` | 接任务引擎；`max_runs/score_target/escape/danger/space` 抛 `StopRequested` |

## 4. 当前问题（按优先级）

### 问题 A：出红区后又转回去（第一优先）

**现状**

- 约第 2538 行：`free = not in_live`；只要脚下这一格不在活红区，就立刻清 `escape_commit`。
- 约第 2570 行：`momentum` 也随即失效，下一帧进入常规选路。
- 约第 2585 行：`last_commit_dir` 的“不回头”只在 `in_live or near2` 时生效，出区后失效。
- 常规 `_pick_hybrid` / `_pick_strafe` 会朝商店、怪或中心移动，直接反向走回红区。

**5a 证据**

- `逃逸完成(方向a) → 停止惯性，恢复常规走位`
- 下一步：`路径为空 → 强制横向移动 d`
- 再下一步：`屏幕红区优先跳过: a 目标仍危险/占用`，但实际仍按旧方向走。

**建议修法（P16-A）**

1. `free` 改为：`not in_live` **且 2 格内没有活红区**。
2. 解除后写 `red_release_dir=d`、`red_release_until=now+1.5s`。
3. 冷却期内，常规选路若选择 `opposite(d)`，直接否决。
4. BFS 目标加 1 格红区缓冲，不选“贴红区边缘”的安全格。
5. 红区重新压来时允许重新逃，但禁止反向走回旧红区。

### 问题 B：边缘检测不出来，一直朝边走

**现状**

- 约第 2863 行边缘护栏条件包含 `not in_live`、`not near2`、`not near_danger`、`not escape_commit`、`not momentum`；只要在红区/逃逸/危险中，护栏完全不执行。
- 护栏用虚拟 `pi,pj`；屏幕玩家格 `spc` 才更接近实际。
- 玩家贴边时 `spc` 可能丢失；日志出现过 `视觉重定位 (13,7) → 屏幕格(12,7)`，说明坐标有漂移。
- 屏幕红区安全层最后执行，可能覆盖边缘护栏，重新按向边界。

**建议修法（P16-B）**

1. `effective_pc = spc if spc_detected else last_screen_pc`。
2. 边缘护栏移到**屏幕红区层之后**，独立于红区/危险状态执行。
3. 只要 `x=0/14` 或 `y=0/14`，禁止朝该边的方向；朝内是红区时走沿边安全切向。
4. `spc` 丢失且上次位置贴边时，进入 1 秒 `edge_lock`，强制朝内直到重新识别。
5. 朝内和切向都被红区堵死时，选离红区最远的格，但绝不按朝外方向。

### 问题 C：白格 + 红格叠加误判，屏幕逃逸目标不安全仍按旧方向

**5a 证据**

- 局5 `64s / 0店`，红区识别一度 **65 格**、白格 34 格；活区只剩 1 个甚至 0 个。
- 日志：`屏幕红区优先跳过: d 目标仍危险/占用`，但 `move_dirs` 仍沿用旧的 `d`。
- 代码约第 2933 行：目标不安全只打日志，没有清空/重选 `move_dirs`。
- 红区检测按 40×40 格中心均值，阈值 `r-g>20`、`r-b>20`；粉色白格边缘会被算成红。
- 红区面积 `>80` 才整帧丢弃；65 格不会被丢弃。

**建议修法（P16-C）**

1. 屏幕逃逸目标不安全时，必须清空 `move_dirs`，重新选安全邻格；不能只打日志。
2. 红区检测增加红像素占比和白色占比：白主导格不并入红区硬危险；白格继续作为软代价。
3. 记录 `red/white` 计数，避免再次出现“白格把红区撑到 65 格”。

### 问题 D：Boss2/Boss3 尚未在当前版本实战验证

- P15 只给 Boss1 打开屏幕红区逃生层；Boss2/3 仍按 P12.8 动态本体。
- 5a 只观测到 Boss1 局，Boss2/3 未出现。
- Boss3 冰带列 `1/8/13` 已实现，但还没有在 P14/P15 下回归。
- 不能把 Boss1 的环带/固定中心直接套给 Boss2/3。

### 问题 E：测试波动与统计口径

- 5a 只有 5 局，样本小；单局长局会把均值拉高。
- 以日志里的 `📊 实验` / `📊📊 走位` 为准；GUI 的 `total_shops` 字段会随当前局/统计集合变化，只作参考。
- P12.6 的 5.86 也受 14 店长局影响；不能只看一次平均就认定稳定。

## 5. 测试记录摘要

### 关键版本对比

| 版本 | 有效结果 | 场均 | 备注 |
|---|---:|---:|---|
| **P12.6** | **41 / 7** | **5.86** | 最高；单局 14 店；含 98s/2、49s/0 两个 Boss 短局；无 GUI 状态钩子 |
| P13 | 35 / 7 | 5.00 | GUI 兼容；有 60s/0 清怪短局 |
| 7i | 31 / 7 | 4.43 | 无 0 店局；P14 修复前基线 |
| P10 | 30 / 7 | 4.29 | 含 11 店长局；低店局多 |
| GUI 第一轮 | 29 / 7 | 4.14 | GUI 目标版首轮 |
| P9 | 20 / 5 | 4.00 | 白格软代价强化 |
| 5a（P14+P15） | 10 / 5 | 2.00 | 当前主文件；明显退步 |

早期 P2-P8 场均约 2.6-4.0，已被后续版本覆盖，不再作为候选。

### P12.6 明细

`(98s,2店) (348s,4店) (680s,7店) (49s,0店) (801s,7店) (726s,7店) (1492s,14店)`  
合计 41 店 / 7 局 = 5.86；平均存活 599s。

### P13 明细

`(629s,6店) (822s,7店) (336s,4店) (60s,0店) (506s,5店) (1158s,7店) (563s,6店)`  
合计 35 店 / 7 局 = 5.00；有 1 个 0 店清怪短局。

### 7i 明细

`(265s,3店) (427s,5店) (246s,2店) (285s,3店) (422s,4店) (327s,3店) (1259s,11店)`  
合计 31 店 / 7 局 = 4.43；无 0 店局。

### 5a 明细（当前版本）

`(186s,1店) (142s,1店) (466s,4店) (471s,4店) (64s,0店)`  
合计 10 店 / 5 局 = 2.00；平均存活 266s；`stop_reason=max_runs`。

死亡分布：

- 局1：清怪，HP1
- 局2：坚持6回合，HP7
- 局3：坚持51回合，HP13
- 局4：Boss1/清怪混合，HP5，4店
- 局5：清怪，HP5，0店；白格+红格叠加，红区 65 格，贴边死亡

### 日志与录像

- 5a：`temp\r1999_gui_5a.out`、`r1999_gui_5a.err`、`r1999_gui_5a_status.jsonl`
- 7i：`temp\r1999_gui_7i.out`、`.err`、`_status.jsonl`
- 5a 第5局死前录像：`temp\r1999_death_233452\`
- 5a 其他死亡录像：`r1999_death_231439`、`231719`、`232519`、`233329`
- 测试记录：`E:\m\game_bot\temp\r1999_test_records.md`

## 6. 版本备份与回滚风险

归档目录：`E:\m\game_bot\temp\r1999_versions\`，当前只保留 6 个关键版本。

### 关键备份（2026-11-23 清理后）

| 文件 | 大小 | 用途 |
|---|---:|---|
| `r1999_p12_6_gui_20261123.py` | 167,784 | 当前正式版：P12.6 + GUI 状态钩子 |
| `r1999_p12_6_exit_20261119.py` | 166,486 | 原始 P12.6 基线，无 GUI 钩子 |
| `r1999_7br6_pretest_20261123.py` | 178,525 | 7b+R6 回退包 |
| `r1999_p13_white_heal_20261120.py` | 177,278 | P13 历史参考（5.00 店/7局） |
| `r1999_p12_6_dynboss_20261123.py` | 173,852 | B 实验版；动态 Boss 未实测 |
| `gui_r1999_5rounds_20261120.py` | 50,329 | GUI 备份 |

### 回滚注意

- **不能直接把 P12.6 复制成当前主文件**：P12.6 没有 `status_cb`、`_emit_status`，GUI 会拿不到实时状态/停止进度。
- 如果一定要 P12.6 的性能，必须先把 GUI 状态钩子、停止原因、`R1999_MAX_RUNS` 路径端口到 P12.6。
- 更稳妥的 GUI 兼容基线是 **P13**，但它只有 5.00，且有 1 个 0 店短局。
- 当前 P15 已归档；任何回滚前先备份当前 `r1999.py` 和 `gui.py`，不要覆盖用户手动改动。

## 7. 下一步建议（未执行）

### 方案选择

1. **店数优先**：以 P12.6 为行为基线，端口 GUI 状态钩子，再做 P16-A/B/C。
2. **GUI 直接可用**：以 P13 为基线，先做 P16-A/B/C。
3. 不建议继续在 P14/P15 上叠补丁；当前 5a 2.0 已经说明补丁链方向不对。

### P16 最小改动

- A：逃逸解除改为“红区外 2 格 + 1.5s 反向冷却”。
- B：边缘护栏移到最后，使用屏幕玩家格/上次屏幕格，贴边禁止朝外。
- C：屏幕逃逸目标不安全时清空旧方向重选；红区检测排除白主导格。
- 先做 A+B，确认短局改善，再决定是否动红区检测；一次不要同时改太多层。

### 验证口径

- 5 局全部到 GAME OVER，不中途停。
- 目标：平均 ≥5 店，至少没有 0 店局。
- 若 5 局稳定，再跑 7 局；不要用 1-2 局下结论。
- 每局记录：存活秒数、商店次数、死亡场景、HP、是否 Boss、红区/白格峰值、死前最后 3 个方向。
- 测试中不改代码；测试后把结果追加到 `temp\r1999_test_records.md`。

## 8. 文件地图（排障先看这里）

| 目的 | 路径 |
|---|---|
| 当前主逻辑 | `E:\m\game_bot\games\r1999\r1999.py` |
| 当前 GUI | `E:\m\game_bot\gui.py` |
| GUI 参数 | `E:\m\game_bot\r1999_gui.json` |
| 测试记录 | `E:\m\game_bot\temp\r1999_test_records.md` |
| 最新对照报告 | `E:\m\game_bot\docs\reports\` |
| GUI 测试脚本 | `E:\m\game_bot\temp\tools\run_gui_5_round.py` |
| 复现 P12.6 十局 | `E:\m\game_bot\temp\tools\run_p12_6_10.py` |
| 关键日志/积分 CSV | `E:\m\game_bot\temp\evidence\logs\` |
| 关键截图/最后一局录像 | `E:\m\game_bot\temp\evidence\shots\`、`temp\evidence\death_p12_6_final\` |
| 版本归档 | `E:\m\game_bot\temp\r1999_versions\`（6 个关键版本） |
| 历史文档 | `E:\m\game_bot\docs\history\` |

## 9. 未决问题（2026-11-23）

1. P12.6 的均值 5.10 店已确认，但方差仍大，尚未达到“稳定 5 店”；后续是否继续修短局需要单独立项。
2. P12.6 十局未开启积分读取，510 分是按历史日志“每店 +10”换算；如要严格核算，需要跑一局带 `R1999_SCORE_TARGET` 的读分验证。
3. Boss2/Boss3 动态本体只在 B 实验版实现，尚未在实战中触发；不能把 B 的 3.60 店/5局当作动态 Boss 修复证据。
4. GUI 当前仍是 5 局、积分目标 0；正式挂机前按需要改 `r1999_gui.json`。
5. GUI `total_shops` 字段与日志口径不完全一致，验收以 `📊` 日志为准。
