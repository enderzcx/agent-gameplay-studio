---
name: gameplay-postproduction
description: "把游戏录屏做成可审片成片的标准作业流程：素材登记、事件解析、解说准备、统一时间线、终片审片问题单与局部修复。Use when 用户要整理游戏录屏/录播、准备后期解说与分镜、核对成片事实、或做审片验收，以及为某款游戏固化可复用的录屏后期规范时。Not for 纯切片/合成/导出操作（交给宿主的剪辑能力）、逐帧或 MM:SS 定位与逐字稿（交给宿主的定位/转写能力）、视频理解调用本身（交给宿主的视频理解通道）、以及游戏参赛评测或对局决策质量（与后期完全隔离）。"
license: MIT
compatibility: 需要 Python 3.9+（跑 `scripts/check_postproduction.py`，纯标准库）与 ffprobe/ffmpeg（素材登记、切片段、`ready` 门槛探测）。**视频理解与剪辑不由本包提供**：它们由宿主提供的能力完成，见 `references/routing.md` —— 那里列的是可替换角色与可选适配器，换成你自己的实现同样成立。
metadata:
  short-description: 游戏录屏后期工作流（素材登记/事件解析/解说准备/时间线/审片）
  sunny_skill_type: contract
---

# Gameplay Postproduction

把"一段游戏录屏"变成"可交付、可审查的成片"。

本 skill **own 规范、模板、确定性与就绪门槛检查器，并负责推进端到端流程**；
**实际的视频理解、剪辑、合成与导出由宿主提供的能力完成**（它们是可替换角色，见下）。
换句话说：流程和验收标准在这里，执行通道由你提供。

通用流程与游戏特有清单分层：通用部分对任何游戏成立，**特定游戏只写进 `references/<game>-checklist.md`**，
没有该文件就表示**未适配**，不要假装已支持。

## 安装 / 装载

本包就是标准 Agent Skill 形态：`SKILL.md` + `references/` + `templates/` + `evals/` + `scripts/`。
把它放到宿主的 skill 目录即可被识别。**安装是失败即关闭的：不覆盖、不合并。**

```bash
# 在仓库根目录执行（就是刚 clone 下来的那个目录）
REPO="$(pwd)"
SKILL=gameplay-postproduction
# 把 SKILL_HOME 指向你宿主的 skill 目录（~/.agents/skills、~/.dsh/skills …）
SKILL_HOME="${SKILL_HOME:-$HOME/.agents/skills}"
DEST="$SKILL_HOME/$SKILL"

# 1) 先建父目录
mkdir -p "$SKILL_HOME"
# 2) 目标位置只要有东西就拒绝——目录、文件、软链都算。
#    没有这一步，`cp -R` 遇到已存在目录会变成嵌套/合并，而不是报错。
if [ -e "$DEST" ] || [ -L "$DEST" ]; then
  echo "refusing to overwrite existing: $DEST" >&2
else
  # 3) 复制安装
  cp -R "$REPO/skills/$SKILL" "$DEST"
fi

# 或者干脆不装，直接用仓库路径（多数宿主也接受）
python3 "$REPO/skills/$SKILL/scripts/check_postproduction.py" --help
```

宿主 skill 目录名因实现而异（例如 `~/.agents/skills`、`~/.dsh/skills`），按你的宿主约定设置 `SKILL_HOME`。
装载后可用下面的按需加载表把 references 只读需要的部分。
**本仓库不会改动你已有的 skill 安装，也不会改任何全局配置**；上面每一步都由你执行。

## Boundary

- **Owns**：素材登记与覆盖核验、事件解析与解说准备、统一时间线（源/片段/成片）、审片问题单与局部修复规范、
  端到端流程推进，以及"本流程是否被正确执行"的确定性检查与就绪门槛。
- **Delegates — 角色，不是硬依赖**：剪辑/合成/导出（*剪辑能力*）、逐帧与精确剪点定位、逐字稿（*定位/转写能力*）、
  视频理解调用本身（*视频理解通道*）。这些由**宿主提供**，本仓库不内置、也不附带任何模型或额度。
  作者本机当时用的是 `editor` / `watch` / `gemini-companion` 这类 skill，**它们只是可替换实现之一**；
  换成你自己的编辑器或模型通道同样成立。
