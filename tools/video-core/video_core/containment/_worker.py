"""Isolated worker for video_core.containment. SELF-CONTAINED: stdlib only, no video_core imports; run as `python -I -S _worker.py CONFIG_JSON -- TARGET...`.

It is the ONLY place in this package that touches native resource APIs, and it only ever touches ITS OWN process:
  POSIX   : setrlimit() on itself (RLIMIT_CPU, RLIMIT_FSIZE, then RLIMIT_AS last), then execv() the target -> the target IS this process image and inherits the limits.
  Windows : create a fresh UNNAMED Job Object, set its limits, AssignProcessToJobObject(job, GetCurrentProcess()) -> only this new worker; then start the target as
            its child (children inherit the job; no breakaway flag is ever passed). The job handle is created non-inheritable, is never duplicated or closed explicitly
            and lives until this worker exits; JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE then makes the OS terminate everything still in the job.
It never attaches to another process, never calls any API with a handle it did not create, never breaks away from an inherited job and never changes one.

Protocol with the parent (files in an owned 0700 directory, token-checked, all waits bounded):
  1. apply limits -> write hello.json (requested, applied-from-READ-BACK, unsupported, errors); if anything is unsupported/failed: exit 87 (target never started)
  2. wait (bounded) for `go` (start) or `abort` (exit 86); neither in time: exit 85
  3. write started.json, then exec/spawn the target
`applied` values come from reading the limits back from the OS, never from what this file believes it set. No import-time side effects.
"""

import json
import os
import sys
import time

HELLO_SCHEMA = "video_core.containment_hello/1"
EXIT_UNSUPPORTED, EXIT_ABORTED, EXIT_NO_GO, EXIT_EXEC_FAILED, EXIT_BAD_CONFIG = 87, 86, 85, 88, 89

# ---- Windows constants/structures (declared lazily so the module is importable and layout-testable on any OS) ----
JOB_OBJECT_LIMIT_PROCESS_TIME = 0x2
JOB_OBJECT_LIMIT_JOB_TIME = 0x4
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x8
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100
JOB_OBJECT_LIMIT_JOB_MEMORY = 0x200
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9
HUNDRED_NS_PER_SECOND = 10_000_000
_JOB_HANDLE = None  # keeps the Job handle alive for the whole worker lifetime (never closed explicitly)


