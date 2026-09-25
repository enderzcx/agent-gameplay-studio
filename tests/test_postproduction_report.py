#!/usr/bin/env python3
"""postproduction report 生成器的反例：**结论只能读，不能手填**。

Cloud 复审指出：旧版 `--ready-cmd true` + 两份只有 {"verdict":"passed"} 就能给
任意片 ready，且那两份 evidence 不进 review_evidence（容易"旧报告配新片"）。
这里逐条钉住收紧后的行为。
"""
import hashlib, json, subprocess, sys, tempfile, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "tools" / "production" / "postproduction_report.py"
REAL_CHECKER = ROOT / "skills" / "gameplay-postproduction" / "scripts" / "check_postproduction.py"
P = F = 0

def ck(n, c, d=""):
    global P, F
    if c: P += 1; print(f"\033[32m✓\033[0m {n}")
    else: F += 1; print(f"\033[31m✗\033[0m {n}  — {d}")

def run(*a):
    return subprocess.run([sys.executable, str(GEN), *a], capture_output=True, text=True)

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def base(tmp, final_name="cut.mp4"):
    (tmp/"src.mp4").write_bytes(b"source media")
    (tmp/final_name).write_bytes(b"final output media")
    (tmp/"rev.json").write_text('{"r":1}')
    return tmp/final_name

def envelope(tmp, name, run_id, subject, *, input_media=None, verdict="passed", issues=None):
    """信封：subject_final（审的是哪版成片）+ input_media（实际拿去审的文件）。"""
    inp = input_media or subject
    p = tmp/name
    p.write_text(json.dumps({"run_id": run_id,
                             "subject_final": {"sha256": sha(subject)},
                             "input_media": {"path": str(Path(inp).resolve()), "sha256": sha(inp)},
                             "verdict": verdict, "issues": issues or []}))
    return p

def args(tmp, final, *, audio=None, picture=None, checker=None, ready=None, out="rep.json", extra=()):
    a = ["--run-id","r1","--source",str(tmp/"src.mp4"),"--final",str(final),
         "--review",str(tmp/"rev.json"),"--out",str(tmp/out)]
    if audio: a += ["--audio-review-json", str(audio)]
    if picture: a += ["--picture-review-json", str(picture)]
    if checker: a += ["--checker", str(checker)]
    a += list(extra); a += list(ready or [])
    return a

# —— 正常路径：真 checker 的 ready 输入不全 → 不可能 ready ——
t = Path(tempfile.mkdtemp()); final = base(t)
ae = envelope(t,"ae.json","r1",final); pe = envelope(t,"pe.json","r1",final)
o = json.loads((t/"rep.json").read_text()) if run(*args(t,final,audio=ae,picture=pe)).returncode==0 else None
ck("没有真 checker/ready 输入 → needs_review", o and o["status"]=="needs_review", str(o and o.get("ready_failure_reasons")))
ck("ready_exit_code 为 None（没跑就只能是 None）", o and o["ready_exit_code"] is None)
ck("审听/审看信封归档进 review_evidence", o and len(o["review_evidence"])==3, str(o and len(o["review_evidence"])))

# —— 核心反例：旧报告配新片 ——
t2 = Path(tempfile.mkdtemp()); f2 = base(t2)
old_env = envelope(t2,"old.json","r1",f2)
Path(f2).write_bytes(b"a DIFFERENT final now")     # 换片
r = run(*args(t2,f2,audio=old_env,picture=old_env))
ck("旧报告配新片被拒（信封 hash != 本次 final）", r.returncode==2 and "旧报告配新片" in r.stdout, r.stdout[:160])

# —— 反例：裸 verdict 信封（缺 run_id/media/issues）——
t3 = Path(tempfile.mkdtemp()); f3 = base(t3)
bare = t3/"bare.json"; bare.write_text('{"verdict":"passed"}')
r3 = run(*args(t3,f3,audio=bare,picture=bare))
ck("裸 verdict 信封被拒", r3.returncode==2, r3.stdout[:140])

# —— 反例：run_id 不匹配 ——
t4 = Path(tempfile.mkdtemp()); f4 = base(t4)
wrong = envelope(t4,"w.json","OTHER-RUN",f4)
r4 = run(*args(t4,f4,audio=wrong,picture=wrong))
ck("信封 run_id 不匹配被拒", r4.returncode==2 and "run_id" in r4.stdout, r4.stdout[:140])

# —— 反例：任意命令当 ready（旧洞）已被接口移除 ——
t5 = Path(tempfile.mkdtemp()); f5 = base(t5)
a5 = envelope(t5,"a.json","r1",f5); p5 = envelope(t5,"p.json","r1",f5)
r5 = run(*args(t5,f5,audio=a5,picture=p5,checker="/bin/sh"))
ck("--checker 指向非本 checker 被拒", r5.returncode==2 and "check" in r5.stdout.lower(), r5.stdout[:160])

# —— 反例：给了 checker 但 ready 输入不齐 ——
t6 = Path(tempfile.mkdtemp()); f6 = base(t6)
a6 = envelope(t6,"a.json","r1",f6); p6 = envelope(t6,"p.json","r1",f6)
r6 = run(*args(t6,f6,audio=a6,picture=p6,checker=REAL_CHECKER))
ck("给 checker 但不给齐 ready 输入被拒", r6.returncode==2 and "ready 的输入" in r6.stdout, r6.stdout[:160])

