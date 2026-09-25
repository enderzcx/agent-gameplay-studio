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
| 交接凭证 | 本 skill 的 `scripts/run_manifest.py` |

## Boundary

**Own：** 阶段顺序与交界处的**门禁**、统一 run_id 的交接凭证、
三个结论（录像完整性 / 游戏结果 / 后期 ready）的**分开表达**、
异常时"只重试相应阶段"的纪律。

**Not own：** 采集实现（→ window-recording）、剪辑合成（→ gameplay-postproduction）、
**对局评分**（→ GameBench，与本流程完全隔离）。

## 铁律

### 1. 录制**有效开始**之后才允许开局

`run_manifest.py set-game` 会检查 `capture=pass`。不是 pass 就**拒绝**记录对局结果。

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

### 3. 异常只重试**相应阶段**，不重开新局

每个阶段有独立的 `attempts`。重跑 verify 只会让 `capture.attempts+1`，
**不会**新建 run_id、**不会**重开一局。run_id 一换，"这一局"就没了。

### 4. 后期**不能**反向影响对局

`set-post` 只写 postproduction 段，**永远不碰** game 段。
审片发现的问题只能改成片，不能改"当时对局发生了什么"。
这是评审公平性的底线：**后期不得反向提示参赛玩家**。

### 5. 不能把字符串状态机 / mock 渲染算成全流程

"游玩"这一阶段必须**真的用游戏工具玩**。以下**都不算**走完流程：

- 手写一个字符串状态机模拟对局过程
- 用 mock / 事后渲染的动画冒充真实窗口采集
- 拿旧素材当成这一局录的

`set-game` 只记录结论，**无法**验证你真的玩了 —— 所以这条靠执行者守，
而不是靠工具拦。工具能拦的是"没录上就记结果"（规则 1）。

## 流程

```bash
RUN=demo-01
JOB=./run
MAN=$JOB/$RUN.manifest.json

# 0) 建交接凭证
python3 scripts/run_manifest.py init --manifest "$MAN" --run-id "$RUN" --game "Chess"

# 1) 录制前：先看有什么可录，再预检（不要跳）
python3 <agent-capture>/scripts/agent_capture.py targets
python3 <agent-capture>/scripts/agent_capture.py preflight \
  --video-app com.apple.Chess --audio-app com.apple.Chess

# 2) 开始录（后台 worker；等"有效开始"再开局）
python3 <agent-capture>/scripts/agent_capture.py start \
  --video-app com.apple.Chess --audio-app com.apple.Chess \
  --job-dir "$JOB" --run-id "$RUN" --out "$JOB/$RUN.mp4" --duration 180

#   轮询直到 status=running（= 有效开始），**不要**在 starting 时就开局
python3 <agent-capture>/scripts/agent_capture.py status --job-dir "$JOB" --run-id "$RUN"

# 3) 实际游玩（用已有游戏工具真玩；不抢前台也可以，采集是后台的）

# 4) 停止 + 校验
python3 <agent-capture>/scripts/agent_capture.py stop   --job-dir "$JOB" --run-id "$RUN"
python3 <agent-capture>/scripts/agent_capture.py verify --job-dir "$JOB" --run-id "$RUN"

# 5) 把采集结论写进凭证（capture=pass 之后才允许写对局）
python3 scripts/run_manifest.py set-capture --manifest "$MAN" --result pass \
  --media "$JOB/$RUN.mp4" --metrics "$JOB/$RUN.metrics.json"
python3 scripts/run_manifest.py set-game --manifest "$MAN" --result pass --note "有界示范"

# 6) 交接给 gameplay-postproduction（它 own 后期规范）
#    ... 出成片 ...
python3 scripts/run_manifest.py set-post --manifest "$MAN" --result pass \
  --cut "$JOB/$RUN-cut.mp4"

# 7) 自检：run_id 一致性 / 门禁有没有被绕过
python3 scripts/run_manifest.py verify --manifest "$MAN"
```

## 交接给 gameplay-postproduction 时带什么

- **原片**路径 + `run_id`（成片文件名必须含同一个 run_id）
- 采集指标（`*.metrics.json`）与焦点日志（`*.focus.jsonl`）
- **`capture_integrity` 的结论**：后期要知道这段素材可不可信

后期**不回传**任何东西影响对局结论。它只产出成片与审片问题单。

## 输出契约

跑完应给出：

1. `run_manifest.py verify` 的 `ok` 与 `problems`
2. 三个结论**分开**列出（含 `unknown` 的那些，不要藏）
3. 成片路径（文件名含 run_id）
4. **明确说清没验证什么**：音画同步、画面内容质量、
   "这段声音确实是游戏发出的"、以及任何 `verified_level != recorded` 的后端

## 与 GameBench 的关系

**没有关系。** GameBench 保持评测职责；本流程的录制与后期**不并入评分逻辑**，
评分逻辑也**不因本流程而改变**。本 skill 不读、不写、不参与任何评分。
