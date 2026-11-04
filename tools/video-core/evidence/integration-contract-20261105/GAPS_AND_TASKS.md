# Concrete unresolved gaps and suggested tasks for the existing owners

Scope: what an owner must supply or decide before the library can be called safely. **Nothing here was implemented**; no existing code or spec was changed.
Evidence column names the test case in `contract_suite.py` (details: `EXPECTED_VS_OBSERVED.json`). “Pinned” = the suite asserts the current behaviour, so closing the gap makes that test fail loudly
(demonstrated by `results/MUTATION_SELF_CHECK.json`, M3/M4).

Priority: **P1** can let an unqualified, unpinned or unowned input be executed without any library signal · **P2** easy to misread a non-ready outcome as ready · **P3** boundary / documentation.

## Gaps

| ID | Pri | Gap (API cannot express / does not check) | Actual behaviour (observed) | Evidence |
|---|---|---|---|---|
| GAP-01 | P1 | video-only has no input-state vocabulary or binding ref | `state`/`binding_ref` fields → `unknown_field`; a `proxy_clip_a.mp4` and a colour stand-in for an unresolved clip → exit 0 | b6 |
| GAP-02 | P1 | video-only plan pins no source content | same-size byte swap → identical `plan_sha256`; no `sha256` anywhere in plan/observations | c1 |
| GAP-03 | P1 | no slot for a video render receipt / qualification in the AV manifest | `video.receipt`, `video.render_receipt`, `top.video_receipt` → `unknown_field`; video bound by path+sha256, hashed but never parsed by plan | d5, f1 |
| GAP-04 | P1 | audio receipt optional and shallow; no ownership/authentication | none, 3-field forged, and self-contradicting edited receipts → exit 0; only `status`, stem sha256, `readback.wav.sample_count` read; stem of an UNKNOWN run accepted if receipt omitted | d3, g1, g2 |
| GAP-05 | P2 | warnings and unresolved report of the audio run are dropped; silent stem indistinguishable | receipt with `ZERO_FILL_APPLIED`/`NO_AUDIO_EVENTS_SELECTED` and a selected UNKNOWN in `unresolved_report` → exit 0; plan keeps only path/sha256/status/plan_sha256 | s3 |
| GAP-06 | P1 | no phrase-boundary / sample-timing qualification field | `timing_qualification`, `phrase_boundary`, `qualified` → `unknown_field`; mid-phrase shifted range and USE rows without refs → READY | e1 |
| GAP-07 | P2 | `selection.ref`, `decision_ref`, scope, plan/manifest hashes not cross-checked between stages; avmux has no `--episode` | any `selection.ref` accepted; receipt with another `plan_sha256` accepted; plan has no scope | a5, d4 |
| GAP-08 | P2 | exit 0 for non-ready | `mix` dry-run of HOLD plan → 0 with `status:"HOLD"`; `validate` with `hold_if_planned` → 0; READY with zero-fill warning → 0; avmux UNVERIFIED → 0 | b1, f4 |
| GAP-09 | P2 | `policy.gap=ALLOW_ZERO_FILL` converts absent audio into zeros | READY + warning; all-absent scope READY (`NO_AUDIO_EVENTS_SELECTED`) | s2 |
| GAP-10 | P3 | HOLD vocabulary differs per stage | audiomix HOLD/5; video-only & avmux reject/1 with codes | b2, b5, b6 |
| GAP-11 | P3 | no carrier for execution permission or creative approval (**by design**) | `execution_permission`, `creative_approval`, `approved_by` → `unknown_field` in all three schemas | x1 |
| GAP-12 | P3 | stem handoff geometry is nobody's job | avmux output may not sit in its asset root; audiomix cannot write into its own | c2 |
| GAP-13 | P3 | TOCTOU: pinning detects, does not prevent; post-run re-hash **not exercised** (no mix/mux run) | – | g1, g3 (documented only) |
| GAP-14 | P2 | mis-shaped receipt JSON → uncaught `AttributeError` | exit 1, empty stdout, traceback, nothing created (indistinguishable from a rejection by exit code) | d2b |

Behavioural observations (not gaps): **OB-01** unselected audio rows must still be schema-valid but are never read/hashed/overlap-checked (b3); **OB-02** audiomix/avmux plans hash and parse what they read, video-only plan only stats (c1).

