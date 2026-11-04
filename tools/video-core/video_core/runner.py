"""Bounded subprocess execution.

* argv is a list; shell=False always; stdin is /dev/null.
* The child runs in its own session/process group (POSIX) so that timeout/flood
  cleanup signals only that owned group. Nothing is ever killed by name or pid
  lookup, and nothing is retried.
* Wall-clock runtime and captured diagnostic bytes are bounded. Output beyond the
  bounds is drained (so the child cannot block on a full pipe) but not stored; the
  receipt keeps head + tail, the full byte count and a SHA-256 of the full stream.
* phase / exit code / signal / timeout / drain are recorded separately.
* The drain bound (`max_drain_bytes`) is a bound on what the child may WRITE, not only on how long it lingers: a breach is recorded as
  `drain_limit_exceeded` whether the child was still running (it is then killed: FLOOD_KILLED) or had already exited on its own (the actual exit code is kept,
  no kill is claimed, and `ok` is False).
* `kill_attempted` / `kill_delivered` say whether the runner itself signalled the owned group and whether a signal reached it, so an outcome name is never the only
  evidence of a kill.
* KeyboardInterrupt (Ctrl-C / the CLIs' SIGTERM translation) is handled in EVERY phase after the child exists (start of the drain threads, the optional hook, the wait loop and
  the final pipe-drain wait): the owned group is cleaned up within a bound and the call returns outcome INTERRUPTED (never an escaping exception that strands a member of
  the group). Orchestrators turn that into a stop with `raise_if_interrupted`.
"""

from __future__ import annotations

import hashlib
import os
import signal as _signal
import subprocess
import threading
import time
from dataclasses import dataclass, field

from .numeric import GRACE_CEILING_SECONDS, finite_seconds, non_negative_int, positive_int

POSIX = os.name == "posix"

EXITED_ZERO = "EXITED_ZERO"
NONZERO_EXIT = "NONZERO_EXIT"
SIGNALED = "SIGNALED"
TIMEOUT = "TIMEOUT"
FLOOD_KILLED = "FLOOD_KILLED"
INTERRUPTED = "INTERRUPTED"
LAUNCH_FAILED = "LAUNCH_FAILED"
CONTAINMENT_REFUSED = "CONTAINMENT_REFUSED"  # an explicit resource envelope could not be applied; the target was NOT started


class _Capture:
    def __init__(self, head_limit: int, tail_limit: int):
        self.head_limit, self.tail_limit = head_limit, tail_limit
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0
        self._sha = hashlib.sha256()

    def feed(self, chunk: bytes) -> None:
        self.total += len(chunk)
        self._sha.update(chunk)
        room = self.head_limit - len(self.head)
        if room > 0:
            self.head += chunk[:room]
            chunk = chunk[room:]
        if chunk and self.tail_limit:
            self.tail += chunk
            if len(self.tail) > self.tail_limit:
                del self.tail[: len(self.tail) - self.tail_limit]

    def summary(self) -> dict:
        kept = len(self.head) + len(self.tail)
        dropped = self.total - kept
        return {
            "total_bytes": self.total,
            "kept_bytes": kept,
            "dropped_bytes": dropped,
            "truncated": dropped > 0,
            "sha256_full_stream": self._sha.hexdigest(),
        }

    def raw(self) -> bytes:
        return bytes(self.head) + bytes(self.tail)

    def text(self) -> str:
        marker = f"\n...[{self.total - len(self.head) - len(self.tail)} bytes dropped]...\n"
        if self.total > len(self.head) + len(self.tail):
            return bytes(self.head).decode("utf-8", "replace") + marker + bytes(self.tail).decode("utf-8", "replace")
        return self.raw().decode("utf-8", "replace")


