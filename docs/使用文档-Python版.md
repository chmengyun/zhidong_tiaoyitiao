# 跳一跳助手 · Python 版 使用与参数手册

适用项目：本仓库
形态：电脑通过 ADB 控制手机（`adb exec-out screencap` 截图 + `adb shell input swipe` 按压）
Android 悬浮球版：仅提供正式安装包（见 GitHub Releases），不包含 App 源码。

---

## 1. 原理与组成

```
手机画面 --adb screencap--> OpenCV 识别（棋子/当前平台/目标平台）
      --> 计算像素距离 --> 按压时长 = 距离 × press_per_px + press_overhead_ms
      --> adb shell input swipe --> 下一帧校验落点/结算/识别失败
```

| 文件 | 作用 |
|---|---|
| `main.py` | 命令行入口：analyze / run / calibrate / show-config |
| `jumpbot/config.py` | 参数定义、设备档案匹配、JSON 读写 |
| `jumpbot/device.py` | ADB 封装：截屏、swipe、tap、keyevent |
| `jumpbot/vision.py` | 画面识别：棋子、平台、结算界面、标注图 |
| `jumpbot/planner.py` | 距离 → 按压时长；校准反推系数 |
| `jumpbot/runner.py` | 自动循环、重开、人工接管、CSV 记录 |
| `config.json` | 当前生效参数 |
| `logs/jumps.csv` | 每跳记录 |
| `debug_frames/` | 调试截图（开关控制） |

---

## 2. 环境准备

### 2.1 依赖

- Python 3.9+（开发与测试环境为 3.10/3.11）
- `adb` 已加入 PATH，且 `adb devices` 能看到手机
- 手机开启「USB 调试」，首次连接时在手机上允许调试授权

```bash
cd path/to/zhidong_tiaoyitiao
pip install -r requirements.txt
adb devices
```

`requirements.txt` 只有两项：`opencv-python>=4.8`、`numpy>=1.24`。

### 2.2 画面要求

- 打开微信小游戏「跳一跳」，停在**可跳跃**画面；
- 手机亮度、分辨率保持稳定；分屏/小窗/悬浮球遮挡都会影响识别；
- 系统「自动旋转」和刘海遮挡区域的额外 UI 会改变布局、影响识别。

### 2.3 多设备

连接多台手机时必须用 `--serial` 指定，否则脚本会直接报错，避免操作错设备：

```bash
python main.py run --serial <设备序列号>
```

---

## 3. 快速开始

### 3.1 只识别，不操作

```bash
python main.py analyze
```

输出示例：

```
设备已连接: <设备序列号>  分辨率 1080x2408
按压参数: 1.20 ms/px + 20ms（通用起步值，需校准）
Scene(state=playing, distance=512.3, piece=..., current=..., target=...)
建议按压: 635ms (k=1.20, 原始值 634.8ms)
标注图已保存: ...\analyze.png
```

`analyze.png` 会用不同颜色标出棋子、当前平台、目标平台，确认标注正确后再运行自动跳跃。

### 3.2 自动跳跃

```bash
python main.py run
python main.py run --jumps 50        # 只跳 50 次
python main.py run --dry-run         # 只识别和算时长，不按压
```

结束时打印：

```
跳跃 50 次 | 成功 47 | 失败 3 | 成功率 94% | 重开 2 次
```

### 3.3 交互式校准

```bash
python main.py calibrate
```

流程：脚本给出距离与建议按压时长 → 输入本次实际按住的毫秒数（直接回车用建议值）
→ 落地成功则反推新的 `press_per_px`，并询问是否写回 `config.json` 和当前设备档案。

### 3.4 查看当前生效参数

```bash
python main.py show-config
```

### 3.5 命令行参数

| 参数 | 作用 |
|---|---|
| `--config PATH` | 指定配置文件（默认项目根目录 `config.json`） |
| `--serial SERIAL` | 指定 adb 设备序列号（多设备必填） |
| `--k FLOAT` | 临时覆盖 `press_per_px`，不写文件 |
| `--press-method {swipe,motionevent}` | 按压方式，默认 `swipe` |
| `--no-save` | 本次不保存调试截图 |
| `run --jumps N` | 本次最多跳 N 次 |
| `run --dry-run` | 只识别与计算，不按压 |
| `analyze --out PATH` | 标注图输出路径（默认 `analyze.png`） |

