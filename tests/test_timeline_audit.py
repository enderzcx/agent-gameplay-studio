#!/usr/bin/env python3
"""Offline tests for scripts/check_timeline_audit.py (preflight + adopted-timeline audit).

Everything here is deterministic and needs no model, no network, no credential and no real match.
Media is generated in a temp directory with `ffmpeg` lavfi sources, so the "real ffmpeg" path is
exercised without committing any binary fixture.

The fixtures in `tests/fixtures/` are anonymous synthetic timelines reproducing the defects seen in
a real re-edit:

  * a phase claimed before the picture reaches it, a cross-phase line that was never declared as a
    retrospective, and an invalid `claim_mode`;
  * a line anchored to a *different* event at the same phase, and an anchor interval outside the
    shot that is actually on screen;
  * a cross-turn shot whose narration only references a fragment of the span it covers;
  * a registered freeze-frame with no on-screen mark, and a reward hold anchored outside the window
    where the candidates are actually visible;
  * narration longer than its own picture window;
  * two subtitle versions mixed in one timeline;
  * stale / incomplete / self-contradicting / non-finite silence ledgers — **including a ledger
    computed from picture windows instead of the real narration audio spans**;
  * a stale source manifest, NaN and negative anchor intervals, and broken range arithmetic.

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
sys.path.insert(0, str(AUDIT.parent))

PASS = 0
FAIL = 0

# (fixture suffix, substring the audit must report)
NEGATIVE_FIXTURES = [
    ("phase-mismatch", "阶段错位"),
    ("claim-cross-phase-undeclared", "必须显式写成"),
    ("claim-mode-invalid", "claim_mode"),
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


def run(script: Path, *args: str, cwd: Path | None = None) -> tuple:
    p = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True,
                       cwd=str(cwd) if cwd else None)
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


def expect(name: str, want_rc: int, script: Path, args: tuple, substr: str | None = None,
           cwd: Path | None = None) -> dict:
    global PASS, FAIL
    rc, payload, stderr = run(script, *args, cwd=cwd)
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

    src_long = root / "src-long.mp4"        # 66 s source, for the "silence vs picture" case
    sh(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
        "-i", "testsrc=size=320x240:rate=30", "-t", "66",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(src_long)])

    not_media = root / "not-media.txt"
    not_media.write_text("this is not a media file\n", encoding="utf-8")
    return {"src": src, "silent": silent, "sparse": sparse, "voice": voice, "final": final,
            "src_long": src_long, "not_media": not_media}


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


def write_receipt(root: Path, timeline: Path, srt: Path, audio: Path, final: Path, pre: Path,
                  name: str = "produce-receipt.json", **overrides) -> Path:
    """制作 receipt：由制作路径写出，把 timeline 与三份产物绑在同一次制作上。"""
    assets = json.loads(pre.read_text(encoding="utf-8")).get("assets") or {}
    rec = {
        "schema": "gameplay-postproduction/produce-receipt/1",
        "produced_by": "tests/test_timeline_audit.py (synthetic harness)",
        "produced_at": "2026-09-24T00:00:00Z",
        "timeline": {"path": str(timeline), "sha256": sha(timeline)},
        "subtitle": {"path": str(srt), "sha256": sha(srt)},
        "audio": {"path": str(audio), "sha256": sha(audio)},
        "final": {"path": str(final), "sha256": sha(final)},
        "sources": {k: v.get("sha256") for k, v in assets.items()},
    }
    for dotted, value in overrides.items():
        key, _, field = dotted.partition("__")
        if field:
            rec[key][field] = value
        else:
            rec[key] = value
    out = root / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def audit_args(timeline: Path, pre: Path, ledger: Path, srt: Path, audio: Path, final: Path,
               receipt: Path) -> tuple:
    return ("audit", str(timeline), "--preflight", str(pre),
            "--silence-ledger", str(ledger), "--subtitle", str(srt), "--audio", str(audio),
            "--final-mp4", str(final), "--receipt", str(receipt), "--json")


def bound(name: str, root: Path, srt: Path, audio: Path, final: Path, pre: Path,
          ledger: Path = None, **overrides):
    """materialize + receipt + 组装好的一整套调用参数。"""
    tl = materialize(name, root, srt, audio, final)
    rec = write_receipt(root, tl, srt, audio, final, pre, **overrides)
    return audit_args(tl, pre, ledger or (FIX / "silence.ledger.ok.tsv"), srt, audio, final, rec)


# ------------------------------------------------------------------ cases

def test_structure_mode_alone_lets_the_semantic_defects_through() -> None:
    """The pre-existing checker is not wrong — it never claimed to look at these semantics."""
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
    ck("preflight: the ledger stores an absolute, resolved source_path (cwd-independent)",
       Path(str(a.get("source_path"))).is_absolute()
       and Path(str(a.get("source_path"))) == media["src"].resolve(), str(a.get("source_path")))

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


def test_probe_helpers_fail_closed(root: Path, media: dict) -> None:
    """The two probe helpers must not silently succeed on a partial read."""
    import check_timeline_audit as mod

    data, err = mod.ffprobe_json(media["src"], "stream=codec_type:format=duration,size")
    ck("ffprobe helper: `:`-separated sections really return the `format` block "
       "(the old `&` form silently dropped it)",
       data is not None and (data.get("format") or {}).get("duration") is not None
       and (data.get("format") or {}).get("size") is not None, f"{data} {err}")
    bad, _e = mod.ffprobe_json(media["src"], "stream=codec_type&format=duration")
    ck("ffprobe helper: the `&` form is demonstrably wrong (format section missing)",
       bad is not None and not (bad.get("format") or {}).get("duration"),
       f"{bad}")

    # a video-only file cannot be measured for audio signal: ffmpeg exits non-zero -> unknown
    signal, vol, serr = mod.measure_audio_signal(media["src"], -60.0)
    ck("volumedetect: ffmpeg non-zero exit is a FAILURE, not a `silent`/`present` verdict",
       signal == "unknown" and vol is None and serr, f"{signal} {vol} {serr}")
    ck("media_duration: a real file yields a finite positive duration",
       (mod.media_duration(media["final"])[0] or 0) > 60, str(mod.media_duration(media["final"])))
    ck("has_audio_stream: a video-only file is reported as having no audio stream",
       mod.has_audio_stream(media["src"])[0] is False, str(mod.has_audio_stream(media["src"])))

    for fn, value, label in ((mod.validate_tol, float("nan"), "tol"),
                             (mod.validate_tol, -1.0, "tol"),
                             (mod.validate_tol, 1e9, "tol"),
                             (mod.validate_silence_threshold, float("inf"), "silence_threshold"),
                             (mod.validate_silence_threshold, 0.0, "silence_threshold"),
                             (mod.validate_silence_threshold, -5.0, "silence_threshold")):
        try:
            fn(value)
            ck(f"library: {label}={value!r} is rejected at the library layer", False, "accepted")
        except SystemExit:
            ck(f"library: {label}={value!r} is rejected at the library layer", True)


def test_audit_positive(root: Path, media: dict, pre: Path, srt: Path) -> None:
    args = bound("timeline.audit.ok.tsv", root, srt, media["voice"], media["final"], pre)
    rc, payload, stderr = run(AUDIT, *args)
    ck("audit: the fully anchored + content-bound + receipt-bound timeline passes",
       rc == 0 and payload.get("ok") is True, f"rc={rc} {payload.get('errors')} {stderr[-200:]}")
    rep = payload.get("report") or {}
    sil = rep.get("silence") or {}
    ck("audit: silence is measured on the REAL audio spans, so the first gap starts at 11.5 s",
       [g["final_range"] for g in sil.get("gaps") or []] == [[11.5, 34.0], [39.5, 62.0]],
       str(sil.get("gaps")))
    ck("audit: narration ratio uses the measured audio seconds (20.0 s / 66.0 s), not picture length",
       sil.get("narration_audio_s") == 20.0
       and abs((sil.get("narration_ratio") or 0) - 20 / 66) < 1e-4, str(sil))
    ck("audit: the report states what it refuses to judge (narration ratio, decode, taste)",
       "旁白占比" in "".join(rep.get("not_a_verdict_on") or []), str(rep.get("not_a_verdict_on")))
    ck("audit: the report states that human-declared anchors/annotations are not machine proof",
       any("anchor_asset" in c for c in rep.get("human_annotations_not_machine_verified") or []),
       str(rep.get("human_annotations_not_machine_verified")))
    ck("audit: the report names the receipt it verified",
       (rep.get("receipt") or {}).get("produced_by") and (rep.get("receipt") or {}).get("sha256"),
       str(rep.get("receipt")))
    vb = rep.get("version_binding") or {}
    ck("audit: version binding pins subtitle source, cue count and the three content digests",
       vb.get("subtitle_source") == "subs-synth" and vb.get("subtitle_cues") == 4
       and vb.get("subtitle_sha256") == sha(srt) and vb.get("audio_sha256") == sha(media["voice"])
       and vb.get("final_sha256") == sha(media["final"]) and vb.get("audio_has_stream") is True,
       str(vb)[:300])
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
        expect(f"audit: the {name} fixture is rejected", 1, AUDIT,
               bound(f"timeline.audit.{name}.tsv", root, srt, media["voice"], media["final"], pre),
               substr=substr)
    expect("audit: broken range arithmetic is still caught (delegated, not re-implemented)",
           1, AUDIT,
           bound("timeline.audit.bad-arithmetic.tsv", root, srt, media["voice"], media["final"],
                 pre), substr="结构")


def test_silence_is_measured_on_audio_not_picture(root: Path, media: dict, pre: Path) -> None:
    """A 66 s picture carrying a 1 s line is 65 s of silence, and the ratio is 1/66."""
    header = (FIX / "timeline.audit.ok.tsv").read_text(encoding="utf-8").splitlines()
    hdr = next(ln for ln in header if ln.startswith("asset_id\t"))
    srt = write_srt(root / "subs-solo.srt", [(0.0, 1.0, "one second line")])
    # a 66 s SHOT (so the timeline arithmetic stays self-consistent) carrying only a 1 s line
    row = ["rec-synth", "ev-solo", "0.0-66.0", "0.0-66.0", "0.0-66.0", "1x", "one second line",
           "1.0", "subs-synth", "battle", "battle", "live", "rec-synth", "ev-solo", "0.0-1.0",
           "src rec-synth@0.5s opening", "-", "-", "@SUB_SHA@", "@AUD_SHA@", "@FINAL_SHA@"]
    work = root / "shortsilence"
    work.mkdir(parents=True, exist_ok=True)
    tl = work / "timeline.tsv"
    tl.write_text(hdr + "\n" + "\t".join(row) + "\n", encoding="utf-8")
    tl.write_text(tl.read_text(encoding="utf-8")
                  .replace("@SUB_SHA@", sha(srt)).replace("@AUD_SHA@", sha(media["voice"]))
                  .replace("@FINAL_SHA@", sha(media["final"])), encoding="utf-8")
    pre_local = work / "preflight.json"
    rc, _p, err = run(AUDIT, "preflight", "--json", "--out", str(pre_local),
                      f"rec-synth={media['src_long']}")
    assert rc == 0, err
    rec = write_receipt(work, tl, srt, media["voice"], media["final"], pre_local)
    empty = root / "gaps-empty.tsv"
    empty.write_text("gap_id\tfinal_range\tdisposition\treason\n", encoding="utf-8")
    local_args = lambda ledger: audit_args(tl, pre_local, ledger, srt, media["voice"],  # noqa: E731
                                           media["final"], rec)

    payload = expect("audit: a 1 s line on a 66 s shot leaves 65 s of silence and is rejected",
                     1, AUDIT, local_args(empty), substr="65.000s")
    gaps = ((payload.get("report") or {}).get("silence") or {}).get("gaps") or []
    ck("audit: the reported gap starts at 1.0 s (end of the real audio), not at the shot end",
       gaps and gaps[0]["final_range"] == [1.0, 66.0], str(gaps))
    ck("audit: narration ratio is 1/66, not the picture window's",
       abs((((payload.get("report") or {}).get("silence") or {}).get("narration_ratio") or 0)
           - 1 / 66) < 1e-4, str(payload.get("report", {}).get("silence")))

    ledger = work / "gaps-solo.tsv"
    ledger.write_text("gap_id\tfinal_range\tdisposition\treason\n"
                      "g-1\t1.000-66.000\tkeep\tpicture carries the whole fight; no line needed\n",
                      encoding="utf-8")
    expect("audit: the same cut passes once that silence is justified per stretch",
           0, AUDIT, local_args(ledger))

    expect("audit: a ledger computed from picture windows instead of audio spans is rejected",
           1, AUDIT, bound("timeline.audit.ok.tsv", root, srt, media["voice"], media["final"], pre,
                           ledger=FIX / "silence.ledger.picture-window.tsv"), substr="静默")


def test_audit_content_binding(root: Path, media: dict, pre: Path) -> None:
    """Same segment count is not evidence: words, timings, streams and file content must match."""
    ok_srt = write_srt(root / "subs-ok.srt", good_cues())

    edited = write_srt(root / "subs-edited-words.srt",
                       [(s, e, ("changed words" if t == "reward line" else t))
                        for s, e, t in good_cues()])
    expect("audit: a same-count subtitle whose words changed is rejected", 1, AUDIT,
           bound("timeline.audit.ok.tsv", root / "words", edited, media["voice"], media["final"],
                 pre), substr="字幕文本")

    tl_ok = materialize("timeline.audit.ok.tsv", root, ok_srt, media["voice"], media["final"])
    rec_ok = write_receipt(root, tl_ok, ok_srt, media["voice"], media["final"], pre)
    expect("audit: a re-worded subtitle also fails the declared content hash", 1, AUDIT,
           audit_args(tl_ok, pre, FIX / "silence.ledger.ok.tsv", edited, media["voice"],
                      media["final"], rec_ok), substr="字幕内容 hash 不符")

    shifted_cues = [(40.0, 42.0, t) if t == "reward line" else (s, e, t)
                    for s, e, t in good_cues()]
    shifted = write_srt(root / "subs-shifted.srt", shifted_cues)
    expect("audit: a same-count subtitle whose timings moved out of its audio span is rejected",
           1, AUDIT, bound("timeline.audit.ok.tsv", root / "shifted", shifted, media["voice"],
                           media["final"], pre), substr="字幕时点")

    other_voice = root / "voice-replaced.wav"
    sh(["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi",
        "-i", "sine=frequency=1000", "-t", "66", str(other_voice)])
    expect("audit: a replaced audio track of the same length is rejected by content hash",
           1, AUDIT, audit_args(tl_ok, pre, FIX / "silence.ledger.ok.tsv", ok_srt, other_voice,
                                media["final"], rec_ok), substr="音轨内容 hash 不符")

    expect("audit: an --audio file with no audio stream is rejected (duration alone is not a track)",
           1, AUDIT, audit_args(tl_ok, pre, FIX / "silence.ledger.ok.tsv", ok_srt, media["src"],
                                media["final"], rec_ok), substr="没有音频流")

    other_final = root / "final-old.mp4"
    make_final(other_final, 66, 441)
    expect("audit: an older exported cut of the same length is rejected by content hash",
           1, AUDIT, audit_args(tl_ok, pre, FIX / "silence.ledger.ok.tsv", ok_srt, media["voice"],
                                other_final, rec_ok), substr="成片内容 hash 不符")

    expect("audit: a missing final cut cannot pass", 1, AUDIT,
           audit_args(tl_ok, pre, FIX / "silence.ledger.ok.tsv", ok_srt, media["voice"],
                      root / "nope.mp4", rec_ok), substr="成片文件不存在")

    for missing in ("--final-mp4", "--subtitle", "--audio", "--preflight", "--silence-ledger",
                    "--receipt"):
        args = list(audit_args(tl_ok, pre, FIX / "silence.ledger.ok.tsv", ok_srt, media["voice"],
                               media["final"], rec_ok))
        i = args.index(missing)
        del args[i:i + 2]
        expect(f"audit: omitting {missing} is a usage error", 2, AUDIT, tuple(args))


def test_receipt_binding(root: Path, media: dict, pre: Path) -> None:
    """A receipt written at production time is what ties the four artifacts to ONE production."""
    srt = write_srt(root / "subs-rec.srt", good_cues())
    tl = materialize("timeline.audit.ok.tsv", root, srt, media["voice"], media["final"])
    rec = write_receipt(root, tl, srt, media["voice"], media["final"], pre)
    base = audit_args(tl, pre, FIX / "silence.ledger.ok.tsv", srt, media["voice"],
                      media["final"], rec)
    expect("audit: a consistent receipt passes", 0, AUDIT, base)

    expect("audit: a missing receipt is rejected (old media + a fresh declaration is not proof)",
           1, AUDIT, audit_args(tl, pre, FIX / "silence.ledger.ok.tsv", srt, media["voice"],
                                media["final"], root / "no-receipt.json"), substr="receipt 不存在")

    # an OLD cut documented by a NEW timeline: the receipt is the only thing that can catch it
    newer = root / "newtl" / "timeline.tsv"
    newer.parent.mkdir(parents=True, exist_ok=True)
    newer.write_text(tl.read_text(encoding="utf-8").replace("opening line", "opening line v2"),
                     encoding="utf-8")
    expect("audit: a receipt that belongs to a DIFFERENT timeline is rejected",
           1, AUDIT, audit_args(newer, pre, FIX / "silence.ledger.ok.tsv", srt, media["voice"],
                                media["final"], rec), substr="不是同一次制作")

    for overrides, substr, label in (
            ({"produced_by": ""}, "produced_by", "missing produced_by"),
            ({"schema": "something-else/1"}, "schema", "wrong schema"),
            ({"final__sha256": "0" * 64}, "final sha256", "final digest that is not the file's"),
            ({"subtitle__sha256": "0" * 64}, "subtitle sha256", "subtitle digest mismatch")):
        bad = write_receipt(root, tl, srt, media["voice"], media["final"], pre,
                            name=f"receipt-{label.split()[0]}.json", **overrides)
        expect(f"audit: a receipt with a {label} is rejected", 1, AUDIT,
               audit_args(tl, pre, FIX / "silence.ledger.ok.tsv", srt, media["voice"],
                          media["final"], bad), substr=substr)

    bad_src = write_receipt(root, tl, srt, media["voice"], media["final"], pre,
                            name="receipt-bad-source.json")
    body = json.loads(bad_src.read_text(encoding="utf-8"))
    body["sources"]["rec-synth"] = "0" * 64
    bad_src.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    expect("audit: a receipt whose recorded source digest disagrees with the preflight ledger "
           "is rejected", 1, AUDIT,
           audit_args(tl, pre, FIX / "silence.ledger.ok.tsv", srt, media["voice"],
                      media["final"], bad_src), substr="源对不上")


def test_preflight_relative_paths_survive_a_cwd_change(root: Path, media: dict) -> None:
    """A relative source_path must resolve against the manifest's directory, not the caller's cwd."""
    work = root / "cwdwork"
    work.mkdir(parents=True, exist_ok=True)
    srt = write_srt(work / "subs.srt", good_cues())
    pre = work / "preflight.json"
    rc, _p, err = run(AUDIT, "preflight", "--json", "--out", str(pre), "rec-synth=src-normal.mp4",
                      cwd=root)
    ck("preflight: a path given relative to the caller's cwd is stored resolved",
       rc == 0 and Path(json.loads(pre.read_text(encoding="utf-8"))
                        ["assets"]["rec-synth"]["source_path"]).is_absolute(), err[-200:])

    # hand-edit it to a path that is only valid RELATIVE TO THE MANIFEST's directory,
    # then run the audit from a different cwd
    import os as _os
    body = json.loads(pre.read_text(encoding="utf-8"))
    body["assets"]["rec-synth"]["source_path"] = _os.path.relpath(media["src"], work)
    pre.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    tl = materialize("timeline.audit.ok.tsv", work, srt, media["voice"], media["final"])
    rec = write_receipt(work, tl, srt, media["voice"], media["final"], pre)
    expect("audit: a relative source_path resolves against the manifest dir, not the caller's cwd",
           0, AUDIT, audit_args(tl, pre, FIX / "silence.ledger.ok.tsv", srt, media["voice"],
                                media["final"], rec), cwd=ROOT)


def test_audit_ledger_and_numeric_rejection(root: Path, media: dict, pre: Path) -> None:
    srt = write_srt(root / "subs-num.srt", good_cues())
    for ledger, substr in (("silence.ledger.missing.tsv", "静默"),
                           ("silence.ledger.stale.tsv", "静默"),
                           ("silence.ledger.cut.tsv", "静默"),
                           ("silence.ledger.nonfinite.tsv", "final_range")):
        expect(f"audit: {ledger} is rejected", 1, AUDIT,
               bound("timeline.audit.ok.tsv", root, srt, media["voice"], media["final"], pre,
                     ledger=FIX / ledger), substr=substr)

    args = bound("timeline.audit.ok.tsv", root, srt, media["voice"], media["final"], pre)
    for flag, value in (("--tol", "nan"), ("--tol", "-1"), ("--tol", "1e9"),
                        ("--silence-threshold", "nan"), ("--silence-threshold", "0"),
                        ("--silence-threshold", "-5")):
        expect(f"audit: {flag} {value} is a usage error, not a silent default", 2, AUDIT,
               args + (flag, value))
    expect("preflight: --min-fps inf is a usage error, not a silent default", 2, AUDIT,
           ("preflight", "--json", f"rec-x={media['src']}", "--min-fps", "inf"))


def test_audit_stale_manifest(root: Path, media: dict, pre: Path) -> None:
    srt = write_srt(root / "subs-stale.srt", good_cues())
    tl = materialize("timeline.audit.ok.tsv", root, srt, media["voice"], media["final"])
    rec = write_receipt(root, tl, srt, media["voice"], media["final"], pre)
    body = json.loads(pre.read_text(encoding="utf-8"))
    body["assets"]["rec-synth"]["sha256"] = "0" * 64
    stale = root / "preflight-stale.json"
    stale.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    expect("audit: a source whose digest no longer matches the report is a stale manifest",
           1, AUDIT, audit_args(tl, stale, FIX / "silence.ledger.ok.tsv", srt, media["voice"],
                                media["final"], rec), substr="sha256")

    body = json.loads(pre.read_text(encoding="utf-8"))
    body["assets"]["rec-synth"]["source_path"] = str(root / "gone.mp4")
    gone = root / "preflight-gone.json"
    gone.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    expect("audit: a source that is no longer on disk cannot be certified",
           1, AUDIT, audit_args(tl, gone, FIX / "silence.ledger.ok.tsv", srt, media["voice"],
                                media["final"], rec), substr="不存在")

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
        test_probe_helpers_fail_closed(root, media)
        test_audit_positive(root, media, pre, srt)
        test_audit_semantic_defects(root, media, pre, srt)
        test_silence_is_measured_on_audio_not_picture(root, media, pre)
        test_audit_content_binding(root, media, pre)
        test_receipt_binding(root, media, pre)
        test_preflight_relative_paths_survive_a_cwd_change(root, media)
        test_audit_ledger_and_numeric_rejection(root, media, pre)
        test_audit_stale_manifest(root, media, pre)
    finally:
        shutil.rmtree(root, ignore_errors=True)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
