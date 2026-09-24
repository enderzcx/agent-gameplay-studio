# Agent Gameplay Studio

**把一段游戏录屏，做成真正可审的成片。** 录下游戏自己的声音和画面，解析发生了什么，写决策解说，
配音，组装剪辑，然后在发布前拿成片对着成文标准逐项核对。

它是**一套 skill 驱动的工具集**：流程、模板和门槛都在 skill 里，几个小工具负责机械活。
你以"一个 skill + 几个脚本"的方式使用它，剪辑器和模型通道自带。背后不捆绑任何东西——
没有模型、没有 key、没有服务——需要判断的部分始终是你的，不容含糊的部分交给机器检查。

### 你会拿到什么

- **一套后期 SOP**（`skills/gameplay-postproduction/`）：ingest → analyze → commentary plan →
  edit plan → review → repair，含 canonical 标准、四份产物模板、一份已适配游戏的清单。
- **两个确定性检查器**：`check_postproduction.py`（结构 + `ready` 门槛）与
  `check_timeline_audit.py`（输入预检 + 采用时间线语义审计），都是纯标准库、零模型调用。
  前者做 timeline / units / 审片单的结构校验并对真实导出的 MP4 跑严格门槛；后者独立探测每个
  源素材的音轨与采样状态，并审计采用时间线的阶段锚点、保持帧标注与可见区间、逐段静默依据、
  字幕/音轨版本绑定与素材台账是否 stale。
- **一个 macOS 录制器**（`tools/gamerec/`）：按 app 过滤，只抓目标应用的声音**和**画面；从源码编译，
  不附带签名二进制。
- **配音与组装工具**（`tools/voice/`）：可选 TTS 适配器、放不下就拒绝导出的 EDL 组装器、
  以及给没有 libass 的 ffmpeg 用的字幕烧录器。
- **223 项离线测试**：不需要网络、不需要密钥、不需要游戏。

