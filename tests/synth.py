"""测试辅助工具：合成一张可控的「跳一跳」画面，用于离线验证识别算法。"""

from __future__ import annotations

import cv2
import numpy as np

BACKGROUND_TOP = (247, 228, 211)      # 淡蓝渐变（BGR）
BACKGROUND_BOTTOM = (222, 209, 196)
PLATFORM_FACE = (138, 138, 138)       # 顶面灰（饱和度 0）
PLATFORM_LEFT = (150, 150, 150)
PLATFORM_RIGHT = (168, 168, 168)
PIECE_COLOR = (95, 60, 70)            # 紫色棋子（BGR）


def make_background(w: int, h: int) -> np.ndarray:
    """生成纵向渐变背景。"""
    top = np.array(BACKGROUND_TOP, dtype=np.float32)
    bottom = np.array(BACKGROUND_BOTTOM, dtype=np.float32)
    ramp = np.linspace(0.0, 1.0, h, dtype=np.float32)[:, None, None]
    rows = top * (1 - ramp) + bottom * ramp
    return np.repeat(rows, w, axis=1).astype(np.uint8)


def draw_platform(img: np.ndarray, center: tuple[int, int],
                  rx: int = 226, ry: int = 113, depth: int = 150) -> None:
    """绘制一个等距立方体平台，center 为其顶面菱形中心。"""
    cx, cy = center
    top_face = np.array([[cx, cy - ry], [cx + rx, cy], [cx, cy + ry], [cx - rx, cy]])
    left_face = np.array([[cx - rx, cy], [cx, cy + ry],
                          [cx, cy + ry + depth], [cx - rx, cy + depth]])
    right_face = np.array([[cx + rx, cy], [cx, cy + ry],
                           [cx, cy + ry + depth], [cx + rx, cy + depth]])
    cv2.fillPoly(img, [left_face], PLATFORM_LEFT)
    cv2.fillPoly(img, [right_face], PLATFORM_RIGHT)
    cv2.fillPoly(img, [top_face], PLATFORM_FACE)


def draw_piece(img: np.ndarray, base: tuple[int, int],
               width: int = 76, height: int = 200) -> None:
    """绘制棋子，base 为脚底中心。"""
    cx, by = base
    body_w, body_h = int(width * 0.55), int(height * 0.62)
    cv2.rectangle(img, (cx - body_w // 2, by - body_h), (cx + body_w // 2, by), PIECE_COLOR, -1)
    cv2.ellipse(img, (cx, by - int(height * 0.06)), (int(width * 0.5), int(height * 0.06)),
                0, 0, 360, PIECE_COLOR, -1)
    cv2.circle(img, (cx, by - body_h - int(width * 0.36)), int(width * 0.38), PIECE_COLOR, -1)


def make_scene(width: int = 1080, height: int = 2408,
               piece_base: tuple[int, int] = (336, 1374),
               current_center: tuple[int, int] = (354, 1355),
               target_center: tuple[int, int] = (789, 1093),
               scale: float = 1.0) -> np.ndarray:
    """合成完整画面；scale 用于模拟不同分辨率。"""
    w, h = int(width * scale), int(height * scale)
    img = make_background(w, h)
    rx, ry, depth = int(226 * scale), int(113 * scale), int(150 * scale)
    draw_platform(img, (int(current_center[0] * scale), int(current_center[1] * scale)),
                  rx, ry, depth)
    draw_platform(img, (int(target_center[0] * scale), int(target_center[1] * scale)),
                  rx, ry, depth)
    draw_piece(img, (int(piece_base[0] * scale), int(piece_base[1] * scale)),
               width=int(76 * scale), height=int(200 * scale))
    return img


def make_game_over(width: int = 1080, height: int = 2408) -> np.ndarray:
    """合成结算界面：暗色蒙版 + 白色「再玩一局」按钮。"""
    img = make_scene(width, height)
    overlay = np.full_like(img, (90, 90, 95))
    img = cv2.addWeighted(img, 0.35, overlay, 0.65, 0)
    x1, y1, x2, y2 = int(0.38 * width), int(0.662 * height), int(0.855 * width), int(0.727 * height)
    cv2.rectangle(img, (x1, y1), (x2, y2), (255, 255, 255), -1)
    return img