---

## 4. 运行行为与安全策略

每轮循环：等待 `settle_ms` → 连续截屏直到画面静止 → 识别 → 判断状态。

| 识别状态 | 处理 |
|---|---|
| `playing` | 确认上一跳成功，记录 `residual_px`，然后计算并执行下一跳 |
| `game_over` | 上一跳记为 failed；尝试自动重开（点按钮 / 返回键 / 兜底坐标） |
| `unknown` | 连续 `unknown_retries` 次后暂停并请求人工，**绝不猜测坐标按压** |

**人工接管**：暂停时终端打印原因与截图路径，输入：

- 回车或 `r`：重新识别，连续 `resume_confirm_frames` 帧确认在游戏中才恢复；
- `q`：正常退出。

恢复确认期间只截屏、不操作设备；无法确认时保持暂停。

`recover()` 自动重开时最多尝试 5 次，且「返回键」最多只按一次，避免退出小程序。

---

## 5. 参数文件

- 默认读取项目根目录 `config.json`；
- 未写的字段沿用 `jumpbot/config.py` 的默认值；
- **出现未知字段会直接报错**（防止拼写错误静默失效）；
- 修改后重新运行命令即可生效（无热加载）。

```bash
python main.py show-config          # 打印完整生效配置
```

> `screenshot_delay_ms`、`restart_retries` 当前版本只保留字段，运行逻辑尚未使用
> （截图靠稳定帧检测，重开固定最多 5 次）。保留是为了与 App 版配置字段兼容。

### 5.1 仅 Python 版使用的字段

| 字段 | 默认 | 说明 |
|---|---|---|
| `adb_path` | `adb` | adb 可执行文件路径 |
| `serial` | `null` | 目标设备序列号；命令行 `--serial` 优先 |
| `press_method` | `swipe` | `swipe`：单条 `input swipe`；`motionevent`：DOWN→sleep→UP |
| `debug_dir` | `debug_frames` | 调试截图目录（相对项目根） |
| `log_csv` | `logs/jumps.csv` | 跳跃记录文件 |
| `press_profiles` | `[]` | 按「序列号 + 分辨率」匹配，见 6.2 |

---

## 6. 核心公式与设备档案

### 6.1 按压时长

```
raw   = distance * press_per_px + press_overhead_ms
press = round(raw)
press = clamp(press, min_press_ms, max_press_ms)   # 被截断时日志标记 (已截断)
```

- `distance`：棋子脚底到目标平台顶面中心的像素距离；
- `press_per_px`：斜率，决定「远跳」够不够；
- `press_overhead_ms`：截距，决定「近跳」准不准。

### 6.2 按压档案自动匹配

连接设备后按「adb serial + 分辨率」打分：

- 序列号和分辨率都匹配 = 3 分；
- 只匹配序列号 = 2 分；
- 只匹配分辨率 = 1 分；
- 字段留空视为通配；取分数最高的档案，用其覆盖全局 `press_per_px` / `press_overhead_ms`。

`config.example.json` 的 `press_profiles` 为空。
在本地 `config.json` 中按下面格式添加设备档案：

```json
"press_profiles": [
  {
    "serial": "<设备序列号>",
    "resolution": "1080x2408",
    "note": "设备备注",
    "press_per_px": 1.20,
    "press_overhead_ms": 20
  }
]
```

`config.json` 已被 `.gitignore` 排除。
`calibrate` 成功后会通过 `save_press_profile()` 把结果写回当前设备档案（没有则新增）。

### 6.3 落点偏差 residual_px

```
residual = (本次棋子落点 - 当前平台中心) · 跳跃方向单位向量
```

- 正数 = 跳过头（偏远）；负数 = 没跳到（偏近）；空 = 该跳结果尚未确认；
- 必须用同一帧内的棋子脚底与平台中心计算（跳跃后镜头会移动）；
- 绝对值持续 > 30px 才值得调参，±10px 属于正常抖动。

---

## 7. 参数详解

### 7.1 运行节奏

