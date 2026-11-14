"""换算与配置测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from jumpbot.config import Config
from jumpbot.planner import distance_between, plan_jump, suggest_press_per_px

ROOT = Path(__file__).resolve().parent.parent


class PlannerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Config()

    def test_linear_mapping(self) -> None:
        plan = plan_jump(400, self.cfg)
        self.assertAlmostEqual(plan.raw_press_ms, 400 * 1.35)
        self.assertEqual(plan.press_ms, round(400 * 1.35))
        self.assertFalse(plan.clamped)

    def test_overhead_applied(self) -> None:
        self.cfg.press_overhead_ms = 50
        plan = plan_jump(200, self.cfg)
        self.assertEqual(plan.press_ms, round(200 * 1.35 + 50))

    def test_clamp_low(self) -> None:
        plan = plan_jump(5, self.cfg)
        self.assertEqual(plan.press_ms, self.cfg.min_press_ms)
        self.assertTrue(plan.clamped)

    def test_clamp_high(self) -> None:
        plan = plan_jump(5000, self.cfg)
        self.assertEqual(plan.press_ms, self.cfg.max_press_ms)
        self.assertTrue(plan.clamped)

    def test_distance_between(self) -> None:
        self.assertAlmostEqual(distance_between((0, 0), (3, 4)), 5.0)

    def test_suggest_coefficient(self) -> None:
        self.assertAlmostEqual(suggest_press_per_px(500, 700), 1.4)
        self.assertAlmostEqual(suggest_press_per_px(500, 750, overhead_ms=50), 1.4)
        with self.assertRaises(ValueError):
            suggest_press_per_px(0, 500)


class ConfigTest(unittest.TestCase):
    def test_default_config_file_is_valid(self) -> None:
        cfg = Config.load(ROOT / "config.json")
        cfg.validate()
        self.assertGreater(cfg.press_per_px, 0)

    def test_roundtrip(self) -> None:
        cfg = Config(press_per_px=1.7)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            cfg.save(path)
            loaded = Config.load(path)
        self.assertAlmostEqual(loaded.press_per_px, 1.7)
        self.assertEqual(loaded.restart_points, cfg.restart_points)

    def test_unknown_field_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            path.write_text(json.dumps({"nope": 1}), encoding="utf-8")
            with self.assertRaises(ValueError):
                Config.load(path)

    def test_validate_rejects_bad_values(self) -> None:
        with self.assertRaises(ValueError):
            Config(press_per_px=0).validate()
        with self.assertRaises(ValueError):
            Config(min_press_ms=500, max_press_ms=100).validate()
        with self.assertRaises(ValueError):
            Config(resume_confirm_frames=0).validate()
        with self.assertRaises(ValueError):
            Config(resume_confirm_tolerance_px=-1).validate()


class DeviceProfileTest(unittest.TestCase):
    """按设备识别码 / 分辨率自动切换按压系数。"""

    def setUp(self) -> None:
        self.cfg = Config(press_per_px=1.35, press_overhead_ms=20, press_profiles=[
            {"serial": "OLD", "resolution": "1080x2408", "note": "旧机",
             "press_per_px": 1.35, "press_overhead_ms": 20},
            {"serial": "NEW", "resolution": "1280x2772", "note": "新机",
             "press_per_px": 1.11, "press_overhead_ms": 34},
            {"resolution": "1280x2772", "press_per_px": 1.2, "press_overhead_ms": 30},
        ])

    def test_exact_match(self) -> None:
        label = self.cfg.apply_device_profile("NEW", 1280, 2772)
        self.assertEqual(label, "新机")
        self.assertAlmostEqual(self.cfg.press_per_px, 1.11)
        self.assertEqual(self.cfg.press_overhead_ms, 34)
        # 换回旧设备要能切回去
        self.cfg.apply_device_profile("OLD", 1080, 2408)
        self.assertAlmostEqual(self.cfg.press_per_px, 1.35)
        self.assertEqual(self.cfg.press_overhead_ms, 20)

    def test_serial_only_match(self) -> None:
        self.cfg.press_profiles = [{"serial": "NEW", "press_per_px": 1.05}]
        self.assertIsNotNone(self.cfg.apply_device_profile("NEW", 720, 1280))
        self.assertAlmostEqual(self.cfg.press_per_px, 1.05)

    def test_resolution_fallback_for_unknown_device(self) -> None:
        label = self.cfg.apply_device_profile("UNKNOWN", 1280, 2772)
        self.assertEqual(label, "1280x2772")
        self.assertAlmostEqual(self.cfg.press_per_px, 1.2)
        self.assertEqual(self.cfg.press_overhead_ms, 30)

    def test_no_match_keeps_defaults(self) -> None:
        self.assertIsNone(self.cfg.apply_device_profile("UNKNOWN", 800, 600))
        self.assertAlmostEqual(self.cfg.press_per_px, 1.35)
        self.assertEqual(self.cfg.press_overhead_ms, 20)

    def test_save_profile_roundtrip(self) -> None:
        self.cfg.save_press_profile("NEW2", 1280, 2772, 1.08, 40)
        self.assertAlmostEqual(
            self.cfg.match_press_profile("NEW2", 1280, 2772)["press_per_px"], 1.08)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            self.cfg.save(path)
            loaded = Config.load(path)
        loaded.apply_device_profile("NEW2", 1280, 2772)
        self.assertAlmostEqual(loaded.press_per_px, 1.08)
        self.assertEqual(loaded.press_overhead_ms, 40)


if __name__ == "__main__":
    unittest.main()
