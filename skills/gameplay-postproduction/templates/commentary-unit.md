# 解说单元（可复制模板）

**五要素是内部准备结构，不是口播稿。** 成片解说要**提炼关键取舍**，不要把五段逐条念出来。
`stated_reason` / `retrospective_commentary` / `outcome` **三者必须分列**，禁止互相回填。

机器可校验的形态（TSV，`units` 检查器读这个）：

```
event_id	source_range	state	action	stated_reason	retrospective_commentary	outcome	coverage
ev-007	470.0-478.5	HP 80/80, 3 敌	打出防御+岿然不动	"这回合先满挡"	回头看该回合药水可以省	挡下 12 点	ok
ev-008	478.5-496.0	战斗胜利, 3 选 1	选择加入"武装"	null	其实该拿输出牌	牌组 +武装	ok
```

列说明：

| 列 | 必需 | 说明 |
|---|---|---|
| `event_id` | 是 | 与素材登记/时间线一致 |
| `source_range` | 是 | `起-止` 秒，源时间基 |
| `state` | 是 | **当时可见**的状态 |
| `action` | 是 | 实际做了什么 |
| `stated_reason` | 是 | **当时公开说的**；没有写 `null` 或 `未记录`（**不得**用事后解释回填） |
| `retrospective_commentary` | 是 | **事后复盘**（含事后合理化）；没有写 `null` |
| `outcome` | 是 | 实际结果 |
| `coverage` | 是 | `ok` / `na`(本局未发生) / `coverage_gap`(日志有画面无) |

## 五要素填写区（给需要展开的单元）

```
单元 #__  event_id ____  源 ___-___s
  情况  ：
  候选  ：
  选择  ：
  理由(stated_reason)      ：
  事后复盘(retrospective)  ：
  实际结果(outcome)        ：
```

```bash
S="$HOME/.agents/skills/gameplay-postproduction"
python3 "$S/scripts/check_postproduction.py" units "units.tsv"         # 输入格式：TSV（TAB 推荐；`|` 也支持，自动识别）
```