## Suggested integration tasks (each is a proposal for the owner to decide; effort/priority are mine)

### Selection / binding exporter owner
| Task | Does | Closes / mitigates |
|---|---|---|
| SIT-01 | Export every row with `state ∈ {USE, INTENTIONAL_SILENCE, OMIT, MISSING, UNKNOWN, PROXY, HOLD}` (never null), a stable `row_id` equal to the audio event id, and a snapshot ref. Selected rows with an unresolved state stop the export (HOLD record), they are not exported as manifests. | 01, 10 |
| SIT-02 | Export a timing-qualification record per USE row (`status`, `kind`, `qualification_ref`) and gate manifest creation on `QUALIFIED`. | 06 |
| SIT-03 | Export silence only as explicit `INTENTIONAL_SILENCE` rows with a `decision_ref`; always set `policy.gap = REQUIRE_FULL_COVERAGE`; no zero-fill. | 09, 05 |
| SIT-04 | Export a sha256 pin for every selected file, **including video-only sources**, taken at selection time. | 02, 13 |

### Ingestion application owner
| Task | Does | Closes / mitigates |
|---|---|---|
| SIT-05 | Expose finished sources read-only with sha256; mark proxies/partials in the *export* (never rely on file names); never leave a `.partial`/in-progress file under a media extension in the asset root. | 01, 04 |
| SIT-06 | Stage layout: read-only `assets/`; `stage_audio_out/` (fresh); `mux_inputs/` (copy of stem + receipt + video, re-hashed after copy); `mux_out/` (fresh). One new output name per attempt. | 12 |

### Caller / orchestrating owner (whoever runs the CLIs)
| Task | Does | Closes / mitigates |
|---|---|---|
| SIT-07 | Status reader: parse JSON `status`/`readiness`/`plan.warnings`; map to READY / HOLD / UNVERIFIED / REJECTED / REJECTED_UNSTRUCTURED (exit ≠ 0 with empty stdout). Never branch on exit code alone. | 08, 14 |
| SIT-08 | Keep, in the owner's *existing* record store, `plan_sha256`, manifest sha256, receipt sha256 and stem sha256 per run; before building the AV manifest compare them with the receipt (`plan_sha256`, `input.manifest_sha256`, `inputs_unchanged`, `readback.all_ok`, `written/stage`, `warnings`). Require a non-null receipt. | 04, 05, 07 |
| SIT-09 | Check the video render receipt (`READBACK_MATCH`, non-partial output sha256 == AV manifest video sha256) before building the AV manifest. | 03 |
| SIT-10 | Hold execution permission bound to the exact `plan_sha256` and creative approval as separate owner records; pass `--render/--mix/--mux` only after both. | 11 |
| SIT-11 | Failure/rollback runbook: new output name per attempt, keep failed dirs, restore pointers from a hashed backup, re-verify original hashes and re-plan (pattern shown in `rollback/ROLLBACK_EVIDENCE.json`). | – |

### Library owner (proposals only — need their own design, spec and review; not part of this job)
| Task | Proposal | Closes |
|---|---|---|
| LIB-01 | avmux: policy to require `audio.receipt`; compare `plan_sha256`, `input.manifest_sha256`, `inputs_unchanged`, `readback.all_ok`, `written`, `stage`; surface `warnings` and `unresolved_report` in the plan. (Still unauthenticated; says so.) | 04, 05, 07 |
| LIB-02 | Turn mis-shaped receipts into structured `audio_receipt_unreadable` issues. | 14 |
| LIB-03 | video-only: optional per-source `sha256` in the episode, verified and echoed in the plan; avmux: optional `video.receipt` slot. | 02, 03 |
| LIB-04 | Distinct exit codes (or a flag) for HOLD dry-run, `hold_if_planned`, zero-fill READY and UNVERIFIED. | 08 |
| LIB-05 | Forbid `ALLOW_ZERO_FILL` unless an explicit `zero_fill_decision_ref` is present. | 09 |
| LIB-06 | An opaque `timing_ref` echoed in the audio plan with a “required when USE” policy (still an assertion, not evidence). | 06 |

Suggested order: SIT-01, SIT-04, SIT-02, SIT-08, SIT-07, SIT-09, SIT-03, SIT-10, SIT-05/06/11; then LIB-01 and LIB-02 (smallest, highest leverage). Real story/voice/timing remain unresolved and are the owners' decisions.
