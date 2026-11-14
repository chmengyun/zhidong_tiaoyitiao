"""参数配置：默认值 + JSON 覆盖 + 命令行覆盖。"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional


@dataclass
class Config:
    # ---------- 设备 ----------
    adb_path: str = "adb"
    serial: Optional[str] = None

    # ---------- 跳跃换算 ----------
    # 按压时长(ms) = 距离(px) * press_per_px
    press_per_px: float = 1.35
    min_press_ms: int = 120
    max_press_ms: int = 1600
    # adb input swipe 的固定耗时补偿（ms），由实测校准
    press_overhead_ms: int = 0
    # swipe: 单次 adb swipe 命令；motionevent: DOWN/sleep/UP
    press_method: str = "swipe"
    # 各设备的按压换算档案，连接设备后自动套用（serial / resolution 留空表示通配）
    # 示例：[{"serial": "你的设备序列号", "resolution": "1080x2408",
    #        "press_per_px": 1.20, "press_overhead_ms": 20}]
    press_profiles: list = field(default_factory=list)

    # ---------- 流程节奏 ----------
    settle_ms: int = 900          # 每次跳跃后等待游戏稳定的时间
    screenshot_delay_ms: int = 120  # 截屏前等待渲染完成
    max_jumps: int = 300
    restart_retries: int = 3
    unknown_retries: int = 5
    # 人工处理后，连续多帧识别一致才恢复自动跳跃
    resume_confirm_frames: int = 2
    resume_confirm_tolerance_px: float = 12.0

    # ---------- 画面区域 ----------
    region_top_ratio: float = 0.08     # 忽略顶部（微信胶囊按钮）
    region_bottom_ratio: float = 0.95  # 忽略底部（广告条 / 系统手势条）

    # ---------- 平台识别 ----------
    bg_sample_top: float = 0.10    # 背景采样区（画面顶部纯净区域）
    bg_sample_bottom: float = 0.32
    platform_sat_delta: int = 8    # 与背景饱和度的差
    platform_val_delta: int = 15   # 比背景更亮的幅度
    # 比背景更暗但色相明显不同的平台（灰绿色圆桌面等）：色相差阈值 + 最低饱和度
    platform_hue_delta: int = 12
    platform_hue_sat_min: int = 40
    # 白色圆柱顶面：高亮 + 低饱和，用于从白色条纹里分离出顶面
    white_top_sat_max: int = 40
    white_top_val_min: int = 190
    # 白色顶面的最小面积：小方块顶面只有常规平台的一半左右，仍应作为平台候选
    white_top_min_area_ratio: float = 0.0004
    # 同色方块竖切后，单侧平台的顶点居中程度上限（越严越不会误切）
    seam_symmetry_max: float = 0.06
    platform_min_area_ratio: float = 0.006  # 平台面积 / 全屏面积，药瓶等小平台也要认
    # 迷你平台（小圆凳 / 线轴）面积只有常规平台的一半以下，只有轮廓非常对称
    # 时才接受，避免把拆分出的半块平台当成平台
    platform_small_area_ratio: float = 0.0028
    platform_small_symmetry_max: float = 0.03
    # 迷你平台的顶盖很宽、支腿细，整体长宽比会低于细高平台下限
    platform_small_aspect_min: float = 0.42
    platform_max_area_ratio: float = 0.06   # 超过此面积认为是多块平台粘连
    platform_aspect_min: float = 0.85
    platform_aspect_max: float = 2.80
    # 细高圆柱类平台（卷纸筒等）允许的下限，需轮廓干净对称
    platform_aspect_relax_min: float = 0.48
    # 圆桌 / 圆盘类平台（盘面 + 细支腿）填充率下限，这类平台填不满外接框
    platform_disc_fill_min: float = 0.55
    platform_fill_min: float = 0.30    # 轮廓面积 / 外接框面积，排除细长区域
    # 组件最上方顶点允许偏离外接框中心的比例，超过说明是多块平台粘连
    platform_top_symmetry_max: float = 0.12
    # 暗色中性平台（深灰圆凳 / 线轴）与阴影同色同饱和、只是更暗，只能靠
    # 「比背景暗得多」来区分：暗度阈值 + 饱和度上限 + 区域亮度下限 / 最小面积
    dark_platform_val_delta: int = 60
    dark_platform_sat_max: int = 45
    dark_platform_val_min: int = 90
    dark_platform_min_area_ratio: float = 0.0025
    # 迷你圆凳 / 线轴的亮色顶盖：比背景亮多少（相对背景亮度）、最小面积
    cap_val_delta: int = 12
    cap_min_area_ratio: float = 0.0004

    # 粘连平台拆分（按亮度分峰）
    split_peak_ratio: float = 0.08
    split_tolerance: int = 16
    split_max_area_ratio: float = 0.35  # 过大的连通域不再尝试拆分
    split_min_area_ratio: float = 0.010
    split_aspect_min: float = 0.85  # 拆分出的方块顶面接近正方形
    split_aspect_max: float = 2.60
    split_fill_min: float = 0.38
    split_fill_max: float = 0.92
    split_top_width_max: float = 0.60
    # 腐蚀找种子时，种子最小面积 / 全屏面积
    split_seed_area_ratio: float = 0.004
    # 棋子脚下平台与脚底的最大距离（占屏幕短边比例），超过则认为当前平台未识别
    current_max_distance_ratio: float = 0.22
    # 当前平台顶面中心与棋子脚底的纵向差上限：目标平台总明显更靠上
    current_max_vertical_px: int = 110
    # 当前平台顶面中心与脚底的水平差上限：目标平台总明显偏左/偏右
    current_max_horizontal_px: int = 100
    # 目标平台必须比当前平台「更靠前」（画面上更靠上）的最小像素差
    target_min_ahead_px: int = 30

    # ---------- 棋子识别 ----------
    piece_hue_min: int = 100        # OpenCV H 范围 0-179
    piece_hue_max: int = 150
    piece_sat_min: int = 70
    piece_val_max: int = 155
    piece_min_area_ratio: float = 0.0003

    # ---------- 结算界面 ----------
    button_val_min: int = 240
    button_sat_max: int = 12
    button_aspect_min: float = 2.5
    button_min_area_ratio: float = 0.015
    button_max_area_ratio: float = 0.05
    # 按钮纵向位置区间（相对全屏高度），用于排除底部广告条
    button_y_min: float = 0.55
    button_y_max: float = 0.82
    # 排行榜等子页面的圆形返回按钮（位于左下方）
    back_button_y_min: float = 0.78
    back_button_x_max: float = 0.40
    back_button_area_min_ratio: float = 0.002
    back_button_area_max_ratio: float = 0.04
    # 找不到按钮时的兜底点击坐标（相对全屏比例），按顺序尝试
    restart_points: tuple = ((0.64, 0.71), (0.50, 0.71))

    # ---------- 画面稳定判定 ----------
    stability_diff: float = 1.5     # 相邻帧平均灰度差阈值
    stability_timeout: float = 3.0  # 最长等待秒数

    # ---------- 输出 ----------
    debug_dir: str = "debug_frames"
    save_debug_frames: bool = True
    log_csv: str = "logs/jumps.csv"

    # ------------------------------------------------------------------
    def match_press_profile(self, serial: Optional[str], width: int, height: int
                            ) -> Optional[dict]:
        """按「识别码 + 分辨率」挑出最精确的按压档案。

        同时匹配识别码与分辨率的档案优先，其次只匹配识别码，再次只匹配分辨率；
        某项留空视为通配。
        """
        resolution = f"{width}x{height}"
        best: Optional[dict] = None
        best_score = -1
        for profile in self.press_profiles:
            if not isinstance(profile, dict):
                continue
            p_serial = profile.get("serial")
            p_res = profile.get("resolution")
            if p_serial and p_serial != serial:
                continue
            if p_res and p_res != resolution:
                continue
            score = (2 if p_serial else 0) + (1 if p_res else 0)
            if score > best_score:
                best, best_score = profile, score
        return best

    @staticmethod
    def _profile_label(profile: dict) -> str:
        label = str(profile.get("note") or "").strip()
        if label:
            return label
        parts = [str(v) for v in (profile.get("serial"), profile.get("resolution")) if v]
        return "@".join(parts) or "默认档案"

    def apply_device_profile(self, serial: Optional[str], width: int, height: int
                             ) -> Optional[str]:
        """套用匹配到的设备按压档案，返回档案标识；没有匹配则返回 None。"""
        profile = self.match_press_profile(serial, width, height)
        if profile is None:
            return None
        for key in ("press_per_px", "press_overhead_ms"):
            if key in profile:
                setattr(self, key, profile[key])
        return self._profile_label(profile)

    def save_press_profile(self, serial: Optional[str], width: int, height: int,
                           press_per_px: float, press_overhead_ms: int) -> str:
        """把校准结果写回该设备的档案（没有则新增），返回档案标识。"""
        resolution = f"{width}x{height}"
        profile = self.match_press_profile(serial, width, height)
        if profile is None:
            profile = {"serial": serial, "resolution": resolution}
            self.press_profiles.append(profile)
        profile["press_per_px"] = round(float(press_per_px), 4)
        profile["press_overhead_ms"] = int(press_overhead_ms)
        return self._profile_label(profile)

    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Optional[str | Path] = None) -> "Config":
        """从 JSON 载入配置；未提供的字段沿用默认值。"""
        cfg = cls()
        if path is None:
            return cfg
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"配置文件不存在: {p}")
        data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        valid = {f.name for f in fields(cls)}
        unknown = set(data) - valid
        if unknown:
            raise ValueError(f"配置文件包含未知字段: {sorted(unknown)}")
        for key, value in data.items():
            if key == "restart_points":
                value = tuple(tuple(v) for v in value)
            setattr(cfg, key, value)
        return cfg

    def save(self, path: str | Path) -> None:
        Path(path).write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["restart_points"] = [list(p) for p in self.restart_points]
        return data

    def validate(self) -> None:
        if self.press_per_px <= 0:
            raise ValueError("press_per_px 必须为正数")
        if self.min_press_ms >= self.max_press_ms:
            raise ValueError("min_press_ms 必须小于 max_press_ms")
        if not 0 <= self.region_top_ratio < self.region_bottom_ratio <= 1:
            raise ValueError("region_top_ratio / region_bottom_ratio 取值非法")
        if self.press_method not in ("swipe", "motionevent"):
            raise ValueError("press_method 只能是 swipe 或 motionevent")
        if self.resume_confirm_frames < 1:
            raise ValueError("resume_confirm_frames 必须至少为 1")
        if self.resume_confirm_tolerance_px < 0:
            raise ValueError("resume_confirm_tolerance_px 不能为负数")
