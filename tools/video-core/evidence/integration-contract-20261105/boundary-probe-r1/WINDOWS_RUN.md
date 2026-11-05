# Native Windows run - one command set, one receipt

Needs: Python 3.11+, `git`, a clone of `alignlabai-rgb/claude` with branch `codex/video-core-integration-20261105` fetched at the commit named in the hand-off (it holds `portable-r2/` and `boundary-probe-r1/`).
Scratch and output directories must be NEW/existing directories OUTSIDE the clone. Nothing is written to the clone. No network, privilege, symlink, registry or browser action is made; the probe never passes `--render|--mix|--mux`.

PowerShell (replace the paths):

```powershell
$R = "C:\work\claude"                                   # the clone
$P = "$R\tools\video-core\evidence\integration-contract-20261105\boundary-probe-r1"
New-Item -ItemType Directory C:\work\bp-scratch | Out-Null
python $P\export_sources.py --repo $R --dest C:\work\bp-src
python $P\boundary_probe.py --source "b4ca4a3=C:\work\bp-src\b4ca4a3\tools\video-core" --source "092b420=C:\work\bp-src\092b420\tools\video-core" --source "91ec2d4=C:\work\bp-src\91ec2d4\tools\video-core" --scratch-root C:\work\bp-scratch --out-dir C:\work\bp-out-win1 --normalize-stored-paths
python $P\make_receipt.py --out-dir C:\work\bp-out-win1 --write
```

Return `C:\work\bp-out-win1\RECEIPT.txt`, `RECEIPT.json`, `run.json`, `ATTRIBUTION.json` (small). Exit code 0 = completed, 5 = the deny-control admission failed on that machine (nothing else ran - report it, do not work around it).

Runtime-only copy (no clone): copy `boundary_probe.py`, `attribution.py`, `make_receipt.py`, `profiles\pin_*.json` and, from `portable-r2\`, `portable_core.py portable_cases.py portable_selftest.py child_runner.py json_equiv.py candidate_fixtures.py`; pass `--harness-dir <that portable-r2 folder>` and `--profiles-dir`; the three source trees are then exported elsewhere with the same `git archive <sha> tools/video-core`.

What to expect, and what not to conclude:

* On Windows `E1_non_utf8_root_bytes_posix` and `E3_symlink_to_raw_root` must report **NOT_APPLICABLE** (6 + 1 checks per source) with the reason "no OS evidence"; a raw-byte directory is not created and no symlink is attempted. If either case shows MET/NOT_MET on Windows, that is a defect of the probe - report it.
* `E2` (a root argument `...caf\udce9` that does not exist) and the K/Z/R/J/S cases do run. Whatever they show on Windows is the first Windows observation of these boundaries; it is not predicted here. A pin status other than `PINNED_EXACT` (for example `PINNED_MODULO_LINE_ENDINGS` after CRLF conversion) is a label, not an equivalence claim.
* The Linux receipts to diff against: `results/py3.13/RECEIPT.txt` (3.11 and 3.12 identical in outcomes). Windows is expected to differ at least in NOT_APPLICABLE counts, probably in E2 observations, and possibly in J1 console-independent bytes only if a library behaves differently - none of these differences is judged in advance.
* No Windows execution is claimed anywhere in this directory.
