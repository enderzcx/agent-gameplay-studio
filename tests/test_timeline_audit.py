#!/usr/bin/env python3
"""Offline tests for scripts/check_timeline_audit.py (preflight + adopted-timeline audit).

Everything here is deterministic and needs no model, no network, no credential and no real match.
Media is generated in a temp directory with `ffmpeg` lavfi sources, so the "real ffmpeg" path is
exercised without committing any binary fixture.

The fixtures in `tests/fixtures/` are anonymous synthetic timelines that reproduce the defects seen
in a real re-edit:

  * narration claiming a phase the picture has not reached yet (a post-battle / card-pick line
    sitting on the battle opening);
  * a line anchored to a *different* event at the same phase (the silent swap), and a line whose
    declared anchor interval lies outside the shot that is actually on screen;
  * a cross-turn shot whose narration only references a fragment of the span it covers;
  * a registered freeze-frame with no on-screen mark, and a reward hold anchored outside the window
    where the candidates are actually visible;
  * narration longer than its own picture window;
  * two subtitle versions mixed in one timeline;
  * a stale / incomplete / self-contradicting / non-finite silence ledger;
  * a stale source manifest (recorded digest no longer matches the file on disk);
  * NaN and negative anchor intervals.

`check_postproduction.py timeline` still passes the semantic ones: that is the gap this suite locks
shut, and the first case below asserts it explicitly.

Run:  python3 tests/test_timeline_audit.py
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
AUDIT = ROOT / "skills" / "gameplay-postproduction" / "scripts" / "check_timeline_audit.py"
STRUCT = ROOT / "skills" / "gameplay-postproduction" / "scripts" / "check_postproduction.py"
FIX = HERE / "fixtures"

PASS = 0
FAIL = 0

# (fixture suffix, substring the audit must report)
NEGATIVE_FIXTURES = [
    ("phase-mismatch", "阶段错位"),
    ("anchor-event-mismatch", "换了事件不能默默过"),
    ("anchor-outside-shot", "不在这一行真实画面的源区间"),
    ("cross-turn-uncovered", "没有覆盖镜头跨度"),
    ("anchor-nonfinite", "anchor_source"),
    ("anchor-negative", "anchor_source"),
    ("hold-unlabeled", "hold_mark 为空"),
    ("hold-outside-window", "可见区间"),
    ("overlong-narration", "放不进"),
    ("version-mixed", "subtitle_source"),
]


def run(script: Path, *args: str) -> tuple:
    p = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)
    try:
        payload = json.loads(p.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"_raw": p.stdout[:600]}
    return p.returncode, payload, p.stderr


def ck(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        print(f"ok  {name}")
        PASS += 1
    else:
        print(f"FAIL {name}: {detail}")
        FAIL += 1


def expect(name: str, want_rc: int, script: Path, args: tuple, substr: str | None = None) -> dict:
    global PASS, FAIL
    rc, payload, stderr = run(script, *args)
    detail = ""
    ok = rc == want_rc
    if ok and substr is not None:
        blob = json.dumps(payload, ensure_ascii=False) + stderr
        ok = substr in blob
        detail = f"（输出里找不到 {substr!r}）" if not ok else ""
    if ok:
        print(f"ok  {name}")
        PASS += 1
    else:
        print(f"FAIL {name}: rc={rc} want={want_rc} {detail}")
        print(f"     payload={json.dumps(payload, ensure_ascii=False)[:400]}")
        print(f"     stderr={stderr.strip()[:300]}")
        FAIL += 1
    return payload


# ------------------------------------------------------------------ scratch media

def sh(cmd: list) -> None:
    p = subprocess.run(cmd, capture_output=True, text=True)
    assert p.returncode == 0, f"{cmd[0]} failed: {p.stderr[-400:]}"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_final(path: Path, seconds: float, tone: int) -> None:
    sh(["ffmpeg", "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency={tone}",
        "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path)])


def make_media(root: Path) -> dict:
    """Tiny lavfi-generated media. No committed binaries, no real recording, no model."""
    src = root / "src-normal.mp4"           # 20 s, 30 fps, NO audio stream
    sh(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
        "-i", "testsrc=size=320x240:rate=30", "-t", "20",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(src)])

    silent = root / "src-silent-track.mp4"  # audio stream present but digitally silent
    sh(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
        "-i", "testsrc=size=320x240:rate=30", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono",
        "-t", "6", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(silent)])

    sparse = root / "src-sparse.mp4"        # ~2 fps capture => sparse frame sampling
    sh(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
        "-i", "testsrc=size=320x240:rate=2", "-t", "6",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(sparse)])

    voice = root / "voice.wav"              # narration track: 66.0 s to match the timeline
    sh(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
        "-i", "anullsrc=r=24000:cl=mono", "-t", "66", str(voice)])

    final = root / "final.mp4"              # the exported cut: 66 s, video + audio
    make_final(final, 66, 440)

    not_media = root / "not-media.txt"
    not_media.write_text("this is not a media file\n", encoding="utf-8")
    return {"src": src, "silent": silent, "sparse": sparse, "voice": voice, "final": final,
            "not_media": not_media}


CUE_TIMES = [(0.5, 3.5), (6.5, 9.0), (34.5, 38.0), (62.2, 64.0)]
CUE_TEXTS = ["opening line", "mid-battle line", "reward line", "closing line covering both turns"]


def hhms(x: float) -> str:
    ms = int(round(x * 1000))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def write_srt(path: Path, rows: list) -> Path:
    body = [f"{i + 1}\n{hhms(s)} --> {hhms(e)}\n{text}\n" for i, (s, e, text) in enumerate(rows)]
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def good_cues() -> list:
    return [(s, e, t) for (s, e), t in zip(CUE_TIMES, CUE_TEXTS)]


def materialize(name: str, root: Path, srt: Path, audio: Path, final: Path) -> Path:
    """把 fixture 里的 @..._SHA@ 占位换成真实产物摘要，写进临时目录。

    fixture 本身是**未绑定**的（直接跑 audit 必然失败，这是对的）；测试在这里做绑定，
    这样"同一个 fixture + 真实产物"才有意义，负例也只剩它要复现的那一个缺陷。
    """
    body = (FIX / name).read_text(encoding="utf-8")
    body = (body.replace("@SUB_SHA@", sha(srt))
                .replace("@AUD_SHA@", sha(audio))
                .replace("@FINAL_SHA@", sha(final)))
    out = root / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    return out


def audit_args(timeline: Path, pre: Path, ledger: Path, srt: Path, audio: Path, final: Path) -> tuple:
    return ("audit", str(timeline), "--preflight", str(pre),
            "--silence-ledger", str(ledger), "--subtitle", str(srt), "--audio", str(audio),
            "--final-mp4", str(final), "--json")


# ------------------------------------------------------------------ cases

def test_structure_mode_alone_lets_the_semantic_defects_through() -> None:
    """The pre-existing checker is not wrong — it never claimed to look at these semantics.

    All the *semantic* defective timelines pass `timeline` structure mode today, and only `audit`
    rejects them. The arithmetic one is the exception, which is why `audit` delegates arithmetic to
    the structure checker instead of implementing the same invariant twice.
    """
    for name, _sub in NEGATIVE_FIXTURES:
        expect(f"structure mode still accepts the {name} fixture (the gap this suite closes)",
               0, STRUCT, ("timeline", str(FIX / f"timeline.audit.{name}.tsv")))
    expect("structure mode rejects the bad-arithmetic fixture (audit inherits that check)",
           1, STRUCT, ("timeline", str(FIX / "timeline.audit.bad-arithmetic.tsv")),
           substr="final 长度")


def test_preflight_classifies_input_state(root: Path, media: dict) -> None:
    out = root / "preflight-normal.json"
    rc, payload, stderr = run(AUDIT, "preflight", "--json", "--out", str(out),
                              f"rec-normal={media['src']}")
    a = (payload.get("assets") or {}).get("rec-normal", {})
    ck("preflight: a normal source is fully determined (digest, size, fps, no audio stream)",
       rc == 0 and a.get("status") == "determined" and a.get("audio_signal") == "absent"
       and a.get("has_audio") is False and a.get("frame_sampling") == "normal"
       and len(str(a.get("sha256"))) == 64 and (a.get("size_bytes") or 0) > 0,
       f"rc={rc} {a} {stderr[-200:]}")
    ck("preflight: --out writes the ledger keyed by asset_id (no source audio is quietly dropped)",
       out.is_file() and list(json.loads(out.read_text(encoding="utf-8"))["assets"]) == ["rec-normal"])

    rc, payload, stderr = run(AUDIT, "preflight", "--json", f"rec-silent={media['silent']}")
    a = (payload.get("assets") or {}).get("rec-silent", {})
    ck("preflight: an audio stream that carries no signal is `silent`, not `present`",
       rc == 0 and a.get("audio_signal") == "silent" and a.get("has_audio") is True,
       f"rc={rc} {a} {stderr[-200:]}")

    rc, payload, stderr = run(AUDIT, "preflight", "--json", f"rec-sparse={media['sparse']}")
    a = (payload.get("assets") or {}).get("rec-sparse", {})
    ck("preflight: a ~2 fps capture is flagged `sparse` instead of being assumed fine",
       rc == 0 and a.get("frame_sampling") == "sparse", f"rc={rc} {a} {stderr[-200:]}")

    expect("preflight: a non-media file is `undetermined` and fails instead of passing quietly",
           1, AUDIT, ("preflight", "--json", f"rec-bad={media['not_media']}"), substr="undetermined")


def test_audit_positive(root: Path, media: dict, pre: Path, srt: Path) -> None:
    tl = materialize("timeline.audit.ok.tsv", root, srt, media["voice"], media["final"])
    rc, payload, stderr = run(AUDIT, *audit_args(tl, pre, FIX / "silence.ledger.ok.tsv", srt,
                                                 media["voice"], media["final"]))
    ck("audit: the fully anchored + content-bound adopted timeline passes",
       rc == 0 and payload.get("ok") is True, f"rc={rc} {payload.get('errors')} {stderr[-200:]}")
    rep = payload.get("report") or {}
    ck("audit: the report lists the two long silent stretches it found",
       (rep.get("silence") or {}).get("threshold_s") == 20.0
       and len((rep.get("silence") or {}).get("gaps") or []) == 2, str(rep.get("silence")))
    ck("audit: the report states what it refuses to judge (narration ratio, decode success, taste)",
       "旁白占比" in "".join(rep.get("not_a_verdict_on") or []), str(rep.get("not_a_verdict_on")))
    ck("audit: the report states that human-declared anchors/annotations are not machine proof",
       any("anchor_asset" in c for c in rep.get("human_annotations_not_machine_verified") or []),
       str(rep.get("human_annotations_not_machine_verified")))
    vb = rep.get("version_binding") or {}
    ck("audit: version binding pins subtitle source, cue count and the three content digests",
       vb.get("subtitle_source") == "subs-synth" and vb.get("subtitle_cues") == 4
       and vb.get("subtitle_sha256") == sha(srt) and vb.get("audio_sha256") == sha(media["voice"])
       and vb.get("final_sha256") == sha(media["final"]), str(vb)[:300])
    ck("audit: each anchor record names asset, events and the shot span it was checked against",
       len(rep.get("anchors") or []) == 4
       and all({"anchor_asset", "anchor_event", "anchor_source", "shot_source"} <= set(a)
               for a in rep["anchors"]), str(rep.get("anchors"))[:300])
    ck("audit: a cross-turn shot is carried as an explicit unverified item",
       any("跨回合" in u for u in payload.get("unverified") or []), str(payload.get("unverified")))
    ck("audit: the source's missing audio track is carried as an explicit unverified item",
       any("无音轨" in u for u in payload.get("unverified") or []), str(payload.get("unverified")))


def test_audit_semantic_defects(root: Path, media: dict, pre: Path, srt: Path) -> None:
    for name, substr in NEGATIVE_FIXTURES:
        tl = materialize(f"timeline.audit.{name}.tsv", root, srt, media["voice"], media["final"])
        expect(f"audit: the {name} fixture is rejected", 1, AUDIT,
               audit_args(tl, pre, FIX / "silence.ledger.ok.tsv", srt, media["voice"],
                          media["final"]), substr=substr)
    tl = materialize("timeline.audit.bad-arithmetic.tsv", root, srt, media["voice"], media["final"])
    expect("audit: broken range arithmetic is still caught (delegated, not re-implemented)",
           1, AUDIT, audit_args(tl, pre, FIX / "silence.ledger.ok.tsv", srt, media["voice"],
                                media["final"]), substr="结构")


def test_audit_content_binding(root: Path, media: dict, pre: Path) -> None:
    """Same segment count is not evidence: words, timings and file content must all match."""
    ok_srt = write_srt(root / "subs-ok.srt", good_cues())

    # (a) same cue count, one line re-worded — bound to itself, so only the text check can fire
    edited = write_srt(root / "subs-edited-words.srt",
                       [(s, e, ("changed words" if t == "reward line" else t))
                        for s, e, t in good_cues()])
    tl = materialize("timeline.audit.ok.tsv", root / "words", edited, media["voice"], media["final"])
    expect("audit: a same-count subtitle whose words changed is rejected", 1, AUDIT,
           audit_args(tl, pre, FIX / "silence.ledger.ok.tsv", edited, media["voice"],
                      media["final"]), substr="字幕文本")

    # ...and the same edit also breaks the declared content hash when the timeline was NOT re-bound
    tl_ok = materialize("timeline.audit.ok.tsv", root, ok_srt, media["voice"], media["final"])
    expect("audit: a re-worded subtitle also fails the declared content hash", 1, AUDIT,
           audit_args(tl_ok, pre, FIX / "silence.ledger.ok.tsv", edited, media["voice"],
                      media["final"]), substr="字幕内容 hash 不符")

    # (b) same cue count and same words, timings moved out of that segment's window
    shifted_cues = [(40.0, 42.0, t) if t == "reward line" else (s, e, t)
                    for s, e, t in good_cues()]
    shifted = write_srt(root / "subs-shifted.srt", shifted_cues)
    tl2 = materialize("timeline.audit.ok.tsv", root / "shifted", shifted, media["voice"],
                      media["final"])
    expect("audit: a same-count subtitle whose timings moved out of its segment is rejected",
           1, AUDIT, audit_args(tl2, pre, FIX / "silence.ledger.ok.tsv", shifted, media["voice"],
                                media["final"]), substr="字幕时点")

    # (c) a replaced audio track / an older exported cut, both the same length
    other_voice = root / "voice-replaced.wav"
    sh(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
        "-i", "sine=frequency=1000", "-t", "66", str(other_voice)])
    expect("audit: a replaced audio track of the same length is rejected by content hash",
           1, AUDIT, audit_args(tl_ok, pre, FIX / "silence.ledger.ok.tsv", ok_srt, other_voice,
                                media["final"]), substr="音轨内容 hash 不符")

    other_final = root / "final-old.mp4"
    make_final(other_final, 66, 441)
    expect("audit: an older exported cut of the same length is rejected by content hash",
           1, AUDIT, audit_args(tl_ok, pre, FIX / "silence.ledger.ok.tsv", ok_srt, media["voice"],
                                other_final), substr="成片内容 hash 不符")

    expect("audit: a missing final cut cannot pass", 1, AUDIT,
           audit_args(tl_ok, pre, FIX / "silence.ledger.ok.tsv", ok_srt, media["voice"],
                      root / "nope.mp4"), substr="成片文件不存在")

    # (d) the media parameters themselves are mandatory — no independent JSON report can replace them
    for missing in ("--final-mp4", "--subtitle", "--audio", "--preflight", "--silence-ledger"):
        args = list(audit_args(tl_ok, pre, FIX / "silence.ledger.ok.tsv", ok_srt, media["voice"],
                               media["final"]))
        i = args.index(missing)
        del args[i:i + 2]
        expect(f"audit: omitting {missing} is a usage error", 2, AUDIT, tuple(args))


def test_audit_ledger_and_numeric_rejection(root: Path, media: dict, pre: Path) -> None:
    srt = write_srt(root / "subs-num.srt", good_cues())
    tl = materialize("timeline.audit.ok.tsv", root, srt, media["voice"], media["final"])
    for ledger, substr in (("silence.ledger.missing.tsv", "静默"),
                           ("silence.ledger.stale.tsv", "静默"),
                           ("silence.ledger.cut.tsv", "静默"),
                           ("silence.ledger.nonfinite.tsv", "final_range")):
        expect(f"audit: {ledger} is rejected", 1, AUDIT,
               audit_args(tl, pre, FIX / ledger, srt, media["voice"], media["final"]),
               substr=substr)

    base = ("audit", str(tl), "--preflight", str(pre), "--silence-ledger",
            str(FIX / "silence.ledger.ok.tsv"), "--subtitle", str(srt), "--audio",
            str(media["voice"]), "--final-mp4", str(media["final"]), "--json")
    for flag, value in (("--tol", "nan"), ("--tol", "-1"), ("--silence-threshold", "nan"),
                        ("--silence-threshold", "-5")):
        expect(f"audit: {flag} {value} is a usage error, not a silent default", 2, AUDIT,
               base + (flag, value))
    expect("preflight: --min-fps inf is a usage error, not a silent default", 2, AUDIT,
           ("preflight", "--json", f"rec-x={media['src']}", "--min-fps", "inf"))


def test_audit_stale_manifest(root: Path, media: dict, pre: Path) -> None:
    srt = write_srt(root / "subs-stale.srt", good_cues())
    tl = materialize("timeline.audit.ok.tsv", root, srt, media["voice"], media["final"])
    body = json.loads(pre.read_text(encoding="utf-8"))
    body["assets"]["rec-synth"]["sha256"] = "0" * 64
    stale = root / "preflight-stale.json"
    stale.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    expect("audit: a source whose digest no longer matches the report is a stale manifest",
           1, AUDIT, audit_args(tl, stale, FIX / "silence.ledger.ok.tsv", srt, media["voice"],
                                media["final"]), substr="sha256")

    body = json.loads(pre.read_text(encoding="utf-8"))
    body["assets"]["rec-synth"]["source_path"] = str(root / "gone.mp4")
    gone = root / "preflight-gone.json"
    gone.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    expect("audit: a source that is no longer on disk cannot be certified",
           1, AUDIT, audit_args(tl, gone, FIX / "silence.ledger.ok.tsv", srt, media["voice"],
                                media["final"]), substr="不存在")

    expect("audit: the preflight report is mandatory, not optional",
           2, AUDIT, ("audit", str(tl), "--json"))


def main() -> int:
    if not AUDIT.is_file():
        print(f"audit checker not found: {AUDIT}", file=sys.stderr)
        return 2
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        print("ffmpeg/ffprobe not found: the preflight cases cannot run", file=sys.stderr)
        return 2
    root = Path(tempfile.mkdtemp(prefix="tlaudit-test-"))
    try:
        test_structure_mode_alone_lets_the_semantic_defects_through()
        media = make_media(root)
        pre = root / "preflight-synth.json"
        rc, _p, err = run(AUDIT, "preflight", "--json", "--out", str(pre),
                          f"rec-synth={media['src']}")
        assert rc == 0 and pre.is_file(), f"preflight failed: rc={rc} {err}"
        srt = write_srt(root / "subs.srt", good_cues())
        test_preflight_classifies_input_state(root, media)
        test_audit_positive(root, media, pre, srt)
        test_audit_semantic_defects(root, media, pre, srt)
        test_audit_content_binding(root, media, pre)
        test_audit_ledger_and_numeric_rejection(root, media, pre)
        test_audit_stale_manifest(root, media, pre)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
