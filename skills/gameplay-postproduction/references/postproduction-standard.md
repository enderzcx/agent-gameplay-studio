# 游戏录屏后期标准（canonical，通用层）

本文件是**唯一权威来源**；把本 skill 装到多处时，指定本文件为 canonical，不要维护第二份流程。
通用规则对任何游戏成立；某款游戏的特有清单放在 `references/<game>-checklist.md`（没有该文件＝未适配）。

适用范围：本地录屏 → 事件解析 → 解说准备 → 剪辑 → 终片审查。
不覆盖：游戏参赛评测（对局决策质量）、公开发布、账号运营。

---

## 0. 与评测隔离（硬约束）

- 评测与后期是两条独立线。评测侧遵循**各赛道自己既定的协议**；本流程**不指定、不推荐**评测侧用任何模型或工具。
- 后期产出的 `events` / `cards` / `stated_reason` **只是候选**，不是对局事实的裁定。
- 后期输出**不得回流**到参赛决策或任何被评测的行为；**后期不参与参赛决策**。

---

## 1. 录制（开录前必做）

1. **优先完整原速录屏**；多窗口（游戏 + 助手/聊天面板）是**附加**，不是必需。
   - 多窗口会把面板里的战报/分析文字录进画面 → **答案泄漏**，也让模型可能不靠音轨作答。
   - 若必须多窗口，审查时**先声明该段存在画面内答案**。
2. **开录前验证捕获**：
   - 实际录一段并回放 5 秒，确认游戏画面在录、不是黑屏/静态帧。
   - **检查并记录音轨情况**（`ffprobe` 是否看到 audio stream）。这**不是**硬性阻断条件：
     - 预期有游戏声却丢失 → **补录，或标记为素材缺陷**；
     - **有意静音**的源（例如后期另行配音）→ **可以接受**，按"无音轨"如实登记；
     - **禁止编造源音**：不得给静音源补一段假装的"原始游戏声"；
     - 但**带解说的最终 MP4 仍然必须有音轨**。
3. **脱敏**：录制前关闭/移出含密钥、token、私人对话、真实姓名的窗口。日志与素材不得出现密钥。
4. 分辨率与帧率按成片目标设定；**不要**为了省空间预先抽帧或降帧——原速原始素材是唯一可信时间基。

---

## 2. 素材登记（唯一时间基）

每个源素材入库记录：

```
asset_id, source_path, sha256, size_bytes,
ffprobe: duration_s / r_frame_rate / codec / width / height / sample_rate / channels / has_audio,
recorded_at, session_id, notes(是否多窗口/是否含画面内答案)
```

逐事件状态，**与素材同一时间基**（源时间戳）：

```
event_id, source_start_s, source_end_s,
state(当时可见的状态: 面板/数值/单位/敌人等),
action(实际做了什么),
stated_reason(当时公开说出的简短理由; 没有就写 null/未记录),
retrospective_commentary(事后复盘, 含事后合理化),
outcome(实际结果)
```

- `stated_reason` 是**当时**说的；`retrospective_commentary` 是**事后**的；`outcome` 是**实际结果**。
  **三者必须分字段**，不得混写，尤其**不得用事后解释回填 `stated_reason`**。
- **先检查素材覆盖**：某段只在日志里出现、原始画面缺失时，**不要用日志替代缺失画面**——
  标记 `coverage_gap`，该单元不得进入成片解说。

模板：`templates/asset-register.md`

---

## 3. 事件解析

用 `gemini-companion` 对素材（或源区间）跑理解，得到候选
`events / visible / audio_said / audio_video_mismatch / uncertain`。

**必须解析的单元**按**素材里实际发生**判断，不是照清单凑数：

- **发生了的**：该游戏清单里的单元 → **逐个事件覆盖**，不能"同类讲过一次"就跳过第二次。
- **没发生的**：可以记 `N/A`，**不准为了凑齐清单编造或脑补**。
- **有日志但画面没录到**：记 `coverage_gap`，该单元**不得**进入成片解说。

每单元在**内部准备**里写齐五要素：**情况 → 候选 → 选择 → 理由 → 实际结果**。

> ⚠️ 这是**内部准备结构，不是成片口播格式**。成片解说要**提炼关键取舍**，
> **不要**把五段逐条念出来（"情况是……候选是……选择是……"会变成废话）。五要素用于保证不丢信息。

**理由与事后复盘必须分开**：

