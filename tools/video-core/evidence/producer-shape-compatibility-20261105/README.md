# Producer-shape compatibility audit of the caller-admission candidate (`55ce8bc`)

Question: do the **already committed** producer JSON files have the shape the (UNREVIEWED, proposal-only) caller candidate compares, and where do they not? Read-only and pure: the script opens the committed JSON,
never writes to it, and calls the candidate's pure function. No fake tool, no media, no re-run, no download. It is **not** an adoption test, not authentication, not a quality or completeness proof, and the candidate / profile /
specs / schemas / earlier evidence are unchanged (`git diff 55ce8bc -- tools/video-core/evidence` outside this directory is empty).

## Inputs (unchanged; `git_blob` = git's own object id, checked equal to `git ls-tree 55ce8bc`)

| name | path | bytes | git blob | SHA-256 |
|---|---|---|---|---|
| `video_receipt_6s` | `evidence/receipt.synthetic-6s.json` | 12739 | `a31eed68bb40a3be7cb449cf9e3b7e24bc52bd34` | `28a097f1da49d57492022215d9cfca9f331d7b1a81131d443b86643d21b49f59` |
| `video_plan_6s` | `evidence/plan.canonical.json` | 2306 | `125b8a477b04cd5b21a294d18784dd257ffa04fb` | `4bc20b3fd703f46651c868c1929efd3d9df3fc3c0a99f86607502ca92d5d9f21` |
| `audio_receipt_stem` | `evidence/stage-b-audio-stem/receipt.stem-demo.json` | 3914 | `8ebca373ff483190eabfa98c25ba7977d5c150fd` | `f87030ea130a55805a400454b48c00fa7d88ab7ceb3313ff36a10df1a37c1711` |
| `audio_plan_stem` | `evidence/stage-b-audio-stem/plan.canonical.json` | 2804 | `8f77425e1bde6cec2e535301ed422c3020235175` | `a897549b8c56c5b474952420059db3adfda8aaf458093ee49373fd82eb90228e` |
| `audio_manifest_stem` | `evidence/stage-b-audio-stem/manifest.synthetic.json` | 1602 | `9f42579833029ac8c44c55109fb360bdf0ef3509` | `d7b7a2b1dcc77681c3f4698763a2c570cb5e7fa2625bf9dbdd3c2aa6bc9dd737` |
| `audio_stem_independent` | `evidence/stage-b-audio-stem/independent_verification.json` | 2642 | `8e187e33950fe745ffd681bc81f5d7492642c34e` | `af9eddba6984400d0cc9c1cb1eaf48f8f83645435b98d7b56663cef762f1eb1b` |
| `avmux_manifest_mov` | `evidence/avmux-synthetic/mov.manifest.json` | 678 | `a48de7e278bf188ef32bdcc1f8a5192fb26a5f0b` | `8c10c1a1f4480b0062ba8eac09038adebb39039fdcf55b5698ed0e30bd2c29c6` |
| `avmux_receipt_mov` | `evidence/avmux-synthetic/mov.receipt.json` | 28259 | `b58923a018de96fb0a384c92a16f740341b0c73c` | `f8d7795315ff5ad9e779de5d178f78758bfa0fc75630719f0fc74b94e831d5ed` |

## Rules kept

