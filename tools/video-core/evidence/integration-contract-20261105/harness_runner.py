"""Child-side instrumented CLI runner (harness-owned).

Usage:  python -I -B harness_runner.py LOGPATH VIDEO_CORE_DIR MODULE -- ARG...

It runs one real public CLI (`python -m MODULE ARG...` semantics, via runpy) inside THIS harness-owned Python process and records, with a
PEP 578 audit hook, every process launch, network call and filesystem mutation attempted while the CLI runs. The CLI itself is not wrapped,
patched or replaced. The record is written to LOGPATH (JSON) after the CLI has finished.

Categories (so a reader can tell harness-owned from external launches):
  process   subprocess.Popen / os.exec* / os.posix_spawn / os.spawn* / os.system / os.fork*   -> ANY of these inside the CLI is an *external* launch
  network   socket.connect / socket.getaddrinfo / socket.bind
  mutation  os.mkdir/rmdir/remove/rename/replace/symlink/link/truncate/chmod/utime, shutil.*, and open() for writing
The runner process itself is the harness-owned launch; the parent counts those separately.
"""
import json
import os
import runpy
import sys

_PROCESS = {"subprocess.Popen", "os.exec", "os.posix_spawn", "os.spawn", "os.system", "os.fork", "os.forkpty"}
_NETWORK = {"socket.connect", "socket.getaddrinfo", "socket.bind", "socket.gethostbyname"}
_MUTATE = {"os.mkdir", "os.rmdir", "os.remove", "os.rename", "os.replace", "os.symlink", "os.link", "os.truncate", "os.chmod", "os.utime",
           "shutil.copyfile", "shutil.copymode", "shutil.copystat", "shutil.copytree", "shutil.move", "shutil.rmtree", "shutil.make_archive"}
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

events = {"process": [], "network": [], "mutation": [], "read_open_count": 0, "foreign_reads": []}
ALLOWED_PREFIXES = []
recording = [True]


def _short(x):
    try:
        return repr(x)[:200]
    except Exception:  # noqa: BLE001
        return "<unrepr>"


def hook(event, args):
    if not recording[0]:
        return
    if event in _PROCESS or event.startswith("os.exec") or event.startswith("os.spawn"):
        events["process"].append({"event": event, "args": _short(args)})
    elif event in _NETWORK:
        events["network"].append({"event": event, "args": _short(args)})
    elif event in _MUTATE:
        events["mutation"].append({"event": event, "args": _short(args)})
    elif event == "open":
        path, mode, flags = args
        mode = mode or ""
        writing = any(c in str(mode) for c in "wax+") or (isinstance(flags, int) and flags & _WRITE_FLAGS)
        if writing:
            events["mutation"].append({"event": "open-for-write", "args": _short((path, mode, flags))})
        else:
            events["read_open_count"] += 1
            if isinstance(path, (str, bytes, os.PathLike)):
                ps = os.fsdecode(path)
                if ps and not isinstance(path, int) and not any(ps == a or ps.startswith(a.rstrip(os.sep) + os.sep) for a in ALLOWED_PREFIXES):
                    if ps not in events["foreign_reads"] and len(events["foreign_reads"]) < 50:
                        events["foreign_reads"].append(ps)


def main():
    logpath, vc_dir, module = sys.argv[1], sys.argv[2], sys.argv[3]
    assert sys.argv[4] == "--"
    cli_args = sys.argv[5:]
    log_fh_path = logpath  # opened only after the CLI has finished
    sys.path.insert(0, vc_dir)
    import sysconfig
    stdlib = {os.path.realpath(v) for k, v in sysconfig.get_paths().items() if k in ("stdlib", "platstdlib", "purelib", "platlib")} | {os.path.realpath(os.path.dirname(os.__file__))}
    ALLOWED_PREFIXES.extend(sorted(stdlib | {os.path.realpath(x) for x in (vc_dir, os.getcwd(), os.path.dirname(os.path.abspath(__file__)))}))
    sys.argv = [module] + cli_args
    sys.addaudithook(hook)
    code = 0
    try:
        if module.endswith(".py"):      # only the harness's own positive control is ever run this way (see contract_lib.run_control)
            runpy.run_path(module, run_name="__main__")
        else:
            runpy.run_module(module, run_name="__main__", alter_sys=True)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        recording[0] = False
        sys.stdout.flush()
        with open(log_fh_path, "w", encoding="utf-8") as fh:
            json.dump({"exit_code": code, "events": events, "flags": {"dont_write_bytecode": sys.flags.dont_write_bytecode, "isolated": sys.flags.isolated}}, fh)
    sys.exit(code)


if __name__ == "__main__":
    main()