| 字段 | 含义 | 缺失时 |
|---|---|---|
| `stated_reason` | **当时公开说出的**简短理由 | 如实写 `null` / `未记录`，**不得**用事后解释回填 |
| `retrospective_commentary` | **事后复盘**（含事后合理化） | 单独字段，**禁止**冒充 `stated_reason` |
| `outcome` | 实际结果 | 单独记录 |

模板：`templates/commentary-unit.md`

### 3.1 模型策略

- **默认模型**：`gemini-companion` 的当前默认（`gemini-3.8-flash-medium`），做素材理解、
  候选片段定位、解说分镜、审片首轮。
- **第二意见**：更高档模型（`gemini-3.1-pro-high`）**只在必要时对该片段显式调用一次**：
  默认模型自报低把握 / 结论与已知事件冲突 / 关键音画判断有争议。**不逐任务自动重跑**。
- 仍有冲突 → **回源帧或游戏状态**。**高档模型不是真值裁定者。**
- **可用性后备**（`gemini-companion` 的 MiMo 后端）只在**可恢复的可用性故障**下自动启用；
  权限拒绝、认证失败、内容安全拒绝**不切供应商绕过**。

### 3.2 必须回源核验的关键结论

以下字段**永远不能**只凭模型输出定稿，必须核对**真实源帧或游戏状态**：

```
胜负 / 死亡 / 存活   选择与跳过   伤害数值   资源数值   身份或阶段判定
```

（各游戏的具体字段见其 `<game>-checklist.md`。）

- **视频尾段强制覆盖**：必须确认素材**最后 10%** 的画面结论（尤其结局画面），
  不能接受"中途就写视频结束"。
- **模型自报的 `confidence` 不构成核验**，只是路由提示。

### 3.3 音画错配的判定口径（易错）

- **只**在"旁白对**当前画面**提出了**可核实的事实**、而画面与之**矛盾**"时，才算 `audio_video_mismatch`。
- 旁白补充画面**没有显示**的信息（暗号、编号、口令、计划、意图、背景）→ **不算错配**。
- 代价是双向的：漏报会让错误解说进成片，**误报**会让正确解说被无端返工。**按实报，不按条数比强弱。**

### 3.4 候选时间不是剪点

模型给的 `start_s/end_s` **一律是候选**。精确剪点用 `ffmpeg` 场景检测或源帧吸附后确定。
逐帧与 `MM:SS` 级定位交给 `watch`；本流程不产出可直接下刀的剪点。

---

## 4. 剪辑

- **关键战斗/关键操作原速保留**：动作与结算必须完整。**不机械全片倍速。**
- **剪掉无信息等待**（加载、无操作停帧、重复翻页）。
- 必要时用**短定格 / 局部放大**替代倍速。**（定格是剪辑手段，不是缺陷。）**
- **唯一 timeline manifest**：视频、配音、字幕**共用同一份**，不得各自维护一份时间。
  三档时间必须显式区分，任何一次切分/拼合都要登记映射，**同一次偏移只加一次**：

  ```
  asset_id | event_id | source_range | clip_range | final_range | speed/freeze | narration_text | audio_duration_s | subtitle_source
  ```

  - **时间算术必须自洽**：`final` 长度 = `clip` 长度（按 `speed` 换算）**加上** `freeze` 时长。
  - `speed/freeze` 必须写明，`1x + 定格Ns` 这种组合要能一眼看出。
  - `subtitle_source` 指向**实际口播稿/音频对齐**产物，不是另行扩写的文案。
  - 这是**登记表**，本流程**不实现自动生成引擎**。

模板：`templates/timeline.md`　检查：`scripts/check_postproduction.py timeline <file>`

---

## 5. 解说与字幕

- **开场简短自我介绍**（一两句，不铺陈）。
- **口播自然、有停顿**，不要赶成一条连读。
- **逐事件生成配音**，每段生成后**测实长**再据此细剪——不要先剪好再硬塞配音。
- **字幕来自实际口播稿/音频对齐**，不是另写一份扩写文案；说完即消失，不留长驻字幕；
  字幕文字 = 实际说出的文字（同音误听按实际音频改正）。

---

## 6. 终片审查

**最终检查实际导出的 MP4**，而不是脚本退出码 0，也不是渲染日志。

复制 `templates/review-sheet.md` 逐项填写；任何一项不通过就**局部修改该段**，不重做整场。

