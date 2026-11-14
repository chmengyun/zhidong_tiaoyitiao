"""识别模块测试：合成画面 + 真机截图。"""

from __future__ import annotations

import unittest
from pathlib import Path

import cv2

from jumpbot.config import Config
from jumpbot.vision import detect_piece, detect_platforms, detect_scene, find_restart_button

from . import synth

FIXTURES = Path(__file__).parent / "fixtures"


class SyntheticSceneTest(unittest.TestCase):
    """用合成画面验证几何识别的准确性。"""

    def setUp(self) -> None:
        self.cfg = Config()

    def assert_close(self, actual: float, expected: float, tol: float, label: str) -> None:
        self.assertLessEqual(abs(actual - expected), tol,
                             f"{label}: 期望 {expected}, 实际 {actual}")

    def test_target_on_right(self) -> None:
        img = synth.make_scene(piece_base=(336, 1374), current_center=(354, 1355),
                               target_center=(789, 1093))
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assert_close(scene.target.center[0], 789, 3, "target.x")
        self.assert_close(scene.target.center[1], 1093, 3, "target.y")
        self.assert_close(scene.piece.base[0], 336, 3, "piece.x")
        self.assert_close(scene.piece.base[1], 1374, 3, "piece.y")
        expected = ((789 - 336) ** 2 + (1093 - 1374) ** 2) ** 0.5
        self.assert_close(scene.distance, expected, 6, "distance")

    def test_target_on_left(self) -> None:
        img = synth.make_scene(piece_base=(700, 1350), current_center=(700, 1330),
                               target_center=(300, 1050))
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assert_close(scene.target.center[0], 300, 3, "target.x")
        self.assertLess(scene.target.center[0], scene.piece.base[0])

    def test_scaled_resolution(self) -> None:
        img = synth.make_scene(scale=0.667,
                               piece_base=(336, 1374), target_center=(789, 1093))
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assert_close(scene.piece.base[0], 336 * 0.667, 5, "piece.x")
        self.assert_close(scene.target.center[0], 789 * 0.667, 5, "target.x")

    def test_game_over_synthetic(self) -> None:
        img = synth.make_game_over()
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "game_over")
        self.assertIsNotNone(scene.restart_point)
        self.assert_close(scene.restart_point[1], 0.6945 * 2408, 30, "button.y")


