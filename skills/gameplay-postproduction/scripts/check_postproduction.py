#!/usr/bin/env python3
"""gameplay-postproduction 产物检查器（确定性，不调用任何模型）。

**四种模式，语义不同，不要混用**：

    structure（结构）  timeline | units | sheet
        只验证"字段齐全 + 算术自洽 + 章节完整"。**不代表审片通过，也不代表成片可用。**
        空的 review-sheet 模板也能通过 structure —— 这是设计如此。

    readiness（就绪门槛）  ready
        在 structure 之上，要求：表头声明已填、没有未填占位符、结论已写、
        并且**独立探测实际导出的 MP4**（不是采信表里写的数字）。
        **还要过一遍采用时间线的语义审计**（`--timeline` + `--preflight` + 静默台账 +
        字幕 + 音轨，见 `check_timeline_audit.py`）：阶段锚点、保持帧标注与可见区间、
        旁白是否放得进窗口、长静默依据、版本绑定、stale 素材台账。
        仍然**不判断画面好不好、不听音轨**——它只回答"能不能把这份单子当作已审片交付"。

用法：

    S=~/.agents/skills/gameplay-postproduction
    python3 "$S/scripts/check_postproduction.py" timeline "timeline.tsv"   # TSV（TAB 或 | 自动识别）
    python3 "$S/scripts/check_postproduction.py" units    "units.tsv"      # TSV（见 templates/commentary-unit.md）
    python3 "$S/scripts/check_postproduction.py" sheet    "review-sheet.md" # Markdown
    python3 "$S/scripts/check_postproduction.py" ready    "review-sheet.md" --final-mp4 "/abs/final.mp4" \\
        --timeline timeline.tsv --preflight preflight.json --silence-ledger gaps.tsv \\
        --subtitle subs.srt --audio voice_master.wav

退出码：0=该模式通过，1=有缺陷/未就绪，2=用法或读取错误。
`--json` 输出结构化结果（供测试与自动化使用）。

设计原则：只做**机器可判定**的检查。文本相同、缺证据、没有真值一类情况一律**不宣判**，
只按可证的来源字段或明确占位符判定。
"""
from __future__ import annotations

import argparse
import json
import re
import math
import shutil
import subprocess
import sys
from pathlib import Path

TOL = 0.05
DEFAULT_AUDIT_TOL = 0.25
RANGE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*$")
SF_RE = re.compile(r"^\s*(?P<speed>\d+(?:\.\d+)?)x\s*(?:\+\s*定格\s*(?P<freeze>\d+(?:\.\d+)?)\s*s)?\s*$")
PLACEHOLDER_RE = re.compile(r"_{3,}|\bTBD\b|\bTODO\b")

BASE_COLUMNS = ["asset_id", "event_id", "source_range", "clip_range", "final_range",
                "speed/freeze", "narration_text", "audio_duration_s", "subtitle_source"]
TIMELINE_COLUMNS = BASE_COLUMNS
UNITS_COLUMNS = ["event_id", "source_range", "state", "action", "stated_reason",
                 "retrospective_commentary", "outcome", "coverage"]
UNITS_OPTIONAL_COLUMNS = ["reason_source"]
UNIT_COVERAGE_VALUES = {"ok", "na", "coverage_gap"}
SHEET_HEADINGS = ["## A.", "## B.", "## C.", "## D.", "## E.", "## F.", "## G.", "## H."]
ISSUE_COLUMNS = ["issue_id", "event_id", "asset_id", "源区间", "成片区间",
                 "旁白主张", "实际画面", "证据"]
NULLISH = {"null", "none", "未记录", "n/a", "-", "–", ""}
# 只有**来源字段明确写出来**才判定为事后回填（文本相同本身不构成证据）
RETRO_SOURCE_MARKERS = ("事后", "复盘", "回填", "retro", "hindsight", "post-hoc", "posthoc")
# 允许的"当时理由"来源标记
STATEMENT_SOURCE_MARKERS = ("当时", "口播", "现场", "live", "contemporaneous", "transcript", "实录")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.checked = 0

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def payload(self, mode: str, target: str, *, semantics: str) -> dict:
        return {"mode": mode, "target": target, "semantics": semantics,
                "ok": not self.errors, "checked_rows": self.checked,
                "errors": self.errors, "warnings": self.warnings}


