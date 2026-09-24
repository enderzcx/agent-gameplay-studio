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

### 1.1 开录后、写旁白前的输入预检（机器判定，不靠人填）

素材入库前用确定性探测把**输入状态**写成台账，不要等到审片才发现：

```bash
A=skills/gameplay-postproduction/scripts/check_timeline_audit.py
python3 "$A" preflight --json --out preflight.json rec-both=/abs/both.mp4 rec-win=/abs/win.mp4
```

台账逐素材给出：`sha256`、`size_bytes`、`duration_s`、**实测帧率**（帧数÷时长，不是容器声明的
`r_frame_rate`）、`has_video`、`has_audio`、`audio_signal ∈ present|silent|absent`、
`frame_sampling ∈ normal|sparse`、`status ∈ determined|undetermined`。

口径（三条都容易搞错）：

- **`silent` ≠ `absent`。** 有音轨但 `max_volume` 低于阈值是"**静音音轨**"，不是"有游戏原声"；
  必须查明是目标自身静音、抓错目标还是权限问题。`absent`（真的没有音轨）是**允许**的
  （有意静音源），但**不得编造源音**，成片音轨仍必须存在。
- **稀疏采集是源属性，不是剪辑缺陷。** 约 1.5 fps 的合成录制按真实速度播放是**时间正确**，
  但运动信息本来就少：不得据此声称画面动感充足，**不得靠插帧假装流畅**，也不得把它当成
  "剪辑又插了静帧"。
- **`undetermined` 一律不得当作通过。** 探测不出来（拿不到帧数、量不到音轨信号）就**非 0 退出**，
  先修可读性再继续；`audit` 与 `ready` 都会因此判未就绪。

这份台账同时是 `audit` 的输入：它记的 `sha256`/`size_bytes` 之后会被拿来与磁盘**重新比对**，
所以它也是"素材有没有换过"的唯一凭据（见 §4.4）。

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

模板：`templates/asset-register.md`；机器可读版本由 §1.1 的 `preflight --out` 生成
（两者冲突时以**实际探测**为准，手填的 `has_audio` 不得覆盖探测结果）。

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

### 4.1 三档时间的含义不许互相顶替

| 时间 | 含义 | 谁可以改 |
|---|---|---|
| `source_range` | **源时间基**：素材里真实发生的区间。任何速度/冻结都**不改**它 | 只有素材本身 |
| `clip_range` | **片段时间基**：切出来的片段自身从 0 起算，长度 == `source_range` 长度 | 切片段 |
| `final_range` | **成片时间基**：实际装配后的位置，长度 = `clip` ÷ speed + freeze | 装配 |

速度与冻结**只**影响 `final`。字幕、音轨与一切版本标识**都从这一份实际采用的时间线生成**——
不允许"字幕另算一套时间"或"音轨按旧版稿生成"。审计会核对：

- 全片只有一个 `subtitle_source`（多版本混用直接失败）；
- 时间线自带 `subtitle_sha256` / `audio_sha256` / `final_sha256` 三份**内容摘要**；
- 字幕文件的内容摘要 == 声明值；字幕段数 == 旁白行数；**逐条**字幕文本 == 该行口播稿，
  且起止落在该行的成片区间之内；
- 音轨内容摘要 == 声明值，且音轨时长 == 时间线总长（容差内）；
- 成片内容摘要 == 声明值，时长 == 总长（容差内），且确实带音轨。

**只数字幕段数、音轨时长，或只 hash 输入原片都不够**：同样 74 段但改了词或改了时间的 SRT、
上一版的音轨、上一版的 MP4，都会在这套绑定下失败。`audit` 把 **timeline ↔ 字幕 ↔ 音轨 ↔ 成片**
绑成同一版本，并把四份摘要写进报告。

`ready` 门槛**不接收**任何"现成的审计 JSON"：它总是现场重跑一遍审计，
所以一份过期或伪造的独立报告不能替代现场判定。`ready` 还会把审计的 warnings / `unverified`
原样带进自己的产物——无原声、稀疏采集、人工声明项这些限制**不允许在门槛里被吞掉**。

### 4.1.1 制作 receipt：同一次制作的绑定

四份内容摘要只说明"时间线里写的 hash == 现在磁盘上的文件"，**不说明这些产物是同一次做出来的**：
拿一份旧成片、再手写一份声称旧 hash 的时间线，也能对上。所以交付时还要一份
`produce-receipt.json`，由**制作路径在产出那一刻**写出（`build_sample.sh` 已自动写）：

