#!/usr/bin/env bash
# Offline test entry point. Nothing here needs network, credentials, a game, a display,
# or any path outside this repository. Dependencies: python3, ffmpeg, ffprobe.
#
#   ./tests/run_offline_tests.sh
#
# For the macOS recorder suite see tools/gamerec/tests/regression.sh (its offline group runs
# anywhere; its permission group needs Screen Recording access and a real target app).
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
fail=0

need() { command -v "$1" >/dev/null 2>&1 || { echo "缺少依赖：$1" >&2; exit 2; }; }
need python3
need ffmpeg
need ffprobe

echo "── 1/5 postproduction checker（结构 / 就绪门槛 / 反例）"
python3 "$ROOT/tests/test_check_postproduction.py" || fail=1

echo
echo "── 2/5 input preflight + adopted-timeline audit（阶段锚点 / 保持帧 / 静默依据 / stale 台账）"
python3 "$ROOT/tests/test_timeline_audit.py" || fail=1

echo
echo "── 3/5 字幕分页与时基（短语分页 / ASS 进位 / PlayRes = 视频尺寸）"
python3 "$ROOT/tests/test_subtitles.py" || fail=1

echo
echo "── 4/5 voice assembly smoke（合成素材，真 ffmpeg：组装 / 烧字幕 / 保留原声）"
python3 "$ROOT/tests/test_build_sample.py" || fail=1

echo
echo "── 5/5 TTS 硬保证（本地假端点，不出网：不改稿 / 空音频失败 / 测不出时长失败 / 只重算改变节点）"
python3 "$ROOT/tests/test_tts_guarantees.py" || fail=1

echo
if [ "$fail" -eq 0 ]; then echo "═══ 离线测试全部通过 ═══"; else echo "═══ 有失败项 ═══" >&2; fi
exit "$fail"
