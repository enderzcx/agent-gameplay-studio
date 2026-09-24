#!/usr/bin/env python3
"""find_tools.py — 找到本包的 tools/voice（组装器/字幕/像素抽检）在哪。

为什么需要：skill 的安装入口通常只软链 `skills/<name>/`，而 `tools/voice/` 与它**不是父子关系**
（标准快照布局是 `<root>/skills/gameplay-postproduction/…` + `<root>/tools/voice/…`）。
调用方（尤其是宿主里的 Agent）据此猜路径会猜错，然后开始自己手写脚本 —— 这正是要避免的。

用法:
  python3 find_tools.py            # 打印 tools/voice 的绝对路径（找不到则非 0 退出）
  python3 find_tools.py --json     # 机器可读
  python3 find_tools.py --check    # 顺带确认 make_cut.sh / build_sample.sh 在

查找顺序（全部是**存在性**检查，不猜）：
  1. $GAMEPLAY_TOOLS 环境变量（显式覆盖）
  2. <skill_dir>/../tools/voice
  3. <skill_dir>/../../tools/voice          ← 标准快照布局
  4. 从当前工作目录逐级向上找 tools/voice/make_cut.sh
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent          # <skill>/scripts
SKILL = HERE.parent                              # <skill>
NEEDED = ("make_cut.sh", "build_sample.sh", "subtitles.py", "burn_subs.py", "check_burned_subs.py")


def candidates() -> list[Path]:
    out = []
    env = os.environ.get("GAMEPLAY_TOOLS")
    if env:
        out.append(Path(env).expanduser())
    out.append(SKILL / "tools" / "voice")
    out.append(SKILL.parent / "tools" / "voice")
    out.append(SKILL.parent.parent / "tools" / "voice")
    cur = Path.cwd().resolve()
    for d in (cur, *cur.parents):
        out.append(d / "tools" / "voice")
    return out


def find() -> Path | None:
    for c in candidates():
        if (c / "make_cut.sh").is_file():
            return c.resolve()
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true", help="确认关键脚本都在")
    a = ap.parse_args()
    found = find()
    if found is None:
        msg = ("找不到 tools/voice。它不是 skill 目录的子目录：\n"
               "  仓库/快照布局：<root>/skills/gameplay-postproduction/… 与 <root>/tools/voice/…\n"
               "  解决：clone 本仓库后用它；或设 GAMEPLAY_TOOLS=<你的 tools/voice>；\n"
               "  或用安装脚本把 <root>/tools 一起放到本地快照目录。\n"
               "  **不要**因为找不到就自己手写一套组装/烧字幕脚本——那会绕过 adopted-timeline 审计。")
        if a.json:
            print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
        else:
            print(msg, file=sys.stderr)
        return 2
    missing = [n for n in NEEDED if not (found / n).is_file()] if a.check else []
    if a.json:
        print(json.dumps({"ok": not missing, "tools_voice": str(found),
                          "missing": missing, "skill_dir": str(SKILL)}, ensure_ascii=False))
    else:
        print(found)
        if missing:
            print(f"!! 缺少：{', '.join(missing)}", file=sys.stderr)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