```json
{ "schema": "gameplay-postproduction/produce-receipt/1",
  "produced_by": "tools/voice/build_sample.sh", "produced_at": "…",
  "timeline": {"sha256": "…"}, "subtitle": {"sha256": "…"},
  "audio": {"sha256": "…"}, "final": {"sha256": "…"},
  "sources": {"rec-win": "…"} }
```

审计会核对：receipt 里的 timeline 摘要 == 这一份 timeline；三份产物摘要 == 现在的文件；
`sources` 与 preflight 台账逐条一致。任何一条不符 → "不是同一次制作"。

**边界要说清楚**：receipt **没有签名**，防不了蓄意伪造。它的作用是让"复用旧媒体 + 新造声明"
变成一次明显的、会被逐条比对拦下的不一致，而不是一个默认通过的路径。

### 4.2 锚点交叉核对：旁白说的那一刻必须在画面上

阶段只是粗粒度。同属 `battle` 的第 6 场和第 7 场是**不同事件**，把一场的台词挂到另一场的画面上，
阶段检查看不出来。所以每个旁白行都要显式声明它引用的 **(素材, 回合, 源区间)**：

```
anchor_asset  | anchor_event  | anchor_source
rec-win       | f31           | 1389.0-1393.0
```

审计逐行核对，且**只信这一行真实画面的源区间**：

- `anchor_asset` 必须等于本行 `asset_id`（跨素材锚点机器无法核验 → 直接失败）；
- `anchor_event` 必须是本行 `event_id` 的子集。单事件镜头把旁白挂到别的事件上 → **失败**
  （这就是"换了同 phase 但不同 event 默默过"）；
- `anchor_source` 必须落在本行 `source_range` **之内** —— 说的那一刻没在画面上就失败。
  1.2 秒的动作配 25.76 秒的口播，只要按真实跨度填 `anchor_source` 就必然失败；
  声明了锚点不等于锚点被证实：`evidence` / `anchor_*` 都是**人工声明**，机器只核对它们与画面源区间自洽；
- **跨回合句**：把该行 `event_id` 写成一个 `+` 连接的集合（`h03_f33c+a07_c31b`）来**显式声明**
  这一镜覆盖了哪几个回合；此时 `anchor_source` 必须覆盖整个镜头跨度，即"跨回合句必须由连续画面承载"。
  被声明过的跨回合镜头会在报告里留一条 `unverified`（机器只认区间覆盖，不认画面里到底是哪几回合）。

`evidence` 仍然是必填的**人写来源说明**（源帧时间码等），但要说清楚：**它是声明，不是证据**。
审计报告里有 `human_annotations_not_machine_verified` 专门列出"机器核对不了、只能靠人填"的字段，
不要把"填了"当成"证过了"。

### 4.3 阶段：跨阶段必须显式声明，全局顺序不是时间轴

`PHASE_ORDER`（`setup < battle < reward < map < shop < rest < event < other`）只是**枚举顺序**，
不是时间轴：循环类游戏里 map → battle → reward → map 会反复出现，把 phase 排成一条全局序列
并不成立。因此规则是：

- `claim_phase == event_phase` → `claim_mode: live`；
- **跨阶段**（`claim_phase != event_phase`）→ 必须显式写 `claim_mode: retrospective`，
  并让 `anchor_event` 指到具体回合。没写就是失败——不允许靠"顺序早于"默认放行。
  报告会把这类行记进 `phase_crossings` 与 `unverified`（机器不核对这段回顾是否属实）。
- 唯一保留的顺序判定是**同一事件内部**的"提前讲结果"：
  `len(event_id)==1`、`anchor_event ⊆ event_id`、且 `claim_phase` 在枚举上晚于 `event_phase`
  → 失败。这就是原始缺陷「战斗还没打完就在说打完了、我拿了 X」。
  修法是把台词移到真实发生的那一段，或让这一镜真的覆盖那个事件（`event_id=a+b`）。

### 4.4 保持帧：连续动作优先，无标注的长静帧不许用来填配音

- 一段长旁白**优先配连续真实动作**，不要"1.2 秒动作 + 25 秒不动"。
- `speed/freeze` 里登记了冻结（`freeze > 0`），就必须有 `hold_mark`，且标注里要写出
  **被保持的是哪一帧的源时间码**。没有标注的长静帧 = 失败。理由：观众分不清
  "这是有意定格"还是"渲染卡住了"，而复盘时也分不清它是有意为之还是漏剪。
- 保持帧锚点还要落在**旁白引用区间**（`anchor_source`）之内：定格停在你说到的那一段之外，
  等于说的和看的是两回事。
