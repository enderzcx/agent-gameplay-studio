#!/usr/bin/env bash
# 兼容转发 —— 旧的 record-game.sh 入口。
#
# **这里不再有第二份实现。** 录制器的唯一源码在 agent-capture：
#     <agent-capture>/tools/macos/main.swift
#     <agent-capture>/tools/macos/record-game.sh
#
# 本脚本只做一件事：把调用原样转发过去，让旧的命令行继续能用。
# 双维护是刻意的反面教材：两份实现会在"哪一份才是真的"上产生分歧，
# 而分歧在录制这种"看起来都成功"的场景里最难发现。
#
#   GAME_BUNDLE_ID=<id> ./record-game.sh preflight
#   GAME_BUNDLE_ID=<id> ./record-game.sh start --out x.mp4 --duration 60
#   ./record-game.sh verify x.mp4
#
# 目标仍由 GAME_BUNDLE_ID 指定（旧约定不变）。
#
# 更完整的入口（单窗口、画面与音频分开指定、持久 run 状态、后台 worker）请直接用：
#   python3 <agent-capture>/scripts/agent_capture.py --help
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 依次尝试：环境变量 → 安装态快照 → 同级仓库检出
find_target() {
  if [ -n "${AGENT_CAPTURE_ROOT:-}" ] && [ -x "$AGENT_CAPTURE_ROOT/tools/macos/record-game.sh" ]; then
    printf '%s\n' "$AGENT_CAPTURE_ROOT/tools/macos/record-game.sh"; return 0
  fi
  local base="${TOOLS_HOME:-$HOME/.local/share}/agent-capture"
  if [ -d "$base" ]; then
    local newest
    newest="$(ls -1dt "$base"/*/tools/macos/record-game.sh 2>/dev/null | head -1 || true)"
    [ -n "$newest" ] && { printf '%s\n' "$newest"; return 0; }
  fi
  local sib="$HERE/../../../agent-capture/tools/macos/record-game.sh"
  [ -x "$sib" ] && { printf '%s\n' "$sib"; return 0; }
  return 1
}

if ! TARGET="$(find_target)"; then
  cat >&2 <<'EOF'
✗ 找不到 agent-capture 的录制器实现 —— 本脚本只是转发，不内置第二份实现。

请任选一种方式让它可被发现：
  1) 设 AGENT_CAPTURE_ROOT=/path/to/agent-capture
  2) 安装到 ~/.local/share/agent-capture/<版本>/（跑 agent-capture 的 install.sh）
  3) 把 agent-capture 仓库检出在与本仓库同级的目录

（本仓库不再维护录制器源码：唯一实现在 agent-capture/tools/macos/。）
EOF
  exit 3
fi

exec bash "$TARGET" "$@"