@dataclass
class RunResult:
    phase: str
    argv: list
    outcome: str
    exit_code: int | None = None
    signal: int | None = None
    timed_out: bool = False
    kill_reason: str | None = None  # why the runner terminated the child, if it did
    drain_complete: bool = True
    orphan_group_killed: bool = False
    wall_seconds: float = 0.0
    timeout_seconds: float = 0.0
    launch_error: str | None = None
    stdout: _Capture | None = field(default=None, repr=False)
    stderr: _Capture | None = field(default=None, repr=False)
    containment: dict | None = None  # set only by video_core.containment.run_contained (requested/applied/unsupported receipt)
    drain_limit_bytes: int = 0  # the max_drain_bytes this run was held to
    drain_limit_exceeded: bool = False  # more than max_drain_bytes was written, whether or not the child was still running when that was noticed
    kill_attempted: bool = False  # the runner itself signalled the owned group (timeout / flood / interrupt / containment refusal / surviving pipe holders)
    kill_delivered: bool = False  # ... and at least one of those signals reached a live process (False when the group was already gone)

    @property
    def ok(self) -> bool:
        return self.outcome == EXITED_ZERO and self.drain_complete and not self.orphan_group_killed and not self.drain_limit_exceeded

    def exit0_not_ok_reason(self, who: str) -> str:
        """Why a child that exited 0 is still not a clean success (for receipts' unknown_reason)."""
        if self.drain_limit_exceeded:
            return (f"{who} exited 0 but wrote more than the {self.drain_limit_bytes}-byte drain bound to its output pipes; the bound was breached "
                    + ("and the runner killed a surviving pipe holder" if self.orphan_group_killed else "although it had exited on its own (no kill was needed or performed)"))
        return f"{who} exited 0 but its output pipes did not drain cleanly (possible surviving child process)"

    def describe(self) -> str:
        """One honest phrase for messages: the outcome plus any reason `ok` is False although the child exited 0."""
        notes = []
        if self.drain_limit_exceeded:
            notes.append("output exceeded the drain bound" + (" and the runner killed it" if self.kill_reason == "flood" else "; the child had exited on its own" if self.outcome == EXITED_ZERO else ""))
        if not self.drain_complete:
            notes.append("output pipes did not drain")
        if self.orphan_group_killed:
            notes.append("a surviving pipe holder was killed")
        return self.outcome + (f" ({'; '.join(notes)})" if notes else "")

    def stdout_bytes(self) -> bytes:
        return self.stdout.raw() if self.stdout else b""

    def to_dict(self, include_text: bool = True) -> dict:
        def cap(c: _Capture | None, text: bool):
            if c is None:
                return None
            d = c.summary()
            if text:
                d["text"] = c.text()
            return d

        return {
            "phase": self.phase,
            "argv": list(self.argv),
            "outcome": self.outcome,
            "exit_code": self.exit_code,
            "signal": self.signal,
            "timed_out": self.timed_out,
            "kill_reason": self.kill_reason,
            "drain_complete": self.drain_complete,
            "orphan_group_killed": self.orphan_group_killed,
            "wall_seconds": round(self.wall_seconds, 3),
            "timeout_seconds": self.timeout_seconds,
            "launch_error": self.launch_error,
            "drain_limit_bytes": self.drain_limit_bytes,
            "drain_limit_exceeded": self.drain_limit_exceeded,
            "kill_attempted": self.kill_attempted,
            "kill_delivered": self.kill_delivered,
            "stdout": cap(self.stdout, False),  # stdout is structured data (ffprobe) or unused
            "stderr": cap(self.stderr, include_text),
            **({"containment": self.containment} if self.containment is not None else {}),
        }


def split_returncode(rc: int | None, posix: bool) -> tuple[int | None, int | None]:
    """Interpret a Popen return code as (exit_code, signal).

    POSIX: a negative code means "terminated by signal -rc" -> (None, -rc).
    Windows has no signals: every code, including the 1 that Popen.terminate() /
    TerminateProcess forces on a runner-killed child, is an exit code -> (rc, None).
    The code alone therefore never decides the outcome; see classify().
    """
    if rc is None:
        return None, None
    if posix and rc < 0:
        return None, -rc
    return rc, None


def classify(kill_reason: str | None, signal: int | None, rc: int | None) -> str:
    """Outcome precedence: what *we* did (kill_reason) beats what the OS reports.

    A runner-killed child looks like a signal on POSIX and like "exit code 1" on Windows;
    only kill_reason says it was a timeout/flood/interrupt.
    """
    if kill_reason == "timeout":
        return TIMEOUT
    if kill_reason == "flood":
        return FLOOD_KILLED
    if kill_reason == "interrupt":
        return INTERRUPTED
    if kill_reason and kill_reason.startswith("containment"):
        return CONTAINMENT_REFUSED
    if signal is not None:
        return SIGNALED
    if rc == 0:
        return EXITED_ZERO
    return NONZERO_EXIT


def _drain(fd: int, cap: _Capture, state: dict, done: threading.Event) -> None:
    try:
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            cap.feed(chunk)
            state["bytes"] += len(chunk)
            if state["bytes"] > state["limit"]:
                state["flood"].set()
    except OSError:
        pass
    finally:
        done.set()


