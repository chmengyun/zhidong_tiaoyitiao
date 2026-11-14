"""跳一跳自动化脚本命令行入口。

用法示例：
    python main.py analyze              # 只识别当前画面，不操作（含保存标注图）
    python main.py run                  # 自动跳跃
    python main.py run --dry-run        # 只打印计划，不按压
    python main.py calibrate            # 交互式校准 press_per_px 系数
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

from jumpbot.config import Config
from jumpbot.device import AdbDevice, DeviceError
from jumpbot.planner import plan_jump, suggest_press_per_px
from jumpbot.runner import AutoJumper
from jumpbot.vision import annotate, detect_scene

ROOT = Path(__file__).resolve().parent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="微信「跳一跳」自动化脚本（ADB + OpenCV）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--config", type=str, default=None, help="配置文件路径(JSON)")
    parser.add_argument("--serial", type=str, default=None, help="指定 adb 设备序列号")
    parser.add_argument("--k", type=float, default=None,
                        help="覆盖换算系数 press_per_px（ms/px）")
    parser.add_argument("--press-method", choices=["swipe", "motionevent"],
                        default=None, help="按压方式，默认 swipe")
    parser.add_argument("--no-save", action="store_true", help="不保存调试截图")

    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="自动跳跃")
    p_run.add_argument("--jumps", type=int, default=None, help="最多跳跃次数")
    p_run.add_argument("--dry-run", action="store_true", help="只识别与计算，不按压")

    p_analyze = sub.add_parser("analyze", help="识别当前画面并保存标注图")
    p_analyze.add_argument("--out", type=str, default="analyze.png", help="标注图输出路径")

    p_cal = sub.add_parser("calibrate", help="交互式校准换算系数")

    sub.add_parser("show-config", help="打印当前生效配置")

    return parser


def load_config(args) -> Config:
    # 未显式指定时，自动使用脚本目录下的 config.json
    path = args.config
    if path is None and (ROOT / "config.json").exists():
        path = str(ROOT / "config.json")
    cfg = Config.load(path)
    if args.k is not None:
        cfg.press_per_px = args.k
    if args.press_method:
        cfg.press_method = args.press_method
    if args.no_save:
        cfg.save_debug_frames = False
    if args.serial:
        cfg.serial = args.serial
    cfg.validate()
    return cfg


def make_device(cfg: Config, args=None) -> AdbDevice:
    device = AdbDevice(adb_path=cfg.adb_path, serial=cfg.serial)
    info = device.connect_check()
    print(f"设备已连接: {info.serial}  分辨率 {info.width}x{info.height}")
    # 连接后按「识别码 + 分辨率」自动套用该设备的按压档案
    label = cfg.apply_device_profile(info.serial, info.width, info.height)
    if args is not None and getattr(args, "k", None) is not None:
        cfg.press_per_px = args.k
        label = "命令行 --k 覆盖"
    if label:
        print(f"按压参数: {cfg.press_per_px} ms/px + {cfg.press_overhead_ms}ms"
              f"（{label}）")
    return device


# ----------------------------------------------------------------------
def cmd_analyze(args, cfg: Config) -> int:
    device = make_device(cfg, args)
    img = device.screenshot()
    scene = detect_scene(img, cfg)
    print(scene)
    if scene.distance is not None:
        plan = plan_jump(scene.distance, cfg)
        print(f"建议按压: {plan.press_ms}ms (k={cfg.press_per_px}, "
              f"原始值 {plan.raw_press_ms:.0f}ms)")
    out = Path(args.out)
    cv2.imwrite(str(out), annotate(img, scene))
    print(f"标注图已保存: {out.resolve()}")
    return 0 if scene.state == "playing" else 1


def cmd_run(args, cfg: Config) -> int:
    device = make_device(cfg, args)
    jumper = AutoJumper(
        device, cfg,
        debug_dir=ROOT / cfg.debug_dir if cfg.save_debug_frames else None,
        csv_path=ROOT / cfg.log_csv,
    )
    scene = jumper.grab()
    if scene.state == "game_over":
        print("当前处于结算界面，先重开一局…")
        if not jumper.recover():
            print("自动重开失败，请在手机上点开「跳一跳」并进入游戏再运行")
            return 1
    stats = jumper.run(max_jumps=args.jumps, dry_run=args.dry_run)
    print("-" * 52)
    print(f"跳跃 {stats.jumps} 次 | 成功 {stats.success} | 失败 {stats.failed} "
          f"| 成功率 {stats.success_rate:.0%} | 重开 {stats.restarts} 次")
    return 0


def cmd_calibrate(args, cfg: Config) -> int:
    """交互式校准：手动给一个按压时长，根据是否落地反推系数。"""
    device = make_device(cfg, args)
    info = device.connect_check()
    jumper = AutoJumper(device, cfg, debug_dir=None, csv_path=None)

    print("校准说明：脚本会给出距离与建议时长，你可以手动输入本次按压时长；")
    print("游戏结束即代表未命中，可重新输入；落地成功则输出新的 press_per_px。")
    print("直接回车表示采用建议值；输入 q 退出。\n")

    while True:
        scene = jumper.grab()
        if scene.state == "game_over":
            if not jumper.recover():
                print("自动重开失败，请手动回到游戏")
                return 1
            scene = jumper.grab()
        if scene.state != "playing":
            print("画面无法识别，请确认游戏处于可跳跃状态")
            return 1

        distance = scene.distance
        assert distance is not None
        suggest = plan_jump(distance, cfg).press_ms
        raw = input(f"距离 {distance:.0f}px，建议按压 {suggest}ms，"
                    f"输入本次按压时长(ms) > ").strip()
        if raw.lower() == "q":
            return 0
        press = int(raw) if raw else suggest

        device.press(int(scene.piece.base[0]), int(scene.piece.base[1]), press,
                     method=cfg.press_method)
        time.sleep(max(1.0, cfg.settle_ms / 1000.0))
        after = jumper.grab()

        if after.state == "playing":
            k = suggest_press_per_px(distance, press, cfg.press_overhead_ms)
            print(f"\n✅ 落地成功！本次 press_per_px = {k:.4f}\n"
                  f"   建议写入配置：\"press_per_px\": {k:.4f}\n")
            answer = input("是否把该系数写入 config.json？(y/N) > ").strip().lower()
            if answer == "y":
                cfg.press_per_px = round(k, 4)
                # 同时更新当前设备（识别码 + 分辨率）对应的按压档案
                label = cfg.save_press_profile(info.serial, info.width, info.height,
                                               cfg.press_per_px, cfg.press_overhead_ms)
                cfg.save(ROOT / "config.json")
                print(f"已写入 config.json（设备档案：{label}）")
            return 0

        print("❌ 未命中（游戏结束），等待重开后再试…\n")
        time.sleep(0.8)


def cmd_show_config(args, cfg: Config) -> int:
    import json
    print(json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = load_config(args)
    handlers = {
        "analyze": cmd_analyze,
        "run": cmd_run,
        "calibrate": cmd_calibrate,
        "show-config": cmd_show_config,
    }
    try:
        return handlers[args.command](args, cfg)
    except DeviceError as exc:
        print(f"设备错误: {exc}")
        return 2
    except KeyboardInterrupt:
        print("\n已中断")
        return 130


if __name__ == "__main__":
    sys.exit(main())
