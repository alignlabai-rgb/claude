# portable-r2 — what changed against the closed `portable/` harness, and why

Additive version 2 candidate. **Nothing outside `portable-r2/` was edited**: `portable/` (harness, pinned-original profile and results), `portable-recovery/`, `portable-candidate-092b420/`, `portable-candidate-recovery/` and the library are byte-identical (`verify_scope_r2.py`).
The exact textual difference of the five copied harness files is in `R2_DIFF_vs_portable.patch` (`portable_core.py` +106/−17, `portable_harness.py` +77/−41, `portable_selftest.py` +99/−32, `portable_cases.py` +5/−3, **`child_runner.py` byte-identical: the deny hook is the same code**).
**Oracles are unchanged**: the only edit in `portable_cases.py` is where PC00 looks for the fixture index (below). No expected value, no layer, no fixture was altered to make any library pass.

## 1. H-1 — the comparison rule (`json_equiv.py`)
Finding H-1 (from `portable-candidate-092b420/CANDIDATE_REVIEW.md` §7): a reviewed expectation read from a JSON file was compared with `==` against the live observation, where tuples never equal lists; the in-memory self-test MS07 could not see it.
r2 uses **one** structural rule for every comparison (expected vs observed, and reviewed vs observed):
| Aspect | Rule |
|---|---|
| tuple / list | the same kind (intended) at every depth; order and length significant |
| scalars | `None`, `bool`, `int`, finite `float`, `str` — **types preserved**: `True≠1`, `1≠1.0`, `0.0≠-0.0`, `"1"≠1`, `None≠False`, `None≠missing key`, `[]≠{}≠None≠""` |
| objects | `str` keys; `int` keys (not `bool`) canonicalised to their decimal string exactly as JSON does, collisions rejected; key **set** and values significant, key order not (JSON objects are unordered) |
| unsupported | `bytes`, `set`, `Fraction`, `Decimal`, `datetime`, objects, subclasses of builtins, `NaN`/`±Infinity`, lone surrogates, non-str/int keys, nesting > 64 → `UnsupportedValue` → check outcome **ERROR**. Never stringified, hex-encoded or `repr()`ed |
Invariant (tested on 3000 seeded structures): `equivalent(x, json.loads(json.dumps(x)))` for every supported `x`; a single change anywhere after a round trip is always detected (1500 mutations). The result writer no longer has a `default=` fallback, so nothing is stringified on write either.

## 2. Profiles
* Strict validating loader (`load_profile_file`): ≤ 1 MB, no BOM, valid UTF-8, no duplicate keys, no `NaN`/`Infinity`, `profile_version` is the integer 1, four 64-hex pin digests, `reviewed_expectations` an object of entries with `observed` and a **non-empty `reviewed_by_ref`** (a missing reviewer reference is never assumed), no unknown entry fields. A bad profile is refused (exit 2) as a whole.
* Source-pin semantics unchanged (`PINNED_EXACT`, `PACKAGE_PINNED_OTHER_FILES_DIFFER`, `PINNED_MODULO_LINE_ENDINGS` apply; `UNPINNED` does not; ignored entries are listed in `run.json`).
* Reviewed entries can change only `LIBRARY_ENFORCED`/`LIBRARY_PERMISSIVE` outcomes. **Guard demos, harness controls and ERROR are never rescued.** A changed gap with no entry stays `REVIEW_REQUIRED`; a wrong or other-key entry changes nothing.
* An entry whose reference contains `NOT A REVIEW` or `SELF-TEST ONLY` applies as `REVIEWED_CHANGE` with `review_marker: NOT_A_REVIEW`, is listed in `run.json`, and makes the run exit **4** (never 0): a dry run cannot be mistaken for a reviewed result.
* New profile files in r2 are copies of the earlier pin profiles (byte-identical, checked) and **UNREVIEWED proposals** (`proposed_expectations`, ignored by the harness; `reviewed_expectations: {}`). No review is fabricated.

## 3. Deny-first admission and selection
* The deny controls (MS01: 16 forbidden actions attempted inside the hooked child, each denied before its side effect and checked for absence, plus the counterfactual and the read control) are **admission**: they run **before any contract case, other mechanism test or mutation control**. If they do not PASS the run STOPS with exit **5**, `STOP.txt`, and `run.json → admission.stopped`; `children_started` equals the admission's children.
* `--only` accepts contract (`PC*`), mechanism (`MS*`) and mutation (`MU*`) ids (the old `--only` refused `MS01…` before any child). Admission always runs first, once; selecting `MS01…` alone runs only the admission. `--list` shows the admission.
* Verified by a disposable, intentionally failing deny control (`MSX_DISPOSABLE_FAILING_DENY_CONTROL`): its only child **reads** a file the harness created and wrongly claims the read was denied. It performs no prohibited action and cannot permit one (the hook is the unchanged `child_runner.py`); the run stops after exactly 1 child. A real hook failure cannot be staged without allowing the action, so that path is covered by construction and by the unchanged positive controls, not by an artificial hook bypass.
* Mechanism-control children are now recorded like any invocation (bounded stdout/stderr/status, attributed to the current group).
* Unchanged: 10 s per child, 128 KiB per stream, flood kill, serial owned children, ≤ 700 children, budget (default 420 s, max 900), exclusive-create output, no media/network/write side effects in the tested child.

## 4. Smaller changes
* PC00 finds `FIXTURES_INDEX.json` in `portable-r2/fixtures/` first (byte copy of the closed job's), so a runtime-only copy no longer **skips** the cross-check (the Windows run of `8cab6e4` reported that skip).
* MS04 recorded a `bytes` value; r2 records text instead (nothing is stringified).
* MS05, MS07, MS08 rewritten: MS07 now writes the profile to disk, reads it back through the loader and checks tuple-rich observations (tuples inside a dict); MS08 tests the loader against 22 unsafe variants (and 2 valid ones).

## 5. Tests (`tests/`, stdlib `unittest`; `python -m unittest discover -s tests` from `portable-r2/`)
`test_json_equiv.py` 14 · `test_profile_disk.py` 11 (real files on disk, correct/wrong pins, nested arrays, 15 counterexamples, ERROR/GUARD_MISMATCH/CONTROL_FAILED never rescued, unknown gaps stay REVIEW_REQUIRED, loader rejections) · `test_admission.py` 7 (real bounded children, ~15 s): `--only MS01`, admission order, unknown ids refused, failing control stops everything, no prohibited action permitted, passing custom admission, NOT-A-REVIEW exit rule on a disposable library copy.
