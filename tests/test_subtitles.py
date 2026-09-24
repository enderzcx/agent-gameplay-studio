#!/usr/bin/env python3
"""subtitles.py 的针对性回归：短语分页、时基进位、ASS 头尺寸。

这几条都是**实际踩过的坑**，不是通用工具测试：
  1. 一条 21 字的口播必须被拆成多条单行 cue，而不是渲染成 3 行压到 HUD 上；
  2. 分页后的 cue 必须**严丝合缝**铺满原时间窗（无空洞、无重叠、末条精确落点）；
  3. ASS 时间戳必须走**总厘秒**再拆分（`int(round(t%1*100))` 会把 0.999 写成 `.100`）；
  4. ASS 头的 PlayResX/Y 必须等于视频尺寸（否则字号被按 384×288 放大 3.35×）。

运行: python3 test_subtitles.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parent / "tools" / "voice"
sys.path.insert(0, str(TOOLS))
import subtitles as S  # noqa: E402


def test_carry_safe_timestamp() -> None:
    # 0.999s = 99.9 厘秒 → 100 厘秒 → 必须进位成 1.00，而不是 "0:00:00.100"
    assert S.ts_ass(0.999) == "0:00:01.00", S.ts_ass(0.999)
    assert S.ts_ass(0.994) == "0:00:00.99", S.ts_ass(0.994)
    assert S.ts_ass(0.0) == "0:00:00.00"
    assert S.ts_ass(3661.234) == "1:01:01.23", S.ts_ass(3661.234)
    assert S.ts_ass(-5) == "0:00:00.00", "负时间必须夹到 0，不能出负号"
    print("ok  test_carry_safe_timestamp（总厘秒进位：0.999 → 1.00，不是 .100）")


def test_phrase_paging_covers_the_window() -> None:
    text = "先看敌人意图再决定是补防御还是全力输出因为这一回合的伤害刚好卡在斩杀线下面一点点"
    cues = S.page_text(text, 10.0, 14.0, max_chars=12)
    assert len(cues) >= 3, f"25 字 / 12 字上限应当拆成多条，实得 {len(cues)}"
    assert all(S.display_width(c.text) <= 12 for c in cues), \
        [S.display_width(c.text) for c in cues]
    assert abs(cues[0].start - 10.0) < 1e-6, cues[0]
    assert abs(cues[-1].end - 14.0) < 1e-6, cues[-1]
    for a, b in zip(cues, cues[1:]):
        assert abs(a.end - b.start) < 1e-6, f"分页有空洞/重叠: {a} -> {b}"
    # 文本不能丢字
    assert "".join(c.text for c in cues) == text, "".join(c.text for c in cues)
    print(f"ok  test_phrase_paging_covers_the_window（{len(cues)} 条，无空洞无重叠，末条精确落点）")


def test_no_page_when_it_fits() -> None:
    cues = S.page_text("短句。", 0.0, 1.0, max_chars=20)
    assert len(cues) == 1 and cues[0].text == "短句。", cues
    print("ok  test_no_page_when_it_fits（放得下就不拆）")


def test_srt_roundtrip_and_ass_playres(tmp: Path) -> None:
    cues = [S.Cue(0.2, 1.2, "第一句"), S.Cue(2.1, 3.1, "第二句")]
    srt = S.write_srt(cues, tmp / "s.srt")
    back = S.parse_srt(srt.read_text(encoding="utf-8"))
    assert [(c.start, c.end, c.text) for c in back] == [(0.2, 1.2, "第一句"), (2.1, 3.1, "第二句")], back
    ass = S.write_ass(cues, tmp / "s.ass", 960, 966, labels=[S.Cue(0.0, 1.0, "标注")])
    head = ass.read_text(encoding="utf-8")
    assert "PlayResX: 960" in head and "PlayResY: 966" in head, head[:200]
    assert "Dialogue: 1,0:00:00.00,0:00:01.00,Label" in head, head
    print("ok  test_srt_roundtrip_and_ass_playres（SRT 往返一致；PlayRes = 视频尺寸）")


def test_ass_text_is_escaped() -> None:
    rendered = S._ass_escape("有{标签}和\n换行")
    assert "{" not in rendered and "}" not in rendered, \
        "花括号必须转义，否则会被 libass 当成覆盖标签"
    assert "\\N" in rendered, "换行必须转成 ASS 的 \\N"
    assert "换行" in rendered, "转义不能把字吃掉"
    print("ok  test_ass_text_is_escaped（{ } 与换行不会变成 ASS 指令，也不丢字）")


def test_bad_srt_is_rejected_not_skipped() -> None:
    """坏条必须明确失败。历史上"静默 continue"让 74 条只烧进 1 条没被任何人发现。"""
    cases = {
        "没有时间码行": "1\n这不是时间码\n随便一段话\n",
        "end <= start": "1\n00:00:05,000 --> 00:00:04,000\n倒着走\n",
        "空文本": "1\n00:00:01,000 --> 00:00:02,000\n\n",
        "顺序错乱": "1\n00:00:05,000 --> 00:00:06,000\n后\n\n2\n00:00:01,000 --> 00:00:02,000\n前\n",
        "整份为空": "\n\n",
    }
    for name, text in cases.items():
        try:
            S.parse_srt(text)
        except SystemExit as e:
            assert "subtitle error" in str(e), (name, e)
        else:
            raise AssertionError(f"{name}：坏 SRT 被静默接受了")
    print(f"ok  test_bad_srt_is_rejected_not_skipped（{len(cases)} 种坏输入全部明确失败）")


def test_crlf_and_english_line_breaks() -> None:
    # CRLF：块分隔与行尾都必须正确处理，不能把 \r 带进字幕文本
    srt = "1\r\n00:00:01,000 --> 00:00:02,000\r\nhello world\r\n"
    c = S.parse_srt(srt)
    assert len(c) == 1 and c[0].text == "hello world", c
    # 西文折行必须补空格（否则 helloworld，词边界丢失）；中文折行不能补空格
    assert S.join_wrapped_lines(["hello", "world"]) == "hello world"
    assert S.join_wrapped_lines(["你好", "世界"]) == "你好世界"
    assert S.join_wrapped_lines(["你好", "world"]) == "你好world"
    two = S.parse_srt("1\n00:00:01,000 --> 00:00:02,000\nпервая строка\nвторая строка\n")
    assert two[0].text == "первая строка вторая строка", two[0].text
    print("ok  test_crlf_and_english_line_breaks（CRLF 干净；西文保留词边界，中文不加空格）")


def test_numeric_args_are_validated(tmp: Path) -> None:
    cues = tmp / "c.tsv"
    cues.write_text("0\t1000\t短句\n", encoding="utf-8")
    bad = [["--w", "0"], ["--h", "-5"], ["--size", "0"], ["--margin-lr", "-1"],
           ["--margin-lr", "500"], ["--margin-v", "-3"]]
    for extra in bad:
        p = subprocess.run([sys.executable, str(TOOLS / "subtitles.py"), "build",
                            "--cues", str(cues), "--srt", str(tmp / "o.srt"),
                            "--ass", str(tmp / "o.ass"), "--w", "320", "--h", "240"] + extra,
                           capture_output=True, text=True)
        assert p.returncode != 0 and "subtitle error" in (p.stdout + p.stderr), (extra, p.stdout, p.stderr)
    print(f"ok  test_numeric_args_are_validated（{len(bad)} 组非法数值全部拒绝）")


def main() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_carry_safe_timestamp()
        test_phrase_paging_covers_the_window()
        test_no_page_when_it_fits()
        test_srt_roundtrip_and_ass_playres(tmp)
        test_ass_text_is_escaped()
        test_bad_srt_is_rejected_not_skipped()
        test_crlf_and_english_line_breaks()
        test_numeric_args_are_validated(tmp)
    print("\n全部通过（8 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