# —— 反例：进程中被改媒体（源片在 ready 前后被改）——
t7 = Path(tempfile.mkdtemp()); f7 = base(t7)
a7 = envelope(t7,"a.json","r1",f7); p7 = envelope(t7,"p.json","r1",f7)
# 用一个"跑的时候改源片"的假 checker 不行（--checker 被绑定为真 checker），
# 所以直接验证：源片 hash 在生成前被改 → 报告里的 source_sha256 是**当时**的值
o7 = json.loads((t7/"rep.json").read_text()) if run(*args(t7,f7,audio=a7,picture=p7)).returncode==0 else None
ck("report 记录源片真实 hash", o7 and o7["source_sha256"][0] == sha(t7/"src.mp4"))

# —— 反例：输出已存在不覆盖 ——
t8 = Path(tempfile.mkdtemp()); f8 = base(t8)
a8 = envelope(t8,"a.json","r1",f8); p8 = envelope(t8,"p.json","r1",f8)
(t8/"rep.json").write_text("{}")
r8 = run(*args(t8,f8,audio=a8,picture=p8))
ck("输出已存在 → 拒绝覆盖", r8.returncode==2 and "拒绝覆盖" in r8.stdout, r8.stdout[:140])
r8b = run(*args(t8,f8,audio=a8,picture=p8,extra=("--overwrite",)))
ck("显式 --overwrite 才允许", r8b.returncode==0, r8b.stdout[:140])

# —— 语义边界：明说是未签名 attestation ——
o9 = json.loads((t/"rep.json").read_text())
ck("标注为未签名、仅一致性", o9["attestation"]["signed"] is False and o9["attestation"]["scope"]=="consistency only")
ck("列出不证明什么", bool(o9["attestation"]["not_proof_of"]))

# —— Cloud 反例 A：独立音轨审听，实际输入是 wav（不是 MP4）——
tA = Path(tempfile.mkdtemp()); fA = base(tA)
(tA/"audio-track.wav").write_bytes(b"narrator audio track, NOT the mp4")
aeA = envelope(tA,"ae.json","r1",fA, input_media=tA/"audio-track.wav")
peA = envelope(tA,"pe.json","r1",fA)
rA = run(*args(tA,fA,audio=aeA,picture=peA))
ck("音轨审听的实际输入是 wav 时不被迫伪造 MP4 身份", rA.returncode==0, rA.stdout[:200])
if rA.returncode==0:
    oA = json.loads((tA/"rep.json").read_text())
    ev = [x["sha256"] for x in oA["review_evidence"]]
    ck("被审的 wav 也进了 review_evidence", sha(tA/"audio-track.wav") in ev, str(len(ev)))
    ck("subject_final 仍绑定本次成片",
       oA["audio_review_envelope"]["subject_final"]["sha256"] == sha(fA))
    ck("如实记录 input 是否就是 final",
       oA["audio_review_envelope"]["input_is_final"] is False)

# —— Cloud 反例 A2：input_media 声明的哈希与实际文件不符 ——
tA2 = Path(tempfile.mkdtemp()); fA2 = base(tA2)
(tA2/"a.wav").write_bytes(b"real audio")
bad = tA2/"bad.json"
bad.write_text(json.dumps({"run_id":"r1","subject_final":{"sha256":sha(fA2)},
    "input_media":{"path":str((tA2/"a.wav").resolve()),"sha256":"0"*64},
    "verdict":"passed","issues":[]}))
rA2 = run(*args(tA2,fA2,audio=bad,picture=envelope(tA2,"p.json","r1",fA2)))
ck("input_media 哈希与实际文件不符被拒",
   rA2.returncode==2 and "input_media" in rA2.stdout, rA2.stdout[:160])

# —— Cloud 反例 A3：subject_final 不绑定本次成片（旧报告配新片）——
tA3 = Path(tempfile.mkdtemp()); fA3 = base(tA3)
other = tA3/"other.mp4"; other.write_bytes(b"some OTHER final")
rA3 = run(*args(tA3,fA3,audio=envelope(tA3,"o.json","r1",other),
                picture=envelope(tA3,"p.json","r1",fA3, input_media=fA3)))
ck("subject_final 不是本次成片被拒（旧报告配新片）",
   rA3.returncode==2 and "旧报告配新片" in rA3.stdout, rA3.stdout[:160])

# —— Cloud 反例 B：并发 no-clobber（exists 之后、replace 之前被别人创建）——
tB = Path(tempfile.mkdtemp()); fB = base(tB)
aeB = envelope(tB,"a.json","r1",fB); peB = envelope(tB,"p.json","r1",fB)
import subprocess as _sp, threading as _th
outB = tB/"race.json"
res = {}
def _mk():
    (tB/"src.mp4").write_bytes(b"source media")
    r = run(*args(tB,fB,audio=aeB,picture=peB,out="race.json"))
    res["rc"] = r.returncode; res["out"] = r.stdout
th = _th.Thread(target=_mk); th.start()
# 抢在它之前创建目标文件
import time as _t
for _ in range(2000):
    if not outB.exists():
        try:
            outB.write_text('{"pre-existing":"created concurrently"}'); break
        except FileExistsError:
            break
th.join()
ck("并发创建的目标文件不被覆盖（os.link no-clobber）",
   outB.read_text() == '{"pre-existing":"created concurrently"}' or res.get("rc") == 2,
   "target=" + outB.read_text()[:40] + " rc=" + str(res.get("rc")))

for d in (t,t2,t3,t4,t5,t6,t7,t8,tA,tA2,tA3,tB): shutil.rmtree(d, ignore_errors=True)
print(f"\n{P} passed, {F} failed")
sys.exit(0 if F == 0 else 1)
