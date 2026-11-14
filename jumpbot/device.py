"""ADB 设备封装：截屏、按压、屏幕尺寸。"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np


class DeviceError(RuntimeError):
    """ADB 操作失败。"""


@dataclass
class DeviceInfo:
    serial: str
    width: int
    height: int

    @property
    def area(self) -> int:
        return self.width * self.height


class AdbDevice:
    """通过 adb 驱动一台 Android 设备。"""

    def __init__(self, adb_path: str = "adb", serial: Optional[str] = None,
                 timeout: float = 30.0) -> None:
        self.adb_path = adb_path
        self.serial = serial
        self.timeout = timeout
        self._info: Optional[DeviceInfo] = None

    # ------------------------------------------------------------------
    # 基础命令
    # ------------------------------------------------------------------
    def _base(self) -> list[str]:
        cmd = [self.adb_path]
        if self.serial:
            cmd += ["-s", self.serial]
        return cmd

    def _run(self, args: list[str], binary: bool = False) -> bytes | str:
        cmd = self._base() + args
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=self.timeout,
                                  check=False)
        except FileNotFoundError as exc:
            raise DeviceError(f"找不到 adb 可执行文件: {self.adb_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise DeviceError(f"adb 命令超时: {' '.join(args)}") from exc
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "ignore").strip()
            raise DeviceError(f"adb 命令失败({proc.returncode}): {' '.join(args)} :: {err}")
        return proc.stdout if binary else proc.stdout.decode("utf-8", "ignore")

    # ------------------------------------------------------------------
    # 设备信息
    # ------------------------------------------------------------------
    def list_devices(self) -> list[str]:
        out = self._run(["devices"])
        serials = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if line and "\tdevice" in line:
                serials.append(line.split("\t", 1)[0])
        return serials

    def connect_check(self) -> DeviceInfo:
        """确认设备在线并返回屏幕信息。"""
        if not self.serial:
            serials = self.list_devices()
            if not serials:
                raise DeviceError("未检测到已连接设备，请先执行 adb devices 确认")
            self.serial = serials[0]
            if len(serials) > 1:
                # 多设备时明确提示，避免操作错设备
                raise DeviceError(
                    f"检测到多台设备 {serials}，请用 --serial 指定目标设备")
        info = self._info
        if info is None:
            raw = self._run(["shell", "wm", "size"]).strip()
            # 形如 "Physical size: 1080x2408" 或 "Override size: 1080x2408"
            size_line = raw.splitlines()[-1]
            dims = size_line.split(":")[-1].strip()
            w, h = (int(v) for v in dims.lower().split("x"))
            info = DeviceInfo(serial=self.serial, width=w, height=h)
            self._info = info
        return info

    # ------------------------------------------------------------------
    # 画面
    # ------------------------------------------------------------------
    def screenshot(self) -> np.ndarray:
        """截屏并解码为 BGR 图像。"""
        raw = self._run(["exec-out", "screencap", "-p"], binary=True)
        if not isinstance(raw, bytes) or len(raw) < 1024:
            raise DeviceError("截屏数据为空")
        buf = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is None:
            raise DeviceError("截屏数据无法解码为图片")
        return img

    # ------------------------------------------------------------------
    # 输入
    # ------------------------------------------------------------------
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> None:
        self._run(["shell", "input", "swipe", str(x1), str(y1),
                   str(x2), str(y2), str(int(duration_ms))])

    def tap(self, x: int, y: int) -> None:
        self._run(["shell", "input", "tap", str(x), str(y)])

    def keyevent(self, key: str) -> None:
        self._run(["shell", "input", "keyevent", key])

    def press(self, x: int, y: int, duration_ms: int,
              method: str = "swipe") -> None:
        """在 (x, y) 处按住 duration_ms 后松开。"""
        duration_ms = int(round(duration_ms))
        if method == "swipe":
            # 起点终点相同，等价于长按；duration 由系统精确插值
            self.swipe(x, y, x, y, duration_ms)
        elif method == "motionevent":
            self._run(["shell", "input", "motionevent", "DOWN", str(x), str(y)])
            time.sleep(max(0.0, duration_ms / 1000.0))
            self._run(["shell", "input", "motionevent", "UP", str(x), str(y)])
        else:
            raise ValueError(f"未知按压方式: {method}")