第一次来？先看[最短可复制 quick start](#最短可复制-quick-start)，再看
[实际使用 skill](#实际使用-skill)，然后读[哪些真的验证过](#哪些真的验证过哪些没有)再决定信什么。

边界先说一次：它**刻意不是** GUI 应用、**不是**一键出片、**也不声称**全自动真人级效果。
它是给"要把流程摆明、把结果做成可核对"的人用的工具。所有边界与未验证项都列在下面的状态表和
[诚实的边界](#诚实的边界)里，而不是埋在开头的免责声明里。

本项目与 `agent-gamebench`（Agent 评测项目）是两件事，见[非评测作弊与隔离](#非评测作弊与隔离)。

---

## 最短可复制 quick start

需要 **Python 3.9+**——这是验证过的，不是假设：整套离线测试跑在 Python **3.9.6** 上。
媒体步骤与 `ready` 模式需要 `ffmpeg`/`ffprobe`；**烧字幕**还需要 `ffmpeg` 带 **libass**（`ffmpeg -filters | grep ass`）。检查器本身是纯标准库。

```bash
git clone https://github.com/enderzcx/agent-gameplay-studio.git
cd agent-gameplay-studio

# 0) 先看能做什么，再跑全部离线检查。无网络、无密钥、不需要游戏。
make help
make check          # 5 套离线测试：检查器 + 时间线审计 + 字幕分页与时基 + 组装/烧字幕/原声 + TTS 硬保证（共 223 例）

# 1) 检查器可以直接用在你的产物上
C=skills/gameplay-postproduction/scripts/check_postproduction.py
A=skills/gameplay-postproduction/scripts/check_timeline_audit.py
python3 "$C" timeline examples/timeline.example.tsv      # 仅结构
python3 "$C" units    examples/units.example.tsv
python3 "$C" sheet    examples/review-sheet.example.md
# 退出码 0 = 结构合法。对 timeline/units/sheet 来说，这**不代表**成片是好的。

# 先预检源素材（真 ffprobe/ffmpeg）：音轨 present/silent/absent、采集是否 sparse，都写进台账
python3 "$A" preflight --json --out preflight.json rec-example-01=/abs/path/to/recording.mp4

# 2) 读标准，然后按你自己的录屏填模板
#    references/postproduction-standard.md 是 canonical
```

`ready` 门槛会额外独立探测真实导出的 MP4，**并且强制要求整套审计输入**
（时间线 / 预检台账 / 静默台账 / 字幕 / 音轨），所以语义审计在交付路径上不可跳过：

```bash
python3 "$A" audit timeline.tsv --preflight preflight.json --silence-ledger gaps.tsv \
    --subtitle subs.srt --audio voice_master.wav --final-mp4 final.mp4 \
    --receipt produce-receipt.json --report-out audit-report.json
python3 "$C" ready review-sheet.md --final-mp4 /abs/path/to/final.mp4 \
    --timeline timeline.tsv --preflight preflight.json \
    --silence-ledger gaps.tsv --subtitle subs.srt --audio voice_master.wav
```

`ready` = "这份单子可以当作已审片交付"：A/E 每一项都**明确判「是」且有证据**、没有未填占位符、
H 段结论**明确且唯一**为 `通过`、没有未解决 issue，且末段媒体的轨道/时长经独立探测与声明一致。
它**不观看画面、不检查帧内容、不验证音画同步**，也**永远不替代人工审片**。

---


## 实际使用 skill

skill 才是"这是工作流而不是一堆脚本"的地方。它是标准 Agent Skill 包
（`SKILL.md` + `references/` + `templates/` + `evals/` + `scripts/`），凡是支持这种形态的宿主都能装载。

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
```

这是本项目唯一推荐的安装路径，而且是**失败即关闭**：绝不覆盖、绝不合并。
宿主 skill 目录因实现而异（`~/.agents/skills`、`~/.dsh/skills` …），按你的宿主约定设置 `SKILL_HOME`。

如果你的宿主能直接从路径加载 skill，也可以完全不装：

```bash
REPO="$(pwd)"        # 在仓库根目录执行
python3 "$REPO/skills/gameplay-postproduction/scripts/check_postproduction.py" --help
```

**本仓库不会替你安装任何东西、不会覆盖已有 skill、也不会改任何全局配置。** 上面每一步都由你执行。

装载后，skill 推进流程并按需加载 references，而不是一次全读：

| 你要做什么 | 读 |
|---|---|
| 完整流程与规则 | `references/postproduction-standard.md`（canonical） |
| 与你的视频分析/剪辑能力的边界，以及怎么替换 | `references/routing.md` |
| 声音层：角色、发音、TTS 控制手段的真实效力 | `references/voice-design.md` |
| 录游戏原声 | `references/spire-checklist.md` §录游戏原声 |
| 要填的产物形状 | `templates/*.md` |

**视频理解与剪辑由你的宿主提供，不由本包提供**——那些角色都是可替换的，`references/routing.md`
里写明了这一点。本包不含任何模型、端点或额度。

---

## 关不掉的安全默认值

三个「失败即关闭」的行为写进了工具本体，并有常驻测试覆盖 —— 因为每一个都挡的是静默且昂贵的错误：

| 行为 | 为什么 |
|---|---|
| 录音器**没有默认目标**，且**绝不回退**去抓别的 app | 抓错 app 会得到一个看起来正常、实际没用的文件。缺目标属于用法错误（`64`），在**申请权限之前**就报掉。 |
| 产物**默认不覆盖**（`--out` / `--json` / `--log` / `--status`） | 重跑一次录制不该毁掉上一份证据。要覆盖必须显式 `--overwrite`，否则退出码 `3` 且一个字节都不动。 |
| 录制**有界停止**，不会挂死 | 启动 30s 上限 + 收尾截止时间，两者都以终态结束（`exit 4`），而不是无限等。 |

这三条都在**离线**状态下验证（不需要任何权限）—— 安全属性不该非要先拿到权限才能被测。

---

## 哪些真的验证过，哪些没有

信任何结论之前先看这张表。"已验证"= 在作者机器上真跑过且证据在本仓库里；"未验证"= 没人证明它成立。

| 范围 | 状态 | 依据 |
|---|---|---|
| **现成 GUI / 一键出片** | **不存在 —— 这是刻意的** | 这是 skill + 脚本。剪辑器你自己带。 |
| **捆绑的模型 / key / 端点 / 额度** | **没有** | 安装本仓库不会带来任何模型能力；不把 TTS 适配器指向你自己的端点，它什么也做不了。 |
| Python 3.9 兼容性 | **已验证** | 整套离线测试是在 Python **3.9.6**（`/usr/bin/python3`）上跑的，不只是新解释器。 |
| 后期产物（timeline / units / 审片单） | **已验证 · 离线** | `tests/test_check_postproduction.py`：32 例，无网络、无模型 |
| 确定性检查器（结构 + `ready` 门槛） | **已验证 · 离线** | 同一套测试；`ready` 用 `ffprobe` 独立探测媒体，且缺审计输入一律不通过 |
| 输入预检（音轨 present/silent/absent、采集 normal/sparse） | **已验证 · 离线** | `tests/test_timeline_audit.py`：对 lavfi 合成素材跑真 ffmpeg/ffprobe，含 `undetermined` 失败路径 |
| 采用时间线审计（阶段锚点 / 保持帧 / 可见区间 / 静默依据 / 版本绑定 / stale 台账） | **已验证 · 离线** | 同一套 112 例，每个缺陷都由一个匿名合成 fixture 复现（锚点 / 阶段声明 / 按实际声段的静默 / 内容绑定 / 制作 receipt / 数值拒绝） |
| 听感、事实语义、"这条片子好不好" | **不判定 —— 这是刻意的** | `audit` 报告里有 `not_a_verdict_on` 列表；只有真人听看 + 回源帧能定 |
| 字幕分页与时基 | **已验证 · 离线** | `tests/test_subtitles.py`：8 例——短语分页严丝合缝铺满时间窗、ASS 总厘秒进位、PlayRes = 视频尺寸、转义、CRLF 与西文词边界、坏条明确失败、非法数值拒绝 |
| 合成媒体走 EDL 组装（真 `ffmpeg`） | **已验证 · 离线** | `tests/test_build_sample.py`：20 例，lavfi 合成素材（含首次一次 build 闭环、A/B 源错配、定格 fail-closed、libass 烧字幕 + 像素抽检、错带必须被拒、原声混音与无声源拒绝） |
| TTS 适配器硬保证（不改稿 / 空音频与测不出时长都失败 / 缓存不放废件 / 只重算改变节点） | **已验证 · 离线** | `tests/test_tts_guarantees.py`：51 例，走回环假端点 |
| TTS 适配器能对接**你的**供应商 | **未验证 —— 这是刻意的** | 只针对一种 wire 形状，不做跨供应商声明 |
| macOS 录音器能编译、离线检查通过 | **已验证** | `tools/gamerec/tests/regression.sh` 离线组：20 项，含两道"默认不覆盖"闸门 |
| macOS 录音器：必须显式给目标，**绝不**回退抓别的 app | **已验证 · 离线** | 回归 `O1`/`O6`；用法错误在申请任何权限之前就报出 |
| macOS 录音器：不给 `--overwrite` 就拒绝覆盖已有产物 | **已验证 · 离线** | 回归 `O7`/`O8`：退出码 `3`，原文件字节不变 |
| macOS 录音器：有界停止（启动与收尾双截止） | **已验证** | 启动上界离线覆盖；收尾上界在权限组 `P7` |
| macOS 录音器：目标应用**非前台**、窗口在屏时仍能录到 | **作者机器已验证** | `references/spire-checklist.md` 录游戏原声一节：−29.7 dBFS，16/16 秒有声（当时另一个 app 在前台） |
| macOS 录音器：app 级隔离（只收目标应用声音） | **作者机器已验证** | 同节：同时抓另一个 app 实测 −160 dBFS / 22 桶静音 |
| macOS 录音器：最小化 / 隐藏 / 锁屏 | **未验证** | 没测过 |
| macOS 录音器：输出静音、游戏重启、多显示器 | **未验证** | 没测过 |
| macOS 录音器：Windows / Linux | **未验证** | 仅 macOS，依赖 ScreenCaptureKit |
| 动作级**音画同步** | **未验证** | 校验器只比容器时长与时间戳，并且它自己会这么写明 |
| 非零波形是否**就是目标应用**的声音 | **工具不证明** | 需要隔离测试 + 开关前后对照 + 与日志对齐 |
| 配音"像不像真人"、留存、受欢迎程度 | **未测量** | 没做听感研究；模型分数是意见，不是真值 |
| 导演提示是否**真的**改变 TTS 表现 | **未被证明** | 一次小样本 2×2（每格 n=1、各 2 次）**没有显示出稳定的提示效应**；没做显著性检验，所以是"未被证明"，不是"已证明无效"，也不代表供应商能力如何。 |
| 第二个游戏适配 | **不存在** | 只带一个 `<game>-checklist.md`，其他游戏均未适配 |

本次导出修订的完整命令输出与边界见 [`docs/verification-log.md`](docs/verification-log.md)。

---


## 目录

```
agent-gameplay-studio/
├── README.md                  # 英文说明
├── README.zh-CN.md            # 本文件
├── LICENSE                    # MIT —— 仅覆盖确认自有部分，见 NOTICE
├── NOTICE.md                  # 来源、第三方状态、刻意不包含的东西
├── SECURITY.md                # 密钥/脱敏口径，与实际用过的扫描范围
├── CHANGELOG.md
├── Makefile                   # 离线入口
├── .env.example               # 只有占位符
├── docs/
│   ├── architecture.md        # 阶段、数据流、交互示例
│   └── verification-log.md    # 本次实际跑了什么，附接近原始的输出
├── examples/                  # **合成**fixture + 示例 timeline/EDL/审片单
├── skills/gameplay-postproduction/
│   ├── SKILL.md               # SOP 正文（中文；自身声明 license: MIT）
│   ├── references/            # canonical 标准 / 路由 / 配音设计 / 一个游戏的清单
│   ├── templates/             # 素材登记 / 解说单元 / 时间线 / 静默台账 / 审片单
│   ├── evals/trigger_cases.json
│   └── scripts/               # check_postproduction.py + check_timeline_audit.py（都不调用模型）
├── tools/gamerec/             # macOS「目标应用原声 + 画面」录制器（Swift）
├── tools/voice/               # 可选 TTS 适配器、EDL 组装、字幕烧录
└── tests/                     # 离线测试
```

`skills/gameplay-postproduction/` 就是标准 Agent Skill 形态：`SKILL.md` + `references/` +
`templates/` + `evals/` + `scripts/`。它可独立使用——检查器不需要任何模型。

---


## 真实前提依赖（以及**没有**捆绑什么）

| 步骤 | 需要 | 本仓库是否自带 |
|---|---|---|
| 产物校验（`timeline`/`units`/`sheet`/`ready`） | Python 3.9+，`ready` 另需 `ffprobe` | **自带** |
| 素材登记、片段组装 | `ffmpeg` / `ffprobe` | 不自带，请自行安装 |
| 录制游戏原声 + 画面 | macOS 15+、屏幕录制权限 | **含源码**（`tools/gamerec/`），自行编译 |
| 字幕分页与烧录 | `ffmpeg`（需带 **libass**） | **自带**（`tools/voice/subtitles.py` + `burn_subs.py` + `check_burned_subs.py`） |
| **视频理解 / 事件解析** | **你自己的模型通道** | **没有。不自带。** |
| **语音合成** | **你自己的 TTS 端点 + key** | **没有。只有一个可选适配器，而且它不是 Provider 抽象层。** |
| 剪辑 / 合成 / 导出 | **你自己的剪辑器** | **没有。不自带。** |

**安装本仓库不会让你获得任何模型能力。** 没有捆绑模型、没有 API key、没有代理、没有订阅、
没有登录态。`tools/voice/tts_adapter.py` 是**适配器**：只有你自己把 `TTS_BASE_URL` 与
`TTS_API_KEY` 指向一个符合文档所述 wire 形状的端点，它才可能跑起来。

**它不是"通用语音 API 适配层"。** 它只针对**一种** wire 形状
（`POST {base}/chat/completions` 带 `audio` 字段），**不声称跨供应商通用**，
也不能因为两家都暴露 `/chat/completions` 就假定行为一致。换供应商请另写适配器。
它**确实保证**的（离线测试用一个本地假端点逐条证明）：

- **你的稿子不会被自动改写**：默认不请求改写；该形状支持时把 `optimize_text_preview` 显式置
  `false`；万一还是被改写，会**标记**而不是静默接受（`--strict-no-rewrite` 时直接硬失败）。
- **空音频/测不出时长就是失败，不会进缓存**：响应没有音频字段、音频为空、或 `ffprobe` 测不出时长，
  一律抛错并**删掉半成品文件**，避免下次从缓存里拿出一个废件。
- **缓存键覆盖真正影响结果的东西，命中后还要再校验**：请求本身 **+** strict 标志 **+** 端点的**单向指纹**；
  命中时还要求磁盘上的音频仍与记录的 **sha256**、字节数一致，且时长是**有限正数**。
  宽松模式下接受的 take 不会被回放给 strict 运行；一个供应商的音频不会被当成另一个供应商的结果；
  被截断或手工替换的文件也不会被过期 sidecar 认证为合格。端点 **URL 不落盘**，只存指纹。
- **凭据来自环境变量，或用 `TTS_ENV_FILE` 指向的 dotenv 文件**：只按这些名字读取，
  不回显、不写进产物。
- **供应商错误体会先脱敏**：bearer token、`api_key=`/`token=`/`secret=`/`password=` 这类键值对
  （带引号或不带引号）、key 形状字符串、带凭据的查询参数，都会在进入日志、traceback 或异常消息
  之前被替换为 `<redacted>` 并截断。

旁边的组装器**刻意只出旁白**：每个画面片段都用 `-an` 渲染，源片原声在切片段阶段就被丢掉，
终片音轨完全由你的旁白段构成。它**不会**悄悄把源片原声混进成片；源片本身是静音，成片就如实静音；
后续要不要压低/保留游戏原声，是你自己显式决定的事。

skill 的 `references/routing.md` 里列了 `editor` / `watch` / 视频理解通道之类的角色名——
那是作者本机当时把这类工作委托给了谁，**不是你必须安装的依赖**，换成你自己的即可。

作者自己的视频理解偏好是一个可配置的 Flash 档模型 + 一个作故障后备的第二供应商。
那是**可配置的选择**，不是本仓库提供的能力，也不代表任何人的账号、代理或登录态会随之附赠。

### macOS 录游戏原声

```bash
tools/gamerec/build.sh                                  # → tools/gamerec/build/GameAVRec.app
# 把示例里的 bundle id 换成你自己目标应用的。
# 刻意没有内置默认值：抓错 app 比直接失败更糟。
export GAME_BUNDLE_ID="com.megacrit.SlayTheSpire2"

tools/gamerec/record-game.sh preflight
tools/gamerec/record-game.sh start --out run.mp4 --duration 60 --focus-log run.focus.jsonl
tools/gamerec/record-game.sh verify run.mp4
```

先跑 `tools/gamerec/record-game.sh preflight`：它会告诉你这个 id 有没有真的命中正在运行的目标，
在开始录之前就能发现。

`verify` **只报它真正测到的东西**：音轨存在、单遍逐秒峰值（全覆盖）、有声桶占比、
视频帧是否持续到达、抽帧内容是否真的在变（md5）。
它**在自己的输出里写明**：不证明音画同步、不证明画面质量、也不证明"这段声音是目标应用发出的"。
有些游戏失焦会自己静音；录制器区分不了"游戏自己静音"和"抓取失败"，
所以它如实报静音，而不是猜。

---


## 架构

六个**阶段名，不是 API**——按需执行，不要求全部跑满：

```
ingest → analyze → commentary_plan → edit_plan → review → repair
```

```
录屏 ──ffprobe──► 素材登记 ──(你的模型通道)──► 候选事件
                                                  │
                                    回源帧核验（必备，不可跳过）
                                                  ▼
                          解说单元 ──► 统一时间线 ──► 你的剪辑器
                        (五要素 / 三字段分列)             │
                                              实际导出 MP4 ──► 审片问题单
                                                                  │
                                                          只做局部修复
```

三条不靠自觉、靠机器兜底的约束：

1. **唯一时间基。** 视频、配音、字幕共用一份时间线。`final` 长度必须 = `clip` 长度 ÷ speed
   + freeze；同一素材内 clip/final 区间不得重叠。
2. **三字段禁止互相回填。** `stated_reason`（当时公开说的）、`retrospective_commentary`
   （事后复盘，含事后合理化）、`outcome`（实际结果）是分开的列。
   检查器会拒绝**来源字段明确标为事后**的 `stated_reason`。
3. **校验优先于导出。** EDL 组装里，一段旁白放不进它的画面窗口时，**在渲染前失败**，
   而不是警告一句然后导出一条时间轴错位的成片。

### 交互示例

```
你：把录屏整理成后期流程，给我素材登记和解说准备

  1. ffprobe 源片                        → 素材行：sha256 / 字节 / 时长 / fps / 编码 /
                                            分辨率 / has_audio
  2. 你的模型通道读视频                   → **候选**事件。不是事实，更不是剪点。
  3. 每条承重结论回源帧核验               → 胜负 / 选牌 / 伤害 / 血量 / 金币 以帧为准；
                                            对不上就写 unknown
  4. 写解说单元                           → 情况/动作/stated_reason/事后复盘/结果/覆盖，
                                            每一次发生一行
  5. 建统一时间线                         → 源 → 片段 → 成片，含 speed/freeze 算术
  6. 跑检查器                             → 先结构门槛，导出后再跑 ready 门槛
  7. 写审片问题单                         → 逐项给证据；未解决 issue 会卡住 ready
```

---


## 非评测作弊与隔离

这是标准里的**硬约束**（`references/postproduction-standard.md` §0），不是客套话：

- 评测与后期是两条独立线。本项目**不指定、不推荐**评测侧使用任何模型或工具，
  评测遵循各赛道自己既定的协议。
- 后期产出——events、读牌结果、`stated_reason`——**只是候选，绝不是对局事实的裁定**。
- 后期输出**不得回流**到参赛决策或任何被评测的行为；**后期不参与参赛决策**。
- **画面内答案泄漏：** 多窗口录屏（游戏 + 助手/战报面板）会把答案录进画面，
  既泄漏答案，也让"模型是不是真听懂了音轨"变得无法回答。必须这么录时，要在审片单里声明。
- `ready` 门槛把一条相关拒绝写成硬规则：没核过的项写 `unknown`，而 **`unknown` 不是通过**。
  没有独立真值就不能声称"零漏报"。

这也是本项目与 `agent-gamebench` 保持分开的原因：共用 fixture、评分路径或模型通道，
两边都会被污染。

---


## 诚实的边界

引用本仓库任何内容之前，先读这一节。

**它不是什么。** 没有 GUI。没有一键出片。没有全自动真人级主播。skill 推进流程并强制门槛；
成片好不好仍然由人判定，而 `ready` **明确拒绝**评判画面与声音质量。

**哪些未验证。** 音画**同步**没有任何工具验证过——录制器的校验器只比容器时长与时间戳，
并且在它自己的输出里写明这一点。录制器只在"目标应用**非前台、窗口在屏**"以及 app 级隔离下实测过；
最小化、隐藏、锁屏、输出静音、录制中途重启、多显示器、非 macOS 均**未测**。
TTS 适配器只针对**一种** wire 形状，不是跨供应商抽象。
导演提示是否改变 TTS 表现**没有被证明**（一次小样本 2×2，未做显著性检验）——
"未被证明"不等于"已证明无效"，也说明不了供应商能力。

**刻意不测的部分。** 录制器的权限组用例，除非你给出真实目标并授予屏幕录制权限，否则一律 SKIP；
它宁可不报，也不报一个没挣到的 pass。

**示例数据是什么。** `examples/` 下全部是合成的——没有真实对局、没有真实成绩、没有性能声明。

完整命令输出、以及每个检查的局限，见 [`docs/verification-log.md`](docs/verification-log.md)。

---

## 许可与范围

**确认自有部分按 MIT 授权**——见 [`LICENSE`](LICENSE)。
第三方情况、刻意**不**包含的东西、以及每个随包文件的来源，见 [`NOTICE.md`](NOTICE.md)。
这里没有把任何闭源内容重新许可，也没有 vendor 任何第三方代码。

随包文档与工具输出以**中文**为主，与作者工作语言及 skill 原文一致；两份 README 刻意都做成双语。