STRUCTURE_SEMANTICS = "structure-valid only: 字段/算术/章节齐全；不代表审片通过，也不代表成片可用"
READY_SEMANTICS = ("readiness gate only: 必查项已明确判定通过且有证据 + 结论明确通过 + "
                   "末段媒体的**元数据/轨道**经独立探测且与声明一致 + "
                   "**采用时间线通过语义审计**（锚点与画面源区间交叉核对 / 保持帧 / 静默依据 / "
                   "内容级版本绑定 / stale 素材台账）。审计由本进程**现场重跑**，不接受外部报告替代。"
                   "它**不观看尾段、不检查帧内容、不验证音画同步、不听音轨**，不替代人工审片")

# 采用时间线在基础列之外还必须带的审计列。这里只声明 schema；**语义判定只有一个 owner**：
# check_timeline_audit.py（它 import 本模块的读取/区间工具，方向单一，不构成环）。
AUDIT_COLUMNS = ["event_phase", "claim_phase", "anchor_asset", "anchor_event", "anchor_source",
                 "evidence", "hold_mark", "visible_window",
                 "subtitle_sha256", "audio_sha256", "final_sha256"]


def read_table(path: Path) -> tuple[str, list[str], list[list[str]]]:
    """读取表格并**自动识别**分隔符：真正 TAB TSV 优先，其次 `|`。返回 (delim, header, rows)。"""
    if not path.is_file():
        raise SystemExit(f"文件不存在: {path}")
    lines = [ln.rstrip("\n") for ln in path.read_text(encoding="utf-8").splitlines()]
    data = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if not data:
        raise SystemExit(f"没有数据行: {path}")
    first = data[0]
    if "\t" in first:
        delim = "\t"
    elif "|" in first:
        delim = "|"
    else:
        raise SystemExit(f"无法识别分隔符（既没有 TAB 也没有 `|`）: {path}")
    header = [c.strip() for c in first.split(delim)]
    if delim == "|":
        header = [c for c in header if c != ""]
    rows = []
    for ln in data[1:]:
        cells = [c.strip() for c in ln.split(delim)]
        if delim == "|":
            cells = cells[1:-1] if (cells and cells[0] == "" and cells[-1] == "") else cells
        rows.append([c for c in cells])
    return delim, header, rows


def parse_range(value: str) -> tuple[float, float] | None:
    m = RANGE_RE.match(value or "")
    return (float(m.group(1)), float(m.group(2))) if m else None


def parse_speed_freeze(value: str) -> tuple[float, float] | None:
    m = SF_RE.match(value or "")
    return (float(m.group("speed")), float(m.group("freeze") or 0.0)) if m else None


def is_nullish(value: str) -> bool:
    return value.strip().lower() in NULLISH


def unfilled_lines(text: str) -> list[str]:
    """返回仍含未填占位符（`____` / TBD / TODO）的行。注意 `……` 属说明文字，不算占位符。"""
    out = []
    for ln in text.splitlines():
        if not ln.strip():
            continue
        if PLACEHOLDER_RE.search(ln):
            out.append(ln.strip()[:80])
    return out


# --------------------------------------------------------------- structure modes