def _signal_group(proc: subprocess.Popen, sig: int) -> bool:
    """Signal only the child's own process group. Returns True if a signal was delivered."""
    try:
        if POSIX:
            os.killpg(proc.pid, sig)  # pgid == pid because of start_new_session
        elif sig == getattr(_signal, "SIGKILL", None):
            proc.kill()
        else:
            proc.terminate()
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


_KILL = getattr(_signal, "SIGKILL", _signal.SIGTERM)


def _terminate(proc: subprocess.Popen, grace: float) -> bool:
    """SIGTERM the owned group, wait up to `grace` for the leader, then SIGKILL. Returns True if any signal was delivered.
    A second interrupt during the cleanup escalates to an immediate SIGKILL instead of abandoning it."""
    delivered = _signal_group(proc, _signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        delivered |= _signal_group(proc, _KILL)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
    except KeyboardInterrupt:
        delivered |= _signal_group(proc, _KILL)
        try:
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, KeyboardInterrupt):
            pass
    return delivered


def _clean_survivors(proc: subprocess.Popen, dones, grace: float) -> bool:
    """Interrupt (or flood) AFTER the leader exited: members of the owned group may still hold the pipes. Signal only that group (TERM, then KILL after `grace`)
    and wait for EOF within a bound. Returns True if any signal was delivered. Never touches anything outside the group."""
    delivered = _signal_group(proc, _signal.SIGTERM)
    try:
        end = time.monotonic() + grace
        while time.monotonic() < end and not all(d.is_set() for d in dones):
            time.sleep(0.01)
    except KeyboardInterrupt:
        pass
    if not all(d.is_set() for d in dones):
        delivered |= _signal_group(proc, _KILL)
    for d in dones:
        try:
            d.wait(1.0)
        except KeyboardInterrupt:
            pass
    return delivered


class RunInterrupted(KeyboardInterrupt):
    """Raised by raise_if_interrupted: a child run was interrupted (Ctrl-C / SIGTERM) and the caller must stop launching work. It IS a KeyboardInterrupt, so the CLIs' existing
    handlers return 130; `.result` carries the interrupted RunResult so an orchestrator that already has a receipt can record the execution before it ends the run."""

    def __init__(self, result: "RunResult"):
        super().__init__(f"{result.phase} interrupted")
        self.result = result


def raise_if_interrupted(res: "RunResult") -> "RunResult":
    """run_bounded keeps its general contract (an interrupt is a normal RunResult with outcome INTERRUPTED). Callers that must not go on to launch another child call this
    right after recording the result."""
    if res.outcome == INTERRUPTED:
        raise RunInterrupted(res)
    return res


