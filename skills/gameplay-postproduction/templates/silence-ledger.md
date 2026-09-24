# 静默台账（可复制模板）

用途：把**实际采用的成片**里每一段"长时间没有口播"逐段交代清楚——
保留（画面自解释）、已剪掉、还是已补讲。规则见 `references/postproduction-standard.md` §5.1。

为什么要有这一张表：一段静默是"剪掉无信息等待"还是"漏了解说"，机器看不出来；
但"有没有被逐段交代过"可以。补讲 14 段之后成片反而变长、或者静默从 44s 变 35s，
都必须在这张表上留下依据，否则不允许交付。

```bash
# 先让审计把当前成片里的长静默列出来（阈值默认 20s），再逐条填依据
S=skills/gameplay-postproduction/scripts
python3 "$S/check_timeline_audit.py" audit timeline.tsv \
    --preflight preflight.json --silence-ledger gaps.tsv \
    --subtitle subs.srt --audio voice_master.wav --final-mp4 final.mp4 \
    --json --report-out audit-report.json
```

```
# TAB 分隔；`#` 行与空行被忽略
gap_id	final_range	disposition	reason
g-001	41.300-62.300	keep	该段为原速战斗结算与地图切换，保留关键回合与奖励画面，不放旁白
g-002	120.000-143.500	narration_added	原本整段无解说；已补一段讲奖励取舍，剩余静默是商店比价过程
```

## 列

| 列 | 必需 | 说明 |
|---|---|---|
| `gap_id` | 是 | 台账内唯一 id，方便审查时引用 |
| `final_range` | 是 | 该静默在**成片时间基**上的 `起-止` 秒。**按实际声段算**：每段旁白占 `[final_range 起点 + audio_offset_s, 同上 + 实测 audio_duration_s]`（offset 是 EDL 里的真实值），多段取**并集**。一段 60s 画面只配 1s 旁白就只算 1s 有声，静默从 1s 起——不是从画面窗口末尾起 |
| `disposition` | 是 | `keep`（保留：画面自解释）/ `cut`（已剪掉）/ `narration_added`（已补讲但仍有剩余） |
| `reason` | 是 | **逐段的真实依据**。空、`TBD`、占位符、几个字敷衍都会被审计拒绝 |

## 审计会怎么判

- 成片里每一段 ≥ 阈值（默认 20s，`--silence-threshold` 可调）的静默**都必须有一行**，否则失败；
- 台账里**多出来的行**（成片里找不到对应静默）按**台账陈旧**失败——静默变了就必须重新生成，
  不许沿用旧表，也不许靠清空缓存/重跑一遍糊过去；
- `disposition=cut` 却仍然检出该静默 → **自相矛盾**，失败；
- `final_range` 必须是**有限数、起点 ≥ 0、终点 > 起点**：NaN / inf / 负数 / 倒序一律拒绝；
- 审计**不**把"旁白占比"或"解码无报错"当成内容通过，这两条会原样写在报告的
  `not_a_verdict_on` 里；旁白占比本身也只按**实测声段**计算；
- **用画面窗口算出来的旧台账会对不上**（静默起点会晚一个"窗口-声段"的差值），这是有意为之：
  静默边界必须跟实际配音走；
- 报告里的 `human_annotations_not_machine_verified` 会明说：`keep/cut/narration_added` 的**理由**
  是人给的，机器只核对"逐段给了、且不矛盾"。