def check_timeline(path: Path) -> Report:
    rep = Report()
    delim, header, rows = read_table(path)
    missing = [c for c in TIMELINE_COLUMNS if c not in header]
    if missing:
        rep.error(f"缺少必需列: {', '.join(missing)}")
        return rep
    idx = {c: header.index(c) for c in TIMELINE_COLUMNS}
    per_asset: dict[str, list[tuple[str, float, float, float, float]]] = {}

    for n, row in enumerate(rows, start=2):
        if len(row) < len(header):
            rep.error(f"第 {n} 行列数不足（{len(row)} < {len(header)}）")
            continue
        rep.checked += 1
        asset, event = row[idx["asset_id"]], row[idx["event_id"]]
        if not asset:
            rep.error(f"第 {n} 行 asset_id 为空")
        if not event:
            rep.error(f"第 {n} 行 event_id 为空")
        src = parse_range(row[idx["source_range"]])
        clip = parse_range(row[idx["clip_range"]])
        final = parse_range(row[idx["final_range"]])
        sf = parse_speed_freeze(row[idx["speed/freeze"]])
        if src is None:
            rep.error(f"第 {n} 行 source_range 无法解析: {row[idx['source_range']]!r}")
            continue
        if clip is None:
            rep.error(f"第 {n} 行 clip_range 无法解析: {row[idx['clip_range']]!r}")
            continue
        if final is None:
            rep.error(f"第 {n} 行 final_range 无法解析: {row[idx['final_range']]!r}")
            continue
        if sf is None:
            rep.error(f"第 {n} 行 speed/freeze 无法解析（示例 `1x` 或 `1x + 定格3s`）: "
                      f"{row[idx['speed/freeze']]!r}")
            continue
        speed, freeze = sf
        if speed <= 0:
            rep.error(f"第 {n} 行 speed 必须 > 0: {speed}")
            continue
        src_len, clip_len, final_len = src[1] - src[0], clip[1] - clip[0], final[1] - final[0]
        if src[0] < 0 or clip[0] < 0 or final[0] < 0:
            rep.error(f"第 {n} 行存在负起始时间: source={src[0]} clip={clip[0]} final={final[0]}")
            continue
        if src_len <= 0 or clip_len <= 0 or final_len <= 0:
            rep.error(f"第 {n} 行区间长度非正（可能倒序写反）: "
                      f"source={src_len} clip={clip_len} final={final_len}")
            continue
        if abs(clip_len - src_len) > TOL:
            rep.error(f"第 {n} 行 {event}: clip 长度 {clip_len:.2f}s != source 长度 {src_len:.2f}s"
                      f"（clip_range 必须与 source_range 等长）")
        expected = clip_len / speed + freeze
        if abs(final_len - expected) > TOL:
            rep.error(f"第 {n} 行 {event}: final 长度 {final_len:.2f}s != clip {clip_len:.2f}s ÷ {speed}x"
                      f" + 定格 {freeze:.2f}s = {expected:.2f}s")
        per_asset.setdefault(asset, []).append((event, clip[0], clip[1], final[0], final[1]))

    for asset, entries in per_asset.items():
        for label, i0, i1 in (("clip", 1, 2), ("final", 3, 4)):
            ordered = sorted(entries, key=lambda e: e[i0])
            for prev, cur in zip(ordered, ordered[1:]):
                if cur[i0] < prev[i1] - TOL:
                    rep.error(f"asset {asset}: {label} 区间重叠 "
                              f"{prev[0]}={prev[i0]}-{prev[i1]} 与 {cur[0]}={cur[i0]}-{cur[i1]}")
                elif cur[i0] - prev[i1] > TOL:
                    rep.warn(f"asset {asset}: {label} 区间有 {cur[i0] - prev[i1]:.2f}s 空隙 "
                             f"（{prev[0]} → {cur[0]}）")

    # 审计列只做"给了就得给全"的 schema 判断；阶段锚点/保持帧/静默依据等**语义**由
    # check_timeline_audit.py 判定（单一 owner），ready 门槛会强制跑它。
    provided = [c for c in AUDIT_COLUMNS if c in header]
    if provided and len(provided) != len(AUDIT_COLUMNS):
        rep.error(f"审计列不完整：还缺 {', '.join(c for c in AUDIT_COLUMNS if c not in header)}"
                  f"（要么全给，要么全不给）")
    elif not provided:
        rep.warn("缺少审计列（event_phase/claim_phase/evidence/hold_mark/visible_window）："
                 "结构通过只说明字段与算术自洽；交付前必须过 `check_timeline_audit.py audit`"
                 "（`ready` 门槛已强制要求），否则阶段错位/无标注保持帧/长静默无从判定")
    return rep


