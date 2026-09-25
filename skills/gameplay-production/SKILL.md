---
name: gameplay-production
description: "把『录一局游戏并做成成片』这件事**从头到尾**串起来：开局前先确认录制真的有效开始，实际游玩，停止并校验录像完整性，再把素材交接给 gameplay-postproduction 出成片。Use when 用户要『录一局游戏并出一支片子』『边玩边录、录完做成视频』，或需要一条从录制前到成片的可核对流程时。Not for 单纯的后期/剪辑（用 gameplay-postproduction）、单纯录屏不留片子（用 window-recording）、以及游戏对局质量评测与评分（与 GameBench 完全隔离，本 skill 不改评分、不参与评分逻辑）。"
license: MIT
compatibility: 需要 Python 3.9+（纯标准库）。录制依赖 window-recording（agent-capture）与其平台后端；后期依赖 gameplay-postproduction。两者缺失时本 skill 会**如实报错并停下**，不会假装走完。
metadata:
  short-description: 录制→对局→校验→后期交接的薄总控
  sunny_skill_type: contract
---

# Gameplay Production

**这是薄总控，不是新框架。** 它只做一件别处都没做的事：
把"录制""游玩""后期"三个阶段**用同一个 run_id 串起来**，
并在每个交界处**挡住不该继续的情况**。

真正的能力在别处：

| 阶段 | 谁负责 |
|---|---|
| 录制（窗口/应用、音画、完整性） | `window-recording`（agent-capture） |
| 游玩 | **主 Agent 用已有的游戏工具实际玩** |
| 后期（素材登记、解说、时间线、审片、成片） | `gameplay-postproduction` |
| 交接凭证 | **canonical 总控**：`<studio>/tools/production/production_run.py` |

## Boundary

**Own：** 阶段顺序与交界处的**门禁**、统一 run_id 的交接凭证、
三个结论（录像完整性 / 游戏结果 / 后期 ready）的**分开表达**、
异常时"只重试相应阶段"的纪律。

**Not own：** 采集实现（→ window-recording）、剪辑合成（→ gameplay-postproduction）、
**对局评分**（→ GameBench，与本流程完全隔离）。

## 铁律

### 1. 录制**有效开始**之后才允许开局

`production_run.py play-start` 需要一份**刚刚查的**采集状态报告：它要求
`status=recording`、`first_video_frame=true`、带 `capture_id`，且快照在**新鲜窗口**内
（过期的状态会被拒）。没通过 `record-ready` 就没有 `recording` 阶段，`play-start` 直接拒绝。

为什么值得单独设一道门：录屏"看起来在跑"和"真的录到了东西"是两件事。
进程活着但一帧都没收到是完全可能的。如果不设这道门，
"对局结果"会挂在一段**根本没有内容的录像**上，而且事后无法察觉。

"有效开始"的定义（由 window-recording 提供）：
`capture_initialized` **且** `first_video_frame` 都为真。

### 2. 三个结论互相独立，谁也不给谁背书

```
capture_integrity   录像本身完整吗
game_result         这一局玩出结果了吗
postproduction_ready 后期能开始/已完成吗
```

缺结论 = `unknown`，**不是** pass。`all_known` 为假时，
不许对外说"这一局完整跑通了"。

**特别地**：`audio_signal_observed=false`（整段没声音）**不是失败**。
游戏开局前本来就是静音的；把它当失败条件会**死锁等一个永远不来的信号**。
若本次用 `audio_mode=none`（源本来就没有音频），verify 的静音**不作为失败**——
判据只用实测：帧在持续到达、轨道已核实。

**后期结论同理**：`ready` 需要真正的审计输入跑过、且审听/审看都有真实证据；
拿不到就落 `needs_review`，**不允许**用"音量看起来正常"顶替独立听感。

### 3. 异常只重试**相应阶段**，不重开新局

`production_run.py` 用 `revision` 做 CAS：每次变更要带当前 `revision`，
不匹配就拒绝（"reread the run before retrying"）。
后期被判 `needs_review` 后，`edit-start` 允许在**同一个 run** 上继续修片，
**不**新建 run_id、**不**重玩一遍。run_id 一换，"这一局"就没了。

### 4. 后期**不能**反向影响对局

`deliver` 只写 postproduction 字段，**永远不碰** `game_result`。
审片发现的问题只能改成片，不能改"当时对局发生了什么"。
这是评审公平性的底线：**后期不得反向提示参赛玩家**。

### 5. 不能把字符串状态机 / mock 渲染算成全流程

"游玩"这一阶段必须**真的用游戏工具玩**。以下**都不算**走完流程：

- 手写一个字符串状态机模拟对局过程
- 用 mock / 事后渲染的动画冒充真实窗口采集
- 拿旧素材当成这一局录的

`game-finish` 只记录结论，**无法**验证你真的玩了 —— 所以这条靠执行者守，
而不是靠工具拦。工具能拦的是"没录上就记结果"（规则 1）。

## 流程

