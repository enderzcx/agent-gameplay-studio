#!/usr/bin/env python3
"""Small evidence-bound journal for Agent-managed play/capture/edit runs.
No game decisions or recording engine here. Reports are attestations, not signed
proof; hashes establish content binding, not truth or human-quality output.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from state_io import local_lock, publish_json, load_json, StateIOError

SCHEMA = "gameplay-production/run-v1"
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,95}\Z")
OUTCOMES = {"won", "lost", "limit", "aborted", "error"}

class RunError(RuntimeError):
    pass

def finite(x, name):
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x):
        raise RunError(name + " must be finite")
    return float(x)

def safe_id(run_id):
    if not isinstance(run_id, str) or not ID.fullmatch(run_id):
        raise RunError("run_id must be 1-96 ASCII letters, digits, '_' or '-'")
    return run_id

def _object(path):
    def invalid(value):
        raise RunError("non-finite JSON: " + value)
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=invalid)
    except (OSError, ValueError) as e:
        raise RunError("cannot read JSON report: " + str(path)) from e
    if not isinstance(obj, dict):
        raise RunError("report must be a JSON object")
    return obj

def artifact(path):
    p = Path(path).expanduser().resolve(strict=True)
    if not p.is_file():
        raise RunError("artifact must be a regular file")
    h = hashlib.sha256()
    with p.open("rb") as src:
        a = os.fstat(src.fileno())
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            h.update(chunk)
        b = os.fstat(src.fileno())
    if (a.st_size, a.st_mtime_ns, a.st_ino) != (b.st_size, b.st_mtime_ns, b.st_ino):
        raise RunError("artifact changed while hashing")
    if a.st_size == 0:
        raise RunError("empty artifact")
    return {"path": str(p), "sha256": h.hexdigest(), "bytes": a.st_size}

def verify_artifact(item):
    if not isinstance(item, dict) or not isinstance(item.get("path"), str):
        raise RunError("bad artifact reference")
    current = artifact(item["path"])
    if current != item:
        raise RunError("artifact content changed: " + item["path"])
    return current

def read_report(path, run_id, producer):
    ref = artifact(path)
    obj = _object(ref["path"])
    if artifact(ref["path"]) != ref:
        raise RunError("report changed while reading")
    if obj.get("run_id") != run_id or obj.get("producer") != producer:
        raise RunError("report run_id/producer mismatch")
    return obj, ref

def init(root, run_id, *, game, player, mode="content"):
    safe_id(run_id)
    if mode not in ("content", "evaluation") or not game.strip() or not player.strip():
        raise RunError("game/player required; mode must be content or evaluation")
    obj = {"schema": SCHEMA, "run_id": run_id, "revision": 0, "stage": "prepared",
           "game": game, "player": player, "mode": mode, "created_at": time.time(),
           "game_result": "not_started", "capture_result": "not_started", "postproduction_result": "not_started",
           "capture_id": None, "artifacts": {}, "observations": {}, "history": [], "issues": []}
    with local_lock(root, run_id + ".lock"):
        publish_json(root, run_id + ".json", obj, create=True)
    return obj

def read(root, run_id):
    safe_id(run_id)
    obj = load_json(root, run_id + ".json")
    if not isinstance(obj, dict) or obj.get("schema") != SCHEMA or obj.get("run_id") != run_id:
        raise RunError("invalid run journal identity")
    if type(obj.get("revision")) is not int or obj["revision"] < 0:
        raise RunError("invalid revision")
    return obj

def _fresh_live_capture(r, path):
    s, ref = read_report(path, r["run_id"], "agent-capture")
    age = time.time() - finite(s.get("observed_at"), "observed_at")
    if not -2 <= age <= 15:
        raise RunError("capture status is stale: query status again immediately before play")
    if s.get("status") != "recording" or s.get("first_video_frame") is not True:
        raise RunError("capture must report a real first video frame before starting gameplay")
    if not isinstance(s.get("capture_id"), str) or not s["capture_id"]:
        raise RunError("capture_id required")
    if r["capture_id"] is not None and s["capture_id"] != r["capture_id"]:
        raise RunError("another recording cannot replace this run's capture")
    if s.get("audio_scope") not in ("none", "app", "process"):
        raise RunError("explicit supported audio scope required")
    return s, ref

def change(root, run_id, revision, action, report=None, *, outcome=None, note=""):
    safe_id(run_id)
    if type(revision) is not int or revision < 0:
        raise RunError("expected revision must be a non-negative integer")
    with local_lock(root, run_id + ".lock"):
        r = read(root, run_id)
        if revision != r["revision"]:
            raise RunError("revision conflict; reread the run before retrying")
        before = r["stage"]
        if action == "record-ready":
            if before != "prepared":
                raise RunError("record-ready requires prepared")
            s, ref = _fresh_live_capture(r, report)
            r["capture_id"] = s["capture_id"]
            r["observations"]["capture_start"] = {"source_sha256": ref["sha256"], "snapshot": s}
            r["capture_result"] = "recording"
            r["stage"] = "recording"
        elif action == "play-start":
            if before != "recording":
                raise RunError("play-start requires an established recording")
            s, ref = _fresh_live_capture(r, report)
            r["observations"]["capture_before_play"] = {"source_sha256": ref["sha256"], "snapshot": s}
            r["game_result"] = "running"
            r["stage"] = "playing"
        elif action == "game-finish":
            if before != "playing" or outcome not in OUTCOMES:
                raise RunError("game-finish requires playing and an explicit outcome")
            s, ref = read_report(report, run_id, "game-adapter")
            if s.get("outcome") != outcome:
                raise RunError("game outcome does not match the adapter report")
            raw = s.get("evidence")
            if not isinstance(raw, list) or not raw:
                raise RunError("game result must cite non-empty raw evidence")
            for item in raw:
                verify_artifact(item)
            r["artifacts"]["game_report"] = ref
            r["artifacts"]["game_evidence"] = raw
            r["game_result"] = outcome
            r["stage"] = "closing"
        elif action == "capture-finish":
            if before not in ("recording", "playing", "closing"):
                raise RunError("capture-finish requires an active recording stage")
            s, ref = read_report(report, run_id, "agent-capture")
            if s.get("capture_id") != r["capture_id"] or s.get("status") not in ("stopped", "failed"):
                raise RunError("capture identity or terminal status mismatch")
            media = s.get("media")
            if not isinstance(media, list) or not media:
                raise RunError("no recording artifact; keep run blocked, do not invent a video")
            for item in media:
                verify_artifact(item)
            checks = s.get("checks")
            if not isinstance(checks, dict) or checks.get("container_readable") is not True:
                raise RunError("capture container has not been verified readable")
            complete = (s["status"] == "stopped" and checks.get("video_continuity") is True
                        and s.get("unexpected_stop") is False and before == "closing")
            r["artifacts"]["capture_final"] = ref
            r["artifacts"]["recordings"] = media
            r["capture_result"] = "complete" if complete else "partial"
            if before in ("recording", "playing"):
                r["game_result"] = "interrupted" if before == "playing" else "not_started"
            r["stage"] = "captured"
        elif action == "edit-start":
            # `needs_review` 也必须能续后期：一旦交付被判 needs_review
            # （审片未过、或模型语义/听感结论不确定），修片应当是**在同一个 run 上继续**，
            # 而不是重开一局、更不是重玩一遍。若这里只允许 captured/editing，
            # 后期一失败整条 run 就死了，只剩"造假"或"重录"两条路 —— 两者都不可接受。
            if before not in ("captured", "editing", "needs_review"):
                raise RunError("edit-start requires sealed capture artifacts")
            # 续后期前必须确认**原片没被动过**：换掉源片就不再是同一次录制了
            for item in r["artifacts"].get("recordings", []):
                verify_artifact(item)
            r["postproduction_result"] = "running"
            r["stage"] = "editing"
        elif action == "deliver":
            if before != "editing":
                raise RunError("deliver requires editing")
            s, ref = read_report(report, run_id, "gameplay-postproduction")
            original = r["artifacts"]["recordings"]
            for item in original:
                verify_artifact(item)
            if s.get("source_sha256") != [x["sha256"] for x in original]:
                raise RunError("postproduction used a different source recording")
            final = verify_artifact(s.get("final"))
            reviews = s.get("review_evidence")
            if not isinstance(reviews, list) or not reviews:
                raise RunError("delivery must include review evidence, even when blocked")
            for item in reviews:
                verify_artifact(item)
            status = s.get("status")
            if status not in ("ready", "needs_review", "blocked"):
                raise RunError("invalid postproduction status")
            if status == "ready" and (type(s.get("ready_exit_code")) is not int or s.get("ready_exit_code") != 0 or
                                      s.get("unresolved_issues") != [] or s.get("audio_review") != "passed" or
                                      s.get("picture_review") != "passed"):
                raise RunError("ready requires recorded checks; unknown is not a pass")
            # **保留历史成片**：同一 run 上重试后期时，旧成片不能被静默覆盖 ——
            # 否则"这次交付的是哪一版"就无从追溯。当前版放 final，
            # 被替换下来的进 final_history。
            #
            # 注意状态要从 `final_result` 取，**不能**读 r["postproduction_result"]：
            # `edit-start` 已经把它重置成 "running" 了，读它会记成 running，
            # 把"上一版其实是 needs_review"这个事实丢掉。
            prev = r["artifacts"].get("final")
            if isinstance(prev, dict):
                r["artifacts"].setdefault("final_history", []).append({
                    "final": prev,
                    "postproduction_result": r["artifacts"].get("final_result"),
                    "postproduction_report_sha256": (r["artifacts"].get("postproduction_report") or {}).get("sha256"),
                    "replaced_at": time.time(),
                })
            r["artifacts"]["postproduction_report"] = ref
            r["artifacts"]["final"] = final
            # 状态与成片**绑在一起**存，避免下次重试时被 edit-start 抹掉
            r["artifacts"]["final_result"] = status
            r["postproduction_result"] = status
            r["stage"] = "delivered" if status == "ready" and r["capture_result"] == "complete" else "needs_review"
        elif action == "issue":
            if not note.strip():
                raise RunError("issue requires a concrete note")
            r["issues"].append({"at": time.time(), "stage": before, "note": note})
        else:
            raise RunError("unknown action")
        r["revision"] += 1
        r["history"].append({"revision": r["revision"], "at": time.time(), "action": action,
                             "from": before, "to": r["stage"]})
        publish_json(root, run_id + ".json", r)
        return r

def handoff(root, run_id):
    r = read(root, run_id)
    if r["stage"] not in ("captured", "editing", "delivered", "needs_review"):
        raise RunError("recording is not sealed for editing")
    for item in r["artifacts"]["recordings"]:
        verify_artifact(item)
    for item in r["artifacts"].get("game_evidence", []):
        verify_artifact(item)
    return {"schema": "gameplay-production/handoff-v1", "run_id": run_id,
            "game": r["game"], "player": r["player"], "mode": r["mode"],
            "game_result": r["game_result"], "capture_result": r["capture_result"],
            "artifacts": r["artifacts"], "observations": r["observations"], "issues": r["issues"],
            "instructions": "Use gameplay-postproduction. Preserve source media and player identity. "
                            "No postproduction advice may flow back to a competitive game run. "
                            "Unrecorded player reasons remain unknown; hindsight must be labelled.",
            "not_proof_of": ["correct scoring", "human-quality commentary", "truth of source annotations"]}

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, required=True); p.add_argument("--run-id", required=True)
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("init")
    a.add_argument("--game", required=True); a.add_argument("--player", required=True)
    a.add_argument("--mode", choices=("content", "evaluation"), default="content")
    sub.add_parser("status"); sub.add_parser("handoff")
    for name in ("record-ready", "play-start", "game-finish", "capture-finish", "edit-start", "deliver", "issue"):
        q = sub.add_parser(name); q.add_argument("--revision", type=int, required=True)
        if name not in ("edit-start", "issue"): q.add_argument("--report", type=Path, required=True)
        if name == "game-finish": q.add_argument("--outcome", choices=sorted(OUTCOMES), required=True)
        if name == "issue": q.add_argument("--note", required=True)
    a = p.parse_args(argv)
    try:
        if a.command == "init": result = init(a.root, a.run_id, game=a.game, player=a.player, mode=a.mode)
        elif a.command == "status": result = read(a.root, a.run_id)
        elif a.command == "handoff": result = handoff(a.root, a.run_id)
        else: result = change(a.root, a.run_id, a.revision, a.command, getattr(a, "report", None),
                              outcome=getattr(a, "outcome", None), note=getattr(a, "note", ""))
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except (RunError, StateIOError, OSError, ValueError, TypeError, KeyError) as e:
        print(json.dumps({"ok": False, "error": type(e).__name__, "message": str(e)}, ensure_ascii=False))
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
