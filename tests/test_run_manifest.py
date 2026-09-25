"""gameplay-production 的 run_manifest 离线测试：门禁 + 串档自检 + 只重试相应阶段。"""
import json, subprocess, sys, tempfile, shutil
from pathlib import Path
SCRIPT = Path(__file__).resolve().parent.parent / "skills" / "gameplay-production" / "scripts" / "run_manifest.py"
P=F=0
def ck(n,c,d=""):
    global P,F
    if c: P+=1; print(f"\033[32m✓\033[0m {n}")
    else: F+=1; print(f"\033[31m✗\033[0m {n}  — {d}")
def run(*a):
    return subprocess.run([sys.executable,str(SCRIPT),*a],capture_output=True,text=True)
d=Path(tempfile.mkdtemp()); M=str(d/"m.json")
run("init","--manifest",M,"--run-id","r1","--game","T")
r=run("set-game","--manifest",M,"--result","pass")
ck("capture 未 pass 时拒绝记对局", r.returncode==2 and "capture" in r.stderr, r.stderr[:120])
run("set-capture","--manifest",M,"--result","pass","--media",str(d/"r1.mp4"))
r=run("set-game","--manifest",M,"--result","pass")
ck("capture=pass 后允许记对局", r.returncode==0, r.stderr[:120])
r=run("set-post","--manifest",M,"--result","pass","--cut","/tmp/other.mp4")
ck("成片文件名不含 run_id 时自检失败", run("verify","--manifest",M).returncode==2)
r=run("set-post","--manifest",M,"--result","pass","--cut",str(d/"r1-cut.mp4"),"--note","a/b 与 8/8")
v=json.loads(run("verify","--manifest",M).stdout)
ck("修正后自检通过", v["ok"] is True, str(v["problems"]))
ck("note 里的斜杠不再误报", not any("note" in p for p in v["problems"]), str(v["problems"]))
ck("后期 attempts=2 而 capture/game 仍=1（只重试相应阶段）",
   v["summary"]["attempts"]=={"capture":1,"game":1,"postproduction":2}, str(v["summary"]["attempts"]))
ck("三个结论都已知", v["summary"]["all_known"] is True)
ck("run_id 全程未变", v["summary"]["run_id"]=="r1")
r=run("set-capture","--manifest",M,"--result","bogus")
ck("非法 result 被拒", r.returncode==2)
shutil.rmtree(d,ignore_errors=True)
print(f"\n{P} passed, {F} failed")
sys.exit(0 if F==0 else 1)
