# 默认执行路径：一句话 → 成片

这份文件是**可执行手册**，不是又一份规范。规范和字段定义在
`references/postproduction-standard.md`（canonical）；这里只回答一件事：

> 用户说"用 gameplay-postproduction 处理这段录屏，做完解析解说、配音、剪辑、字幕、审听审片、
> 局部修正并交付"之后，**我按什么顺序做什么，在哪里会失败，失败了怎么如实说**。

---

## 0. 先认清这是什么（能力边界，别越界承诺）

- 这是**由 Agent 执行的 skill + 工具集**：规范、模板、确定性检查器、一个短合成组装器、
  以及"该调用哪类外部能力"的路由。**它不是常驻服务，也不是确定性的"一键导演"**。
- **同一句提示词不等于同一份成片**。Agent 每次要重新判断取舍；源素材、依赖、授权任何一项不满足，
  结果就会不同。任何人说"给任意素材就能自动出精品"，都是假的。
- **缺什么就报什么，不要降级蒙过去**：没有源文件 / 没有音轨 / 没有 TTS 通道 / 没有视频理解通道 /
  没有剪辑依赖 → 在工作流里标 `blocked` 或 `degraded`，把**具体缺什么、缺到什么程度**说出来。
  不许用一个近似的假产物顶替（例如拿静音轨冒充游戏原声、拿候选事件直接写旁白）。
- **它推进到成片，不推进到"计划"**：一句话要求端到端，就不允许停在"先给你一版方案/先做个小样，
  等你批准"。只有在**真的需要用户决策**时才停：改稿方向、隐私/公开边界、付费额度、无法从素材判断的事实。

### 1 个叙事单元 ≠ 1 个 TTS 组 ≠ 1 条字幕 cue ≠ 1 个镜头

这四层长度不同，**任何时候都不许互相套用**：

| 层 | 是什么 | 数量关系 |
|---|---|---|
| 叙事单元（event） | 一次取舍：什么情况 → 有哪些选择 → 选了哪个 → 为什么 → 结果 | 一段 3 分钟成片通常只有 3–8 个 |
| TTS 组 | 一次合成的语义段（口播一口气说完的一段） | 一个单元常拆成 1–3 组 |
| 字幕 cue | 观众**一眼读完**的一句 | 一组常拆成 2–5 条 |
| 镜头/片段（EDL 行） | 一段真实源画面 | 一个单元常跨 2–6 段 |

配音按**组**合成（省钱、语气连贯），字幕按**短语**分页（`tools/voice/subtitles.py` 做），
画面按**段**裁切。把其中任何两层当成同一层，就会出"字幕压 HUD""配音比画面长 8 秒"这类事故。

---

## 1. 一句话怎么进

用户可以只说一句，例如：

```
使用 gameplay-postproduction，处理 <录屏或项目目录>，做完解析解说、配音、剪辑、字幕、
审听审片、局部修正并交付。
```

Agent 收到后**自己决定**下面全部内容，不要回头问可以自己查的东西（素材规格、时长、已有产物）：

1. 源是什么、多长、有没有音轨、采集是否稀疏 → `check_timeline_audit.py preflight`
2. 讲哪个故事、取哪几个叙事单元 → 视频理解通道（见 §3）+ **回源帧核验**
3. 口播怎么写、配音怎么合成 → §4
4. 画面怎么切、哪些等待要剪、哪些定格要登记 → §5
5. 字幕怎么分页、怎么烧 → §6
6. 原声要不要留 → §7
7. 审听（音轨）与审片（成片）→ §8
8. 验收怎么说才算诚实 → §9

**只有这四件事必须回来问用户**：改稿方向（说什么、什么口吻）、是否公开发布或上传媒体、
是否消耗付费额度、以及素材里**无法判断的事实**（例如"这张牌你当时为什么选"）。

---

## 2. 开工前 60 秒：先证明这件事做得成

```bash
S=<skill 安装路径>          # 例如 ~/.agents/skills/gameplay-postproduction
A="$S/scripts/check_timeline_audit.py"
C="$S/scripts/check_postproduction.py"

# ① 源台账：音轨存在性 / 采集密度 / 摘要 / 时长（真 ffprobe，不出网）
python3 "$A" preflight --json --out preflight.json rec-win=/abs/rec-win.mp4 rec-both=/abs/rec-both.mp4

# ② 依赖体检（没有就现在说，不要做完 90% 才说）
command -v ffmpeg  && ffmpeg -hide_banner -filters | grep -E ' (ass|drawtext) '   # 烧字幕/定格标注
python3 -c "import sys;print(sys.version)"                                        # 3.9+
```

**这些情况直接停并如实报，不要"先做一版看看"**：