- **Does not own**：游戏参赛评测与对局决策（**完全隔离，后期不参与参赛决策**）。
- **上位规则优先**：用户/系统规则、以及你所选工具的自身命令语法与权限模型**始终优先**于本 SOP。
  本 skill **不裁决**这些工具的参数或权限，也不替它们做语法决定。

## 按需加载

| 需要做的事 | 读 |
|---|---|
| 完整流程与规则 | `references/postproduction-standard.md`（canonical） |
| 具体游戏的特有清单 | `references/<game>-checklist.md`（现有：`spire-checklist.md`） |
| 与宿主视频分析/剪辑能力的边界与替换方式 | `references/routing.md` |
| **配音的声音层（角色/发音词典/控制手段真实效力/引擎参数边界/人工vs机器验收）** | `references/voice-design.md` |
| **录"游戏原声"（入口、前置设置、预检/停止后探测）** | `references/spire-checklist.md` §录游戏原声 |
| 产物形状 | `templates/asset-register.md`、`commentary-unit.md`、`timeline.md`、`silence-ledger.md`、`review-sheet.md` |
| 机器校验（结构/就绪） | `scripts/check_postproduction.py` |
| 输入预检 + 采用时间线语义审计 | `scripts/check_timeline_audit.py`（锚点交叉核对/阶段声明/保持帧/按实际声段的静默依据/内容级版本绑定 + 制作 receipt/stale 台账，规则见 canonical §1.1、§4.1–4.5） |

只在需要时读对应文件，不要把全部 references 一次加载。

## 触发

**用**：整理游戏录屏/录播；为录屏准备解说口播与分镜；核对成片事实（选牌/伤害/血量/胜负）；
建立或校验统一时间线；写审片问题单并做局部修复；把某款游戏的录屏后期流程固化成规范。

**完整任务由本 skill 推进到实际成片**：像"**把这局录屏加解说、剪成片并审查**"这种端到端要求，
本 skill 负责**推进流程并盯到成片**——素材登记 → 事件解析 → 解说与分镜 → `edit_plan` →
**交给宿主的剪辑能力执行** → **取回实际导出的成片做审查**（`ready` 门槛）。
**不要**因为一句话里出现"剪辑"就退回只给计划、把整件事丢回给用户。

**不用**：**纯**切片/合成/导出操作（只要求剪或导，不涉及流程与审片）→ 直接交给宿主的剪辑能力；
只要求"第几秒发生了什么"或逐字稿 → 宿主的定位/转写能力；只要求调用模型看视频 → 宿主的视频理解通道；
游戏比赛/评测/排名。

## 输入与缺失处理

输入：**录屏文件（必需）**、可选事件日志、可选风格要求、可选目标时长。

| 缺失 | 处理 |
|---|---|
| 没有事件日志 | 从**宿主的视频理解能力**得到**候选**事件，再回源帧核验；不得凭候选直接写旁白 |
| 源画面缺失但日志里有 | 记 `coverage_gap`，该单元**不进成片解说** |
| 音轨情况 | **先用 `check_timeline_audit.py preflight` 独立探测**，不要靠人填：`absent`（真的没有音轨）**可接受**（有意静音源），但**不得编造源音**，成片仍必须有音轨；`silent`（音轨在但无真实信号）**不得静默通过**，须查明是目标自身静音、抓错目标还是权限问题；`undetermined` 一律不得当作通过 |
| 采集稀疏（如约 1.5 fps） | 预检标 `frame_sampling: sparse`。**时间正确 ≠ 画质恢复**：不得声称画面动感充足，也不得插帧假装流畅，限制要写进审片单 |
| 依赖缺失（外部定位工具/网络/模型通道不可用） | 诚实标 `degraded`/`blocked`；能用 ffprobe/ffmpeg 本地完成的步骤继续做，**不换通道绕过真实权限拒绝** |

## 工作阶段

`ingest` → `analyze` → `commentary_plan` → `edit_plan` → `review` → `repair`

这些是**阶段名，不是 API**：按需执行，不要求每次都跑满，也不要求调用方实现它们。

## Output Contract

产物与**必需字段**：

1. **素材登记**：`asset_id`、`sha256`、`size_bytes`、ffprobe 规格（duration/fps/codec/分辨率/音频）、
   `has_audio`、是否双窗/含画面内答案。→ `templates/asset-register.md`
