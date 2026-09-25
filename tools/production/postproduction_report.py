#!/usr/bin/env python3
"""postproduction_report — 生成 gameplay-production 认的后期 report。

## 它解决的问题

`production_run.py` 的 `deliver` 读 report 里的 `status` / `ready_exit_code` /
`audio_review` / `picture_review`。如果这些能**手填**，"delivered"就只是个说法。

## 这一版的收紧（Cloud 复审：`--ready-cmd true` + 空 verdict 曾能给任意片 ready）

| 旧写法的问题 | 现在 |
|---|---|
| `--ready-cmd <任意命令>`，`true` 就能过 | **不接受任意命令**。必须给 `--checker` 指向本项目的 `check_postproduction.py`，由本工具**自己构造**并执行 `ready`；`--final-mp4` 直接来自 `--final`，**不给调用方另外指定的机会** |
| 审听/审看 evidence 只要 `{"verdict":"passed"}` | 信封必须含 **`run_id` + 实际审的媒体的 `path`/`sha256` + `verdict` + `issues`**，且 `sha256` 必须等于**本次 final** —— 这样"旧报告配新片"当场被拒 |
| 那两份 evidence 不进 `review_evidence` | 两份信封连同 `--review` 一起**归档进 `review_evidence`** |
| 跑完 ready 不再核对 | ready 跑完**重新核** final / source / review 摘要，任何一个变了就拒绝 |
| 直接写 `--out` | **原子写 + 默认不覆盖**（要覆盖须显式 `--overwrite`） |

## 边界（别读成签名）

未签名的自证记录：哈希只建立**内容绑定**，不证明内容为真、不证明听感/语义正确、
不证明评分正确。`production_run` 也只把它当 attestation。**不扩展签名体系。**
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

VALID_VERDICTS = ("passed", "failed", "unknown")
CHECKER_NAME = "check_postproduction.py"
CHECKER_MARKER = "gameplay-postproduction"


class ReportError(RuntimeError):
    pass


def artifact(path) -> dict:
    """实算 sha256 的产物引用（与 production_run.artifact 同形）。"""
    p = Path(path).expanduser().resolve(strict=True)
    if not p.is_file():
        raise ReportError(f"产物必须是普通文件: {p}")
    h = hashlib.sha256()
    with p.open("rb") as fh:
        a = os.fstat(fh.fileno())
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
        b = os.fstat(fh.fileno())
    if (a.st_size, a.st_mtime_ns, a.st_ino) != (b.st_size, b.st_mtime_ns, b.st_ino):
        raise ReportError(f"产物在计算哈希时被改动: {p}")
    if a.st_size == 0:
        raise ReportError(f"产物是空文件: {p}")
    return {"path": str(p), "sha256": h.hexdigest(), "bytes": a.st_size}


def resolve_checker(path: str) -> Path:
    """确认 `--checker` 真的是本项目的 check_postproduction.py。"""
    p = Path(path).expanduser().resolve(strict=True)
    if not p.is_file() or p.name != CHECKER_NAME:
        raise ReportError(f"--checker 必须是名为 {CHECKER_NAME} 的文件：{p}")
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise ReportError(f"--checker 无法读取：{exc}")
    if CHECKER_MARKER not in text:
        raise ReportError(f"--checker 看起来不是 {CHECKER_MARKER} 的确定性检查器：{p}")
    return p


def _no_constant(v):
    raise ValueError(f"non-finite constant {v}")


def read_review_envelope(path: str, label: str, *, run_id: str, final: dict,
                         kind: str) -> dict:
    """读审听/审看信封。

    ## 为什么把"被审的对象"与"实际审的输入"分开

    早期版本要求信封里的 `media.sha256` **必须等于 final.mp4** —— 这对"成片审看"
    是对的，但用在**独立音轨审听**上就错了：审听的实际输入是音轨文件
    （`--audio` 那条 wav，或同源的黑屏副本），不是那个 MP4。
    强制它填 MP4 的哈希，等于**逼审听伪造自己的输入身份** —— 那比不填更糟。

    所以信封分两层：

    | 字段 | 含义 | 约束 |
    |---|---|---|
    | `subject_final.sha256` | 这次审的是**哪一版成片**（结论挂在谁身上） | 必须等于本次 final 的 sha256 |
    | `input_media{path,sha256}` | **实际拿去审的那个文件** | 实算哈希，进 `review_evidence`；可以是音轨、黑屏副本等 |

    音频**不许**冒充"给了我 MP4"：`kind="audio"` 时 `input_media` 按实际填，
    与 final 相同或不同都接受，但必须是**真实存在且可哈希**的文件。
    """
    try:
        obj = json.loads(Path(path).expanduser().read_text(encoding="utf-8"),
                         parse_constant=_no_constant)
    except (OSError, ValueError) as exc:
        raise ReportError(f"{label} evidence 无法读取：{path}（{exc}）")
    if not isinstance(obj, dict):
        raise ReportError(f"{label} evidence 必须是 JSON 对象")

    if obj.get("run_id") != run_id:
        raise ReportError(f"{label} evidence 的 run_id 不是本次 run："
                          f"{obj.get('run_id')!r} != {run_id!r}")

    # —— 被审的对象：必须绑定到本次 final ——
    subject = obj.get("subject_final")
    if not isinstance(subject, dict):
        raise ReportError(f"{label} evidence 必须含 subject_final{{sha256}}"
                          f"（说明这次审的是哪一版成片）")
    shash = subject.get("sha256")
    if not isinstance(shash, str) or len(shash) != 64:
        raise ReportError(f"{label} evidence 的 subject_final.sha256 必须是 64 位十六进制")
    if shash != final["sha256"]:
        raise ReportError(
            f"{label} evidence 绑定的成片不是本次 final（旧报告配新片）："
            f"subject={shash[:12]}… final={final['sha256'][:12]}…")

    # —— 实际输入：独立实体，实算哈希 ——
    inp = obj.get("input_media")
    if not isinstance(inp, dict):
        raise ReportError(f"{label} evidence 必须含 input_media{{path,sha256}}"
                          f"（实际拿去审的是哪个文件）")
    ipath, ihash = inp.get("path"), inp.get("sha256")
    if not isinstance(ipath, str) or not ipath:
        raise ReportError(f"{label} evidence 的 input_media.path 缺失")
    if not isinstance(ihash, str) or len(ihash) != 64:
        raise ReportError(f"{label} evidence 的 input_media.sha256 必须是 64 位十六进制")
    try:
        input_ref = artifact(ipath)
    except ReportError as exc:
        raise ReportError(f"{label} 的 input_media 无法核实：{exc}")
    if input_ref["sha256"] != ihash:
        raise ReportError(
            f"{label} evidence 声明的 input_media 哈希与实际文件不符："
            f"declared={ihash[:12]}… actual={input_ref['sha256'][:12]}…")

    # 成片审看：实际输入**应当**就是那个成片（或明确声明的分析副本）。
    # 这里不强制（分析副本是合理的），但把它记下来供人核对。
    input_is_final = (input_ref["path"] == Path(final["path"]).resolve())

    v = obj.get("verdict")
    if v not in VALID_VERDICTS:
        raise ReportError(f"{label} evidence 的 verdict 必须是 {VALID_VERDICTS} 之一，收到 {v!r}")
    issues = obj.get("issues")
    if not isinstance(issues, list):
        raise ReportError(f"{label} evidence 必须含 issues（数组，可为空）")

    env = dict(obj)
    env.setdefault("kind", kind)
    env["input_is_final"] = input_is_final
    return {"envelope": env, "artifact": artifact(path),
            "input_artifact": input_ref, "verdict": v, "issues": issues,
            "input_is_final": input_is_final}


def run_ready(checker: Path, *, final: dict, review_sheet: str, timeline: str,
              preflight: str, silence_ledger: str, subtitle: str, audio: str,
              receipt: str, edl: str = "", timeout: float = 1800.0):
    """用**本次 final** 构造并执行 checker 的 ready。返回 (退出码, 输出尾部, argv)。"""
    for label, p in (("review-sheet", review_sheet), ("timeline", timeline),
                     ("preflight", preflight), ("silence-ledger", silence_ledger),
                     ("subtitle", subtitle), ("audio", audio), ("receipt", receipt)):
        if not Path(p).expanduser().exists():
            raise ReportError(f"ready 输入缺失：{label}={p}")
    argv = [sys.executable, str(checker), "ready", str(Path(review_sheet).expanduser()),
            # 关键：--final-mp4 由本工具从 --final 填，不给调用方另外指定的机会
            "--final-mp4", final["path"],
            "--timeline", str(Path(timeline).expanduser()),
            "--preflight", str(Path(preflight).expanduser()),
            "--silence-ledger", str(Path(silence_ledger).expanduser()),
            "--subtitle", str(Path(subtitle).expanduser()),
            "--audio", str(Path(audio).expanduser()),
            "--receipt", str(Path(receipt).expanduser())]
    if edl:
        argv += ["--edl", str(Path(edl).expanduser())]
    cp = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    return cp.returncode, ((cp.stdout or "") + (cp.stderr or ""))[-2000:], argv


def atomic_write_new(path: Path, text: str, *, overwrite: bool) -> None:
    """原子写。

    **默认 no-clobber 用 `os.link`，不是"先 exists 再 replace"**：
    后者在并发下有个真窗口 —— 两个进程同时通过 exists 检查，后一个的 `replace`
    会把前一个刚创建的文件**覆盖掉**。`os.link` 让内核来判胜负：
    目标已存在时直接 `FileExistsError`。

    显式 `--overwrite` 才走 `os.replace`（那本来就是"我同意覆盖"）。
    """
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise ReportError(f"输出已存在，拒绝覆盖：{path}（要覆盖请显式 --overwrite）")
    fd, tmp = tempfile.mkstemp(prefix=".postreport-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if overwrite:
            os.replace(tmp, str(path))
        else:
            try:
                os.link(tmp, str(path))
            except FileExistsError:
                raise ReportError(f"输出已存在，拒绝覆盖：{path}"
                                  f"（并发下被别的进程抢先创建）")
            except OSError as exc:
                # 文件系统不支持 hardlink：退回 O_EXCL 建目标 + 写内容，
                # 仍然不覆盖已存在的（只是少了"一次性原子可见"）。
                import errno as _e
                if exc.errno not in (_e.EPERM, _e.ENOTSUP, _e.EOPNOTSUPP, _e.EXDEV, _e.EMLINK):
                    raise
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                try:
                    out = os.open(str(path), flags, 0o644)
                except FileExistsError:
                    raise ReportError(f"输出已存在，拒绝覆盖：{path}")
                with os.fdopen(out, "w", encoding="utf-8") as fh2:
                    fh2.write(text)
                    fh2.flush()
                    os.fsync(fh2.fileno())
        try:
            dfd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        except OSError:
            pass
    finally:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成后期 report（只读证据，不手填结论）")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--source", action="append", default=[], help="源片（可多次）")
    ap.add_argument("--final", required=True, help="成片")
    ap.add_argument("--review", action="append", default=[], help="额外审片证据文件")
    ap.add_argument("--checker", default="", help=f"本项目 {CHECKER_NAME} 的路径")
    for opt in ("review-sheet", "timeline", "preflight", "silence-ledger",
                "subtitle", "audio", "receipt", "edl"):
        ap.add_argument(f"--{opt}", default="")
    ap.add_argument("--audio-review-json", default="",
                    help='音轨审听信封 {run_id, media{path,sha256}, verdict, issues}')
    ap.add_argument("--picture-review-json", default="",
                    help='成片审看信封 {run_id, media{path,sha256}, verdict, issues}')
    ap.add_argument("--issues-from", default="")
    ap.add_argument("--out", required=True)
    ap.add_argument("--overwrite", action="store_true")
    a = ap.parse_args(argv)

    try:
        if not a.source:
            raise ReportError("必须显式给出 --source（源片），否则无法绑定来源")

        sources = [artifact(s) for s in a.source]
        final = artifact(a.final)

        # —— 审听/审看：必须绑到**本次 final** ——
        audio_env = read_review_envelope(a.audio_review_json, "音轨审听",
                                         run_id=a.run_id, final=final, kind="audio") \
            if a.audio_review_json else None
        picture_env = read_review_envelope(a.picture_review_json, "成片审看",
                                           run_id=a.run_id, final=final, kind="picture") \
            if a.picture_review_json else None

        reviews = [artifact(r) for r in a.review]
        for env in (audio_env, picture_env):
            if env:
                reviews.append(env["artifact"])
                # **实际被审的输入**才是审听真正听/看的那个文件，必须归档。
                # 但若它就是本次 final，则已经在别处被记录，不再重复塞一遍
                # （早期版本拿它跟**信封文件**比，结果把 cut.mp4 重复加了两次）。
                if env["input_artifact"]["sha256"] != final["sha256"]:
                    reviews.append(env["input_artifact"])
        # 去重（按 path+sha256），避免同一文件以不同名义重复出现
        _seen, _dedup = set(), []
        for x in reviews:
            k = (x["path"], x["sha256"])
            if k not in _seen:
                _seen.add(k); _dedup.append(x)
        reviews = _dedup
        if not reviews:
            raise ReportError("必须给出至少一个审片证据（--review 或审听/审看信封）")

        issues = []
        if a.issues_from:
            try:
                issues = json.loads(Path(a.issues_from).read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise ReportError(f"--issues-from 无法读取：{exc}")
            if not isinstance(issues, list):
                raise ReportError("--issues-from 必须是 JSON 数组")

        # —— ready：必须用真 checker + 真输入 ——
        ready_exit_code = None
        ready_tail = ""
        ready_argv = None
        ready_inputs = (a.checker, a.review_sheet, a.timeline, a.preflight,
                        a.silence_ledger, a.subtitle, a.audio, a.receipt)
        if all(ready_inputs):
            checker = resolve_checker(a.checker)
            ready_exit_code, ready_tail, ready_argv = run_ready(
                checker, final=final, review_sheet=a.review_sheet,
                timeline=a.timeline, preflight=a.preflight,
                silence_ledger=a.silence_ledger, subtitle=a.subtitle,
                audio=a.audio, receipt=a.receipt, edl=a.edl)
        elif a.checker:
            raise ReportError("给了 --checker 就必须同时给齐 ready 的输入："
                              "--review-sheet/--timeline/--preflight/--silence-ledger/"
                              "--subtitle/--audio/--receipt")

        # —— ready 跑完**重新核**摘要：进程中被换掉的媒体必须当场抓出来 ——
        drift = []
        for label, before in (("source", sources), ("final", [final]), ("review", reviews)):
            for b in before:
                try:
                    if artifact(b["path"])["sha256"] != b["sha256"]:
                        drift.append(f"{label} 在生成报告期间被改动：{b['path']}")
                except ReportError as exc:
                    drift.append(f"{label} 复核失败：{exc}")
        if drift:
            raise ReportError("；".join(drift))

        audio_review = audio_env["verdict"] if audio_env else "unknown"
        picture_review = picture_env["verdict"] if picture_env else "unknown"
        env_issues = (audio_env["issues"] if audio_env else []) + \
                     (picture_env["issues"] if picture_env else [])

        ready = (ready_exit_code == 0 and audio_review == "passed"
                 and picture_review == "passed" and issues == [] and env_issues == [])
        status = "ready" if ready else "needs_review"

        reasons = []
        if ready_exit_code is None:
            reasons.append("没有跑 ready（缺少 --checker 或必需输入）")
        elif ready_exit_code != 0:
            reasons.append(f"check_postproduction.py ready 退出码 {ready_exit_code}")
        if audio_review != "passed":
            reasons.append(f"音轨审听={audio_review}")
        if picture_review != "passed":
            reasons.append(f"成片审看={picture_review}")
        if issues:
            reasons.append(f"未决问题 {len(issues)} 条")
        if env_issues:
            reasons.append(f"审听/审看信封里的问题 {len(env_issues)} 条")

        report = {
            "schema": "gameplay-production/postproduction-report-v1",
            "producer": "gameplay-postproduction",
            "run_id": a.run_id,
            "generated_at": time.time(),
            "source_sha256": [s["sha256"] for s in sources],
            "final": final,
            "review_evidence": reviews,
            "status": status,
            "ready_exit_code": ready_exit_code,
            "ready_argv": ready_argv,
            "audio_review": audio_review,
            "picture_review": picture_review,
            "audio_review_envelope": (audio_env["envelope"] if audio_env else None),
            "picture_review_envelope": (picture_env["envelope"] if picture_env else None),
            "unresolved_issues": issues + env_issues,
            "ready_failure_reasons": reasons,
            "ready_output_tail": ready_tail,
            "attestation": {
                "signed": False,
                "scope": "consistency only",
                "not_proof_of": ["正确评分", "解说的人文质量", "听感/语义层面的正确性",
                                 "源注释的真实性"],
            },
        }
        atomic_write_new(Path(a.out), json.dumps(report, ensure_ascii=False,
                                                 indent=2, allow_nan=False) + "\n",
                         overwrite=a.overwrite)
        print(json.dumps({"ok": True, "out": str(Path(a.out).expanduser().resolve()),
                          "status": status, "ready_exit_code": ready_exit_code,
                          "audio_review": audio_review, "picture_review": picture_review,
                          "review_evidence": len(reviews), "reasons": reasons},
                         ensure_ascii=False, indent=2))
        return 0
    except (ReportError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"ok": False, "error": type(exc).__name__,
                          "message": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
