import sys, os, json, tempfile, subprocess, hashlib
sys.path.insert(0, "/home/user/claude/tools/video-core")
PY = sys.executable
d = tempfile.mkdtemp(dir="/tmp/claude-0/-home-user-claude/2633dc6b-3347-5caa-a7c7-bea109ee5d97/scratchpad")
# 1. surrogate key in episode through CLIs
ep = json.load(open("examples/episode.synthetic.json"))
raw = json.dumps(ep)[:-1] + ', "\\ud800": 1}'
p = os.path.join(d, "ep.json"); open(p, "w").write(raw)
for sub in ("validate", "plan"):
    r = subprocess.run([PY, "-m", "video_core", sub, p], capture_output=True, text=True, timeout=10, cwd="/home/user/claude/tools/video-core", env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    print(sub, "rc", r.returncode, "stdout", repr(r.stdout[:80]), "stderr tail", repr(r.stderr[-120:]))
# 2. audiomix manifest with surrogate key
m = {"manifest_version": 1, "timeline": {"fps": {"n": 30, "d": 1}, "total_frames": 30}, "output": {"sample_rate": 48000, "channels": 1, "bits": 16},
     "scope": {"event_ids": ["a"]}, "policy": {"gap": "ALLOW_ZERO_FILL"}, "assets": [], "events": [{"id": "a", "source_id": "s", "state": "UNKNOWN"}]}
raw = json.dumps(m)[:-1] + ', "\\ud800": 1}'
p = os.path.join(d, "m.json"); open(p, "w").write(raw)
r = subprocess.run([PY, "-m", "video_core.audiomix", "validate", p], capture_output=True, text=True, timeout=10, cwd="/home/user/claude/tools/video-core", env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
print("audiomix validate rc", r.returncode, repr(r.stdout[:80]), repr(r.stderr[-120:]))
# 3. zero-sample unselected event at 60000 fps
from video_core.audiomix.plan import prepare_plan, frame_to_sample
print([frame_to_sample(k,60000,1) for k in range(8)])
def man(ev, fps=(60000,1), total=60, scope=("a",)):
    return json.dumps({"manifest_version":1,"timeline":{"fps":{"n":fps[0],"d":fps[1]},"total_frames":total},"output":{"sample_rate":48000,"channels":1,"bits":16},
     "scope":{"event_ids":list(scope)},"policy":{"gap":"ALLOW_ZERO_FILL"},"assets":[],"events":ev}).encode()
evs=[{"id":"a","source_id":"s","state":"INTENTIONAL_SILENCE","decision_ref":"r","start_frame":0,"end_frame":10},
     {"id":"b","source_id":"s","state":"UNKNOWN","start_frame":2,"end_frame":3}]
try:
    p=prepare_plan(man(evs)); print("OK", p.status)
except Exception as e: print("EXC", type(e).__name__, [i.code for i in e.issues])
evs[1]["state"]="OMIT"; evs[1]["decision_ref"]="x"
try:
    p=prepare_plan(man(evs, scope=("a","b"))); print("OK", p.status)
except Exception as e: print("EXC", type(e).__name__, [i.code for i in e.issues])
