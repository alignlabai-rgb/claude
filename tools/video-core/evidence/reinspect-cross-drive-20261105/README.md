# `avmux reinspect` cross-drive ValueError — reproduction, minimal fix, tests (2026-11-05)

Commit on top of `8f7820e`. Linux x86_64, Python 3.11 / 3.12 / 3.13. No real media/probe, no network, no privilege or link creation; nothing outside the tests' owned temp directories is touched.
Reported by the independent native-Windows run (owned synthetic `.mp4` on C: and on D:, `tools.locate` patched, Popen forbidden): D→D and C→C `SKIPPED_TOOL_UNAVAILABLE`; **D→C and C→D `ValueError: Paths don't have the same drive`**, no process, no output.

## Cause and fix
`reinspect_audio_packets` (`avmux/reinspect.py`, after `check_output_location`) tested containment with `os.path.commonpath([dirname(media), run_dir]) == run_dir`. On Windows `commonpath` raises `ValueError` for paths on
different drives (or different UNC shares), and nothing caught it → untyped exception instead of continuing (or a typed rejection).
Fix (2 files, 6 lines): the check is now `paths._inside(location.run_dir, os.path.dirname(media))`. `_inside` is the existing helper that `resolve_asset` already uses; it answers `False` when `commonpath` raises
`ValueError` (different drives cannot contain each other) and is otherwise the identical test (`commonpath([root, candidate]) == root`). `_inside` gained one optional private parameter `_path=os.path` (default unchanged)
so tests can run ntpath semantics on any OS. The `output_overlaps_media` rejection is unchanged for same-drive paths. **Not decided or changed:** root/absolute-path policy, parent-link rejection, atomic open, any new API or flag.

## Reproduction evidence (`repro_cross_drive.py`, SIMULATION of the path arithmetic)
It gives `reinspect` Windows-style paths from the two path checkers and points `os.path.commonpath`/`dirname` at `ntpath` for the duration; a patched `getsize` raising a sentinel marks "passed the containment check".
Not a Windows run. `before.8f7820e.python3.13.json` vs `after.python3.13.json`:
| case | before (`8f7820e`) | after |
|---|---|---|
| media C:, output D: | UNTYPED `ValueError: Paths don't have the same drive` | passes the check |
| media D:, output C: | UNTYPED `ValueError` | passes the check |
| same drive, media elsewhere | passes | passes |
| same drive, media inside output dir / media dir == output dir | typed `output_overlaps_media` | typed `output_overlaps_media` |
| same drive, output dir inside the media dir | passes | passes |

## Tests (`tests/test_reinspect_cross_drive.py`, 8 tests)
Pure containment (`_inside` with `ntpath`/`posixpath`): the premise that `ntpath.commonpath` raises for different drives/shares (so the tests cannot pass vacuously); cross-drive and cross-UNC-share → False both ways, no exception; same-drive positive
(equal, below, case-insensitive) and negative (parent, `run1x` prefix sibling, other, drive root) for both path flavours; platform default unchanged. Reinspect-level (simulated ntpath, no Popen): cross-drive both directions pass the check; same-drive overlap still
`output_overlaps_media`; the public `avmux.cli.main(["reinspect", …])` returns exit 1 with the structured JSON (`media_sha256_mismatch` after passing containment for cross-drive; `unsafe_path`/`output_overlaps_media` for overlap), no traceback.
Native real-filesystem case (runs on whatever OS executes it; on the Windows verifier it is the same-drive case with real C:/D: files): separate dirs get past validation and containment to the guarded tool lookup (`SKIPPED_TOOL_UNAVAILABLE`, `written` False, tree unchanged — **not** a coverage claim),
and an existing output directory is still refused earlier by `output_exists`.
Note: on a real filesystem `output_overlaps_media` is hard to reach because `check_output_location` already rejects an existing run directory (which the media's directory would have to be); it is defence in depth, so its same-drive positive case is exercised through the simulation.

## Results (each interpreter once)
| run | total | pass | skip | fail |
|---|---|---|---|---|
| new tests against `8f7820e` production (py3.13) — `new-tests.BEFORE-fix.8f7820e.python3.13.log` | 8 | 3 control/premise | 0 | 5 (19 sub-cases: 2 reinspect-level tests = the untyped `ValueError`; 3 pure tests = `_inside` has no `_path` parameter yet) |
| targeted set (new + reinspect, original-receipt, paths, inspect link-like, avmux CLI/plan, audio plan, static guards) py3.11 / 3.12 / 3.13 — `targeted.AFTER.log` | 161 each | 161 | 0 | 0 |
| existing full suite, py3.13 only (once) — `full-suite.AFTER.python3.13.log` | 630 | 621 | 9 | 0 |
Previous full suite: 622 total / 613 pass / 9 skip. The 9 skips are pre-existing. No failed attempt in the fix run; the "before" failures are the intended proof.

## Limits
Simulation only for the cross-drive arithmetic: the real C:/D: run belongs to the independent Windows verifier. Mixed absolute/relative paths (not produced here: both operands are absolute) would still raise `ValueError` in `commonpath` and now also read as "not contained". Case-insensitive matching and 8.3 names follow `ntpath.commonpath`,
which is what `_inside` already relied on for `resolve_asset`. Prior evidence, specs, schemas and examples are byte-identical (`unchanged-check.txt`).
