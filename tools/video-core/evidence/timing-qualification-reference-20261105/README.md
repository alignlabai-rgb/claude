# GAP-06 timing-qualification carrier - reference PROPOSAL (UNREVIEWED, test-only)

Additive, supplemental, synthetic. **No** app, binder, executor, library, schema or spec change; **no** integration with the core caller candidate; no branch merge; no adopted profile.
Basis: only the public `CONTRACT_JA.md` / `GAPS_AND_TASKS.md` at `a0acfdaf3409ff09e172865210e2878ad4962680` - GAP-06 ("no phrase-boundary / sample-timing qualification field"; `timing_qualification`, `phrase_boundary`,
`qualified` are `unknown_field` in the old schemas), SIT-02 ("a timing-qualification record per USE row: `status`, `kind`, `qualification_ref`, gate manifest creation on `QUALIFIED`"), LIB-06 (an opaque `timing_ref`: "still an assertion,
not evidence") and step 1 of the caller pseudocode ("USE rows pinned + QUALIFIED + resolvable binding_ref, otherwise HOLD and stop"). The carrier therefore lives **outside** every production manifest, as plain values the owner keeps.
A scope check by an existing PM found no overlapping implementation work; that is a scope check, not adoption authority.

## What it is

`timing_carrier_reference.compare_carrier(expected, observed)` - a pure stdlib function (imports `hashlib`, `json`, `re` only; no I/O, env, clock, network or subprocess; never mutates its inputs; never raises).
It **compares supplied claims**. It does not listen to voice, find or measure phrase boundaries, issue or authenticate a qualification, decide that speech is suitable, or grant execution or creative acceptance.
There is no READY, execute or approval field. A matching `QUALIFIED` status with matching kind, ref and hashes is **not** proof of speech suitability.

* `expected` - the owner's selection/qualification snapshot: `snapshot_ref`, `snapshot_version`, `sample_rate`, `rows[]` (`row_id`, `event_id`, `selected`, `state` in USE / INTENTIONAL_SILENCE / OMIT / MISSING / UNKNOWN / PROXY / HOLD, and for a selected USE row `binding_ref`, `source_sha256`,
  `source_in/out_sample`, `timeline_start/end_sample`, `qualification_revision`; for a selected INTENTIONAL_SILENCE row `decision_ref` and the timeline range).
* `observed` - the carrier: `carrier_version`, `profile_id/version/content_sha256`, `snapshot_ref/version`, `records[]` (per SIT-02 explicit `status`, `kind`, `qualification_ref`, plus the pins the claim is about: row/event/binding/source sha/rate/boundaries, `qualification_revision`,
  `invalidation`), and separately supplied `silence_decisions[]` (`row_id`, `decision_ref`).
* Result: `verdict` = `CONSISTENT_ASSERTIONS_ONLY` | `HOLD` | `REJECTED` (precedence REJECTED > HOLD > consistent), `reasons[]` (code, severity, stage, row, comparison, detail), `comparisons[]` (every comparison that was actually reached: expected vs observed vs outcome
  MATCH / MISMATCH / MISSING / NOT_APPLICABLE), `stages_reached`, per-row `row_outcomes` (`CLAIMS_CONSISTENT` / `HOLD` / `REJECTED` / `CONTRIBUTES_NOTHING`), `limitations`, `not_asserted`. Stages: PROFILE -> STRUCTURE -> CARRIER -> CROSS_RECORD -> ROW_COMPARISON;
  a rejection before ROW_COMPARISON means no row comparison produced the verdict (tests check this, so a negative cannot pass by being an earlier invalid fixture).

## Fixed-semantics profile (UNREVIEWED proposal) and its pin

`PROFILE` holds the vocabulary, limits and the rules marked **P-1 ... P-10** (all *proposed*). Its canonical content hash is pinned as a literal (`PINNED_PROFILE_SHA256`); the function refuses (REJECTED `PROFILE_CONTENT_MISMATCH`) any profile whose content differs,
and the carrier must repeat id, version and content hash. A changed profile therefore needs a new id/version and a new pin; it cannot silently relax a requirement under the same identity (tests: five relaxations of the same id/version are rejected).

