"""画面识别：棋子、平台、结算界面。

识别思路（针对微信「跳一跳」的等距立方体场景）：

1. 平台：平台是中性灰（低饱和度），背景是淡蓝色渐变（饱和度更高），
   因此用「低饱和度 + 亮度阈值」即可把平台从背景中分离。
2. 平台落点：立方体顶面是菱形，其左右极值点所在行就是菱形水平对角线，
   该行中点即顶面中心，也是棋子实际落脚点。
3. 棋子：棋子是画面中饱和度最高且偏暗的色块，用 HSV 阈值提取。
4. 结算界面：存在一个又宽又亮的圆角按钮（「再玩一局」），用宽高比 + 亮度判定。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Tuple

import cv2
import numpy as np

from .config import Config

Point = Tuple[float, float]


@dataclass
class Platform:
    """一个平台（等距立方体）。"""

    bbox: Tuple[int, int, int, int]  # x, y, w, h
    area: int
    center: Point                    # 顶面中心，即落点
    label: int = -1

    @property
    def aspect(self) -> float:
        return self.bbox[2] / max(1, self.bbox[3])

    def __str__(self) -> str:
        x, y, w, h = self.bbox
        return (f"Platform(bbox=({x},{y},{w},{h}), area={self.area}, "
                f"center=({self.center[0]:.1f},{self.center[1]:.1f}))")


@dataclass
class Piece:
    """棋子。"""

    bbox: Tuple[int, int, int, int]
    area: int
    base: Point  # 脚底中心，作为起跳点

    def __str__(self) -> str:
        x, y, w, h = self.bbox
        return (f"Piece(bbox=({x},{y},{w},{h}), area={self.area}, "
                f"base=({self.base[0]:.1f},{self.base[1]:.1f}))")


@dataclass
class Scene:
    """一帧画面的识别结果。"""

    state: str  # playing / game_over / unknown
    piece: Optional[Piece] = None
    current: Optional[Platform] = None
    target: Optional[Platform] = None
    distance: Optional[float] = None
    restart_point: Optional[Tuple[int, int]] = None
    platforms: list[Platform] = field(default_factory=list)
    note: str = ""

    def __str__(self) -> str:
        return (f"Scene(state={self.state}, note={self.note!r}, distance="
                f"{'None' if self.distance is None else f'{self.distance:.1f}'}, "
                f"tap={self.restart_point}, "
                f"piece={self.piece}, current={self.current}, target={self.target})")


# ----------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------
def _hsv(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2HSV)


def _play_region(img: np.ndarray, cfg: Config) -> tuple[int, int]:
    h = img.shape[0]
    return int(h * cfg.region_top_ratio), int(h * cfg.region_bottom_ratio)


def _top_face_center(sub: np.ndarray, ox: int, oy: int,
                     scan_ratio: float = 0.55) -> Point:
    """由组件掩码求立方体 / 圆盘顶面中心。

    顶面在等距投影下是菱形（方块）或椭圆（圆盘），两者最宽的一行都是顶面的
    水平中线。立方体侧面也等宽，但出现得更靠下，所以只在组件上方 scan_ratio
    范围内找「达到最大宽度且最靠上的一行」，即可命中顶面中线。

    sub 为组件在自身外接框内的 0/1 掩码，(ox, oy) 为外接框左上角坐标。
    """
    ys, xs = np.nonzero(sub)
    if len(xs) == 0:
        return (float(ox), float(oy))
    height = sub.shape[0]
    row_min = np.full(height, np.iinfo(np.int32).max)
    row_max = np.full(height, -1)
    np.minimum.at(row_min, ys, xs)
    np.maximum.at(row_max, ys, xs)
    widths = np.where(row_max >= 0, row_max - row_min + 1, 0)
    # 粘连区域的下半部分会把「最宽行」拉到很低的位置，这里只在顶面可能出现的
    # 上半部分里找
    limit = max(2, int(round(height * scan_ratio)))
    window = widths[:limit]
    peak = int(window.max())
    # 留 2px 容差，避免抗锯齿导致真正的顶面中线这一行略窄而被跳过
    row = int(np.nonzero(window >= peak - 2)[0][0])
    cx = ox + (int(row_min[row]) + int(row_max[row])) / 2.0
    return (cx, oy + float(row))


def _top_vertex_offset(sub: np.ndarray) -> float:
    """组件最上面一行的中点相对外接框中心的偏移（占宽度的比例）。

    单个平台的最上方是菱形/椭圆的顶点，必然位于中心正上方；两块平台粘连时，
    最上面的顶点属于其中一块，会明显偏离整体外接框的中心。
    """
    ys, xs = np.nonzero(sub)
    if len(xs) == 0:
        return 1.0
    cols = xs[ys == int(ys.min())]
    top_mid = (int(cols.min()) + int(cols.max())) / 2.0
    width = max(1.0, float(sub.shape[1]))
    return abs(top_mid - width / 2.0) / width



def _components(mask: np.ndarray):
    n, labels, stats, centroids = cv2.connectedComponentsWithStats(mask, 8)
    return labels, stats, centroids


def _bbox_inside(bbox: tuple[int, int, int, int],
                 outer: tuple[int, int, int, int]) -> bool:
    """bbox 是否完全落在 outer 内（x, y, w, h）。"""
    x, y, w, h = bbox
    ox, oy, ow, oh = outer
    return x >= ox and y >= oy and x + w <= ox + ow and y + h <= oy + oh


# ----------------------------------------------------------------------
# 平台
# ----------------------------------------------------------------------
def background_model(img: np.ndarray, cfg: Config) -> tuple[np.ndarray, np.ndarray]:
    """用画面顶部纯净背景区估计「饱和度 / 亮度」随行号的线性变化。"""
    height = img.shape[0]
    hsv = _hsv(img)
    sat = hsv[..., 1].astype(np.float32)
    val = hsv[..., 2].astype(np.float32)
    rows = np.arange(max(1, int(height * cfg.bg_sample_top)),
                     max(2, int(height * cfg.bg_sample_bottom)))
    sat_profile = np.median(sat[rows], axis=1)
    val_profile = np.median(val[rows], axis=1)
    sat_fit = np.polyfit(rows, sat_profile, 1)
    val_fit = np.polyfit(rows, val_profile, 1)
    all_rows = np.arange(height)
    return (np.clip(np.polyval(sat_fit, all_rows), 0, 255),
            np.clip(np.polyval(val_fit, all_rows), 0, 255))


def platform_mask(img: np.ndarray, cfg: Config) -> np.ndarray:
    """平台前景分割。

    背景是平滑的淡色渐变：与背景相比，平台要么色相饱和度不同（白 / 彩色块），
    要么更亮。阴影只是把背景压暗、色相和饱和度都不变，因此可以被自然排除。
    比背景更暗但色相明显不同（例如灰绿色圆桌面）的平台也要认出来。
    """
    hsv = _hsv(img)
    sat = hsv[..., 1].astype(np.float32)
    val = hsv[..., 2].astype(np.float32)
    hue = hsv[..., 0].astype(np.float32)
    sat_bg, val_bg = background_model(img, cfg)
    height = img.shape[0]
    band = hsv[max(1, int(height * cfg.bg_sample_top)):max(2, int(height * cfg.bg_sample_bottom)), :, 0]
    bg_hue = float(np.median(band))
    hue_diff = np.abs(hue - bg_hue)
    hue_diff = np.minimum(hue_diff, 180.0 - hue_diff)
    top, bottom = _play_region(img, cfg)
    # 关卡整体换色时（例如淡绿色主题），线性背景模型会失准、把大片背景当成前景，
    # 此时逐步提高阈值，直到前景占比回到合理范围
    for scale in (1, 2, 3):
        mask = ((np.abs(sat - sat_bg[:, None]) > cfg.platform_sat_delta * scale)
                | ((val - val_bg[:, None]) > cfg.platform_val_delta * scale)
                | ((hue_diff > cfg.platform_hue_delta * scale)
                   & (sat > cfg.platform_hue_sat_min))
                ).astype(np.uint8) * 255
        if float((mask[top:bottom] > 0).mean()) < 0.35:
            break
    mask[:top] = 0
    mask[bottom:] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    return mask


def _brightness_peaks(values: np.ndarray, ratio: float) -> list[int]:
    """在亮度直方图上找峰，用于把相邻粘连的平台按明暗拆开。"""
    hist = np.bincount(values.astype(np.uint8), minlength=256).astype(np.float64)
    kernel = np.array([1, 2, 3, 2, 1], dtype=np.float64)
    kernel /= kernel.sum()
    smooth = np.convolve(hist, kernel, mode="same")
    threshold = ratio * smooth.max()
    candidates = [i for i in range(1, 255)
                  if smooth[i] >= threshold
                  and smooth[i] >= smooth[i - 1] and smooth[i] >= smooth[i + 1]]
    peaks: list[int] = []
    for c in candidates:
        if peaks and c - peaks[-1] < 14:
            if smooth[c] > smooth[peaks[-1]]:
                peaks[-1] = c
        else:
            peaks.append(c)
    return peaks or [int(np.median(values))]


def detect_platforms(img: np.ndarray, cfg: Config,
                     piece: Optional[Piece] = None) -> list[Platform]:
    """检测所有平台顶面，返回顶面中心，按面积从大到小排序。

    piece 为识别到的棋子（可选）：棋子与脚下的平台在掩码里会连成一体，
    先把它从掩码中剔除，避免拆分出来的平台被棋子拉偏中心；棋子脚底纵坐标
    还用于判断拆分结果是否在「前方」。

    五级策略：
    1) 整个前景连通域 —— 大多数情况下一个方块就是一个平台；
    2) 连通域按亮度分峰再切分 —— 处理明暗不同、在画面上贴着的两个平台；
    3) 腐蚀找种子 + 最近种子分配 —— 处理亮度相同（例如两块白色叠层）的粘连平台；
    4) 按饱和度切分 —— 处理白色建筑（商店）与彩色方块黏在一起的情况；
    5) 按色相切分 —— 处理颜色不同（黄 / 橙）但明暗接近的相邻方块。
    """
    mask = platform_mask(img, cfg)
    piece_box: Optional[tuple[int, int, int, int]] = None
    if piece is not None:
        # 只剔除棋子自身的像素（同色相高饱和），不整块矩形清零，避免削掉平台
        px, py, pw, ph = piece.bbox
        pad = 4
        x1, y1 = max(0, px - pad), max(0, py - pad)
        x2, y2 = min(mask.shape[1], px + pw + pad), min(mask.shape[0], py + ph + pad)
        pmask = piece_mask(img, cfg)[y1:y2, x1:x2]
        mask[y1:y2, x1:x2] = np.where(pmask > 0, 0, mask[y1:y2, x1:x2]).astype(np.uint8)
        # 棋子身上的高光 / 反光（低饱和、高亮）不在 piece_mask 里，会残留成一块
        # 小连通域；它落在棋子外接框内，绝不能当平台，否则会变成「前方目标」
        piece_box = (px - 8, py - 8, pw + 16, ph + 16)
    piece_y = piece.base[1] if piece is not None else None
    labels, stats, _ = _components(mask)
    hsv = _hsv(img)
    val = hsv[..., 2]
    sat = hsv[..., 1]
    screen_area = img.shape[0] * img.shape[1]
    min_area = cfg.platform_min_area_ratio * screen_area
    max_area = cfg.platform_max_area_ratio * screen_area
    split_min = cfg.split_min_area_ratio * screen_area
    small_min = cfg.platform_small_area_ratio * screen_area

    found: list[Platform] = []
    for label in range(1, labels.max() + 1):
        x, y, w, h, area = (int(v) for v in stats[label])
        if h <= 0:
            continue
        if piece_box is not None and _bbox_inside((x, y, w, h), piece_box):
            continue
        region = (labels[y:y + h, x:x + w] == label).astype(np.uint8)
        if area < min_area:
            # 迷你平台（小圆凳 / 线轴）面积不到常规平台一半，要求轮廓极干净
            plat = _accept_subregion(region, x, y, cfg, min_area, max_area,
                                     small_min=small_min)
            if plat is not None:
                found.append(plat)
            continue

        # 1) 整个连通域（面积过大 / 顶点偏心说明是多块平台粘连，交给下面的拆分处理）
        normal_ok = (area <= max_area
                     and cfg.platform_aspect_min <= w / h <= cfg.platform_aspect_max
                     and area / (w * h) >= cfg.platform_fill_min
                     and _top_vertex_offset(region) <= cfg.platform_top_symmetry_max)
        # 细高的白色圆柱 / 药瓶（卷纸平台等）长宽比不到 0.85，只要轮廓干净对称
        # 就是一个完整平台
        tall_ok = (not normal_ok and area <= max_area
                   and _clean_tall_object(w, h, area, region, cfg))
        # 圆桌 / 圆盘类平台（盘面 + 细支腿）填不满外接框，但顶点仍在正中；
        # 这类平台的落点仍按顶面中线取，不能走 _top_center_for_tall
        disc_ok = (not normal_ok and not tall_ok and area <= max_area
                   and cfg.platform_aspect_relax_min <= w / h < cfg.platform_aspect_min
                   and area / (w * h) >= cfg.platform_disc_fill_min
                   and _top_vertex_offset(region) <= 0.06)
        if normal_ok or tall_ok or disc_ok:
            center = (_top_center_for_tall(region, x, y) if tall_ok
                      else _top_face_center(region, x, y))
            found.append(Platform(bbox=(x, y, w, h), area=area,
                                  center=center, label=label))
        whole_ok = normal_ok or tall_ok or disc_ok

        if area > cfg.split_max_area_ratio * screen_area:
            continue

        # 2) 按亮度切分，拆分粘连平台
        crop = labels[y:y + h, x:x + w] == label
        crop_val = val[y:y + h, x:x + w]
        brightness_ahead = False
        for peak in _brightness_peaks(crop_val[crop], cfg.split_peak_ratio):
            band = crop & (np.abs(crop_val.astype(np.int32) - peak) < cfg.split_tolerance)
            if int(band.sum()) < split_min:
                continue
            sub_labels, _, _ = _components(band.astype(np.uint8) * 255)
            for k in range(1, sub_labels.max() + 1):
                plat = _accept_subregion(sub_labels == k, x, y, cfg, split_min, max_area)
                if plat is not None:
                    found.append(plat)
                    if (piece_y is None
                            or plat.center[1] < piece_y - cfg.target_min_ahead_px):
                        brightness_ahead = True

        # 3) 腐蚀种子 + 最近种子分配：同色（例如卷纸 + 白方块）粘连也能拆开
        voronoi_ahead = False
        if not whole_ok:
            seeds = _erosion_seeds(region, cfg.split_seed_area_ratio * screen_area)
            for sub_region in _voronoi_split(region, seeds):
                plat = _accept_subregion(sub_region, x, y, cfg, split_min, max_area,
                                         symmetry_max=cfg.platform_top_symmetry_max)
                if plat is not None:
                    found.append(plat)
                    if (piece_y is None
                            or plat.center[1] < piece_y - cfg.target_min_ahead_px):
                        voronoi_ahead = True

        # 4) 白色建筑与彩色方块同亮度，按饱和度再拆一次。
        # 即使整块连通域通过了校验也照拆：两块颜色不同的平台粘在一起时，整体
        # 顶点可能恰好接近中心而骗过校验，拆出的部分会在去重时与整体合并或
        # 作为更靠前的候选保留下来。
        if not brightness_ahead and not voronoi_ahead:
            crop_sat = sat[y:y + h, x:x + w]
            for sub_region in _saturation_split(region, crop_sat):
                plat = _accept_subregion(sub_region, x, y, cfg, split_min, max_area,
                                         symmetry_max=cfg.platform_top_symmetry_max,
                                         small_min=small_min)
                if plat is not None:
                    found.append(plat)

        # 5) 相邻方块颜色（色相）不同但明暗接近时，按色相再拆一次
        if not brightness_ahead and not voronoi_ahead:
            crop_hue = hsv[..., 0][y:y + h, x:x + w]
            for sub_region in _hue_split(region, crop_hue):
                plat = _accept_subregion(sub_region, x, y, cfg, split_min, max_area,
                                         symmetry_max=cfg.platform_top_symmetry_max,
                                         small_min=small_min)
                if plat is not None:
                    found.append(plat)

        # 6) 白色圆柱的顶面又亮又几乎无色，和周围方块的白色条纹混在一起；
        #    用「高亮 + 低饱和」阈值化后做开运算，细条纹被抹掉、顶面留下来
        if not brightness_ahead and not voronoi_ahead:
            white = ((sat[y:y + h, x:x + w] < cfg.white_top_sat_max)
                     & (val[y:y + h, x:x + w] > cfg.white_top_val_min)
                     & (region > 0)).astype(np.uint8)
            white = cv2.morphologyEx(white, cv2.MORPH_OPEN,
                                     cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
            count, lab, white_stats, _ = cv2.connectedComponentsWithStats(white, 8)
            # 白色顶面常比常规平台小一半（小方块），面积下限单独给
            white_min = cfg.white_top_min_area_ratio * screen_area
            for k in range(1, count):
                if white_stats[k, 4] < white_min:
                    continue
                plat = _accept_subregion(lab == k, x, y, cfg, white_min, max_area,
                                         symmetry_max=cfg.platform_top_symmetry_max)
                if plat is not None:
                    found.append(plat)

        # 7) 两块同色方块贴合成一条竖直缝隙：按缝隙竖切，只保留切出来是
        #    完整平台（顶点居中）的那一侧。作为兜底手段，仅在前面所有拆分都
        #    没能给出「前方」平台时才启用，避免把已有的正确目标换成切片。
        if not brightness_ahead and not voronoi_ahead:
            ahead_exists = any(p.center[1] < piece_y - cfg.target_min_ahead_px
                               for p in found) if piece_y is not None else bool(found)
            if not ahead_exists:
                found.extend(_seam_cut_split(region, x, y, cfg, split_min, max_area))

    # 8) 暗色中性平台（深灰圆凳、线轴等）与背景同色相、同饱和度、只是更暗，
    #    按颜色分割会把它当成阴影丢掉。阴影只是背景的轻微压暗，比背景暗得多
    #    的物体才可能是这类平台，因此用更严的「暗 + 低饱和」阈值单独切一次。
    #    同样只在前面没找到前方平台时启用。
    ahead_exists = any(p.center[1] < piece_y - cfg.target_min_ahead_px
                       for p in found) if piece_y is not None else bool(found)
    if not ahead_exists:
        found.extend(_dark_platforms(img, cfg, max_area))
        found.extend(_cap_platforms(img, cfg, max_area))

    if piece_box is not None:
        # 兜底阶段直接在全图上找候选，同样不能把棋子身上的反光当平台
        found = [p for p in found if not _bbox_inside(p.bbox, piece_box)]
    found.sort(key=lambda p: p.area, reverse=True)
    return _dedupe(found)


def _cap_top_region(region: np.ndarray) -> Optional[np.ndarray]:
    """把「宽盘面 + 细支腿」的连通域切出盘面部分（圆凳 / 圆桌）。

    这类平台常与一侧的阴影相连，阴影会把整体外接框拉偏，使顶点不再居中；
    盘面本身仍是完整椭圆，在「最宽行之后宽度骤降」的位置切开即可还原。
    """
    h = region.shape[0]
    widths = region.sum(axis=1)
    if h < 8 or widths.max() <= 0:
        return None
    peak = int(widths.argmax())
    peak_w = int(widths[peak])
    if peak > 0.4 * h:
        return None           # 最宽行在下方，更像方块侧面，不是盘面
    # 盘面下方接细支腿：找第一段连续明显变窄的行（方块的侧壁不会骤降一半）
    narrow = widths[peak + 1:] < 0.5 * peak_w
    run = 0
    cut = None
    for i, flag in enumerate(narrow):
        run = run + 1 if flag else 0
        if run >= 4:
            cut = peak + 1 + i + 1 - run
            break
    if cut is None or cut < peak + 4:
        return None
    return region[:cut].copy()


def _dark_platforms(img: np.ndarray, cfg: Config, max_area: float) -> list[Platform]:
    """识别「暗色中性平台」：明显比背景暗、几乎没有色彩的实体。

    阴影与背景同色，只是轻微压暗（暗的幅度远小于 dark_platform_val_delta），
    因此用严格阈值就能把阴影排除，只留下深灰 / 黑色的平台。画面上同色相的
    文字（分数）也被暗度下限和最小面积挡掉。
    """
    hsv = _hsv(img)
    sat = hsv[..., 1].astype(np.float32)
    val = hsv[..., 2].astype(np.float32)
    _, val_bg = background_model(img, cfg)
    top, bottom = _play_region(img, cfg)
    dark = (((val_bg[:, None] - val) > cfg.dark_platform_val_delta)
            & (sat < cfg.dark_platform_sat_max)).astype(np.uint8) * 255
    dark[:top] = 0
    dark[bottom:] = 0
    dark = cv2.morphologyEx(dark, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    labels, stats, _ = _components(dark)
    screen_area = img.shape[0] * img.shape[1]
    min_area = cfg.dark_platform_min_area_ratio * screen_area
    found: list[Platform] = []
    for label in range(1, labels.max() + 1):
        x, y, w, h, area = (int(v) for v in stats[label])
        if not (min_area <= area <= max_area) or h <= 0:
            continue
        region = (labels[y:y + h, x:x + w] == label).astype(np.uint8)
        # 分数等深色文字的亮度更低，用亮度下限排除
        if float(np.median(val[y:y + h, x:x + w][region > 0])) < cfg.dark_platform_val_min:
            continue
        plat = _accept_subregion(region, x, y, cfg, min_area, max_area,
                                 symmetry_max=cfg.platform_top_symmetry_max,
                                 fill_min=cfg.platform_fill_min)
        if plat is None:
            # 盘面被阴影拖偏时，切出盘面本身再试一次
            cap = _cap_top_region(region)
            if cap is not None:
                plat = _accept_subregion(cap, x, y, cfg, min_area, max_area,
                                         symmetry_max=cfg.platform_top_symmetry_max,
                                         fill_min=cfg.platform_fill_min)
        if plat is not None:
            found.append(plat)
    return found


def _cap_platforms(img: np.ndarray, cfg: Config, max_area: float) -> list[Platform]:
    """迷你圆凳 / 线轴的顶盖：一个比背景更亮（白 / 浅灰）或明显更暗（深色 / 彩色）
    的椭圆盘。

    这类小平台常与旁边的方块粘成一块，整体轮廓也不对称（盘面一侧有阴影），
    但顶盖本身是完整椭圆，用它的最宽行中点即可拿到落脚点。
    """
    hsv = _hsv(img)
    sat = hsv[..., 1].astype(np.float32)
    val = hsv[..., 2].astype(np.float32)
    _, val_bg = background_model(img, cfg)
    top, bottom = _play_region(img, cfg)
    brighter = ((val - val_bg[:, None]) > cfg.cap_val_delta) & (sat < cfg.white_top_sat_max)
    darker = (val_bg[:, None] - val) > cfg.dark_platform_val_delta
    mask = (brighter | darker).astype(np.uint8) * 255
    mask[:top] = 0
    mask[bottom:] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
    labels, stats, _ = _components(mask)
    screen_area = img.shape[0] * img.shape[1]
    min_area = cfg.cap_min_area_ratio * screen_area
    found: list[Platform] = []
    for label in range(1, labels.max() + 1):
        x, y, w, h, area = (int(v) for v in stats[label])
        if not (min_area <= area <= max_area) or h <= 0 or w < h:
            continue
        if not (cfg.platform_aspect_min <= w / h <= cfg.platform_aspect_max):
            continue
        if area / (w * h) < cfg.platform_disc_fill_min:
            continue
        region = (labels[y:y + h, x:x + w] == label).astype(np.uint8)
        # 深色分支里最长的是顶部分数（比背景暗得多、又宽又矮），用亮度下限排除；
        # 真正的顶盖即使比背景暗，也不会暗到接近纯黑
        if float(np.median(val[y:y + h, x:x + w][region > 0])) < cfg.dark_platform_val_min:
            continue
        if _top_vertex_offset(region) > cfg.seam_symmetry_max:
            continue
        found.append(Platform(bbox=(x, y, w, h), area=area,
                              center=_top_face_center(region, x, y)))
    return found


def _seam_cut_split(region: np.ndarray, ox: int, oy: int, cfg: Config,
                    split_min: float, max_area: float) -> list[Platform]:
    """两块同色、同亮度的方块贴合时，沿竖直缝隙竖切。

    在连通域中间区域试切，只接受「切口某一侧正好是一个完整平台」的切法：
    该侧的顶点接近自身外接框中心，说明这一侧是独立的一块方块，另一侧可能
    是多块粘连，直接丢弃。每一侧只保留顶点最居中（最对称）的那一刀。
    """
    _, w = region.shape
    best: dict[str, tuple[float, Platform]] = {}
    for cut in range(int(w * 0.25), int(w * 0.75) + 1, 4):
        for side, offset, part in (("L", 0, region[:, :cut]),
                                   ("R", cut, region[:, cut:])):
            ys, xs = np.nonzero(part)
            if len(xs) < split_min:
                continue
            x1, y1 = int(xs.min()), int(ys.min())
            bw = int(xs.max()) - x1 + 1
            bh = int(ys.max()) - y1 + 1
            area = int(len(xs))
            if bh <= 0 or area > max_area:
                continue
            if not (cfg.platform_aspect_relax_min <= bw / bh <= cfg.split_aspect_max):
                continue
            if not (cfg.split_fill_min <= area / (bw * bh) <= cfg.split_fill_max):
                continue
            sub = part[y1:y1 + bh, x1:x1 + bw].astype(np.uint8)
            sym = _top_vertex_offset(sub)
            if sym > cfg.seam_symmetry_max:
                continue
            if side in best and best[side][0] <= sym:
                continue
            ax, ay = ox + offset + x1, oy + y1
            best[side] = (sym, Platform(
                bbox=(ax, ay, bw, bh), area=area,
                center=_top_face_center(sub, ax, ay)))
    return [plat for _, plat in best.values()]


def _top_center_for_tall(sub: np.ndarray, ox: int, oy: int) -> Point:
    """细高平台（卷纸 / 药瓶）的落点：最高点所在行的中点，纵向按顶面椭圆估算。

    这类平台最宽的一行是瓶身而不是顶盖，用 _top_face_center 会把中心算低。
    """
    ys, xs = np.nonzero(sub)
    if len(xs) == 0:
        return (float(ox), float(oy))
    top_y = int(ys.min())
    cols = xs[ys == top_y]
    cx = ox + (int(cols.min()) + int(cols.max())) / 2.0
    # 等距投影下顶面椭圆高度约为宽度的 1/2，中心在最高点下方约 1/4 宽度
    cy = oy + top_y + 0.25 * sub.shape[1]
    return (cx, cy)


def _clean_tall_object(w: int, h: int, area: int, region: np.ndarray,
                       cfg: Config) -> bool:
    """判断细高组件是否是一个干净的完整平台（例如卷纸造型的圆柱）。"""
    aspect = w / h
    return (cfg.platform_aspect_relax_min <= aspect < cfg.platform_aspect_min
            and area / (w * h) >= 0.80          # 轮廓几乎填满外接框
            and _top_vertex_offset(region) <= 0.06)  # 顶点在正中


def _accept_subregion(region: np.ndarray, ox: int, oy: int, cfg: Config,
                      split_min: float, max_area: float,
                      symmetry_max: Optional[float] = None,
                      small_min: float = 0.0,
                      fill_min: Optional[float] = None) -> Optional[Platform]:
    """校验一个拆分出来的子区域是否像平台顶面，合格则返回 Platform。

    symmetry_max 非空时额外要求「顶点在正中」，用于过滤按距离强行切出来的
    半块平台（Voronoi 拆分）。
    small_min 非零时，面积不足 split_min 但达到 small_min 的「迷你平台」
    （小圆凳 / 线轴）在轮廓极对称时也接受。
    fill_min 为空时用 split_fill_min；暗色圆凳的盘面下方接细腿，整体填充率
    很低，这类兜底阶段可传入更宽松的下限。
    """
    ys, xs = np.nonzero(region)
    if len(xs) == 0:
        return None
    x1, y1 = int(xs.min()), int(ys.min())
    w, h = int(xs.max()) - x1 + 1, int(ys.max()) - y1 + 1
    area = int(len(xs))
    if h <= 0 or area > max_area:
        return None
    small = (small_min > 0 and small_min <= area < split_min)
    if not small and area < split_min:
        return None
    aspect = w / h
    fill = area / (w * h)
    strict_aspect = cfg.split_aspect_min <= aspect <= cfg.split_aspect_max
    if small:
        # 迷你平台只允许细高 / 近圆的轮廓，且必须非常对称，否则多半是碎片；
        # 圆柱类平台几乎填满外接框，不受 split_fill_max 的限制
        if not (cfg.platform_small_aspect_min <= aspect <= 0.80):
            return None
        if fill < 0.45:
            return None
    # 立方体在等距投影下整体（含侧面）比顶面窄一些，长宽比会掉到 0.85 以下；
    # 只要顶点严格居中（说明切出来的是完整一块，而非半块），就放宽到细高平台
    # 的下限，避免把「橙色方块」这类干净目标当成不合格区域丢掉
    if not small and not (cfg.platform_aspect_relax_min <= aspect <= cfg.split_aspect_max):
        return None
    fill_floor = cfg.split_fill_min if fill_min is None else fill_min
    if not small and not (fill_floor <= fill <= cfg.split_fill_max):
        return None
    sub = region[y1:y1 + h, x1:x1 + w].astype(np.uint8)
    sym = _top_vertex_offset(sub)
    if symmetry_max is not None and sym > symmetry_max:
        return None
    if not strict_aspect and sym > 0.06:
        return None
    if small and sym > cfg.platform_small_symmetry_max:
        return None
    row_w = sub.sum(axis=1)
    band_h = max(2, int(h * 0.03))
    if float(row_w[:band_h].max()) / max(1, int(row_w.max())) > cfg.split_top_width_max:
        return None  # 顶边是平的，说明是侧面而不是顶面
    return Platform(bbox=(ox + x1, oy + y1, w, h), area=area,
                    center=_top_face_center(sub, ox + x1, oy + y1))


def _saturation_split(region: np.ndarray, sat_crop: np.ndarray) -> list[np.ndarray]:
    """按饱和度把连通域一分为二（白色建筑 vs 彩色方块），再各自取连通块。"""
    values = sat_crop[region > 0]
    if values.size == 0:
        return []
    threshold, _ = cv2.threshold(values.astype(np.uint8), 0, 255,
                                 cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    parts: list[np.ndarray] = []
    for part in ((sat_crop <= threshold) & (region > 0),
                 (sat_crop > threshold) & (region > 0)):
        count, lab = cv2.connectedComponents(part.astype(np.uint8), 8)
        parts.extend(lab == i for i in range(1, count))
    return parts


def _hue_split(region: np.ndarray, hue_crop: np.ndarray) -> list[np.ndarray]:
    """按色相把连通域拆开（相邻但颜色明显不同的方块，例如黄 / 橙）。

    Otsu 在「两簇数量相近 + 少量离群色相」时会把阈值推到离群值一侧，
    这里改为取色相直方图最高的两个峰，再用两峰之间的谷作阈值。两个峰相距
    过远（含色相环回绕）时认为是同一颜色的明暗变化，放弃拆分。
    """
    values = hue_crop[region > 0]
    if values.size == 0:
        return []
    hist = np.bincount(values.astype(np.uint8), minlength=180).astype(np.float64)
    kernel = np.array([1, 2, 3, 2, 1], dtype=np.float64)
    smooth = np.convolve(hist, kernel / kernel.sum(), mode="same")
    order = sorted(range(1, 179), key=lambda i: smooth[i], reverse=True)
    first = order[0]
    second = next((i for i in order if abs(i - first) >= 6), None)
    if second is None:
        return []
    lo, hi = min(first, second), max(first, second)
    if hi - lo > 60:
        return []
    threshold = lo + int(np.argmin(smooth[lo:hi + 1]))
    low = (hue_crop <= threshold) & (region > 0)
    high = (hue_crop > threshold) & (region > 0)
    n_low, n_high = int(low.sum()), int(high.sum())
    if n_low == 0 or n_high == 0 or min(n_low, n_high) < 0.08 * values.size:
        return []
    parts: list[np.ndarray] = []
    for part in (low, high):
        count, lab = cv2.connectedComponents(part.astype(np.uint8), 8)
        parts.extend(lab == i for i in range(1, count))
    return parts


def _erosion_seeds(region: np.ndarray, min_seed: float) -> list[np.ndarray]:
    """反复腐蚀直到粘连区域断成至少两个「核」，作为拆分的种子。"""
    for size in (11, 17, 25, 35, 49, 65, 85, 101):
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        eroded = cv2.erode(region, kernel)
        count, lab = cv2.connectedComponents(eroded, 8)
        seeds = [lab == i for i in range(1, count)
                 if int((lab == i).sum()) >= min_seed]
        if len(seeds) >= 2:
            return seeds
    return []


def _voronoi_split(region: np.ndarray, seeds: list[np.ndarray]) -> list[np.ndarray]:
    """把连通域按「离哪个种子最近」分配给各种子，得到互不重叠的子区域。"""
    if not seeds:
        return []
    seed_img = np.zeros_like(region)
    for i, seed in enumerate(seeds, start=1):
        seed_img[seed] = i
    _, labels = cv2.distanceTransformWithLabels(
        (seed_img == 0).astype(np.uint8), cv2.DIST_L2, 5,
        labelType=cv2.DIST_LABEL_CCOMP)
    parts = []
    for seed in seeds:
        seed_label = int(labels[seed].flat[0])
        parts.append((region > 0) & (labels == seed_label))
    return parts


def _dedupe(platforms: list[Platform], min_gap: float = 100.0) -> list[Platform]:
    """合并中心过于接近的重复区域（只保留面积最大的）。"""
    kept: list[Platform] = []
    for p in platforms:
        duplicate = False
        for k in kept:
            if math.hypot(p.center[0] - k.center[0], p.center[1] - k.center[1]) < min_gap:
                duplicate = True
                break
        if not duplicate:
            kept.append(p)
    return kept


# ----------------------------------------------------------------------
# 棋子
# ----------------------------------------------------------------------
def piece_mask(img: np.ndarray, cfg: Config) -> np.ndarray:
    hsv = _hsv(img)
    lower = np.array([cfg.piece_hue_min, cfg.piece_sat_min, 0], dtype=np.uint8)
    upper = np.array([cfg.piece_hue_max, 255, cfg.piece_val_max], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower, upper)
    top, bottom = _play_region(img, cfg)
    mask[:top] = 0
    mask[bottom:] = 0
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    return mask


def detect_piece(img: np.ndarray, cfg: Config) -> Optional[Piece]:
    """提取棋子，返回其整体外接框与脚底中心。"""
    mask = piece_mask(img, cfg)
    labels, stats, centroids = _components(mask)
    screen_area = img.shape[0] * img.shape[1]
    min_area = cfg.piece_min_area_ratio * screen_area

    candidates = [i for i in range(1, labels.max() + 1) if stats[i, 4] >= min_area]
    if not candidates:
        return None

    # 棋身又细又高；平台的深色装饰（深紫方块的暗面等）可能更高，但一定更宽。
    # 先按「瘦高」筛选，再比高度，避免把装饰当成棋子、脚底落到平台中部
    def piece_score(i: int) -> tuple:
        w, h = float(stats[i, 2]), float(stats[i, 3])
        narrow = w / max(1.0, h) <= 0.85
        return (narrow, h, int(stats[i, 4]))

    main = max(candidates, key=piece_score)
    mx, my, mw, mh, _ = (int(v) for v in stats[main])
    x1, y1, x2, y2 = mx, my, mx + mw, my + mh
    cx_main = centroids[main][0]
    total_area = int(stats[main, 4])

    # 头部与身体可能被阈值切成两块，按邻接关系合并。
    # 平台上的深色装饰（例如药瓶的蓝色标签）会与棋子同色，必须靠「宽度相近、
    # 紧贴棋身」来排除，否则会把脚底拉到平台中部。
    for i in candidates:
        if i == main:
            continue
        ax, ay, aw, ah, aa = (int(v) for v in stats[i])
        same_column = abs(centroids[i][0] - cx_main) <= mw * 0.8
        above_ok = (ay + ah) >= my - mh * 2
        below_ok = ay <= y2 + 8
        similar_width = aw <= mw * 1.25
        if same_column and above_ok and below_ok and similar_width:
            x1, y1 = min(x1, ax), min(y1, ay)
            x2, y2 = max(x2, ax + aw), max(y2, ay + ah)
            total_area += aa
            mw = max(mw, aw)

    bbox = (x1, y1, x2 - x1, y2 - y1)
    base = ((x1 + x2) / 2.0, float(y2))
    return Piece(bbox=bbox, area=total_area, base=base)


# ----------------------------------------------------------------------
# 结算界面
# ----------------------------------------------------------------------
def find_restart_button(img: np.ndarray, cfg: Config) -> Optional[Tuple[int, int]]:
    """查找「再玩一局」按钮，返回其中心点。"""
    hsv = _hsv(img)
    sat, val = hsv[..., 1], hsv[..., 2]
    mask = (((val >= cfg.button_val_min) & (sat <= cfg.button_sat_max))
            .astype(np.uint8)) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    labels, stats, centroids = _components(mask)

    height = img.shape[0]
    screen_area = img.shape[0] * img.shape[1]
    best: Optional[Tuple[int, int]] = None
    best_area = 0
    for i in range(1, labels.max() + 1):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < cfg.button_min_area_ratio * screen_area or h <= 0:
            continue
        if area > cfg.button_max_area_ratio * screen_area:
            continue
        if w / h < cfg.button_aspect_min:
            continue
        cy = centroids[i][1] / height
        if not cfg.button_y_min <= cy <= cfg.button_y_max:
            continue
        if area > best_area:
            best_area = area
            best = (int(centroids[i][0]), int(centroids[i][1]))
    return best


def find_back_button(img: np.ndarray, cfg: Config) -> Optional[Tuple[int, int]]:
    """查找排行榜等子页面左下角的圆形返回按钮。"""
    hsv = _hsv(img)
    sat, val = hsv[..., 1], hsv[..., 2]
    mask = (((val >= cfg.button_val_min) & (sat <= cfg.button_sat_max))
            .astype(np.uint8)) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    labels, stats, centroids = _components(mask)

    height, width = img.shape[:2]
    screen_area = height * width
    best: Optional[Tuple[int, int]] = None
    best_area = 0
    for i in range(1, labels.max() + 1):
        x, y, w, h, area = (int(v) for v in stats[i])
        if not (cfg.back_button_area_min_ratio * screen_area
                <= area <= cfg.back_button_area_max_ratio * screen_area):
            continue
        if h <= 0 or not 0.8 <= w / h <= 1.25:
            continue
        cx, cy = centroids[i]
        if cy < cfg.back_button_y_min * height or cx > cfg.back_button_x_max * width:
            continue
        if area > best_area:
            best_area = area
            best = (int(cx), int(cy))
    return best


def find_dialog_cancel(img: np.ndarray, cfg: Config) -> Optional[Tuple[int, int]]:
    """识别系统/微信的确认弹窗（例如广告跳转确认），返回「取消」按钮位置。"""
    hsv = _hsv(img)
    sat, val = hsv[..., 1], hsv[..., 2]
    mask = (((val >= 235) & (sat <= 25)).astype(np.uint8)) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    labels, stats, centroids = _components(mask)

    height, width = img.shape[:2]
    screen_area = height * width
    best: Optional[Tuple[int, int]] = None
    best_area = 0
    for i in range(1, labels.max() + 1):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area < 0.06 * screen_area or h <= 0:
            continue
        if not 1.5 <= w / h <= 3.0:
            continue
        if not 0.35 <= centroids[i][1] / height <= 0.65:
            continue
        if area > best_area:
            best_area = area
            # 弹窗底部左侧为「取消」
            best = (int(x + 0.35 * w), int(y + 0.80 * h))
    return best


# ----------------------------------------------------------------------
# 场景
# ----------------------------------------------------------------------
def detect_scene(img: np.ndarray, cfg: Config) -> Scene:
    """识别当前画面状态并给出跳跃所需的几何信息。"""
    piece = detect_piece(img, cfg)

    cancel = find_dialog_cancel(img, cfg)
    if cancel is not None:
        return Scene(state="game_over", restart_point=cancel, note="dialog")

    restart = find_restart_button(img, cfg)
    if restart is not None:
        return Scene(state="game_over", restart_point=restart, note="result_button")

    # 排行榜等子页面没有棋子；游戏进行中若把白色圆盘平台误认成返回按钮，
    # 会导致把正常画面当结算页而中断
    if piece is None:
        back = find_back_button(img, cfg)
        if back is not None:
            return Scene(state="game_over", restart_point=back, note="back_button")

    platforms = detect_platforms(img, cfg, piece=piece)
    if piece is None or not platforms:
        reasons = []
        if piece is None:
            reasons.append("no_piece")
        if not platforms:
            reasons.append("no_platform")
        return Scene(state="unknown", piece=piece, platforms=platforms,
                     note=",".join(reasons))

    ordered = sorted(
        platforms,
        key=lambda p: math.hypot(p.center[0] - piece.base[0], p.center[1] - piece.base[1]),
    )
    # 棋子脚下的平台离脚底很近；若最近的平台都远，说明当前平台没识别出来，
    # 此时最近的平台就是目标，不能误当成「当前平台」而把目标推给次近的。
    radius = cfg.current_max_distance_ratio * min(img.shape[0], img.shape[1])
    nearest = ordered[0]
    gap = math.hypot(nearest.center[0] - piece.base[0], nearest.center[1] - piece.base[1])
    if (gap <= radius
            and abs(nearest.center[1] - piece.base[1]) <= cfg.current_max_vertical_px
            and abs(nearest.center[0] - piece.base[0]) <= cfg.current_max_horizontal_px):
        current = nearest
        candidates = ordered[1:]
        ref_y = current.center[1]
    else:
        current = None
        candidates = ordered
        ref_y = piece.base[1]
    if not candidates:
        return Scene(state="unknown", piece=piece, current=current,
                     platforms=platforms,
                     note="no_target" if current is not None else "no_current")
    # 下一块平台一定在「前方」，即画面上更靠上；来时的平台都在下方，
    # 不能因为它离棋子更近就当成目标。拆分出的半块平台通常更近更低，
    # 因此取最靠前的那块。
    ahead = [p for p in candidates if p.center[1] < ref_y - cfg.target_min_ahead_px]
    if not ahead:
        # 其余平台都在后方，说明真正的目标没识别出来，宁可停下也不乱跳
        return Scene(state="unknown", piece=piece, current=current,
                     platforms=platforms, note="target_behind")
    target = min(ahead, key=lambda p: p.center[1])
    distance = math.hypot(target.center[0] - piece.base[0],
                          target.center[1] - piece.base[1])
    return Scene(state="playing", piece=piece, current=current, target=target,
                 distance=distance, platforms=platforms)


# ----------------------------------------------------------------------
# 调试
# ----------------------------------------------------------------------
def annotate(img: np.ndarray, scene: Scene) -> np.ndarray:
    """把识别结果画到图上，便于人工核对。"""
    out = img.copy()
    if scene.piece:
        x, y, w, h = scene.piece.bbox
        cv2.rectangle(out, (x, y), (x + w, y + h), (0, 220, 0), 3)
        cv2.circle(out, (int(scene.piece.base[0]), int(scene.piece.base[1])), 10, (0, 220, 0), -1)
    if scene.current:
        cx, cy = (int(v) for v in scene.current.center)
        cv2.circle(out, (cx, cy), 12, (255, 140, 0), 3)
        cv2.putText(out, "current", (cx - 60, cy - 20), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (255, 140, 0), 3)
    if scene.target:
        cx, cy = (int(v) for v in scene.target.center)
        cv2.circle(out, (cx, cy), 14, (0, 0, 255), 3)
        cv2.putText(out, "target", (cx - 50, cy - 20), cv2.FONT_HERSHEY_SIMPLEX,
                    1.0, (0, 0, 255), 3)
    if scene.distance is not None and scene.piece and scene.target:
        p1 = (int(scene.piece.base[0]), int(scene.piece.base[1]))
        p2 = (int(scene.target.center[0]), int(scene.target.center[1]))
        cv2.line(out, p1, p2, (255, 0, 255), 3)
        mid = ((p1[0] + p2[0]) // 2, (p1[1] + p2[1]) // 2)
        cv2.putText(out, f"d={scene.distance:.0f}px", mid, cv2.FONT_HERSHEY_SIMPLEX,
                    1.1, (255, 0, 255), 3)
    if scene.restart_point:
        cv2.circle(out, scene.restart_point, 24, (0, 255, 255), 4)
    cv2.putText(out, scene.state.upper(), (30, 220), cv2.FONT_HERSHEY_SIMPLEX,
                1.4, (0, 0, 0), 4)
    return out