def check_units(path: Path) -> Report:
    rep = Report()
    _delim, header, rows = read_table(path)
    missing = [c for c in UNITS_COLUMNS if c not in header]
    if missing:
        rep.error(f"缺少必需列: {', '.join(missing)}")
        return rep
    idx = {c: header.index(c) for c in UNITS_COLUMNS}
    has_source = "reason_source" in header
    idx_source = header.index("reason_source") if has_source else None

    for n, row in enumerate(rows, start=2):
        if len(row) < len(header):
            rep.error(f"第 {n} 行列数不足（{len(row)} < {len(header)}）")
            continue
        rep.checked += 1
        event = row[idx["event_id"]] or f"第{n}行"
        stated = row[idx["stated_reason"]].strip()
        retro = row[idx["retrospective_commentary"]].strip()
        coverage = row[idx["coverage"]].strip().lower()

        if stated == "":
            rep.error(f"{event}: stated_reason 为空；没有当时理由必须显式写 null/未记录")
        if retro == "":
            rep.error(f"{event}: retrospective_commentary 为空；没有就写 null")
        if coverage not in UNIT_COVERAGE_VALUES:
            rep.error(f"{event}: coverage={row[idx['coverage']]!r} 非法，"
                      f"只能是 {'/'.join(sorted(UNIT_COVERAGE_VALUES))}")
        elif coverage == "coverage_gap":
            rep.warn(f"{event}: 标记 coverage_gap，该单元不得进入成片解说")

        # 只有来源字段明确写出来才判定回填；文本相同只是风险提示。
        source_value = row[idx_source].strip() if has_source and idx_source < len(row) else ""
        if source_value:
            low = source_value.lower()
            if any(m in low for m in RETRO_SOURCE_MARKERS) and not any(
                    m in low for m in STATEMENT_SOURCE_MARKERS):
                rep.error(f"{event}: reason_source={source_value!r} 表明该 '当时理由' 来自事后复盘 -> 回填")
            elif any(m in low for m in STATEMENT_SOURCE_MARKERS):
                pass
            else:
                rep.warn(f"{event}: reason_source={source_value!r} 未标明是当时还是事后，无法确证")
        elif stated and retro and not is_nullish(stated) and not is_nullish(retro) and stated == retro:
            rep.warn(f"{event}: stated_reason 与 retrospective_commentary 文本相同——"
                     f"可能是巧合（不得据此判定造假）；若确为回填，请填 reason_source 指出来源")

        if idx["source_range"] < len(row) and parse_range(row[idx["source_range"]]) is None:
            rep.error(f"{event}: source_range 无法解析: {row[idx['source_range']]!r}")
    return rep


def check_sheet(path: Path) -> Report:
    rep = Report()
    if not path.is_file():
        raise SystemExit(f"文件不存在: {path}")
    text = path.read_text(encoding="utf-8")
    for heading in SHEET_HEADINGS:
        if heading not in text:
            rep.error(f"缺少必需章节: {heading} ...")
    missing_cols = [c for c in ISSUE_COLUMNS if c not in text]
    if missing_cols:
        rep.error(f"问题清单缺少必需列: {', '.join(missing_cols)}")
    if "unknown" not in text.lower():
        rep.error("缺少 `unknown` 逃生口：没有真值时必须填 unknown，不得臆造 0")
    if "非预期" not in text:
        rep.warn("A5 未写明“非预期”黑屏/冻结——已登记的定格不应被当成缺陷")
    if "stated_reason" not in text or "retrospective_commentary" not in text:
        rep.error("未同时出现 stated_reason 与 retrospective_commentary，三字段分列要求缺失")
    if "尾段" not in text and "最后 10%" not in text:
        rep.warn("未看到尾段结局强制覆盖的检查项")
    rep.checked = 1
    return rep


# --------------------------------------------------------------- readiness mode

REQUIRED_AE_ITEMS = ("A1", "A2", "A3", "A4", "A5",
                     "E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9")
REQUIRED_G_ITEMS = ("G1", "G2", "G3")
NA_ALLOWED_ITEMS = {"G2"}          # 只有"双窗声明"这一项明确可不适用
PASS_TOKENS = {"是", "通过", "ok", "pass", "yes", "y"}

HEADER_FILE_RE = re.compile(r"成片文件\s*：\s*(?P<value>.*)$", re.MULTILINE)
HEADER_DUR_RE = re.compile(
    r"成片时长\s*：\s*(?P<dur>\d+(?:\.\d+)?)\s*项目预期/容差\s*：\s*(?P<tol>[^\s（(]+)")
