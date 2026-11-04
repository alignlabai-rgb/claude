# Optional resource envelope — native evidence (author's own run, Linux cloud host, 2026-11-04)

Host: Linux 6.18 x86_64, Python 3.11.18/3.12.3/3.13.18, FFmpeg/ffprobe 6.1.1-3ubuntu5, 4 CPUs, no inherited rlimits, cgroup v1 layout (not used by this feature).
Design, semantics, limitations and the (unverified) Windows ownership model: `CONTAINMENT.md`. **Windows was not run; the official Microsoft/Python documentation pages could not be read in this session (network policy denied `learn.microsoft.com` and `docs.python.org`).**

## Native tests that ran on this host (see `native-and-related-tests.log`)

`tests/test_containment.py::NativePosix` — real owned Python fixtures under the real worker and kernel limits:
* accepted target: the **target itself** printed `RLIMIT_CPU [7,7]`, `RLIMIT_AS [268435456,268435456]`, `RLIMIT_FSIZE [1048576,1048576]`, and its pid equalled the receipt's `worker_pid` (exec model); a single requested limit leaves the others unlimited; an inherited stricter hard limit (3 s) stayed 3 s when 50 s was requested (never raised, noted);
* unsupported limits (`tree_memory_bytes`, `tree_cpu_seconds`, `max_processes`, and a mixed request) refused with `subprocess.Popen` patched to raise — no worker, no target, nothing applied, no weaker retry;
* CPU exhaustion: busy target with `cpu_seconds=1` ended by the kernel (`SIGKILL`) in ≈ 1.0 s, not by our timeout;
* memory exhaustion: with a 128 MiB cap an 8-MiB-chunk allocation loop (192 MiB maximum) hit `MemoryError` before 128 MiB; the identical control run without an envelope allocated all 192 MiB;
* file size: a writer under `max_file_bytes=1 MiB` got `EFBIG` and the file stayed ≤ 1 MiB;
* timeout (2.5 s) and a `SIGINT` cancellation: the target, its child and its grandchild all died; an unrelated sentinel process in its own session survived;
* chatty target (3 MB stdout, 0.5 MB stderr) drained without deadlock; non-zero exit and stderr pass through; a missing target fails after `go` with exit 88 and is not retried;
* the caller's own rlimits and the handshake temp directory are checked after **every** test (`tearDown`); importing the package changes no limit (fresh interpreter).
`ParentHandshakeWithStubWorker` uses a **stub** worker to test only the parent's bounded verification (no hello, early exit, garbage/oversize/wrong-token/echo/weaker/missing/extra/unsupported/error hellos → refused, `go` never sent, target never ran, handshake dir `0700`): **logic tests, not native evidence**.
`NativeWindows` (6 tests) is **skipped on this host** and `WindowsStructLayout` pins 64-bit ctypes layout (64/48/144 bytes): **a layout check, not Windows evidence**.

## Development-only measurements (before the design was fixed)

* a bare `python -I -S` worker reserves ≈ 36.8 MB of address space; a 64 MiB `RLIMIT_AS` leaves it functional (json, file write, `os.replace`, `execv`), 32 MiB fails with `MemoryError` → 64 MiB floor;
* a tiny ffmpeg libx264 encode to `-f null` (10 frames) showed a sampled `VmPeak` ≈ 604 MB (a lower bound) → the real run below used a 3 GiB cap. These probes wrote no media.

## The one real contained execution (a NEW tiny synthetic case, different from every earlier render)

Episode `episode.envelope-tiny.json`: 192×108, 25/1 fps, 10 frames, two colour clips (`#204060`, `#a06020`), one caption (sidecar SRT only). No story, voice or timing content. Dry run first (`dry-run.json`: support table, nothing applied). Then, once, at 2026-11-04T21:59:53Z, exit 0:

```bash
python3 -m video_core render episode.envelope-tiny.json --output-root <scratch>/envelope-run/out --output-name tiny-env --render \
    --envelope-cpu-seconds 60 --envelope-memory-bytes 3221225472 --envelope-max-file-bytes 67108864
```
* Status `READBACK_MATCH` (`render.receipt.json`, plan `e890d9b5…`). `containment`: requested = applied = `{cpu_seconds 60, memory_bytes 3221225472, max_file_bytes 67108864}` (read back by the worker), `unsupported []`, `refusal null`, `go_sent true`, handshake 0.031 s, mechanism `setrlimit() before execv()`.
* Only the `render` execution carries `containment`; the version/capability probes and the `inspect` ffprobe ran plain, as documented.
* `output.mp4` 1889 bytes, SHA-256 `fa3cce3d5bcc5b8158b962e49c997b9daac76523b6b778830fcfb76a51f00da4` (not committed). Core readback: 192×108, `25/1`, 10 decoded frames, h264/yuv420p, stderr empty.
* Independent full decode/probe (`independent_check.json`, the existing standalone `evidence/independent_check.py`, not importing `video_core`): 10 frames decoded, constant one-frame PTS step, first PTS 0, duration 0.4 s, corner pixels within the known few-level colour drift (unrelated, unchanged).
* This run proves the envelope works with the real ffmpeg on this host with generous limits; it does **not** show that ffmpeg was ever near a limit. No retry, no second execution, no baseline/stem/mux regeneration.

## Not shown
Windows behaviour; macOS; cgroups; tree-wide POSIX accounting; limits applied to probes/readback; byte-reproducible containers; any quality or creative property.