def run_bounded(
    argv,
    *,
    phase: str,
    timeout_s: float,
    stdout_limits: tuple[int, int] = (0, 0),
    stderr_limits: tuple[int, int] = (8192, 8192),
    max_drain_bytes: int = 64 * 1024 * 1024,
    cwd: str | None = None,
    env: dict | None = None,
    grace_s: float = 2.0,
    drain_timeout_s: float = 2.0,
    after_start=None,
) -> RunResult:
    """`after_start(proc)` (optional, default None = unchanged behaviour) runs once, on the calling thread, after the child and the drain threads exist and before the
    wait loop. It must be bounded by its own timeouts. It returns None to let the child run, or a string beginning with "containment" to refuse: the child's
    process group is then terminated and the outcome is CONTAINMENT_REFUSED. Wall-clock `timeout_s` includes the hook's time."""
    if not isinstance(argv, (list, tuple)) or not argv or not all(isinstance(a, str) and "\x00" not in a for a in argv):
        raise TypeError("argv must be a non-empty list of NUL-free strings")
    # Public numeric boundary: validated before anything is created or launched (NaN/inf/bool/negative/zero are rejected).
    finite_seconds("timeout_s", timeout_s)
    finite_seconds("grace_s", grace_s, GRACE_CEILING_SECONDS)
    finite_seconds("drain_timeout_s", drain_timeout_s, GRACE_CEILING_SECONDS)
    positive_int("max_drain_bytes", max_drain_bytes)
    for label, pair in (("stdout_limits", stdout_limits), ("stderr_limits", stderr_limits)):
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            raise ValueError(f"{label} must be a (head_bytes, tail_bytes) pair")
        non_negative_int(f"{label}[0]", pair[0])
        non_negative_int(f"{label}[1]", pair[1])
    result = RunResult(phase=phase, argv=list(argv), outcome=LAUNCH_FAILED, timeout_seconds=timeout_s, drain_limit_bytes=max_drain_bytes)
    result.stdout, result.stderr = _Capture(*stdout_limits), _Capture(*stderr_limits)
    t0 = time.monotonic()
    kwargs = {"start_new_session": True} if POSIX else {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
    try:
        proc = subprocess.Popen(
            list(argv), shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=cwd, env=env, close_fds=True, **kwargs,
        )
    except (OSError, ValueError) as exc:
        result.launch_error = f"{type(exc).__name__}: {getattr(exc, 'strerror', None) or exc}"
        result.wall_seconds = time.monotonic() - t0
        return result

    state = {"bytes": 0, "limit": max_drain_bytes, "flood": threading.Event()}
    dones = (threading.Event(), threading.Event())
    threads: list = []
    deadline = t0 + timeout_s
    refused = None
    try:
        threads.extend((
            threading.Thread(target=_drain, args=(proc.stdout.fileno(), result.stdout, state, dones[0]), daemon=True),
            threading.Thread(target=_drain, args=(proc.stderr.fileno(), result.stderr, state, dones[1]), daemon=True),
        ))
        for t in threads:
            t.start()
        if after_start is not None:
            try:
                refused = after_start(proc)
            except KeyboardInterrupt:
                result.kill_reason, refused = "interrupt", "interrupt"
            except Exception as exc:  # a broken hook must not leave a running child behind
                refused = f"containment_hook_error: {type(exc).__name__}"
            if refused is not None:
                if result.kill_reason is None:
                    result.kill_reason = refused if str(refused).startswith("containment") else f"containment: {refused}"
                result.kill_attempted = True
                result.kill_delivered |= _terminate(proc, grace_s)
        while refused is None:
            try:
                proc.wait(timeout=0.05)
                break
            except subprocess.TimeoutExpired:
                pass
            if state["flood"].is_set():
                result.kill_reason = "flood"
                result.kill_attempted = True
                result.kill_delivered |= _terminate(proc, grace_s)
                break
            if time.monotonic() >= deadline:
                result.timed_out, result.kill_reason = True, "timeout"
                result.kill_attempted = True
                result.kill_delivered |= _terminate(proc, grace_s)
                break
    except KeyboardInterrupt:  # during thread start, the hook's own cleanup, the wait loop or a kill's wait
        result.kill_reason = "interrupt"
        result.kill_attempted = True
        result.kill_delivered |= _terminate(proc, grace_s)
        for i, d in enumerate(dones):
            if i >= len(threads) or threads[i].ident is None:  # never started: nothing will ever read (or EOF-signal) this pipe
                d.set()

    # Drain: wait briefly for EOF on both pipes. A grandchild that kept the pipe open inside our child's process group is an orphan of ours and is killed once;
    # so is one that keeps FLOODING a pipe past the drain bound (no need to wait out the timeout for output that is only being discarded).
    # An interrupt in this phase is handled here too: the group is cleaned up within a bound and the call still returns (outcome INTERRUPTED).
    try:
        end = time.monotonic() + drain_timeout_s
        while not all(d.is_set() for d in dones) and not state["flood"].is_set() and time.monotonic() < end:
            time.sleep(0.01)
        if not all(d.is_set() for d in dones):
            if POSIX or proc.poll() is None:
                result.kill_attempted = True
                result.orphan_group_killed = _signal_group(proc, _KILL)
                result.kill_delivered |= result.orphan_group_killed
            for d in dones:
                d.wait(1.0)
    except KeyboardInterrupt:
        result.kill_reason = "interrupt"  # overrides an earlier timeout/flood label: the caller cancelled (timed_out stays True if the timeout had fired)
        if proc.poll() is None or not all(d.is_set() for d in dones):
            result.kill_attempted = True
            result.kill_delivered |= _clean_survivors(proc, dones, grace_s) if POSIX else _terminate(proc, grace_s)
    result.drain_limit_exceeded = state["flood"].is_set()
    result.drain_complete = all(d.is_set() for d in dones)
    for stream in (proc.stdout, proc.stderr):
        try:
            if result.drain_complete:
                stream.close()
        except OSError:
            pass

    rc = proc.returncode
    result.exit_code, result.signal = split_returncode(rc, POSIX)
    result.outcome = classify(result.kill_reason, result.signal, rc)
    result.wall_seconds = time.monotonic() - t0
    return result
