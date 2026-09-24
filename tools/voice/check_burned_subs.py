#!/usr/bin/env python3
"""check_burned_subs.py — 成片字幕的**像素级**抽检（工作流 checker 不覆盖的那一层）。

它回答的问题，`subs.srt` 的条数与 sha256 回答不了：

  1. **字幕真的进画面了吗**（不是"数了 SRT 条数"）—— 逐条对准 cue 中点取帧，
     与**未烧字幕的同帧**做逐像素差；差出来的就是被烧进去的字。
  2. **有没有被裁**：文字像素不许碰到左右安全边距。
  3. **会不会压到手牌/关键 UI**：文字像素不许出现在声明的保护带里。
  4. **长句尾字**：最长那条 cue 的文字像素宽度要与字数相称（明显偏少 = 末尾被截）。

为什么必须给 `--baseline`：没有对照就只能靠"亮白像素"猜，而游戏画面本身就有大片白色。
差分是唯一能把"字"和"原本就白的东西"分开的做法。

**它不证明什么**：不证明听感、不证明字幕与语音语义一致、不证明断句舒服。
机器只说"这些像素在不在带里"，不说"这句读起来对不对"。

用法:
  check_burned_subs.py --video final_subbed.mp4 --baseline final.mp4 --subs subs.srt \\
      --band 900:966 --protect 655:845 --margin-x 40 --json subs-check.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subtitles as S  # noqa: E402

DIFF_THRESHOLD = 40  # 灰度差阈值：>40 才算"这一像素被字幕改过"


def probe_wh(video: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0:nk=1", str(video)],
        capture_output=True, text=True, check=True).stdout.strip().splitlines()[0]
    w, h = (int(x) for x in out.split(",")[:2])
    return w, h


def gray_frame(video: Path, t: float, w: int, h: int) -> bytes:
    r = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(video),
         "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True)
    data = r.stdout
    if len(data) < w * h:
        raise SystemExit(f"取帧失败：{video} @{t:.3f}s 只有 {len(data)} 字节（期望 {w*h}）")
    return data[: w * h]


def diff_mask(a: bytes, b: bytes, w: int, h: int) -> list[int]:
    """返回被字幕改动过的像素索引（差分 > 阈值）。"""
    return [i for i in range(w * h) if abs(a[i] - b[i]) > DIFF_THRESHOLD]


def parse_band(s: str, h: int) -> tuple[int, int]:
    if not s:
        return int(h * 0.88), h
    y0, y1 = (int(x) for x in s.split(":"))
    return max(0, y0), min(h, y1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="已烧字幕的成片")
    ap.add_argument("--baseline", required=True, help="同一次制作、未烧字幕的成片（帧对齐）")
    ap.add_argument("--subs", required=True, help="该成片用的 SRT")
    ap.add_argument("--band", default="", help="字幕允许出现的 y 带，如 900:966（默认底部 12%）")
    ap.add_argument("--protect", default="", help="不许被字幕碰到的 y 带，如手牌 655:845")
    ap.add_argument("--margin-x", type=int, default=40, help="左右安全边距（px）")
    ap.add_argument("--min-text-px", type=int, default=40, help="一条 cue 至少要有多少文字像素")
    ap.add_argument("--late-sample", type=float, default=0.92,
                    help="长条额外在 cue 的这个比例处再采一次（默认 0.92）")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    vid, base, srt = Path(a.video), Path(a.baseline), Path(a.subs)
    for label, p in (("成片", vid), ("对照", base), ("字幕", srt)):
        if not p.is_file():
            print(f"找不到{label}: {p}", file=sys.stderr)
            return 2
    w, h = probe_wh(vid)
    bw, bh = probe_wh(base)
    if (w, h) != (bw, bh):
        print(f"成片与对照尺寸不同（{w}x{h} vs {bw}x{bh}）：无法逐像素对照", file=sys.stderr)
        return 2

    band0, band1 = parse_band(a.band, h)
    prot = tuple(int(x) for x in a.protect.split(":")) if a.protect else None
    if prot and not (band1 <= prot[0] or band0 >= prot[1]):
        print(f"!! 声明的字幕带 {band0}-{band1} 与保护带 {prot[0]}-{prot[1]} 相交：\n"
              "   这不是『检查失败』，而是**配置本身矛盾**——字幕带必须完全落在保护带之外。",
              file=sys.stderr)
        return 2

    cues = S.parse_srt(srt.read_text(encoding="utf-8"))
    if not cues:
        print("字幕文件里没有 cue", file=sys.stderr)
        return 2

    rows, fails = [], []
    for i, c in enumerate(cues, 1):
        times = [c.start + (c.end - c.start) / 2]
        if (c.end - c.start) > 4.0:
            times.append(c.start + (c.end - c.start) * a.late_sample)
        for t in times:
            m = diff_mask(gray_frame(vid, t, w, h), gray_frame(base, t, w, h), w, h)
            xs = [k % w for k in m]
            ys = [k // w for k in m]
            in_band = [k for k in m if band0 <= k // w < band1]
            out_band = len(m) - len(in_band)
            in_margin = sum(1 for x in xs if x < a.margin_x or x >= w - a.margin_x)
            in_protect = sum(1 for y in ys if prot and prot[0] <= y < prot[1])
            hit = len(in_band) >= a.min_text_px
            row = {"cue": i, "t": round(t, 3), "text_len": S.visible_len(c.text),
                   "diff_px": len(m), "in_band_px": len(in_band), "out_band_px": out_band,
                   "margin_px": in_margin, "protect_px": in_protect, "hit": hit,
                   "x_min": min(xs) if xs else None, "x_max": max(xs) if xs else None,
                   "y_min": min(ys) if ys else None, "y_max": max(ys) if ys else None,
                   "text": c.text[:40]}
            rows.append(row)
            if not hit:
                fails.append(f"cue {i} @{t:.2f}s：字幕带内文字像素 {len(in_band)} < {a.min_text_px}（这条没烧上？）")
            if out_band > 0:
                fails.append(f"cue {i} @{t:.2f}s：有 {out_band} 个文字像素跑到字幕带 "
                             f"{band0}-{band1} 之外")
            if in_margin > 0:
                fails.append(f"cue {i} @{t:.2f}s：有 {in_margin} 个文字像素进了左右 {a.margin_x}px "
                             f"安全边距（可能被裁）")
            if in_protect > 0:
                fails.append(f"cue {i} @{t:.2f}s：有 {in_protect} 个文字像素落在保护带 "
                             f"{prot[0]}-{prot[1]}（压到手牌/UI）")

    # 长句尾字：最长 cue 的实际像素宽度要与字数相称（偏少 = 末尾被吃掉）
    longest = max(rows, key=lambda r: r["text_len"]) if rows else None
    tail_note = None
    if longest and longest["x_min"] is not None:
        px_per_char = (longest["x_max"] - longest["x_min"]) / max(1, longest["text_len"])
        longest["px_per_char"] = round(px_per_char, 2)
        tail_note = (f"最长 cue #{longest['cue']}（{longest['text_len']} 字）实际宽 "
                     f"{longest['x_max'] - longest['x_min']}px，{px_per_char:.1f}px/字；"
                     f"右端 x_max={longest['x_max']}，右边距 {w - longest['x_max']}px")
        if px_per_char < 8:
            fails.append(f"最长 cue #{longest['cue']} 只有 {px_per_char:.1f}px/字：末尾疑似被截")

    verdict = {"ok": not fails, "video": str(vid), "baseline": str(base), "size": f"{w}x{h}",
               "band": f"{band0}-{band1}", "protect": list(prot) if prot else None,
               "margin_x": a.margin_x, "cues": len(cues), "samples": len(rows),
               "sampled_hit": sum(1 for r in rows if r["hit"]), "tail": tail_note,
               "failures": fails, "rows": rows,
               "not_a_verdict_on": ["听感", "字幕与语音的语义一致性", "断句是否舒服",
                                    "字幕之外是否有别的遮挡"]}
    print(f"字幕像素抽检：{verdict['sampled_hit']}/{verdict['samples']} 采样点命中"
          f"（{len(cues)} 条 cue，带 {band0}-{band1}，保护带 "
          f"{f'{prot[0]}-{prot[1]}' if prot else '未声明'}）")
    if tail_note:
        print("  " + tail_note)
    for f in fails:
        print("  !! " + f, file=sys.stderr)
    if a.json:
        Path(a.json).write_text(json.dumps(verdict, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  report: {a.json}")
    if fails:
        print("字幕像素抽检未通过。这不代表成片一定不能看，但**不能声称字幕已验证**。",
              file=sys.stderr)
        return 1
    print("通过（机器只证明像素层面：字在带内、未越界、未压保护带；听感与语义仍需人核）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
