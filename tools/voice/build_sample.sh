#!/usr/bin/env bash
# build_sample.sh — 把「源录屏区间 + 配音段」组装成可播放的解说样片。
#
# 本项目的最小可复用执行方法（标为项目目标，不是全平台标准）：
#   1. 源区间 1x 取证，不改动作速度；EDL 只剪掉无信息等待
#   2. 旁白**压在画面之上**；每段配音按**实测时长**驱动
#   3. 中间产物无损；终混 48k；响度/true peak 归一化
#   4. 字幕由实际口播稿生成，随实测时长对齐；**长句按短语分页**（一条口播 ≠ 一条 cue），
#      并写出 **PlayRes = 视频尺寸** 的 ASS（SRT→ASS 默认 384×288 会把字号放大 3.35×）
#   5. **校验优先于导出**：任何一段音频放不进它的画面窗口，就**在制作前失败**，
#      不允许"先警告再导出"——那会让超长音频把后续所有句子推迟，
#      最后被 -shortest 截尾，而人只看得到成片长度不对。
#   6. **成片音轨默认只有旁白**：
#      · 每个画面片段都用 `-an` 渲染（源音轨在切片段阶段就被丢掉）；
#      · 终片音轨完全由 `voice_master.wav` 构成，而它只拼接 voice_dir 里的旁白段。
#      也就是说：**本脚本不会悄悄把源片原声带进成片**。源片是静音就如实出静音成片，
#      不伪造游戏原声。
#      源片**确实有原声**、且你要保留时，显式开 `KEEP_SRC_AUDIO=1`：脚本会把每个片段的
#      源音轨按同一 EDL 切出来、拼成与画面等长的 `src_audio.wav`，再与旁白分轨混音
#      （默认 `SRC_AUDIO_DUCK=1`，用旁白做 sidechain 把原声压下去）。源片没有音轨时
#      **直接失败报错**，不会给你一条静音轨冒充原声。
#
# 用法:
#   SRC_VIDEO=/path/to/source.mp4 build_sample.sh <edl.tsv> <voice_dir> <out_dir>
#
# 环境变量（**没有私有默认值**，源视频必须显式给）：
#   SRC_VIDEO   源录屏文件（必需）
#   BURN_SUBS   1 = 用 libass 把字幕烧进成片（默认 0，只出 subs.srt/subs.ass）。
#               烧录发生在**写 receipt 之前**，所以 receipt/审计绑定的是真正交付的那个文件；
#               未烧版留在 final-nosub.mp4，供像素级字幕抽检做对照。
#   KEEP_SRC_AUDIO  1 = 保留源片原声并与旁白分轨混音（默认 0）。源片无音轨 → 直接失败。
#   SRC_AUDIO_GAIN  原声增益（dB，默认 -8）
#   SRC_AUDIO_DUCK  1 = 旁白说话时把原声压下去（默认 1）；0 = 只做固定增益叠加
#   SUB_FONT / SUB_SIZE / SUB_MARGIN_V / SUB_MARGIN_LR   字幕样式（默认 Hiragino Sans GB /
#               28 / 16 / 100，与 960×966 实测可读的那一版一致）
#   SUB_MAX_CHARS   单条字幕最多几个全角字；0 = 按画面宽度自动算
#   SUB_BAND / SUB_PROTECT  可选：给 `check_burned_subs.py` 用的字幕带与保护带（y0:y1）
#   TIMELINE / PREFLIGHT / SILENCE_LEDGER   可选：给了就在导出后**现场跑采用时间线审计**
#               （与 `ready` 门槛同一个 checker、同一套规则），并在制作完成的这一刻写出
#               `produce-receipt.json`（把 timeline 与字幕/音轨/成片绑在同一制作上）。
#               审计不过 → 退出 3，并写出 `DRAFT.txt`；没给 → 产物一律标为
#               **DRAFT（不可交付）**，见文件末尾。
#   SRC_CROP    源画面的裁剪/缩放滤镜链，默认 scale=960:-2（等宽等比）。
#               若源片里混入了带答案的辅助面板，必须在这里显式裁掉。
#   OUT_FPS     输出帧率，默认 30（降采样，不补帧）
#   TARGET_I / TARGET_TP   终混响度 / true peak 目标，默认 -16 LUFS / -1.5 dBTP
#   FIT_TOL     旁白放得下的容差（秒），默认 0.05
#   SYNC_TOL    终片音视频等长容差（秒），默认 0.15
#
# edl.tsv 列（TAB，表头必须完全一致）：
#   seg  src_start  src_end  audio_file  offset  freeze  subtitle_text
#   freeze: 该段末尾**显式登记**的定格秒数（0 = 不定格）。定格是剪辑手段，用来给长旁白腾时间。
#
# 放不下时的三种合法修法（脚本会把这三种印在报错里）：
#   a) 改稿：把这一句说短；
#   b) 调画面：把 src_end 往后延（多用一点源片），或减小 offset；
#   c) 显式定格：把 freeze 设成需要的秒数并登记进 timeline。
set -euo pipefail