CONCLUSION_RE = re.compile(r"是否需要重做整场\s*：\s*(?P<value>.*)$", re.MULTILINE)


def probe_media(path: Path) -> dict:
    """独立探测媒体**元数据与轨道**（不采信单子里写的数字，也不观看内容）。

    这里刻意保留自己的 ffprobe 调用，而不复用 `check_timeline_audit.py` 里的 JSON 包装：
    依赖方向是 audit -> 本模块（audit 要拿 AUDIT_COLUMNS/区间工具），反过来 import 会成环。
    两者职责也不同——这里只问"末段 MP4 有没有可读视频流/音轨/有效时长"。

    返回 {"ok": bool, "error": str|None, "duration": float|None,
          "has_video": bool, "has_audio": bool, "streams": int}
    失败一律结构化返回，不抛裸异常。
    """
    out = {"ok": False, "error": None, "duration": None,
           "has_video": False, "has_audio": False, "streams": 0}
    if not shutil.which("ffprobe"):
        out["error"] = "找不到 ffprobe"
        return out
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                               "format=duration", "-show_entries",
                               "stream=codec_type", "-of", "json", str(path)],
                              capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        out["error"] = "ffprobe 超时（120s）"
        return out
    except OSError as exc:
        out["error"] = f"无法执行 ffprobe: {type(exc).__name__}: {exc}"
        return out
    if proc.returncode != 0:
        out["error"] = f"ffprobe 返回 {proc.returncode}: {(proc.stderr or '').strip()[:160]}"
        return out
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        out["error"] = "ffprobe 输出不是 JSON"
        return out
    streams = data.get("streams") or []
    out["streams"] = len(streams)
    out["has_video"] = any(s.get("codec_type") == "video" for s in streams)
    out["has_audio"] = any(s.get("codec_type") == "audio" for s in streams)
    try:
        out["duration"] = round(float((data.get("format") or {}).get("duration")), 3)
    except (TypeError, ValueError):
        out["duration"] = None
    out["ok"] = True
    return out


def run_timeline_audit(rep: Report, timeline: Path, preflight: Path, silence_ledger: Path,
                       subtitle: Path, audio: Path, final_mp4: Path,
                       silence_threshold: float, tol: float) -> None:
    """把采用时间线的语义审计接进 ready 门槛。

    单独跑 `check_timeline_audit.py audit` 也能得到同样的判定；这里只是**让它不可跳过**。
    审计的语义 owner 是那一个模块，本函数不重复实现任何规则。

    注意：**没有**"提交一份审计 JSON 就算过"的入口。`ready` 总是现场重跑审计，
    否则一份过期或伪造的独立报告就能绕过全部语义检查。
    """
    try:
        from check_timeline_audit import build_audit_payload
    except ImportError as exc:                          # pragma: no cover - 同目录一起发布
        rep.error(f"无法加载时间线审计模块（{exc}）-> 就绪门槛不完整，未就绪")
        return
    try:
        payload = build_audit_payload(timeline, preflight, silence_ledger, subtitle, audio,
                                      final_mp4, silence_threshold, tol)
    except (SystemExit, OSError, ValueError) as exc:
        rep.error(f"时间线审计无法执行：{exc} -> 未就绪")
        return
    rep.checked += int(payload.get("checked_rows") or 0)
    if payload.get("ok"):
        return
    errors = payload.get("errors") or ["时间线审计未通过"]
    shown = errors[:6]
    for msg in shown:
        rep.error(f"时间线审计: {msg}")
    if len(errors) > len(shown):
        rep.error(f"时间线审计: 另有 {len(errors) - len(shown)} 条同类问题（跑 "
                  f"`check_timeline_audit.py audit ... --json` 看全量）")


