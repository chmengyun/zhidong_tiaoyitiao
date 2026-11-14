"""自动跳跃主循环：截屏 -> 识别 -> 按压 -> 校验 -> 记录。"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from .config import Config
from .device import AdbDevice
from .planner import distance_between, plan_jump
from .vision import Scene, annotate, detect_scene


@dataclass
class JumpRecord:
    index: int
    distance: float
    press_ms: int
    clamped: bool
    piece_base: tuple[float, float]
    target_center: tuple[float, float]
    result: str = "pending"
    residual: Optional[float] = None  # 落点沿跳跃方向的偏差(px)，正=跳远了


@dataclass
class RunStats:
    jumps: int = 0
    success: int = 0
    failed: int = 0
    restarts: int = 0
    records: list[JumpRecord] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        done = self.success + self.failed
        return self.success / done if done else 0.0


class AutoJumper:
    """把设备、识别、按压串成一个自动循环。"""

    def __init__(self, device: AdbDevice, cfg: Config, debug_dir: Optional[Path] = None,
                 csv_path: Optional[Path] = None) -> None:
        self.device = device
        self.cfg = cfg
        self.debug_dir = debug_dir
        self.csv_path = csv_path
        self.stats = RunStats()
        self._pending: Optional[JumpRecord] = None
        self._csv_file = None
        self._csv_writer = None
        if self.debug_dir is not None:
            self.debug_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    def _open_log(self) -> None:
        if self.csv_path is None:
            return
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        is_new = not self.csv_path.exists()
        self._csv_file = self.csv_path.open("a", newline="", encoding="utf-8")
        self._csv_writer = csv.writer(self._csv_file)
        if is_new:
            self._csv_writer.writerow(
                ["time", "index", "distance_px", "press_ms", "clamped", "result",
                 "residual_px"])

    def _log(self, record: JumpRecord) -> None:
        if self._csv_writer is None:
            return
        self._csv_writer.writerow([
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            record.index,
            f"{record.distance:.1f}",
            record.press_ms,
            int(record.clamped),
            record.result,
            "" if record.residual is None else f"{record.residual:.1f}",
        ])
        self._csv_file.flush()

    # ------------------------------------------------------------------
    def grab(self) -> Scene:
        img = self.device.screenshot()
        return detect_scene(img, self.cfg)

    def grab_stable(self) -> tuple[Scene, "np.ndarray"]:
        """连续截屏直到画面静止（相机滚动结束），避免按错误坐标起跳。"""
        deadline = time.time() + self.cfg.stability_timeout
        prev = None
        img = None
        scene = None
        while True:
            img = self.device.screenshot()
            scene = detect_scene(img, self.cfg)
            small = cv2.resize(img, (108, 240))
            if prev is not None and scene.state == "playing":
                diff = float(np.mean(cv2.absdiff(small, prev)))
                if diff < self.cfg.stability_diff:
                    return scene, img
            if time.time() >= deadline:
                return scene, img
            prev = small
            time.sleep(0.12)

    def _save_frame(self, tag: str, img, scene: Scene) -> Optional[Path]:
        if self.debug_dir is None or not self.cfg.save_debug_frames:
            return None
        # 原图用于离线复现识别问题；标注图用于人工核对
        path = self.debug_dir / f"{tag}.png"
        cv2.imwrite(str(path), img)
        cv2.imwrite(str(self.debug_dir / f"{tag}_ann.png"), annotate(img, scene))
        return path

    # ------------------------------------------------------------------
    def recover(self, max_attempts: int = 5) -> bool:
        """从结算界面 / 排行榜 / 弹窗 / 广告页回到游戏内。"""
        info = self.device.connect_check()
        fallback = [(int(px * info.width), int(py * info.height))
                    for px, py in self.cfg.restart_points]
        back_presses = 0

        for _ in range(max_attempts):
            scene, _ = self.grab_stable()
            if scene.state == "playing":
                return True
            if scene.restart_point is not None:
                self.device.tap(*scene.restart_point)
            elif back_presses < 1:
                # 无法识别且没有可点按钮时，用返回键退出广告页等（只按一次，避免退出小程序）
                self.device.keyevent("BACK")
                back_presses += 1
            else:
                self.device.tap(*fallback[0])
                fallback.append(fallback.pop(0))
            time.sleep(1.5)

        return self.grab().state == "playing"

    # ------------------------------------------------------------------
    def _confirm_playing(self) -> bool:
        """连续多帧识别一致才允许恢复；这里只截屏，不操作设备。"""
        previous: Optional[Scene] = None
        for _ in range(self.cfg.resume_confirm_frames):
            scene, _ = self.grab_stable()
            if scene.state != "playing" or scene.piece is None or scene.target is None:
                return False
            if previous is not None:
                tolerance = self.cfg.resume_confirm_tolerance_px
                if (distance_between(previous.piece.base, scene.piece.base) > tolerance
                        or distance_between(previous.target.center,
                                            scene.target.center) > tolerance):
                    return False
            previous = scene
        return previous is not None

    def _pause_for_human(self, reason: str,
                         frame_path: Optional[Path] = None) -> bool:
        """请求人工处理；确认恢复返回 True，用户主动退出返回 False。"""
        if self._pending is not None:
            interrupted = self._pending
            interrupted.result = "interrupted"
            self._log(interrupted)
            self._pending = None
            print(f"[{interrupted.index:>3}] 上一跳结果无法确认，已记为 interrupted")

        print(f"\n\a{reason}")
        print("已安全暂停，不会按压或点击。请检查手机，处理到可跳跃的游戏画面。")
        if frame_path is not None:
            print(f"原图: {frame_path.resolve()}")
            ann_path = frame_path.with_name(f"{frame_path.stem}_ann.png")
            print(f"标注图: {ann_path.resolve()}")
        print("处理后按回车（或输入 r）重新识别；输入 q 正常退出。")

        while True:
            try:
                answer = input("人工接管 > ").strip().lower()
            except EOFError:
                print("当前终端不可交互，安全结束运行")
                return False
            if answer == "q":
                return False
            if answer not in ("", "r"):
                print("请输入回车、r 或 q")
                continue
            if self._confirm_playing():
                print("画面识别已恢复，继续自动跳跃")
                return True
            print("仍无法确认棋子与目标，保持暂停；请继续处理手机或输入 q 退出")

    # ------------------------------------------------------------------
    @staticmethod
    def _landing_error(record: JumpRecord, scene: Scene) -> Optional[float]:
        """落点相对所在平台中心的偏差，沿上次跳跃方向投影（正=跳远）。

        注意：跳跃后镜头会移动，必须用「同一帧内」的棋子脚底与平台中心计算。
        """
        if scene.current is None or scene.piece is None:
            return None
        dx = record.target_center[0] - record.piece_base[0]
        dy = record.target_center[1] - record.piece_base[1]
        norm = (dx * dx + dy * dy) ** 0.5
        if norm < 1e-6:
            return None
        ux, uy = dx / norm, dy / norm
        ex = scene.piece.base[0] - scene.current.center[0]
        ey = scene.piece.base[1] - scene.current.center[1]
        return ex * ux + ey * uy

    # ------------------------------------------------------------------
    def jump_once(self, scene: Scene, dry_run: bool = False) -> JumpRecord:
        """根据当前画面执行一次跳跃（返回记录，不负责等待）。"""
        assert scene.piece and scene.target and scene.distance is not None
        plan = plan_jump(scene.distance, self.cfg)
        record = JumpRecord(
            index=self.stats.jumps + 1,
            distance=scene.distance,
            press_ms=plan.press_ms,
            clamped=plan.clamped,
            piece_base=scene.piece.base,
            target_center=scene.target.center,
        )
        if dry_run:
            return record

        x = int(round(scene.piece.base[0]))
        y = int(round(scene.piece.base[1]))
        self.device.press(x, y, plan.press_ms, method=self.cfg.press_method)
        return record

    # ------------------------------------------------------------------
    def run(self, max_jumps: Optional[int] = None, dry_run: bool = False) -> RunStats:
        cfg = self.cfg
        limit = max_jumps if max_jumps is not None else cfg.max_jumps
        self._open_log()
        unknown_streak = 0

        try:
            while self.stats.jumps < limit:
                if not dry_run:
                    time.sleep(cfg.settle_ms / 1000.0)
                scene, img = self.grab_stable()

                # 1) 结算界面：记录上一跳为失败，然后恢复游戏
                if scene.state == "game_over":
                    if self._pending is not None:
                        failed = self._pending
                        failed.result = "failed"
                        self._log(failed)
                        self.stats.failed += 1
                        self._pending = None
                        print(f"[{failed.index:>3}] 失败：距离 "
                              f"{failed.distance:.0f}px / 按压 {failed.press_ms}ms")
                    frame_path = self._save_frame(
                        f"{self.stats.jumps:03d}_gameover", img, scene)
                    print(f"检测到结算界面({scene.note})，尝试重开…")
                    if not self.recover():
                        if not self._pause_for_human("无法自动重开，需要人工帮助",
                                                    frame_path):
                            break
                        unknown_streak = 0
                        continue
                    self.stats.restarts += 1
                    continue

                # 2) 无法识别：自动重试仍失败则请求人工处理，绝不猜测目标按压
                if scene.state != "playing":
                    unknown_streak += 1
                    frame_path = self._save_frame(
                        f"{self.stats.jumps:03d}_unknown", img, scene)
                    if unknown_streak >= cfg.unknown_retries:
                        reason = (f"连续 {unknown_streak} 次无法识别画面"
                                  f"（{scene.note or 'unknown'}），需要人工帮助")
                        if not self._pause_for_human(reason, frame_path):
                            break
                        unknown_streak = 0
                        continue
                    time.sleep(0.4)
                    continue
                unknown_streak = 0

                # 上一跳已确认落地成功；顺便测量落点偏差，用于校准换算系数
                if self._pending is not None:
                    self._pending.result = "success"
                    self._pending.residual = self._landing_error(self._pending, scene)
                    self._log(self._pending)
                    self.stats.success += 1
                    if self._pending.residual is not None:
                        print(f"     └ 落点偏差 {self._pending.residual:+.0f}px")
                    self._pending = None

                self._save_frame(f"{self.stats.jumps:03d}_before", img, scene)
                record = self.jump_once(scene, dry_run=dry_run)
                self.stats.jumps += 1
                self.stats.records.append(record)

                print(f"[{record.index:>3}] 距离 {record.distance:7.1f}px  "
                      f"按压 {record.press_ms:>4}ms"
                      f"{'  (已截断)' if record.clamped else ''}")

                if dry_run:
                    continue

                self._pending = record
                time.sleep(cfg.settle_ms / 1000.0)
        finally:
            if self._csv_file:
                self._csv_file.close()
        return self.stats