```bash
# 工具在 studio 仓库里（安装态则在快照下）：
#   <studio>/tools/production/production_run.py        ← canonical 总控（唯一）
#   <studio>/tools/production/postproduction_report.py ← 后期 report 生成器
#   <studio>/tools/production/state_io.py              ← 状态 IO（锁 + 原子写）
RUN=demo-01
JOB=./run
PR=$JOB/prod                 # journal 目录
PROD=<studio>/tools/production

# 0) 建 run
python3 $PROD/production_run.py --root "$PR" --run-id "$RUN" init \
  --game "Chess" --player "DSH agent" --mode content

# 1) 录制前：先看有什么可录，再预检（不要跳）
python3 <agent-capture>/scripts/agent_capture.py targets
python3 <agent-capture>/scripts/agent_capture.py preflight \
  --video-app com.apple.Chess --audio-app com.apple.Chess

# 2) 开始录
python3 <agent-capture>/scripts/agent_capture.py start \
  --video-app com.apple.Chess --audio-app com.apple.Chess \
  --job-dir "$JOB" --run-id "$RUN" --out "$JOB/$RUN.mp4" --duration 180

# 3) **先确认录制有效开始**，再把 run 推进到 recording
#    轮询到 status=running（= 采集初始化 + 有效首帧），不要用 starting
python3 <agent-capture>/scripts/agent_capture.py status --job-dir "$JOB" --run-id "$RUN"
python3 <agent-capture>/scripts/agent_capture.py report \
  --job-dir "$JOB" --run-id "$RUN" --production-run-id "$RUN" --out "$JOB/live1.json"
python3 $PROD/production_run.py --root "$PR" --run-id "$RUN" \
  record-ready --revision 0 --report "$JOB/live1.json"

# 4) **然后**才允许开局（play-start 会再要一份新鲜的采集状态）
python3 <agent-capture>/scripts/agent_capture.py report \
  --job-dir "$JOB" --run-id "$RUN" --production-run-id "$RUN" --out "$JOB/live2.json"
python3 $PROD/production_run.py --root "$PR" --run-id "$RUN" \
  play-start --revision 1 --report "$JOB/live2.json"

# 5) 实际游玩（用已有游戏工具真玩）

# 6) 对局结束：outcome 用真值（won/lost/limit/aborted/error）
#    报告必须由**真实 adapter 证据**生成，不能手填
python3 $PROD/production_run.py --root "$PR" --run-id "$RUN" \
  game-finish --revision 2 --report "$JOB/game.json" --outcome limit

# 7) 停止采集 + 校验，然后封印
python3 <agent-capture>/scripts/agent_capture.py stop   --job-dir "$JOB" --run-id "$RUN"
python3 <agent-capture>/scripts/agent_capture.py verify --job-dir "$JOB" --run-id "$RUN"
python3 <agent-capture>/scripts/agent_capture.py report \
  --job-dir "$JOB" --run-id "$RUN" --production-run-id "$RUN" --out "$JOB/final.json"
python3 $PROD/production_run.py --root "$PR" --run-id "$RUN" \
  capture-finish --revision 3 --report "$JOB/final.json"
#   → capture_result 只在 stopped + 容器可读 + 帧连续 + 非意外停止 时才是 complete

# 8) 后期（由 gameplay-postproduction 出片），然后进 editing
python3 $PROD/production_run.py --root "$PR" --run-id "$RUN" edit-start --revision 4

# 9) 生成后期 report —— **只读证据，不手填结论**
#    必给 --checker 指向 gameplay-postproduction 的 check_postproduction.py 及它的
#    真实 ready 输入；生成器会**自己跑**它并用真实退出码，同时核 final/source/review 摘要。
python3 $PROD/postproduction_report.py --run-id "$RUN" \
  --source "$JOB/$RUN.mp4" --final "$OUT/final.mp4" \
  --checker <gameplay-postproduction>/scripts/check_postproduction.py \
  --review-sheet ... --timeline ... --preflight ... --silence-ledger ... \
  --subtitle ... --audio ... --receipt ... \
  --audio-review-json "$OUT/audio-review.json" \
  --picture-review-json "$OUT/picture-review.json" \
  --out "$JOB/post.json"

# 10) 交付
python3 $PROD/production_run.py --root "$PR" --run-id "$RUN" \
  deliver --revision 5 --report "$JOB/post.json"
#   → delivered 只在 status=ready **且** capture_result=complete 时成立；
#     否则 needs_review。**不要**为了 closeout 手填 pass。

# 11) 交接 / 查状态
python3 $PROD/production_run.py --root "$PR" --run-id "$RUN" handoff
python3 $PROD/production_run.py --root "$PR" --run-id "$RUN" status
```

### 审听与审看必须**各自有真实输入**

`postproduction_report.py` 的审听/审看信封分两层：

| 字段 | 含义 |
|---|---|
| `subject_final.sha256` | 这次审的是**哪一版成片**（必须等于本次 final） |
| `input_media{path,sha256}` | **实际拿去审的那个文件**（音轨 wav / 成片 mp4），实算哈希并入 `review_evidence` |

**独立审听要真的听音轨**：音量统计或"带字幕的视频看着清晰"**不能**算独立听感。
拿不到有效听感结论就如实填 `unknown` —— 那会让最终状态落在 `needs_review`，
这正是正确的表达。

### 旧 `run_manifest.py` 已废弃

它已被 canonical 取代并**从仓库移除**。不要再调用它，也不存在
`set-capture --result pass` 这种可手填 `pass` 的入口。

## 交接给 gameplay-postproduction 时带什么

- **原片**路径 + `run_id`（成片文件名必须含同一个 run_id）
- 采集指标（`*.metrics.json`）与焦点日志（`*.focus.jsonl`）
- **`capture_integrity` 的结论**：后期要知道这段素材可不可信

后期**不回传**任何东西影响对局结论。它只产出成片与审片问题单。

## 输出契约

跑完应给出：

1. `production_run.py status` 的 stage 与三个结果字段
   （`capture_result` / `game_result` / `postproduction_result`）
2. 三个结论**分开**列出（含 `unknown` 的那些，不要藏）
3. 成片路径（文件名含 run_id）
4. **明确说清没验证什么**：音画同步、画面内容质量、
   "这段声音确实是游戏发出的"、以及任何 `verified_level != recorded` 的后端

## 与 GameBench 的关系

**没有关系。** GameBench 保持评测职责；本流程的录制与后期**不并入评分逻辑**，
评分逻辑也**不因本流程而改变**。本 skill 不读、不写、不参与任何评分。
