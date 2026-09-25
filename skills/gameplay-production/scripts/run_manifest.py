#!/usr/bin/env python3
"""run_manifest — 一次"录制 → 对局 → 后期"的**统一交接凭证**。

它回答一个具体问题：**这一局的哪一段是哪一段**。
所以它用**一个 run_id** 把所有东西串起来：

    run_id ──┬── capture : 原片 / 指标 / 焦点日志 / 采集结论
             ├── game    : 对局结果 / 证据
             └── post    : 成片 / 审片结论

## 三条硬规则（都在代码里，不只是文档）

1. **录制有效开始之后才允许记对局结果。**
   `set-game` 会先检查 capture 阶段是不是 `pass`；不是就拒绝。
   没有这条，"对局结果"可能挂在一段根本没录上的录像上。

2. **三个结论互相独立，谁也不给谁背书。**
   `capture` / `game` / `postproduction` 各自有 `result`（pass/fail/unknown）。
   缺结论 = `unknown`，**不是** pass。合成出的 `all_known` 为假时，
   调用方不能对外说"这一局完整跑通了"。

3. **后期不能反向影响对局。**
   `set-post` 只写 postproduction 段，**永远不碰** game 段。
   审片发现的问题只能改成片，不能改"当时对局发生了什么"。
   这条是评审公平性的底线：后期不得反向提示参赛玩家。

## 状态机（只允许这些转移）

    draft → capturing → captured → playing → played → postproducing → delivered
                     ↘ failed（任一步都可失败；失败**只重试对应阶段**，不重开新局）

"异常只重试相应阶段"的落地方式：每个阶段有独立的 `result` 与 `attempts`。
重新跑 verify 只会让 capture.attempts+1，**不会**新建 run_id、不会重开一局。

## CLI

    run_manifest.py init --manifest P --run-id R --game <名字>
    run_manifest.py set-capture --manifest P --result pass|fail|unknown [--media P] [--metrics P] [--note S]
    run_manifest.py set-game    --manifest P --result pass|fail|unknown [--note S]
    run_manifest.py set-post    --manifest P --result pass|fail|unknown [--cut P] [--note S]
    run_manifest.py show        --manifest P
    run_manifest.py verify      --manifest P     # 内部一致性自检
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

RESULTS = ("pass", "fail", "unknown")

# 只允许这些转移；失败**不**回到起点，而是留在原地等重试对应阶段
ALLOWED: Dict[str, List[str]] = {
    "draft": ["capturing", "failed"],
    "capturing": ["captured", "failed"],
    "captured": ["playing", "failed"],
    "playing": ["played", "failed"],
    "played": ["postproducing", "failed"],
    "postproducing": ["delivered", "failed"],
    "delivered": [],
    "failed": ["capturing", "captured", "playing", "played", "postproducing"],
}

SETTABLE = {"capture", "game", "postproduction"}
PROTECTED = {"schema_version", "run_id", "state", "created_at", "updated_at", "game_name"}


class ManifestError(RuntimeError):
    pass


def _check_finite(obj: Any, where: str) -> None:
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ManifestError(f"{where} 含非有限数值 {obj!r}")
    elif isinstance(obj, dict):
        for k, v in obj.items():
            _check_finite(v, f"{where}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _check_finite(v, f"{where}[{i}]")


def validate_run_id(rid: str) -> str:
    if not isinstance(rid, str) or not RUN_ID_RE.match(rid or ""):
        raise ManifestError(f"run_id 非法: {rid!r}（只允许 [A-Za-z0-9._-]，字母数字开头，≤64）")
    return rid


def _atomic_write(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
    fd, tmp = tempfile.mkstemp(prefix=".manifest-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, str(path))
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def load(path: Path) -> Dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ManifestError(f"manifest 不存在: {path}")
    except json.JSONDecodeError as exc:
        raise ManifestError(f"manifest 损坏: {path}: {exc}")
    if not isinstance(raw, dict):
        raise ManifestError("manifest 必须是 JSON 对象")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(f"schema 不匹配：文件={raw.get('schema_version')!r} "
                            f"本工具={SCHEMA_VERSION}")
    if raw.get("state") not in ALLOWED:
        raise ManifestError(f"未知 state {raw.get('state')!r}")
    validate_run_id(raw.get("run_id"))
    _check_finite(raw, "manifest")
    return raw


def init_manifest(path: Path, run_id: str, game_name: str) -> Dict[str, Any]:
    validate_run_id(run_id)
    if path.exists():
        raise ManifestError(f"manifest 已存在，拒绝覆盖: {path}（换 run_id 或换路径）")
    now = time.time()
    m = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "game_name": game_name,
        "state": "draft",
        "created_at": now,
        "updated_at": now,
        # 三段互相独立，谁也不给谁背书
        "capture": {"result": "unknown", "attempts": 0},
        "game": {"result": "unknown", "attempts": 0},
        "postproduction": {"result": "unknown", "attempts": 0},
        "history": [{"at": now, "from": None, "to": "draft", "note": "created"}],
    }
    _atomic_write(path, m)
    return m


def _advance(m: Dict[str, Any], to: str, note: str = "") -> None:
    cur = m["state"]
    if to == cur:
        return
    if to not in ALLOWED.get(cur, []):
        raise ManifestError(f"非法状态转移 {cur} → {to}"
                            f"（允许：{ALLOWED.get(cur) or '无，终态'}）")
    m["state"] = to
    m["updated_at"] = time.time()
    m["history"].append({"at": m["updated_at"], "from": cur, "to": to, "note": note})


def set_stage(path: Path, stage: str, result: str, note: str = "",
              **extra: Any) -> Dict[str, Any]:
    """写一个阶段的结论。**只碰这一段**，不碰其它段。"""
    if stage not in SETTABLE:
        raise ManifestError(f"未知阶段 {stage!r}；可写：{sorted(SETTABLE)}")
    if result not in RESULTS:
        raise ManifestError(f"result 必须是 {RESULTS} 之一，收到 {result!r}")
    m = load(path)

    if stage == "game":
        # **硬规则 1**：录制有效开始（capture=pass）之后才允许记对局结果
        if result in ("pass", "fail") and m["capture"].get("result") != "pass":
            raise ManifestError(
                "拒绝记录对局结果：capture 阶段还不是 pass"
                f"（当前={m['capture'].get('result')!r}）。\n"
                "先让录制真的有效开始并校验通过，否则'对局结果'会挂在一段"
                "根本没录上的录像上。"
            )
        _advance(m, "playing" if m["state"] in ("captured", "failed") else m["state"],
                 "game result")
        if m["state"] == "playing":
            _advance(m, "played", "game result recorded")

    if stage == "postproduction":
        if result in ("pass", "fail") and m["game"].get("result") == "unknown" \
                and m["capture"].get("result") != "pass":
            raise ManifestError(
                "拒绝记录后期结论：既没有 capture=pass，也没有对局结论 —— "
                "后期不能凭空产生。"
            )
        if m["state"] == "played":
            _advance(m, "postproducing", "postproduction started")
        if result == "pass" and m["state"] == "postproducing":
            _advance(m, "delivered", "cut delivered")

    if stage == "capture":
        if m["state"] == "draft":
            _advance(m, "capturing", "capture started")
        if result in ("pass", "fail") and m["state"] == "capturing":
            _advance(m, "captured" if result == "pass" else "failed", f"capture={result}")

    sec = m[stage]
    sec["result"] = result
    # attempts 记录"这一段被重试了几次" —— 异常只重试相应阶段，不重开新局
    sec["attempts"] = int(sec.get("attempts") or 0) + 1
    if note:
        sec["note"] = note
    for k, v in extra.items():
        if v in (None, ""):
            continue
        if k in PROTECTED:
            raise ManifestError(f"字段 {k!r} 受保护，不能由阶段更新写入")
        _check_finite(v, k)
        sec[k] = v
    sec["updated_at"] = time.time()
    m["updated_at"] = sec["updated_at"]
    m["history"].append({"at": m["updated_at"], "from": m["state"], "to": m["state"],
                         "note": f"{stage}={result}"})
    _atomic_write(path, m)
    return m


def summarize(m: Dict[str, Any]) -> Dict[str, Any]:
    def tri(sec: Dict[str, Any]) -> str:
        r = (sec or {}).get("result")
        return r if r in RESULTS else "unknown"

    c, g, p = tri(m["capture"]), tri(m["game"]), tri(m["postproduction"])
    return {
        "run_id": m["run_id"],
        "game_name": m.get("game_name", ""),
        "state": m["state"],
        # 三个**分开报**：录像完整 ≠ 游戏有结果；游戏有结果 ≠ 后期能开始
        "capture_integrity": c,
        "game_result": g,
        "postproduction_ready": p,
        "all_known": all(x != "unknown" for x in (c, g, p)),
        "attempts": {k: int((m.get(k) or {}).get("attempts") or 0)
                     for k in ("capture", "game", "postproduction")},
    }


def self_check(m: Dict[str, Any]) -> List[str]:
    """内部一致性自检。返回问题列表；非空即不合格。"""
    problems: List[str] = []
    rid = m["run_id"]

    # 只检查**路径类字段**。早期版本对所有含 "/" 的字符串都查 run_id，
    # 于是把 note 里的自由文本（"e4/Nf3/Bc4"、"8/8"）误报成"可能串档" ——
    # 假阳性会让自检失去意义（人就开始忽略它）。
    PATH_FIELDS = {"capture": ("media", "metrics", "focus_log"),
                   "postproduction": ("cut", "review")}
    for stage, keys in PATH_FIELDS.items():
        sec = m.get(stage) or {}
        for k in keys:
            v = sec.get(k)
            if isinstance(v, str) and v and rid not in v:
                problems.append(
                    f"{stage}.{k} 的文件名里不含 run_id（成片/素材必须能被 run_id 串起来）: {v}"
                )

    if m["game"].get("result") in ("pass", "fail") and m["capture"].get("result") != "pass":
        problems.append("有对局结论但 capture 不是 pass（违反'录制有效开始后才能开局'）")
    if m["postproduction"].get("result") == "pass" and m["game"].get("result") == "unknown" \
            and m["capture"].get("result") != "pass":
        problems.append("有后期结论但既没有 capture=pass 也没有对局结论")
    if m["state"] == "delivered" and m["postproduction"].get("result") != "pass":
        problems.append("state=delivered 但 postproduction 不是 pass")
    return problems


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="run_manifest",
                                 description="录制→对局→后期的统一交接凭证")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("init")
    p.add_argument("--manifest", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--game", default="")

    for name, stage in (("set-capture", "capture"), ("set-game", "game"),
                        ("set-post", "postproduction")):
        p = sub.add_parser(name)
        p.add_argument("--manifest", required=True)
        p.add_argument("--result", required=True, choices=list(RESULTS))
        p.add_argument("--note", default="")
        if stage == "capture":
            p.add_argument("--media", default="")
            p.add_argument("--metrics", default="")
            p.add_argument("--focus-log", default="")
        if stage == "postproduction":
            p.add_argument("--cut", default="")
            p.add_argument("--review", default="")

    p = sub.add_parser("show"); p.add_argument("--manifest", required=True)
    p = sub.add_parser("verify"); p.add_argument("--manifest", required=True)

    a = ap.parse_args(argv)
    path = Path(a.manifest)
    try:
        if a.cmd == "init":
            m = init_manifest(path, a.run_id, a.game)
            print(json.dumps(summarize(m), ensure_ascii=False, indent=2))
        elif a.cmd == "set-capture":
            m = set_stage(path, "capture", a.result, a.note,
                          media=a.media, metrics=a.metrics, focus_log=a.focus_log)
            print(json.dumps(summarize(m), ensure_ascii=False, indent=2))
        elif a.cmd == "set-game":
            m = set_stage(path, "game", a.result, a.note)
            print(json.dumps(summarize(m), ensure_ascii=False, indent=2))
        elif a.cmd == "set-post":
            m = set_stage(path, "postproduction", a.result, a.note,
                          cut=a.cut, review=a.review)
            print(json.dumps(summarize(m), ensure_ascii=False, indent=2))
        elif a.cmd == "show":
            m = load(path)
            print(json.dumps({"summary": summarize(m), "manifest": m},
                             ensure_ascii=False, indent=2))
        elif a.cmd == "verify":
            m = load(path)
            problems = self_check(m)
            print(json.dumps({"ok": not problems, "problems": problems,
                              "summary": summarize(m)},
                             ensure_ascii=False, indent=2))
            return 0 if not problems else 2
    except ManifestError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
