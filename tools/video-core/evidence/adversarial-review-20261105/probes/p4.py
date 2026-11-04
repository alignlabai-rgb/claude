import sys, os, json, time, signal, threading
sys.path.insert(0, "/home/user/claude/tools/video-core")
from video_core import runner
PY = sys.executable
# A: child writes 2 MB to stdout fast then exits 0; drain limit 1000
r = runner.run_bounded([PY, "-c", "import sys; sys.stdout.buffer.write(b'x'*2000000)"], phase="t", timeout_s=8, stdout_limits=(10,10), max_drain_bytes=1000)
print("A outcome", r.outcome, "ok", r.ok, "total", r.stdout.total, "kill_reason", r.kill_reason)
# B: same, but child lingers 1s so the flood is noticed
r = runner.run_bounded([PY, "-c", "import sys,time; sys.stdout.buffer.write(b'x'*2000000); sys.stdout.flush(); time.sleep(1)"], phase="t", timeout_s=8, stdout_limits=(10,10), max_drain_bytes=1000)
print("B outcome", r.outcome, "ok", r.ok, "total", r.stdout.total, "kill_reason", r.kill_reason)
