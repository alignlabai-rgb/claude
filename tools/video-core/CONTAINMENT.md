# Optional resource containment for owned child executions (`video_core.containment`)

Addresses the documented gap "FFmpeg has no memory or CPU cap" — nothing more. It is **opt-in per call**: nothing is limited unless a caller passes a
`ResourceEnvelope` (API) or an `--envelope-*` flag (CLI). Existing defaults, plans, plan hashes (the baseline `4bc20b3f…` is pinned by a test), media routes and APIs are
unchanged when no envelope is given. It is not OS repair, not a sandbox, and not a claim about any connectivity or provider issue.

## Hard constraints this design keeps

* Limits are applied only to a **new** worker process this package starts, and inherited by the target that worker starts. Never the calling Python process, an
  existing process, the agent/application, or any unrelated process. `tests/test_containment.py` asserts the caller's own `RLIMIT_*` are unchanged after every run.
* No import-time native mutation: importing `video_core.containment` calls no native API (tested in a fresh interpreter). The only code that touches `resource`/`ctypes` is
  `video_core/containment/_worker.py`, only inside functions; static tests enforce that, plus: no shell, no name-based or broad killing, no process enumeration,
  no `OpenProcess`/`OpenJobObject`/`DuplicateHandle`/breakaway flags, exactly one `AssignProcessToJobObject(job, me)` where `me = GetCurrentProcess()` of the worker.
* **Fail closed**: if any requested limit cannot be applied exactly (or stricter), the target is **not started**; there is no retry and no weaker second attempt. Refusals are
  `CONTAINMENT_REFUSED` (runner outcome / receipt status) with the reason.
* Strict finite numerics (shared `numeric.py`): no bool, no NaN/inf, no float for integer limits, documented floors/ceilings; an envelope that requests nothing is rejected.
* Wall time and output draining stay with the bounded runner (timeout, grace, drain, flood kill, own process group), including the handshake: the handshake time counts toward the target's wall budget.

## API

```python
from video_core.containment import ResourceEnvelope, run_contained
env = ResourceEnvelope(cpu_seconds=60, memory_bytes=3 * 2**30, max_file_bytes=64 * 2**20)      # all optional; at least one required
res = run_contained([ffmpeg, ...], env, phase="render", timeout_s=600)                         # -> runner.RunResult with .containment
render(episode_bytes, ..., envelope=env)            # envelope applies to the main ffmpeg process only
avmux.mux.mux(manifest_bytes, ..., envelope=env)    # likewise (not to ffprobe / readback decodes)
```
CLI (explicit, render / `avmux mux` only): `--envelope-cpu-seconds --envelope-memory-bytes --envelope-max-file-bytes --envelope-tree-cpu-seconds --envelope-tree-memory-bytes --envelope-max-processes --envelope-handshake-timeout-seconds`.
Without `--render`/`--mux` they only produce a dry-run report of what this platform would support; nothing is applied.
A limit the platform cannot apply is rejected **before anything is created or any tool probed** (`envelope_unsupported`, exit 1); bad values are usage errors (exit 2).