2. **事件/解说准备**：每单元 `event_id`、源区间、`state`/`action`、
   **`stated_reason`（当时公开说的；缺失写 `null`/`未记录`）**、
   **`retrospective_commentary`（事后复盘）**、`outcome`——**三者分列，禁止互相回填**；
   五要素（情况/候选/选择/理由/结果）是**内部准备结构，不是口播稿**。
   → `templates/commentary-unit.md`
3. **统一时间线**：视频/配音/字幕**共用一份**；每行
   `asset_id | event_id | source_range | clip_range | final_range | speed/freeze | narration_text | audio_duration_s | subtitle_source`；
   **算术必须自洽**：`final` 长度 = `clip` 长度（按 speed 换算）+ `freeze` 时长。
   → `templates/timeline.md`
4. **审片问题单**：必查项 + 问题清单（`issue_id/event_id/asset_id/源区间/成片区间/旁白主张/实际画面/证据/局部修复建议`）。
   终片审查的对象是**实际导出的 MP4**；**未知不得当通过**（无真值填 `unknown`，不填 `0`）。
   → `templates/review-sheet.md`
5. **门槛输入（`ready` 强制要求，缺一即未就绪，且不接受外部报告替代现场重跑）**：
   `preflight.json`（源台账，`preflight --out` 生成）、`gaps.tsv`（每段 ≥ 阈值的无口播逐段给
   `keep|cut|narration_added` + 依据，**按实际声段算** → `templates/silence-ledger.md`）、
   `produce-receipt.json`（**制作路径在产出那一刻写出**，把时间线与字幕/音轨/成片绑在同一次制作上）、
   以及 `audit-report.json`（锚点核对 / 阶段 / 保持帧 / 静默 / **内容级版本绑定 + receipt** /
   `unverified` / `human_annotations_not_machine_verified` / `not_a_verdict_on`）。
   时间线必须自带字幕/音轨/成片三份**内容摘要**；只数段数、只 hash 输入原片、或拿旧媒体再手写
   一份声明，都挡不住旧产物。

**证据**：每条事实性结论必须能指到**源帧时间码 / ffprobe 输出 / 文件**。
**停止条件**：修复**尽量局部**；仍无法确认的事实**如实挂起**（写进问题单的 `unknown`），**不无限循环**。

## Checker

默认用**仓库相对路径**（不依赖任何人的家目录）。若已按上面的「安装 / 装载」装载，把 `S` 换成你的安装路径即可：

```bash
# 在仓库根目录直接跑（推荐，最省事）
C=skills/gameplay-postproduction/scripts/check_postproduction.py
A=skills/gameplay-postproduction/scripts/check_timeline_audit.py

# ① 输入预检：源有没有音轨、音轨有没有真实信号、采集是否稀疏（真 ffprobe/ffmpeg，不出网）
python3 "$A" preflight --json --out preflight.json rec-win=/abs/win.mp4 rec-both=/abs/both.mp4

# ② 结构检查
python3 "$C" timeline "timeline.tsv"                 # 结构：TSV（TAB 或 | 自动识别）
python3 "$C" units    "units.tsv"                    # 结构：TSV（见 templates/commentary-unit.md）
python3 "$C" sheet    "review-sheet.md"              # 结构：Markdown（见 templates/review-sheet.md）

# ③ 采用时间线语义审计（阶段锚点 / 保持帧 / 静默依据 / 版本绑定 / stale 素材台账）
python3 "$A" audit "timeline.tsv" --preflight preflight.json \
    --silence-ledger gaps.tsv --subtitle subs.srt --audio voice_master.wav \
    --final-mp4 final.mp4 --receipt produce-receipt.json \
    --json --report-out audit-report.json

# ④ 就绪门槛：单子 + 实际导出的 MP4 + 上面那一整套审计输入（缺一即未就绪）
python3 "$C" ready "review-sheet.md" --final-mp4 "/abs/path/final.mp4" \
    --timeline timeline.tsv --preflight preflight.json \
    --silence-ledger gaps.tsv --subtitle subs.srt --audio voice_master.wav \
    --receipt produce-receipt.json

# 装载到宿主后：
# S="$HOME/.agents/skills/gameplay-postproduction"   # 换成你的实际安装路径
# python3 "$S/scripts/check_postproduction.py" timeline "timeline.tsv"
```