必查：
1. 文件可播放；时长符合**项目预期/容差**（与"音画同步"是两件事，**不要混用一个阈值**）；
   音轨存在且与画面同步。
2. 按素材**实际发生**的单元齐全（发生了的逐个覆盖；没发生的记 `N/A` 不凑数；有日志没录到的记 `coverage_gap`）。
3. 每单元五要素齐全，`stated_reason`（缺失如实 `null`/`未记录`）、`retrospective_commentary`、`outcome` **三者分列**。
4. 关键结论与源帧一致（见 §3.2 与游戏清单）。
5. 尾段结局画面已覆盖。
6. 字幕与音频逐句一致；无长驻字幕。
7. 三档时间映射自洽，无重复偏移。
8. 无密钥/私人信息入画入声。

**未知不得当通过**：没有真值可比对时填 `unknown`，**不要臆造 `0`**。

检查：`scripts/check_postproduction.py sheet <file>`

---

## 7. 修复

- **尽量局部**：按问题单里的源/成片区间只改该段，**不重做整场**。
- 每条问题要能**唯一定位到单元**（`event_id`）与**素材**（`asset_id`）。
- 修复后重跑受影响检查；**仍不能确认的事实如实挂起**（记 `unknown`），**不无限循环**。

---

## 8. 首个风格样片

第一支样片只做**最小可用单元组合**（各游戏清单给出具体建议，例如"一场完整战斗 + 一次选择"）。
按验收问题单做局部修改，**不要每次重做整场**。样片通过后再扩展单元。

---

## 9. 最小可复制命令

```bash
GCB=~/.dsh/plugins/gemini-companion/0.1.1/scripts/gcb.py
S=~/.agents/skills/gameplay-postproduction
SRC=/abs/path/to/recording.mp4        # 换成真实绝对路径；不要写尖括号占位（shell 会当重定向）

# 1) 素材登记
ffprobe -v error -show_streams -show_format -of json "$SRC"

# 2) 素材理解（默认 Flash Medium，不传 --model）
python3 "$GCB" video --path "$SRC" --kind game --json
python3 "$GCB" video --path "$SRC" --start 470 --end 506 --kind game --json

#    异步 job 必须取回：launch 返回 job_id，随后 wait 到终态再 result
python3 "$GCB" video --path "$SRC" --background --json      # --background 已是默认（等价不写）
JOB=20260923-162145-video-77f92a46    # 换成上一步真实返回的 job_id
python3 "$GCB" wait   "$JOB" --timeout 120 --json           # 反复直到 completed=true
python3 "$GCB" result "$JOB" --json                         # 正文与 provenance

# 3) 关键片段第二意见（仅必要时，只对该片段）
python3 "$GCB" video --path /abs/path/to/clip.mp4 --model gemini-3.1-pro-high --json

# 4) 产物校验
python3 "$S/scripts/check_postproduction.py" timeline timeline.tsv
python3 "$S/scripts/check_postproduction.py" units    units.tsv
python3 "$S/scripts/check_postproduction.py" sheet    review-sheet.md

# 5) 精确剪点定位（不归本流程；dapi 命令面以本机 help 为准）
dapi media probe --help
dapi media grab  --help
```

> **§9 的可复制性边界（公开包必读）**：第 2/3 步里的 `gcb.py` 是作者本机的**可选外部适配器**，
> 不在本仓库内；**装了本仓库也不会得到它，更不附带任何模型额度、订阅、代理或登录态**。
> 换成任何"能读本地视频并给出**带 provenance** 的事件候选"的通道都成立（含你自己写的），
> 不变的是纪律：**模型给的是候选，必须回源帧核验**。
> 第 1 步（`ffprobe`）与第 4 步（本仓库的 `check_postproduction.py`）**完全离线、不需要任何模型**。
> 第 5 步 `dapi` 同理，属可选外部工具。
>
> **dapi 说明**：只给 `--help` 入口。作者本机 2026-09-23 实测 `dapi media probe/grab <path>` 返回
> `MCP error -32001: Request timed out`（后端不可达），因此**没有**本机跑通的可复制示例；
> 请以你当时 `--help` 输出为准。这也是一条通用纪律：**工具不可达就如实标 degraded，不要绕过**。

> 本文件是**工作流规范**，不声称流程已由代码自动实施；本仓库只提供**规范 + 模板 + 确定性检查器**
> 与三个独立小工具（录音器 / TTS 适配器 / EDL 组装）。视频理解与剪辑由你选定的外部工具完成。
