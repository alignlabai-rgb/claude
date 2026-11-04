"""run_contained: run ONE owned target under an explicit ResourceEnvelope, with an honest requested/applied/unsupported receipt.

Flow (nothing here touches the calling Python process's own limits; nothing is applied at import time):
  1. Validate envelope and numeric arguments (strict, finite). Statically refuse limits this platform cannot apply -> CONTAINMENT_REFUSED, no process started.
  2. Create an owned, private (0700) handshake directory and a random token.
  3. Start the isolated worker through the bounded runner (own session/process group, drained pipes, wall-clock timeout, no retry).
  4. Bounded handshake: wait for hello.json, verify the token, that the worker's READ-BACK `applied` limits equal the requested ones (or are stricter) and that
     nothing is unsupported or failed. Only then write `go`. Any deviation writes `abort` and the target is never started; there is no weaker second attempt.
  5. The runner supervises the target to exit/timeout/interrupt, terminates only the owned process group (POSIX) / the worker (Windows, whose job then kills the tree),
     and drains output.
  6. Remove the handshake directory (always) and return the RunResult with `.containment`.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import sys
import tempfile
import time

from .. import runner
from ..numeric import finite_seconds
from .envelope import NOT_CONTAINED, SUPPORTED, ResourceEnvelope, platform_family, unsupported_limits

RECEIPT_SCHEMA = "video_core.containment_receipt/1"
WORKER_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_worker.py")
HELLO_SCHEMA = "video_core.containment_hello/1"
MAX_HELLO_BYTES = 64 * 1024
POLL_SECONDS = 0.01
MECHANISM = {"posix": "setrlimit() on the new worker before execv(): the target runs as that process image",
             "nt": "new unnamed Job Object; the new worker assigns only itself, then starts the target as its child (kill-on-job-close)"}


def validate_hello(hello, token: str, requested: dict):
    """Pure. Returns (ok, reason). The worker's READ-BACK report must name exactly the requested limits, each applied value finite, an int and <= the request."""
    if not isinstance(hello, dict) or hello.get("schema") != HELLO_SCHEMA:
        return False, "hello has the wrong schema"
    if hello.get("token") != token:
        return False, "hello token does not match"
    if hello.get("requested") != requested:
        return False, "the worker echoed different requested limits"
    if hello.get("unsupported"):
        return False, "worker reports unsupported: " + json.dumps(hello["unsupported"])[:300]
    if hello.get("errors"):
        return False, "worker reports errors: " + json.dumps(hello["errors"])[:300]
    applied = hello.get("applied")
    if not isinstance(applied, dict) or set(applied) != set(requested):
        return False, f"applied limits {sorted(applied) if isinstance(applied, dict) else applied!r} do not cover exactly the requested {sorted(requested)}"
    for k, want in requested.items():
        got = applied[k]
        if isinstance(got, bool) or not isinstance(got, int) or got <= 0 or got > want:
            return False, f"{k}: applied {got!r} is not a positive integer <= requested {want}"
    return True, "ok"


def _atomic_write(path: str, text: str) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def _refusal_result(argv, phase, timeout_s, envelope, unsupported, reason, detail=None) -> runner.RunResult:
    r = runner.RunResult(phase=phase, argv=list(argv), outcome=runner.CONTAINMENT_REFUSED, timeout_seconds=timeout_s, kill_reason="containment_refused: " + reason)
    r.stdout, r.stderr = runner._Capture(0, 0), runner._Capture(0, 0)
    r.containment = _receipt(envelope, {}, unsupported, reason, detail, worker_started=False, go_sent=False, hello=None, handshake_seconds=0.0)
    return r


def _receipt(envelope, applied, unsupported, refusal, detail, *, worker_started, go_sent, hello, handshake_seconds) -> dict:
    fam = platform_family()
    return {
        "schema": RECEIPT_SCHEMA, "platform": os.name, "mechanism": MECHANISM.get(fam, "none"),
        "requested": envelope.requested(), "applied": applied, "unsupported": unsupported, "supported_limit_names_here": list(SUPPORTED.get(fam, ())),
        "refusal": None if refusal is None else {"reason": refusal, "detail": detail}, "worker_started": worker_started, "go_sent": go_sent,
        "target_may_have_started": bool(go_sent), "handshake_seconds": round(handshake_seconds, 4), "handshake_timeout_seconds": envelope.handshake_timeout_seconds,
        "worker_pid": (hello or {}).get("worker_pid"), "inherited_job_present": (hello or {}).get("inherited_job_present"), "notes": (hello or {}).get("notes", []),
        "also_in_force": (hello or {}).get("also_in_force", []), "not_contained": list(NOT_CONTAINED),
        "applied_to": "the new worker/target process(es) only; never the calling process, an existing process, or an inherited job",
        "retry": "none: a refusal is final, no weaker limits are tried",
    }


