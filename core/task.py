"""
Task 数据类 — 独立模块，消除对 main.py 的循环依赖。
"""

from dataclasses import dataclass, field
from typing import Optional, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from core.game_bot import GameBot


@dataclass
class Task:
    """
    一个游戏自动化任务。

    支持两种模式：
    - 模板模式：指定 template，引擎匹配并点击
    - 回调模式：指定 callback，引擎执行回调函数（用于复合流程）

    属性
    ----
    name : str        显示名称，如 "成长奖励领取"
    template : str    模板文件名（回调模式下可为空字符串）
    repeat : bool     是否重复触发
    threshold : float 匹配阈值
    cooldown : float  重复触发的冷却时间（秒）
    callback : callable | None  复合任务回调，接收 GameBot 返回 bool
    done : bool       本次运行是否已完成
    """
    name: str
    template: str = ""
    repeat: bool = False
    threshold: float = 0.85
    cooldown: float = 0.0
    callback: Optional[Callable[["GameBot"], bool]] = field(default=None, init=True, repr=False)
    enabled: bool = True   # 是否启用（GUI 复选框控制）
    done: bool = field(default=False, init=False, repr=False)
    _last_triggered: float = field(default=0.0, init=False, repr=False)

    def reset(self):
        """重置完成状态和冷却计时（不改变 enabled）。"""
        self.done = False
        self._last_triggered = 0.0
