# portable/ — bounded contract acceptance harness (Python 3.11+ stdlib, Linux and Windows)

Start with `../PORTABLE_HANDOFF.md`. Files:

| File | Role |
|---|---|
| `portable_harness.py` | driver / CLI (`--library-root`, `--scratch-root`, `--out-dir`, `--profile`, `--strict`, `--budget-seconds`, `--only`, `--skip-mutation`, `--normalize-stored-paths`, `--list`) |
| `portable_core.py` | bounded serial child driver, source inventory + pin, check classification, exclusive-create output writer |
| `child_runner.py` | isolated child wrapper with the **denying** audit hook; runs one CLI via `runpy` or one mechanism control |
| `portable_cases.py` | 11 contract cases with independent oracles and the three layers kept separate |
| `portable_selftest.py` | 7 mechanism self-tests and 7 mutation controls on disposable copies |
| `profiles/pinned_original_b4ca4a3.json` | source pin of the original library (no reviewed expectations) |
| `verify_scope.py` | proves only `portable/` + `PORTABLE_HANDOFF.md` were added relative to a base commit |
| `results/` | Linux evidence (3.11/3.12/3.13, strict, Japanese+space roots, read-only `f42c2db` candidate probe) |
| `attempts/` | preserved failed first attempts |