def run_contained(argv, envelope: ResourceEnvelope, *, phase: str, timeout_s, stdout_limits=(0, 0), stderr_limits=(8192, 8192), max_drain_bytes=64 * 1024 * 1024,
                  cwd=None, env=None, grace_s=2.0, drain_timeout_s=2.0) -> runner.RunResult:
    if not isinstance(envelope, ResourceEnvelope):
        raise TypeError("envelope must be a ResourceEnvelope")
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) and "\x00" not in a for a in argv):
        raise TypeError("argv must be a non-empty list of NUL-free strings")
    finite_seconds("timeout_s", timeout_s)  # run_bounded re-validates everything else before launching
    unsupported = unsupported_limits(envelope)
    if unsupported:
        return _refusal_result(argv, phase, timeout_s, envelope, unsupported, "unsupported_limit")
    if not sys.executable or not os.path.isfile(WORKER_PATH):
        return _refusal_result(argv, phase, timeout_s, envelope, [], "worker_unavailable", "no usable Python executable or worker script")

    hs_dir = tempfile.mkdtemp(prefix="vc-envelope-")  # owned; created private (0700 on POSIX)
    token = secrets.token_hex(16)
    requested = envelope.requested()
    cfg = {"dir": hs_dir, "token": token, "limits": requested, "wait_go_seconds": envelope.handshake_timeout_seconds + 2.0}
    worker_argv = [sys.executable, "-I", "-S", WORKER_PATH, json.dumps(cfg, sort_keys=True), "--", *argv]
    state = {"hello": None, "go_sent": False, "reason": None, "detail": None, "seconds": 0.0}

    def handshake(proc):
        t0 = time.monotonic()
        end = t0 + envelope.handshake_timeout_seconds
        hello_path = os.path.join(hs_dir, "hello.json")
        try:
            while not os.path.exists(hello_path):
                if proc.poll() is not None:
                    state.update(reason="worker_exited_before_hello", detail=f"worker exit code {proc.returncode}")
                    return "containment_refused: worker_exited_before_hello"
                if time.monotonic() >= end:
                    state.update(reason="handshake_timeout", detail=f"no hello within {envelope.handshake_timeout_seconds} s")
                    return "containment_refused: handshake_timeout"
                time.sleep(POLL_SECONDS)
            with open(hello_path, "rb") as fh:
                blob = fh.read(MAX_HELLO_BYTES + 1)
            if len(blob) > MAX_HELLO_BYTES:
                state.update(reason="hello_too_large", detail=f"more than {MAX_HELLO_BYTES} bytes")
                return "containment_refused: hello_too_large"
            try:
                hello = json.loads(blob.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                state.update(reason="hello_unreadable", detail="not valid UTF-8 JSON")
                return "containment_refused: hello_unreadable"
            state["hello"] = hello if isinstance(hello, dict) else None
            ok, why = validate_hello(hello, token, requested)
            if not ok:
                state.update(reason="limits_not_applied", detail=why)
                return "containment_refused: limits_not_applied"
            _atomic_write(os.path.join(hs_dir, "go"), "go")
            state["go_sent"] = True
            return None
        finally:
            state["seconds"] = time.monotonic() - t0
            if not state["go_sent"]:
                try:
                    _atomic_write(os.path.join(hs_dir, "abort"), "abort")  # best effort; the runner also terminates the worker
                except OSError:
                    pass

    try:
        res = runner.run_bounded(worker_argv, phase=phase, timeout_s=timeout_s, stdout_limits=stdout_limits, stderr_limits=stderr_limits, max_drain_bytes=max_drain_bytes,
                                 cwd=cwd, env=env, grace_s=grace_s, drain_timeout_s=drain_timeout_s, after_start=handshake)
    finally:
        shutil.rmtree(hs_dir, ignore_errors=True)
    res.argv = list(argv)  # the receipt shows the TARGET command; the worker invocation is described by `mechanism`
    hello = state["hello"] if isinstance(state["hello"], dict) else None
    refusal = state["reason"] if res.outcome == runner.CONTAINMENT_REFUSED else None
    if res.outcome == runner.LAUNCH_FAILED:
        refusal = "worker_launch_failed"
        res.outcome, res.kill_reason = runner.CONTAINMENT_REFUSED, "containment_refused: worker_launch_failed"
    res.containment = _receipt(envelope, (hello or {}).get("applied", {}) if refusal is None else {}, (hello or {}).get("unsupported", []), refusal, state["detail"],
                               worker_started=res.outcome != runner.LAUNCH_FAILED and res.launch_error is None, go_sent=state["go_sent"], hello=hello, handshake_seconds=state["seconds"])
    if res.signal is not None and refusal is None:
        res.containment["signal_hint"] = {"signal": res.signal, "note": "if this is SIGXCPU the CPU limit was reached; SIGKILL/SIGSEGV/MemoryError exits can follow address-space or other limits; this is a hint, not a proof"}
    return res