TL="${1:?edl tsv}"; VDIR="${2:?voice dir}"; OUT="${3:?out dir}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PRODUCE="$HERE/produce_timeline.py"

# 未通过采用时间线审计的产物一律标 draft：没有这个标记就不许当成可交付。
draft() {
  {
    echo "DRAFT - NOT DELIVERABLE"
    echo "reason: $1"
    echo "见 $OUT/audit-report.json 或上面的 stdout/stderr。"
  } > "$OUT/DRAFT.txt"
  echo "!! $1" >&2
  echo "!! 产物留在 $OUT，已标记为 draft，不是可交付成片。" >&2
}
SRC="${SRC_VIDEO:?必须显式给源视频：SRC_VIDEO=/path/to/source.mp4（本脚本不内置任何私有默认路径）}"
CROP="${SRC_CROP:-scale=960:-2}"           # 默认等比缩到宽 960；混入带答案的面板时必须显式裁掉
FPS="${OUT_FPS:-30}"                       # 降采样，不补帧
TARGET_I="${TARGET_I:--16}"; TARGET_TP="${TARGET_TP:--1.5}"
FIT_TOL="${FIT_TOL:-0.05}"                 # 旁白放得下的容差（秒）
SYNC_TOL="${SYNC_TOL:-0.15}"               # 终片音视频时长一致容差（秒）
BURN_SUBS="${BURN_SUBS:-0}"                # 1 = 用 libass 把字幕烧进交付成片
KEEP_SRC_AUDIO="${KEEP_SRC_AUDIO:-0}"      # 1 = 保留源原声并与旁白分轨混音
SRC_AUDIO_GAIN="${SRC_AUDIO_GAIN:--8}"     # 原声增益 dB
SRC_AUDIO_DUCK="${SRC_AUDIO_DUCK:-1}"      # 1 = 旁白说话时压低原声
SUB_FONT="${SUB_FONT:-Hiragino Sans GB}"; SUB_SIZE="${SUB_SIZE:-28}"
SUB_MARGIN_V="${SUB_MARGIN_V:-16}"; SUB_MARGIN_LR="${SUB_MARGIN_LR:-100}"
SUB_MAX_CHARS="${SUB_MAX_CHARS:-0}"
BURN="$HERE/burn_subs.py"; SUBS_PY="$HERE/subtitles.py"
# 字体名会写进 ASS 头；带换行/大括号就能注入 ASS 指令，这里直接拒绝。
case "$SUB_FONT" in *[!A-Za-z0-9\ _-]*) echo "!! SUB_FONT 含可疑字符：$SUB_FONT" >&2; exit 2;; esac

command -v ffmpeg >/dev/null || { echo "需要 ffmpeg" >&2; exit 2; }
[ -f "$TL" ] || { echo "找不到 EDL: $TL" >&2; exit 2; }
[ -f "$SRC" ] || { echo "找不到源视频: $SRC" >&2; exit 2; }

