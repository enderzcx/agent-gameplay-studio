#!/usr/bin/env python3
"""gameplay-postproduction 时间线语义审计 + 输入预检（确定性，不调用任何模型）。

**两个模式，语义与 `check_postproduction.py` 互补，不要互相替代**：

    preflight（输入预检）  <asset_id>=<path> [...]
        对每个源素材独立探测：sha256 / size / duration / 实测帧率 / 视频流 / 音轨 /
        **音轨是否真的有信号** / 采集是否稀疏。输出机器可读台账（`--out`），供 audit 复用与 stale 检测。
        **探测不出来、或探测过程本身失败，一律 `undetermined` 并非 0 退出**——不许静默放过。

    audit（采用时间线审计）  <timeline.tsv> --preflight p.json --silence-ledger gaps.tsv
                            --subtitle s.srt --audio a.wav --final-mp4 final.mp4
                            --receipt produce-receipt.json
        以**实际采用的那一份 timeline** 为唯一入口，判定：
          **锚点交叉核对**：旁白声明的 (asset, event, interval) 必须与这一行**真实画面的源区间**对得上。
          **阶段**：同 phase 才默认允许；跨 phase 必须显式 `claim_mode=retrospective`，
              且同一事件上"画面还没到那个阶段却已经在讲结果"仍然失败。全局 phase 顺序**不被当成时间轴**。
          **静默/占比**：按**实际声段**（`final_start + audio_offset_s` 起、加实测 `audio_duration_s`）
              算并取**并集**（与 gap 同口径），
              不是按画面窗口；旁白占比同样只算声段。逐段给依据，台账陈旧/自相矛盾都失败。
          **保持帧 / 可见区间**：见下。
          **内容级版本绑定 + 制作 receipt**：timeline/字幕/音轨/成片四份内容摘要必须一致，
              且 receipt（由制作路径写出）必须把这一份 timeline 与这三份产物绑在同一次制作上；
              字幕还要**逐句核对文本与时点**，且时点必须**贴合实际声段起止**（不是"落在里面就行"）。
              同段数改词/改时间、整句压成末尾 0.1s、旧音轨、旧成片都失败。
        所有区间/时长/容差都拒绝 NaN、inf、负数与倒序，且容差有合理上界。

它**不做什么**：不看画面、不听音轨、不判断听感与事实语义。**人填的锚点与标注本身不构成机器证据**
—— 报告里有 `human_annotations_not_machine_verified` 与 `not_a_verdict_on` 两块显式写出这条边界。
receipt 也只能证明"这几份产物是一次制作、且与这份 timeline 一致"，**它没有签名，防不了伪造**。

用法：

    S=skills/gameplay-postproduction/scripts
    python3 "$S/check_timeline_audit.py" preflight --json --out preflight.json rec-win=/abs/win.mp4
    python3 "$S/check_timeline_audit.py" audit timeline.tsv --preflight preflight.json \\
        --silence-ledger gaps.tsv --subtitle subs.srt --audio voice_master.wav \\
        --final-mp4 final.mp4 --receipt produce-receipt.json --json --report-out audit-report.json

退出码：0=该模式通过，1=有缺陷/未就绪，2=用法或读取错误。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_postproduction import (AUDIT_COLUMNS, BASE_COLUMNS, check_timeline,  # noqa: E402
                                  is_nullish, parse_range, parse_speed_freeze, read_table)

TOL = 0.05
DEFAULT_SILENCE_S = 20.0
DEFAULT_MIN_FPS = 12.0
DEFAULT_SILENCE_DB = -60.0
DEFAULT_SYNC_TOL = 0.25
MAX_TOL = 10.0                 # 容差超过这个值，门槛就没有意义了
MIN_SILENCE_S = 0.5
MAX_SILENCE_S = 3600.0

PHASE_ORDER = ["setup", "battle", "reward", "map", "shop", "rest", "event", "other"]
CLAIM_MODES = ("live", "retrospective")
SILENCE_DISPOSITIONS = ("keep", "cut", "narration_added")
LEDGER_COLUMNS = ["gap_id", "final_range", "disposition", "reason"]
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RECEIPT_SCHEMA_PREFIX = "gameplay-postproduction/produce-receipt/"

# 严格的有界区间：有限数、起点 >= 0、终点 > 起点。NaN/inf/负值/倒序一律不接受。
STRICT_RANGE_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*$")
TIMECODE_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*s\b")
MAX_VOLUME_RE = re.compile(r"max_volume:\s*(-?[\d.]+|-inf)\s*dB")
CUE_LINE_RE = re.compile(r"^\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*"
                         r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")

PREFLIGHT_SEMANTICS = ("input preflight only: 每个源素材的音轨存在性/是否有真实信号/采集密度被独立探测"
                       "并明确写成状态；它不判断画面内容好不好，也不构成审片通过")
AUDIT_SEMANTICS = ("adopted-timeline audit only: 锚点与画面源区间的交叉核对、阶段声明、按实际声段算的静默、"
                   "保持帧、内容级版本绑定与制作 receipt、stale 素材台账按确定性规则判定；"
                   "它**不看画面、不听音轨**，通过不等于审片通过")

NOT_A_VERDICT_ON = [
    "旁白占比：占比高低不等于内容通过",
    "解码成功 / 命令退出码 0：不等于内容正确",
    "听感、抑扬、断句是否自然：只有真人能判",
    "事实语义（选牌/伤害/胜负/身份）：必须回源帧，机器不裁定",
]


def human_annotation_caveats() -> list:
    """机器核对不了、只能靠人填写的东西。写进报告，避免「填了就算证过」。"""
    return [
        "anchor_asset/anchor_event/anchor_source 由人填写：机器只核对它们与画面源区间是否自洽，"
        "不核对「这句话确实在讲那个回合」",
        "evidence 是人工写的来源说明，本身不是证据；证据在源帧，机器不读帧",
        "claim_mode=retrospective 是人声明的：机器只核对「跨 phase 时必须显式声明」与"
        "「同一事件不得提前讲结果」，不核对这段回顾是否属实",
        "hold_mark 由人填写：机器核对锚点与区间的关系，不核对「这一帧确实值得保持」",
        "visible_window 由人填写：机器核对锚点落在窗口内，不核对「窗口里确实能看到候选」",
        "keep/cut/narration_added 的静默理由由人给出：机器只核对是否逐段给出且不矛盾",
        "produce-receipt 是制作路径写出的绑定记录，**没有签名**：它能证明这几份产物属于同一次制作"
        "且与这份 timeline 一致，防不了蓄意伪造",
    ]


class Report:
    def __init__(self) -> None:
        self.errors: list = []
        self.warnings: list = []
        self.unverified: list = []
        self.checked = 0

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def note_unverified(self, msg: str) -> None:
        self.unverified.append(msg)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def as_finite_float(value) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def validate_tol(value) -> float:
    """容差：有限、非负、且有合理上界。library 与两个 CLI 共用同一套判定。"""
    v = as_finite_float(value)
    if v is None or v < 0 or v > MAX_TOL:
        raise SystemExit(f"tol 必须是 [0, {MAX_TOL:g}] 内的有限数：{value!r}（NaN/inf/负数/过大一律拒绝）")
    return v


def validate_silence_threshold(value) -> float:
    v = as_finite_float(value)
    if v is None or v < MIN_SILENCE_S or v > MAX_SILENCE_S:
        raise SystemExit(f"silence_threshold 必须是 [{MIN_SILENCE_S:g}, {MAX_SILENCE_S:g}] 内的有限数："
                         f"{value!r}（NaN/inf/0/负数一律拒绝：那会让静默检查失效）")
    return v


def strict_range(value: str) -> tuple | None:
    """严格解析 `a-b`：必须是有限数、a >= 0、b > a。否则 None（调用方负责报错）。"""
    m = STRICT_RANGE_RE.match(value or "")
    if not m:
        return None
    a, b = as_finite_float(m.group(1)), as_finite_float(m.group(2))
    if a is None or b is None or a < 0 or b <= a:
        return None
    return (a, b)


def range_error(label: str, raw: str) -> str:
    return (f"{label}={raw!r} 非法：必须形如 `起-止`，且为有限数、起点 ≥ 0、终点 > 起点"
            f"（NaN/inf/负数/倒序一律不接受）")


def ffprobe_json(path: Path, entries: str, extra: list | None = None) -> tuple:
    """`-show_entries` 的分节分隔符是 **`:`**（`stream=…:format=…`）。

    用 `&` 时 ffprobe 不会报错，但 `format` 那一段会被整段丢掉——于是"量不到时长"这种失败
    会退化成"用了 stream 时长"，静默偏航。这里固定用 `:`，并在解析后核对必需字段是否真的回来了。
    """
    cmd = ["ffprobe", "-v", "error", "-of", "json", "-show_entries", entries]
    if extra:
        cmd += extra
    cmd.append(str(path))
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return None, "ffprobe 超时（300s）"
    except OSError as exc:
        return None, f"无法执行 ffprobe: {type(exc).__name__}: {exc}"
    if p.returncode != 0:
        return None, f"ffprobe 返回 {p.returncode}: {(p.stderr or '').strip()[:160]}"
    try:
        return json.loads(p.stdout or "{}"), ""
    except json.JSONDecodeError:
        return None, "ffprobe 输出不是 JSON"


def media_duration(path: Path) -> tuple:
    data, err = ffprobe_json(path, "format=duration:stream=duration")
    if data is None:
        return None, err
    fmt = data.get("format") or {}
    dur = as_finite_float(fmt.get("duration"))
    if dur is None:
        # format 段缺失是**失败**，不是"退而求其次用 stream 时长"
        dur = max((as_finite_float(s.get("duration")) or 0.0) for s in (data.get("streams") or [{}]))
    if dur is None or dur <= 0:
        return None, f"无法得到有效时长（{dur!r}）"
    return dur, ""


def has_audio_stream(path: Path) -> tuple:
    data, err = ffprobe_json(path, "stream=codec_type")
    if data is None:
        return None, err
    return any(s.get("codec_type") == "audio" for s in (data.get("streams") or [])), ""


# --------------------------------------------------------------------- preflight

def measure_audio_signal(path: Path, silence_db: float) -> tuple:
    """返回 (audio_signal, max_volume_db, error)。`silent` 是**音轨在但没有信号**，不是「静音源」。

    **ffmpeg 非 0 退出一律算失败**：解码中途炸掉时日志里可能已经打出了 max_volume，
    只看那一行会把"没量全"当成"量到了"。
    """
    cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-v", "info",
           "-i", str(path), "-map", "0:a:0", "-af", "volumedetect", "-f", "null", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return "unknown", None, "ffmpeg volumedetect 超时（300s）"
    except OSError as exc:
        return "unknown", None, f"无法执行 ffmpeg: {type(exc).__name__}: {exc}"
    blob = (p.stderr or "") + (p.stdout or "")
    if p.returncode != 0:
        return "unknown", None, (f"ffmpeg volumedetect 返回 {p.returncode}"
                                f"（{(p.stderr or '').strip().splitlines()[-1][:120] if p.stderr else ''}）"
                                f" -> 音轨信号未验证")
    m = MAX_VOLUME_RE.search(blob)
    if not m:
        return "unknown", None, "volumedetect 未给出 max_volume（无法判定音轨是否有信号）"
    raw = m.group(1)
    if raw == "-inf":
        return "silent", None, ""
    vol = as_finite_float(raw)
    if vol is None:
        return "unknown", None, f"max_volume 不是有限数: {raw!r}"
    return ("silent" if vol <= silence_db else "present"), vol, ""


def probe_asset(asset_id: str, path: Path, min_fps: float, silence_db: float) -> tuple:
    rep = Report()
    resolved = path.expanduser()
    rec = {"asset_id": asset_id, "source_path": str(resolved.resolve()) if resolved.exists()
           else str(resolved), "source_path_as_given": str(path), "status": "undetermined",
           "sha256": None, "size_bytes": None, "duration_s": None, "avg_fps": None,
           "frames": None, "has_video": False, "has_audio": False,
           "audio_signal": "unknown", "audio_max_volume_db": None,
           "frame_sampling": "unknown", "notes": []}
    if not path.is_file():
        rep.error(f"{asset_id}: 源文件不存在或不是普通文件: {path}")
        return rec, rep
    # 台账里存**绝对规范路径**：换 cwd 之后 audit 仍能核对同一份素材
    rec["source_path"] = str(path.resolve())

    rec["sha256"] = sha256_file(path)
    rec["size_bytes"] = path.stat().st_size

    data, err = ffprobe_json(path, "stream=codec_type,avg_frame_rate,r_frame_rate,nb_frames,duration,"
                                   "width,height:format=duration,size")
    if data is None:
        rep.error(f"{asset_id}: 无法探测媒体（{err}）")
        return rec, rep
    streams = data.get("streams") or []
    video = [s for s in streams if s.get("codec_type") == "video"]
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    rec["has_video"] = bool(video)
    rec["has_audio"] = bool(audio)
    if not video:
        rep.error(f"{asset_id}: 没有视频流 -> 不是可用的源画面")
        return rec, rep

    fmt_dur = as_finite_float((data.get("format") or {}).get("duration"))
    dur = fmt_dur if fmt_dur is not None else as_finite_float(video[0].get("duration"))
    if fmt_dur is None:
        rep.warn(f"{asset_id}: 容器的 format 时长缺失，退用视频流时长")
    if dur is None or dur <= 0:
        rep.error(f"{asset_id}: 无法得到有效时长（{dur!r}）")
        return rec, rep
    rec["duration_s"] = round(dur, 3)

    frames = None
    try:
        nbf = video[0].get("nb_frames")
        frames = int(nbf) if nbf not in (None, "N/A") else None
    except (TypeError, ValueError):
        frames = None
    if not frames:
        pk, pkerr = ffprobe_json(path, "stream=nb_read_packets",
                                 extra=["-select_streams", "v:0", "-count_packets"])
        if pk is not None:
            try:
                frames = int((pk.get("streams") or [{}])[0].get("nb_read_packets"))
            except (TypeError, ValueError, IndexError):
                frames = None
        if not frames:
            rep.warn(f"{asset_id}: 拿不到帧数（{pkerr or 'nb_frames 缺失'}），改用容器声明的帧率")
    if frames and frames > 0:
        rec["frames"] = frames
        avg = frames / dur
        rec["avg_fps"] = round(avg, 3)
        rec["frame_sampling"] = "sparse" if avg < min_fps else "normal"
    else:
        rec["frame_sampling"] = "unknown"
        rec["notes"].append("实测帧数不可得：无法区分正常采集与稀疏采集")
    if rec["frame_sampling"] == "sparse":
        rec["notes"].append(f"稀疏采集（约 {rec['avg_fps']:g}fps）：原速播放是时间正确，但运动信息少；"
                            f"不得据此声称画面动感充足，也不得用冻结/插帧假装流畅")

    if not audio:
        rec["audio_signal"] = "absent"
        rec["notes"].append("源无音轨：允许（有意静音），但**禁止编造源音**；成品 MP4 仍必须有音轨")
        rep.warn(f"{asset_id}: 源没有音轨（如实登记，不得伪造游戏原声）")
    else:
        signal, vol, aerr = measure_audio_signal(path, silence_db)
        rec["audio_signal"] = signal
        rec["audio_max_volume_db"] = vol
        if signal == "unknown":
            rep.error(f"{asset_id}: 音轨存在但无法判定是否有真实信号（{aerr}）")
        elif signal == "silent":
            rec["notes"].append(f"音轨存在但无真实信号（max_volume≈{vol} dB ≤ {silence_db} dB）："
                                f"这不是「有原声」，必须如实标为静音音轨")
            rep.warn(f"{asset_id}: 音轨是静音音轨（max_volume={vol} dB），不得当作有游戏原声")
        else:
            rec["notes"].append(f"音轨有真实信号（max_volume≈{vol} dB）")

    if rec["frame_sampling"] == "unknown":
        rep.error(f"{asset_id}: 采集密度无法判定 -> 状态 undetermined（不得静默通过）")
    if not rep.errors:
        rec["status"] = "determined"
    return rec, rep


def run_preflight(assets: list, min_fps: float, silence_db: float, out_path, as_json: bool) -> int:
    rep = Report()
    records: dict = {}
    for spec in assets:
        if "=" in spec:
            asset_id, _, raw = spec.partition("=")
            asset_id = asset_id.strip()
        else:
            raw, asset_id = spec, Path(spec).stem
        if not asset_id:
            rep.error(f"资产 id 为空: {spec!r}")
            continue
        if asset_id in records:
            rep.error(f"资产 id 重复: {asset_id}")
            continue
        rec, sub = probe_asset(asset_id, Path(raw).expanduser(), min_fps, silence_db)
        records[asset_id] = rec
        rep.errors += sub.errors
        rep.warnings += sub.warnings
        rep.checked += 1

    payload = {"mode": "preflight", "semantics": PREFLIGHT_SEMANTICS,
               "ok": not rep.errors, "checked_assets": rep.checked,
               "min_fps": min_fps, "silence_db": silence_db,
               "assets": records, "errors": rep.errors, "warnings": rep.warnings,
               "not_a_verdict_on": NOT_A_VERDICT_ON}
    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"[preflight] {rep.checked} 个素材")
        for asset_id, rec in records.items():
            print(f"  {asset_id}: status={rec['status']} audio={rec['audio_signal']} "
                  f"sampling={rec['frame_sampling']} fps={rec['avg_fps']} dur={rec['duration_s']}s")
            for note in rec["notes"]:
                print(f"      · {note}")
        for w in rep.warnings:
            print(f"  WARN  {w}")
        for e in rep.errors:
            print(f"  ERROR {e}")
        if out_path is not None:
            print(f"  report: {out_path}")
        print("结果：" + ("输入状态已明确（≠内容通过）" if payload["ok"] else "输入状态未确定"))
    return 0 if payload["ok"] else 1


# --------------------------------------------------------------------- audit

def parse_srt(path: Path) -> list:
    """解析 SRT 为 [{index, start, end, text}]。读不懂就抛 SystemExit（读不懂 ≠ 通过）。"""
    if not path.is_file():
        raise SystemExit(f"字幕文件不存在: {path}")
    raw = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    blocks, cur = [], []
    for line in raw.split("\n") + [""]:
        if line.strip() == "":
            if cur:
                blocks.append(cur)
                cur = []
            continue
        cur.append(line)
    out = []
    for block in blocks:
        ts_at = next((i for i, ln in enumerate(block) if CUE_LINE_RE.match(ln)), None)
        if ts_at is None:
            raise SystemExit(f"字幕块缺少时间码: {block[:1]!r}")
        m = CUE_LINE_RE.match(block[ts_at])
        # 毫秒字段按**原文字面长度**换算：`050` 是 50ms，不能先 int() 再数位数
        start = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) \
            + int(m.group(4)) / (10 ** len(m.group(4)))
        end = int(m.group(5)) * 3600 + int(m.group(6)) * 60 + int(m.group(7)) \
            + int(m.group(8)) / (10 ** len(m.group(8)))
        idx = None
        if ts_at > 0:
            try:
                idx = int(block[0].strip())
            except ValueError:
                idx = None
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
            raise SystemExit(f"字幕时间码非法（{start}-{end}）")
        out.append({"index": idx, "start": start, "end": end,
                    "text": norm(" ".join(block[ts_at + 1:]))})
    return out


def norm(text: str) -> str:
    return " ".join((text or "").split())


def event_parts(value: str) -> list:
    return [p.strip() for p in (value or "").split("+") if p.strip()]


def _merge(intervals: list, tol: float) -> list:
    out: list = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + tol:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def narration_spans(narration_rows: list) -> list:
    """**实际声段**，不是画面窗口，且用真实 offset。

    声段 = `[final_range 起点 + audio_offset_s, 同上 + 实测 audio_duration_s]`。
    一段 60s 的画面只配了 1s 旁白，就只能算 1s 有声，剩下 59s 是静默；
    `audio_offset_s` 由制作路径按真实 EDL 记进最终时间线，不靠"一般 ~0.2s"的假设。
    """
    spans = []
    for r in narration_rows:
        start = r["final"][0] + (r["audio_offset_s"] or 0.0)
        end = start + (r["audio_duration_s"] or 0.0)
        spans.append((start, end))
    return spans


def merged_audio_spans(narration_rows: list, tol: float) -> list:
    """声段取并集（与静默 gap 同口径）：占比也用它，不能再用 sum(spans)。"""
    return _merge(narration_spans(narration_rows), tol)


def _find_gaps(merged: list, total: float, threshold: float, tol: float) -> list:
    if not merged:
        return [(0.0, round(total, 3))] if total >= threshold else []
    gaps = []
    if merged[0][0] >= threshold:
        gaps.append((0.0, round(merged[0][0], 3)))
    for (_a, b), (c, _d) in zip(merged, merged[1:]):
        if c - b >= threshold:
            gaps.append((round(b, 3), round(c, 3)))
    if total - merged[-1][1] >= threshold:
        gaps.append((round(merged[-1][1], 3), round(total, 3)))
    return gaps


def _empty_report(threshold: float) -> dict:
    """同一个 JSON 形状，即使审计在列检查阶段就退出。消费方不必写防御性 .get 链。"""
    return {"silence": {"threshold_s": threshold, "gaps": [], "narration_ratio": None,
                        "narration_audio_s": None, "ledger_sha256": None},
            "holds": [], "anchors": [], "phase_crossings": [], "phases": {},
            "version_binding": {}, "receipt": {},
            "human_annotations_not_machine_verified": human_annotation_caveats(),
            "not_a_verdict_on": NOT_A_VERDICT_ON}


def _resolve_source_path(raw: str, manifest_dir: Path) -> Path:
    """相对路径按 **manifest 所在目录**解析，避免换 cwd 就误判成"源不存在"。"""
    p = Path(raw).expanduser()
    return p if p.is_absolute() else (manifest_dir / p)


def verify_receipt(rep: Report, receipt_path: Path, timeline: Path, subtitle: Path, audio: Path,
                   final_mp4: Path, assets: dict, used_assets: set,
                   edl_path: Path | None = None) -> dict:
    """制作 receipt：把这一份 timeline 与这三份产物绑在**同一次制作**上。

    它能证明的是"这几份产物是一起被产出的、且与这份 timeline 一致"；它**没有签名**，防不了伪造。
    没有它，"拿一份旧成片 + 新写一份声明了旧 hash 的时间线"就能自称语义正确。
    """
    if not receipt_path.is_file():
        rep.error(f"制作 receipt 不存在或未提供: {receipt_path} -> 无法证明这些产物属于同一次制作"
                  f"（只读旧媒体再手写一份声明，不构成验证）")
        return {}
    try:
        rec = json.loads(receipt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"receipt 不是 JSON: {exc}")
    if not isinstance(rec, dict):
        raise SystemExit("receipt 顶层必须是对象")
    out = {"path": str(receipt_path), "sha256": sha256_file(receipt_path),
           "produced_by": rec.get("produced_by"), "produced_at": rec.get("produced_at")}
    if not str(rec.get("schema", "")).startswith(RECEIPT_SCHEMA_PREFIX):
        rep.error(f"receipt schema={rec.get('schema')!r} 不是 {RECEIPT_SCHEMA_PREFIX}* -> 不是本流程的收据")
    if not str(rec.get("produced_by") or "").strip():
        rep.error("receipt 缺少 produced_by（哪条制作路径写出的）-> 无法追溯来源")

    for key, path, label in (("timeline", timeline, "时间线"), ("subtitle", subtitle, "字幕"),
                             ("audio", audio, "音轨"), ("final", final_mp4, "成片")):
        entry = rec.get(key) if isinstance(rec.get(key), dict) else {}
        want = entry.get("sha256")
        if not SHA256_RE.match(str(want or "")):
            rep.error(f"receipt 缺少 {key}.sha256 -> 无法把{label}绑到这一次制作")
            continue
        if path.is_file():
            live = sha256_file(path)
            if want != live:
                rep.error(f"receipt 里的 {key} sha256 与当前{label}文件不一致 -> "
                          f"这几份产物不是同一次制作的（旧媒体 + 新时间线会被拦下）")

    # 实际读过的源视频必须与 timeline 用到的素材是同一份。
    # 只抄 preflight 台账不算：preflight=A、SRC_VIDEO=B（同长不同画面）必须被检出。
    src_video = rec.get("source_video") if isinstance(rec.get("source_video"), dict) else {}
    sv_sha = src_video.get("sha256")
    out["source_video_sha256"] = sv_sha
    ledger_shas = {(assets.get(a) or {}).get("sha256") for a in used_assets}
    if not SHA256_RE.match(str(sv_sha or "")):
        rep.error("receipt 缺少 source_video.sha256 -> 无法确认这次制作读的是哪份源视频")
    elif sv_sha not in ledger_shas:
        rep.error(f"receipt 记录的 source_video {str(sv_sha)[:12]}… 与 timeline 用到的素材"
                  f"（preflight 台账）都不是同一份 -> 源被换过（同长不同画面也照样失败）")

    edl = rec.get("edl") if isinstance(rec.get("edl"), dict) else {}
    out["edl_sha256"] = edl.get("sha256")
    if not SHA256_RE.match(str(edl.get("sha256") or "")):
        rep.error("receipt 缺少 edl.sha256 -> 无法把这次制作绑到那份 EDL")
    elif edl_path is not None:
        if not edl_path.is_file():
            rep.error(f"--edl 指向的文件不存在: {edl_path}")
        elif sha256_file(edl_path) != edl.get("sha256"):
            rep.error("receipt 里的 edl sha256 与 --edl 不一致 -> 这份 receipt 不是由该 EDL 产出的")
        else:
            out["edl_verified"] = True

    srcs = rec.get("sources") if isinstance(rec.get("sources"), dict) else {}
    out["sources"] = sorted(srcs)
    for asset in sorted(used_assets):
        want = srcs.get(asset)
        if not SHA256_RE.match(str(want or "")):
            rep.error(f"receipt 没有记录素材 {asset} 的 sha256 -> 无法确认这次制作读的是哪份源")
        elif want != (assets.get(asset) or {}).get("sha256"):
            rep.error(f"receipt 记录的素材 {asset} sha256 与 preflight 台账不一致 -> 源对不上")
    return out


def build_audit_payload(timeline: Path, preflight_path: Path, ledger_path: Path,
                        subtitle_path: Path, audio_path: Path, final_mp4: Path,
                        receipt_path: Path, silence_threshold: float, tol: float,
                        edl_path: Path | None = None) -> dict:
    """跑完整审计并返回结构化 payload（不打印、不写文件）。

    `ready` 门槛从这里取判定，所以审计规则只有这一份实现；参数校验（含 finite/上界）也在这层，
    这样 CLI 与 library 两侧口径一致。
    """
    tol = validate_tol(tol)
    silence_threshold = validate_silence_threshold(silence_threshold)

    rep = Report()
    holds: list = []
    anchors: list = []
    phase_crossings: list = []
    phases: dict = {}
    narration_rows: list = []
    subtitle_sources: set = set()
    declared: dict = {"subtitle_sha256": set(), "audio_sha256": set(), "final_sha256": set()}
    asset_src_end: dict = {}
    used_assets: set = set()
    total_final = 0.0

    # --- preflight 台账（机器可读素材登记，同时是 stale 检测基线） ---
    if not preflight_path.is_file():
        raise SystemExit(f"preflight 报告不存在: {preflight_path}")
    try:
        pf = json.loads(preflight_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"preflight 报告不是 JSON: {exc}")
    assets = pf.get("assets") or {}
    manifest_dir = preflight_path.resolve().parent
    if not pf.get("ok", False):
        rep.error("preflight 报告自身未通过（输入状态未确定）-> 不得据此审计")
    if not assets:
        rep.error("preflight 报告没有任何素材条目")

    # --- timeline ---
    _delim, header, rows = read_table(timeline)
    missing = [c for c in BASE_COLUMNS + AUDIT_COLUMNS if c not in header]
    if missing:
        rep.error(f"缺少必需列: {', '.join(missing)}"
                  f"（审计列见 templates/timeline.md；缺失即无法判定锚点/阶段/保持帧/静默/版本绑定）")
        return _payload(rep, timeline, _empty_report(silence_threshold), 0)

    # 区间算术（clip==source 长度、final==clip/speed+freeze、重叠）只有**一个** owner：
    # structure 模式。这里折进它的报错，而不是再实现一遍同样的规则。
    rep.errors += [f"结构: {e}" for e in check_timeline(timeline).errors]

    idx = {c: header.index(c) for c in BASE_COLUMNS + AUDIT_COLUMNS}
    get_rows = []
    for n, row in enumerate(rows, start=2):
        if len(row) < len(header):
            rep.error(f"第 {n} 行列数不足（{len(row)} < {len(header)}）")
            continue
        get_rows.append((n, row))
    timeline_events: set = set()
    for _n, row in get_rows:
        timeline_events.update(event_parts(row[idx["event_id"]]))

    for n, row in get_rows:
        def get(col: str, row=row) -> str:
            return row[idx[col]].strip()

        asset, event_raw = get("asset_id"), get("event_id")
        if not asset or not event_raw:
            rep.error(f"第 {n} 行 asset_id/event_id 为空")
            continue
        owns = event_parts(event_raw)
        if not owns:
            rep.error(f"第 {n} 行 event_id={event_raw!r} 解析不出事件 id")
            continue

        src, clip, final = (parse_range(get("source_range")), parse_range(get("clip_range")),
                            parse_range(get("final_range")))
        sf = parse_speed_freeze(get("speed/freeze"))
        if src is None or clip is None or final is None or sf is None:
            rep.error(f"{event_raw}: 区间或 speed/freeze 无法解析（先跑 structure 模式的 timeline 检查）")
            continue
        speed, freeze = sf
        if speed <= 0:
            continue                      # structure 模式已经报错；这里只避免除零
        final_len = final[1] - final[0]
        total_final = max(total_final, final[1])
        used_assets.add(asset)
        asset_src_end[asset] = max(asset_src_end.get(asset, 0.0), src[1])

        narration = get("narration_text")
        has_narration = not is_nullish(narration)
        event_phase, claim_phase = get("event_phase").lower(), get("claim_phase").lower()
        claim_mode = get("claim_mode").lower()
        if event_phase not in PHASE_ORDER:
            rep.error(f"{event_raw}: event_phase={get('event_phase')!r} 非法，只能是 "
                      f"{'/'.join(PHASE_ORDER)}")
        else:
            phases[event_phase] = phases.get(event_phase, 0) + 1

        if has_narration:
            # ---- 阶段：同 phase 默认允许；跨 phase 必须显式声明为事后复盘 ----
            if claim_mode not in CLAIM_MODES:
                rep.error(f"{event_raw}: claim_mode={get('claim_mode')!r} 非法，只能是 "
                          f"{'/'.join(CLAIM_MODES)}（跨 phase 必须显式声明 retrospective）")
            if claim_phase not in PHASE_ORDER:
                rep.error(f"{event_raw}: claim_phase={get('claim_phase')!r} 非法（旁白行必须声明主张阶段）")
            elif event_phase in PHASE_ORDER:
                if claim_phase == event_phase:
                    if claim_mode == "retrospective":
                        rep.warn(f"{event_raw}: 声明了 retrospective，但主张阶段与画面阶段相同"
                                 f"（{claim_phase}）——多此一举，确认不是误填")
                elif claim_mode != "retrospective":
                    rep.error(f"{event_raw}: 旁白主张的阶段「{claim_phase}」与画面阶段「{event_phase}」"
                              f"不同，但 claim_mode={get('claim_mode')!r} —— 跨 phase 必须显式写成 "
                              f"`retrospective`，否则阶段检查会被绕开")
                else:
                    # 全局 phase 顺序**不是**时间轴（循环游戏里 map/shop/rest/battle 会反复出现），
                    # 所以只在一个事件内部、"画面还没到那个阶段却已经在讲结果"时判失败。
                    later = PHASE_ORDER.index(claim_phase) > PHASE_ORDER.index(event_phase)
                    same_event = len(owns) == 1 and set(event_parts(get("anchor_event"))) <= set(owns)
                    if later and same_event:
                        rep.error(
                            f"{event_raw}: 阶段错位——同一事件上「画面还在 {event_phase}，"
                            f"旁白已经在讲 {claim_phase}」，这就是「战斗还没打完就在说打完了/我拿了 X」。"
                            f"把台词移到真实发生的那一段，或让这一镜真的覆盖那个事件")
                    else:
                        phase_crossings.append({"event_id": event_raw, "event_phase": event_phase,
                                                "claim_phase": claim_phase, "declared": "retrospective"})

            # ---- 锚点交叉核对：声明的 (asset, event, interval) 必须与真实画面源区间对得上 ----
            anchor_asset = get("anchor_asset")
            if is_nullish(anchor_asset):
                rep.error(f"{event_raw}: 缺少 anchor_asset —— 旁白必须声明它讲的是哪个素材的源区间")
            elif anchor_asset != asset:
                rep.error(f"{event_raw}: anchor_asset={anchor_asset!r} 而画面来自 {asset!r}；"
                          f"跨素材锚点无法机器核验（把旁白挂到该素材的画面上，或改锚点）")

            anchor_events = event_parts(get("anchor_event"))
            if not anchor_events:
                rep.error(f"{event_raw}: 缺少 anchor_event —— 旁白必须声明它讲的是哪个/哪些回合")
            else:
                unknown = [e for e in anchor_events if e not in timeline_events]
                if unknown:
                    rep.error(f"{event_raw}: anchor_event 引用了 timeline 里不存在的事件 {unknown}")
                extra = [e for e in anchor_events if e not in owns]
                if extra:
                    rep.error(
                        f"{event_raw}: 旁白锚定到 {extra}，但这一行画面属于 {owns} —— 换了事件不能默默过。"
                        f"要么把台词移到该事件的画面上，要么把 event_id 写成 "
                        f"`{'+'.join(owns + extra)}` 显式声明这是跨回合镜头")

            anchor_range = strict_range(get("anchor_source"))
            if anchor_range is None:
                rep.error(f"{event_raw}: " + range_error("anchor_source", get("anchor_source")))
            else:
                if not (src[0] - tol <= anchor_range[0] and anchor_range[1] <= src[1] + tol):
                    rep.error(
                        f"{event_raw}: 旁白引用的源区间 {anchor_range[0]}-{anchor_range[1]}s 不在这一行"
                        f"真实画面的源区间 {src[0]}-{src[1]}s 内 —— 说的那一刻没有在画面上"
                        f"（跨回合句必须让画面真正覆盖它引用的整段）")
                if len(owns) > 1 and not (anchor_range[0] <= src[0] + tol
                                          and anchor_range[1] >= src[1] - tol):
                    rep.error(
                        f"{event_raw}: 这是跨回合镜头（{'+'.join(owns)}），但旁白只引用 "
                        f"{anchor_range[0]}-{anchor_range[1]}s，没有覆盖镜头跨度 {src[0]}-{src[1]}s —— "
                        f"跨回合句必须由连续画面承载")
                anchors.append({"event_id": event_raw, "anchor_asset": anchor_asset,
                                "anchor_event": anchor_events,
                                "anchor_source": [anchor_range[0], anchor_range[1]],
                                "shot_source": [src[0], src[1]]})

            adur = as_finite_float(get("audio_duration_s"))
            off = as_finite_float(get("audio_offset_s"))
            if adur is None or adur <= 0:
                rep.error(f"{event_raw}: audio_duration_s 必须是实测的有限正数（得到 "
                          f"{get('audio_duration_s')!r}）")
            if off is None or off < 0:
                rep.error(f"{event_raw}: audio_offset_s 必须是有限非负数（真实 offset，"
                          f"不许靠「一般 ~0.2s」的假设）：得到 {get('audio_offset_s')!r}")
            if adur is not None and adur > 0 and off is not None and off >= 0 \
                    and off + adur > final_len + tol:
                rep.error(f"{event_raw}: 旁白放不进画面窗口——offset {off:.3f}s + 实测 {adur:.3f}s "
                          f"> 窗口 {final_len:.3f}s（改稿 / 调画面 / 显式定格三选一）")

            subs = get("subtitle_source")
            if is_nullish(subs):
                rep.error(f"{event_raw}: 旁白行缺少 subtitle_source（字幕必须来自同一版采用稿）")
            else:
                subtitle_sources.add(subs)

            for col in ("subtitle_sha256", "audio_sha256", "final_sha256"):
                raw = get(col)
                if not SHA256_RE.match(raw):
                    rep.error(f"{event_raw}: {col}={raw!r} 不是 64 位小写十六进制摘要 —— "
                              f"版本绑定必须写内容 hash，否则同段数改词/换文件都检不出来")
                else:
                    declared[col].add(raw)

            narration_rows.append({"event_id": event_raw, "asset_id": asset,
                                   "text": norm(narration), "final": final,
                                   "audio_offset_s": off, "audio_duration_s": adur})
        else:
            if not is_nullish(claim_phase):
                rep.warn(f"{event_raw}: 画面行填了 claim_phase={claim_phase!r} 但没有旁白")

        # ---- 保持帧：必须带标注，锚点要在画面区间（+旁白引用区间 / 候选可见区间）内 ----
        if freeze > TOL:
            mark = get("hold_mark")
            if is_nullish(mark):
                rep.error(f"{event_raw}: 登记了 {freeze:.2f}s 冻结但 hold_mark 为空 —— "
                          f"不许用无标注的长静帧填配音（要么换成连续动作，要么把标注写清楚）")
            else:
                m = TIMECODE_RE.search(mark)
                if not m:
                    rep.error(f"{event_raw}: hold_mark 里读不出源时间码（{mark!r}）——"
                              f"标注必须写明保持的是**哪一帧**")
                else:
                    anchor = as_finite_float(m.group(1))
                    if anchor is None or anchor < 0:
                        rep.error(f"{event_raw}: hold_mark 的时间码非法（{m.group(1)!r}）")
                    elif not (src[0] - tol <= anchor <= src[1] + tol):
                        rep.error(f"{event_raw}: 保持帧锚点 {anchor}s 不在本段源区间 "
                                  f"{src[0]}-{src[1]}s 内")
                    else:
                        burned = get("hold_burned_in").lower()
                        if burned != "yes":
                            rep.error(
                                f"{event_raw}: 登记了冻结和标注，但 hold_burned_in={get('hold_burned_in')!r}"
                                f" —— 表格里有 mark 不代表画面上真的标注了。支持烧录的制作路径要写 yes，"
                                f"没有烧录能力的路径必须保持 draft")
                        hold = {"event_id": event_raw, "freeze_s": round(freeze, 3),
                                "anchor_s": anchor, "phase": event_phase, "mark": mark,
                                "burned_in": burned}
                        if has_narration:
                            ar = strict_range(get("anchor_source"))
                            if ar is not None and not (ar[0] - tol <= anchor <= ar[1] + tol):
                                rep.error(f"{event_raw}: 保持帧锚点 {anchor}s 落在旁白引用区间 "
                                          f"{ar[0]}-{ar[1]}s 之外")
                        if event_phase == "reward" or claim_phase == "reward":
                            rng = strict_range(get("visible_window"))
                            if rng is None:
                                rep.error(f"{event_raw}: 奖励段的保持帧必须写明合法的 visible_window"
                                          f"（候选/结论真正可见的源区间）："
                                          + range_error("visible_window", get("visible_window")))
                            elif not (rng[0] - tol <= anchor <= rng[1] + tol):
                                rep.error(
                                    f"{event_raw}: 奖励保持帧锚点 {anchor}s 落在候选可见区间 "
                                    f"{rng[0]}-{rng[1]}s **之外**——定格会停在候选已经消失的画面上")
                            else:
                                holds.append(hold)
                        else:
                            holds.append(hold)
        else:
            if not is_nullish(get("visible_window")):
                rep.warn(f"{event_raw}: 填了 visible_window 但没有冻结（该列不会被使用）")

    # --- 源台账 stale 检测：报告里的摘要必须与磁盘上的真实文件一致 ---
    for asset in sorted(used_assets):
        rec = assets.get(asset)
        if rec is None:
            rep.error(f"素材 {asset} 在 timeline 里被引用，但 preflight 报告里没有它 -> 无法核验")
            continue
        raw_path = str(rec.get("source_path") or "")
        if not raw_path:
            rep.error(f"素材 {asset}: preflight 报告没有 source_path -> 无法核验")
            continue
        sp = _resolve_source_path(raw_path, manifest_dir)
        if not sp.is_file():
            rep.error(f"素材 {asset}: 源文件不存在: {sp}（相对路径按 manifest 目录解析）-> "
                      f"无法核验（不得当作已验证）")
            continue
        want_sha, want_size = rec.get("sha256"), rec.get("size_bytes")
        if not SHA256_RE.match(str(want_sha or "")):
            rep.error(f"素材 {asset}: preflight 报告的 sha256 形状非法 -> 无法做 stale 判定")
        elif want_sha != sha256_file(sp):
            rep.error(f"素材 {asset}: stale manifest —— 报告的 sha256 与磁盘实测不一致"
                      f"（源变了就必须按真实内容重算，不得沿用旧台账）")
        if not isinstance(want_size, int) or want_size <= 0:
            rep.error(f"素材 {asset}: preflight 报告的 size_bytes 非法: {want_size!r}")
        elif want_size != sp.stat().st_size:
            rep.error(f"素材 {asset}: stale manifest —— 报告的 size_bytes 与磁盘实测不一致")
        src_dur = as_finite_float(rec.get("duration_s"))
        if src_dur is None or src_dur <= 0:
            rep.error(f"素材 {asset}: preflight 时长非法: {rec.get('duration_s')!r}")
        elif asset in asset_src_end and asset_src_end[asset] > src_dur + tol:
            rep.error(f"素材 {asset}: timeline 用到源 {asset_src_end[asset]:.3f}s，"
                      f"但该素材只有 {src_dur:.3f}s -> 剪点超出素材长度")
        if rec.get("status") != "determined":
            rep.error(f"素材 {asset}: preflight status={rec.get('status')!r} != determined")
        sig = rec.get("audio_signal")
        if sig == "absent":
            rep.note_unverified(f"{asset}: 源无音轨 —— 成片音轨只能来自旁白；不得伪造游戏原声")
        elif sig == "silent":
            rep.note_unverified(f"{asset}: 音轨无真实信号（静音音轨）—— 不得当作有游戏原声")
        if rec.get("frame_sampling") == "sparse":
            rep.note_unverified(f"{asset}: 源为稀疏采集（约 {rec.get('avg_fps')}fps）—— "
                                f"时间正确但运动信息有限，不得声称画面动感充足")

    # --- 长静默逐段依据（按**实际声段**，不是画面窗口） ---
    spans = narration_spans(narration_rows)
    union = merged_audio_spans(narration_rows, tol)
    gaps = _find_gaps(union, total_final, silence_threshold, tol)
    ledger_rows: list = []
    if ledger_path.is_file():
        _d, lheader, lrows = read_table(ledger_path)
        missing_l = [c for c in LEDGER_COLUMNS if c not in lheader]
        if missing_l:
            rep.error(f"静默台账缺少必需列: {', '.join(missing_l)}")
        else:
            li = {c: lheader.index(c) for c in LEDGER_COLUMNS}
            for n, row in enumerate(lrows, start=2):
                if len(row) < len(lheader):
                    rep.error(f"静默台账第 {n} 行列数不足")
                    continue
                gid = row[li["gap_id"]].strip() or f"row{n}"
                rng = strict_range(row[li["final_range"]])
                disp = row[li["disposition"]].strip().lower()
                reason = row[li["reason"]].strip()
                if rng is None:
                    rep.error(f"静默台账 {gid}: " + range_error("final_range",
                                                                row[li["final_range"]]))
                    continue
                if disp not in SILENCE_DISPOSITIONS:
                    rep.error(f"静默台账 {gid}: disposition={disp!r} 非法，只能是 "
                              f"{'/'.join(SILENCE_DISPOSITIONS)}")
                if is_nullish(reason) or len(reason) < 8:
                    rep.error(f"静默台账 {gid}: reason 太短/为空 —— 逐段静默必须有依据，"
                              f"不能只写占位符")
                ledger_rows.append({"gap_id": gid, "range": rng, "disposition": disp,
                                    "reason": reason, "used": False})
    else:
        rep.error(f"静默台账不存在: {ledger_path}")

    matched_gaps = []
    for g0, g1 in gaps:
        hit = next((e for e in ledger_rows
                    if abs(e["range"][0] - g0) <= 1.0 and abs(e["range"][1] - g1) <= 1.0), None)
        if hit is None:
            rep.error(f"成片在 {g0:.3f}-{g1:.3f}s 有 {g1 - g0:.3f}s 无口播（≥ 阈值 "
                      f"{silence_threshold:g}s），静默台账里没有对应依据 -> 逐段补 keep/cut/"
                      f"narration_added 与理由")
            matched_gaps.append({"final_range": [g0, g1], "duration_s": round(g1 - g0, 3),
                                 "disposition": None, "reason": None})
            continue
        hit["used"] = True
        if hit["disposition"] == "cut":
            rep.error(f"静默台账 {hit['gap_id']} 声明 disposition=cut（本源静默已被剪掉），"
                      f"但成片 {g0:.3f}-{g1:.3f}s 仍然没有口播 -> 自相矛盾")
        matched_gaps.append({"final_range": [g0, g1], "duration_s": round(g1 - g0, 3),
                             "disposition": hit["disposition"], "reason": hit["reason"]})
    for entry in ledger_rows:
        if not entry["used"]:
            rep.error(f"静默台账 {entry['gap_id']}（{entry['range'][0]}-{entry['range'][1]}s）"
                      f"在成片里找不到对应的静默 -> 台账陈旧（重新从实际采用时间线生成）")

    # 占比与 gap 同口径：都用合并后的 union，不是 sum(每段)
    narration_audio = sum(e - s for s, e in union)
    narration_ratio = (narration_audio / total_final) if total_final else 0.0

    # --- 内容级版本绑定 + 制作 receipt ---
    binding = {"timeline": str(timeline), "timeline_sha256": sha256_file(timeline),
               "narration_rows": len(narration_rows), "total_final_s": round(total_final, 3),
               "narration_audio_s": round(narration_audio, 3),
               "subtitle_source": sorted(subtitle_sources)[0] if len(subtitle_sources) == 1 else None,
               "subtitle_sources": sorted(subtitle_sources),
               "declared": {k: sorted(v) for k, v in declared.items()}}
    if len(subtitle_sources) > 1:
        rep.error(f"同一份 timeline 里出现多个 subtitle_source: "
                  f"{', '.join(sorted(subtitle_sources))} -> 字幕/配音版本混用")
    if not subtitle_sources:
        rep.error("没有任何旁白行声明 subtitle_source -> 无法做版本绑定")
    if not narration_rows:
        rep.error("这份 timeline 没有任何旁白行 -> 不构成带解说的成片时间线")
    for key, label in (("subtitle_sha256", "字幕"), ("audio_sha256", "音轨"),
                       ("final_sha256", "成片")):
        if len(declared[key]) > 1:
            rep.error(f"同一份 timeline 里出现多个 {key}: {', '.join(sorted(declared[key]))} -> "
                      f"{label}版本混用")
        elif not declared[key]:
            rep.error(f"没有任何旁白行声明 {key} -> 无法把{label}绑定到这一版 timeline")

    # 字幕：内容 hash + 段数 + 逐条文本 + 逐条时点（同段数改词/改时间必须失败）
    cues = parse_srt(subtitle_path)
    binding["subtitle"] = str(subtitle_path)
    binding["subtitle_sha256"] = sha256_file(subtitle_path)
    binding["subtitle_cues"] = len(cues)
    want_sub = declared["subtitle_sha256"]
    if len(want_sub) == 1 and next(iter(want_sub)) != binding["subtitle_sha256"]:
        rep.error(f"字幕内容 hash 不符：timeline 声明 {next(iter(want_sub))[:12]}…，"
                  f"实际 {binding['subtitle_sha256'][:12]}… -> 这不是这一版时间线的字幕"
                  f"（同段数改词/改时间也照样失败）")
    if len(cues) != len(narration_rows):
        rep.error(f"字幕段数 {len(cues)} != 旁白行数 {len(narration_rows)} -> "
                  f"字幕不是从这一版采用时间线生成的")
    elif cues:
        ordered = sorted(narration_rows, key=lambda r: r["final"][0])
        text_bad = [r["event_id"] for cue, r in zip(cues, ordered) if norm(cue["text"]) != r["text"]]
        timing_bad = []
        for cue, r in zip(cues, ordered):
            s0 = r["final"][0] + (r["audio_offset_s"] or 0.0)
            s1 = s0 + (r["audio_duration_s"] or 0.0)
            # 本契约是 **1 cue ↔ 1 旁白行**：起止要贴合实际声段，不是"落在里面就行"
            # （否则整句压成末尾 0.1s 也会通过）。容差是显式允许的同步容差。
            if abs(cue["start"] - s0) > tol or abs(cue["end"] - s1) > tol:
                timing_bad.append(r["event_id"])
        if text_bad:
            rep.error(f"字幕文本与该段口播稿不一致（同段数但改了词）：{', '.join(text_bad[:4])}"
                      f" -> 字幕必须是实际采用稿，不是另写一份")
        if timing_bad:
            rep.error(f"字幕起止没有贴合该段的实际声段（含真实 offset，容差 {tol}s）："
                      f"{', '.join(timing_bad[:4])} -> 整句压成末尾一小段也算不合格；"
                      f"字幕必须是这一版配音对齐的产物")

    # 音轨：内容 hash + 时长 + **真的有音轨**
    if not audio_path.is_file():
        rep.error(f"音轨文件不存在: {audio_path}")
    else:
        adur, aerr = media_duration(audio_path)
        a_stream, a_serr = has_audio_stream(audio_path)
        binding["audio"] = str(audio_path)
        binding["audio_sha256"] = sha256_file(audio_path)
        binding["audio_duration_s"] = adur
        binding["audio_has_stream"] = a_stream
        want_aud = declared["audio_sha256"]
        if len(want_aud) == 1 and next(iter(want_aud)) != binding["audio_sha256"]:
            rep.error(f"音轨内容 hash 不符：timeline 声明 {next(iter(want_aud))[:12]}…，"
                      f"实际 {binding['audio_sha256'][:12]}… -> 这是旧音轨，不是这一版时间线的产物")
        if a_stream is None:
            rep.error(f"无法判定音轨是否真的有音频流（{a_serr}）")
        elif a_stream is False:
            rep.error("--audio 指向的文件没有音频流 —— 只凭容器时长不算音轨")
        if adur is None:
            rep.error(f"音轨时长无法测量（{aerr}）-> 无法做版本绑定")
        else:
            binding["audio_delta_s"] = round(adur - total_final, 3)
            if abs(adur - total_final) > tol:
                rep.error(f"音轨 {adur:.3f}s 与时间线总长 {total_final:.3f}s 相差 "
                          f"{abs(adur - total_final):.3f}s（容差 {tol}）-> 音轨不是这一版时间线的产物")

    # 成片：必须给、必须存在、内容 hash + 时长 + 有音轨 + 与时间线同长
    if not final_mp4.is_file():
        rep.error(f"成片文件不存在或未提供: {final_mp4} -> 未验证，不得当作已审片")
    else:
        fsha = sha256_file(final_mp4)
        fdur, ferr = media_duration(final_mp4)
        faud, _ferr2 = has_audio_stream(final_mp4)
        binding["final_mp4"] = str(final_mp4)
        binding["final_sha256"] = fsha
        binding["final_duration_s"] = fdur
        want_fin = declared["final_sha256"]
        if len(want_fin) == 1 and next(iter(want_fin)) != fsha:
            rep.error(f"成片内容 hash 不符：timeline 声明 {next(iter(want_fin))[:12]}…，"
                      f"实际 {fsha[:12]}… -> 这是旧成片，不是这一版时间线的产物")
        if fdur is None:
            rep.error(f"成片时长无法测量（{ferr}）-> 无法做版本绑定")
        elif abs(fdur - total_final) > tol:
            rep.error(f"成片 {fdur:.3f}s 与时间线总长 {total_final:.3f}s 相差 "
                      f"{abs(fdur - total_final):.3f}s（容差 {tol}）-> 最终媒体不是这一版时间线的产物")
        if faud is False:
            rep.error("成片没有音轨——带解说的成片必须有音轨")

    # 制作 receipt：把 timeline 与三份产物绑到同一次制作上
    receipt = verify_receipt(rep, receipt_path, timeline, subtitle_path, audio_path, final_mp4,
                             assets, used_assets, edl_path)

    rep.note_unverified(f"旁白占比 {narration_ratio * 100:.1f}%（按实测声段 {narration_audio:.3f}s / "
                        f"成片 {total_final:.3f}s；这只是占比，不是内容通过）")
    rep.note_unverified("听感、抑扬、断句自然度：机器不判，需真人听")
    rep.note_unverified("事实语义（选牌/伤害/胜负/身份）：必须回源帧，机器不裁定")
    for ev in [r["event_id"] for r in narration_rows if len(event_parts(r["event_id"])) > 1]:
        rep.note_unverified(f"{ev}: 跨回合镜头——机器只确认旁白引用区间覆盖了镜头跨度，"
                            f"不确认画面里确实是那几个回合")
    for x in phase_crossings:
        rep.note_unverified(f"{x['event_id']}: 声明为跨阶段事后复盘（画面 {x['event_phase']} / "
                            f"主张 {x['claim_phase']}）——机器不核对这段回顾是否属实")
    for hold in holds:
        rep.note_unverified(f"{hold['event_id']}: 冻结 {hold['freeze_s']}s 停在源 "
                            f"{hold['anchor_s']}s —— 标注与区间自洽，但「该帧确实值得保持」仍需人看")

    report = {
        "silence": {"threshold_s": silence_threshold, "gaps": matched_gaps,
                    "narration_ratio": round(narration_ratio, 4),
                    "narration_audio_s": round(narration_audio, 3),
                    "audio_spans": [[round(s, 3), round(e, 3)] for s, e in union],
                    "audio_spans_raw": [[round(s, 3), round(e, 3)] for s, e in spans],
                    "ledger_sha256": sha256_file(ledger_path) if ledger_path.is_file() else None},
        "holds": holds,
        "anchors": anchors,
        "phase_crossings": phase_crossings,
        "phases": phases,
        "version_binding": binding,
        "receipt": receipt,
        "human_annotations_not_machine_verified": human_annotation_caveats(),
        "not_a_verdict_on": NOT_A_VERDICT_ON,
    }
    return _payload(rep, timeline, report, len(get_rows))


def _payload(rep: Report, timeline: Path, report: dict, rows_checked: int) -> dict:
    return {"mode": "audit", "target": str(timeline), "semantics": AUDIT_SEMANTICS,
            "ok": not rep.errors, "checked_rows": rows_checked,
            "errors": rep.errors, "warnings": rep.warnings,
            "unverified": rep.unverified, "report": report}


def run_audit(timeline: Path, preflight_path: Path, ledger_path: Path, subtitle_path: Path,
              audio_path: Path, final_mp4: Path, receipt_path: Path, silence_threshold: float,
              tol: float, as_json: bool, report_out, edl_path: Path | None = None) -> int:
    payload = build_audit_payload(timeline, preflight_path, ledger_path, subtitle_path, audio_path,
                                  final_mp4, receipt_path, silence_threshold, tol, edl_path)
    if report_out is not None:
        report_out.parent.mkdir(parents=True, exist_ok=True)
        report_out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        gaps = (payload.get("report") or {}).get("silence", {}).get("gaps") or []
        print(f"[audit] {timeline}  检查 {payload['checked_rows']} 行")
        print(f"  语义: {AUDIT_SEMANTICS}")
        print(f"  静默台账: {len(gaps)} 段 ≥ {silence_threshold:g}s")
        for w in payload["warnings"]:
            print(f"  WARN  {w}")
        for u in payload["unverified"]:
            print(f"  UNVERIFIED  {u}")
        for e in payload["errors"]:
            print(f"  ERROR {e}")
        print("结果：" + ("时间线审计通过（≠审片通过）" if payload["ok"] else "审计未通过"))
        if report_out is not None:
            print(f"  report: {report_out}")
    return 0 if payload["ok"] else 1


# --------------------------------------------------------------------- cli

def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="check_timeline_audit.py",
        description="输入预检与采用时间线语义审计（确定性；不看画面、不调用模型）")
    sub = ap.add_subparsers(dest="mode", required=True)

    p = sub.add_parser("preflight", help="独立探测每个源素材的音轨/信号/采集密度状态")
    p.add_argument("assets", nargs="+", help="<asset_id>=<path> 或直接给路径（id 取文件名）")
    p.add_argument("--out", default=None, help="把机器可读台账写到该 JSON 路径")
    p.add_argument("--min-fps", type=float, default=DEFAULT_MIN_FPS,
                   help=f"低于该实测帧率视为稀疏采集（默认 {DEFAULT_MIN_FPS:g}）")
    p.add_argument("--silence-db", type=float, default=DEFAULT_SILENCE_DB,
                   help=f"max_volume ≤ 该值视为静音音轨（默认 {DEFAULT_SILENCE_DB:g} dB）")
    p.add_argument("--json", action="store_true")

    a = sub.add_parser("audit", help="锚点/阶段/保持帧/静默/内容级版本绑定/receipt/stale 台账审计")
    a.add_argument("timeline")
    a.add_argument("--preflight", required=True, help="preflight 写出的素材台账 JSON")
    a.add_argument("--silence-ledger", required=True, help="静默台账 TSV（逐段给依据）")
    a.add_argument("--subtitle", required=True, help="实际采用的字幕文件（.srt）")
    a.add_argument("--audio", required=True, help="实际采用的旁白音轨（必须真的有音频流）")
    a.add_argument("--final-mp4", required=True, help="实际导出的成片（内容 hash/时长/音轨核验）")
    a.add_argument("--receipt", required=True, help="制作 receipt（build_sample.sh 在制作时写出）")
    a.add_argument("--silence-threshold", type=float, default=DEFAULT_SILENCE_S,
                   help=f"多长的无口播算「需要依据」（默认 {DEFAULT_SILENCE_S:g}s）")
    a.add_argument("--tol", type=float, default=DEFAULT_SYNC_TOL,
                   help=f"时长/区间比对容差（默认 {DEFAULT_SYNC_TOL}）")
    a.add_argument("--edl", default=None,
                   help="可选：制作用的 EDL；给了就核对 receipt 里的 edl sha256")
    a.add_argument("--report-out", default=None, help="把完整报告写到该 JSON 路径")
    a.add_argument("--json", action="store_true")

    args = ap.parse_args(argv)
    try:
        if args.mode == "preflight":
            min_fps = as_finite_float(args.min_fps)
            if args.min_fps is None or min_fps is None or min_fps < 0:
                print(f"ERROR: --min-fps 必须是有限非负数: {args.min_fps!r}", file=sys.stderr)
                return 2
            if args.silence_db is None or as_finite_float(args.silence_db) is None:
                print(f"ERROR: --silence-db 必须是有限数: {args.silence_db!r}", file=sys.stderr)
                return 2
        else:
            validate_tol(args.tol)
            validate_silence_threshold(args.silence_threshold)
    except SystemExit as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if not shutil.which("ffprobe") or not shutil.which("ffmpeg"):
        print("ERROR: 需要 ffprobe/ffmpeg（本工具不做任何模型调用）", file=sys.stderr)
        return 2

    try:
        if args.mode == "preflight":
            return run_preflight(args.assets, args.min_fps, args.silence_db,
                                 Path(args.out).expanduser() if args.out else None, args.json)
        return run_audit(Path(args.timeline).expanduser(), Path(args.preflight).expanduser(),
                         Path(args.silence_ledger).expanduser(), Path(args.subtitle).expanduser(),
                         Path(args.audio).expanduser(), Path(args.final_mp4).expanduser(),
                         Path(args.receipt).expanduser(), args.silence_threshold, args.tol,
                         args.json, Path(args.report_out).expanduser() if args.report_out else None,
                         Path(args.edl).expanduser() if args.edl else None)
    except (SystemExit, OSError, ValueError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"mode": args.mode, "ok": False, "errors": [str(exc)],
                              "warnings": [], "unverified": [], "checked_rows": 0},
                             ensure_ascii=False, indent=2))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
