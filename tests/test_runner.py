"""自动运行中的安全暂停与人工恢复测试。"""

from __future__ import annotations

import math
import unittest
from unittest.mock import Mock, patch

import numpy as np

from jumpbot.config import Config
from jumpbot.runner import AutoJumper, JumpRecord
from jumpbot.vision import Piece, Platform, Scene

IMG = np.zeros((100, 100, 3), dtype=np.uint8)


def playing_scene(base=(100.0, 500.0), target=(300.0, 300.0)) -> Scene:
    piece = Piece(bbox=(90, 400, 20, 100), area=1000, base=base)
    platform = Platform(bbox=(250, 250, 100, 100), area=5000, center=target)
    return Scene(state="playing", piece=piece, target=platform,
                 distance=math.hypot(target[0] - base[0], target[1] - base[1]))


class FakeDevice:
    def __init__(self) -> None:
        self.presses = []

    def press(self, x, y, duration_ms, method="swipe") -> None:
        self.presses.append((x, y, duration_ms, method))


class HumanRecoveryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Config(settle_ms=0, unknown_retries=2,
                          resume_confirm_frames=2,
                          resume_confirm_tolerance_px=12,
                          save_debug_frames=False)
        self.device = FakeDevice()
        self.jumper = AutoJumper(self.device, self.cfg, debug_dir=None, csv_path=None)

    def set_scenes(self, *scenes: Scene) -> None:
        self.jumper.grab_stable = Mock(
            side_effect=[(scene, IMG) for scene in scenes])

    def run_with_input(self, answers) -> None:
        with patch("builtins.input", side_effect=answers), \
                patch("jumpbot.runner.time.sleep"):
            self.stats = self.jumper.run(max_jumps=1)

    def test_retry_exhausted_can_quit_without_press(self) -> None:
        self.set_scenes(Scene(state="unknown", note="no_target"))
        self.cfg.unknown_retries = 1
        self.run_with_input(["q"])
        self.assertEqual(self.stats.jumps, 0)
        self.assertEqual(self.device.presses, [])

    def test_matching_frames_resume_and_jump_once(self) -> None:
        unknown = Scene(state="unknown", note="no_target")
        self.set_scenes(unknown, unknown, playing_scene(), playing_scene(),
                        playing_scene())
        self.run_with_input(["r"])
        self.assertEqual(self.stats.jumps, 1)
        self.assertEqual(len(self.device.presses), 1)
        self.assertEqual(self.jumper.grab_stable.call_count, 5)

    def test_inconsistent_frames_stay_paused(self) -> None:
        self.cfg.unknown_retries = 1
        self.set_scenes(Scene(state="unknown"), playing_scene(),
                        playing_scene(target=(330.0, 300.0)))
        self.run_with_input(["r", "q"])
        self.assertEqual(self.stats.jumps, 0)
        self.assertEqual(self.device.presses, [])

    def test_pending_jump_is_interrupted_by_human_takeover(self) -> None:
        self.cfg.unknown_retries = 1
        record = JumpRecord(index=1, distance=200, press_ms=300, clamped=False,
                            piece_base=(100, 500), target_center=(300, 300))
        self.jumper._pending = record
        self.jumper._log = Mock()
        self.set_scenes(Scene(state="unknown"))
        self.run_with_input(["q"])
        self.assertEqual(record.result, "interrupted")
        self.jumper._log.assert_called_once_with(record)
        self.assertIsNone(self.jumper._pending)
        self.assertEqual(self.stats.success, 0)
        self.assertEqual(self.stats.failed, 0)

    def test_failed_auto_restart_also_waits_for_human(self) -> None:
        self.jumper.recover = Mock(return_value=False)
        self.set_scenes(Scene(state="game_over"), playing_scene(), playing_scene(),
                        playing_scene())
        self.run_with_input([""])
        self.jumper.recover.assert_called_once_with()
        self.assertEqual(self.stats.restarts, 0)
        self.assertEqual(self.stats.jumps, 1)
        self.assertEqual(len(self.device.presses), 1)

    def test_eof_ends_safely_without_press(self) -> None:
        self.cfg.unknown_retries = 1
        self.set_scenes(Scene(state="unknown"))
        with patch("builtins.input", side_effect=EOFError), \
                patch("jumpbot.runner.time.sleep"):
            stats = self.jumper.run(max_jumps=1)
        self.assertEqual(stats.jumps, 0)
        self.assertEqual(self.device.presses, [])


if __name__ == "__main__":
    unittest.main()