用法帮助：`python3 "$C" --help`。检查器**纯标准库**，不调用任何模型、不需要网络。

**两种语义必须分清（不要混用）**：

| 模式 | 输入格式 | 通过意味着 |
|---|---|---|
| `timeline` / `units` / `sheet` | TSV（TAB 或 `\|`）/ TSV / Markdown | **仅"结构合法"**：字段齐全、算术自洽、章节完整。**空白模板也会通过** —— 这不代表审片通过，也不代表成片可用。 |
| `preflight` | 源素材路径（真 ffprobe/ffmpeg） | **"输入状态已明确"**：音轨存在性（`present`/`silent`/`absent`）、采集密度（`normal`/`sparse`）、摘要与时长全部被独立探测；探测不出来就是 `undetermined` 并**非 0 退出**。 |
| `audit` | timeline TSV + preflight 台账 + 静默台账 + 字幕 + 音轨 + 成片 + 制作 receipt | **"采用时间线自洽"**：旁白声明的 (素材, 回合, 源区间) 与本行真实画面源区间交叉核对通过（跨回合句必须覆盖镜头跨度）、跨阶段句显式声明且同一事件不提前讲结果、`freeze>0` 都有带源时间码的标注（奖励保持帧还须落在候选可见区间内）、旁白放得进窗口、**按实际声段**算的每段长静默有依据、字幕/音轨/成片按**内容 hash + 字幕文本与时点**绑定且 receipt 证明同属一次制作、源台账不 stale。 |
| `ready` | Markdown + `--final-mp4` + 上面那一整套审计输入 | **"可交付就绪"**：A1–A5/E1–E9/G1–G3 全部**明确判定为「是」且有证据**（仅 G2 允许 N/A）、无未填占位符、无未解决 issue、H 段**明确且唯一地写「通过」**、末段媒体的**元数据/轨道**经独立 `ffprobe` 探测并与声明时长在容差内一致，**且采用时间线通过语义审计**。 |

**本 skill 的离线测试是"检查器套件"，不是完整渲染引擎**：端到端跑满的只有 `tools/voice/build_sample.sh`
那条短合成路径（几秒 lavfi 素材）。真实录屏的剪辑/合成/导出仍由你选的外部工具完成。

**`ready` / `audit` 不做什么**：它们**不观看画面、不听音轨、不检查帧内容、不验证音画同步**，
也不做画面质量与听感判定 —— 不替代人工审片。审计报告里有一项 `not_a_verdict_on` 明确写着：
**旁白占比、解码成功、听感、事实语义都不构成内容通过**。

- 只要表里还留着 `____` 之类的占位符、没给 `--final-mp4`、或「成片文件」与 `--final-mp4` 归一后不是同一文件，
  `ready` **一律不通过**（缺 `--final-mp4` 时状态是 `unverified`）。
- `unknown` 是合法值，但它意味着**该项未验证**；**未验证不得当作通过**。
- 结论写「局部修改」或仍留双选项 → **not-ready**；有未解决 issue → **not-ready**。
- `--tol` / 声明容差 / 声明时长必须是**有限非负数**；非法值直接拒绝，不用它掩盖硬失败。
- 退出码：`0`=该模式通过，`1`=有缺陷/未就绪，`2`=用法或读取错误。

## 故意不加

- 不内置剪辑/合成/导出实现：那是**宿主的剪辑能力**（作者本机用过 `editor`，只是可替换实现之一）。
- 不做逐帧/逐字稿：交给**宿主的定位/转写能力**。
- 不建视频理解适配器：交给**宿主的视频理解通道**（本包不含任何模型、端点或额度）。
- 不为其他游戏预置清单：**没适配就明确说没适配**。
- 不做发布流水线：**发布由使用者和宿主决定**；本 skill 只保证产物可审、并把门槛写死。
- 不替宿主工具做语法或权限裁决（见 Boundary）。

## Next Three Iterations

1. 第二个游戏的 `<game>-checklist.md` 出现后，再把通用标准里残留的游戏假设抽干净。
2. 把 `check_postproduction.py` 的 `sheet` 检查扩展到"必查项证据非空"。
3. 让 `audit` 能从 `--preflight` 台账直接核对"每个在 timeline 里用到的 asset_id 都真的登记过"，
   并把 `visible_window` 从人工填写升级为可选的帧级候选检测（仍需回源确认，不能只信模型）。
