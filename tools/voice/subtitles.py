#!/usr/bin/env python3
"""subtitles.py — 字幕分页与时基写入（SRT / ASS），纯标准库。

为什么需要这一层（都是有现场依据的，不是设计洁癖）：

1. **一条口播 ≠ 一条字幕 cue**。配音按语义组合成，字幕必须按"听到的短语"分页，
   否则一条 25 字的 cue 会被渲染成 3 行、压到 HUD 上。
2. **ASS 时间戳必须用总厘秒再拆分**。`int(round(t % 1 * 100))` 会把 0.999 写成 `.100`
   （实测踩过），所以这里统一走 `total = round(t*100)` 再 divmod。
3. **PlayRes 必须等于视频尺寸**。SRT→ASS 的默认脚本空间是 384×288，
   `force_style` 里的 FontSize/MarginV 按那个空间解释，在 960×966 上会被放大约 3.35 倍。
   所以 ASS 头由调用方给出真实 W/H（burn_subs.py 会从 ffprobe 读）。
4. **没有 PIL、没有临时 PNG**：这一步只产出文本文件，烧录交给 ffmpeg 的 `ass=`（libass）。

本模块只做"文本 → cue 表 → 文件"，不碰视频、不调模型。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

# 断句标点：出现在这些字符之后就是一个可分的短语边界（标点跟着前一个短语）。
PHRASE_BREAKS = "。！？；…，、：,.;:!?）)】」』》"
# 这些标点后面**不**断（避免"1.5 秒"被切开）。
NO_BREAK_AFTER = ".0123456789"

SRT_TS = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


@dataclass
class Cue:
    start: float
    end: float
    text: str

    def as_row(self) -> dict:
        return {"start": round(self.start, 3), "end": round(self.end, 3), "text": self.text}


# --------------------------------------------------------------------------- 宽度

def _is_wide(ch: str) -> bool:
    o = ord(ch)
    return (
        0x1100 <= o <= 0x115F or 0x2E80 <= o <= 0xA4CF or 0xAC00 <= o <= 0xD7A3
        or 0xF900 <= o <= 0xFAFF or 0xFE30 <= o <= 0xFE6F or 0xFF00 <= o <= 0xFF60
        or 0xFFE0 <= o <= 0xFFE6 or 0x20000 <= o <= 0x3FFFD
    )


def display_width(text: str) -> float:
    """近似的显示宽度（以 CJK 全角为 1.0，ASCII 约 0.55）。用于分页与时长比例。"""
    return sum(1.0 if _is_wide(c) else 0.55 for c in text)


def visible_len(text: str) -> int:
    return len(re.sub(r"\s", "", text))


# --------------------------------------------------------------------------- 分页

def split_phrases(text: str) -> list[str]:
    """按标点把一句话切成短语；标点留在前一个短语上。"""
    text = re.sub(r"\s*\n\s*", "", text.strip())
    out, cur = [], ""
    for i, ch in enumerate(text):
        cur += ch
        if ch in PHRASE_BREAKS:
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt and nxt in NO_BREAK_AFTER:
                continue
            out.append(cur)
            cur = ""
    if cur:
        out.append(cur)
    return out or ([text] if text else [])


def _hard_split(phrase: str, max_chars: float) -> list[str]:
    parts, cur = [], ""
    for ch in phrase:
        if cur and display_width(cur + ch) > max_chars:
            parts.append(cur)
            cur = ch
        else:
            cur += ch
    if cur:
        parts.append(cur)
    return parts


def page_text(text: str, start: float, end: float, max_chars: float) -> list[Cue]:
    """把一段口播文本在该段的**实测时长**内按短语分页（时长按可见字宽比例分配）。

    字幕仍对应"整段配音"这一件事，只是分页显示；不做逐短句合成。
    """
    chunks: list[str] = []
    for ph in split_phrases(text):
        if display_width(ph) <= max_chars:
            chunks.append(ph)
            continue
        chunks.extend(_hard_split(ph, max_chars))
    # 相邻短块合并，避免"我 / 觉得"这种碎片
    merged: list[str] = []
    for ch in chunks:
        if merged and display_width(merged[-1] + ch) <= max_chars:
            merged[-1] += ch
        else:
            merged.append(ch)
    if not merged:
        return []
    total = max(0.0, end - start)
    weights = [max(0.5, display_width(c)) for c in merged]
    wsum = sum(weights)
    cues, acc = [], 0.0
    for i, (ch, w) in enumerate(zip(merged, weights)):
        acc += w
        t1 = end if i == len(merged) - 1 else start + total * acc / wsum
        cues.append(Cue(round(start + total * (acc - w) / wsum, 3), round(t1, 3), ch))
    # 单调 + 末条精确落点
    for i in range(1, len(cues)):
        if cues[i].start < cues[i - 1].end:
            cues[i].start = cues[i - 1].end
    if cues:
        cues[-1].end = round(end, 3)
    return [c for c in cues if c.end > c.start and c.text.strip()]


# --------------------------------------------------------------------------- SRT / ASS

def _srt_ts(t: float) -> str:
    total = int(round(max(0.0, t) * 1000))
    h, rem = divmod(total, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ts_ass(t: float) -> str:
    """ASS 时间戳：**先取总厘秒再拆分**，避免 0.999 → `.100` 这类进位错误。"""
    total = int(round(max(0.0, t) * 100))
    h, rem = divmod(total, 360_000)
    m, rem = divmod(rem, 6_000)
    s, cs = divmod(rem, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    # `{}` 会被 libass 当覆盖标签；换行用 \N。这里不静默丢字，而是转成全角可见字符。
    return text.replace("\\", "＼").replace("{", "｛").replace("}", "｝").replace("\n", r"\N")


def write_srt(cues: list[Cue], path: Path) -> Path:
    blocks = [f"{i}\n{_srt_ts(c.start)} --> {_srt_ts(c.end)}\n{c.text}\n"
              for i, c in enumerate([c for c in cues if c.text.strip()], 1)]
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def parse_srt(text: str) -> list[Cue]:
    out: list[Cue] = []
    for block in [b for b in text.strip().split("\n\n") if b.strip()]:
        lines = block.split("\n")
        m = SRT_TS.search(block)
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        en = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        body = "\n".join(lines[2:]) if len(lines) > 2 else ""
        out.append(Cue(st, en, body.replace("\n", "")))
    return out


def ass_header(w: int, h: int, *, font: str = "Hiragino Sans GB", size: int = 28,
               margin_v: int = 16, margin_lr: int = 100, box_alpha: str = "A0") -> str:
    """ASS 头。**PlayResX/Y 必须等于视频尺寸**，否则字号/边距会被按 384×288 放大解释。"""
    return (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {w}\n"
        f"PlayResY: {h}\n"
        "WrapStyle: 0\n"
        "ScaledBorderAndShadow: yes\n"
        "YCbCr Matrix: None\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},&H00FFFFFF,&H000000FF,&H00000000,&H{box_alpha}000000,"
        f"0,0,0,0,100,100,0.3,0,3,2,0,2,{margin_lr},{margin_lr},{margin_v},1\n"
        f"Style: Label,{font},{max(12, int(size * 0.6))},&H0040D8FF,&H000000FF,&H00000000,"
        f"&H{box_alpha}000000,0,0,0,0,100,100,0.2,0,3,1,0,7,12,12,12,1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )


def write_ass(cues: list[Cue], path: Path, w: int, h: int, *,
              font: str = "Hiragino Sans GB", size: int = 28, margin_v: int = 16,
              margin_lr: int = 100, labels: list[Cue] | None = None) -> Path:
    lines = [ass_header(w, h, font=font, size=size, margin_v=margin_v, margin_lr=margin_lr)]
    for c in cues:
        if not c.text.strip():
            continue
        lines.append(f"Dialogue: 0,{ts_ass(c.start)},{ts_ass(c.end)},Default,,0,0,0,,"
                     f"{_ass_escape(c.text)}")
    for c in labels or []:
        lines.append(f"Dialogue: 1,{ts_ass(c.start)},{ts_ass(c.end)},Label,,0,0,0,,"
                     f"{_ass_escape(c.text)}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def parse_cues_tsv(path: Path) -> list[Cue]:
    """cues.tsv：start_ms <TAB> end_ms <TAB> text（text 内的 TAB 视作空格）。"""
    cues = []
    for n, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not ln.strip() or ln.startswith("#"):
            continue
        c = ln.split("\t")
        if len(c) < 3:
            raise SystemExit(f"cues.tsv 第 {n} 行列数 {len(c)} < 3：{ln[:80]}")
        cues.append(Cue(int(c[0]) / 1000.0, int(c[1]) / 1000.0, "\t".join(c[2:]).strip()))
    return cues


def parse_labels_tsv(path: Path) -> list[Cue]:
    out = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        a, b, txt = (ln.split("\t") + ["", ""])[:3]
        out.append(Cue(float(a), float(b), txt))
    return out


def effective_margin_lr(w: int, margin_lr: int) -> int:
    """左右边距按画面宽度收敛：窄画面（例如 320px 的合成测试片）用 100px 边距会只剩一条缝。
    960 宽、margin_lr=100 时结果不变（与 960×966 实测可读的那一版一致）。"""
    return min(margin_lr, max(8, int(w * 0.12)))


def max_chars_default(w: int, size: int, margin_lr: int) -> int:
    """单行能放下的 CJK 字数（留 1 个字的余量，宁可多分一页也不要压行）。"""
    lr = effective_margin_lr(w, margin_lr)
    return max(4, int((w - 2 * lr) / (size * 1.05)) - 1)


# --------------------------------------------------------------------------- CLI

def cmd_build(a) -> int:
    raw = parse_cues_tsv(Path(a.cues))
    lr = effective_margin_lr(a.w, a.margin_lr)
    limit = a.max_chars or max_chars_default(a.w, a.size, a.margin_lr)
    cues: list[Cue] = []
    for c in raw:
        if a.no_page or visible_len(c.text) <= limit:
            cues.append(c)
        else:
            cues.extend(page_text(c.text, c.start, c.end, limit))
    write_srt(cues, Path(a.srt))
    labels = parse_labels_tsv(Path(a.labels)) if a.labels and Path(a.labels).is_file() else None
    write_ass(cues, Path(a.ass), a.w, a.h, font=a.font, size=a.size,
              margin_v=a.margin_v, margin_lr=lr, labels=labels)
    longest = max((visible_len(c.text) for c in cues), default=0)
    print(json.dumps({"cues_in": len(raw), "cues_out": len(cues), "max_chars": limit,
                      "margin_lr": lr, "longest_cue": longest, "srt": a.srt, "ass": a.ass,
                      "labels": len(labels or [])}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="字幕分页 + SRT/ASS 写入（纯标准库）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="cues.tsv → subs.srt + subs.ass（长条按短语分页）")
    b.add_argument("--cues", required=True)
    b.add_argument("--srt", required=True)
    b.add_argument("--ass", required=True)
    b.add_argument("--w", type=int, required=True, help="视频宽（PlayResX）")
    b.add_argument("--h", type=int, required=True, help="视频高（PlayResY）")
    b.add_argument("--font", default="Hiragino Sans GB")
    b.add_argument("--size", type=int, default=28)
    b.add_argument("--margin-v", type=int, default=16)
    b.add_argument("--margin-lr", type=int, default=100)
    b.add_argument("--max-chars", type=int, default=0, help="0 = 按宽度自动算")
    b.add_argument("--no-page", action="store_true", help="不完全分页（仅按原 cue 输出）")
    b.add_argument("--labels", default="", help="可选：定格标注 TSV（start<TAB>end<TAB>text）")
    b.set_defaults(func=cmd_build)
    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
