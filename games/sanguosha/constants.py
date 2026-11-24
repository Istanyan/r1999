"""
三国杀脚本共享常量与工具。

由原 games/sanguosha.py 拆分而来：
- 模块级日志 logger / _warn（原文件第 24-31 行）
- HookLossError 异常类（原文件第 33-36 行）
- SanguoshaScript 类级常量 HOOK_LOSS_TIMEOUT / FISHING_IDLE_POLL / ENERGY_COOLDOWN
"""

import logging

logger = logging.getLogger("GameBot")


def _warn(msg: str):
    """标红日志：未检测到某步骤时使用。"""
    logger.error(msg)


class HookLossError(Exception):
    """鱼脱钩：收线过快导致鱼逃跑，本次钓鱼被终止，需人工介入。"""


# ── 弹窗清理（启动/回主界面共用）──────────────────
# 活动弹窗底部中央的 × 关闭按钮（2026-08-29 新增：多层活动弹窗的顶层无返回箭头，
# 唯一关闭方式是 ×；返回箭头属于下层页面，点击被遮罩吞掉）
POPUP_CLOSE_X = "guanbi_x.png"
POPUP_CLOSE_X_THRESHOLD = 0.80
# 主界面判定阈值（chengzhang 双态模板：干净主界面均 1.000，弹窗遮罩下均 ~0.75）
# chengzhang.png=可领取态(红点+底板) chengzhang2.png=空闲态(领完奖后无底板)
MAIN_SCREEN_THRESHOLD = 0.85
CHENGZHANG_ALT = "chengzhang2.png"
# 灰度辅助通道阈值（用户要求"忽略颜色"）：离线实测灰度会把好友等金色图形
# 按钮误匹配到 1.000，故阈值显著抬高，仅作彩色分被顶栏横幅扫过干扰时的兜底
MAIN_SCREEN_GRAY_THRESHOLD = 0.90
# 主界面 OCR 兜底：底部菜单区域的起始 y（"成长/商城/特惠…"一栏）
MAIN_MENU_REGION_Y = 650
# 主界面独有的大按钮文字（经典场/排位赛 只有主界面有；百趣闲市等子页面
# 底部菜单也有"成长"但绝无这两个按钮——OCR 双文字确认防跨页面误判）
MAIN_SCREEN_VENUE_MARKERS = ("经典场", "排位赛")
# 全屏活动页特征文字（出现即判"被活动页覆盖",防 OCR 只看底部菜单误判主界面
# ——run17: 赵云首发页盖住主界面,菜单栏从遮罩下露出导致误判+点穿进充值页）
ACTIVITY_MARKERS = ("前往获取", "立即参与", "立即获取", "前往参与", "限时上架")
# 未知确认弹窗的取消按钮（dismiss_popups 兜底手段：只点取消绝不点确定，
# 避免误确认不可逆操作；战斗流程内的"敌人过于强大"确认框同样只点取消，
# 由 _zz_battle_flow 返回 weak，禁止强行开战）
DIALOG_CANCEL = "quxiao.png"
DIALOG_CANCEL_THRESHOLD = 0.85
# 体力购买确认框的取消按钮（竖屏商店页专属竖排样式；误入商店时由 dismiss 关闭）
PURCHASE_CANCEL = "quxiao2.png"
PURCHASE_CANCEL_THRESHOLD = 0.80
# 成长模块顶栏保护区：宝箱/元宝等小图标模板在顶栏货币"加号"区域的假匹配
# 会误开充值商店（2026-08-30 run10/11 实测），命中位置低于此 y 一律忽略
# 2026-08-31 阈值由 300 调低至 100：jiaoshui.png 底栏真实位置 y≈137 被误伤忽略
GROWTH_TOPBAR_MIN_Y = 100

# 鱼脱钩判定：收线+蓄力均消失，超过该秒数仍未恢复 → 判脱钩终止本次钓鱼。
# 该窗口需覆盖终结一击的 QTE 恢复时间（实测 5s 仍偏短，整体放宽到 8s）。
HOOK_LOSS_TIMEOUT = 8.0
# 收线/蓄力均消失时的重新检测间隔（秒），越小收线重现后恢复越快
FISHING_IDLE_POLL = 0.2
# 蓄力冷却：蓄力不可见后暂停收线的额外缓冲（秒）。越小越接近"一直点"
ENERGY_COOLDOWN = 0.2


# ═══ 合成大将军小游戏（合成大西瓜类）═══
# 进入/通用模板匹配阈值
JJ_ENTER_THRESHOLD = 0.85
# 锚定定位1/定位2 的匹配阈值
JJ_ANCHOR_THRESHOLD = 0.85
# 锚定失败重试上限（次）
JJ_ANCHOR_RETRIES = 3
# 开始游戏1 点击重试上限（次）
JJ_START_RETRIES = 3
# 主循环低频点击间隔（秒）
JJ_CLICK_INTERVAL = 1.0
# 切换落点后的警戒线响应冷却（秒）
JJ_SWITCH_COOLDOWN = 10.0
# 主循环超时保护（秒），避免异常场景下死循环
JJ_PLAY_TIMEOUT = 300.0
# 领奖1 检测阈值（高置信度）
JJ_CLAIM_THRESHOLD = 0.99
# 最大局数（2026-08-30 用户确认：领取奖励即完成；无领奖时单局返回，
# 不烧剩余次数反复重打，重试由任务引擎的失败重试兜底）
JJ_MAX_ROUNDS = 1
# 返回1 循环上限（回到百趣闲市的确认次数）
JJ_RETURN_MAX = 10