HDR=$'seg\tsrc_start\tsrc_end\taudio_file\toffset\tfreeze\tsubtitle_text'
got_hdr="$(head -1 "$TL" | tr -d '\r')"
[ "$got_hdr" = "$HDR" ] || {
  echo "!! EDL 表头不符。" >&2
  echo "   期望: $HDR" >&2
  echo "   实际: $got_hdr" >&2
  echo "   （加 freeze 列是为了让长旁白有合法的腾挪手段；旧格式请显式补一列 0）" >&2
  exit 2
}

# ---------------------------------------------------------------- 预检：制作前就失败
# 只做两件事：量每段旁白的实测时长、按 EDL 计划算出该段画面窗口够不够。
python3 - "$TL" "$VDIR" "$FIT_TOL" <<'PY'
import subprocess, sys, math
tl, vdir, tol = sys.argv[1], sys.argv[2], float(sys.argv[3])
bad, rows = [], []
with open(tl, encoding='utf-8') as f:
    next(f)
    for n, line in enumerate(f, 2):
        line = line.rstrip('\n')
        if not line.strip():
            continue
        c = line.split('\t')
        if len(c) != 7:
            bad.append((c[0] if c else f'第{n}行', f'列数 {len(c)} ≠ 7（TAB 分隔）', 0, 0, 0)); continue
        seg, ss, se, afile, off, freeze, text = c[0], float(c[1]), float(c[2]), c[3], float(c[4]), float(c[5]), c[6]
        try:
            adur = float(subprocess.run(
                ['ffprobe','-v','error','-show_entries','format=duration','-of','default=nw=1:nk=1',
                 f'{vdir}/{afile}'], capture_output=True, text=True, check=True).stdout.strip())
        except Exception:
            bad.append((seg, f'取不到旁白时长（{vdir}/{afile} 不存在或不可解码）', 0, 0, 0)); continue
        if not math.isfinite(adur) or adur <= 0:
            bad.append((seg, f'旁白时长无效（{adur}）', 0, 0, 0)); continue
        planned = (se - ss) + freeze            # 计划画面窗口（源长 + 登记定格）
        need = off + adur
        rows.append((seg, planned, adur, off, freeze))
        if need > planned + tol:
            bad.append((seg, 'POS', need, planned, freeze))

for seg, why, need, planned, freeze in bad:
    if why == 'POS':
        print(f"!! seg {seg}: 旁白放不进画面窗口 —— 需要 {need:.3f}s，窗口只有 {planned:.3f}s"
              f"（源 {planned-freeze:.3f}s + 定格 {freeze:.3f}s），超出 {need-planned:.3f}s", file=sys.stderr)
    elif why == 'NEG':
        print(f"!! seg {seg}: 偏移为负（{need}）", file=sys.stderr)
    else:
        print(f"!! seg {seg}: {why}", file=sys.stderr)
if bad:
    print("", file=sys.stderr)
    print("拒绝导出。三种合法修法（选一个，不要靠警告继续）：", file=sys.stderr)
    print("  a) 改稿：把这一句说短，重新合成该段；", file=sys.stderr)
    print("  b) 调画面：把 src_end 往后延（多用一点源片），或把 offset 减小；", file=sys.stderr)
    print("  c) 显式定格：把 freeze 设成需要的秒数，并登记进 timeline（定格是剪辑手段，不是缺陷）。", file=sys.stderr)
    sys.exit(3)
print(f"预检通过：{len(rows)} 段，最大余量 "
      f"{min(planned-(off+adur) for _,planned,adur,off,_ in rows):.3f}s")
PY

