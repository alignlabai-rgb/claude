# portable-candidate-092b420 — source-pinned, UNREVIEWED candidate evidence (input-contract commit 092b420)

Read `CANDIDATE_REVIEW.md` first. Additive to the closed `../portable/` harness, which is imported **unchanged**; nothing under `../portable/` (including the pinned-original profile and results) was touched. No library file was edited.
Not a review, not adoption, no Windows claim. Strict result changes stay `REVIEW_REQUIRED` / `FAIL` until a human moves an entry into `reviewed_expectations`.

| File | Role |
|---|---|
| `CANDIDATE_REVIEW.md` | pin, attribution, coverage of the five findings, every changed check explained, the three receipt classes, what a profile can/cannot resolve, Windows commands |
| `profiles/source_pin_092b420.json` | source pin only (no proposals, no reviews) |
| `profiles/proposal_asis_092b420.UNREVIEWED.json`, `profiles/proposal_forgery_control_092b420.UNREVIEWED.json` | proposals under `proposed_expectations` (ignored by the harness); `reviewed_expectations` empty |
| `check_pin.py` | prints the complete inventory digests of a library root and compares them with a pin |
| `run_with_forgery_control.py` | the closed harness with one fixture substituted from outside (stub receipt -> self-consistent forgery) so cases are not cut short; marker file written |
| `probe_receipt_classes.py`, `candidate_fixtures.py` | supplementary plan-only probe of GENUINE / FABRICATED_STUB / TAMPERED_GENUINE / SELF_CONSISTENT_FORGERY / MALFORMED / NO_RECEIPT; re-runs the harness deny controls first and STOPS (exit 5) if any fails |
| `make_proposal.py` | builds an UNREVIEWED proposal (or a clearly labelled dry-run copy) from a finished run; create-only |
| `run_profile_dryrun.py` | dry-run wrapper with a JSON-normalised reviewed-profile comparison (works around harness finding H-1 from outside) |
| `verify_scope_candidate.py` | proves only this directory was added relative to a base commit |
| `results/` | Linux evidence: existing-harness-as-is (3.11/3.12/3.13), fixture-control (candidate + pinned original), receipt probe (original + candidate), inert-proposal check, NOT-A-REVIEW dry-runs |
| `attempts/` | preserved failed first attempts; `REDACTIONS.json` lists copy-time path redactions |

Everything ran read-only: `validate`/`plan`/`srt`/dry-run CLIs only, each in an isolated child (`-I -S -B -X utf8`) with a denying audit hook, ≤ 10 s and ≤ 256 KiB per child, serial. No FFmpeg, no media, no mix/render/mux.