def check_ready(path: Path, final_mp4: Path | None, tol_override: float | None,
                timeline: Path | None = None, preflight: Path | None = None,
                silence_ledger: Path | None = None, subtitle: Path | None = None,
                audio: Path | None = None, silence_threshold: float = 20.0) -> Report:
    rep = check_sheet(path)                      # 复用结构检查
    text = path.read_text(encoding="utf-8")

    unfilled = unfilled_lines(text)
    if unfilled:
        rep.error(f"仍有 {len(unfilled)} 处未填占位符（示例: {unfilled[0]!r}）-> 未就绪")

    # 采用时间线的语义审计是门槛的一部分，不是可选项：缺任一输入就不构成"已审片"。
    missing_audit = [name for name, value in (("--timeline", timeline), ("--preflight", preflight),
                                              ("--silence-ledger", silence_ledger),
                                              ("--subtitle", subtitle), ("--audio", audio))
                     if value is None]
    if missing_audit:
        rep.error(f"缺少 {' '.join(missing_audit)}：采用时间线未做语义审计 -> 状态 unverified"
                  f"（阶段锚点/保持帧/长静默依据/版本绑定都未判定，不得当作已审片）")

    m_file = HEADER_FILE_RE.search(text)
    declared_file = (m_file.group("value").strip() if m_file else "")
    if not declared_file or declared_file.endswith("："):
        rep.error("表头未填「成片文件」-> 未就绪")

    m_dur = HEADER_DUR_RE.search(text)
    declared_dur, declared_tol = (None, None)
    if not m_dur:
        rep.error("表头未按格式填「成片时长 + 项目预期/容差」-> 未就绪")
    else:
        declared_dur = float(m_dur.group("dur"))
        raw_tol = m_dur.group("tol").strip()
        try:
            declared_tol = float(re.sub(r"[^\d.]", "", raw_tol) or "nan")
        except ValueError:
            declared_tol = None
        if declared_tol is None or declared_tol != declared_tol:   # NaN 检查
            rep.error(f"容差无法解析为数值: {raw_tol!r} -> 未就绪")

    m_conc = CONCLUSION_RE.search(text)
    if not m_conc or not m_conc.group("value").strip():
        rep.error("H 段「是否需要重做整场」未填 -> 未就绪")

    # 必查项：必须存在、结果已明确选中且为"通过"、A/E 还要有证据。
    rows_by_id: dict[str, list[str]] = {}
    for line in text.splitlines():
        ln = line.strip()
        if not ln.startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip("|").split("|")]
        if len(cells) >= 3 and re.fullmatch(r"[AEG]\d+", cells[0]):
            rows_by_id[cells[0]] = cells
    for item in REQUIRED_AE_ITEMS:
        cells = rows_by_id.get(item)
        if cells is None:
            rep.error(f"缺少必需检查项 {item} -> 未就绪")
            continue
        verdict = cells[2] if len(cells) > 2 else ""
        evidence = cells[3] if len(cells) > 3 else ""
        if verdict in NA_ALLOWED_ITEMS:
            rep.warn(f"{item} 标记 N/A（明确不适用）")
        elif verdict.strip().lower() not in PASS_TOKENS:
            rep.error(f"{item} 的结果是「{verdict}」——只有明确选中「是」才算通过"
                      f"（否/unknown/空白/双选项一律未就绪）")
        if len(cells) < 4 or not evidence:
            rep.error(f"{item} 缺少证据（必须指到源帧时间码 / ffprobe 输出 / 文件）-> 未就绪")
    for item in REQUIRED_G_ITEMS:
        cells = rows_by_id.get(item)
        if cells is None:
            rep.error(f"缺少必需检查项 {item} -> 未就绪")
            continue
        verdict = cells[2] if len(cells) > 2 else ""
        if item in NA_ALLOWED_ITEMS and verdict.strip().upper() == "N/A":
            continue
        if verdict.strip().lower() not in PASS_TOKENS:
            rep.error(f"{item} 的结果是「{verdict}」——只有明确选中「是」（{item} 允许 N/A）才算通过")

    # F 段：unknown 是合法记录，但意味着未验证，不得据此判通过
    for m in re.finditer(r"(误报数|漏报数)\s*：\s*(\S+)", text):
        if m.group(2).strip().lower() == "unknown":
            rep.error(f"{m.group(1)} = unknown（无真值可比对）-> 未验证，不得当作通过")

    # H 结论必须**明确且唯一**地写「通过」；「局部修改」/双选项/缺失都保持 not-ready
    head = text[text.find("## H."):] if "## H." in text else ""
    verdict_line = None
    for line in head.splitlines():
        s_line = line.strip()
        if not s_line or s_line.startswith("#") or s_line.startswith("```"):
            continue
        if "是否需要重做整场" in s_line or s_line.startswith("本轮修改段落"):
            continue
        verdict_line = s_line
        break
    if verdict_line is None:
        rep.error("H 段缺少明确结论 -> 未就绪")
    elif verdict_line.endswith("列出段落与时间码）") or " / " in verdict_line:
        rep.error(f"H 段结论仍是双选项未选（{verdict_line!r}）-> 未就绪")
    elif verdict_line != "通过":
        rep.error(f"H 段结论是「{verdict_line}」而非「通过」-> 未就绪"
                  f"（局部修改/未解决状态不得当作已审片通过）")

    # 问题清单：**任何**有实际内容的行都算未解决 issue（不依赖 id 前缀）
    issue_hint = "（源帧时间码 / ffprobe 输出 / 文件）"
    in_issue_table = False
    issue_rows = []
    for ln in text.splitlines():
        st = ln.strip()
        if not st.startswith("|"):
            in_issue_table = False
            continue
        cells = [c.strip() for c in st.strip("|").split("|")]
        if cells and cells[0] in ("issue_id",):
            in_issue_table = True
            continue
        if cells and set("".join(cells)) <= set("-: "):
            continue
        if in_issue_table and len(cells) >= 8 and cells[0] and cells[0] != "issue_id":
            real = [c for c in cells[1:] if c and c != issue_hint]
            if real:
                issue_rows.append(st)
    if issue_rows:
        rep.error(f"问题清单有 {len(issue_rows)} 条未解决 issue -> 未就绪（须先修复或明确挂起）")

    # 容差必须有限且非负；有限性/符号错了会掩盖硬失败
    for label, value in (("--tol", tol_override), ("声明容差", declared_tol),
                         ("声明时长", declared_dur)):
        if value is None:
            continue
        if not math.isfinite(value) or value < 0:
            rep.error(f"{label} 非法（必须是有限非负数）: {value!r} -> 未就绪")
    if declared_dur is not None and not math.isfinite(declared_dur):
        rep.error(f"声明时长非有限数: {declared_dur!r} -> 未就绪")
    tol = tol_override if tol_override is not None else declared_tol

    # 末段媒体：**先收集全部问题，不提前 return** —— 任何一条提前退出都会让语义审计被跳过，
    # 而"缺媒体参数"本身也不该成为绕开审计的通道。
    media = None
    probe_target = final_mp4 if final_mp4 is not None else Path("（未提供 --final-mp4）")
    if final_mp4 is None:
        rep.error("未提供 --final-mp4：末段媒体未经独立探测 -> 状态 unverified（不得当作已审片）")
    elif not final_mp4.is_file():
        rep.error(f"--final-mp4 指向的文件不存在: {final_mp4} -> 未就绪")
    else:
        # 声明文件与实际验证文件必须归一后相同，否则等于"审的是 A、验的是 B"
        if declared_file:
            try:
                if Path(declared_file).expanduser().resolve() != final_mp4.resolve():
                    rep.error(f"表头「成片文件」{declared_file} 与 --final-mp4 {final_mp4} "
                              f"归一化后不是同一个文件 -> 未就绪（不得审 A 验 B）")
            except OSError as exc:
                rep.error(f"无法归一化文件路径: {exc} -> 未就绪")
        media = probe_media(final_mp4)
        if not media["ok"]:
            rep.error(f"媒体探测失败（{media['error']}）-> 未就绪")
            media = None
        else:
            rep.checked += 1
            if not media["has_video"]:
                rep.error("该文件没有视频流 -> 未就绪")
            if not media["has_audio"]:
                rep.error("该文件没有音轨——带解说的成片必须有音轨 -> 未就绪")
            if media["duration"] is None or not math.isfinite(media["duration"]) \
                    or media["duration"] <= 0:
                rep.error(f"无法得到有效时长（{media['duration']!r}）-> 未就绪")
                media = None
            elif declared_dur is None or tol is None:
                rep.error("缺少可比的声明时长/容差，无法与实测比对 -> 未就绪")
            elif abs(media["duration"] - declared_dur) > tol + 1e-9:
                rep.error(f"实测时长 {media['duration']:.3f}s 与声明 {declared_dur:.3f}s 相差 "
                          f"{abs(media['duration'] - declared_dur):.3f}s，超出容差 {tol} -> 未就绪")

    # 缺审计输入的情况**已在前面报过错**，这里只决定跑不跑；不接受外部审计报告替代现场重跑。
    if not missing_audit:
        run_timeline_audit(rep, timeline, preflight, silence_ledger, subtitle, audio,
                           probe_target, silence_threshold, DEFAULT_AUDIT_TOL)
    return rep


