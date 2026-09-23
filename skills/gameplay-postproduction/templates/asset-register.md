# 素材登记（可复制模板）

用法：复制到本次工作目录，每个源素材一组。字段定义见 `references/postproduction-standard.md` §2。

```bash
# 采集（真实路径，不要用尖括号占位）
SRC=/abs/path/to/recording.mp4
shasum -a 256 "$SRC"
stat -f%z "$SRC"          # macOS；Linux 用 stat -c%s
ffprobe -v error -show_streams -show_format -of json "$SRC"
```

```
asset_id        :
source_path     :
sha256          :
size_bytes      :
duration_s      :
r_frame_rate    :
codec           :
width x height  :
has_audio       : 是 / 否      # 否 = 允许（有意静音），但不得编造源音
audio_spec      : codec / sample_rate / channels   （has_audio=否 时填 N/A）
recorded_at     :
session_id      :
multi_window    : 是 / 否      # 是 → 画面内可能有答案，审查时必须声明
onscreen_answer : 无 / 战报文字 / 烧录字幕 / 其他：____
notes           :
```

## 覆盖核验（写旁白之前必做）

| 单元 | 画面有？ | 日志有？ | 判定 |
|---|---|---|---|
| | | | 可解析 / `N/A`(未发生) / **`coverage_gap`**(日志有画面无) |

- `coverage_gap` 的单元**不得**进入成片解说；不得用日志文字替代缺失画面。