def windows_structs():
    """JOBOBJECT_BASIC_LIMIT_INFORMATION / IO_COUNTERS / JOBOBJECT_EXTENDED_LIMIT_INFORMATION with explicit fixed-width types (not wintypes aliases)."""
    import ctypes
    from ctypes import Structure, c_int64, c_size_t, c_uint32, c_uint64

    class Basic(Structure):
        _fields_ = [("PerProcessUserTimeLimit", c_int64), ("PerJobUserTimeLimit", c_int64), ("LimitFlags", c_uint32), ("MinimumWorkingSetSize", c_size_t),
                    ("MaximumWorkingSetSize", c_size_t), ("ActiveProcessLimit", c_uint32), ("Affinity", c_size_t), ("PriorityClass", c_uint32), ("SchedulingClass", c_uint32)]

    class IoCounters(Structure):
        _fields_ = [(n, c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class Extended(Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IoCounters), ("ProcessMemoryLimit", c_size_t), ("JobMemoryLimit", c_size_t),
                    ("PeakProcessMemoryUsed", c_size_t), ("PeakJobMemoryUsed", c_size_t)]
    return Basic, IoCounters, Extended, ctypes


def write_atomic(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)
        fh.flush()
    os.replace(tmp, path)


def apply_posix(lim, hello):
    import resource

    plan = [("cpu_seconds", resource.RLIMIT_CPU), ("max_file_bytes", resource.RLIMIT_FSIZE), ("memory_bytes", resource.RLIMIT_AS)]  # address space LAST: it constrains this worker too
    for name, res in plan:
        if name not in lim:
            continue
        want = lim[name]
        try:
            cur_soft, cur_hard = resource.getrlimit(res)
            target = want
            if cur_hard != resource.RLIM_INFINITY and cur_hard < want:
                target = cur_hard  # never raise a limit: an already-stricter inherited limit stays in force
                hello.setdefault("notes", []).append(f"{name}: inherited hard limit {cur_hard} is already stricter than the requested {want}")
            resource.setrlimit(res, (target, target))
            soft, hard = resource.getrlimit(res)  # READ BACK
            hello["applied"][name] = soft if soft == hard and soft != resource.RLIM_INFINITY else None
            if hello["applied"][name] is None:
                hello["errors"].append(f"{name}: read-back soft/hard limits are ({soft}, {hard}), not a finite equal pair")
        except (ValueError, OSError, MemoryError) as exc:
            hello["errors"].append(f"{name}: {type(exc).__name__}: {exc}")


def apply_windows(lim, hello):
    global _JOB_HANDLE
    Basic, _Io, Extended, ctypes = windows_structs()
    from ctypes import byref, c_int, c_void_p, sizeof

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.GetCurrentProcess.restype = c_void_p
    k32.CreateJobObjectW.restype = c_void_p
    k32.CreateJobObjectW.argtypes = [c_void_p, c_void_p]
    k32.SetInformationJobObject.argtypes = [c_void_p, c_int, c_void_p, ctypes.c_uint32]
    k32.QueryInformationJobObject.argtypes = [c_void_p, c_int, c_void_p, ctypes.c_uint32, c_void_p]
    k32.AssignProcessToJobObject.argtypes = [c_void_p, c_void_p]
    k32.IsProcessInJob.argtypes = [c_void_p, c_void_p, c_void_p]
    me = k32.GetCurrentProcess()
    inherited = c_int(0)
    if k32.IsProcessInJob(me, None, byref(inherited)):
        hello["inherited_job_present"] = bool(inherited.value)  # informational: an existing (sandbox) job is never touched, only nested under if the OS allows
    job = k32.CreateJobObjectW(None, None)  # unnamed, default (non-inheritable) security
    if not job:
        hello["errors"].append(f"CreateJobObjectW failed (winerror {ctypes.get_last_error()})")
        return
    info = Extended()
    flags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if "cpu_seconds" in lim:
        flags |= JOB_OBJECT_LIMIT_PROCESS_TIME
        info.BasicLimitInformation.PerProcessUserTimeLimit = lim["cpu_seconds"] * HUNDRED_NS_PER_SECOND
    if "tree_cpu_seconds" in lim:
        flags |= JOB_OBJECT_LIMIT_JOB_TIME
        info.BasicLimitInformation.PerJobUserTimeLimit = lim["tree_cpu_seconds"] * HUNDRED_NS_PER_SECOND
    if "max_processes" in lim:
        flags |= JOB_OBJECT_LIMIT_ACTIVE_PROCESS
        info.BasicLimitInformation.ActiveProcessLimit = lim["max_processes"]
    if "memory_bytes" in lim:
        flags |= JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = lim["memory_bytes"]
    if "tree_memory_bytes" in lim:
        flags |= JOB_OBJECT_LIMIT_JOB_MEMORY
        info.JobMemoryLimit = lim["tree_memory_bytes"]
    info.BasicLimitInformation.LimitFlags = flags
    if not k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, byref(info), sizeof(info)):
        hello["errors"].append(f"SetInformationJobObject failed (winerror {ctypes.get_last_error()})")
        k32.CloseHandle(job)
        return
    if not k32.AssignProcessToJobObject(job, me):  # assigns ONLY this new worker; ACCESS_DENIED (5) typically means a surrounding job forbids nesting
        hello["unsupported"].append({"limit": "*", "reason": f"AssignProcessToJobObject(self) failed (winerror {ctypes.get_last_error()}); an inherited job may not allow nesting"})
        k32.CloseHandle(job)
        return
    _JOB_HANDLE = job
    out = Extended()
    if not k32.QueryInformationJobObject(job, JobObjectExtendedLimitInformation, byref(out), sizeof(out), None):  # READ BACK
        hello["errors"].append(f"QueryInformationJobObject failed (winerror {ctypes.get_last_error()})")
        return
    b, f = out.BasicLimitInformation, out.BasicLimitInformation.LimitFlags
    if not f & JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE:
        hello["errors"].append("kill-on-job-close is not in force after read-back")
    got = {}
    if f & JOB_OBJECT_LIMIT_PROCESS_TIME:
        got["cpu_seconds"] = b.PerProcessUserTimeLimit // HUNDRED_NS_PER_SECOND
    if f & JOB_OBJECT_LIMIT_JOB_TIME:
        got["tree_cpu_seconds"] = b.PerJobUserTimeLimit // HUNDRED_NS_PER_SECOND
    if f & JOB_OBJECT_LIMIT_ACTIVE_PROCESS:
        got["max_processes"] = b.ActiveProcessLimit
    if f & JOB_OBJECT_LIMIT_PROCESS_MEMORY:
        got["memory_bytes"] = out.ProcessMemoryLimit
    if f & JOB_OBJECT_LIMIT_JOB_MEMORY:
        got["tree_memory_bytes"] = out.JobMemoryLimit
    hello["applied"].update(got)
    hello["also_in_force"] = ["kill_on_job_close"]


def main(argv):
    try:
        cfg = json.loads(argv[1])
        if argv[2] != "--" or len(argv) < 4:
            raise ValueError("expected -- TARGET...")
        d, token, lim, target = cfg["dir"], cfg["token"], cfg["limits"], argv[3:]
        wait = float(cfg["wait_go_seconds"])
    except Exception as exc:  # malformed invocation: nothing has been touched
        sys.stderr.write(f"containment worker: bad config: {type(exc).__name__}: {exc}\n")
        return EXIT_BAD_CONFIG
    hello = {"schema": HELLO_SCHEMA, "token": token, "worker_pid": os.getpid(), "platform": os.name, "requested": lim, "applied": {}, "unsupported": [], "errors": []}
    if os.name == "posix":
        apply_posix(lim, hello)
    elif os.name == "nt":
        apply_windows(lim, hello)
    else:
        hello["unsupported"].append({"limit": "*", "reason": f"os.name={os.name!r} has no implementation"})
    try:
        write_atomic(os.path.join(d, "hello.json"), hello)
    except Exception as exc:
        sys.stderr.write(f"containment worker: cannot write hello: {type(exc).__name__}\n")
        return EXIT_UNSUPPORTED
    if hello["unsupported"] or hello["errors"]:
        return EXIT_UNSUPPORTED  # the target is never started
    end = time.monotonic() + wait
    while True:
        if os.path.exists(os.path.join(d, "abort")):
            return EXIT_ABORTED
        if os.path.exists(os.path.join(d, "go")):
            break
        if time.monotonic() >= end:
            return EXIT_NO_GO
        time.sleep(0.01)
    try:
        write_atomic(os.path.join(d, "started.json"), {"token": token, "phase": "about_to_start_target"})
        if os.name == "posix":
            os.execv(target[0], target)  # replaces this image: the target runs in this very process with the limits in force
        import subprocess

        return subprocess.Popen(target, stdin=subprocess.DEVNULL, close_fds=True).wait()  # Windows: a child of the jobbed worker, inherits the job; stdout/stderr inherited
    except Exception as exc:
        sys.stderr.write(f"containment worker: cannot start target: {type(exc).__name__}: {exc}\n")
        return EXIT_EXEC_FAILED


if __name__ == "__main__":
    sys.exit(main(sys.argv))