* No value of any producer file is rewritten to reach READY (status, counts, hashes, durations, identity-bearing refs are as committed). `test_no_case_is_ready_and_nothing_was_rewritten…` compares the packets with the committed documents.
* The 6 s video demonstration (180 frames, 640x360) and the Stage-B stem demonstration (3 frames, 4800 samples = 0.1 s) are **different jobs** and are never merged; they are paired only in X1 / X2, which are expected to fail.
* What the producers do not carry is a **synthetic assertion** made by the script and listed per case in `result.json` (`synthetic_assertions`): the caller identity envelope (only the avmux `selection.ref` is a committed value), the CLI exit code 0, the owner's expected snapshot and pins
  (computed from file bytes or the independent verification record, so not copied from a receipt's own claim — except the 6 s **video pin**, which has no independent committed record and therefore equals the receipt's own output hash: a tautology, labelled `receipt_own_value`),
  and the `audio_plan` response envelope (built mechanically around the unmodified committed canonical plan: status = the plan's status, `plan_sha256` = SHA-256 of the file bytes).
* Missing stages stay HOLD; an incompatible geometry stays incompatible. The profile is not relaxed.

## Cases (max 10). Reached comparisons: `plan`=AUDIO_PLAN, `sel`=SELECTION, `arcpt`=AUDIO_RECEIPT, `vrcpt`=VIDEO_RECEIPT, `view`=MANIFEST_VIEW, `pins`=PINS; `P` pass, `F` fail, `S` skipped (prerequisite absent)

| id | case | status | reached comparisons | reason codes |
|---|---|---|---|---|
| A1 | Stage-B audio plan + audio receipt (real shapes), owner binding not asserted | **HOLD** | plan=P sel=F arcpt=P vrcpt=S view=S pins=F | BINDING_UNRESOLVED, OBSERVED_PIN_MISSING, REQUIRED_RECEIPT_ABSENT, REQUIRED_STAGE_ABSENT |
| A2 | same, with an owner binding_ref asserted for the two USE rows | **HOLD** | plan=P sel=F arcpt=P vrcpt=S view=S pins=F | BINDING_UNPROVEN, OBSERVED_PIN_MISSING, REQUIRED_RECEIPT_ABSENT, REQUIRED_STAGE_ABSENT |
| V1 | 6 s video render receipt (real shape) only | **HOLD** | plan=S sel=S arcpt=S vrcpt=P view=S pins=F | OBSERVED_PIN_MISSING, REQUIRED_RECEIPT_ABSENT, REQUIRED_STAGE_ABSENT |
| M1 | avmux manifest view (committed mov.manifest.json) + Stage-B stem receipt/plan | **HOLD** | plan=P sel=F arcpt=P vrcpt=S view=P pins=P | BINDING_UNRESOLVED, REQUIRED_RECEIPT_ABSENT |
| X1 | 6 s video receipt + Stage-B audio plan/receipt as DIFFERENT jobs (identities differ) | **REJECTED** | plan=P sel=F arcpt=P vrcpt=F view=S pins=F | BINDING_UNRESOLVED, COUNT_MISMATCH, IDENTITY_MISMATCH, PIN_MISMATCH, REQUIRED_STAGE_ABSENT |
| X2 | 6 s video receipt + Stage-B audio forced under ONE asserted identity (negative control only) | **REJECTED** | plan=P sel=F arcpt=P vrcpt=F view=S pins=F | BINDING_UNRESOLVED, COUNT_MISMATCH, PIN_MISMATCH, REQUIRED_STAGE_ABSENT |
| X3 | avmux world as committed: Stage-B stem + 3-frame clip manifest, no video render receipt | **HOLD** | plan=P sel=F arcpt=P vrcpt=S view=P pins=P | BINDING_UNRESOLVED, REQUIRED_RECEIPT_ABSENT |
| S1 | avmux receipt substituted into the audio_receipt slot | **REJECTED** | plan=P sel=F arcpt=F vrcpt=S view=S pins=F | BINDING_UNRESOLVED, OBSERVED_PIN_MISSING, OUTPUT_MISSING, OUTPUT_ROLE_UNKNOWN, PIN_MISMATCH, READBACK_EVIDENCE_MISSING, RECEIPT_LINK_MISSING, REQUIRED_RECEIPT_ABSENT, REQUIRED_STAGE_ABSENT, SCHEMA_UNKNOWN, SHAPE_COUNT, SHAPE_UNRESOLVED, SHAPE_WARNING, STATUS_UNKNOWN |
| S2 | 6 s video canonical plan substituted into the audio_plan slot | **REJECTED** | plan=F sel=S arcpt=P vrcpt=S view=S pins=F | OBSERVED_PIN_MISSING, REQUIRED_RECEIPT_ABSENT, REQUIRED_STAGE_ABSENT, SCHEMA_UNKNOWN, SHAPE_COUNT, SHAPE_PLAN, SHAPE_STATUS |
| P1 | Stage-B audio packets against an owner plan pin that is the 6 s video plan's hash | **REJECTED** | plan=P sel=F arcpt=P vrcpt=S view=S pins=F | BINDING_UNRESOLVED, OBSERVED_PIN_MISSING, PIN_MISMATCH, REQUIRED_RECEIPT_ABSENT, REQUIRED_STAGE_ABSENT |

`result.json` has the per-case source list, assertions, exact reason paths and every comparison outcome.

## Compatibility, per comparison of the candidate

| candidate comparison | real producer shape | finding |
|---|---|---|
| AUDIO_PLAN | committed canonical audio plan (`video_core.audio_plan/1`) | **fits**: schema, row structure, scope == selected rows, timeline formula, output geometry, coverage, unresolved list, warnings all pass (A1, A2, M1, X3) |
| SELECTION (v0 binding) | plan events carry `decision_ref` but **no `provenance_ref`** | **does not fit**: a v0 selected USE row needs a binding; without one `BINDING_UNRESOLVED`, with an owner-asserted one `BINDING_UNPROVEN` (A1 / A2). The requirement is not relaxed; the producer output cannot satisfy it as committed |
| AUDIO_RECEIPT | committed Stage-B mix receipt | **fits**: outputs (exactly the 5 keys), plan output == plan_sha256, stem geometry, five readback checks, wav facts, unresolved report == plan's all pass |
| VIDEO_RECEIPT | committed 6 s render receipt | **fits** (V1): required readback checks (12 incl. `decoded_frame_count` string observed), flags, outputs, plan output == plan_sha256 pass |
| MANIFEST_VIEW | committed avmux manifest | derivable mechanically (M1); the candidate has no stage for an **avmux receipt** (S1 shows it is not an audio receipt) — it models avmux only through the caller-derived view |
| PINS | file-byte hashes + independent stem record | plan / manifest / receipt / stem pins agree with the receipts; the 6 s video pin has no independent record (tautology) |
| whole job | — | **no READY is reachable** from the committed evidence: the audio job has no video render receipt for the 3-frame clip (the 3-frame clip was produced by `make_inputs`, not by a `video_core` render), and the only video render receipt is a different, 180-frame job |

## Run

```bash
cd tools/video-core
python3 evidence/producer-shape-compatibility-20261105/audit_producer_shape.py --out /tmp/result.json   # exclusive create
python3 -m unittest discover -s evidence/producer-shape-compatibility-20261105 -p "test_*.py"             # 8 short tests
```

Limits: 10 hand-chosen cases over 8 files, not a coverage claim; the exit code, identity and owner snapshot are assertions; Linux x86_64 only; author's own check, not a third-party or listening / viewing review.
