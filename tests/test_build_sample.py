#!/usr/bin/env python3
"""build_sample.sh 的针对性回归（只测这次改的几件事，不跑无关模型测试）。

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
AUDIT_HDR = ("asset_id\tevent_id\tsource_range\tclip_range\tfinal_range\tspeed/freeze\t"
             "narration_text\taudio_duration_s\tsubtitle_source\tevent_phase\tclaim_phase\t"
             "anchor_asset\tanchor_event\tanchor_source\tevidence\thold_mark\tvisible_window\t"
             "subtitle_sha256\taudio_sha256\tfinal_sha256\n")


def _sha(p: Path) -> str:
    import hashlib
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write_timeline(out: Path, dur: float) -> Path:
    """与本次产物绑定的最小采用时间线（1 段），用于驱动 build_sample 的审计路径。"""
    row = ["rec-test", "ev-1", "0.0-2.0", "0.0-2.0", f"0.0-{dur}", "1x", "audit line", "1.0",
           "subs-test", "battle", "battle", "rec-test", "ev-1", "0.1-1.9",
           "src rec-test@0.5s synthetic battle screen", "-", "-",
           _sha(out / "subs.srt"), _sha(out / "voice_master.wav"), _sha(out / "final.mp4")]
    tl = out / "timeline.tsv"
    tl.write_text(AUDIT_HDR + "\t".join(row) + "\n", encoding="utf-8")
    return tl


def test_default_path_marks_unaudited_output_as_draft(root: Path, src: Path, vdir: Path) -> None:
    p, out = build(root, src, vdir, "1\t0.0\t2.0\tshort.wav\t0.2\t0\taudit line\n", "draftcase")
    assert p.returncode == 0, f"基础导出失败:\n{p.stdout}\n{p.stderr}"
    assert (out / "DRAFT.txt").exists(), "没给审计输入却没有标 DRAFT（产物会被误当可交付）"
    assert "DRAFT ONLY" in p.stderr, f"没有向调用方明说这是 draft:\n{p.stderr}"
    print("ok  test_default_path_marks_unaudited_output_as_draft（绕过审计 = 明确 draft）")


def test_failing_audit_blocks_the_delivery_path(root: Path, src: Path, vdir: Path) -> None:
    pre = root / "preflight.json"
    subprocess.run([sys.executable, str(AUDIT_SCRIPT), "preflight", "--json", "--out", str(pre),
                    f"rec-test={src}"], check=True, capture_output=True, text=True)
    ledger = root / "gaps.tsv"
    ledger.write_text("gap_id\tfinal_range\tdisposition\treason\n", encoding="utf-8")
    # an unbound fixture: the audit must reject it (phase 错位 among other reasons)
    bad_tl = HERE / "fixtures" / "timeline.audit.phase-mismatch.tsv"
    p, out = build(root, src, vdir, "1\t0.0\t2.0\tshort.wav\t0.2\t0\taudit line\n", "badcase",
                   {"TIMELINE": str(bad_tl), "PREFLIGHT": str(pre),
                    "SILENCE_LEDGER": str(ledger)})
    assert p.returncode != 0, f"审计不过却成功了（exit {p.returncode}）:\n{p.stdout[-600:]}"
    assert "阶段错位" in (p.stdout + p.stderr), f"没有把审计的具体失败原因打出来:\n{p.stdout[-400:]}"
    assert (out / "DRAFT.txt").exists(), "审计不过却没有标 DRAFT"
    print("ok  test_failing_audit_blocks_the_delivery_path（默认制作路径真的跑了审计，不过就拒交）")


def test_passing_audit_clears_the_draft_mark(root: Path, src: Path, vdir: Path) -> None:
    """两遍：先出产物（draft），把时间线绑到产物上，再用审计路径重跑一次。

    依赖本机 ffmpeg 对同一命令产出字节一致的 MP4（已验证）；若某台机器不是，
    审计会正确地报内容 hash 不符 —— 那也是对的，因为时间线必须绑定到**实际**那一刀。
    """
    edl = "1\t0.0\t2.0\tshort.wav\t0.2\t0\taudit line\n"
    p, out = build(root, src, vdir, edl, "okcase")
    assert p.returncode == 0 and (out / "DRAFT.txt").exists(), f"第一遍失败:\n{p.stderr}"
    tl = write_timeline(out, 2.0)
    pre = root / "preflight-ok.json"
    subprocess.run([sys.executable, str(AUDIT_SCRIPT), "preflight", "--json", "--out", str(pre),
                    f"rec-test={src}"], check=True, capture_output=True, text=True)
    ledger = root / "gaps-ok.tsv"
    ledger.write_text("gap_id\tfinal_range\tdisposition\treason\n", encoding="utf-8")
    p2, _ = build(root, src, vdir, edl, "okcase", {"TIMELINE": str(tl), "PREFLIGHT": str(pre),
                                                   "SILENCE_LEDGER": str(ledger)})
    assert p2.returncode == 0, f"绑定好的时间线仍被拒:\n{p2.stdout[-800:]}\n{p2.stderr[-400:]}"
    assert (out / "audit-report.json").exists(), "没有留下审计报告"
    assert not (out / "DRAFT.txt").exists(), "审计通过了却还留着 DRAFT 标记"
    print("ok  test_passing_audit_clears_the_draft_mark（审计通过才取消 draft，并留下报告）")


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
                   test_failing_audit_blocks_the_delivery_path,
                   test_passing_audit_clears_the_draft_mark):
            fn(root, src, vdir)
        print("\n全部通过（7 项）")
        return 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