| 字段 | 默认 | 作用 | 调大 | 调小 | 配错症状 |
|---|---|---|---|---|---|
| `settle_ms` | 900 | 每次按压后等待画面稳定的时间（ms） | 更稳、更慢 | 更快，但会拍到落点动画 | 太小：连续无法识别；太大：跳得慢 |
| `stability_diff` | 1.5 | 缩放到 108×240 后，相邻两帧平均像素差小于该值即认为静止 | 更容易判定为静止（可能截到动画） | 更严格 | 太小：一直等不到稳定帧；太大：截到动画 |
| `stability_timeout` | 3.0 | 等稳定帧的最长秒数，超时就用当前帧 | 更耐心 | 更容易超时 | 画面有粒子/云飘动时容易超时 |
| `max_jumps` | 600（config.json） | 单次运行最大跳数；`run --jumps` 优先 | 跑更久 | 更早停 | — |

### 7.2 识别区域与背景模型

| 字段 | 默认 | 作用 | 配错症状 |
|---|---|---|---|
| `region_top_ratio` | 0.08 | 参与识别的区域上边界（占屏高比例） | 太小：顶部 UI 被当平台；太大：高处平台漏检 |
| `region_bottom_ratio` | 0.95 | 识别区域下边界 | 太大：底部按钮/文字干扰 |
| `bg_sample_top` | 0.10 | 背景取样起始行 | 取样混入平台会导致背景色估错 |
| `bg_sample_bottom` | 0.32 | 背景取样结束行 | 同上 |

背景模型：对取样行内每行的 S/V 中位数做一阶线性拟合，逐行估计「纯背景颜色」。

### 7.3 平台前景分割

满足任一即判为前景：

```
|sat - sat_bg| > platform_sat_delta × scale
或 (val - val_bg) > platform_val_delta × scale
或 (hue_diff > platform_hue_delta × scale 且 sat > platform_hue_sat_min)
```

前景占比 ≥ 35% 时 `scale` 提高到 2、3 重试，防止阈值过松导致整屏都是前景。

| 字段 | 默认 | 调大 | 调小 |
|---|---|---|---|
| `platform_sat_delta` | 8 | 更保守（漏检） | 更敏感（误检） |
| `platform_val_delta` | 15 | 更保守 | 更敏感 |
| `platform_hue_delta` | 12 | 更保守 | 更敏感 |
| `platform_hue_sat_min` | 40 | 只有高饱和色块算平台 | 低饱和也算（易误检） |

### 7.4 平台几何校验

| 字段 | 默认 | 作用 |
|---|---|---|
| `platform_min_area_ratio` | 0.006 | 平台最小面积（占屏比例），滤掉小噪点 |
| `platform_max_area_ratio` | 0.06 | 超过则认为是多块平台粘连 |
| `platform_aspect_min` / `platform_aspect_max` | 0.85 / 2.80 | 宽高比范围 |
| `platform_aspect_relax_min` | 0.48 | 细高圆柱类平台的放宽下限 |
| `platform_fill_min` | 0.30 | 面积 / 外接框（实心度）下限 |
| `platform_disc_fill_min` | 0.55 | 圆盘类平台实心度下限 |
| `platform_top_symmetry_max` | 0.12 | 顶面顶点居中度上限 |
| `platform_small_area_ratio` | 0.0028 | 迷你平台最小面积 |
| `platform_small_symmetry_max` | 0.03 | 迷你平台居中度上限（更严） |
| `platform_small_aspect_min` | 0.42 | 迷你平台宽高比下限 |

### 7.5 粘连平台拆分（8 级拆分链）

两个平台贴在一起会被当成一个超宽块，导致「只跳到两块中间」。依次尝试：
亮度分峰 → 腐蚀种子 + 最近种子分配 → 饱和度拆分 → 色相拆分 → 白色顶面 →
缝隙竖切 → 暗色平台 → 亮色顶盖。只在前方没有可用平台时才启用。

| 字段 | 默认 | 作用 |
|---|---|---|
| `split_max_area_ratio` | 0.35 | 超过屏面积 35% 直接跳过拆分 |
| `split_min_area_ratio` | 0.010 | 拆分出的子块最小面积 |
| `split_peak_ratio` | 0.08 | 亮度直方图峰值门槛 |
| `split_tolerance` | 16 | 亮度峰值合并容差 |
| `split_aspect_min` / `split_aspect_max` | 0.85 / 2.60 | 子块宽高比范围 |
| `split_fill_min` / `split_fill_max` | 0.38 / 0.92 | 子块实心度范围 |
| `split_top_width_max` | 0.60 | 顶部宽度 / 底宽上限 |
| `split_seed_area_ratio` | 0.004 | 腐蚀种子最小面积 |

