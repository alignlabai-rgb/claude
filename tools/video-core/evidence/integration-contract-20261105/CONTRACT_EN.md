# Integration contract — owner-facing (EN)

Status: **design + executable contract tests of a generic public abstraction.** This is *not* adoption, *not* operational readiness and *not* a creative-quality claim.
Real story, voice and timing are unresolved and cannot be inferred from the synthetic fixtures used here. Nothing was rendered, mixed, muxed, probed or downloaded.
Every statement marked **[test X]** is pinned by a case in `contract_suite.py` and reproduced in `EXPECTED_VS_OBSERVED.json`.

Base: `b4ca4a3b031351a631c0fbe1eb149935f4db19ba` (branch `codex/video-core-integration-20261105`). Library: `tools/video-core` (`video_core` 0.1.0), unchanged.

## 1. Six things that must never be merged into one field

| # | Concern | Who holds it | Can the library carry it? | What the library does with a look-alike |
|---|---|---|---|---|
| 1 | **Input selection authority** — which rows are in play | existing selection/binding owner | only as a *derived explicit subset*: audiomix `scope.event_ids`; avmux = one video + one stem; video-only = the clip list you wrote | cannot tell who selected; no implicit "all" **[test b3b]** |
| 2 | **Adopted binding references** — which source/take a row is bound to | the same owner's records | opaque strings (`decision_ref`, `provenance_ref`, `selection.ref`, `source_id`) | echoed verbatim, 1–128 chars, **never authenticated** — an "APPROVED-BY…" string is just text **[test x1]** |
| 3 | **Technical measurements** | the library, where it says so | sha256 of files it reads; strict WAV facts; exact frame→sample arithmetic. ffprobe facts only at `avmux mux --mux` preflight (not run here) | declared values are labelled `CALLER_STATED_UNVERIFIED`; plan says `UNVERIFIED…` and still exits 0 **[test f1, f4]** |
| 4 | **Phrase-boundary / sample-timing qualification** — "these cut points were measured/accepted" | existing owner | **not at all** (GAP-06): `timing_qualification`, `qualified`, … are `unknown_field` | equal-length ranges are accepted however the boundaries were chosen **[test e1]** |
| 5 | **Execution permission** — "this exact plan may now run" | the person/system that invokes the CLI | **not at all** (GAP-11, by design): the only gate is a bare flag `--render/--mix/--mux` | none |
| 6 | **Creative approval** | existing owner | **not at all**: verdicts are `quality_evaluated:false`, `creative_acceptance:"NOT_EVALUATED"` | none; no output key mentions approval **[test x1]** |

Rule: a status string or hash inside an unauthenticated JSON receipt is **not** evidence of who produced or approved it. The AV-mux stage accepts a three-field hand-written
receipt, and an edited one whose own fields contradict its status **[test d3]**. Treat receipts as *consistency* evidence only; bind them to your own records (§5).

## 2. What each stage actually verifies

`validate`, `plan` and the default action never launch a process, touch the network or create a file or directory **[test p1: 12 commands × 2 PATH variants (sentinel stub / empty) = 24 invocations]**.
Checks marked ✔ are enforced by the library; “plan-only” means the check happens at plan, not at an execution this job did not run.

| Check | video-only (`video_core`) | audio (`audiomix`) | AV mux (`avmux`) |
|---|---|---|---|
| schema, strict JSON, integers, unknown fields refused | ✔ | ✔ every row, selected or not | ✔ |
| exact rational fps; boundaries from integers (no accumulation) | ✔ frames/duration | ✔ `floor((2·f·d·48000+n)/(2·n))` per absolute boundary **[a4]** | ✔ same rule, declared sample count must equal it **and** the WAV |
| selected **unresolved** input | cannot be expressed (GAP-01); missing file → reject `asset_missing` | ✔ **HOLD**, exit 5, never silence **[b1]** | reject `input_state_not_use` (exit 1) — HOLD survives only as a code (GAP-10) **[b5]** |
| unselected unresolved input | n/a (owner omits them) | ✔ never blocks; reported **[a2, b3]** | n/a |
| NULL / absent state | `type_error` | `invalid_enum` / `missing_field` — never defaulted **[b2]** | `invalid_enum` / `missing_field` **[b5]** |
| source content pinned by sha256 | ✘ **not at plan** (GAP-02); plan hash unchanged by a content swap **[c1]** | ✔ selected assets read, hashed, WAV-parsed **[c1]** | ✔ both files hashed **[c1]**; video hashed but never parsed |
| source/output collision, existing output, symlinked root | ✔ **[c2, c3]** | ✔ | ✔ |
| phrase/sample timing *qualification* | ✘ | ✘ (GAP-06) exact *length* equality only: ±1 sample refused **[a4, e2]** | ✘ |
| explicit silence | n/a | ✔ `INTENTIONAL_SILENCE` + `decision_ref`, counts as coverage; `OMIT`/absent do not **[s1]** | ✘ cannot tell a silent stem (GAP-05) **[s3]** |
| no duration/mix/stretch inference | ✔ no padding | ✔ no trim/pad/stretch, no implicit summing **[e2]** | ✔ no `-shortest/-t/-ss/-r/-vsync/-fps_mode/-ar/-af` in argv **[a3, s4]** |
| video properties | planned only | n/a | **declared only** at plan; measured only at `--mux` preflight **[f1]**; comparison logic shown on canned dicts only **[f2]** |
| receipt of the previous stage | none consumed (GAP-03) | n/a | optional; if given: status `MIXED_VERIFIED`, one non-partial stem with the stem sha256, `readback.wav.sample_count` — **nothing else** **[d1–d4]** |
| default `-an` | ✔ `-an -sn -dn`, no audio input; example plan hash `4bc20b3f…9f21` unchanged **[f3]** | no mux | separate argv; video `copy` |
| episode cross-check | – | ✔ optional `--episode` (fps, frames, episode sha256) **[a5]** | ✘ none (GAP-07) |
| process/dir creation in plan paths | none | none | none **[p1]** |