| Situation | Verdict |
|---|---|
| selected USE, one record, all pins equal, `QUALIFIED`, explicit kind and ref, current revision, not invalidated; unselected rows and selected OMIT contribute nothing; selected silence has the owner's decision | CONSISTENT_ASSERTIONS_ONLY |
| selected USE without record; status UNQUALIFIED/UNKNOWN; stale `qualification_revision`; supplied `INVALIDATED`; selected MISSING/UNKNOWN/PROXY/HOLD; selected silence without a decision; empty effective scope | HOLD |
| any pin differs (row/event/binding/source sha/sample rate/source or timeline boundary - **a shift by one sample with the same duration is a mismatch**); stale snapshot ref/version; carrier/profile version or content; different silence decision_ref; a record for a silence/OMIT/unselected/unknown row; duplicates; malformed or oversized input | REJECTED |

Normal selection stays separate from structure: an unselected UNKNOWN or a selected OMIT is valid, does not block a qualified USE and contributes no audio and **no implied silence**. Silence is only explicit (`INTENTIONAL_SILENCE` + the owner's `decision_ref`), never an invented phrase qualification, never inferred from absence.
Strict integers (no bool/float), bounded rows/records/decisions (256), strings (128), depth (5), nodes (20000), JSON-safe finite output. No zero-fill exception and no warning suppression exists in the profile.

## Unsettled - stated, not decided here

Who issues a qualification and how it is authenticated; expiry; revocation (the supplied `invalidation` flag and `qualification_revision` are only *compared*: no clock, issuer, approval or lifecycle is inferred); the kind vocabulary (`SAMPLE_BOUNDARY_OWNER_CHECKED`, `PHRASE_BOUNDARY_OWNER_CHECKED` are placeholders);
the `qualification_ref` syntax (`^tq:[A-Za-z0-9._-]{1,64}$` is a placeholder); requalification rules (this profile only says a changed source/boundary needs new pins and a new record - not who requalifies or when); whether one ref may cover several rows (not restricted);
whether a UNQUALIFIED/UNKNOWN record may carry a real ref (allowed); whether a snapshot_version bump invalidates old claims (here: stale claims are REJECTED); where the carrier is stored. Every P-n rule is a proposal for the owners to accept, change or drop.

## Limits that stay

* **A self-consistent forged claim set is accepted** (test `forged_self_consistent_claims_are_accepted_documented_limit`): nothing here can tell a claim set copied from the owner's pins from a real one.
* No coverage/overlap/gap check and no check that source or timeline boundaries are phrase boundaries; no hashing of media; the owner still re-hashes sources and the library's `REQUIRE_FULL_COVERAGE`/HOLD handling stays separate.
* Synthetic values only (hex digests `aaaa...`, refs `snap-0001`, `owner-silence-1`); no real story, dialogue, voice, media or user path.

## Counts (derived by running the suite: `count_cases.py`, `results/case-counts-py*.json`; identical on 3.11 / 3.12 / 3.13)

17 test methods. Executed sub-cases: **35** in the negative table (12 expected HOLD + 23 expected REJECTED; this is the only "negative example" count - earlier mentions of 33 were a miscount), **40** malformed-shape cases, **5** profile relaxations under the same id/version,
**11** structure/limit cases (subclass, cycle, depth, size, wrong container types) - 91 sub-cases in total. `test_missing_and_extra_keys` loops over 11 key mutations without sub-tests (not in the 91), and the fuzz test crosses 14 x 14 odd values. 9 mutants, all killed.

## Run

```bash
cd tools/video-core/evidence/timing-qualification-reference-20261105
python3 -B -W error::ResourceWarning -m unittest discover -s tests -v 2>err.txt; echo "exit $?"; cat err.txt   # exit 0 is NOT proof of "no warning": an unclosed handle is an unraisable ResourceWarning on stderr; read err.txt
python3 -B mutation_controls.py --out /tmp/mutation.json        # 9 targeted mutants of a COPY; the child suites run with -W always::ResourceWarning and any "Warning" line fails the run
python3 -B count_cases.py --out /tmp/counts.json                # actual executed sub-case counts
python3 -B verify_scope.py                                      # only this directory was added
```
Files: `timing_carrier_reference.py`, `tests/test_carrier.py`, `mutation_controls.py`, `count_cases.py`, `verify_scope.py`, `results/`.

## Results files (kept, never overwritten)
`results/unittest-py*.txt`, `results/mutation-py*.{json,txt}` = the first run at 1775e9a (their runner had merged and tail-trimmed stderr). `results/prefix-warning-unittest-py*.{stdout,stderr}.txt` = the unclosed-handle `ResourceWarning`
at `tests/test_carrier.py:370` (`open(...).read()`) reproduced BEFORE the fix, on all three interpreters. `results/*-fixed-py*` and `case-counts-py*.json` = after the fix, with stderr captured separately and empty of warnings.