- **奖励保持帧**（`event_phase` 或 `claim_phase` 为 `reward`）还必须写明
  `visible_window`——候选/结论**真正可见**的源区间——并且保持帧锚点必须落在该区间**之内**。
  典型缺陷：定格锚在窗口末端，而那时画面已经翻到地图页，于是"说选牌时并没有在选牌"。

### 4.5 长静默必须逐段给依据（按**实际声段**，不是画面窗口）

旁白的占位是**声段**，不是它所在的画面窗口：

```
声段 = [final_range 起点, final_range 起点 + 实测 audio_duration_s]
```

一段 60 秒的画面只配了 1 秒旁白，就只有 1 秒有声，剩下 59 秒是静默；把画面窗口当成有声区间
会把"整段没解说"算成"整段都在讲"。声段之间取并集后，才从**实际采用的时间线**算出所有
≥ 阈值（默认 20s）的无口播区间。**旁白占比同样只按声段计算**（`narration_audio_s ÷ 成片总长`），
画面长度不参与。

> 记账口径：时间线不带 offset 列，offset 记账在 EDL 里（`build_sample.sh` 默认量级 ~0.2s，
> 小于台账匹配容差 ±1s）。所以声段按"从段首开始"计；如果你的渲染用了更大的 offset，
> 静默边界会随之平移，需要以实际音轨为准。

逐段填 `templates/silence-ledger.md`：

| disposition | 含义 | 审计怎么判 |
|---|---|---|
| `keep` | 保留：画面自解释（关键动作/结算/地图） | 依据必须写实；空/占位符失败 |
| `cut` | 本源静默已在剪辑中删掉 | 若该静默**仍被检出** → 自相矛盾，失败 |
| `narration_added` | 已补讲，但仍留有 ≥ 阈值的静默 | 依据必须写实 |

- 所有区间/时长/容差都拒绝 **NaN、inf、负数与倒序**：`anchor_source`、`visible_window`、
  台账 `final_range`、`--tol`、`--silence-threshold`、`--min-fps` 都按这条口径校验，非法直接拒绝，
  不用它掩盖硬失败。
- 成片里有 ≥ 阈值的静默而台账**缺行** → 失败。用**画面窗口**算出来的台账（静默起点偏晚）
  会直接对不上，这是有意的。
- 台账里有**对不上的行**（成片里已无此静默）→ 按**陈旧台账**失败：静默变了就必须从当前
  时间线重新生成，**不许沿用旧表**，也不许靠"清空缓存重跑一遍"糊过去。

**旁白占比不是通过标准。** 占比高不等于内容好，解码无报错也不等于画面对。这两条由 `audit`
原样写在报告的 `not_a_verdict_on` 里，不得被当成验收项。

---

## 5. 解说与字幕

- **开场简短自我介绍**（一两句，不铺陈）。
- **口播自然、有停顿**，不要赶成一条连读。
- **逐事件生成配音**，每段生成后**测实长**再据此细剪——不要先剪好再硬塞配音。
- **字幕来自实际口播稿/音频对齐**，不是另写一份扩写文案；说完即消失，不留长驻字幕；
  字幕文字 = 实际说出的文字（同音误听按实际音频改正）。
- **字幕与音轨都从最终采用的那一版时间线生成**，并各自留 sha256；版本绑定由 §4.1 的审计核对。
  字幕不是"最后另配一份"，音轨也不是"用上一版稿渲染的"。

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

### 6.1 机器门槛（结构与语义分开，不许互相顶替）

```bash
C=skills/gameplay-postproduction/scripts/check_postproduction.py
A=skills/gameplay-postproduction/scripts/check_timeline_audit.py

python3 "$C" sheet  review-sheet.md                    # 结构
python3 "$A" audit  timeline.tsv --preflight preflight.json --silence-ledger gaps.tsv \
    --subtitle subs.srt --audio voice_master.wav --final-mp4 final.mp4 \
    --report-out audit-report.json
python3 "$C" ready  review-sheet.md --final-mp4 /abs/final.mp4 \
    --timeline timeline.tsv --preflight preflight.json \
    --silence-ledger gaps.tsv --subtitle subs.srt --audio voice_master.wav
```

`ready` **强制要求**后面那一整套审计输入：缺任一项就不是"已审片"。两份语义说清楚：

- `sheet` 通过只说明**单子结构合法**（空白模板也会过）；
- `audit` 通过只说明**时间线自洽**（阶段/保持帧/静默/版本），它**不看画面、不听音轨**；
- `ready` 通过才说明"这份单子可以当作已审片交付"，仍然**不等于内容好**。