### 7.6 兜底检测

| 字段 | 默认 | 作用 |
|---|---|---|
| `white_top_sat_max` | 40 | 白色顶面饱和度上限 |
| `white_top_val_min` | 190 | 白色顶面亮度下限 |
| `white_top_min_area_ratio` | 0.0004 | 白色顶面最小面积 |
| `seam_symmetry_max` | 0.06 | 缝隙竖切左右对称度要求 |
| `dark_platform_val_delta` | 60 | 暗色平台与背景亮度差阈值 |
| `dark_platform_sat_max` | 45 | 暗色平台饱和度上限 |
| `dark_platform_val_min` | 90 | 暗色平台绝对亮度下限 |
| `dark_platform_min_area_ratio` | 0.0025 | 暗色平台最小面积 |
| `cap_val_delta` | 12 | 亮色顶盖与背景亮度差 |
| `cap_min_area_ratio` | 0.0004 | 亮色顶盖最小面积 |

### 7.7 当前平台 / 目标平台选择

| 字段 | 默认 | 作用 |
|---|---|---|
| `current_max_distance_ratio` | 0.22 | 当前平台中心与棋子脚底的最大距离（占屏宽比例） |
| `current_max_vertical_px` | 110 | 当前平台包住脚底的垂直容差 |
| `current_max_horizontal_px` | 100 | 水平容差 |
| `target_min_ahead_px` | 30 | 目标平台必须比棋子更靠上的最小像素差 |

### 7.8 棋子识别

棋子是蓝色小人（HSV 色相 100~150）。

| 字段 | 默认 | 作用 |
|---|---|---|
| `piece_hue_min` / `piece_hue_max` | 100 / 150 | 色相范围（OpenCV H，0~179） |
| `piece_sat_min` | 70 | 饱和度下限 |
| `piece_val_max` | 155 | 亮度上限（棋子偏暗） |
| `piece_min_area_ratio` | 0.0003 | 棋子最小面积 |

### 7.9 结算界面 / 重开

| 字段 | 默认 | 作用 |
|---|---|---|
| `button_val_min` | 240 | 「重新开始」白色按钮亮度下限 |
| `button_sat_max` | 12 | 按钮饱和度上限 |
| `button_aspect_min` | 2.5 | 按钮宽高比下限（长条） |
| `button_min_area_ratio` / `button_max_area_ratio` | 0.015 / 0.05 | 按钮面积范围 |
| `button_y_min` / `button_y_max` | 0.55 / 0.82 | 按钮纵向范围（屏高比例） |
| `back_button_y_min` | 0.78 | 返回按钮纵向位置下限 |
| `back_button_x_max` | 0.40 | 返回按钮横向位置上限 |
| `back_button_area_min_ratio` / `max` | 0.002 / 0.04 | 返回按钮面积范围 |
| `restart_points` | `[[0.64,0.71],[0.5,0.71]]` | 认不到按钮时的兜底点击坐标，轮换尝试 |

### 7.10 求助与恢复

| 字段 | 默认 | 作用 |
|---|---|---|
| `unknown_retries` | 5 | 连续识别失败多少次后请求人工 |
| `resume_confirm_frames` | 2 | 恢复前需连续确认的帧数 |
| `resume_confirm_tolerance_px` | 12 | 恢复确认时相邻帧棋子/目标最大位移 |

### 7.11 输出与调试

| 字段 | 默认 | 作用 |
|---|---|---|
| `save_debug_frames` | true | 是否保存每跳截图与标注图；`--no-save` 可临时关闭 |
| `debug_dir` | `debug_frames` | 调试截图目录 |
| `log_csv` | `logs/jumps.csv` | 跳跃记录 CSV |

---

## 8. 调参案例

调参依据：`logs/jumps.csv` 的 `residual_px` 列。

### 8.1 落点整体偏近 / 偏远

- 症状：residual 均值持续为负（偏近）或为正（偏远），各距离段都一样偏。
- 处理：优先调 `press_overhead_ms`，每 10ms 约等于 7~8px（按 1.2~1.4 的系数反推）。
  - 偏近：`press_overhead_ms += 10~20`；
  - 偏远：`press_overhead_ms -= 10~20`。

### 8.2 近跳准、远跳偏（改斜率）