| 情况 | 报什么 |
|---|---|
| 源文件不存在 / 读不动 | `blocked: 源缺失`，附尝试过的绝对路径 |
| `preflight` 出 `undetermined` | **不是通过**；说清是哪个素材、哪一项探测不出来 |
| 有声轨但 `audio_signal: silent` | 不许静默放过。查明是游戏自身静音、抓错目标还是权限问题（见 `<game>-checklist.md`） |
| `frame_sampling: sparse` | 时间正确 ≠ 画质恢复；限制写进审片单，不许插帧假装流畅 |
| 没有视频理解通道 | `degraded`：可以只用 ffprobe/ffmpeg 做结构级工作，但**旁白不许凭猜** |
| 没有 TTS 通道 | `blocked: 配音`。可以先出稿与时间线，但别假装有配音 |
| 没有 libass | `blocked: 烧字幕`。可以出 SRT/ASS 文件，但**不许说"字幕已交付进成片"** |

---

## 3. 多模态路线（作者本机已实测通过的那条）

原则：**模型给的是候选，剪点与事实必须回源核验**。模型输出的时间码只作粗定位。

| 用途 | 走什么 | 为什么 |
|---|---|---|
| 读**整段结构**（哪几段值得讲） | 默认通道的**快速视频模型**读全片或大段 | 便宜、够用来圈候选区间 |
| 读**细节**（牌名/数值/意图/UI 文字） | 先切**高清短片**或抽**裁图**再问 | 直接问全片会读错牌名与数值 |
| **审听**（配音念对没有、断句难不难受） | **独立无字幕音轨**，画面给纯色或黑 | 带画面的"审听"会把"看来的"当"听来的"（实测发生过） |
| **审片**（字幕/遮挡/节奏） | **最终实际导出的 MP4** | 审中间产物等于没审交付物 |

本机实测的调用形状（`gemini-companion` 0.1.3 CLI；换成你自己的通道同样成立）：

```bash
GCB=<你的视频理解通道 CLI>          # 作者本机：gemini-companion 的 gcb.py
JOBS=<项目目录>/.multimodal-jobs

# ① 结构：给区间，别给整片
python3 "$GCB" video --path /abs/rec.mp4 --start 150 --end 200 --kind game \
  --question "只描述发生顺序与画面阶段，不要读牌名与数值" \
  --backend beefapi --beefapi-model gemini-3.8-flash --jobs-dir "$JOBS" --json

# ② 细节：先切高清短片再问（模型对全片的牌面读数不可靠）
ffmpeg -nostdin -v error -y -ss 318 -to 322 -i /abs/rec.mp4 \
  -vf "crop=660:360:110:300,scale=1320:-2" -an -c:v libx264 -crf 16 /tmp/detail.mp4
python3 "$GCB" video --path /tmp/detail.mp4 --kind game \
  --question "读出这三张候选牌的名字与效果文字" --backend beefapi --json

# ③ 审听：只给音轨，画面不要给内容
ffmpeg -nostdin -v error -y -i final.mp4 -vn -c:a pcm_s16le voice.wav
python3 "$GCB" ask --task-text "听这段音频，逐句转写，并标出念错/重复/断句别扭的地方" \
  --backend beefapi --json
```

**后备通道怎么写才算诚实**：

- **显式后备**（人指定换通道）：可以说"这一次用 X 通道出的结果"。
- **自动后备**：只有当它**已经实现并实测**（在真触发条件下跑过）才允许说"自动后备"。
  作者的 0.1.x 曾把 auth 失败误判成 timeout 而触发后备 —— 所以**任何"自动降级"的声明都必须附上
  触发条件的实测证据**，否则写成"未验证"。
- **auth/权限/内容拒绝**：不换渠道绕、不静默关代理、不换账号。如实报"这个通道现在不可用，
  原因是什么"，然后**问用户**要不要换通道。
- **模型时点越界或读错**：标"不可靠，已回源核对，结论以源帧为准"，不要把模型结论当事实写进成片。

**大原片请求失败（例如 405）时**：用**独立压缩分析副本**或有界短分段，并记录
**实际 hash 与源↔副本时间映射**；**不改母带**，也不声称"一定小于某个固定大小就能过"。

---

## 4. 口播与配音

1. **先讲取舍，再讲操作**。一个叙事单元的五要素（情况 / 候选 / 选择 / 理由 / 结果）是**内部准备结构，
   不是口播稿**。稿子要说人话，别把五要素念出来。
2. `stated_reason`（当时公开说的）、`retrospective_commentary`（事后复盘）、`outcome` **三列分列，
   禁止互相回填**。事后复盘可以说"现在看应该……"，但不能冒充当时的想法。