| limit | POSIX (verified on the author's Linux host) | Windows (written, **unverified on any Windows host by the author**) |
|---|---|---|
| `cpu_seconds` | `RLIMIT_CPU` soft=hard, per process | `PerProcessUserTimeLimit` (user-mode time), per process in the job |
| `memory_bytes` (≥ 64 MiB) | `RLIMIT_AS` soft=hard: virtual **address space** of each process (not RSS) | `ProcessMemoryLimit`: committed memory of each process in the job |
| `max_file_bytes` | `RLIMIT_FSIZE` | unsupported → refused |
| `tree_cpu_seconds` | unsupported → refused (no aggregate accounting) | `PerJobUserTimeLimit` |
| `tree_memory_bytes` | unsupported → refused | `JobMemoryLimit` |
| `max_processes` (≥ 2) | unsupported → refused (`RLIMIT_NPROC` is per real user, not per tree: could affect unrelated processes) | `ActiveProcessLimit` (counts the worker itself) |

Unix per-process RLIMITs do **not** give aggregate tree accounting; children get their own counters.

## Mechanism

1. Parent (`contained.py`): validates, statically refuses unsupported limits (no process started), makes an owned private handshake directory (`mkdtemp`, 0700 on POSIX) and a random token.
2. Parent starts the worker through the bounded runner: `python -I -S _worker.py CONFIG -- TARGET…` (own session/process group, drained pipes, timeout). `_worker.py` is self-contained stdlib.
3. Worker applies the limits to **itself**, then **reads them back from the OS** and writes `hello.json` (requested, applied-from-read-back, unsupported, errors). `applied` is never what the worker believes it set.
4. Parent (bounded poll, ≤ 64 KiB, token check) accepts only if the read-back names exactly the requested limits, each a positive int ≤ the request (an inherited *stricter* limit is accepted and noted, never raised), with nothing unsupported/failed. Only then it writes `go`; any deviation writes `abort`, the runner terminates the worker, and the target never runs.
5. After `go`: **POSIX** the worker `execv`s the target — the target *is* that process, limits inherited (tests show the target reporting the limits itself and `worker_pid == target pid`). **Windows** the worker starts the target as its child, which inherits the job.
6. Handshake directory is always removed. Nothing is retried.

POSIX order matters: `RLIMIT_AS` is applied last because it constrains the worker too. Measured on the author's host: a bare `python -I -S` worker reserves ≈ 36 MB of address space and fails with `MemoryError` under a 32 MiB cap — hence the 64 MiB floor.

### Windows job ownership and lifetime (design; see "unverified" below)

The worker creates a fresh, **unnamed** Job Object (`CreateJobObjectW(NULL, NULL)`, default non-inheritable handle), sets `JOBOBJECT_EXTENDED_LIMIT_INFORMATION` with `KILL_ON_JOB_CLOSE` plus the requested limits,
and assigns **only itself** (`AssignProcessToJobObject(job, GetCurrentProcess())`) **before** it starts the target, so the target and its descendants are created inside the job (no breakaway flag is ever passed).
The handle is kept in a module global for the worker's whole life and never duplicated or closed explicitly; the target does not inherit it (`close_fds`, non-inheritable). When the worker exits — normally, or because the runner terminated it on timeout/cancel — the last handle closes and the OS
kills whatever is still in the job (which also gives Windows the process-tree cleanup that a process group gives POSIX). If the worker already sits in an inherited (sandbox) job, the code does not look for, open, modify or break away from that job: `IsProcessInJob` is only recorded
(`inherited_job_present`), and if assigning into a nested job is refused (typically `ERROR_ACCESS_DENIED`) the result is an explicit `unsupported` / `CONTAINMENT_REFUSED` before the target starts. Limits of any enclosing job still apply in addition and may be stricter than the request; the read-back reports this job's own values.

## Receipt (`containment` on the RunResult / execution entry / render and mux receipts)

`requested`, `applied` (from read-back), `unsupported`, `refusal` (reason + detail), `worker_started`, `go_sent`, `target_may_have_started`, `handshake_seconds`, `platform`, `mechanism`, `worker_pid`, `inherited_job_present`, `notes`, `also_in_force`, `not_contained`, `applied_to`, `retry: "none…"`,
and, when a signal ended the target, a `signal_hint` stating it is a hint, not a proof (observed on the author's host: a busy target under `cpu_seconds=1` was killed by `SIGKILL` at ≈ 1.0 s). The receipt shows the **target** command, not the worker invocation.

## Sources, and what could and could not be checked

Requested primary references: Microsoft Learn — Job Objects (`https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects`) and `SetInformationJobObject`
(`https://learn.microsoft.com/en-us/windows/win32/api/jobapi2/nf-jobapi2-setinformationjobobject`); Python `resource` (`https://docs.python.org/3/library/resource.html`).
**In the author's session these pages could not be read**: the cloud environment's network policy denied `learn.microsoft.com` and `docs.python.org` (DNS `ENOTFOUND`, reported by the fetch tool; the environment's own documentation says the hosts must be allowed in the environment's network settings). No network setting was changed and nothing was substituted.
Therefore:
* **Windows** structure layouts, flag values, nested-job behaviour, handle inheritance and kill-on-close semantics in `_worker.py` are written from the author's prior knowledge and are **not verified against primary documentation or on a Windows host**. Guards that do not depend on that knowledge: applied values are taken only from `QueryInformationJobObject` read-back (a wrong flag constant or layout makes the read-back disagree and the run is refused); a layout test pins the 64-bit sizes (64/48/144 bytes) and field offsets, which is a layout check, not Windows evidence; Windows tests exist and are skipped here.
  Please verify against the Microsoft pages above, in particular nested-job rules (`AssignProcessToJobObject`), `JobObjectExtendedLimitInformation` numbering and per-process vs per-job limit semantics.
* **POSIX** behaviour was checked empirically on the author's host (Linux 6.18, Python 3.11–3.13): `RLIMIT_CPU/AS/FSIZE` applied by `setrlimit`, inherited across `exec`, read back by the target itself; raising a lowered hard limit raises `ValueError` ("not allowed to raise maximum limit"), which is why the worker never raises limits. The `resource` documentation page itself was not read.

## What remains uncontained / limitations

wall time is the runner's job, not the envelope's; POSIX has no tree-wide CPU/memory; `RLIMIT_AS` is address space, not resident memory, and may be too coarse or too strict for some programs (ffmpeg here reserved ≈ 0.6 GB of address space for a tiny encode, so caps must be generous); a POSIX descendant that creates its own session escapes group termination;
only the main ffmpeg render/mux process runs inside an envelope (ffprobe and readback decodes do not); no cgroups, namespaces, seccomp, network or filesystem isolation; the handshake directory lives in the system temp directory; a hostile target running as the same user could still read it (it holds only a token and limit numbers);
Windows is untested by the author. The inherited-stricter-limit case is accepted, never raised. macOS: untested (POSIX path, but `RLIMIT_AS` semantics differ there; not claimed).

## Integration seam

A caller that wants containment passes an envelope to `render`/`mux` (or the CLI flags) and reads `receipt["containment"]` and status `CONTAINMENT_REFUSED`. Choosing limits (what is "too much" for a given job), applying the envelope to probes/decodes, aggregate POSIX accounting (cgroups) and Windows verification are separate, explicitly requested follow-ups.
