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

echo "── 1/3 postproduction checker（结构 / 就绪门槛 / 反例）"
python3 "$ROOT/tests/test_check_postproduction.py" || fail=1

echo
echo "── 2/3 voice assembly smoke（合成素材，真 ffmpeg 组装）"
python3 "$ROOT/tests/test_build_sample.py" || fail=1

echo
echo "── 3/3 TTS 硬保证（本地假端点，不出网：不改稿 / 空音频失败 / 测不出时长失败）"
python3 "$ROOT/tests/test_tts_guarantees.py" || fail=1

echo
if [ "$fail" -eq 0 ]; then echo "═══ 离线测试全部通过 ═══"; else echo "═══ 有失败项 ═══" >&2; fi
exit "$fail"