3. **必要的时候先做临时配音试节奏**：一句稿配完发现比画面长 2 秒，与其改稿三次，不如先合成一版
   量长度。**试完要重合成**（改了字就必须重合成 —— 内容寻址缓存会因此 miss，这是特性不是 bug）。
4. 稿子与画面的**阶段锚点**必须对得上：不能在战斗开头说"我打完了"（`audit` 会硬失败）。
5. 配音适配器（`tools/voice/tts_adapter.py`）**不内置任何模型与额度**，也不假装有默认声线：
   **声线是听感决定，不是配置默认**。没有 TTS 通道就报 `blocked`。

---

## 5. 画面：动作结算原速，剪掉无信息等待

- **源区间 1x 取证，不改动作速度**。动作结算被人为加速，观众就看不出"这一下打了多少"。
- **剪掉的是没有信息的等待**（翻牌动画、地图加载、发呆），**不是动作本身**。
- **定格（freeze）是剪辑手段**：长旁白需要时间时，显式登记 `freeze` 并写进时间线，
  还必须有源时间码标注（`audit` 会查，奖励段的保持帧还要落在候选可见区间内）。
  不许用无标注的长静帧去填配音。
- **可选**：关键状态放大（zoom 到敌人意图/候选牌面）、声音衔接、章节包装。
  **不要机械塞转场** —— 转场是节奏手段，不是装饰。
- 中间产物无损；每段窗口 = 源长 + 登记定格，**音视频必须等长**（不等 = 有段被 `-shortest` 截了）。

---

## 6. 字幕：短语分页 + libass + 真尺寸 ASS

**分工**：`tools/voice/subtitles.py` 只产出文本（SRT / ASS），`tools/voice/burn_subs.py` 用
ffmpeg 的 `ass=` 滤镜烧录。中间**没有临时 PNG、没有 Pillow 依赖**。

```bash
V=<repo 或 skill 安装路径>/tools/voice

# 组装（见 §7 的完整调用）会自动做这一步；单独重做字幕时：
python3 "$V/subtitles.py" build \
  --cues out/cues.tsv --srt out/subs.srt --ass out/subs.ass \
  --w 960 --h 966 --font "Hiragino Sans GB" --size 28 --margin-v 16 --margin-lr 100
```

必须遵守的三条（都是实测踩出来的）：

1. **PlayResX/Y 必须等于视频尺寸**。SRT→ASS 默认脚本空间 384×288，`subtitles=srt:force_style=`
   里的 FontSize/MarginV 会被按那个空间解释，在 966 高画面上放大约 3.35 倍 → 一条 cue 变 3 行并压到 HUD。
2. **一条口播 ≠ 一条 cue**。长句按短语分页；单条上限按画面宽度自动算（`--max-chars` 可覆盖）。
3. **ASS 时间戳走总厘秒进位**（`ts_ass`）：`int(round(t % 1 * 100))` 会把 0.999 写成 `.100`。

**烧完必须实测，不能只数 SRT 条数**：

```bash
python3 "$V/check_burned_subs.py" \
  --video out/final.mp4 --baseline out/final-nosub.mp4 --subs out/subs.srt \
  --band 900:966 --protect 655:845 --margin-x 40 --json out/subs-check.json
```

它做逐像素差分（`--baseline` 是同一制作流程产出的**未烧字幕版**，所以"字"和"本来就白的东西"能分开），
逐条回答四个问题：**这条真的烧上了吗 / 有没有越出字幕带 / 有没有碰到左右边距（末字被裁）/
有没有压到保护带（手牌等 UI）**。

> 它**不证明**听感、字幕与语音的语义一致性、断句是否舒服。机器只说"这些像素在不在带里"。

---

## 7. 原声：有就分轨，没有就写明

```bash
# 默认：成片音轨 = 旁白（源原声不参与，因为很多录屏本来就是静音采集）
SRC_VIDEO=/abs/rec.mp4 bash tools/voice/build_sample.sh edl.tsv voice_dir out_dir

# 源片真的有原声、且要保留时：
SRC_VIDEO=/abs/rec-with-audio.mp4 KEEP_SRC_AUDIO=1 SRC_AUDIO_GAIN=-8 SRC_AUDIO_DUCK=1 \
  TIMELINE=recipe.tsv PREFLIGHT=preflight.json SILENCE_LEDGER=gaps.tsv BURN_SUBS=1 \
  bash tools/voice/build_sample.sh edl.tsv voice_dir out_dir
```

- `KEEP_SRC_AUDIO=1` 时，脚本按**同一 EDL** 把源音轨切出来、夹到与画面等长，再与旁白**分轨混音**；
  `SRC_AUDIO_DUCK=1` 用旁白当 sidechain，说话时把原声压下去（`src_audio.wav` 与 `voice_master.wav`
  都保留，终混不覆盖母版）。