class RealScreenshotTest(unittest.TestCase):
    """用真机截图验证，覆盖真实光影、圆角与渐变的干扰。"""

    def setUp(self) -> None:
        self.cfg = Config()

    def test_playing_screen(self) -> None:
        img = cv2.imread(str(FIXTURES / "real_playing.png"))
        self.assertIsNotNone(img, "缺少 fixtures/real_playing.png")
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.piece)
        self.assertIsNotNone(scene.target)

        # 真值由像素级人工核对得到
        self.assertAlmostEqual(scene.piece.base[0], 336.5, delta=12)
        self.assertAlmostEqual(scene.piece.base[1], 1374, delta=12)
        self.assertAlmostEqual(scene.target.center[0], 788.5, delta=12)
        self.assertAlmostEqual(scene.target.center[1], 1093, delta=12)
        self.assertAlmostEqual(scene.distance, 532, delta=25)
        self.assertAlmostEqual(scene.current.center[0], 336, delta=20)

    def test_game_over_screen(self) -> None:
        img = cv2.imread(str(FIXTURES / "real_game_over.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "game_over")
        self.assertAlmostEqual(scene.restart_point[0], 669, delta=25)
        self.assertAlmostEqual(scene.restart_point[1], 1673, delta=25)

    def test_platform_count_and_aspect(self) -> None:
        img = cv2.imread(str(FIXTURES / "real_playing.png"))
        platforms = detect_platforms(img, self.cfg)
        self.assertEqual(len(platforms), 2)
        for p in platforms:
            self.assertGreater(p.area, 50000)
            self.assertGreater(p.aspect, 0.8)
            self.assertLess(p.aspect, 3.0)

    def test_disc_level(self) -> None:
        """关卡换成白色叠层 + 灰色圆盘（两平台在画面上粘连）。"""
        img = cv2.imread(str(FIXTURES / "real_disc_platform.png"))
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertGreater(scene.target.center[0], scene.piece.base[0])
        self.assertAlmostEqual(scene.distance, 258, delta=45)

    def test_multi_platform_level(self) -> None:
        """场景里有多块平台时，应选画面中更靠上（更远）的那块作为目标。

        棋盘上「更靠下」的平台是来时路，不能因为距离更近就当成目标。
        """
        img = cv2.imread(str(FIXTURES / "real_multi_platform.png"))
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNone(scene.current)  # 脚下平台被粘连，未单独识别
        self.assertIsNotNone(scene.target)
        self.assertLess(scene.target.center[1], scene.piece.base[1])  # 目标在前方
        self.assertAlmostEqual(scene.target.center[0], 709.5, delta=30)
        self.assertAlmostEqual(scene.distance, 351, delta=35)

    def test_square_platform_level(self) -> None:
        """近方形（外接框长宽比约 1.0）的黄色方块也要能从粘连区域中拆出来。"""
        img = cv2.imread(str(FIXTURES / "real_square_platform.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 698, delta=30)
        self.assertAlmostEqual(scene.distance, 333, delta=35)

    def test_shop_level(self) -> None:
        """白色「微信支付」商店与黄色方块同亮度粘连，需要按饱和度拆分。"""
        img = cv2.imread(str(FIXTURES / "real_shop_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 407, delta=35)
        self.assertAlmostEqual(scene.distance, 372, delta=40)

    def test_bottle_level(self) -> None:
        """药瓶造型的小平台：面积小、长宽比 0.61，落点取瓶盖中心而不是瓶身。"""
        img = cv2.imread(str(FIXTURES / "real_bottle_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 454, delta=30)
        self.assertAlmostEqual(scene.target.center[1], 1163, delta=35)
        self.assertAlmostEqual(scene.distance, 265, delta=35)

    def test_adjacent_same_color_blocks(self) -> None:
        """两块绿色方块贴在一起：棋子不能污染拆分结果，目标落点要靠近右侧方块。"""
        img = cv2.imread(str(FIXTURES / "real_adjacent_blocks.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 645, delta=30)
        self.assertAlmostEqual(scene.distance, 188, delta=35)

    def test_hue_split_blocks(self) -> None:
        """黄色与橙色方块明暗接近、色相不同，需要按色相拆分才能找到目标。"""
        img = cv2.imread(str(FIXTURES / "real_hue_split_blocks.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 460, delta=30)
        self.assertAlmostEqual(scene.target.center[1], 1214, delta=35)
        self.assertAlmostEqual(scene.distance, 248, delta=35)

    def test_green_table_level(self) -> None:
        """灰绿色圆桌面比背景更暗且饱和度接近，需要按色相差异才能分割出来。"""
        img = cv2.imread(str(FIXTURES / "real_green_table_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 338, delta=30)
        self.assertAlmostEqual(scene.target.center[1], 1085, delta=35)
        self.assertAlmostEqual(scene.distance, 543, delta=45)

    def test_box_and_cube_level(self) -> None:
        """白色方块与黄色纸箱粘连且整体近似对称，仍要按饱和度拆出前方纸箱。"""
        img = cv2.imread(str(FIXTURES / "real_box_and_cube_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 639, delta=30)
        self.assertAlmostEqual(scene.target.center[1], 1160, delta=35)
        self.assertAlmostEqual(scene.distance, 207, delta=35)

    def test_dark_stool_beside_shop(self) -> None:
        """商店屋顶与两侧圆凳粘成一块时，仍要认出左前方深灰圆凳；
        棋子头顶的高光不能被当成前方平台（否则会朝自己头上短跳而摔下）。"""
        img = cv2.imread(str(FIXTURES / "real_stool_beside_shop.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 538, delta=20)
        self.assertAlmostEqual(scene.target.center[1], 1327, delta=20)
        self.assertAlmostEqual(scene.distance, 335, delta=25)
        px, py, pw, ph = scene.piece.bbox
        for plat in scene.platforms:
            x, y, w, h = plat.bbox
            self.assertFalse(x >= px and y >= py and x + w <= px + pw
                             and y + h <= py + ph,
                             f"棋子内部区域被当成平台: {plat}")

    def test_white_cylinder_level(self) -> None:
        """白色圆柱顶面与绿色方块的白色条纹粘连，需要按高亮低饱和区域单独提取。"""
        img = cv2.imread(str(FIXTURES / "real_white_cylinder_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 640, delta=30)
        self.assertAlmostEqual(scene.distance, 200, delta=35)

    def test_seam_blocks_level(self) -> None:
        """两块同色灰色叠层方块贴合成一条竖直缝隙，需要按缝隙竖切出前方方块。"""
        img = cv2.imread(str(FIXTURES / "real_seam_blocks_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 654, delta=30)
        self.assertAlmostEqual(scene.target.center[1], 1169, delta=35)
        self.assertAlmostEqual(scene.distance, 220, delta=35)

    def test_orange_cube_level(self) -> None:
        """灰色叠层方块右侧紧贴橙色立方体：立方体整体长宽比只有 0.84，仍要认出来。"""
        img = cv2.imread(str(FIXTURES / "real_orange_cube_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 649, delta=30)
        self.assertAlmostEqual(scene.target.center[1], 1179, delta=35)
        self.assertAlmostEqual(scene.distance, 184, delta=35)

    def test_dark_stool_level(self) -> None:
        """深灰圆凳平台与背景同色相、只是更暗，不能被当成阴影丢掉。"""
        img = cv2.imread(str(FIXTURES / "real_dark_stool_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 459, delta=30)
        self.assertAlmostEqual(scene.target.center[1], 1158, delta=35)
        self.assertAlmostEqual(scene.distance, 242, delta=35)

    def test_gray_roll_level(self) -> None:
        """灰色卷纸筒细高（长宽比 0.53），要靠「细高 + 轮廓干净」这条规则认出来。"""
        img = cv2.imread(str(FIXTURES / "real_gray_roll_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 453, delta=30)
        self.assertAlmostEqual(scene.target.center[1], 1156, delta=35)
        self.assertAlmostEqual(scene.distance, 243, delta=35)

    def test_small_stool_level(self) -> None:
        """紧贴橙色方块的迷你圆凳面积不到常规平台一半，形状干净时要能认出来。"""
        img = cv2.imread(str(FIXTURES / "real_small_stool_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 490, delta=30)
        self.assertAlmostEqual(scene.target.center[1], 1177, delta=35)
        self.assertAlmostEqual(scene.distance, 159, delta=35)

    def test_green_stool_level(self) -> None:
        """绿顶白腿圆凳整体长宽比只有 0.47，属于迷你平台（面积不足一半）。"""
        img = cv2.imread(str(FIXTURES / "real_green_stool_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 728, delta=30)
        self.assertAlmostEqual(scene.target.center[1], 1122, delta=35)

    def test_shadowed_cap_level(self) -> None:
        """圆凳顶盖右半被阴影压暗、整体与绿方块粘连，要靠亮部椭圆顶盖定位落点。"""
        img = cv2.imread(str(FIXTURES / "real_shadowed_cap_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 498, delta=30)
        self.assertAlmostEqual(scene.target.center[1], 1180, delta=35)
        self.assertAlmostEqual(scene.distance, 167, delta=35)

    def test_green_cap_table_level(self) -> None:
        """木桌上方的绿顶圆凳顶盖比背景更暗且带色彩，仍要按椭圆顶盖定位。"""
        img = cv2.imread(str(FIXTURES / "real_green_cap_table_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 472, delta=30)
        self.assertAlmostEqual(scene.target.center[1], 1167, delta=35)
        self.assertAlmostEqual(scene.distance, 222, delta=35)

    def test_white_cube_top_face_ahead(self) -> None:
        """白色方块顶面比常规平台小一半，且与脚下深灰方块粘连成一块。

        整块连通域顶点偏心（多块粘连），白色顶面必须单独拆出来当目标，
        否则会把整块当成「当前平台」而判定前方没有目标、停机。
        """
        img = cv2.imread(str(FIXTURES / "real_white_cube_ahead.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 483, delta=25)
        self.assertAlmostEqual(scene.target.center[1], 1184, delta=25)
        self.assertAlmostEqual(scene.distance, 140, delta=25)

    def test_score_is_not_platform(self) -> None:
        """顶部的分数「47B」比背景暗得多、又宽又矮，容易被当成深色顶盖。

        目标必须是紧邻的白色圆柱顶盖（短距离），不能跳到屏幕顶部的分数上。
        """
        img = cv2.imread(str(FIXTURES / "real_score_not_platform.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 481, delta=25)
        self.assertAlmostEqual(scene.target.center[1], 1169, delta=25)
        self.assertAlmostEqual(scene.distance, 184, delta=30)
        for p in scene.platforms:
            self.assertGreater(p.center[1], 1000, "分数区域不应产生平台")

    def test_dark_purple_cube_not_piece(self) -> None:
        """深紫色方块的暗面比棋身更高、且与棋子同色系，不能把方块当成棋子。

        误选时脚底会落到方块下沿，起跳距离被算短一半，直接跳空。
        """
        img = cv2.imread(str(FIXTURES / "real_purple_cube_level.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertAlmostEqual(scene.piece.base[0], 477, delta=20)
        self.assertAlmostEqual(scene.piece.base[1], 1542, delta=20)
        self.assertIsNotNone(scene.target)
        self.assertAlmostEqual(scene.target.center[0], 855, delta=25)
        self.assertAlmostEqual(scene.distance, 449, delta=30)

    def test_piece_on_purple_cube(self) -> None:
        """棋子站在深紫色方块上时，方块侧面比棋身更亮但饱和度更高，会与棋子连成
        一块；亮度上限要能把方块排除，否则脚底落到方块下沿、距离被算长。"""
        img = cv2.imread(str(FIXTURES / "real_piece_on_purple_cube.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "playing")
        self.assertAlmostEqual(scene.piece.base[0], 295, delta=20)
        self.assertAlmostEqual(scene.piece.base[1], 1647, delta=25)
        self.assertIsNotNone(scene.target)
        self.assertLess(scene.target.center[1], scene.piece.base[1])

    def test_piece_not_found_on_game_over(self) -> None:
        img = cv2.imread(str(FIXTURES / "real_game_over.png"))
        piece = detect_piece(img, self.cfg)
        self.assertIsNone(piece)
        self.assertIsNotNone(find_restart_button(img, self.cfg))

    def test_leaderboard_back_button(self) -> None:
        img = cv2.imread(str(FIXTURES / "real_leaderboard.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "game_over")
        self.assertEqual(scene.note, "back_button")
        # 左下角圆形返回按钮
        self.assertAlmostEqual(scene.restart_point[0], 140, delta=20)
        self.assertAlmostEqual(scene.restart_point[1], 2002, delta=20)

    def test_wechat_dialog_cancel(self) -> None:
        img = cv2.imread(str(FIXTURES / "real_dialog.png"))
        self.assertIsNotNone(img)
        scene = detect_scene(img, self.cfg)
        self.assertEqual(scene.state, "game_over")
        self.assertEqual(scene.note, "dialog")
        # 「取消」按钮位于弹窗左下
        self.assertAlmostEqual(scene.restart_point[0], 396, delta=30)
        self.assertAlmostEqual(scene.restart_point[1], 1328, delta=30)


if __name__ == "__main__":
    unittest.main()
