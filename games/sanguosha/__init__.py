"""
三国杀脚本包。

由原 games/sanguosha.py 拆分重构而来。为保持 main.py / gui.py 兼容，
此处导出原模块顶层符号：SanguoshaScript（游戏脚本类）与 HookLossError（钓鱼脱钩异常）。
"""

from .sanguosha import SanguoshaScript
from .constants import HookLossError

__all__ = ["SanguoshaScript", "HookLossError"]
