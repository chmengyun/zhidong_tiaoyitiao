# 跳一跳助手（Python + OpenCV）

用 ADB 截屏 + OpenCV 识别微信小游戏「跳一跳」的棋子与目标平台，按像素距离计算按压时长并自动跳跃。

识别失败时会暂停并请求人工处理，**不会猜测坐标盲按**。

> Android 悬浮球版只提供正式安装包（见 GitHub Releases），**不包含 App 源码**。

## 功能

- 棋子、当前平台、目标平台识别，支持白色/深色/迷你/粘连平台
- 按压时长自动换算，支持按设备档案自动套用参数
- 自动处理结算界面与重开
- 识别失败自动暂停，人工处理后确认恢复
- 每跳记录 `logs/jumps.csv`，可导出调试截图
- 交互式校准 `calibrate`，根据落点偏差反推按压系数

## 目录结构

```
main.py                 # 命令行入口
jumpbot/
  config.py             # 参数与设备档案
  device.py             # ADB 截屏与手势
  vision.py             # 画面识别
  planner.py            # 距离 -> 按压时长
  runner.py             # 自动循环与人工接管
tests/                  # 单元测试
docs/使用文档-Python版.md  # 完整使用与调参手册
config.example.json     # 通用参数模板（不含真实设备信息）
requirements.txt
```

## 环境要求

- Python 3.9+
- 电脑已安装 `adb`，并已加入 PATH
- 手机开启「USB 调试」，首次连接时在手机上允许调试授权
- 依赖：`opencv-python`、`numpy`

```bash
pip install -r requirements.txt
adb devices
```

## 配置

复制通用模板生成本地配置：

```bash
# Windows
copy config.example.json config.json

# macOS / Linux
cp config.example.json config.json
```

`config.json` 已被 `.gitignore` 排除，**不会上传**。它用于保存：

- 你的设备序列号 `serial`
- 本机校准后的 `press_profiles` 按压档案

`config.example.json` 只包含通用参数：`serial` 为空、`press_profiles` 为空，
`press_per_px = 1.2`、`press_overhead_ms = 20` 只是起步值，需要在你的手机上校准。

多设备或需要固定设备时，在 `config.json` 中填写：

```json
"serial": "你的设备序列号"
```

或运行时指定：

```bash
python main.py run --serial 你的设备序列号
```

## 快速开始

先只识别、不操作，确认标注正确：

```bash
python main.py analyze
```

自动跳跃：

```bash
python main.py run
python main.py run --jumps 50
python main.py run --dry-run
```

交互式校准：

```bash
python main.py calibrate
```

查看当前生效参数：

```bash
python main.py show-config
```

完整的参数说明、调参案例和故障排查见
[docs/使用文档-Python版.md](docs/使用文档-Python版.md)。

## 日志与调试

| 路径 | 内容 |
|---|---|
| `logs/jumps.csv` | 每跳距离、按压时长、结果、落点偏差 |
| `debug_frames/` | 按压前后截图与标注图 |
| `analyze.png` | `analyze` 命令输出的标注图 |

这些运行产物同样被 `.gitignore` 排除。

## Android 悬浮球版

- 正式安装包：请到本仓库的 **Releases** 页面下载
- 不提供 App 源码
- App 与 Python 版使用同一套识别参数和按压公式

## 隐私与安全

- 设备序列号、按压档案只保存在本地 `config.json`，不会提交到仓库
- 调试截图、运行日志、录屏内容不会上传
- 自动跳跃只在识别成功时执行；识别失败会暂停，人工确认后才恢复
- 请遵守游戏与平台的使用规则，仅用于学习与研究
