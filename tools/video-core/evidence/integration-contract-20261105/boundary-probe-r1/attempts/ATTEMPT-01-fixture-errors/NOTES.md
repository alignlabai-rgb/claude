# ATTEMPT-01 - fixture and reading errors found by the first run (preserved, not repaired in place)

First probe draft, run against the candidate 092b420 only. 21 expectations were NOT_MET on the candidate; none was a library fault. Each was a fixture or reading error of mine, confirmed by the
library's own validation message or by the written spec, and corrected in the fixture or the expectation WITH that evidence - never by looking at what a library did.

| first-run NOT_MET | cause (mine) | evidence | correction |
|---|---|---|---|
| unselected OMIT / selected OMIT zero-sample cases rejected | OMIT needs an opaque `decision_ref` | library message `OMIT requires a decision_ref (an opaque caller assertion)` | fixture adds `decision_ref` |
| unselected / selected USE zero-sample rejected with `source_range_invalid` | schema needs `source_out_sample > source_in_sample` even on a zero-sample timeline range | library message `source_out_sample must be greater than source_in_sample`; plan.py checks `event_zero_samples` BEFORE `range_length_mismatch` | fixture uses `source_out = source_in + 1` |
| 48000/1, 48001/1, 60000/1001 unselected-UNKNOWN cases HOLD, and S1 | at a normal-span frame an UNSELECTED row covers nothing: the frame has samples and no selected event covers them, so `REQUIRE_FULL_COVERAGE` HOLDs (correctly) | plan output: `INCOMPLETE_SELECTED_INPUT ... 1 gap(s)`; hold entries use `event_id`, not `id` | fixture adds a selected `USE` filler `ev_c` on normal-span frames (an unselected row may overlap it); hold ids read from `event_id` |
| expected exit 0 for HOLD | HOLD exits 5 | AUDIO_SPEC.md line 56 ("HOLD (`status: HOLD`, exit 5, nothing created)") | expectations use exit 5 for HOLD |
| Japanese output NAME rejected | output names are ASCII-only by rule | library message `output name must match [A-Za-z0-9][A-Za-z0-9._-]{0,63}` | Japanese is used for ROOTS and file names; the Japanese NAME became its own documented-rejection control |
| avmux plan / mux dry-run rejected `audio_receipt_plan_hash` | the candidate qualifies a receipt mechanically; the shallow stub of the closed harness is not accepted | message `plan_sha256 must equal the sha256 of the receipt's single plan output` (earlier portable-candidate evidence) | a SELF_CONSISTENT_FORGERY fixture (portable-r2/candidate_fixtures.py) lets the plan reach the root/path checks; it is a mechanical fixture, not a producer receipt |

The first-run files (`SUMMARY.txt`, `run.json`, `cases-092b420/`, `boundary_probe.py.attempt01`) are kept next to this note. The corrected probe re-ran on all three sources; the original b4ca4a3 produced
99 NOT_MET (observed limitations) with the corrected fixtures, so the corrections did not turn the probe into an always-pass.