# ---------------------------------------------------------------- 制作前交叉核对
# 需要 recipe（TIMELINE）+ preflight 才会走 adopted 路径；否则产物只能是 draft。
WANT_ADOPT=0
if [ -n "${TIMELINE:-}" ] && [ -n "${PREFLIGHT:-}" ] && [ -f "$PRODUCE" ]; then
  mkdir -p "$OUT"
  echo "--- 制作前交叉核对（recipe ↔ EDL ↔ preflight ↔ 真实 SRC_VIDEO）---"
  if python3 "$PRODUCE" plan --recipe "$TIMELINE" --edl "$TL" --preflight "$PREFLIGHT" \
       --src-video "$SRC" --voice-dir "$VDIR" --out-dir "$OUT"; then
    WANT_ADOPT=1
  else
    mkdir -p "$OUT"
    draft "制作前交叉核对未通过：recipe/EDL/preflight/源视频对不上（没有渲染，也没有猜）"
    exit 3
  fi
fi

# ---------------------------------------------------------------- 制作
mkdir -p "$OUT/concat" "$OUT/seg_norm"
OUT="$(cd "$OUT" && pwd)"   # 绝对路径：ffmpeg concat 按列表文件所在目录解析相对路径
: > "$OUT/concat/video.txt"; : > "$OUT/concat/audio.txt"; : > "$OUT/cues.tsv"
if [ "$KEEP_SRC_AUDIO" = "1" ]; then
  # 源片必须真的有音轨，否则"保留原声"就是在造假
  if [ -z "$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_type -of csv=p=0 "$SRC" </dev/null)" ]; then
    mkdir -p "$OUT"
    draft "KEEP_SRC_AUDIO=1，但源片 $SRC 没有音轨（拒绝用静音轨冒充游戏原声）"
    exit 3
  fi
  : > "$OUT/concat/srcaudio.txt"
fi
MEASURED="$OUT/adopt/measured.tsv"
if [ "$WANT_ADOPT" -eq 1 ]; then
  printf 'seg\twindow\tnarration_duration\toffset\tfinal_start\tfinal_end\n' > "$MEASURED"
fi
t_cursor=0; i=0

