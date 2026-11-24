---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1df91ad2acf5cb5214f3001c0d877bb7_d1ce7f3081d411f18a64525400826444
    ReservedCode1: g6by9fH8VTEjhUFCJs2i6dM9c+xo8OOkbqwzP8SMEKej2WxCt00pLnuFxh5LIDzmYMG08NYz2UCaD8f53XCYGDRNSFyEcwPqE89y+ByMu3PxTaW3ndSOvzOfZ5ytNKb79iqiujjJieB9kHr3svlUk+HBSxOj0dVkXQikoAnbUIh22mXB6OjVvD7HTjw=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1df91ad2acf5cb5214f3001c0d877bb7_d1ce7f3081d411f18a64525400826444
    ReservedCode2: g6by9fH8VTEjhUFCJs2i6dM9c+xo8OOkbqwzP8SMEKej2WxCt00pLnuFxh5LIDzmYMG08NYz2UCaD8f53XCYGDRNSFyEcwPqE89y+ByMu3PxTaW3ndSOvzOfZ5ytNKb79iqiujjJieB9kHr3svlUk+HBSxOj0dVkXQikoAnbUIh22mXB6OjVvD7HTjw=
---

# Game Bot — 游戏自动化脚本框架

基于 Python 的手游日常任务自动化框架，支持 MuMu 模拟器中运行三国杀、重返未来1999、无期迷途等游戏。

## 功能特性

- **截图定位** — 基于窗口标题自动定位模拟器窗口，截取指定区域
- **模板匹配** — 基于 OpenCV 的单模板 / 多模板匹配，支持 NMS 去重
- **文字 OCR** — 基于 PaddleOCR 识别屏幕文字和位置
- **模拟操作** — 基于 pyautogui 的点击、拖拽、按键、输入
- **流程编排** — 高层 API 封装，`wait_and_click` / `click_if_found` / `find_and_click_text` 等

## 项目结构

```
game_bot/
├── main.py              # 主入口 & 流程编排
├── requirements.txt     # 依赖清单
├── README.md            # 本文件
├── core/
│   ├── __init__.py
│   ├── screenshot.py    # 截图模块 (mss)
│   ├── matcher.py       # 模板匹配 (opencv-python)
│   ├── ocr.py           # OCR 模块 (paddleocr)
│   ├── action.py        # 操作模块 (pyautogui)
│   └── game_bot.py      # 核心控制器 (整合以上)
└── templates/           # 存放模板图片 (.png)
```

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

> **注意**：PaddlePaddle 和 PaddleOCR 首次安装可能较慢。如遇安装问题，可参考：
> - PaddleOCR: https://github.com/PaddlePaddle/PaddleOCR
> - 如仅需模板匹配（不使用 OCR），可注释掉 `core/ocr.py` 和 `core/game_bot.py` 中的 OCR 相关导入

### 2. 准备模板图片

在 `templates/` 目录下放置需要匹配的界面元素截图：

```
templates/
├── start_button.png     # 开始按钮
├── confirm.png          # 确认按钮
├── close.png            # 关闭按钮
├── back.png             # 返回按钮
├── claim_all.png        # 一键领取
├── daily_quest.png      # 每日任务入口
└── ...
```

#### 如何截取模板图？

1. 启动 MuMu 模拟器，打开目标游戏
2. 使用 MuMu 自带的截图工具或 Windows 截图 (Win+Shift+S)
3. 用画图或任意图片工具裁剪出目标 **按钮 / 图标 / 文字区域**
4. 保存为 `.png` 放入 `templates/` 目录

> **最佳实践**：
> - 模板图尽量裁剪精确，减少背景干扰
> - 保持模板图与实际游戏中的尺寸一致
> - 模板文件名用英文描述，便于在代码中引用

### 3. 运行

```bash
# 运行通用示例流程
python main.py

# 运行三国杀日常（占位示例）
python main.py --game sanguosha

# 指定模拟器窗口标题（如 MuMu12）
python main.py --window "MuMu12"

# 调整匹配置信度阈值
python main.py --threshold 0.9
```

## 核心 API 速览

```python
from core.game_bot import GameBot

bot = GameBot(window_title="MuMu", template_dir="templates")

# 等待模板出现并点击（超时 10 秒，每 0.5 秒检测一次）
bot.wait_and_click("templates/confirm.png", timeout=10, interval=0.5)

# 如果找到就点击，否则跳过
bot.click_if_found("templates/start.png")

# 等待元素消失
bot.wait_until_gone("templates/loading.png", timeout=30)

# OCR 查找文字并点击
bot.find_and_click_text("领取", fuzzy=True)

# 等待
bot.sleep(2)

# 直接使用底层操作
bot.action.press("esc")           # 按键
bot.action.click(100, 200)        # 点击坐标
bot.action.drag(0, 500, 0, 200)  # 滑动
```

## 自定义游戏流程

在 `main.py` 中仿照 `run_sanguosha()` 编写你的流程函数：

```python
def run_my_game(bot: GameBot):
    """我的游戏日常"""
    bot.sleep(3)
    bot.click_if_found("templates/daily_entry.png")
    bot.wait_and_click("templates/claim.png", timeout=5)
    # ... 更多步骤
    logger.info("日常任务完成")
```

然后在 `main()` 中添加命令行选项即可。

## 不背单词：批量收藏单词

按单词表在 MuMu 的不背单词 App 里批量搜索并收藏：主界面下滑打开搜索 →
输入单词 → OCR 校验第一个搜索结果 → 点该行星标 → 在「将单词收藏至」弹层中
点「生词本」→ 星标变橙色实心即成功 → 点 × 清空 → 下一个单词。

```bash
# 把单词写进项目根目录 words.txt（一行一个，序号/中文释义会自动清洗）
python main.py --game bbdc --limit 3     # 先试跑 3 个词，确认点击位置正确
python main.py --game bbdc               # 全量运行
python main.py --game bbdc --fresh       # 忽略 temp/bbdc_done.txt 进度重跑
```

说明：

- **文本输入走 adb**：MuMu 对该 App 开了按键映射，Windows 虚拟按键打不进
  字。脚本自动使用 MuMu 自带 adb（`E:\MuMuPlayer\nx_device\12.0\shell\adb.exe`
  或 PATH 中的 adb，connect 127.0.0.1:16384）注入文本
- 进度写在 `temp/bbdc_done.txt`（中断后重跑会自动跳过已收藏的词），
  失败的词写在 `temp/bbdc_failed.txt`；失败现场截图在
  `temp/bbdc_debug_*.png`
- 已收藏的词（星标橙色实心）会被识别并跳过，不会点成取消收藏
- 界面定位优先用 `templates/bbdc/` 下的可选模板，没有模板时用
  比例坐标 + OCR 自动定位（1601x900 窗口实测校准）；点击不准时微调
  `games/bbdc/bbdc.py` 顶部常量
- 空格键紧急停止（与三国杀脚本一致）

## 环境要求

- Windows 10 / 11
- Python 3.9+
- MuMu 模拟器（或其他支持窗口句柄的模拟器）

## 注意事项

- 运行前确保模拟器窗口可见（不要最小化）
- 模板匹配受分辨率影响，建议固定模拟器窗口大小
- pyautogui 的 FAILSAFE 已开启：将鼠标快速移到屏幕左上角 (0,0) 可紧急停止脚本
- OCR 首次运行会下载模型文件，请保持网络畅通
*（内容由AI生成，仅供参考）*
