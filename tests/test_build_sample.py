#!/usr/bin/env python3
"""build_sample.sh 的针对性回归（只测这次改的几件事，不跑无关模型测试）。

  0. **首次一次 build 就能闭环**：干净的输出目录 + recipe（摘要可以是占位）+ preflight +
     静默台账 → 一次调用就产出 adopted-timeline.tsv（摘要按实际产物填实）与 receipt，
     不需要"先跑一遍拿摘要、填回 recipe、再跑一遍"。

  1. 超长段必须**拒绝导出**：旁白放不进画面窗口时，非 0 退出、给出可操作提示、**不得产出 final.mp4**。
     （改之前的行为：只 echo 一句警告就继续，apad 不裁超长音频 → 后续所有句子被推迟 → -shortest 截尾）
  2. 正常段必须**长度映射正确**：每段窗口 = 源长 + 登记定格，字幕起止 = 该段起点 + offset … + adur，
     且终片音视频等长。
  3. **默认制作路径必须调用采用时间线审计**：
     - 没给 TIMELINE / PREFLIGHT / SILENCE_LEDGER → 产物一律标 `DRAFT.txt`（不可交付）；
     - 给了但审计不过 → 退出 3，并同样标 draft；
     - 给了且审计通过 → 不再有 DRAFT.txt，并且留下 audit-report.json。

运行: python3 test_build_sample.py
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "tools" / "voice" / "build_sample.sh"
HDR = "seg\tsrc_start\tsrc_end\taudio_file\toffset\tfreeze\tsubtitle_text\n"


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def dur(p: Path) -> float:
    return float(run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                      "-of", "default=nw=1:nk=1", str(p)]).stdout.strip())


def ffprobe_streams(p: Path) -> dict:
    return json.loads(run(["ffprobe", "-v", "error", "-show_entries",
                           "format=duration", "-show_entries", "stream=codec_type,codec_name",
                           "-of", "json", str(p)]).stdout)


def make_fixtures(root: Path) -> tuple[Path, Path]:
    src = root / "src.mp4"
    r = run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
             "-i", "testsrc=size=320x240:rate=30", "-t", "12",
             "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(src)])
    assert src.exists(), r.stderr
    vdir = root / "voice"
    vdir.mkdir()
    for name, secs in (("short.wav", 1.0), ("long.wav", 4.0)):
        run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
             "-i", f"sine=frequency=220:duration={secs}", "-ar", "24000", "-ac", "1",
             str(vdir / name)])
    return src, vdir


def build(root: Path, src: Path, vdir: Path, edl_body: str, outname: str, extra_env: dict = None):
    edl = root / f"{outname}.tsv"
    edl.write_text(HDR + edl_body, encoding="utf-8")
    out = root / outname
    env = {"SRC_VIDEO": str(src), "SRC_CROP": "scale=320:240",
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    env.update(extra_env or {})
    p = subprocess.run(["bash", str(SCRIPT), str(edl), str(vdir), str(out)],
                       capture_output=True, text=True, env=env)
    return p, out


def test_overlong_is_rejected(root: Path, src: Path, vdir: Path) -> None:
    # 窗口 1.0s（src 0-1, freeze 0），旁白 1.0s @0.5 → 需要 1.5s，放不下
    p, out = build(root, src, vdir,
                   "1\t0.0\t1.0\tlong.wav\t0.5\t0\tshould not fit\n", "overlong")
    assert p.returncode != 0, f"超长段竟然通过了（exit {p.returncode}）\n{p.stdout}\n{p.stderr}"
    assert "放不进画面窗口" in p.stderr, f"报错信息没说清原因:\n{p.stderr}"
    # 必须给出三种合法修法
    for kw in ("改稿", "调画面", "显式定格"):
        assert kw in p.stderr, f"报错里缺修法提示 {kw!r}"
    assert not (out / "final.mp4").exists(), "拒绝导出却仍然产出了 final.mp4"
    assert not (out / "video_raw.mp4").exists(), "拒绝导出却仍然产出了 video_raw.mp4"
    print("ok  test_overlong_is_rejected（超长段被拒，且没有产出成片）")


def test_overlong_can_be_fixed_by_registered_freeze(root: Path, src: Path, vdir: Path) -> None:
    # 同一段：long.wav 是 4.0s，@0.5 需要 4.5s 窗口；显式登记 4.0s 定格 → 窗口 1.0+4.0=5.0s，放得下
    p, out = build(root, src, vdir,
                   "1\t0.0\t1.0\tlong.wav\t0.5\t4.0\tfixed by freeze\n", "freeze")
    assert p.returncode == 0, f"登记定格后仍失败:\n{p.stdout}\n{p.stderr}"
    d = dur(out / "final.mp4")
    assert abs(d - 5.0) < 0.2, f"定格后窗口应为 5.0s，实得 {d}"
    print("ok  test_overlong_can_be_fixed_by_registered_freeze（定格是合法出路，窗口=源+定格）")


def test_normal_length_mapping(root: Path, src: Path, vdir: Path) -> None:
    # seg1: src 0.0-2.0 (2.0s), short.wav(1.0s) @0.2 → 字幕 0.200-1.200, 段尾 2.0
    # seg2: src 5.0-6.5 (1.5s), short.wav(1.0s) @0.1 → 字幕 2.100-3.100, 段尾 3.5
    p, out = build(root, src, vdir,
                   "1\t0.0\t2.0\tshort.wav\t0.2\t0\tfirst line\n"
                   "2\t5.0\t6.5\tshort.wav\t0.1\t0\tsecond line\n", "normal")
    assert p.returncode == 0, f"正常段失败:\n{p.stdout}\n{p.stderr}"
    final = out / "final.mp4"
    vd, ad = dur(out / "video_raw.mp4"), dur(out / "voice_master.wav")
    assert abs(vd - 3.5) < 0.1, f"画面总长应为 3.5s，实得 {vd}"
    assert abs(ad - vd) < 0.15, f"音视频不等长: {ad} vs {vd}"
    assert abs(dur(final) - 3.5) < 0.15, f"终片应为 3.5s，实得 {dur(final)}"
    srt = (out / "subs.srt").read_text(encoding="utf-8")
    assert "00:00:00,200 --> 00:00:01,200" in srt, f"第1段字幕时间不对:\n{srt}"
    assert "00:00:02,100 --> 00:00:03,100" in srt, f"第2段字幕时间不对:\n{srt}"
    for f in (out / "seg_norm/a01.wav", out / "seg_norm/a02.wav"):
        d = dur(f)
        assert abs(d - (2.0 if f.name == "a01.wav" else 1.5)) < 0.02, \
            f"{f.name} 段长 {d}s 应等于其窗口（否则时间轴会漂）"
    print("ok  test_normal_length_mapping（窗口/字幕/总长映射正确，音视频等长）")


AUDIT_SCRIPT = HERE.parent / "skills" / "gameplay-postproduction" / "scripts" / "check_timeline_audit.py"
PRODUCE = HERE.parent / "tools" / "voice" / "produce_timeline.py"
RECIPE_COLS = ["asset_id", "event_id", "source_range", "clip_range", "final_range", "speed/freeze",
               "narration_text", "audio_duration_s", "subtitle_source", "event_phase", "claim_phase",
               "claim_mode", "audio_offset_s", "anchor_asset", "anchor_event", "anchor_source",
               "evidence", "hold_mark", "visible_window", "hold_burned_in",
               "subtitle_sha256", "audio_sha256", "final_sha256"]


def _sha(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_recipe(path: Path, segs: list) -> Path:
    """recipe：语义字段由人写，三份输出摘要留占位（首次制作时它们还不存在）。"""
    body = "\t".join(RECIPE_COLS) + "\n"
    for i, seg in enumerate(segs, start=1):
        row = dict(seg)
        row.setdefault("asset_id", "rec-test")
        row.setdefault("event_id", f"ev-{i}")
        row.setdefault("clip_range", "0.0-0.0")      # adopted 版会按实际重算
        row.setdefault("final_range", "0.0-0.0")
        row.setdefault("subtitle_source", "subs-test")
        row.setdefault("event_phase", "battle")
        row.setdefault("claim_phase", "battle")
        row.setdefault("claim_mode", "live")
        row.setdefault("audio_offset_s", "0.1")
        row.setdefault("anchor_asset", "rec-test")
        row.setdefault("anchor_event", f"ev-{i}")
        row.setdefault("anchor_source", f"{seg['source_range'].split('-')[0]}-{seg['src_end']}")
        row.setdefault("evidence", "src rec-test@1.0s synthetic battle screen")
        row.setdefault("hold_mark", "-")
        row.setdefault("visible_window", "-")
        row.setdefault("hold_burned_in", "no")
        row.setdefault("subtitle_sha256", "@PENDING@")
        row.setdefault("audio_sha256", "@PENDING@")
        row.setdefault("final_sha256", "@PENDING@")
        body += "\t".join(str(row[c]) for c in RECIPE_COLS) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def make_preflight(root: Path, src: Path, asset_id: str = "rec-test") -> Path:
    pre = root / f"preflight-{asset_id}.json"
    subprocess.run([sys.executable, str(AUDIT_SCRIPT), "preflight", "--json", "--out", str(pre),
                    f"{asset_id}={src}"], check=True, capture_output=True, text=True)
    return pre


LEDGER_EMPTY = "gap_id\tfinal_range\tdisposition\treason\n"
TWO_SEG_EDL = ("1\t0.0\t2.0\tshort.wav\t0.1\t0\tfirst line\n"
               "2\t3.0\t5.0\tshort.wav\t0.1\t0\tsecond line\n")
TWO_SEG_RECIPE = [
    {"source_range": "0.0-2.0", "src_end": 2.0, "speed/freeze": "1x",
     "narration_text": "first line", "audio_duration_s": "1.0"},
    {"source_range": "3.0-5.0", "src_end": 5.0, "speed/freeze": "1x",
     "narration_text": "second line", "audio_duration_s": "1.0"},
]


def test_default_path_marks_unaudited_output_as_draft(root: Path, src: Path, vdir: Path) -> None:
    p, out = build(root, src, vdir, "1\t0.0\t2.0\tshort.wav\t0.2\t0\taudit line\n", "draftcase")
    assert p.returncode == 0, f"基础导出失败:\n{p.stdout}\n{p.stderr}"
    assert (out / "DRAFT.txt").exists(), "没给审计输入却没有标 DRAFT（产物会被误当可交付）"
    assert "DRAFT ONLY" in p.stderr, f"没有向调用方明说这是 draft:\n{p.stderr}"
    assert not (out / "adopted-timeline.tsv").exists(), "没有 recipe 却产出了 adopted timeline"
    # 回归：macOS 自带 bash 3.2 在非 UTF-8 locale 下会把 `$OUT，` 里的中文标点吃进变量名，
    # 于是"标记为 draft"这一步自己先炸了。draft 提示必须完整说出口。
    assert "unbound variable" not in p.stderr, f"draft 提示本身炸了:\n{p.stderr}"
    assert "DRAFT.txt" in p.stderr, f"没有说清产物留在哪:\n{p.stderr}"
    print("ok  test_default_path_marks_unaudited_output_as_draft（绕过审计 = 明确 draft，且提示完整）")


def test_first_build_closes_the_loop_in_one_invocation(root: Path, src: Path, vdir: Path) -> None:
    """干净的输出目录、摘要还是占位，一次 build 就要给出 adopted timeline 与 receipt。"""
    out = root / "onepass"
    recipe = write_recipe(root / "onepass-recipe.tsv", [dict(r) for r in TWO_SEG_RECIPE])
    pre = make_preflight(root, src)
    ledger = root / "onepass-gaps.tsv"
    ledger.write_text(LEDGER_EMPTY, encoding="utf-8")
    p, _ = build(root, src, vdir, TWO_SEG_EDL, "onepass",
                 {"TIMELINE": str(recipe), "PREFLIGHT": str(pre), "SILENCE_LEDGER": str(ledger)})
    assert p.returncode == 0, f"首次一次 build 应当直接通过:\n{p.stdout[-800:]}\n{p.stderr[-400:]}"
    assert not (out / "DRAFT.txt").exists(), "闭环成功却仍标了 draft"
    adopted = out / "adopted-timeline.tsv"
    assert adopted.is_file(), "没有产出 adopted-timeline.tsv"
    text = adopted.read_text(encoding="utf-8")
    assert "@PENDING@" not in text, "adopted 输出里还留着占位摘要"
    header, *rows = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    idx = {c: i for i, c in enumerate(header.split("\t"))}
    assert idx["audio_offset_s"] == 12 and header.split("\t")[idx["hold_burned_in"]] == "hold_burned_in"
    seg1, seg2 = [r.split("\t") for r in rows]
    # 真实 offset 记进最终时间线，声段 = final_start + offset .. + 实测声长
    assert seg1[idx["audio_offset_s"]] == "0.1", seg1[idx["audio_offset_s"]]
    assert seg1[idx["final_range"]] == "0.0-2.0" and seg2[idx["final_range"]] == "2.0-4.0", rows
    assert seg1[idx["subtitle_sha256"]] == _sha(out / "subs.srt"), "摘要没有按实际字幕填实"
    assert seg1[idx["final_sha256"]] == _sha(out / "final.mp4"), "摘要没有按实际成片填实"
    # 字幕 cue 必须贴合声段 [0.1, 1.1] / [2.1, 3.1]
    srt = (out / "subs.srt").read_text(encoding="utf-8")
    assert "00:00:00,100 --> 00:00:01,100" in srt and "00:00:02,100 --> 00:00:03,100" in srt, srt
    receipt = json.loads((out / "produce-receipt.json").read_text(encoding="utf-8"))
    assert receipt["source_video"]["sha256"] == _sha(src), "receipt 没有记录实际读过的源"
    assert receipt["edl"]["sha256"] == _sha(root / "onepass.tsv"), "receipt 没有绑定 EDL"
    assert receipt["timeline"]["sha256"] == _sha(adopted)
    assert json.loads((out / "audit-report.json").read_text(encoding="utf-8"))["ok"] is True
    print("ok  test_first_build_closes_the_loop_in_one_invocation"
          "（首次一次 build 就产出 adopted timeline + receipt，无需二次渲染）")


def test_source_and_recipe_mismatches_fail_before_rendering(root: Path, src: Path,
                                                           vdir: Path) -> None:
    """preflight=A、SRC_VIDEO=B（同长不同画面），以及 recipe 与 EDL 对不上，都要在渲染前失败。"""
    pre = make_preflight(root, src)
    # A/B：台账是 src，真正给的是同长不同画面的另一份
    other = root / "src-other.mp4"
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=320x240:rate=30", "-t", "12", "-vf", "hflip",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast", str(other)])
    recipe = write_recipe(root / "ab-recipe.tsv", [dict(r) for r in TWO_SEG_RECIPE])
    ledger = root / "ab-gaps.tsv"
    ledger.write_text(LEDGER_EMPTY, encoding="utf-8")
    # the preflight ledger describes `src`, but this run is handed `other`
    p, out = build(root, other, vdir, TWO_SEG_EDL, "abcase",
                   {"TIMELINE": str(recipe), "PREFLIGHT": str(pre), "SILENCE_LEDGER": str(ledger)})
    assert p.returncode != 0 and "源不匹配" in (p.stdout + p.stderr), (p.stdout, p.stderr)
    assert (out / "DRAFT.txt").exists() and not (out / "final.mp4").exists(), "渲染前就该失败"
    print("ok  test_source_and_recipe_mismatches_fail_before_rendering（A/B 源错配在渲染前失败）")

    # 同长不同源区间：recipe 说 0.0-2.0，EDL 说 0.5-2.5
    shifted = [dict(TWO_SEG_RECIPE[0]), dict(TWO_SEG_RECIPE[1])]
    shifted[0]["source_range"] = "0.5-2.5"
    recipe2 = write_recipe(root / "range-recipe.tsv", shifted)
    p2, out2 = build(root, src, vdir, TWO_SEG_EDL, "rangecase",
                     {"TIMELINE": str(recipe2), "PREFLIGHT": str(pre), "SILENCE_LEDGER": str(ledger)})
    assert p2.returncode != 0 and "不一致" in (p2.stdout + p2.stderr), (p2.stdout, p2.stderr)
    assert (out2 / "DRAFT.txt").exists()
    print("ok  …（同长但源区间不同也失败）")

    # recipe 的 offset 与 EDL 不一致
    bad_off = [dict(TWO_SEG_RECIPE[0]), dict(TWO_SEG_RECIPE[1])]
    bad_off[0]["audio_offset_s"] = "0.4"
    recipe3 = write_recipe(root / "offset-recipe.tsv", bad_off)
    p3, out3 = build(root, src, vdir, TWO_SEG_EDL, "offsetcase",
                     {"TIMELINE": str(recipe3), "PREFLIGHT": str(pre), "SILENCE_LEDGER": str(ledger)})
    assert p3.returncode != 0 and "offset" in (p3.stdout + p3.stderr), (p3.stdout, p3.stderr)
    assert (out3 / "DRAFT.txt").exists()
    print("ok  …（recipe 的 offset 与 EDL 不一致也失败）")


def test_freeze_is_burned_in_or_explicitly_draft(root: Path, src: Path, vdir: Path) -> None:
    """支持的 freeze 必须真的把标注烧进画面；做不到就明确 draft，不许"表格有 mark"就算过。"""
    have_drawtext = any(parts[1] == "drawtext" for parts in
                        (ln.split() for ln in subprocess.run(
                            ["ffmpeg", "-hide_banner", "-filters"], capture_output=True,
                            text=True).stdout.splitlines() if len(ln.split()) > 2))
    recipe_segs = [{"source_range": "0.0-1.0", "src_end": 1.0, "speed/freeze": "1x + 定格3s",
                    "narration_text": "held line", "audio_duration_s": "4.0",
                    "audio_offset_s": "0.0",
                    "hold_mark": "复盘定格 · 源 rec-test@1.0s（保持）", "hold_burned_in": "no"}]
    recipe = write_recipe(root / "freeze-recipe.tsv", recipe_segs)
    pre = make_preflight(root, src)
    ledger = root / "freeze-gaps.tsv"
    ledger.write_text(LEDGER_EMPTY, encoding="utf-8")
    edl = "1\t0.0\t1.0\tlong.wav\t0.0\t3.0\theld line\n"
    p, out = build(root, src, vdir, edl, "freezecase",
                   {"TIMELINE": str(recipe), "PREFLIGHT": str(pre), "SILENCE_LEDGER": str(ledger)})
    if not have_drawtext:
        assert p.returncode != 0, f"没有 drawtext 却放过了定格:\n{p.stdout[-400:]}"
        assert "drawtext" in (p.stdout + p.stderr), (p.stdout, p.stderr)
        assert (out / "DRAFT.txt").exists(), "无法烧录标注却没有标 draft"
        print("ok  test_freeze_is_burned_in_or_explicitly_draft"
              "（本机没有 drawtext：明确 fail-closed 并保持 draft）")
        return
    assert p.returncode == 0, f"有 drawtext 时支持的定格应当通过:\n{p.stdout[-800:]}"
    adopted = (out / "adopted-timeline.tsv").read_text(encoding="utf-8")
    header, *rows = [ln for ln in adopted.splitlines() if ln.strip() and not ln.startswith("#")]
    idx = {c: i for i, c in enumerate(header.split("\t"))}
    assert rows[0].split("\t")[idx["hold_burned_in"]] == "yes", "adopted 没有声明已烧录"
    # 冻的那一段真的被画上了标注：把冻结时刻的画面与同源无标注的参考帧逐字节比较
    frozen = root / "frozen.png"
    plain = root / "plain.png"
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", "3.0", "-i", str(out / "final.mp4"),
         "-frames:v", "1", str(frozen)])
    run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", "0.9", "-i", str(src),
         "-vf", "scale=320:240,fps=30", "-frames:v", "1", str(plain)])
    assert _sha(frozen) != _sha(plain), "冻结帧与无标注参考帧完全一致 -> 标注没有真的烧进去"
    print("ok  test_freeze_is_burned_in_or_explicitly_draft（支持的定格真的标注在画面上）")


def test_subtitle_must_match_the_real_span(root: Path, src: Path, vdir: Path) -> None:
    """短字幕/错时字幕在默认验收路径上失败。"""
    out = root / "srtcase"
    recipe = write_recipe(root / "srt-recipe.tsv", [dict(r) for r in TWO_SEG_RECIPE])
    pre = make_preflight(root, src)
    ledger = root / "srt-gaps.tsv"
    ledger.write_text(LEDGER_EMPTY, encoding="utf-8")
    p, _ = build(root, src, vdir, TWO_SEG_EDL, "srtcase",
                 {"TIMELINE": str(recipe), "PREFLIGHT": str(pre), "SILENCE_LEDGER": str(ledger)})
    assert p.returncode == 0, p.stdout[-600:]
    squeezed = root / "subs-squeezed.srt"
    squeezed.write_text("1\n00:00:01,000 --> 00:00:01,100\nfirst line\n\n"
                        "2\n00:00:03,000 --> 00:00:03,100\nsecond line\n", encoding="utf-8")
    q = run([sys.executable, str(AUDIT_SCRIPT), "audit", str(out / "adopted-timeline.tsv"),
             "--preflight", str(pre), "--silence-ledger", str(ledger), "--subtitle", str(squeezed),
             "--audio", str(out / "voice_master.wav"), "--final-mp4", str(out / "final.mp4"),
             "--receipt", str(out / "produce-receipt.json"), "--edl", str(root / "srtcase.tsv")])
    assert q.returncode != 0 and "严丝合缝" in (q.stdout + q.stderr), (q.stdout, q.stderr)
    print("ok  test_subtitle_must_match_the_real_span（整句压成末尾 0.1s 在默认验收路径上失败）")


def test_bad_header_is_rejected(root: Path, src: Path, vdir: Path) -> None:
    edl = root / "old.tsv"
    edl.write_text("seg\tsrc_start\tsrc_end\taudio_file\toffset\tsubtitle_text\n"
                   "1\t0.0\t2.0\tshort.wav\t0.2\told format\n", encoding="utf-8")
    env = {"SRC_VIDEO": str(src), "SRC_CROP": "scale=320:240",
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    p = subprocess.run(["bash", str(SCRIPT), str(edl), str(vdir), str(root / "oldhdr")],
                       capture_output=True, text=True, env=env)
    assert p.returncode != 0 and "表头不符" in p.stderr, f"旧列数被静默接受:\n{p.stderr}"
    print("ok  test_bad_header_is_rejected（少一列不会静默错位）")


def make_av_source(root: Path) -> Path:
    """带真实音轨的合成源片：用来测「保留原声」这条路（源无音轨时必须失败）。"""
    src = root / "src-av.mp4"
    r = run(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
             "-i", "testsrc2=size=320x240:rate=30", "-t", "12",
             "-f", "lavfi", "-i", "sine=frequency=180:duration=12", "-shortest",
             "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
             "-c:a", "aac", "-ar", "48000", "-ac", "2", str(src)])
    assert src.exists(), r.stderr
    return src


def has_audio(p: Path) -> bool:
    return bool(run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                     "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(p)]).stdout.strip())


def test_burn_subs_and_pixel_check(root: Path, src: Path, vdir: Path) -> None:
    """BURN_SUBS=1：长句必须分页成多条单行 cue、用真尺寸 ASS 烧进成片，并过像素抽检。"""
    long_line = ("先看敌人意图再决定是补防御还是全力输出因为这一回合的伤害刚好卡在斩杀线下面一点点")
    p, out = build(root, src, vdir,
                   f"1\t0.0\t4.0\tshort.wav\t0.0\t0\t{long_line}\n", "burncase",
                   {"BURN_SUBS": "1"})
    assert p.returncode == 0, f"烧字幕失败:\n{p.stdout[-600:]}\n{p.stderr[-600:]}"
    assert (out / "final-nosub.mp4").is_file(), "没有留下未烧版，像素对照就没法做"
    ass = (out / "subs.ass").read_text(encoding="utf-8")
    assert "PlayResX: 320" in ass and "PlayResY: 240" in ass, \
        f"ASS 的 PlayRes 必须等于视频尺寸:\n{ass[:200]}"
    srt = (out / "subs.srt").read_text(encoding="utf-8")
    assert srt.count("-->") >= 3, f"长条没有被分页:\n{srt}"
    q = run([sys.executable, str(HERE.parent / "tools" / "voice" / "check_burned_subs.py"),
             "--video", str(out / "final.mp4"), "--baseline", str(out / "final-nosub.mp4"),
             "--subs", str(out / "subs.srt"), "--band", "150:240", "--margin-x", "10",
             "--json", str(out / "subs-check.json")])
    assert q.returncode == 0, f"字幕像素抽检没过:\n{q.stdout}\n{q.stderr}"
    report = json.loads((out / "subs-check.json").read_text(encoding="utf-8"))
    assert report["sampled_hit"] == report["samples"] and report["samples"] >= 3, report
    print(f"ok  test_burn_subs_and_pixel_check（{report['cues']} 条 cue / "
          f"{report['samples']} 采样点全部命中，PlayRes=真尺寸）")


def test_pixel_check_catches_a_wrong_band(root: Path, src: Path, vdir: Path) -> None:
    """抽检必须能抓到"字幕跑到带外"：把带声明在画面顶部，就该失败而不是放行。"""
    p, out = build(root, src, vdir, "1\t0.0\t2.0\tshort.wav\t0.0\t0\t顶部没字的\n", "wrongband",
                   {"BURN_SUBS": "1"})
    assert p.returncode == 0, p.stderr[-400:]
    q = run([sys.executable, str(HERE.parent / "tools" / "voice" / "check_burned_subs.py"),
             "--video", str(out / "final.mp4"), "--baseline", str(out / "final-nosub.mp4"),
             "--subs", str(out / "subs.srt"), "--band", "0:60", "--margin-x", "10"])
    assert q.returncode != 0 and "字幕带" in (q.stdout + q.stderr), (q.stdout, q.stderr)
    print("ok  test_pixel_check_catches_a_wrong_band（带声明错就失败，不是永远通过）")


def test_keep_source_audio_mixes_and_refuses_silent_source(root: Path, src: Path, vdir: Path) -> None:
    av = make_av_source(root)
    p, out = build(root, av, vdir, "1\t0.0\t2.0\tshort.wav\t0.1\t0\t保留原声\n", "keepsrc",
                   {"KEEP_SRC_AUDIO": "1"})
    assert p.returncode == 0, f"保留原声失败:\n{p.stdout[-500:]}\n{p.stderr[-500:]}"
    assert (out / "src_audio.wav").is_file(), "没有产出与原片同源的 src_audio.wav"
    assert has_audio(out / "final.mp4"), "开了 KEEP_SRC_AUDIO 却仍然没有音轨"
    assert has_audio(out / "voice_master.wav"), "旁白母版不该被原声覆盖"
    sd, vd = dur(out / "src_audio.wav"), dur(out / "video_raw.mp4")
    assert abs(sd - vd) < 0.15, f"原声轨 {sd}s 与画面 {vd}s 不等长，混出来必然错位"
    assert "已混入源原声" in p.stdout, p.stdout

    # 源片没有音轨时：必须失败、不许拿静音轨冒充原声
    p2, out2 = build(root, src, vdir, "1\t0.0\t2.0\tshort.wav\t0.1\t0\t假装有原声\n", "keepsilent",
                     {"KEEP_SRC_AUDIO": "1"})
    assert p2.returncode == 3, f"无声源却成功了:\n{p2.stdout}\n{p2.stderr}"
    assert (out2 / "DRAFT.txt").exists() and not (out2 / "final.mp4").exists(), \
        "该失败的时候既没标 draft 又留了成片"
    assert "没有音轨" in p2.stderr, p2.stderr
    print("ok  test_keep_source_audio_mixes_and_refuses_silent_source"
          "（有原声才混、等长把关；无原声直接失败）")


def test_no_subtitle_text_is_refused(root: Path, src: Path, vdir: Path) -> None:
    p, out = build(root, src, vdir, "1\t0.0\t2.0\tshort.wav\t0.1\t0\t \n", "nosubs",
                   {"BURN_SUBS": "1"})
    assert p.returncode == 3 and (out / "DRAFT.txt").exists(), (p.returncode, p.stderr)
    assert not (out / "final.mp4").exists(), "没有字幕文本却产出了成片"
    print("ok  test_no_subtitle_text_is_refused（没有字幕文本 → draft，不产出「成片」）")


LONG_NARRATION = ("先看敌人这一回合的意图再决定是补防御还是全力输出因为伤害刚好卡在斩杀线下面一点点")


def test_paged_subtitles_still_pass_the_audit(root: Path, src: Path, vdir: Path) -> None:
    """一条口播分页成多条 cue 之后，采用时间线审计必须仍然能过。

    这是真实短片跑出来的缺陷：审计原先把绑定写死成 "1 cue ↔ 1 旁白行"，
    于是"短语分页"与"默认验收路径"直接互斥。新口径是
    「这一行的 cue 拼起来 == 这一行的稿」且「这些 cue 的并集 == 这一行的实际声段」。
    """
    out = root / "paged"
    recipe = write_recipe(root / "paged-recipe.tsv", [{
        "source_range": "0.0-6.0", "src_end": 6.0, "speed/freeze": "1x",
        "narration_text": LONG_NARRATION, "audio_duration_s": "4.0",
        "audio_offset_s": "0.1", "anchor_source": "0.0-6.0",
        "evidence": "src rec-test@2.0s synthetic reward screen",
    }])
    pre = make_preflight(root, src)
    ledger = root / "paged-gaps.tsv"
    ledger.write_text(LEDGER_EMPTY, encoding="utf-8")
    p, _ = build(root, src, vdir, f"1\t0.0\t6.0\tlong.wav\t0.1\t0\t{LONG_NARRATION}\n", "paged",
                 {"TIMELINE": str(recipe), "PREFLIGHT": str(pre), "SILENCE_LEDGER": str(ledger),
                  "BURN_SUBS": "1"})
    assert p.returncode == 0, f"分页后的默认路径应当通过:\n{p.stdout[-900:]}\n{p.stderr[-500:]}"
    assert not (out / "DRAFT.txt").exists(), "分页之后就过不了审计（契约被写死成 1 cue = 1 行）"
    srt = (out / "subs.srt").read_text(encoding="utf-8")
    assert srt.count("-->") >= 3, f"长口播没有被分页:\n{srt}"
    report = json.loads((out / "audit-report.json").read_text(encoding="utf-8"))
    assert report["ok"] is True, report.get("errors")

    # 反例：把中间那条 cue 抽掉（同一行内出现空洞）→ 必须失败，不能因为"行数对得上"就放行
    blocks = [b for b in srt.strip().split("\n\n") if b.strip()]
    (out / "subs-gap.srt").write_text("\n\n".join(blocks[:1] + blocks[2:]) + "\n", encoding="utf-8")
    adopted = (out / "adopted-timeline.tsv").read_text(encoding="utf-8")
    (out / "adopted-gap.tsv").write_text(
        adopted.replace(_sha(out / "subs.srt"), _sha(out / "subs-gap.srt")), encoding="utf-8")
    q = run([sys.executable, str(AUDIT_SCRIPT), "audit", str(out / "adopted-gap.tsv"),
             "--preflight", str(pre), "--silence-ledger", str(ledger),
             "--subtitle", str(out / "subs-gap.srt"), "--audio", str(out / "voice_master.wav"),
             "--final-mp4", str(out / "final.mp4"), "--receipt", str(out / "produce-receipt.json")])
    assert q.returncode != 0 and "严丝合缝" in (q.stdout + q.stderr), (q.stdout[-500:], q.stderr[-500:])
    print(f"ok  test_paged_subtitles_still_pass_the_audit（{srt.count('-->')} 条 cue 通过；"
          f"抽掉一条 = 有空洞 → 失败）")


MAKE_CUT = HERE.parent / "tools" / "voice" / "make_cut.sh"
BURN = HERE.parent / "tools" / "voice" / "burn_subs.py"
PIXEL = HERE.parent / "tools" / "voice" / "check_burned_subs.py"


def test_burn_survives_a_hostile_output_path(root: Path, src: Path, vdir: Path) -> None:
    """输出目录带逗号/冒号/单引号时，`ass=<path>` 会打断整个 filtergraph —— 必须仍然能烧。"""
    p, out = build(root, src, vdir, "1\t0.0\t2.0\tshort.wav\t0.1\t0\t路径里有逗号和冒号\n",
                   "out, with: odd'chars", {"BURN_SUBS": "1"})
    assert p.returncode == 0, f"带特殊字符的输出目录烧字幕失败:\n{p.stderr[-600:]}"
    assert (out / "final-nosub.mp4").is_file() and (out / "final.mp4").stat().st_size > 0
    q = run([sys.executable, str(PIXEL), "--video", str(out / "final.mp4"),
             "--baseline", str(out / "final-nosub.mp4"), "--subs", str(out / "subs.srt"),
             "--band", "150:240", "--margin-x", "10"])
    assert q.returncode == 0, f"特殊路径下字幕没烧上:\n{q.stdout}\n{q.stderr}"
    print(f"ok  test_burn_survives_a_hostile_output_path（{out.name!r} 下仍烧上且抽检通过）")


def test_burner_refuses_missing_labels_and_bad_subtitles(root: Path, src: Path, vdir: Path) -> None:
    p, out = build(root, src, vdir, "1\t0.0\t2.0\tshort.wav\t0.1\t0\t标签用例\n", "labelscase")
    assert p.returncode == 0, p.stderr[-300:]
    # 显式给了 --labels 却不存在：不能静默忽略
    q = run([sys.executable, str(BURN), str(out / "final.mp4"), str(out / "subs.srt"),
             str(root / "labeled.mp4"), "--labels", str(root / "nope.tsv")])
    assert q.returncode != 0 and "标注文件" in q.stderr, (q.returncode, q.stderr)
    # 坏字幕：不能静默跳过坏条后照烧
    bad = root / "bad.srt"
    bad.write_text("1\n00:00:02,000 --> 00:00:01,000\n倒着走\n", encoding="utf-8")
    q2 = run([sys.executable, str(BURN), str(out / "final.mp4"), str(bad), str(root / "bad.mp4")])
    assert q2.returncode != 0 and "subtitle error" in q2.stderr, (q2.returncode, q2.stderr)
    print("ok  test_burner_refuses_missing_labels_and_bad_subtitles（缺标注/坏字幕都不放过）")


def test_pixel_checker_refuses_unsafe_args_and_catches_missing_subs(root: Path, src: Path,
                                                                   vdir: Path) -> None:
    p, out = build(root, src, vdir, "1\t0.0\t2.0\tshort.wav\t0.1\t0\t抽检参数\n", "pixelargs",
                   {"BURN_SUBS": "1"})
    assert p.returncode == 0, p.stderr[-300:]
    base = ["--video", str(out / "final.mp4"), "--baseline", str(out / "final-nosub.mp4"),
            "--subs", str(out / "subs.srt"), "--band", "150:240", "--margin-x", "10"]
    # 0 阈值会让"完全没字幕"的成片通过：必须是参数错误
    q = run([sys.executable, str(PIXEL)] + base + ["--min-text-px", "0"])
    assert q.returncode == 2 and "min-text-px" in q.stderr, (q.returncode, q.stderr)
    # band/protect 相交是实现方配置矛盾，直接拒绝
    q2 = run([sys.executable, str(PIXEL)] + base + ["--protect", "100:200"])
    assert q2.returncode == 2 and "保护带" in q2.stderr, (q2.returncode, q2.stderr)
    # 负例：没有烧字幕的成片（自己跟自己比）必须判不通过
    q3 = run([sys.executable, str(PIXEL), "--video", str(out / "final-nosub.mp4"),
              "--baseline", str(out / "final-nosub.mp4"), "--subs", str(out / "subs.srt"),
              "--band", "150:240", "--margin-x", "10"])
    assert q3.returncode == 1 and "没烧上" in (q3.stdout + q3.stderr), (q3.returncode, q3.stdout)
    print("ok  test_pixel_checker_refuses_unsafe_args_and_catches_missing_subs"
          "（0 阈值/矛盾带被拒；无字幕成片判不通过）")


def test_make_cut_sets_delivery_defaults(root: Path, src: Path, vdir: Path) -> None:
    """一次调用就该拿到：烧了字幕的成片 + 明确的原声结论（有则混、无则记）。"""
    env = {"SRC_CROP": "scale=320:240",
           "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    av = make_av_source(root)
    (root / "mkcut.tsv").write_text(HDR + "1\t0.0\t2.0\tshort.wav\t0.1\t0\t封装默认值\n", encoding="utf-8")
    out = root / "mkcut-av"
    p = subprocess.run(["bash", str(MAKE_CUT), str(root / "mkcut.tsv"), str(vdir), str(out)],
                       capture_output=True, text=True, env={**env, "SRC_VIDEO": str(av)})
    assert p.returncode == 0, f"make_cut 失败:\n{p.stdout[-600:]}\n{p.stderr[-600:]}"
    assert (out / "final-nosub.mp4").is_file(), "交付默认必须烧字幕（未烧版要留作对照）"
    assert has_audio(out / "src_audio.wav"), "源片有原声却没有保留"
    note = (out / "SOURCE-AUDIO.txt").read_text(encoding="utf-8")
    assert "源片有音轨" in note and "不证明听感" in note, note

    # 无声源：不能停掉整条制作，只在 SOURCE-AUDIO.txt 里如实说明
    (root / "mkcut2.tsv").write_text(HDR + "1\t0.0\t2.0\tshort.wav\t0.1\t0\t无声源继续\n", encoding="utf-8")
    out2 = root / "mkcut-silent"
    p2 = subprocess.run(["bash", str(MAKE_CUT), str(root / "mkcut2.tsv"), str(vdir), str(out2)],
                        capture_output=True, text=True, env={**env, "SRC_VIDEO": str(src)})
    assert p2.returncode == 0, f"无声源不该整条停掉:\n{p2.stdout[-400:]}\n{p2.stderr[-400:]}"
    note2 = (out2 / "SOURCE-AUDIO.txt").read_text(encoding="utf-8")
    assert "没有音轨" in note2 and not (out2 / "src_audio.wav").exists(), note2
    assert (out2 / "final-nosub.mp4").is_file(), "无声源也应该产出（只是音轨只来自旁白）"
    print("ok  test_make_cut_sets_delivery_defaults（必烧字幕；有原声就分轨、无原声记录后继续）")


FIND_TOOLS = HERE.parent / "skills" / "gameplay-postproduction" / "scripts" / "find_tools.py"


def test_find_tools_points_at_the_real_tools_dir(root: Path, src: Path, vdir: Path) -> None:
    """skill 目录里**没有** tools/voice；定位器必须给出真实路径，而不是让调用方猜。"""
    q = run([sys.executable, str(FIND_TOOLS), "--check", "--json"])
    assert q.returncode == 0, f"定位器失败:\n{q.stdout}\n{q.stderr}"
    info = json.loads(q.stdout)
    assert info["tools_voice"] == str(HERE.parent / "tools" / "voice"), info
    assert info["missing"] == [], info
    print(f"ok  test_find_tools_points_at_the_real_tools_dir（{info['tools_voice']}）")


def main() -> int:
    if not SCRIPT.exists():
        print(f"找不到 {SCRIPT}", file=sys.stderr)
        return 2
    root = Path(tempfile.mkdtemp(prefix="buildsample-test-"))
    try:
        src, vdir = make_fixtures(root)
        for fn in (test_overlong_is_rejected, test_overlong_can_be_fixed_by_registered_freeze,
                   test_normal_length_mapping, test_bad_header_is_rejected,
                   test_default_path_marks_unaudited_output_as_draft,
                   test_first_build_closes_the_loop_in_one_invocation,
                   test_source_and_recipe_mismatches_fail_before_rendering,
                   test_freeze_is_burned_in_or_explicitly_draft,
                   test_subtitle_must_match_the_real_span,
                   test_burn_subs_and_pixel_check, test_pixel_check_catches_a_wrong_band,
                   test_keep_source_audio_mixes_and_refuses_silent_source,
                   test_no_subtitle_text_is_refused,
                   test_paged_subtitles_still_pass_the_audit,
                   test_burn_survives_a_hostile_output_path,
                   test_burner_refuses_missing_labels_and_bad_subtitles,
                   test_pixel_checker_refuses_unsafe_args_and_catches_missing_subs,
                   test_make_cut_sets_delivery_defaults,
                   test_find_tools_points_at_the_real_tools_dir):
            fn(root, src, vdir)
        print("\n全部通过（19 项）")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
