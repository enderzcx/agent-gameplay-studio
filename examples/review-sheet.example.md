# SYNTHETIC EXAMPLE — fabricated data, not a real match.
# A *filled* review sheet that is structurally valid. It is deliberately NOT delivery-ready:
# the misreport counts are `unknown` because no independent ground truth exists here.
# See examples/README.md.
#
# 审片验收问题单（示例：字段已填，但结论仍未就绪）

```
成片文件        ：examples/does-not-exist-final.mp4
成片时长        ：46.3            项目预期/容差：0.5
源素材 asset_id ：rec-example-01            sha256：0000000000000000000000000000000000000000000000000000000000000000
剪辑完成时间    ：YYYY-MM-DD
审查人/模型     ：synthetic example (not a real reviewer)
```

## A. 文件与时间线

| # | 检查项 | 结果 | 证据（命令输出/帧号/时间码） |
|---|---|---|---|
| A1 | 实际导出的 MP4 可播放 | 是 | ffprobe -v error final.mp4 → 1 video stream, 1 audio stream |
| A2 | 时长符合项目预期/容差（容差见上） | 是 | ffprobe format=duration → 46.28s, declared 46.3 tol 0.5 |
| A3 | 音轨存在、与画面同步 | 是 | ffprobe: aac 48kHz 2ch present; container durations differ by 0.01s |
| A4 | 三档时间映射自洽（源→片段→成片），同一次偏移只加一次 | 是 | examples/timeline.example.tsv passes checker timeline mode |
| A5 | 无**非预期**黑屏/冻结/丢帧；**已登记的定格是剪辑手段，不算缺陷** | 是 | frame-diff sample over 12 timestamps: 12/12 distinct; registered freeze at 20.8-23.8s is expected |

## B. 单元覆盖（按素材实际发生判断）

| 单元 | 出现？(是/否→N/A) | 事件数 | 源时间(s) | 成片时间(s) |
|---|---|---|---|---|
| 选牌 | 是 | 1 | 478.5-496.0 | 20.8-41.3 |
| 跳过牌 | 是 | 1 | 496.0-506.0 | 41.3-46.3 |
| 商店 | 是 | 1 | 496.0-506.0 | 41.3-46.3 |
| 营火（升级/休息） | 是 | 1 | 506.0-512.0 | (not in final cut) |
| 路径选择 | 是 | 1 | 470.0-478.5 | 12.3-20.8 |
| 关键回合（Boss/精英/致死/结算） | 是 | 1 | 518.0-530.0 | (excluded) |

- `coverage_gap`（只在战报里、画面缺失）：ev-006 boss fight — log present, screen missing, excluded from the cut

## C. 每单元五要素（**内部准备，不是口播稿**）

```
单元 #1  源时间 470.0-478.5s / 成片 12.3-20.8s
  情况      ：HP 80/80, 3 enemies
  候选      ：block card / damage card
  选择      ：block card
  理由(stated_reason，当时公开说的；没有就写 null/未记录) ："hold this turn, take nothing"
  事后复盘(retrospective_commentary，含事后合理化)        ：the potion was saveable
  实际结果(outcome)                                        ：blocked 12
  ← 三者必须分列；禁止用事后复盘回填 stated_reason
```

## D. 关键结论回源核验

| 结论 | 模型候选值 | 源帧/游戏状态实测 | 一致？ |
|---|---|---|---|
| 胜负 | win | win (end screen) | 是 |
| 死亡/存活 | alive | alive | 是 |
| 选牌结果 | card A | card A | 是 |
| 伤害数值 | 12 | 12 | 是 |
| 血量 | 68 | 68 | 是 |
| 金币 | 210 | 210 | 是 |
| Boss/精英身份 | elite | elite | 是 |

- 尾段结局画面已强制覆盖（素材最后 10%）：是 → 结论：win, verified against the end screen
- 触发过第二意见（Pro）吗？ 否 → 冲突是否已回源解决：not applicable

### D2. 问题清单（发现问题必填，不能只写"否"）

| issue_id | event_id | asset_id | 源区间 | 成片区间 | 旁白主张 | 实际画面 | 证据 | 局部修正建议 |
|---|---|---|---|---|---|---|---|---|

- 每条问题都要能**唯一定位到单元**（`event_id`）与**素材**（`asset_id`）并给出源/成片区间。
- 修正范围默认**只限该段**，不重做整场。

## E. 解说与字幕

| # | 检查项 | 结果 | 证据 |
|---|---|---|---|
| E1 | 开场有简短自我介绍 | 是 | audible at 00:00.2-00:04.0 |
| E2 | 口播有自然停顿，非连读赶稿 | 是 | 6 inter-sentence pauses >= 0.35s measured on the master track |
| E3 | 每段配音生成后测过实长再细剪 | 是 | per-segment ffprobe durations in examples/timeline.example.tsv |
| E4 | 字幕与音频逐句一致 | 是 | subs.srt vs narration_text, 3/3 identical |
| E5 | 说完即消失，无长驻字幕 | 是 | last cue ends 3 tracks before EOF |
| E6 | 字幕为实际口播稿，非另行扩写 | 是 | subtitle_source column shows the TTS run id |
| E7 | 读音无错：多音字/术语/数字/英文专名已按**实际音轨**核过（不是按原稿推断） | 是 | listened to the full master track; no polyphone or term misreads found |
| E8 | 旁白主张与画面不矛盾：旁白对**当前画面**提出的可核实事实已回源帧核对 | 是 | each narration claim mapped to a source frame timecode in D3 |
| E9 | 音轨里没有残留下不该出现的导演提示/指令性话语 | 是 | full-track listen; no instruction text spoken |

## F. 音画错配判定

| 上报的 mismatch | 是否"旁白对当前画面提出可核实事实却矛盾" | 判定 |
|---|---|---|
| (none reported in this synthetic example) | 是 / 否（旁白补充画面未显示的信息 → 不算错配） | 误报 |

- 误报数：unknown  漏报数：unknown   （**不按条数比强弱**）
- **没有真值可比对时填 `unknown`**，不要臆造 `0`。
  漏报尤甚：没有独立真值就无法声称"零漏报"。

## G. 安全与边界

| # | 检查项 | 结果 |
|---|---|---|
| G1 | 画面/声音无密钥、token、私人信息 | 是 |
| G2 | 若为双窗，已在审查记录中声明画面内答案 | N/A |
| G3 | 无模型输出回流到参赛决策 | 是 |

## H. 审查结论

```
局部修改（列出段落与时间码）
本轮修改段落：none — this synthetic example exists to show field shapes only
是否需要重做整场：否（默认）
```
