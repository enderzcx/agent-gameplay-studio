#!/usr/bin/env python3
"""burn_subs.py — 用 **libass** 把字幕烧进 MP4。

为什么不用 `subtitles=subs.srt:force_style=...`（实测结论，见 2026-09-24 pilot）：
  SRT→ASS 的默认脚本空间是 **384×288**，force_style 里的 FontSize/MarginV 是按那个空间解释的，
  在 960×966 画面上会被放大约 3.35 倍（FontSize=30 → 约 100px、MarginV=18 → 约 60px），
  结果每条 cue 渲染成 3 行并压到手牌带上。
  → 正确做法：自己写 **PlayResX/PlayResY = 视频尺寸** 的 ASS，再交给 `ass=` 滤镜。

为什么不再用 PIL + 临时 PNG + overlay 链：
  旧写法要把每条 cue 画成透明 PNG 再 `overlay enable=between(t,..)`，
  长片上只烧进第 1 条（实测 74 条只进 1 条），且临时目录被清理后再也复现不出来。
  现在只写文本 ASS，烧录全交给 ffmpeg，中间没有临时图、也没有 Pillow 依赖。

用法:
  burn_subs.py <in.mp4> <subs.srt> <out.mp4> [--labels holds.tsv]
               [--font "Hiragino Sans GB"] [--size 28] [--margin-v 16] [--margin-lr 100]

  --labels 是可选定格标注 TSV（start<TAB>end<TAB>text），会以 Label 样式烧在左上角。
  真实尺寸从输入视频 ffprobe 读出，不靠调用方填 —— 填错就是那个 3.35 倍的事故。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import subtitles as S  # noqa: E402


def have_ass_filter() -> bool:
    r = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
    return r.stdout.count(" ass ") > 0 or any(
        line.split()[1:2] == ["ass"] for line in r.stdout.splitlines() if line.strip())


def probe_wh(video: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0:nk=1", str(video)],
        capture_output=True, text=True, check=True).stdout.strip().splitlines()[0]
    w, h = (int(x) for x in out.split(",")[:2])
    return w, h


def _has_audio(p: Path) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                        "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(p)],
                       capture_output=True, text=True)
    return bool(r.stdout.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("srt")
    ap.add_argument("out")
    ap.add_argument("--labels", default="", help="可选定格标注 TSV")
    ap.add_argument("--font", default="Hiragino Sans GB")
    ap.add_argument("--size", type=int, default=28)
    ap.add_argument("--margin-v", type=int, default=16)
    ap.add_argument("--margin-lr", type=int, default=100)
    ap.add_argument("--crf", default="18")
    ap.add_argument("--json", default="", help="把结果写进这个 JSON（给制作链路留证据）")
    a = ap.parse_args()

    if shutil.which("ffmpeg") is None:
        print("需要 ffmpeg", file=sys.stderr)
        return 2
    if not have_ass_filter():
        print("!! 这个 ffmpeg 没有 libass（`ass` 滤镜）。\n"
              "   本脚本不再提供 PNG overlay 后备：那条链在长片上会漏烧字幕。\n"
              "   请装带 libass 的 ffmpeg（`ffmpeg -filters | grep ass`）。", file=sys.stderr)
        return 2

    src, srt, out = Path(a.video), Path(a.srt), Path(a.out)
    for label, p in (("输入视频", src), ("字幕", srt)):
        if not p.is_file():
            print(f"找不到{label}: {p}", file=sys.stderr)
            return 2

    w, h = probe_wh(src)
    cues = S.parse_srt(srt.read_text(encoding="utf-8"))
    if not cues:
        print("字幕文件里没有可用 cue（拒绝产出一个没有字幕的“成片”）", file=sys.stderr)
        return 2
    labels = S.parse_labels_tsv(Path(a.labels)) if a.labels and Path(a.labels).is_file() else None
    ass = out.with_name(out.stem + ".ass")
    S.write_ass(cues, ass, w, h, font=a.font, size=a.size,
                margin_v=a.margin_v, margin_lr=S.effective_margin_lr(w, a.margin_lr), labels=labels)

    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src),
           "-vf", f"ass={ass}", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(a.crf),
           "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "copy"] if _has_audio(src) else ["-an"]
    cmd += [str(out)]
    subprocess.run(cmd, check=True)

    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "default=nw=1:nk=1", str(out)],
                         capture_output=True, text=True).stdout.strip()
    info = {"out": str(out), "ass": str(ass), "play_res": f"{w}x{h}", "cues": len(cues),
            "labels": len(labels or []), "font": a.font, "font_size": a.size,
            "margin_v": a.margin_v, "duration_s": float(dur)}
    print(f"OK  {out}  cues={len(cues)}  labels={len(labels or [])}  dur={dur}s  "
          f"(PlayRes {w}x{h}, FontSize {a.size}, MarginV {a.margin_v})")
    if a.json:
        Path(a.json).write_text(json.dumps(info, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