## 3. Caller responsibility table

“Library” = proven by a LIBRARY_ENFORCED check. “Owner” = must be done by the existing owner/caller; the test shows the library accepts the unguarded case (LIBRARY_PERMISSIVE) and an illustrative guard (CALLER_GUARD_DEMO).

| Responsibility | Library | **Owner / caller must** | Gap |
|---|---|---|---|
| Fix the selection snapshot; derive manifests from **selected rows only** | explicit scope; no implicit all | export a snapshot ref; never pass unselected rows to video-only/avmux | – |
| Refuse to call when a **selected** row is MISSING/UNKNOWN/PROXY/HOLD/NULL | audiomix HOLD; avmux rejects non-USE | stop *before* building the episode/manifest (video-only cannot say it); map codes to the owner's HOLD; **never** substitute a colour clip, proxy file or zero-filled WAV | 01, 10 |
| Keep NULL/UNKNOWN unresolved | NULL rejected; UNKNOWN→HOLD | map NULL→UNKNOWN or leave out of scope; no defaults | – |
| Resolve & store binding refs | echoes opaque text | resolve each ref in the owner's own record at call time; compare refs across stages | 07 |
| Qualify phrase/sample timing | exact arithmetic only | hold a QUALIFIED record per selected USE row; HOLD otherwise; an unqualified range is *accepted* by every stage | 06 |
| Pin source versions | sha256 in audiomix/avmux | record sha256 at selection; **also for video-only** (plan does not); re-check before each stage | 02, 13 |
| Use `REQUIRE_FULL_COVERAGE`; treat any plan warning as not-ready | HOLD on gaps; warns on zero-fill | never ship `ZERO_FILL_APPLIED` / `NO_AUDIO_EVENTS_SELECTED` as READY; absent audio ⇒ choose the video-only (`-an`) path, never a fabricated silent WAV | 09, 05 |
| State intentional silence explicitly | `INTENTIONAL_SILENCE`+`decision_ref` | own the decision; avmux cannot re-verify it | 05 |
| Read **status**, not exit code | JSON `status` / `readiness` | exit 0 ≠ ready: HOLD dry-run, `hold_if_planned`, zero-fill READY, UNVERIFIED all exit 0; exit 1 + empty stdout = REJECTED_UNSTRUCTURED | 08, 14 |
| Bind the audio receipt to its run | three shallow fields | require a non-null receipt; compare `plan_sha256`, `input.manifest_sha256`, `inputs_unchanged`, `readback.all_ok`, `written/stage`, `warnings` with **your own record of the run**; accept that even then provenance is unproven | 04, 05, 07 |
| Bind the video render receipt | no slot | check `READBACK_MATCH`, non-partial output sha256 == AV manifest video sha256 before building it | 03 |
| Treat IN_PROGRESS / UNKNOWN / `.partial` / HOLD as UNKNOWN | refused by avmux when a receipt is given | never hand them on; a stem of an UNKNOWN run is accepted if the receipt is omitted | 04 |
| Hand off the stem | outputs may not sit inside the asset root | copy to the AV stage's single asset root, re-hash, then build the manifest | 12 |
| Fresh output names; keep previous outputs | refuses existing/overlapping outputs; never resumes **[c3]** | pick a new name per attempt; keep failed dirs as evidence | – |
| Hold execution permission & creative approval | no carrier | keep as owner records bound to the exact `plan_sha256`; pass flags only after both | 11 |
| Roll back | none (fresh dirs only) | restore pointers/copies from a hashed backup; verify hashes **[g3]** | – |