# ── 蛋糕小游戏 ──────────────────────────────────
# 阶段0 进入导航
CAKE_MAIN_MARK = "chengzhang.png"    # 主界面标志模板
CAKE_ENTER_MARK = "节节高升.png"      # 百趣闲市界面标志 / 入口模板
CAKE_NAV_FLOW = ["活动场.png", "百趣闲市.png", "节节高升.png"]  # 主界面→进入点击链
CAKE_NAV_CLICK_WAIT = 1.0            # 每次导航点击后等待页面加载（秒）
CAKE_NAV_TIMEOUT = 3.0               # 每步导航等待模板出现超时（秒）
CAKE_NAV_INTERVAL = 1.0              # 导航重试间隔（秒）
# 阶段1 锚点定位
CAKE_ANCHOR_THRESHOLD = 0.85         # 锚点匹配阈值
CAKE_FC_RETRY_TIMES = 5              # fc2 等待重试次数
CAKE_FC_RETRY_INTERVAL = 1.0         # fc2 等待重试间隔（秒）
CAKE_EDGE_OFFSET = 10                # 边缘落点外扩量（px）：left_x-10 / right_x+10
CAKE_RELEASE_LINE_OFFSET = 10        # 释放线 y = fc（悬浮窗）底部 + 该偏移（px）
# 阶段2 游戏主循环
CAKE_GAME_TIMEOUT = 90.0             # 游戏硬超时（秒）
CAKE_DETECT_INTERVAL = 0.05          # 检测轮询间隔（秒）
CAKE_MATCH_THRESHOLD = 0.70          # 蛋糕 / zdg / 导航模板匹配阈值
CAKE_ASPECT_LIMIT = 3.0              # 宽高比过滤：> 该值视为通知文字横幅
CAKE_TRACK_INTERVAL = 0.02           # 追踪采样间隔（秒）
CAKE_TRACK_MAX_FRAMES = 100          # 单次追踪上限帧（约 2s 兜底）
CAKE_TRACK_MAX_FRAMES_BAD = 300      # 坏蛋糕（zd/hdg）追踪上限帧：需横穿到边缘，给足时间
CAKE_RELEASE_WAIT = 0.3              # 点击 zdg 后等待（下落完成，防干扰检测）：从0.5缩短以增加释放数
CAKE_RELEASE_X_TOLERANCE = 15        # 蛋糕 x 距目标落点的允许误差（px）：±10 过窄，pred 跳过窗口漏释放；±15 兼顾命中率与精度
CAKE_RELEASE_DELAY = 0.10             # 点击 zdg 到蛋糕开始下落的延迟（秒）：run26最优1445分54个，run27调0.09虽60个但仅1075分，0.10是精度与数量的最优平衡
CAKE_RELEASE_T_EXTRA = 0.05           # 到达时间判定的提前窗口（秒）：t_arr<=DELAY+T_EXTRA 即释放，落点偏差<=|vx|*T_EXTRA（850px/s 时 42px）
CAKE_TARR_MAX_PRED_ERR = 80          # 到达时间判定释放时的 pred 落点偏差上限（px）：从60放宽至80，增加高vx蛋糕释放成功率；80px≈高vx单帧位移+余量
CAKE_TRACK_DT_MIN = 0.02             # 两点差分测速最小帧间隔（秒）：原 0.05 会挡 30-50ms 帧，高 vx 蛋糕 vx 确认滞后错过提前释放点
CAKE_JUMP_MAX_PRED_ERR = 30           # 快速跨越释放的 pred 偏差上限（px）：超过说明落点会偏 |vx|*DELAY 远超 target，禁止释放
# 标定历程：0.38（纯游戏延迟，偏小落点偏右 +44~+140）→ 0.52（+检测滞后，偏大落点偏左 -48~-87）
#           → 0.45（折中，落点 -4~-18 仍偏左）→ 0.43（继续微调）
CAKE_VX_STABLE_DELTA = 200          # 相邻帧 vx 变化上限（px/s）：超过视为起步加速/折返加速中，pred 不可靠，跳过释放；匀速波动<100，起步/折返差异>200
CAKE_TRACK_MAX_JUMP = 140            # 单帧内允许的最大水平位移（px），超过视为异常采样，不触发跨越释放；140=最高速700px/s×最长帧间隔0.2s
CAKE_MISS_Y_OFFSET = 30              # y 过释放线后仍偏离目标 → 放弃的缓冲（px）
CAKE_AREA_BOTTOM_PAD = 70            # up_area 下边界外扩量（px）：蛋糕模板半高最大 ~64，防区域匹配裁掉蛋糕底部
CAKE_END_MARK = "再来一局.png"        # 结算页标志模板：检测到即游戏正常结束（不再用 zdg 消失判定，zdg 模板会匹配到移动中的蛋糕导致误判退出）
CAKE_END_CONFIRM_COUNT = 3           # 连续检测到结算页标志的次数 → 游戏结束（防单帧误检）
CAKE_END_IDLE_TIMEOUT = 10.0         # 异常保护：既无蛋糕、也无结算页标志的连续时长（秒）超过该值 → 判定游戏异常中断（弹窗/崩溃），退出防卡死；蛋糕刷新间隙远小于此值
# 阶段3 收尾
CAKE_EXIT_TIMEOUT = 5.0              # 结束后点击 fanhui 返回百趣闲市的超时（秒）
CAKE_EXIT_INTERVAL = 0.5             # 收尾重试间隔（秒）
# 阶段4 领奖（每日最多 CAKE_MAX_PLAYS 局，检测到领奖g 才领奖）
CAKE_MAX_PLAYS = 2                   # 每日最大游戏局数（两局都没检测到领奖g 记为失败）
CAKE_CLAIM_ENTRY_THRESHOLD = 0.55    # 领奖g 检测阈值（原0.95过高，实际置信度0.56-0.62，run31验证）
CAKE_CLAIM_GET_THRESHOLD = 0.45      # 领取g 检测阈值（原0.90过高，实际置信度0.485，run32验证）
CAKE_CLAIM_CONFIRM_THRESHOLD = 0.85  # 确定 检测阈值（用户未指定，取中等偏高避免误点）
CAKE_CLAIM_TIMEOUT = 5.0             # 领奖链路各按钮检测超时（秒）
CAKE_CLAIM_INTERVAL = 0.5            # 领奖链路检测间隔（秒）


