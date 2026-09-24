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

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tools" / "voice"))
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


def main() -> int:
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        test_carry_safe_timestamp()
        test_phrase_paging_covers_the_window()
        test_no_page_when_it_fits()
        test_srt_roundtrip_and_ass_playres(tmp)
        test_ass_text_is_escaped()
    print("\n全部通过（5 项）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
