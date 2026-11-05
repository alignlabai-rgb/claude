# Caller-admission reference candidate (UNREVIEWED v0) — test-only, not an application

**Status:** a proposal and its tests. It is **not** an application, binder, executor or production integration; it adopts no schema or profile; nobody has reviewed it; ownership and adoption stay
with the existing owners. It changes no library file and touches no other branch (no merge, no cherry-pick).

Motivation: caller gaps 04 / 05 / 07 / 08 / 14 in `GAPS_AND_TASKS.md` (optional shallow receipts, dropped warnings, unchecked cross-stage references, exit 0 for non-ready, uncaught mis-shaped receipts).
Pinned inputs (`PINS.json`, blob hashes): `CONTRACT_JA.md` + `GAPS_AND_TASKS.md` at integration `a0acfdaf…`; the three producer schemas at core `c7da5ac…` and input `91ec2d40…` (byte-identical at both).

## What it is

`caller_admission.evaluate(expected, observed, profile) -> dict`, a pure function of synthetic **values**:

* `expected` — an owner snapshot: identity (job / work / shot / attempt / selection snapshot ref), `pins` (manifest, plan, receipt, stem, video SHA-256), `pins_source`, and the row set (`row_id`, `selected`, `state`, optional `binding_ref` / `decision_ref`).
* `observed` — stage packets `audio_plan`, `audio_receipt`, `video_receipt`, `avmux_manifest_view`, each carrying a caller identity envelope; the first three also `exit_code` and the producer's JSON `response`.
* `profile` — the explicit proposed policy (`PROFILE_V0_UNREVIEWED` is a value the caller must pass; there is no default parameter and a missing field is never filled in).

No file, environment variable, clock, subprocess or network is used (checked statically by a test); arguments are never mutated (checked on every call); there is no action or execute flag and no approval field in the result.

Result: `status` ∈ `READY | HOLD | REJECTED`, sorted `reasons[]` (`severity`, `code`, `path`, `detail`, bounded `expected`/`observed`), `checks[]` (each of the 11 comparison stages `PASS | FAIL | SKIPPED`, which is how a test proves it reached its
intended comparison), `evidence` (selected scope, unselected unresolved rows, preserved warnings / holds, which observed sources each pin was compared with) and `not_claimed`.

## Precedence (documented, deterministic)

1. any REJECT reason → **REJECTED**: structural contradiction, wrong-typed value (bool / float are not integer counts), pin / identity / selection / schema-version mismatch, status-vs-exit-code contradiction, empty or non-object response, inconsistent report (e.g. READY with a selected unresolved row);
2. else any HOLD reason → **HOLD**: missing or unknown state, absent required stage or receipt, non-ready or unknown status (readiness is the status, **not** exit 0), partial / incomplete receipt, untreated warning, unqualified silence, unresolved selected row, empty effective scope, unresolved policy;
3. else **READY**.

**READY means only:** *these synthetic values are mutually consistent under this UNREVIEWED profile* (`scope_of_status`). Not production execution, not creative acceptance, not authentication (a self-consistent forgery passes — tested and stated), not file verification (hashes are compared as supplied values; none is recomputed).

## Selected-scope meaning vs whole-row structure

Every plan row (selected or not) is structurally validated. Meaning is applied to **selected** rows only: `USE` (binding + `MIX` disposition) and `INTENTIONAL_SILENCE` (needs a `decision_ref`) form the *effective scope*; selected `OMIT` is valid, contributes nothing and does not block; selected `MISSING / UNKNOWN / PROXY` hold; an empty effective scope is non-READY. Unselected `UNKNOWN` rows never block and are preserved in `evidence.unselected_unresolved`.
Silence-only has **no exception**: the producer's `NO_AUDIO_EVENTS_SELECTED` warning is untreated by default, so a warned silence-only scope is HOLD; the warning-free, qualified `USE + INTENTIONAL_SILENCE` case is READY; a silence-only plan *without* that warning is a contradiction (REJECTED).

## Profile v0 (all values explicit; see `PROFILE_V0_UNREVIEWED`)

Required stages and pins (all five); accepted statuses `READY` (plan), `MIXED_VERIFIED` (audio receipt), `READBACK_MATCH` (video receipt); accepted exit codes `[0]`; **no** warning code is treated; silence needs a `decision_ref`; USE rows need an owner binding; required gap policy `REQUIRE_FULL_COVERAGE`; pins must be declared as coming from an `owner_record`; known schemas `video_core.audio_plan/1`, `video_core.audio_receipt/1`, `video_core.receipt/1` (same family, other version → REJECTED; anything else → HOLD).

## Source-contract ambiguities (stated, not decided)

1. Producer receipts carry **no** job / work / shot / attempt id; identity here is a caller-attached envelope per packet, i.e. an assertion (GAP-07 territory).
2. `binding_ref` ↔ `provenance_ref` is my mapping of the contract's "opaque binding reference" onto the plan row field; `CONTRACT_JA.md` lists `decision_ref / provenance_ref / selection.ref / source_id` without saying which is the binding.
3. `receipt_sha256` is a value the caller claims for the audio receipt bytes; the function cannot hash bytes. The video receipt's own hash is not pinned (the contract's list is ambiguous about which receipts get a pin).
4. No receipt JSON schema exists in the pinned schema files; the receipt / plan fixtures are shaped from producer code at `c7da5ac…` (representative subset plus ignored keys), not from a published schema, and no producer was run.
5. Hex casing: manifest `sha256` fields allow upper case by schema; producers emit lower case; the candidate compares lower-case 64-hex strings exactly.
6. `avmux_manifest_view` is a caller-derived view (`selection_ref`, video / audio sha256); the real avmux plan output is not modelled, and the input session's receipt parser at `91ec2d40…` is deliberately not consumed or duplicated.
7. Exit-code vocabularies differ per stage (GAP-10); the profile only lists accepted codes (`0`), everything else is non-READY.
8. Timing / phrase-boundary qualification has no carrier (GAP-06): READY says nothing about it. Execution permission and creative approval are owner records outside this function (GAP-11, by design).

## Tests

`test_caller_admission.py` — 68 unit / property tests on synthetic public values (positive control; warning-free explicit silence vs warned silence-only; unselected UNKNOWN + valid USE; selected OMIT + valid USE; selected / empty scope; single-field cross-job with identical hashes in every stage; every hash source and the owner pin; missing and incomplete receipts (4 patterns); status vs exit-code contradictions; partial, `all_ok=false`, float / bool counts; 6 malformed shapes; schema / profile handling; determinism, no mutation, no aliasing; static purity; the forgery boundary; and a property test that replaces every leaf of a conforming world with 15 hostile values: never an exception, never the safety-net code, and READY survives only on documented-ignored leaves).
`mutation_check.py` — 28 single-defect mutants of the function; every one must make the tests fail (28 / 28 killed; two early survivors found two real test gaps, now covered). A real defect was also found by the property test during development (a receipt with a null `plan_sha256` could pass because another source satisfied the pin) and fixed in the candidate.

```bash
cd tools/video-core
python3 -m unittest discover -s evidence/caller-reference-20261105 -p "test_*.py"
python3 evidence/caller-reference-20261105/mutation_check.py
```

## Not claimed / unresolved

No real file, story, voice or media was used. No integration with any executor exists or is proposed here. Whether the proposed profile, status vocabulary, identity envelope or pin model is right is for the existing owners to decide; this only shows that the comparison logic can be written as a small pure function whose behaviour is pinned by tests. Linux x86_64, Python 3.11 / 3.12 / 3.13 only; author's own checks, not an independent review.
