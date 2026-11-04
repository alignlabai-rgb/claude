"""Optional explicit resource envelope for OWNED child executions. Pure data and validation: nothing native happens here or at import time.

An envelope states limits the CALLER wants applied to a child process this package starts. Nothing is ever applied to the caller's own process, to an
already-running process, or to anything not started by `run_contained`. If a requested limit cannot be applied exactly (or stricter), the target is not started.

Per-limit platform semantics (what each name means where it is supported; "unsupported" = refused before anything starts):

| limit              | POSIX (stdlib `resource`, set in the new worker before exec)            | Windows (new unnamed Job Object, worker assigns only itself)        |
|--------------------|--------------------------------------------------------------------------|---------------------------------------------------------------------|
| cpu_seconds        | RLIMIT_CPU soft=hard, PER PROCESS (children get their own counters)       | PerProcessUserTimeLimit (user-mode time), PER PROCESS in the job    |
| memory_bytes       | RLIMIT_AS soft=hard: virtual ADDRESS SPACE of each process, not RSS       | ProcessMemoryLimit: committed memory of each process in the job     |
| max_file_bytes     | RLIMIT_FSIZE soft=hard: largest file a process may create                 | unsupported                                                         |
| tree_cpu_seconds   | unsupported (no aggregate accounting in per-process RLIMITs)              | PerJobUserTimeLimit: user-mode time of the whole job                |
| tree_memory_bytes  | unsupported                                                               | JobMemoryLimit: committed memory of the whole job                   |
| max_processes      | unsupported (RLIMIT_NPROC is per real user, not per tree: unsafe)         | ActiveProcessLimit: processes in the job (includes the worker)      |

Always uncontained: wall time is enforced by the runner, not the envelope; POSIX has no aggregate CPU/memory for the process tree; a POSIX descendant that
leaves the session/process group escapes group termination; ffprobe/readback commands are not run inside the envelope unless the caller does so explicitly.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, fields

from ..numeric import finite_seconds, non_negative_int, positive_int

MIN_MEMORY_BYTES = 64 * 1024 * 1024  # measured: a bare `python -I -S` worker reserves ~36 MB; a 32 MiB address-space cap makes it fail with MemoryError
MAX_BYTES = 1 << 40
MAX_CPU_SECONDS = 86_400
MIN_MAX_PROCESSES = 2  # the worker itself occupies one slot in the Windows job
MAX_MAX_PROCESSES = 4_096
MAX_HANDSHAKE_SECONDS = 60.0

LIMIT_NAMES = ("cpu_seconds", "memory_bytes", "max_file_bytes", "tree_cpu_seconds", "tree_memory_bytes", "max_processes")
SUPPORTED = {
    "posix": ("cpu_seconds", "memory_bytes", "max_file_bytes"),
    "nt": ("cpu_seconds", "memory_bytes", "tree_cpu_seconds", "tree_memory_bytes", "max_processes"),
}
UNSUPPORTED_REASON = {
    "max_file_bytes": "RLIMIT_FSIZE has no Windows Job Object equivalent here",
    "tree_cpu_seconds": "POSIX per-process RLIMITs have no aggregate (process-tree) CPU accounting",
    "tree_memory_bytes": "POSIX per-process RLIMITs have no aggregate (process-tree) memory accounting",
    "max_processes": "POSIX RLIMIT_NPROC counts per real user, not per process tree; applying it could affect unrelated processes",
}
NOT_CONTAINED = (
    "wall-clock time (enforced by the runner timeout, not by the envelope)",
    "POSIX: no aggregate CPU or memory accounting across the process tree; limits are per process",
    "POSIX: RLIMIT_AS bounds virtual address space, which is not resident memory",
    "POSIX: a descendant that creates its own session or process group escapes group termination",
    "Windows: behaviour unverified on this package's test host (Linux); see the Windows tests and CONTAINMENT.md",
    "readback/probe commands (ffprobe, full-decode checks) are not run inside the envelope unless the caller does so",
    "cgroups, namespaces, seccomp, network and filesystem isolation are not used",
)


def platform_family(os_name: str | None = None) -> str:
    name = os.name if os_name is None else os_name
    return name if name in SUPPORTED else "unsupported"


@dataclass(frozen=True)
class ResourceEnvelope:
    cpu_seconds: int | None = None
    memory_bytes: int | None = None
    max_file_bytes: int | None = None
    tree_cpu_seconds: int | None = None
    tree_memory_bytes: int | None = None
    max_processes: int | None = None
    handshake_timeout_seconds: float | int = 5.0

    def __post_init__(self) -> None:
        for name in ("cpu_seconds", "tree_cpu_seconds"):
            v = getattr(self, name)
            if v is not None:
                positive_int(f"envelope {name}", v, MAX_CPU_SECONDS)
        for name in ("memory_bytes", "tree_memory_bytes"):
            v = getattr(self, name)
            if v is not None:
                positive_int(f"envelope {name}", v, MAX_BYTES)
                if v < MIN_MEMORY_BYTES:
                    raise ValueError(f"envelope {name} must be >= {MIN_MEMORY_BYTES} bytes (the isolated worker itself needs about 36 MB of address space); got {v}")
        if self.max_file_bytes is not None:
            positive_int("envelope max_file_bytes", self.max_file_bytes, MAX_BYTES)
        if self.max_processes is not None:
            positive_int("envelope max_processes", self.max_processes, MAX_MAX_PROCESSES)
            if self.max_processes < MIN_MAX_PROCESSES:
                raise ValueError(f"envelope max_processes must be >= {MIN_MAX_PROCESSES} (the worker occupies one slot); got {self.max_processes}")
        finite_seconds("envelope handshake_timeout_seconds", self.handshake_timeout_seconds, MAX_HANDSHAKE_SECONDS)
        if not self.requested():
            raise ValueError("an envelope must request at least one limit (an empty envelope would only look contained)")

    def requested(self) -> dict:
        return {n: getattr(self, n) for n in LIMIT_NAMES if getattr(self, n) is not None}

    def to_dict(self) -> dict:
        return {**self.requested(), "handshake_timeout_seconds": self.handshake_timeout_seconds}

    @classmethod
    def from_mapping(cls, mapping: dict) -> "ResourceEnvelope":
        if not isinstance(mapping, dict):
            raise ValueError("envelope must be a mapping")
        unknown = sorted(set(mapping) - {f.name for f in fields(cls)})
        if unknown:
            raise ValueError(f"unknown envelope keys: {unknown}")
        return cls(**mapping)


def unsupported_limits(envelope: ResourceEnvelope, os_name: str | None = None, resource_module_available: bool | None = None) -> list:
    """Static, side-effect-free: which requested limits this platform cannot apply. [] means the platform supports every requested limit (not that it WILL apply)."""
    fam = platform_family(os_name)
    out = []
    if fam == "unsupported":
        return [{"limit": k, "reason": f"no native containment implementation for os.name={os.name if os_name is None else os_name!r}"} for k in envelope.requested()]
    if fam == "posix":
        if resource_module_available is None:
            import importlib.util
            resource_module_available = importlib.util.find_spec("resource") is not None
        if not resource_module_available:
            return [{"limit": k, "reason": "the stdlib `resource` module is not available"} for k in envelope.requested()]
    for k in envelope.requested():
        if k not in SUPPORTED[fam]:
            out.append({"limit": k, "reason": UNSUPPORTED_REASON.get(k, "not supported on this platform")})
    return out


CLI_FLAGS = (("cpu-seconds", int), ("memory-bytes", int), ("max-file-bytes", int), ("tree-cpu-seconds", int), ("tree-memory-bytes", int), ("max-processes", int),
             ("handshake-timeout-seconds", float))


def add_cli_args(parser) -> None:
    """Adds the opt-in --envelope-* flags. Nothing is limited unless at least one limit flag is given."""
    g = parser.add_argument_group("optional resource envelope for the main ffmpeg process only (explicit; per-platform semantics in CONTAINMENT.md)")
    for flag, typ in CLI_FLAGS:
        g.add_argument(f"--envelope-{flag}", type=typ, default=None, dest="envelope_" + flag.replace("-", "_"))


def envelope_from_args(ns) -> "ResourceEnvelope | None":
    """None when no limit flag was given; otherwise a validated envelope (ValueError on any bad value)."""
    given = {n: getattr(ns, "envelope_" + n, None) for n in LIMIT_NAMES}
    given = {k: v for k, v in given.items() if v is not None}
    hs = getattr(ns, "envelope_handshake_timeout_seconds", None)
    if not given:
        if hs is not None:
            raise ValueError("--envelope-handshake-timeout-seconds was given without any envelope limit")
        return None
    return ResourceEnvelope(**given, **({} if hs is None else {"handshake_timeout_seconds": hs}))