# 跳过表头
tail -n +2 "$TL" | while IFS=$'\t' read -r seg ss se afile off freeze text; do
  [ -z "${seg:-}" ] && continue
  i=$((i+1)); n=$(printf '%02d' $i)

  # 画面几何完全由 SRC_CROP 决定：这里不再追加硬编码的 scale，
  # 否则会盖掉调用方的裁剪/缩放意图（旧版把 960x966 写死在这里）。
  vf="${CROP},fps=${FPS}"
  mark_file="$OUT/adopt/mark-$n.txt"
  if [ -f "$mark_file" ]; then
    # 支持的定格：冻的是片段末帧（plan 已经核对过 anchor == src_end），
    # 并且**真的**把保持标注烧进画面（不是只在表格里写一行 mark）。
    srclen=$(python3 -c "print(round(float('$se')-float('$ss'),6))")
    vf="${vf},tpad=stop_mode=clone:stop_duration=${freeze}"
    vf="${vf},drawtext=textfile='${mark_file}':x=10:y=h-th-10:fontsize=18:fontcolor=white:box=1:boxcolor=black@0.65:enable='gte(t,${srclen})'"
  elif python3 -c "import sys;sys.exit(0 if float('$freeze')>0 else 1)"; then
    vf="${vf},tpad=stop_mode=clone:stop_duration=${freeze}"   # 显式登记的定格
  fi
  vf="${vf},format=yuv420p"

  vclip="$OUT/concat/v$n.mp4"
  ffmpeg -nostdin -v error -y -ss "$ss" -to "$se" -i "$SRC" -vf "$vf" -an \
    -c:v libx264 -preset veryfast -crf 18 "$vclip"
  printf "file '%s'\n" "$vclip" >> "$OUT/concat/video.txt"

  cdur=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$vclip" </dev/null)
  adur=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$VDIR/$afile" </dev/null)

  # 二次校验（纵深防御）：渲染后的实测窗口仍须放得下
  if ! python3 -c "import sys;sys.exit(0 if float('$off')+float('$adur')<=float('$cdur')+float('$FIT_TOL') else 1)"; then
    echo "!! seg $i 渲染后仍放不下：旁白 ${adur}s @${off}s > 窗口 ${cdur}s。已中止，未生成成片。" >&2
    exit 3
  fi
  # 窗口也不该比旁白长太多（否则是漏登记的定格/多余尾巴）
  if ! python3 -c "import sys;sys.exit(0 if float('$cdur')-float('$off')-float('$adur')<=3.0 else 1)"; then
    echo "   note seg $i: 窗口比旁白长 ${cdur}s-${off}s-${adur}s，检查是否漏登记 freeze" >&2
  fi

  anorm="$OUT/seg_norm/a$n.wav"
  off_ms=$(python3 -c "print(int(float('$off')*1000))")
  # 关键：先 atrim 到窗口（防超长），再 apad 补到窗口（防过短）——两个方向都夹死，
  # 这样"超长音频把后面全部推迟"在结构上不可能发生。
  # 注意顺序：apad 之后不能再接 atrim（实测 apad=whole_dur 会被其后的 atrim 弄失效）。
  ffmpeg -nostdin -v error -y -i "$VDIR/$afile" \
    -af "loudnorm=I=-17:TP=-2:LRA=7,adelay=${off_ms}:all=1,atrim=0:${cdur},apad=whole_dur=${cdur}" \
    -ar 48000 -ac 1 -c:a pcm_s16le "$anorm"
  # 自检：归一化后的段长必须等于窗口，否则后面整条时间轴都会漂
  aout=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$anorm" </dev/null)
  if ! python3 -c "import sys;sys.exit(0 if abs(float('$aout')-float('$cdur'))<=0.02 else 1)"; then
    echo "!! seg $i 段音频长 ${aout}s ≠ 窗口 ${cdur}s（时间轴会漂）。已中止。" >&2
    exit 3
  fi
  printf "file '%s'\n" "$anorm" >> "$OUT/concat/audio.txt"

  # 源原声：按**同一 EDL**切出来、并用同样的 atrim/apad 夹到窗口长度，
  # 这样它与画面严格等长，后面才能真的分轨混音（而不是"大致对一下"）。
  if [ "$KEEP_SRC_AUDIO" = "1" ]; then
    snorm="$OUT/seg_norm/s$n.wav"
    ffmpeg -nostdin -v error -y -ss "$ss" -to "$se" -i "$SRC" -vn \
      -af "volume=${SRC_AUDIO_GAIN}dB,atrim=0:${cdur},apad=whole_dur=${cdur}" \
      -ar 48000 -ac 2 -c:a pcm_s16le "$snorm"
    sout=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$snorm" </dev/null)
    if ! python3 -c "import sys;sys.exit(0 if abs(float('$sout')-float('$cdur'))<=0.02 else 1)"; then
      echo "!! seg $i 源原声段长 ${sout}s ≠ 窗口 ${cdur}s。已中止。" >&2
      exit 3
    fi
    printf "file '%s'\n" "$snorm" >> "$OUT/concat/srcaudio.txt"
  fi

  if [ "$WANT_ADOPT" -eq 1 ]; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$i" "$cdur" "$adur" "$off" "$t_cursor" \
      "$(python3 -c "print(round(float('$t_cursor')+float('$cdur'),6))")" >> "$MEASURED"
  fi

  # 没有口播的行（例如纯画面收尾）不写字幕。
  # 这里只登记"这段口播覆盖的实测时间窗"，分页（长句拆成多条短语 cue）在后面统一做：
  # 一条口播 ≠ 一条字幕 cue。
  if [ -n "${text// /}" ]; then
    ms=$(python3 -c "print(int((float('$t_cursor')+float('$off'))*1000))")
    me=$(python3 -c "print(int((float('$t_cursor')+float('$off')+float('$adur'))*1000))")
    printf '%s\t%s\t%s\n' "$ms" "$me" "$(printf '%s' "$text" | tr '\t' ' ')" >> "$OUT/cues.tsv"
  fi

  t_cursor=$(python3 -c "print(round(float('$t_cursor')+float('$cdur'),3))")
  printf "seg %s  src %s-%s  freeze %s  窗口 %.3fs  旁白 %.3fs @%.2f  余量 %.3fs  -> final end %ss\n" \
    "$i" "$ss" "$se" "$freeze" "$cdur" "$adur" "$off" \
    "$(python3 -c "print(round(float('$cdur')-float('$off')-float('$adur'),3))")" "$t_cursor"
