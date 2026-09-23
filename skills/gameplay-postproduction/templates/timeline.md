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
| `narration_text` | 是 | 该段实际口播稿（**非**另行扩写文案） |
| `audio_duration_s` | 是 | 该段配音**实测**时长（生成后测，不要估算） |
| `subtitle_source` | 是 | 字幕来源标识（口播稿/音频对齐产物） |

## 自检

```bash
S="$HOME/.agents/skills/gameplay-postproduction"
python3 "$S/scripts/check_postproduction.py" timeline "timeline.tsv"   # 输入格式：TSV（TAB 推荐；`|` 也支持，自动识别）
```

检查器会验证：列齐全、区间可解析、`clip` 长度 == `source` 长度、
`final` 长度 == `clip` 长度 ÷ speed + freeze、同一 `asset_id` 的 `clip`/`final` 连续不重叠。