# ── 征战天下·演武 ──────────────────────────────────
# 征战天下页面检测阈值（演武入口比急报/任务更稳定，检测用降低阈值）
ZZ_ENTRY_THRESHOLD = 0.70
# 主界面“征战天下”入口：背景图/活动横幅经常换，模板 征战天下.png 已失效，
# 改用相对坐标点击（2026-11-17 用户主界面实测 1600x894：文字中心 1345,440）。
# 入口横幅纵向约在 41%~52% 高度，比例点击留有余量。
ZZ_MAIN_ENTRY_X_RATIO = 0.84
ZZ_MAIN_ENTRY_Y_RATIO = 0.49
ZZ_MAIN_ENTRY_ATTEMPTS = 3
# 战斗按钮检测阈值（实测：真实可挑战格 0.685+，顶尖对手格 0.627/0.643；用户定为 0.7）
YW_BATTLE_THRESHOLD = 0.70
# 战斗中/胜利 检测阈值
YW_FIGHTING_THRESHOLD = 0.85
YW_WIN_THRESHOLD = 0.85
# 军令检测阈值（极高置信度，防误判）；军令出现 = 本轮 10 次机会已耗尽
YW_JUNLING_THRESHOLD = 0.99
# 军令机会数字识别：左/右锚点模板 + 锚点匹配阈值；数字用 jl0~jl10 模板匹配（替代 OCR，单字符 OCR 漏检）
YW_JUNLING_LEFT = "军令1.png"
YW_JUNLING_RIGHT = "军令2.png"
YW_JUNLING_ANCHOR_THRESHOLD = 0.70
YW_JUNLING_DIGIT_THRESHOLD = 0.70
# 自动战斗 / 刷新 按钮阈值
YW_AUTO_THRESHOLD = 0.85
YW_REFRESH_THRESHOLD = 0.85
# 宝箱 / 战报 / 领取 阈值（收尾阶段放宽）
YW_CLAIM_THRESHOLD = 0.7
# 演武入口阈值
YW_ENTER_THRESHOLD = 0.85
# 顶尖对手格（第一行前两个，带"顶尖对手"横幅，与战斗模板不符），从战斗候选中排除
YW_EXCLUDE_BATTLES = [(490, 396), (950, 396)]
# 同时"战斗中"上限
YW_MAX_CONCURRENT = 5
# 每日战斗机会
YW_DAILY_LIMIT = 10
# 刷新次数上限
YW_REFRESH_MAX = 4
# 自动战斗点击重试上限（次）
YW_CLICK_RETRIES = 3
# 连续未检测到自动战斗按钮跳过本轮上限（次）
YW_AUTO_MISS_MAX = 3
# 收尾领取循环上限（次）
YW_CLAIM_MAX = 10
# 专注补打模式下两轮之间的等待（秒）：急报清完后无任务可穿插，等战斗消化
YW_FOCUS_WAIT = 60.0
# 专注补打轮次上限（防止异常空转）
YW_FOCUS_MAX_ROUNDS = 30
# 军令 ≤1 的两次确认最小间隔（秒，2026-08-31 用户口径）：第二次确认靠
# 下一次任务间隙插入/专注补打轮再读，间隔时间让"战斗中"自然消化；
# 仅当其他任务全完成剩演武时，由专注补打循环原地等到间隔满足
# （08-31 用户二次调整：180 → 120）
YW_LOW_OPP_CONFIRM_GAP = 120.0