done

ffmpeg -nostdin -v error -y -f concat -safe 0 -i "$OUT/concat/video.txt" -c copy "$OUT/video_raw.mp4"
ffmpeg -nostdin -v error -y -f concat -safe 0 -i "$OUT/concat/audio.txt" -c:a pcm_s16le "$OUT/voice_raw.wav"
ffmpeg -nostdin -v error -y -i "$OUT/voice_raw.wav" -af "loudnorm=I=${TARGET_I}:TP=${TARGET_TP}:LRA=9" \
  -ar 48000 -ac 2 -c:a pcm_s16le "$OUT/voice_master.wav"

# ---------------------------------------------------------------- 字幕（长句分页 + 真尺寸 ASS）
# 分页与 ASS 生成在 subtitles.py 里：ASS 时间戳走总厘秒进位，PlayRes 用**视频真实尺寸**。
if [ ! -s "$OUT/cues.tsv" ]; then
  draft "EDL 里没有任何字幕文本：拒绝产出一个没有字幕的成片"
  exit 3
fi
WH=$(ffprobe -v error -select_streams v:0 -show_entries stream=width,height \
      -of csv=p=0:nk=1 "$OUT/video_raw.mp4" </dev/null | head -1)
SUB_W="${WH%,*}"; SUB_H="${WH#*,}"
LABELS_FILE=""
if [ -f "$OUT/hold-labels.tsv" ]; then LABELS_FILE="$OUT/hold-labels.tsv"; fi
python3 "$SUBS_PY" build --cues "$OUT/cues.tsv" --srt "$OUT/subs.srt" --ass "$OUT/subs.ass" \
  --w "$SUB_W" --h "$SUB_H" --font "$SUB_FONT" --size "$SUB_SIZE" \
  --margin-v "$SUB_MARGIN_V" --margin-lr "$SUB_MARGIN_LR" --max-chars "$SUB_MAX_CHARS" \
  --labels "$LABELS_FILE"

SRC_AUDIO=""
if [ "$KEEP_SRC_AUDIO" = "1" ]; then
  ffmpeg -nostdin -v error -y -f concat -safe 0 -i "$OUT/concat/srcaudio.txt" \
    -c:a pcm_s16le "$OUT/src_audio.wav"
  SRC_AUDIO="$OUT/src_audio.wav"
fi

# 音视频必须一样长：不等就是有段被 -shortest 截了，必须失败而不是交付
vd=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$OUT/video_raw.mp4" </dev/null)
ad=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$OUT/voice_master.wav" </dev/null)
if ! python3 -c "import sys;sys.exit(0 if abs(float('$vd')-float('$ad'))<=float('$SYNC_TOL') else 1)"; then
  echo "!! 音视频时长不一致：画面 ${vd}s vs 音轨 ${ad}s（差 $(python3 -c "print(round(abs(float('$vd')-float('$ad')),3))")s）。" >&2
  echo "   这通常意味着某段旁白放不进窗口、把后面全部推迟后被 -shortest 截断。拒绝导出。" >&2
  exit 3
fi
if [ -n "$SRC_AUDIO" ]; then
  sd=$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$SRC_AUDIO" </dev/null)
  if ! python3 -c "import sys;sys.exit(0 if abs(float('$sd')-float('$vd'))<=float('$SYNC_TOL') else 1)"; then
    echo "!! 源原声轨 ${sd}s 与画面 ${vd}s 不等长：不允许混一条对不上的原声。" >&2
    exit 3
  fi
fi

