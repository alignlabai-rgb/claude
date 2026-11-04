"""POSITIVE CONTROL for the instrument (harness-owned, run only through harness_runner.py).

It deliberately does the three things the contract tests assert never happen in a default plan path, so that "zero events" can be told apart from
"the instrument cannot see": it launches a process, makes a directory, writes a file and reads one file outside the world/library/stdlib. The process it launches is the harness's own sentinel stub
named `ffmpeg` on the world's PATH (it exits 97 and appends one line to sentinel.log) — never a real ffmpeg.
"""
import os
import subprocess
import sys

out = os.environ["VCC_OUT"]
subprocess.run(["ffmpeg", "-version"], check=False, capture_output=True)
os.mkdir(os.path.join(out, "control-made"))
with open(os.path.join(out, "control-made", "f.txt"), "w") as fh:
    fh.write("x")
with open(sys.executable, "rb") as fh:       # a read outside the world / library / stdlib: must be reported as a foreign read
    fh.read(1)