- **源片没有音轨时，`KEEP_SRC_AUDIO=1` 直接失败**（退出 3 + `DRAFT.txt`），不会给你一条静音轨冒充原声。
- 源片**确实无声**时：如实写"源无音轨，成片音轨来自旁白"，**不要**编造游戏原声、不要拿别的素材凑。

---

## 8. 审听与审片：两件事，两个对象

| | 对象 | 问题 | 谁做 |
|---|---|---|---|
| **审听** | **独立无字幕音轨** | 念对了没？重复了吗？断句别扭吗？响度对吗？ | 模型可做转写与差异；**听感只有人能做** |
| **审片** | **最终实际导出的 MP4** | 字幕在不在、挡不挡、节奏拖不拖、事实对不对 | 先机器抽检（§6），再模型复核，再回源帧核事实 |

- 审听那次如果**同时给了画面**，结论要降级为"内容念对了"，**不能**当听感通过（实测发生过：
  画面全黑，模型却报出"看到"的牌与血量）。
- 审片的每一条问题都要写进问题单：`issue_id / event_id / asset_id / 源区间 / 成片区间 /
  旁白主张 / 实际画面 / 证据 / 局部修复建议`。
- **未知填 `unknown`，不填 `0`**；**未验证不得当作通过**。

---

## 9. 验收怎么说才算诚实

```bash
# 采用时间线语义审计（阶段锚点 / 保持帧 / 静默依据 / 内容级版本绑定 + receipt）
python3 "$A" audit out/adopted-timeline.tsv --preflight preflight.json \
  --silence-ledger gaps.tsv --subtitle out/subs.srt --audio out/voice_master.wav \
  --final-mp4 out/final.mp4 --receipt out/produce-receipt.json --edl edl.tsv \
  --report-out out/audit-report.json

# 就绪门槛（单子 + 实际导出成片 + 上面那一整套输入，缺一即未就绪）
python3 "$C" ready review-sheet.md --final-mp4 out/final.mp4 \
  --timeline out/adopted-timeline.tsv --preflight preflight.json \
  --silence-ledger gaps.tsv --subtitle out/subs.srt --audio out/voice_master.wav \
  --receipt out/produce-receipt.json
```

- `structure` 通过 ≠ 审片通过（空白模板也会通过）；`audit` / `ready` 通过 ≠ 内容通过。
- `ready`/`audit` **不看画面、不听音轨、不判听感、不判事实语义**。这些必须另外说，而且是"未验证"。
- 交付说明里必须能一眼分出：**已验证 / 未验证 / 机器不证明什么**。做不到就别写"已完成"。

---

## 10. 常见失败与对应报错（照抄口径）

| 现象 | 真实原因 | 正确说法 |
|---|---|---|
| 旁白放不进窗口 | 稿太长或 offset 太大 | 脚本在**制作前**退出 3；修法：改短稿 / 延长源区间 / 显式定格 |
| 音视频不等长 | 某段超长把后面推迟后被 `-shortest` 截断 | 拒绝导出，不是"轻微偏差" |
| 字幕只烧进第 1 条 | 旧 PNG overlay 链在长片上失效 | 改用 libass；本仓库不再提供 PNG 后备 |
| 字幕渲染成 3 行压 HUD | 用了 `force_style` 或 PlayRes 不对 | 写显式 PlayRes 的 ASS（§6） |
| 字幕末字被裁 | 单条太长 / 边距太窄 | 分页 + `check_burned_subs.py --margin-x` 实测 |
| 模型读错牌名/数值 | 全片分辨率下模型读数不可靠 | 标"不可靠"，切高清短片或裁图重问，回源帧定论 |
| "审听"报出画面细节 | 给了画面，模型把看来的当听来的 | 该次结论降级；审听只给音轨 |
| 自动后备被触发 | 可能是把 auth 失败误判成超时 | 附实测证据；否则写"未验证" |
| 大文件请求被拒（405 等） | 端点/网关限制 | 用压缩分析副本或有界短分段，记录 hash 与时间映射，不改母带 |

---

## 11. 什么时候**不要**用这份 runbook

- 只要纯剪切/合成/导出（不涉及流程与审片）→ 直接用你的剪辑工具。
- 只要"第几秒发生了什么"或逐字稿 → 用你的定位/转写通道。
- 只要调用模型看视频 → 用你的视频理解通道。
- 游戏参赛评测、对局决策质量 → **与后期完全隔离**，本流程不参与。

上位规则优先：用户/系统规则、以及你所选工具自身的命令语法与权限模型**始终优先**于本手册。
