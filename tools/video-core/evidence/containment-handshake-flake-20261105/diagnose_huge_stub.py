#!/usr/bin/env python3
"""Diagnosis probe (observation only; run from tools/video-core). Runs the 'huge' stub mode of tests/test_containment.py of the tree it is started from. In ddd405b that stub publishes hello.json with a plain
open(path, "w").write(<200002 bytes>) - not the atomic tmp+replace the real worker uses; in the repaired tree it publishes atomically) through the REAL parent handshake (contained.run_contained), N times, and records for each run
how many bytes the parent's single `read(MAX_HELLO_BYTES + 1)` of hello.json returned and which refusal reason resulted. The parent is instrumented ONLY in this script (the module-level
`open` of video_core.containment.contained is shadowed by a recording wrapper); no production file is changed.

    python3 diagnose_huge_stub.py N OUT.json
"""
import json, os, sys, tempfile, time, collections
sys.dont_write_bytecode = True
sys.path.insert(0, os.getcwd())
from tests.test_containment import STUB, PY
from video_core.containment import ResourceEnvelope, contained, run_contained

N = int(sys.argv[1]); out_path = sys.argv[2]
reads = []
_real_open = open


class _Rec:
    def __init__(self, fh): self.fh = fh
    def __enter__(self): self.fh.__enter__(); return self
    def __exit__(self, *a): return self.fh.__exit__(*a)
    def read(self, n=-1):
        data = self.fh.read(n); reads.append(len(data)); return data


def rec_open(path, mode="r", *a, **k):
    fh = _real_open(path, mode, *a, **k)
    return _Rec(fh) if str(path).endswith("hello.json") and "b" in mode else fh


contained.open = rec_open
rows = collections.Counter(); samples = []
t0 = time.monotonic()
for i in range(N):
    with tempfile.TemporaryDirectory(prefix="vc-diag-") as tmp:
        stub = os.path.join(tmp, "stub_worker.py")
        with open(stub, "w") as fh:
            fh.write(STUB.replace("%MODE%", "huge").replace("%HERE%", repr(tmp)))
        reads.clear()
        contained.WORKER_PATH = stub
        r = run_contained([PY, "-c", "pass"], ResourceEnvelope(cpu_seconds=5, handshake_timeout_seconds=1.0), phase="t", timeout_s=10, stdout_limits=(4096, 4096))
        c = r.containment
        kind = "0 bytes (file created, not yet written)" if reads[:1] == [0] else ("partial (1..%d bytes)" % contained.MAX_HELLO_BYTES if reads and reads[0] <= contained.MAX_HELLO_BYTES
                                                                                   else "more than MAX_HELLO_BYTES")
        rows[(kind, c["refusal"]["reason"], c["go_sent"], c["target_may_have_started"], r.outcome)] += 1
        if reads and reads[0] <= contained.MAX_HELLO_BYTES and len(samples) < 5:
            samples.append(reads[0])
res = {"runs": N, "seconds": round(time.monotonic() - t0, 1), "python": sys.version.split()[0], "MAX_HELLO_BYTES": contained.MAX_HELLO_BYTES,
       "outcomes": [{"parent_read": k[0], "refusal_reason": k[1], "go_sent": k[2], "target_may_have_started": k[3], "outcome": k[4], "count": v} for k, v in sorted(rows.items())],
       "first_short_read_sizes": samples}
json.dump(res, open(out_path, "w"), indent=1)
print(json.dumps(res, indent=1))
