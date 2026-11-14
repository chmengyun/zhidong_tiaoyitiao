"""跳跃规划：把像素距离换算成按压时长。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Tuple

from .config import Config


@dataclass
class Plan:
    """一次跳跃的执行参数。"""

    distance: float
    press_ms: int
    raw_press_ms: float
    clamped: bool


def distance_between(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def plan_jump(distance: float, cfg: Config) -> Plan:
    """按住时长与跳跃距离成正比：t = k * d + overhead。

    超出 [min_press_ms, max_press_ms] 时截断，避免按住过久而失败。
    """
    raw = distance * cfg.press_per_px + cfg.press_overhead_ms
    press = int(round(raw))
    clamped = False
    if press < cfg.min_press_ms:
        press = cfg.min_press_ms
        clamped = True
    elif press > cfg.max_press_ms:
        press = cfg.max_press_ms
        clamped = True
    return Plan(distance=distance, press_ms=press, raw_press_ms=raw, clamped=clamped)


def suggest_press_per_px(distance: float, press_ms: float,
                         overhead_ms: float = 0.0) -> float:
    """根据一次成功跳跃反推系数，供人工校准使用。"""
    if distance <= 0:
        raise ValueError("distance 必须大于 0")
    return (press_ms - overhead_ms) / distance
