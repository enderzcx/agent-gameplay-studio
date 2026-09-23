#!/usr/bin/env python3
"""burn_subs.py — 把 SRT 烧进 MP4（不依赖 libass/drawtext）。

本机 ffmpeg 8.1.1 **没有** `subtitles`/`drawtext` 滤镜（无 libass）。
用 PIL 把每句字幕画成透明 PNG，再用 ffmpeg `overlay ... enable='between(t,a,b)'` 叠上去。

用法: burn_subs.py <in.mp4> <subs.srt> <out.mp4> [--w 960] [--font /path/to.ttc] [--size 30]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DEFAULT_FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def parse_srt(text: str) -> list[tuple[float, float, str]]:
    out = []
    for block in [b for b in text.strip().split("\n\n") if b.strip()]:
        lines = block.split("\n")
        m = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", lines[1])
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        st = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        en = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        out.append((st, en, "\n".join(lines[2:])))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("srt")
    ap.add_argument("out")
    ap.add_argument("--w", type=int, default=960)
    ap.add_argument("--font", default=DEFAULT_FONT)
    ap.add_argument("--size", type=int, default=30)
    a = ap.parse_args()

    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=width,height", "-of", "csv=p=0", a.video],
                           capture_output=True, text=True, check=True).stdout.strip()
    W, H = (int(x) for x in probe.split(",")[:2])
    cues = parse_srt(Path(a.srt).read_text(encoding="utf-8"))
    if not cues:
        print("no cues", file=sys.stderr)
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="subburn-"))
    pngs = []
    font = ImageFont.truetype(a.font, a.size, index=0)
    for i, (st, en, text) in enumerate(cues, 1):
        img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        wrapped, cur = [], ""
        for ch in text.replace("\n", ""):
            if dr.textlength(cur + ch, font=font) > W - 80:
                wrapped.append(cur)
                cur = ch
            else:
                cur += ch
        wrapped.append(cur)
        lh = int(a.size * 1.45)
        y0 = H - int(H * 0.12) - lh * len(wrapped)
        for j, ln in enumerate(wrapped):
            x = (W - dr.textlength(ln, font=font)) / 2
            y = y0 + j * lh
            for dx in (-2, -1, 0, 1, 2):
                for dy in (-2, -1, 0, 1, 2):
                    if dx or dy:
                        dr.text((x + dx, y + dy), ln, font=font, fill=(0, 0, 0, 255))
            dr.text((x, y), ln, font=font, fill=(255, 255, 255, 255))
        p = tmp / f"s{i}.png"
        img.save(p)
        pngs.append((p, st, en))

    parts = []
    for i, (p, st, en) in enumerate(pngs, 1):
        prev = "0:v" if i == 1 else f"v{i-1}"
        nxt = "vout" if i == len(pngs) else f"v{i}"
        parts.append(f"[{prev}][{i}:v]overlay=0:0:enable='between(t,{st},{en})'[{nxt}]")
    cmd = ["ffmpeg", "-nostdin", "-v", "error", "-y", "-i", a.video]
    for p, _, _ in pngs:
        cmd += ["-i", str(p)]
    cmd += ["-filter_complex", ";".join(parts), "-map", "[vout]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-c:a", "copy", a.out]
    subprocess.run(cmd, check=True)
    print(f"wrote {a.out} ({len(pngs)} cues)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
