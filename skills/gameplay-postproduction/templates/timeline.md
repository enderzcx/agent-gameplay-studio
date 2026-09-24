# 统一时间线 manifest（可复制模板）

**视频、配音、字幕共用这一份**，不得各自维护一份时间。
字段与算术规则见 `references/postproduction-standard.md` §4。

```
# ⚠️ 下面 2 行是虚构演示数据，不是真实对局，只为说明字段与算术。
asset_id | event_id | source_range | clip_range | final_range | speed/freeze | narration_text | audio_duration_s | subtitle_source
rec-01 | ev-007 | 470.0-478.5 | 0.0-8.5 | 12.3-20.8 | 1x | 先看这手 | 8.1 | tts-run-3
rec-01 | ev-008 | 478.5-496.0 | 8.5-26.0 | 20.8-41.3 | 1x + 定格3s | 这里选牌 | 14.2 | tts-run-3
```

## 格式

**TAB 分隔的 TSV**（推荐）或 `|` 分隔的表格都支持，检查器自动识别；
两种写法都必须有表头行，`#` 开头的行与空行会被忽略。

## 字段

| 列 | 必需 | 说明 |
|---|---|---|
| `asset_id` | 是 | 对应素材登记 |
| `event_id` | 是 | 对应解说单元 |
| `source_range` | 是 | `起-止` 秒（源时间基） |
| `clip_range` | 是 | 片段自身时间基；**长度必须等于 `source_range` 长度** |
| `final_range` | 是 | 成片时间基；**长度 = `clip` 长度 ÷ speed + freeze 时长** |
| `speed/freeze` | 是 | 如 `1x`、`1.5x`、`1x + 定格3s`、`2x + 定格1.5s` |
| `narration_text` | 是 | 该段实际口播稿（**非**另行扩写文案）；纯画面段填 `-` |
| `audio_duration_s` | 是 | 该段配音**实测**时长（生成后测，不要估算）；纯画面段填 `0.000` |
| `subtitle_source` | 是 | 字幕来源标识（口播稿/音频对齐产物）；**全片只能有一个值** |

上面是结构列。**交付路径还要求下面这些审计列**（`ready` 门槛会强制跑一遍语义审计，
缺列直接判未就绪；纯画面行可以填 `-`）：

| 列 | 必需 | 说明 |
|---|---|---|
| `event_phase` | 是 | 该行**画面**所处阶段：`setup`/`battle`/`reward`/`map`/`shop`/`rest`/`event`/`other` |
| `claim_phase` | 旁白行 | 该行**旁白主张**针对的阶段（同枚举） |
| `claim_mode` | 旁白行 | `live`（主张阶段与画面一致）或 `retrospective`（跨阶段事后回顾）。**跨 phase 必须写 `retrospective`**，否则失败；同一事件上「画面还没到那个阶段就在讲结果」即使写了 `retrospective` 也失败 |
| `anchor_asset` | 旁白行 | 旁白讲的**素材**。必须等于本行 `asset_id`（跨素材锚点机器无法核验，会直接失败） |
| `anchor_event` | 旁白行 | 旁白讲的**回合**，`+` 连接。必须是本行 `event_id` 的子集 —— 单事件镜头不许把旁白挂到别的事件上；真要跨回合，就把 `event_id` 写成 `a+b` |
| `anchor_source` | 旁白行 | 旁白引用的**源区间** `起-止`。必须落在本行 `source_range` 之内（说的那一刻要在画面上）；**跨回合镜头**（`event_id` 含 `+`）还要求它覆盖整个镜头跨度 |
| `evidence` | 旁白行 | 人写的**来源说明**（源帧时间码等）。它是给人看的锚点说明，**本身不是机器证据** |
| `hold_mark` | `freeze>0` | 保持帧的**标注**，必须含被保持那一帧的源时间码，如 `复盘定格 · 源 rec-win@1390.0s（候选保持）`。锚点还要落在 `anchor_source`（有旁白时）之内。**不许用无标注的长静帧填配音** |
| `visible_window` | 奖励保持帧 | 候选/结论**真正可见**的源区间。保持帧锚点必须落在该区间**之内**（否则定格会停在候选已经消失的画面上） |
| `subtitle_sha256` | 旁白行 | 实际采用字幕文件的**内容摘要**；全片只能有一个值 |
| `audio_sha256` | 旁白行 | 实际采用旁白音轨的**内容摘要**；全片只能有一个值 |
| `final_sha256` | 旁白行 | 实际导出成片的**内容摘要**；全片只能有一个值 |

**为什么三个 hash 是必需的**：只数字幕段数、音轨时长或只 hash 输入原片，都挡不住
「同样 74 段但改了词/改了时间的 SRT」和「上一版音轨/旧 MP4」。审计会把这三份产物的
**内容摘要**与这一份时间线绑定，并逐条核对字幕**文本与时点**。

**关于全局 phase 顺序**：`setup < battle < reward < map < shop < rest < event < other` 只是枚举顺序，
**不是**时间轴——循环类游戏里 map/shop/rest/battle 会反复出现。所以跨阶段句一律要显式声明
`claim_mode=retrospective`，机器只在**同一事件内部**判「提前讲结果」。

**关于成片与 receipt**：`--final-mp4` 必须与时间线**同版同长**（内容摘要 + 时长都要对得上），
并且要有一份 `produce-receipt.json`（制作路径在产出那一刻写出，把这份 timeline 与
字幕/音轨/成片绑在同一次制作上）。只读旧媒体再手写一份声明，不算验证。

**这些锚点是"声明"，不是"证明"**：机器只核对它们与本行真实画面源区间是否自洽，
不核对「这句话确实在讲那个回合」。报告里的 `human_annotations_not_machine_verified` 会把这条边界写出来。

## 自检

```bash
S="skills/gameplay-postproduction/scripts"          # 或用你宿主的安装路径
python3 "$S/check_postproduction.py" timeline "timeline.tsv"   # 结构：列齐全 + 算术自洽

# 交付前还要过语义审计（阶段锚点 / 保持帧 / 静默依据 / 版本绑定 / stale 素材台账）
python3 "$S/check_timeline_audit.py" preflight --json --out preflight.json rec-win=/abs/win.mp4
python3 "$S/check_timeline_audit.py" audit "timeline.tsv" \
    --preflight preflight.json --silence-ledger gaps.tsv \
    --subtitle subs.srt --audio voice_master.wav --final-mp4 final.mp4 \
    --receipt produce-receipt.json --json --report-out audit-report.json
```

结构检查器验证：列齐全、区间可解析、`clip` 长度 == `source` 长度、
`final` 长度 == `clip` 长度 ÷ speed + freeze、同一 `asset_id` 的 `clip`/`final` 连续不重叠。
**它不判定阶段、保持帧与静默**——那是 `audit` 的职责，两者语义不同，不要互相替代。
