#!/usr/bin/env bash
# make_cut.sh — 一句话路径的**一次调用**封装：预检 → 决定原声 → 烧字幕 → 交审计。
#
# 为什么要有它：`build_sample.sh` 是底层组装器（默认不烧字幕、默认丢掉源原声），
# 直接调它很容易得到"没字幕 / 丢了原声"的 draft，然后被当成完成。
# 这里把**交付默认值**定死，并让每一步留下可读的结论：
#
#   · 先 preflight（音轨存在性 / 静默 / 采集密度），探测不出来就停；
#   · 源片**确实有原声** → 默认保留并与旁白分轨混音（duck）；显式 `SRC_AUDIO=drop` 才丢；
#   · 源片**确实无声** → 明确记进 SOURCE-AUDIO.txt 并继续（不因为没原声就停掉整个制作）；
#   · **BURN_SUBS=1 永远是默认**：交付物是带字幕的那一版；
#   · 最后仍然由 `build_sample.sh` 走 adopted-timeline 审计；不过审计就是 draft。
#
# 用法:
#   SRC_VIDEO=/abs/rec.mp4 TIMELINE=recipe.tsv PREFLIGHT=preflight.json SILENCE_LEDGER=gaps.tsv \
#     make_cut.sh <edl.tsv> <voice_dir> <out_dir>
#
# 可选环境变量：
#   SRC_AUDIO=keep|drop   源片有原声时保留（默认）还是丢掉
#   SRC_AUDIO_GAIN/-8     原声增益 dB        SRC_AUDIO_DUCK=1  旁白说话时压低原声
#   SUB_*                 字幕样式（见 build_sample.sh）
#   SKIP_PREFLIGHT=1      已知素材状态、跳过预检（不推荐：预检是"能不能做"的第一道门）
set -uo pipefail

TL="${1:?edl tsv}"; VDIR="${2:?voice dir}"; OUT="${3:?out dir}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD="$HERE/build_sample.sh"
AUDIT="$HERE/../../skills/gameplay-postproduction/scripts/check_timeline_audit.py"
SRC="${SRC_VIDEO:?必须显式给源视频：SRC_VIDEO=/path/to/source.mp4}"
SRC_AUDIO="${SRC_AUDIO:-keep}"

[ -f "$BUILD" ] || { echo "找不到 build_sample.sh: $BUILD" >&2; exit 2; }
command -v ffprobe >/dev/null || { echo "需要 ffprobe" >&2; exit 2; }
mkdir -p "$OUT"
OUT="$(cd "$OUT" && pwd)"

# ---------------------------------------------------------------- 1) 输入预检
if [ "${SKIP_PREFLIGHT:-0}" != "1" ] && [ -f "$AUDIT" ] && [ -z "${PREFLIGHT:-}" ]; then
  echo "--- preflight（音轨/静默/采集密度：探测不出来就是 undetermined，直接停）---"
  if ! python3 "$AUDIT" preflight --json --out "$OUT/preflight.json" "src=$SRC"; then
    echo "!! 预检没通过（undetermined 不算通过）。先解决素材状态，不要往下渲染。" >&2
    exit 2
  fi
  PREFLIGHT="$OUT/preflight.json"
  export PREFLIGHT
fi

# ---------------------------------------------------------------- 2) 原声：有就保留，没有就记下来
KEEP=0
probe_rc=0
probe_out="$(ffprobe -v error -select_streams a:0 -show_entries stream=codec_type -of csv=p=0 "$SRC" 2>/dev/null </dev/null)" || probe_rc=$?
if [ "$probe_rc" -ne 0 ]; then
  echo "!! ffprobe 探测源音轨失败（退出码 ${probe_rc}）：无法判断有没有原声，拒绝猜。" >&2
  exit 2
fi
if [ -z "$probe_out" ]; then
  {
    echo "源片没有音轨（探测成功、零个音频流）。"
    echo "成片音轨将**只来自旁白**；没有游戏原声可混，也没有伪造。"
    echo "source=$SRC"
  } > "$OUT/SOURCE-AUDIO.txt"
  echo "源无音轨 → 成片音轨只来自旁白（已记入 $OUT/SOURCE-AUDIO.txt）"
elif [ "$SRC_AUDIO" = "keep" ]; then
  KEEP=1
  {
    echo "源片有音轨 → 按同一 EDL 切出原声，与旁白分轨混音（gain=${SRC_AUDIO_GAIN:--8}dB duck=${SRC_AUDIO_DUCK:-1}）。"
    echo "注：这里只证明『混音这条链跑通了』，不证明听感；游戏原声是否真的在该出现的位置，需要人听。"
    echo "source=$SRC"
  } > "$OUT/SOURCE-AUDIO.txt"
  echo "源有原声 → 保留并与旁白分轨混音（可用 SRC_AUDIO=drop 明确丢弃）"
else
  {
    echo "源片有音轨，但调用方显式 SRC_AUDIO=drop：成片音轨只来自旁白。"
    echo "source=$SRC"
  } > "$OUT/SOURCE-AUDIO.txt"
  echo "源有原声，但调用方要求丢弃（SRC_AUDIO=drop）"
fi

# ---------------------------------------------------------------- 3) 组装 + 烧字幕 + 审计
BURN_SUBS=1 KEEP_SRC_AUDIO="$KEEP" bash "$BUILD" "$TL" "$VDIR" "$OUT"
rc=$?
if [ "$rc" -ne 0 ]; then
  echo "!! 交付路径未通过（退出码 ${rc}）：产物是 draft，不是完成。" >&2
fi
exit "$rc"
