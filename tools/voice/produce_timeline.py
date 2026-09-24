#!/usr/bin/env python3
"""把「输入 recipe + 真实 EDL + 实测结果」变成可交付的 adopted timeline 与 receipt。

这是 `tools/voice/build_sample.sh` 的**小配件**，不是通用渲染引擎。它只支持这个组装器
真正能做的事：单源视频、1x 速度、EDL 的 src_start/src_end/offset/freeze/字幕文本，
以及能把保持标注烧进画面的 freeze。

    plan   制作**前**：交叉核对 recipe ↔ EDL ↔ preflight ↔ 真实源视频。
           任一不符就非 0 退出（调用方据此保持 draft），不渲染、不猜。
    adopt  制作**后**：用实测时长/窗口 + 实际输出摘要，写出 adopted-timeline.tsv 与
           produce-receipt.json。**输入 recipe 允许占位摘要，adopted 输出不允许。**

为什么要它：一次首次制作不可能预先知道最终 MP4/音轨/字幕的摘要。让调用方去"先跑一遍拿摘要、
再填回 recipe、再跑一遍"就是重复渲染——这个脚本的存在就是为了让首次一次 build 就能交付。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

BASE_COLUMNS = ["asset_id", "event_id", "source_range", "clip_range", "final_range", "speed/freeze",
                "narration_text", "audio_duration_s", "subtitle_source"]
AUDIT_COLUMNS = ["event_phase", "claim_phase", "claim_mode", "audio_offset_s",
                 "anchor_asset", "anchor_event", "anchor_source", "evidence", "hold_mark",
                 "visible_window", "hold_burned_in",
                 "subtitle_sha256", "audio_sha256", "final_sha256"]
OUT_COLUMNS = BASE_COLUMNS + AUDIT_COLUMNS
HASH_COLUMNS = ("subtitle_sha256", "audio_sha256", "final_sha256")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RANGE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*$")
SF_RE = re.compile(r"^\s*(?P<speed>\d+(?:\.\d+)?)x\s*(?:\+\s*定格\s*(?P<freeze>\d+(?:\.\d+)?)\s*s)?\s*$")
TIMECODE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*s\b")
NULLISH = {"", "-", "n/a", "none", "null", "未记录", "–"}
TOL = 0.05
PLAN_COLUMNS = ["seg", "src_start", "src_end", "freeze", "offset", "window", "narration_duration",
                "subtitle_text", "burn_mark"] + OUT_COLUMNS


class Fail(SystemExit):
    """调用方据此保持 draft：明确失败，不静默降级。"""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_tsv(path: Path) -> tuple:
    if not path.is_file():
        raise Fail(f"找不到文件: {path}")
    lines = [ln.rstrip("\n") for ln in path.read_text(encoding="utf-8").splitlines()]
    data = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if not data:
        raise Fail(f"没有数据行: {path}")
    delim = "\t" if "\t" in data[0] else ("|" if "|" in data[0] else None)
    if delim is None:
        raise Fail(f"无法识别分隔符（TAB 或 |）: {path}")
    cells = [[c.strip() for c in ln.split(delim)] for ln in data]
    if delim == "|":
        cells = [([c for c in row if c != ""] if row and row[0] == "" else row) for row in cells]
    return cells[0], cells[1:]


def as_float(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def parse_range(value: str):
    m = RANGE_RE.match(value or "")
    if not m:
        return None
    a, b = as_float(m.group(1)), as_float(m.group(2))
    return None if a is None or b is None or a < 0 or b <= a else (a, b)


def is_placeholder(value: str) -> bool:
    return (value or "").strip().lower() in NULLISH or not SHA256_RE.match((value or "").strip())


def probe_duration(path: Path) -> float:
    if not path.is_file():
        raise Fail(f"找不到旁白音频: {path}")
    try:
        out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "default=nw=1:nk=1", str(path)],
                             capture_output=True, text=True, timeout=120).stdout.strip()
        dur = float(out)
    except Exception as exc:  # noqa: BLE001
        raise Fail(f"量不到旁白时长: {path}（{type(exc).__name__}）") from exc
    if not math.isfinite(dur) or dur <= 0:
        raise Fail(f"旁白时长非法: {path} -> {out!r}")
    return round(dur, 6)


def drawtext_available() -> bool:
    try:
        out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True,
                             timeout=60).stdout
    except Exception:  # noqa: BLE001
        return False
    return any(parts[1] == "drawtext" for parts in
               (ln.split() for ln in out.splitlines() if len(ln.split()) > 2))


def norm(text: str) -> str:
    return " ".join((text or "").split())


def read_edl(path: Path) -> list:
    header, rows = read_tsv(path)
    want = ["seg", "src_start", "src_end", "audio_file", "offset", "freeze", "subtitle_text"]
    if header != want:
        raise Fail(f"EDL 表头不符，期望 {want}，实际 {header}")
    out = []
    for row in rows:
        if len(row) != 7:
            raise Fail(f"EDL 行列数 {len(row)} != 7: {row}")
        seg, ss, se, afile, off, freeze, text = row
        ss_f, se_f, off_f, fr_f = (as_float(ss), as_float(se), as_float(off), as_float(freeze))
        if None in (ss_f, se_f, off_f, fr_f) or ss_f < 0 or se_f <= ss_f or off_f < 0 or fr_f < 0:
            raise Fail(f"EDL seg {seg}: 数值非法（src {ss}-{se}, offset {off}, freeze {freeze}）")
        out.append({"seg": seg, "src": (ss_f, se_f), "audio_file": afile, "offset": off_f,
                    "freeze": fr_f, "text": text})
    if not out:
        raise Fail("EDL 没有数据行")
    return out


def read_recipe(path: Path) -> tuple:
    header, rows = read_tsv(path)
    missing = [c for c in OUT_COLUMNS if c not in header]
    if missing:
        raise Fail(f"recipe 缺少必需列: {', '.join(missing)}")
    idx = {c: header.index(c) for c in OUT_COLUMNS}
    parsed = []
    for n, row in enumerate(rows, start=2):
        if len(row) < len(header):
            raise Fail(f"recipe 第 {n} 行列数不足")
        parsed.append({c: row[idx[c]] for c in OUT_COLUMNS})
    return header, parsed


# --------------------------------------------------------------------- plan

def cmd_plan(args) -> int:
    edl = read_edl(Path(args.edl))
    _hdr, recipe = read_recipe(Path(args.recipe))
    if len(recipe) != len(edl):
        raise Fail(f"recipe 有 {len(recipe)} 行，EDL 有 {len(edl)} 段 -> 两者不是同一次制作")

    pre = json.loads(Path(args.preflight).read_text(encoding="utf-8"))
    if not pre.get("ok", False):
        raise Fail("preflight 报告本身未通过 -> 先修输入状态")
    assets = pre.get("assets") or {}
    src_video = Path(args.src_video)
    if not src_video.is_file():
        raise Fail(f"找不到 SRC_VIDEO: {src_video}")
    src_sha = sha256_file(src_video)

    ids = {r["asset_id"].strip() for r in recipe}
    if len(ids) != 1:
        raise Fail(f"本组装器只支持单源视频，recipe 里出现了 {sorted(ids)}")
    asset_id = next(iter(ids))
    ledger = assets.get(asset_id)
    if ledger is None:
        raise Fail(f"recipe 使用素材 {asset_id}，但 preflight 台账里没有它")
    if str(ledger.get("sha256")) != src_sha:
        raise Fail(f"源不匹配：SRC_VIDEO 的摘要 {src_sha[:12]}… 与 preflight 里 {asset_id} 的 "
                   f"{str(ledger.get('sha256'))[:12]}… 不一致（同长不同画面也会在这里失败）")

    voice_dir = Path(args.voice_dir)
    out_dir = Path(args.out_dir) / "adopt"
    out_dir.mkdir(parents=True, exist_ok=True)
    have_drawtext = drawtext_available()
    plan_rows, notes = [], []

    for i, (r, e) in enumerate(zip(recipe, edl), start=1):
        seg = i
        if r["asset_id"].strip() != asset_id:
            raise Fail(f"seg {seg}: recipe 的 asset_id 与其它行不一致")
        src = parse_range(r["source_range"])
        if src is None:
            raise Fail(f"seg {seg}: recipe source_range 非法: {r['source_range']!r}")
        if abs(src[0] - e["src"][0]) > TOL or abs(src[1] - e["src"][1]) > TOL:
            raise Fail(f"seg {seg}: recipe 的源区间 {src[0]}-{src[1]}s 与 EDL 的 "
                       f"{e['src'][0]}-{e['src'][1]}s 不一致（同长不同位置也算不一致）")
        sf = SF_RE.match(r["speed/freeze"])
        if sf is None:
            raise Fail(f"seg {seg}: recipe speed/freeze 非法: {r['speed/freeze']!r}")
        speed, freeze = float(sf.group("speed")), float(sf.group("freeze") or 0.0)
        if abs(speed - 1.0) > 1e-9:
            raise Fail(f"seg {seg}: 本组装器只支持 1x（recipe 写了 {speed}x）；"
                       f"变速请用外部编辑器，不要假装支持")
        if abs(freeze - e["freeze"]) > TOL:
            raise Fail(f"seg {seg}: recipe 定格 {freeze}s 与 EDL {e['freeze']}s 不一致")
        if norm(r["narration_text"]) != norm(e["text"]):
            raise Fail(f"seg {seg}: recipe 口播文本与 EDL 字幕文本不一致\n"
                       f"    recipe: {r['narration_text']!r}\n    edl   : {e['text']!r}")
        measured = probe_duration(voice_dir / e["audio_file"])
        claimed = as_float(r["audio_duration_s"])
        if claimed is None or abs(claimed - measured) > TOL:
            raise Fail(f"seg {seg}: recipe 声长 {r['audio_duration_s']!r} 与实际 {measured}s 不一致")
        off = as_float(r["audio_offset_s"])
        if off is not None and abs(off - e["offset"]) > TOL:
            raise Fail(f"seg {seg}: recipe offset {off}s 与 EDL {e['offset']}s 不一致")
        off = e["offset"] if off is None else off
        window = (e["src"][1] - e["src"][0]) + freeze
        if off + measured > window + TOL:
            raise Fail(f"seg {seg}: offset {off} + 声长 {measured} 放不进窗口 {window}")

        burn = "-"
        if freeze > TOL:
            mark = r["hold_mark"].strip()
            if mark.lower() in NULLISH:
                raise Fail(f"seg {seg}: 有定格但 recipe 的 hold_mark 为空 -> 不许用无标注的长静帧")
            m = TIMECODE_RE.search(mark)
            if not m:
                raise Fail(f"seg {seg}: hold_mark 里读不出源时间码: {mark!r}")
            anchor = as_float(m.group(1))
            if anchor is None or abs(anchor - e["src"][1]) > TOL:
                raise Fail(
                    f"seg {seg}: 本组装器只能冻结片段末帧（源 {e['src'][1]}s），声明的锚点是 {anchor}s。"
                    f"要么把锚点改到片段末帧，要么用外部编辑器；这里**不会**假装锚点被尊重")
            if not have_drawtext:
                raise Fail(f"seg {seg}: 本机 ffmpeg 没有 drawtext 滤镜，无法把保持标注烧进画面。"
                           f"去掉这一段定格，或改用外部编辑器 —— 表格里有 mark 不等于画面已标注")
            burn = mark
            (out_dir / f"mark-{seg:02d}.txt").write_text(mark, encoding="utf-8")

        plan_rows.append([str(seg), f"{e['src'][0]}", f"{e['src'][1]}", f"{freeze}", f"{off}",
                          f"{window}", f"{measured}", e["text"], burn]
                         + [r[c] for c in OUT_COLUMNS])
        notes.append(f"seg {seg:>2}: src {e['src'][0]}-{e['src'][1]}s window {window}s "
                     f"offset {off} 声长 {measured}s freeze {freeze}s burn={burn != '-'}")

    plan_path = out_dir / "plan.tsv"
    plan_path.write_text("\t".join(PLAN_COLUMNS) + "\n"
                         + "".join("\t".join(row) + "\n" for row in plan_rows), encoding="utf-8")
    print("制作前交叉核对通过（recipe ↔ EDL ↔ preflight ↔ SRC_VIDEO）")
    for note in notes:
        print(f"  {note}")
    print(f"  plan: {plan_path}")
    print(f"  drawtext: {'可用（支持的 freeze 会真的烧进画面）' if have_drawtext else '不可用'}")
    return 0


# --------------------------------------------------------------------- adopt

def cmd_adopt(args) -> int:
    plan_path = Path(args.out_dir) / "adopt" / "plan.tsv"
    header, plan = read_tsv(plan_path)
    idx = {c: header.index(c) for c in PLAN_COLUMNS}
    measured_header, measured = read_tsv(Path(args.measured))
    m_idx = {c: measured_header.index(c) for c in ("seg", "window", "narration_duration", "offset",
                                                   "final_start", "final_end")}
    by_seg = {row[m_idx["seg"]]: row for row in measured}
    if len(by_seg) != len(plan):
        raise Fail(f"实测段数 {len(by_seg)} != plan 段数 {len(plan)}")

    subs, audio, final = Path(args.subs), Path(args.audio), Path(args.final)
    for label, path in (("字幕", subs), ("音轨", audio), ("成片", final)):
        if not path.is_file():
            raise Fail(f"adopt: {label}不存在: {path}")
    hashes = {"subtitle_sha256": sha256_file(subs), "audio_sha256": sha256_file(audio),
              "final_sha256": sha256_file(final)}
    edl_path, src_video = Path(args.edl), Path(args.src_video)
    if not edl_path.is_file() or not src_video.is_file():
        raise Fail("adopt: EDL 或 SRC_VIDEO 不存在")

    rows, clip_cursor = [], 0.0
    for row in plan:
        seg = row[idx["seg"]]
        m = by_seg.get(seg)
        if m is None:
            raise Fail(f"adopt: 实测里没有 seg {seg}")
        ss, se = float(row[idx["src_start"]]), float(row[idx["src_end"]])
        freeze, off = float(row[idx["freeze"]]), float(row[idx["offset"]])
        window = float(m[m_idx["window"]])
        adur = float(m[m_idx["narration_duration"]])
        f0, f1 = float(m[m_idx["final_start"]]), float(m[m_idx["final_end"]])
        if abs((f1 - f0) - window) > TOL:
            raise Fail(f"adopt: seg {seg} 实测窗口 {f1 - f0}s != plan {window}s")
        src_len = se - ss
        values = {
            "asset_id": row[idx["asset_id"]], "event_id": row[idx["event_id"]],
            "source_range": f"{ss}-{se}", "clip_range": f"{clip_cursor}-{clip_cursor + src_len}",
            "final_range": f"{f0}-{f1}",
            "speed/freeze": "1x" if freeze <= TOL else f"1x + 定格{freeze:g}s",
            "narration_text": row[idx["narration_text"]], "audio_duration_s": f"{adur}",
            "subtitle_source": row[idx["subtitle_source"]], "audio_offset_s": f"{off}",
            "hold_burned_in": "yes" if row[idx["burn_mark"]].strip().lower() not in NULLISH else "no",
        }
        for c in ("event_phase", "claim_phase", "claim_mode", "anchor_asset", "anchor_event",
                  "anchor_source", "evidence", "hold_mark", "visible_window"):
            values[c] = row[idx[c]]
        values.update(hashes)
        leftover = [c for c in HASH_COLUMNS if is_placeholder(values[c])]
        if leftover:
            raise Fail(f"adopt: seg {seg} 的 adopted 输出仍有占位摘要 {leftover} -> 不许交付")
        rows.append([values[c] for c in OUT_COLUMNS])
        clip_cursor += src_len

    adopted = Path(args.out_dir) / "adopted-timeline.tsv"
    adopted.write_text("\t".join(OUT_COLUMNS) + "\n"
                       + "".join("\t".join(r) + "\n" for r in rows), encoding="utf-8")

    receipt = {
        "schema": "gameplay-postproduction/produce-receipt/1",
        "produced_by": "tools/voice/build_sample.sh",
        "produced_at": args.produced_at,
        "timeline": {"path": str(adopted), "sha256": sha256_file(adopted)},
        # **实际读过的源**：SRC_VIDEO 的真实摘要；plan 已经证明它 == 台账里那个 asset
        "source_video": {"path": str(src_video), "sha256": sha256_file(src_video)},
        "edl": {"path": str(edl_path), "sha256": sha256_file(edl_path)},
        "sources": {rows[0][0]: sha256_file(src_video)},
        "subtitle": {"path": str(subs), "sha256": hashes["subtitle_sha256"]},
        "audio": {"path": str(audio), "sha256": hashes["audio_sha256"]},
        "final": {"path": str(final), "sha256": hashes["final_sha256"]},
    }
    receipt_path = Path(args.out_dir) / "produce-receipt.json"
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"adopted timeline: {adopted}（{len(rows)} 段，摘要已按实际产物填实）")
    print(f"produce receipt : {receipt_path}")
    return 0


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="produce_timeline.py",
                                 description="recipe+EDL+实测 -> adopted timeline 与 receipt")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("plan", help="制作前交叉核对，并可产出保持标注文本")
    p.add_argument("--recipe", required=True)
    p.add_argument("--edl", required=True)
    p.add_argument("--preflight", required=True)
    p.add_argument("--src-video", required=True)
    p.add_argument("--voice-dir", required=True)
    p.add_argument("--out-dir", required=True)

    a = sub.add_parser("adopt", help="制作后写 adopted timeline 与 receipt")
    a.add_argument("--out-dir", required=True)
    a.add_argument("--measured", required=True)
    a.add_argument("--edl", required=True)
    a.add_argument("--src-video", required=True)
    a.add_argument("--subs", required=True)
    a.add_argument("--audio", required=True)
    a.add_argument("--final", required=True)
    a.add_argument("--produced-at", required=True)

    args = ap.parse_args(argv)
    try:
        return cmd_plan(args) if args.cmd == "plan" else cmd_adopt(args)
    except Fail as exc:
        print(f"!! {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