echo "--- loudness / true peak ---"
ffmpeg -nostdin -hide_banner -nostats -i "$OUT/voice_master.wav" -af ebur128=peak=true -f null - 2>&1 \
  | sed -n '/Summary/,/True peak/p' | head -14

# 终混：默认只有旁白；显式 KEEP_SRC_AUDIO=1 才是「原声 + 旁白」分轨混音。
if [ -n "$SRC_AUDIO" ]; then
  # 注意：ffmpeg 的 filtergraph 里一个 label 只能被**消费一次**。
  # 旁白既要当 sidechain 又要进终混，所以必须 asplit（用两次会报
  # "Stream specifier 'voice' … matches no streams"，实测踩过）。
  if [ "$SRC_AUDIO_DUCK" = "1" ]; then
    # 旁白当 sidechain：它一说话就把原声压下去（release 放开），不会两条声音互相盖。
    MIX="[1:a]aformat=sample_rates=48000:channel_layouts=stereo,asplit=2[voice][sc];"
    MIX="${MIX}[2:a]aformat=sample_rates=48000:channel_layouts=stereo[src];"
    MIX="${MIX}[src][sc]sidechaincompress=threshold=0.03:ratio=8:attack=5:release=250[duck];"
    MIX="${MIX}[voice][duck]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]"
  else
    MIX="[1:a]aformat=sample_rates=48000:channel_layouts=stereo[voice];"
    MIX="${MIX}[2:a]aformat=sample_rates=48000:channel_layouts=stereo[src];"
    MIX="${MIX}[voice][src]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]"
  fi
  rm -f "$OUT/final.mp4"
  if ! ffmpeg -nostdin -v error -y -i "$OUT/video_raw.mp4" -i "$OUT/voice_master.wav" -i "$SRC_AUDIO" \
       -filter_complex "$MIX" -map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k -shortest "$OUT/final.mp4"; then
    draft "终混失败（源原声 + 旁白）：没有可交付成片"
    exit 3
  fi
  echo "已混入源原声：gain ${SRC_AUDIO_GAIN}dB duck=${SRC_AUDIO_DUCK}（分轨保留，未覆盖旁白母版）"
else
  rm -f "$OUT/final.mp4"
  if ! ffmpeg -nostdin -v error -y -i "$OUT/video_raw.mp4" -i "$OUT/voice_master.wav" \
       -map 0:v -map 1:a -c:v copy -c:a aac -b:a 192k -shortest "$OUT/final.mp4"; then
    draft "终混失败（画面 + 旁白）：没有可交付成片"
    exit 3
  fi
  echo "成片音轨 = 旁白（未混源原声；若源片确有原声而你要保留，显式 KEEP_SRC_AUDIO=1）"
fi
if [ ! -s "$OUT/final.mp4" ]; then
  draft "终混产出为空文件：没有可交付成片"
  exit 3
fi
echo "final: $OUT/final.mp4"

# ---------------------------------------------------------------- 烧字幕（可选，但在交付前必须做）
# 烧录放在**写 receipt 之前**：receipt/审计绑定的是真正交付的那个文件，不是一个中间物。
# 未烧版留成 final-nosub.mp4，供 check_burned_subs.py 做逐像素对照。
if [ "$BURN_SUBS" = "1" ]; then
  mv "$OUT/final.mp4" "$OUT/final-nosub.mp4"
  if ! python3 "$BURN" "$OUT/final-nosub.mp4" "$OUT/subs.srt" "$OUT/final.mp4" \
       --font "$SUB_FONT" --size "$SUB_SIZE" --margin-v "$SUB_MARGIN_V" \
       --margin-lr "$SUB_MARGIN_LR" --labels "$LABELS_FILE" --json "$OUT/subs-burn.json"; then
    draft "字幕烧录失败（libass）：没有可交付成片"
    exit 3
  fi
fi
ffprobe -v error -show_entries format=duration,size -show_entries stream=codec_type,codec_name,width,height,sample_rate,channels -of default=nw=1 "$OUT/final.mp4"