检查：`scripts/check_postproduction.py sheet <file>`　语义审计：`scripts/check_timeline_audit.py audit`

---

## 7. 修复

- **尽量局部**：按问题单里的源/成片区间只改该段，**不重做整场**。
- 每条问题要能**唯一定位到单元**（`event_id`）与**素材**（`asset_id`）。
- 修复后重跑受影响检查；**仍不能确认的事实如实挂起**（记 `unknown`），**不无限循环**。

### 7.0 制作路径与交付路径是同一条：绕过去就是 draft

`tools/voice/build_sample.sh` **默认就会调用**与 `ready` 同一个 checker、同一套规则的采用时间线审计：

- 同时给了 `TIMELINE` / `PREFLIGHT` / `SILENCE_LEDGER` → 导出后现场审计；不过 → 退出 3 并写出
  `DRAFT.txt`；通过 → 留下 `audit-report.json` 并清掉 DRAFT 标记；
- 没给 → 产物**一律标 `DRAFT.txt`（不可交付）**，并在 stderr 明说"未过采用时间线审计"。

也就是说：**没有经过审计的成片，不可能被误当成可交付物**。`ready` 那边同样不接收外部报告，
缺任一媒体参数（时间线/预检/静默台账/字幕/音轨/成片）都判未就绪。

### 7.1 增量重算与失效：按真实内容，不靠清空缓存，也不靠进程名

- **失效依据是内容，不是时间戳也不是"重跑一遍"。** TTS 侧已经是内容寻址：缓存键覆盖
  `text/model/voice/direction/optimize/fmt/strict_no_rewrite/端点指纹`，且**键命中还不够** ——
  磁盘上的音频必须仍是被测过的那一份（`duration_s` 有限正数 + sha256 + 字节数都相符），
  否则重合成。渲染侧同理：素材台账里的 `sha256`/`size_bytes` 与实际文件不符，就是
  **stale manifest**，必须按真实输入重算（`audit` 会直接判失败）。
- **只重算改变的那一段。** 改一句稿就只重做那一个节点，其余节点保持命中；
  "清空整个缓存"不是增量策略，只是把风险换成重跑。测试 `C6` 锁住这条行为。
- **完成判定只看产物，且要绑定到同一次制作。** 判"跑完了没有"要看输出本身——文件存在、
  sha256/段数/时长与计划相符，并且 receipt 证明它们出自同一次制作——
  **不要**用 `pgrep`/`ps` 匹配自己的进程名或命令行（那会匹配到包装脚本、别的任务，
  甚至匹配到自己），也不要只看日志里出现了某个关键词。这类判定在失败恢复时会直接骗人。
- **失败恢复要留现场**：半成品删除或标记，不写"成功"记录；不可测的结果（取不到时长、
  空音频）一律算失败而不是缓存成功。

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

# 4) 产物校验（结构）
python3 "$S/scripts/check_postproduction.py" timeline timeline.tsv
python3 "$S/scripts/check_postproduction.py" units    units.tsv
python3 "$S/scripts/check_postproduction.py" sheet    review-sheet.md

# 4b) 输入预检 + 采用时间线语义审计（确定性，不调用模型）
python3 "$S/scripts/check_timeline_audit.py" preflight --json --out preflight.json rec-both="$SRC"
python3 "$S/scripts/check_timeline_audit.py" audit timeline.tsv --preflight preflight.json \
    --silence-ledger gaps.tsv --subtitle subs.srt --audio voice_master.wav \
    --final-mp4 final.mp4 --report-out audit-report.json

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
>
> **本仓库的离线测试套件是"检查器套件"，不是完整渲染引擎。** 端到端跑满的只有一条短合成路径
> （`tools/voice/build_sample.sh` + lavfi 生成的几秒素材）。真实录屏的剪辑/合成/导出由你选的
> 外部工具完成；本套件保证的是"产物与时间线、预检台账、receipt 自洽"，不是"渲染结果好看"。
>
> **哪些是机器判的，哪些不是**：结构（`timeline`/`units`/`sheet`）、输入预检（`preflight`）、
> 时间线语义（`audit`，见 §4.1–4.5）与就绪门槛（`ready`）都是确定性代码，可离线复现。
> **听感、抑扬、断句是否自然，以及事实语义（选牌/伤害/胜负/身份），机器一律不裁定**，
> 只能靠真人听看 + 回源帧；`audit` 报告的 `not_a_verdict_on` 与 `unverified` 会把这条边界写出来。
