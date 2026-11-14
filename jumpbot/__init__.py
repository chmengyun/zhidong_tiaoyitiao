"""跳一跳自动化脚本核心包。

模块划分：
- config   : 参数配置
- device   : ADB 设备操作（截屏 / 按压）
- vision   : 画面识别（棋子、平台、结算界面）
- planner  : 距离 -> 按压时长换算
- runner   : 自动跳跃主循环
"""

__all__ = ["config", "device", "vision", "planner", "runner"]