# --------------------------------------------------------------- cli

MODES = {
    "timeline": (check_timeline, STRUCTURE_SEMANTICS),
    "units": (check_units, STRUCTURE_SEMANTICS),
    "sheet": (check_sheet, STRUCTURE_SEMANTICS),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="check_postproduction.py",
        description="gameplay-postproduction 产物确定性检查（structure 与 readiness 语义不同）")
    ap.add_argument("mode", choices=["timeline", "units", "sheet", "ready"])
    ap.add_argument("file")
    ap.add_argument("--final-mp4", default=None,
                    help="ready 模式：实际导出的 MP4 路径（会被独立 ffprobe 探测）")
    ap.add_argument("--timeline", default=None,
                    help="ready 模式：实际采用的统一时间线 TSV（必填，会被语义审计）")
    ap.add_argument("--preflight", default=None,
                    help="ready 模式：`check_timeline_audit.py preflight --out` 写出的素材台账 JSON（必填）")
    ap.add_argument("--silence-ledger", default=None, help="ready 模式：静默台账 TSV（必填）")
    ap.add_argument("--subtitle", default=None, help="ready 模式：实际采用的字幕文件（必填）")
    ap.add_argument("--audio", default=None, help="ready 模式：实际采用的旁白音轨（必填）")
    ap.add_argument("--silence-threshold", type=float, default=20.0,
                    help="ready 模式：多长的无口播算“需要依据”（默认 20s）")
    ap.add_argument("--tol", type=float, default=None,
                    help="ready 模式：覆盖单子里声明的时长容差（秒）")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.tol is not None and (not math.isfinite(args.tol) or args.tol < 0):
        msg = f"--tol 非法（必须是有限非负数）: {args.tol}"
        if args.json:
            print(json.dumps({"mode": args.mode, "target": args.file, "ok": False,
                              "errors": [msg], "warnings": [], "checked_rows": 0},
                             ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {msg}", file=sys.stderr)
        return 2

    path = Path(args.file).expanduser()
    expand = lambda v: Path(v).expanduser() if v else None  # noqa: E731
    try:
        if args.mode == "ready":
            rep = check_ready(path, expand(args.final_mp4), args.tol,
                              timeline=expand(args.timeline), preflight=expand(args.preflight),
                              silence_ledger=expand(args.silence_ledger),
                              subtitle=expand(args.subtitle), audio=expand(args.audio),
                              silence_threshold=args.silence_threshold)
            semantics = READY_SEMANTICS
        else:
            fn, semantics = MODES[args.mode]
            rep = fn(path)
    except (SystemExit, OSError, ValueError) as exc:
        if args.json:
            print(json.dumps({"mode": args.mode, "target": str(path), "ok": False,
                              "errors": [str(exc)], "warnings": [], "checked_rows": 0},
                             ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    payload = rep.payload(args.mode, str(path), semantics=semantics)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        label = "structure" if args.mode in MODES else "readiness"
        print(f"[{args.mode}] {path}  检查 {rep.checked} 项")
        print(f"  语义: {semantics}")
        for w in rep.warnings:
            print(f"  WARN  {w}")
        for e in rep.errors:
            print(f"  ERROR {e}")
        verdict = "结构通过（≠审片通过）" if args.mode in MODES and payload["ok"] else (
            "就绪（≠质量合格）" if payload["ok"] else f"{label} 未通过")
        print(f"结果：{verdict}")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
