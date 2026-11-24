"""
GameScript 基类 — 每个游戏继承此类，定义自己的启动流程和任务列表。

职责：
- get_tasks()    : 返回该游戏的任务列表
- startup(bot)   : 返回 True 表示启动清理成功，可继续执行任务
- name           : 游戏名称（用于模板子目录）

这使得 execute_tasks（引擎）完全通用，不再包含任何游戏特定逻辑。
"""

from abc import ABC, abstractmethod
from typing import List

from core.task import Task
from core.game_bot import GameBot


class GameScript(ABC):
    """游戏脚本基类。"""

    @property
    @abstractmethod
    def name(self) -> str:
        """游戏名称，用作模板子目录名。"""
        ...

    @abstractmethod
    def get_tasks(self) -> List[Task]:
        """返回该游戏的任务列表。"""
        ...

    @abstractmethod
    def startup(self, bot: GameBot) -> bool:
        """
        启动清理流程：签到 → 关公告 → 回主界面。

        返回 True 表示清理成功，引擎继续执行任务；
        返回 False 表示失败，引擎中止。
        """
        ...
