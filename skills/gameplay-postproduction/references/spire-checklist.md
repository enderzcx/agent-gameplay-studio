# Slay the Spire 2 清单（`spire`）

本文件是**该游戏特有**的部分。通用流程见 `references/postproduction-standard.md`。
**只有本文件存在，才表示该游戏已适配**；其他游戏未适配，不要套用本清单。

---

## 必须解析的单元

按**本局实际发生**判断（发生了逐个覆盖；没发生记 `N/A` 不凑数；有日志没画面记 `coverage_gap`）：

| 单元 | 说明 |
|---|---|
| 选牌 | 三选一/多选一，含被选中的是哪张 |
| 跳过牌 | 明确放弃拿牌 |
| 商店 | 购买/删除/离开 |
| 营火 | 升级 或 休息（以及选择了哪张牌升级） |
| 路径选择 | 节点类型与走向 |
| 关键回合 | Boss、精英、致死回合、胜负结算 |

同一类单元出现多次（例如连续几次选牌）时，**每一次都要覆盖**。

## 关键结论回源字段（§3.2 的具体化）

```
胜负 / 死亡 / 存活      选牌与跳过结果      伤害数值
血量                    金币                 Boss 与精英身份
```

**尾段强制覆盖**：必须看到结局画面。本机实测两种典型错误：
- 默认档曾把「**败北**」说成「**胜利**」（关键结论反转）；
- 高档模型曾在片长 43.4s 的素材上**只覆盖到 36s** 就写"视频结束"。

两者都说明：**结局必须回源帧确认**。

## 典型素材结构（用于判断覆盖）

- **多窗口录屏**（左游戏 + 右助手面板）：右面板常带中文战报 → **画面内答案泄漏**，
  必须声明；且会让"是否真的听懂音轨"无法从该片判定。
- **旁白片**：音轨是后期 TTS 叠加，**画面里可能同时有烧录字幕**（与音轨同文）。
- **原速原始录屏**通常**没有音轨**（有意静音），按 §1 登记为"无音轨"，**不得编造源音**。
  2026-09-23 起本机有了带真实游戏原声的录制入口（见下节）；**旧素材仍是无声**，登记时如实写。

## 音画错配的本地口径

- ✅ 有效：旁白说"这里满挡"，而画面显示实际掉血 → 旁白对**当前画面**提出了可核实事实却矛盾。
- ✅ 有效：旁白称"Boss/神官"，画面是普通遭遇战 → 对**当前画面**的身份判定矛盾。
- ❌ 误报：旁白报暗号/编号（例如口令、记录编号），而画面**本来就不显示**该信息 → **不算错配**。
  本机实测该误报对默认档与高档模型都出现过。

## 首个风格样片建议

**一场完整战斗 + 一次选牌**。按验收问题单做局部修改，不重做整场。

## 录游戏原声（本机实测入口 · 2026-09-23）

**入口**：本仓库自带 `tools/gamerec/`（macOS / ScreenCaptureKit）。目标 app **没有内置默认值**，
必须用 `GAME_BUNDLE_ID` 显式指定，免得抓错 app 静默录到别的东西。

```bash
tools/gamerec/build.sh                                  # 首次/改动后编译 → tools/gamerec/build/GameAVRec.app
export GAME_BUNDLE_ID="com.megacrit.SlayTheSpire2"   # 换成你的目标 app bundle id

tools/gamerec/record-game.sh preflight                     # 权限 / 目标 app / 在屏窗口
tools/gamerec/record-game.sh start --out out.mp4 --duration 60 --focus-log out.focus.jsonl
tools/gamerec/record-game.sh verify out.mp4             # 停止后探测：必须过，否则不算录到游戏声
```

实现：`tools/gamerec/main.swift` —— ScreenCaptureKit
`SCContentFilter(display:including:[目标 app])` + `capturesAudio=true` / `captureMicrophone=false`。
**只抓这一个 app** 的音频与画面，不收麦克风、不收其他 app。
`--focus-log` 每 0.5s 被动记录前台 app 与目标窗口是否在屏（**不切焦点、不 activate**）。
**证据精度**：它记的是"哪个 app 在前台"（`NSWorkspace.frontmostApplication`），**不等于**目标窗口的 key 状态；
游戏静音判定看的是**窗口焦点**，所以"前台=游戏"**不能**证明当时没静音 ——
"有没有真实游戏声"要以 **astats 逐秒峰值** ＋ **游戏日志行** 为准。

**前置（该游戏实测必须）**：游戏 profile 的 `prefs.save` 里 `mute_in_background: false`。

```
~/Library/Application Support/<游戏名>/steam/<你的 steam id>/modded/profile1/saves/prefs.save
```
（路径里的 `<...>` 是占位符；本仓库不记录任何真实 steam id。）

该游戏自带"后台时静音"（游戏日志 `godot.log`：
`MuteInBackground: FocusOut received` → 限后台 30fps + 静音；`FocusIn` →
`Unmuted, restored volume to 0.5`）。**默认开启时失焦＝游戏自己不出声**，
这时录到的是一条静音音轨（实测 -160 dBFS），容易误判成"抓取失败"。
关掉后日志会打 `MuteInBackground: FocusOut ignored (setting off or saves null)`。
回滚：把 `mute_in_background` 改回 `true`，或还原该文件备份。

**判定口径**：`record-game.sh verify` 用 **ffmpeg 单遍逐秒（1s 桶）** 测峰值，以 **-66 dBFS** 为"该秒有信号"门槛，
并**报告覆盖范围**（桶数 + 音频时长；末桶可能含补齐的零样本）。同时分开报两件事：
**帧是否持续到达**（PTS 间隔）与**画面内容是否真的在变**（抽帧比 md5）——**PTS 连续不等于画面新鲜**。
`--expect av|audio|auto` 按模式校验（`--no-video` 的产物用 `audio`）。**它不证明音画同步**
（只比容器时长与起止时间戳，未做动作级核验），也不证明"这段声音是游戏发出的"。
**非零波形本身不证明是游戏声**：要同时满足「app 过滤隔离测试通过」＋「同一链路在游戏静音开关
前后的对照」＋「与游戏日志对齐」。本机实测（2026-09-23，同一台机、同一录制器）：

| 条件 | 峰值 | 结论 |
|---|---|---|
| 修复前 · 游戏前台 | -29.8 dBFS，0–6s 有声 | 有真实游戏声 |
| 修复前 · 游戏后台 | -inf（数字静音），0/16s | 游戏自身静音，**不是**抓取失败 |
| 修复后 · 游戏后台（全程 Chrome 在前） | -29.7 dBFS，16/16s 有声 | 后台也能录到真实游戏原声 |
| 同时抓 VS Code（游戏在后台放 BGM） | -160 dBFS，22/22 桶静音 | app 级隔离成立，非全系统混音 |

**已知边界（没测就不要宣称）**：最小化 / 隐藏 / 锁屏、游戏重启、多显示器、Windows/Linux 均未验证；
游戏进程必须在运行、且窗口在屏（app 级画面捕获依赖在屏窗口）。
**录制与监听分离**：用户能否听到不作为是否采到的判据。

## 已知工具边界

- 素材理解用 `gemini-companion`（默认 `gemini-3.8-flash-medium`）。
- 精确剪点用 `watch`（dapi）；本机 `dapi` 曾整片超时（`MCP error -32001`），
  此时明确标 `degraded`，改用 `ffprobe`/`ffmpeg` 能在本地完成的步骤，**不绕过任何权限拒绝**。