## 4. Propagation rules (failure, missing qualification, rollback)

1. **Nothing is repaired downstream.** Upstream HOLD/UNKNOWN/partial/clamped/mismatch outputs are refused or must be refused by the owner; the library never turns them into silence, a proxy or a default. **[b1, d1, g2]**
2. **HOLD stays HOLD.** audiomix reports it with exit 5 on `plan` and with exit **0** but `status:"HOLD"` on the `mix` dry-run; avmux reports the same condition as exit 1 / `input_state_not_use`. Owners map all three to one HOLD and stop. **[b1, b5]**
3. **A missing qualification has no representation.** It must be checked before building any manifest; if missing, the call is not made. **[e1]**
4. **Unstructured failures exist.** A receipt of the wrong JSON shape crashes `avmux plan` (exit 1, empty stdout, traceback); nothing is created. **[d2b]**
5. **Source version pinning** is a sha256 chain checked at plan time; a consistent re-pin of manifest *and* receipt is accepted. **[g1]**
6. **Previous outputs are immutable**: every stage refuses an existing output name, including a stale `IN_PROGRESS` directory, and leaves its bytes untouched. **[c3]**
7. **Rollback** is owner work: restore the previous pointer/copy from a hashed backup, delete only the failed candidate, re-verify the original hashes and re-run `plan` — same `plan_sha256` as before. Demonstrated on isolated temp copies of synthetic fixtures by two methods (rename swap, copy-restore). No production state was read or changed. **[g3, `rollback/ROLLBACK_EVIDENCE.json`]**

## 5. Interface pseudocode for owners (DESIGN ONLY — not implemented, not a module)

```text
# Names are illustrative. Nothing here exists in video_core or in this directory.
OwnerRequest {
  request_ref
  selection_snapshot: { ref, rows: [ {row_id, selected, state, binding_ref,
                                      source: {path, sha256},
                                      timing: {status: QUALIFIED|UNQUALIFIED|UNKNOWN, qualification_ref},
                                      silence: {decision_ref}} ] }
  execution_permission: { granted, plan_sha256, ref }     # owner record; never passed to video_core
  creative_approval:    { status, ref }                   # owner record; never passed to video_core
}
caller steps:
  1 every SELECTED row in {USE, INTENTIONAL_SILENCE, OMIT}, USE rows pinned + QUALIFIED + binding_ref resolvable   else HOLD, stop
  2 re-hash every selected file == pin                                                                              else REJECT, stop
  3 build episode / audio manifest / avmux manifest from SELECTED rows only (pure data transform; policy.gap = REQUIRE_FULL_COVERAGE)
  4 video_core validate,plan · audiomix validate,plan (+ --episode) → read JSON status; map READY / HOLD / UNVERIFIED / REJECTED / REJECTED_UNSTRUCTURED
  5 any plan warning, HOLD or UNVERIFIED-where-verified-is-required ⇒ stop; else permission.plan_sha256 == plan_sha256 ?   else HOLD
  6 execute with fresh output names; read each receipt's status; non-verified ⇒ stop, keep dir as evidence
  7 hand off: copy stem to the AV asset root, re-hash; bind receipt to YOUR record (plan_sha256, manifest hash, inputs_unchanged, readback.all_ok, warnings)
  8 avmux --mux preflight measures the video; READBACK is technical only
  9 promote only after creative_approval is an owner record; rollback = restore previous pointer from hashed backup
```

## 6. Genuine contract gaps (the API cannot express the requirement)

See `GAPS_AND_TASKS.md` for evidence and suggested tasks. Summary: **GAP-01** no input-state vocabulary in video-only · **02** video-only plan pins no source hash · **03** no video-receipt slot ·
**04** audio receipt optional, shallow, forgeable · **05** receipt warnings dropped / silent stem indistinguishable · **06** no timing-qualification field · **07** refs/scope not cross-checked ·
**08** exit 0 for non-ready · **09** `ALLOW_ZERO_FILL` · **10** HOLD vocabulary differs per stage · **11** no permission/approval carrier (by design) · **12** stem handoff geometry (owner task) ·
**13** TOCTOU not exercised · **14** uncaught `AttributeError` on a mis-shaped receipt.
No compatibility was invented: where the API cannot express a distinction the test pins the *actual* behaviour and names the gap.

## 7. What is not claimed

No ffmpeg/ffprobe/render/mix/mux/download ran; the “videos” are plain non-media bytes and the audio receipt is harness-fabricated (so the library's own receipts are not exercised). The `mux --mux` preflight was exercised only as a pure function on canned dicts **[test f2]**.
Linux x86_64 and Python 3.11/3.12/3.13 only. Author checks — not an independent review.
