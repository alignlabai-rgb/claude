import sys, os, time, signal, threading, subprocess, json
sys.path.insert(0, "/home/user/claude/tools/video-core")
from video_core import runner
PY = sys.executable
marker = sys.argv[1]
child = r'''
import subprocess, sys, time, os
g = subprocess.Popen([sys.executable, "-c", "import os,time; open(%r,'w').write(str(os.getpid())); time.sleep(4)"])
time.sleep(0.3)
'''  % marker
threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGINT)).start()
t0=time.time()
try:
    r = runner.run_bounded([PY, "-c", child], phase="t", timeout_s=8, drain_timeout_s=3.0, grace_s=1.0)
    print("returned", r.outcome, r.drain_complete, r.orphan_group_killed)
except KeyboardInterrupt:
    print("KeyboardInterrupt escaped run_bounded after", round(time.time()-t0,2))
pid=int(open(marker).read())
try:
    os.kill(pid, 0); print("grandchild", pid, "STILL ALIVE after run_bounded unwound")
except ProcessLookupError: print("grandchild gone")
time.sleep(4.5)