# ---------------------------------------------------------------- 采用时间线审计（默认验收路径）
# 这里调用的是 `ready` 门槛用的**同一个** checker 与**同一套**规则：锚点与画面源区间交叉核对、
# 阶段声明、按实际声段的静默依据、内容级版本绑定 + 制作 receipt、stale 素材台账。
# adopted-timeline.tsv 是**从真实 EDL 与实测结果**生成的那一份，不是叫调用方盲改自己的输入。
AUDIT="$HERE/../../skills/gameplay-postproduction/scripts/check_timeline_audit.py"
SUBS_FILE="${SUBS:-$OUT/subs.srt}"
if [ "$WANT_ADOPT" -eq 1 ] && [ -n "${SILENCE_LEDGER:-}" ] && [ -f "$AUDIT" ]; then
  if ! python3 "$PRODUCE" adopt --out-dir "$OUT" --measured "$MEASURED" --edl "$TL" \
       --src-video "$SRC" --subs "$SUBS_FILE" --audio "$OUT/voice_master.wav" \
       --final "$OUT/final.mp4" \
       --produced-at "$(date -u +%Y-%m-%dT%H:%M:%SZ)"; then
    draft "制作后无法生成 adopted timeline / receipt"
    exit 3
  fi
  echo "--- 采用时间线审计（$OUT/adopted-timeline.tsv）---"
  if python3 "$AUDIT" audit "$OUT/adopted-timeline.tsv" --preflight "$PREFLIGHT" \
       --silence-ledger "$SILENCE_LEDGER" --subtitle "$SUBS_FILE" \
       --audio "$OUT/voice_master.wav" --final-mp4 "$OUT/final.mp4" \
       --receipt "$OUT/produce-receipt.json" --edl "$TL" \
       --report-out "$OUT/audit-report.json"; then
    rm -f "$OUT/DRAFT.txt"
    echo "审计通过：$OUT/audit-report.json（≠ 审片通过；听感与事实语义仍需人核）"
    echo "交付前再过 ready 门槛（单子 + adopted-timeline.tsv + 同一套输入 + receipt）。"
  else
    draft "采用时间线审计未通过"
    exit 3
  fi
else
  mkdir -p "$OUT"
  cat > "$OUT/DRAFT.txt" <<'DRAFTEOF'
DRAFT - NOT DELIVERABLE
This cut was assembled WITHOUT the adopted-timeline audit: TIMELINE (recipe) / PREFLIGHT /
SILENCE_LEDGER were not all provided, or this ffmpeg has no audit checker next to it. This builder
can only produce a deliverable cut through the adopted-timeline path, because the final subtitle /
audio / cut digests cannot be known before they exist and a generic timeline cannot be trusted to
match the EDL it claims to describe. Anchoring, phase, holds, silence and version binding are
therefore UNCHECKED. This output is a draft only.

Make it deliverable (one invocation, no re-render needed):
  TIMELINE=recipe.tsv PREFLIGHT=preflight.json SILENCE_LEDGER=gaps.tsv \
    SRC_VIDEO=/abs/source.mp4 build_sample.sh <edl.tsv> <voice_dir> <out_dir>
  # build_sample writes <out_dir>/adopted-timeline.tsv + produce-receipt.json, then audits them.
  # the delivery gate then re-runs the same audit:
  python3 <repo>/skills/gameplay-postproduction/scripts/check_postproduction.py ready sheet.md \
      --final-mp4 <out_dir>/final.mp4 --timeline <out_dir>/adopted-timeline.tsv \
      --preflight preflight.json --silence-ledger gaps.tsv --subtitle <out_dir>/subs.srt \
      --audio <out_dir>/voice_master.wav --receipt <out_dir>/produce-receipt.json --edl <edl.tsv>
DRAFTEOF
  echo "!! DRAFT ONLY：没有同时提供 TIMELINE / PREFLIGHT / SILENCE_LEDGER，" >&2
  echo "   本产物**未过采用时间线审计**，已写出 $OUT/DRAFT.txt；交付前必须过 ready 门槛。" >&2
fi