- 症状：300px 以内 residual 接近 0，600px 以上明显偏远或偏近。
- 处理：调 `press_per_px`，每次 ±0.02~0.03；远跳偏远 → 减小，远跳偏近 → 增大。

### 8.3 近跳偏远、远跳准（改截距）

- 症状：短距离总是跳过头，长距离正常。
- 处理：减小 `press_overhead_ms`，每次 5~10ms。

### 8.4 有平台识别不到

依次尝试：

1. `platform_sat_delta` / `platform_val_delta` 各降 2~4；
2. `platform_min_area_ratio` 从 0.006 降到 0.004；
3. 白色平台：`white_top_val_min` 降到 175；
4. 深色平台：`dark_platform_val_delta` 降到 45。

### 8.5 背景被当成平台（原地跳 / 跳到奇怪位置）

1. `platform_fill_min` 从 0.30 提到 0.45；
2. `platform_max_area_ratio` 从 0.06 降到 0.045；
3. `target_min_ahead_px` 从 30 提到 60；
4. 仍误检就把 `platform_val_delta` 提到 20。

### 8.6 两个平台粘在一起，只跳到中间

1. `split_peak_ratio` 从 0.08 降到 0.05；
2. `split_seed_area_ratio` 从 0.004 降到 0.0025；
3. `split_min_area_ratio` 从 0.010 降到 0.007。

### 8.7 一直"无法识别"

- 先确认 `analyze.png` 标注是否正确；
- `stability_timeout` 降到 2.0，`stability_diff` 提到 2.0；
- `settle_ms` 增加到 1100，避免截到落地动画；
- 检查手机是否被系统弹窗、广告、悬浮球遮挡。

### 8.8 更换设备

1. 运行 `python main.py show-config`，看是否已套用对应档案；
2. 没有档案就新增一条（序列号 + 分辨率），初始 `1.20 / 20`；
3. 跑 10 跳，按 8.1~8.3 微调，或直接用 `calibrate` 反推。

---

## 9. 日志与调试

### 9.1 跳跃记录 `logs/jumps.csv`

| 列 | 含义 |
|---|---|
| `time` | 记录时间 |
| `index` | 第几跳 |
| `distance_px` | 识别距离 |
| `press_ms` | 实际按压时长 |
| `clamped` | 是否被 `min/max_press_ms` 截断 |
| `result` | success / failed / interrupted |
| `residual_px` | 落点偏差（正=偏远，负=偏近，空=未确认） |

### 9.2 调试截图 `debug_frames/`

| 文件 | 内容 |
|---|---|
| `NNN_before.png` | 按压前原始帧 |
| `NNN_before_ann.png` | 标注了棋子/当前平台/目标平台的调试图 |
| `NNN_unknown.png` / `_ann.png` | 识别失败时的帧 |
| `NNN_gameover.png` / `_ann.png` | 结算界面帧 |

### 9.3 只识别不操作

```bash
python main.py analyze --out my_frame.png
```

返回码：`0` = 识别到可跳跃画面；`1` = 非 playing（结算/无法识别）。

### 9.4 用同一帧复核

```bash
python -c "import cv2; from jumpbot.config import Config; from jumpbot.vision import detect_scene; print(detect_scene(cv2.imread('my_frame.png'), Config.load('config.json')))"
```

### 9.5 单元测试

```bash
python -m unittest discover -v
```

---

## 10. 常见问题

**Q：找不到 adb？**
A：把 platform-tools 加入 PATH，或在 `config.json` 里把 `adb_path` 写成绝对路径。

**Q：提示"检测到多台设备"？**
A：用 `--serial` 指定目标设备，或在 `config.json` 写 `serial`。

**Q：运行到一半暂停，终端要求输入？**
A：说明连续识别失败或无法重开。处理手机后回车继续，输入 `q` 退出；期间脚本不会按压。

**Q：识别正常但总是跳不准？**
A：先看 residual 方向，按 8.1~8.3 调；换手机先用 8.8 或 `calibrate`。

**Q：能不能改完参数不重启？**
A：每次命令启动时读取配置，改完重新执行命令即可。

**Q：和 App 版有什么区别？**
A：识别算法、参数、按压公式完全一致；Python 版依赖电脑和 ADB，适合调试/标定；
App 版用 MediaProjection + Shizuku/无障碍，手机独立运行，并在设置页内直接改参数。
Python 版支持 `motionevent` 按压方式和 `calibrate` 交互校准，App 版没有。
