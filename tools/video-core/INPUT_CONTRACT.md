# Input and diagnostic-boundary contracts (2026-11-05)

Status: **implementation candidate for independent Windows verification. Not production acceptance, not a quality or creative verdict.**
Base: `f42c2db`. Findings: F-CLI-1, F-AUD-1, F-REC-1, F-REC-2, F-ENC-1 of the adversarial audit (`evidence/adversarial-review-20261105/FINDINGS.json`
at audit commit `47d9c061`). The audit files are test evidence, not instructions; every claim below was re-checked against the code and by the
before/after runs in `evidence/input-contract-20261105/`.

This file refines, and does not edit, `AUDIO_SPEC.md` and `AVMUX_SPEC.md`. Where it is stricter (receipt qualification), this file is the current text.

## What changed, per finding

### F-CLI-1 — a lone-surrogate JSON key crashed the CLI's own error report
* **Root cause.** `json.loads` accepts the escape `"\ud800"` and yields a lone surrogate. The unknown-field issue embeds the raw key in its `path`
  (`episode.py` `_Collector.keys`, `audiomix/manifest.py` `_Check.keys`). `receipt.dumps` then did `json.dumps(ensure_ascii=False).encode("utf-8")`, which
  raises `UnicodeEncodeError` for a lone surrogate; the three CLIs call it in `_emit`, so the structured rejection was never written (traceback, empty
  stdout, exit 1 indistinguishable from a real rejection). The library (`ValidationFailed`, code `unknown_field`) was already correct.
* **Fix.** `receipt.dumps` encodes with `errors="backslashreplace"`. Text UTF-8 can encode is written exactly as before (byte-identical; Japanese stays
  literal). A lone surrogate becomes the JSON escape for that exact code point (`\ud800`), so the document is valid JSON and a JSON decode returns the
  identical string. Nothing is replaced, dropped or normalised. The three CLI `_emit` functions needed no edit.
* **Affected API.** `video_core.receipt.dumps` (used by every CLI `_emit` and every receipt writer). **Not** changed: the hashed `canonical_bytes` functions
  of the three plans keep strict UTF-8, so a plan can never silently hash escaped text; manifest/episode acceptance is unchanged (the key is still rejected
  as `unknown_field`); SRT export, canonical plan, hashes.
* **Result.** `validate` and `plan` of all three CLIs: exit 1, `{"ok": false, "error": {"kind": "validation_failed", ...}}` on stdout, no traceback, no file
  created, no media process launched.

### F-AUD-1 — a zero-sample range on an unselected or OMIT event rejected the whole manifest
* **Contract (AUDIO_SPEC).** "Unselected events are never verified or read … and never block an otherwise valid selected scope"; `OMIT` "consumes nothing" and
  does not count as coverage. At 60000/1 a frame is 0.8 samples, so frame range `[2,3)` spans zero samples (`sample(2)=sample(3)=2`).
* **Root cause.** `audiomix/plan.py` `analyze` rejected `event_zero_samples` for *every* event that has a frame range.
* **Fix.** The check now applies only to events that are selected and not `OMIT`: `if s1 <= s0 and sel and state != "OMIT"`.
* **Distinctions (all tested).**

| event | zero-sample range |
|---|---|
| unselected, any state (`UNKNOWN`, `MISSING`, `PROXY`, `OMIT`, `INTENTIONAL_SILENCE`, `USE`) | accepted; stays in `events`/`unresolved` reports; never read, hashed or mixed |
| selected `OMIT` | accepted; consumes nothing; adds no coverage (a `REQUIRE_FULL_COVERAGE` gap still HOLDs) |
| selected `USE` | **rejected** `event_zero_samples` (unchanged) |
| selected `INTENTIONAL_SILENCE` | **rejected** `event_zero_samples` (unchanged; a zero-length silence would "cover" nothing) |
| selected `MISSING`/`UNKNOWN`/`PROXY` | **rejected** `event_zero_samples` (unchanged; see remaining limits) |

  Sample-length rules for selected `USE` (`range_length_mismatch`), overlap/silence-overlap rules, HOLD for selected unresolved events with a normal span, the
  frame→sample policy and the WAV rules are untouched. No conflicting contract was found.

### F-REC-1 and F-REC-2 — avmux `audio.receipt` parsing and qualification
* **Root cause.** `avmux/plan.py` `_check_audio_receipt` used plain `json.loads` and called `.get` on whatever decoded (`[]`, `null`, `"x"`, `1`,
  `outputs:"x"`, `outputs:[1]`, `readback.wav:"x"` → `AttributeError`; deep nesting → `RecursionError`), and "qualified" a stem from three fields (status,
  stem SHA-256, sample count). Duplicate keys (last wins), `NaN`, `all_ok:false`, `partial:true`, `quality_evaluated:true`, a missing schema and a float
  sample count all passed.
* **Fix.** The receipt is read with the existing strict loader `episode.parse_json_bytes` (duplicate keys, `NaN`/`Infinity`, BOM, invalid UTF-8, nesting
  depth, huge integers → `audio_receipt_unreadable`). The new pure function `avmux/receipt_check.qualify_audio_receipt` then checks, **against what
  `audiomix.mix` really writes for a `MIXED_VERIFIED` run** (the fixture is a receipt produced by the real mixer API; the committed
  `evidence/stage-b-audio-stem/receipt.stem-demo.json` also qualifies):

| check | rejection code |
|---|---|
| top level is an object; `outputs`, `readback`, `readback.wav`, `readback.checks`, `mix`, `input`, `warnings`, `unresolved_report` have the right JSON types; check names are exactly `channels, sample_rate, bits, sample_count, file_bytes`, each once | `audio_receipt_shape` |
| `status == "MIXED_VERIFIED"` (checked first) | `audio_receipt_not_verified` |
| `schema == "video_core.audio_receipt/1"` | `audio_receipt_schema` |
| `written is true`, `stage == "complete"`, `quality_evaluated is false`, stem `partial is false`, `readback.all_ok is true`, every readback check `ok is true` with equal integer `expected`/`observed` that mirror `readback.wav`, `mix.clipped_samples == 0`, `mix.samples_written == sample count`, `input.inputs_unchanged is true` | `audio_receipt_inconsistent` |
| `plan_sha256` is 64 lowercase hex **and** equals the SHA-256 of the receipt's single `plan` output | `audio_receipt_plan_hash` |
| exactly one final `stem` output (no `stem_partial`), named `stem.wav`, SHA-256 and byte count equal to the actual stem; `readback.wav.sha256`/`file_bytes` equal too | `audio_receipt_stem_mismatch` |
| `readback.wav.sample_count` is an exact `int` equal to the declared count | `audio_receipt_sample_mismatch` |
| `readback.wav.channels/sample_rate/bits` are exact ints equal to the declared channels / 48000 / 16 | `audio_receipt_format_mismatch` |

  Flags use `is True`/`is False`, counters `type(x) is int` (no `4800.0`, no `true`, no strings). **Warnings are retained**: the plan's `audio.receipt`
  now carries `warnings` (code + detail, ≤32, text ≤300 chars), `warnings_total`, `warnings_truncated`, `unresolved_events`, `schema`, `mechanical_checks`
  and an `authentication` note. (The receipt does not contain coverage gaps; those are in the audio *plan*, and a `ZERO_FILL_APPLIED` warning is what the
  receipt keeps.)
* **Authentication is out of scope and none is invented.** The receipt is a *caller-supplied file pinned only by its SHA-256 in the same manifest*. Structure
  and hashes prove consistency with this stem — not who produced or approved the file, not creative adoption, not audio quality. Anyone who can write a
  stem can also write a self-consistent receipt for it. No signature, credential or approval authority exists in this component.
* **Compatibility note.** The old tests' three-field receipt no longer qualifies (that *is* F-REC-2). Plans without `audio.receipt` are byte-for-byte unchanged
  (`"receipt": null`). A plan that carries a receipt has additional keys and therefore a different `plan_sha256` than the same inputs had before this change.

### F-ENC-1 — a root containing a raw non-UTF-8 byte stranded a run
* **Root cause.** On Linux a directory named `b"out_caf\xe9"` is a `str` with a lone surrogate (`surrogateescape`). `paths.real_directory` accepted it; the
  run directory was then created and the strict UTF-8 receipt encoding failed afterwards (video: after `output.mp4` was promoted, receipt left `IN_PROGRESS`;
  avmux: empty run directory).
* **Fix.** `paths.real_directory` (the single choke point for asset roots and output roots) now rejects, as `UnsafePath` code `root_invalid`, a path that is
  not UTF-8 encodable — checked on the string as given **and** after `realpath`, so a symlink with an ASCII name that resolves into such a directory is also
  caught. The message never echoes the offending text. This happens in `prepare_plan` / `check_output_location`, i.e. before any directory, receipt or
  process exists. Defence in depth: `receipt.dumps` can no longer raise, so a stray unencodable diagnostic string (for example a tool path from argv) cannot
  strand a run.
* **Behaviour change (intended by this task).** Every component (video, audiomix, avmux) now refuses a root whose name is not UTF-8 encodable. Previously
  e.g. an audio mix into such a root worked. Valid Japanese and other Unicode root names are unaffected (tested).
* **Platform.** Linux evidence only. Windows file names are UTF-16 and a raw `0xE9` byte name cannot be created there, so the raw-byte tests skip with an
  explicit reason on Windows; the string-level guard (`"out_caf\udce9"`) and the receipt-writer tests run on every platform. Native Windows behaviour,
  including lone surrogates in NTFS names, is **not** verified here.

## Constraints that were kept
No change to episode/manifest acceptance, plan canonical bytes or hashes (baseline plan `4bc20b3f…9f21` unchanged and re-checked), SRT bytes, the frame→sample
policy, WAV parsing, selected `USE` qualification, HOLD and intentional-silence rules, numeric limits, `runner.py`, `containment/`, `render.py`,
`audiomix/mix.py`, `avmux/mux.py`, `readback.py`, `tools.py`, `numeric.py`. No global recursion, codec, locale or int-digit-limit change. No new app, binder,
exporter, board or provider pathway. Original specs, schemas, examples and earlier evidence are byte-identical to the base (checked with `git diff`).

## Remaining limits
* Selected `MISSING`/`UNKNOWN`/`PROXY` events with a zero-sample span are still rejected (`event_zero_samples`) instead of yielding HOLD. AUDIO_SPEC does not
  say either way, so no convention was invented; if HOLD is wanted that is a separate contract decision.
* A high surrogate directly followed by a low surrogate as two separate code points would be escaped as `😀`, which a JSON decoder reads back as
  one astral character. Such a string cannot come from a JSON document (the decoder combines valid pairs) nor from `surrogateescape` (only low surrogates).
  Non-Python JSON readers may treat lone-surrogate escapes differently (often as U+FFFD); Python's `json` round-trips them exactly.
* The receipt check does not re-run the mix, does not open the plan the receipt names, and cannot detect a forged but self-consistent receipt (see above).
* `stderr`, `argparse` usage text and `print()` diagnostics are not routed through `receipt.dumps`; Python's stderr already uses `backslashreplace`.
* Not touched on purpose (other owners): `runner.py` F-RUN-1/F-RUN-2, F-INT-1, F-RCPT-1. (`check_media_for_inspect`, F-PATH-1, is addressed in the follow-up below.)
* Windows, macOS: not run. The new tests are bounded and POSIX-neutral except the raw-byte and fake-tool cases, which skip elsewhere.

## Follow-up (commit after `092b420`): F-PATH-1 and a receipt-qualifier review

### F-PATH-1 — `check_media_for_inspect` link-like rule (status: FIXED at stub level; real Windows UNVERIFIED)
* **Audit class.** `STATIC_CONCERN_STUB_ONLY`, LOW. `paths.check_media_for_inspect` rejected only `stat.S_ISLNK(st_mode)`, while `resolve_asset` uses
  `is_link_like(st)` (symlink **or** `FILE_ATTRIBUTE_REPARSE_POINT`). A stubbed `os.lstat` result with a regular-file mode and the reparse attribute passed the
  read-only inspect check — and, in the unfixed run, `video_core inspect` went on to try to launch ffprobe (the test's no-process guard tripped).
* **Fix.** One line: `check_media_for_inspect` now calls `is_link_like(st)` on the named file, same predicate and same code (`symlink_rejected`) as `resolve_asset`.
  Both callers (`video_core inspect`, `avmux reinspect`) refuse before any process or output directory exists. Nothing else changed: protocol, extension,
  option-like (`-x.mp4`), empty-path and missing-file guards run first on the string/filesystem exactly as before; ordinary regular files — including Japanese and
  space-containing names — are accepted; no new permission, no new path accepted.
* **Evidence level — read this.** Every reparse case uses a stat-like stub (`tests/test_inspect_link_like.py`). It shows the code honours the attribute. It does **not**
  show anything about a real NTFS junction, mount point, OneDrive/cloud placeholder or dedup file. No link was created, no privileged operation or developer-mode
  change was attempted; the real-platform prerequisite (a Windows machine where an unprivileged junction/reparse file can be made) remains **UNVERIFIED**.
* **Consequences to know.** Any file carrying a reparse tag is now rejected by `inspect`, as `resolve_asset` already does for sources — conservative, and it includes
  non-link reparse objects such as cloud-sync placeholders. Only the named file is looked at (as before); symlinked *parent directories* of an inspect target are
  not walked — unchanged and still open for `inspect` (the asset resolver does walk them).

### Receipt qualifier review (status: four small consistency holes FIXED; the rest verified)
Method: take the receipt the real mixer wrote and, one field at a time, delete it or replace it with 13 differently typed values (`None`, `"x"`, `""`, `0`, `1`, `-1`,
`1.0`, `true`, `false`, `[]`, `{}`, `[1]`, `{"a":1}`): 1451 mutations on the plain receipt (1494 with a zero-fill warning), plus byte-level cases (empty, BOM, invalid UTF-8,
truncated, `NaN`/`Infinity`, duplicate keys, 100000-deep arrays and objects, a 5000-digit integer). No mutation produced an untyped exception.
Accepted mutations were only informational fields, **except** these genuinely unchecked claims, now rejected (`audio_receipt_inconsistent`):
`outputs[plan].partial` (any value, including `true`) and `outputs[plan].bytes`; `readback.wav.data_bytes` / `data_offset` (must describe this stem: `samples × channels × 2` bytes
ending at the file's last byte); `mix.first_clipped_sample` (must be null when nothing clipped). Producer acceptance is unchanged: the receipt written by the real mixer and
the committed historical `stage-b-audio-stem` receipt still qualify; selected USE / HOLD / OMIT / gap semantics live in the audio planner and were not touched.
`tests/test_input_contract.py` now contains the sweep with an explicit allow-list of informational fields, so a future unchecked contract field fails the test.

What the qualifier still does not, and cannot, establish:
* **A caller-supplied hash is byte integrity, not authenticity.** The manifest pins the receipt's bytes; the receipt names the stem's SHA-256. Both prove "these bytes are
  what the manifest says" and "the receipt is consistent with this stem". Anyone able to write the stem can write a matching receipt, including forged `started_utc` / `platform` /
  `limits`; those are never authenticated. There is no signature or key, and none was added. The plan records `authentication: NONE …` accordingly.
* Informational fields (timestamps, platform, limits, note, caller_assertions, input asset list, mix timing/peak statistics, `unresolved_report` entries) are not validated.
* Deleting or emptying `warnings` (e.g. dropping `ZERO_FILL_APPLIED`) is undetectable from the receipt alone, because the gap facts live in the audio *plan*, which the receipt names only by hash.
* Unknown extra keys are ignored (forward compatibility).

### Open observation (resolved in the next follow-up)
`avmux reinspect --original-receipt FILE` with `[]` raised an untyped `AttributeError` (see "Follow-up 2" below).

## Follow-up 2: `avmux reinspect --original-receipt` (status: FIXED; evidence in `evidence/reinspect-original-receipt-20261105/`)

* **Reproduced on `03ad6928`** (real CLI in an interpreter child, marker `--ffprobe`, plus owned fake tools): `[]`, `null`, `"x"`, `1`, `outputs:"x"|[1]|null`, `observations:"x"|[1]`,
  100000 nested arrays → untyped `AttributeError`/`RecursionError` traceback (exit 1, empty stdout). Worse, `observations.audio_packets:"x"` got past parsing, ran the (fake) ffprobe three
  times, **created the run directory** and then raised `AttributeError`, leaving an empty stranded directory. Duplicate keys, `NaN`/`Infinity`, `status` of any type or missing,
  `route:5`, `plan_sha256:5`, a foreign `schema`, and a final `media` output with status `IN_PROGRESS`/`INTERRUPTED` were all silently accepted and the process went on to start ffprobe.
* **Fix.** New pure module `video_core/avmux/original_receipt.py`; `reinspect.py` calls it where it used plain `json.loads`. The receipt is read with the strict loader
  (`episode.parse_json_bytes`) and checked in the documented order → `original_receipt_unreadable` (not strict JSON), `limit_input_bytes`, `original_receipt_shape`, `original_receipt_schema`,
  `original_receipt_inconsistent`, `original_receipt_media_mismatch`; an unreadable file (`OSError`) is a typed `UnsafePath` too. All of it happens before the tool probe, so no ffprobe/ffmpeg
  is started and nothing is written; the CLI's existing `VideoCoreError` handler turns it into exit 1 plus `{"ok": false, "error": …}`. `raise_if_interrupted`, cancellation and run-directory
  ownership code are not present in or touched by this module.
* **Compatibility rule (exact).** Accepted: any strict-JSON object whose `status` is an upper-snake string, with exactly one `media` output whose sha256 is the media being re-inspected, whose
  status is one of the two verified statuses, and whose optional fields are well-typed. `schema`, `route`, `plan_sha256`, `written`, `stage`, `quality_evaluated` are optional (so the small hand-written
  receipts of earlier tests still work, recorded with `schema: null` rather than an invented schema) but must be right when present. **Excluded, with reason:** wrong-typed top level / `outputs` /
  `observations` / `audio_packets` / `status` (they crashed); duplicate keys, `NaN`/`Infinity`, runaway nesting, 5000-digit integers (silently accepted or crashing; a duplicate key makes the meant
  value a guess); a foreign `schema`; a receipt that lists a final `media` output while its status is not a verified mux (the muxer promotes `media` only for `MUXED_VERIFIED_SAMPLE_EXACT` /
  `MUXED_TECHNICAL_WITHIN_TOLERANCE`, so that is not a receipt it wrote); a copied `audio_packets` summary deeper than 16 levels or larger than 1 MiB (a producer summary is ~1 KB, at most ~250 KB
  with its 1000-record cap, tested). No receipt written by `video_core.avmux.mux`, including the committed `evidence/avmux-synthetic/*.receipt.json`, is excluded. Among inputs that were already rejected, the only code that changed is `{}` (`original_receipt_shape`
  instead of `original_receipt_media_mismatch`); the exclusions listed above are the only inputs whose outcome changed from accepted/crashing to rejected.
* **Status is never promoted.** HOLD, UNVERIFIED, IN_PROGRESS, FAILED/PROCESS_FAILED, INTERRUPTED, UNKNOWN, READBACK_MISMATCH, … are not rewritten or re-labelled: with a final media output they are
  refused as inconsistent, without one they fail the media check. A verified status is copied verbatim, and the correction receipt keeps its own status (`REINSPECTED` / `UNKNOWN` /
  `SKIPPED_TOOL_UNAVAILABLE`), which is never the original's.
* **Hash ≠ authenticity.** `original_receipt.sha256` is now the SHA-256 of the exact bytes that were parsed (previously a second read of the file). It proves which bytes were read; it is not a signature,
  and the recorded `original_receipt.authentication` says so. Nothing authenticates who wrote the receipt, and a self-consistent forged receipt for the same media passes by design.
* **Remaining limits.** The packet summary inside the original receipt is copied as claimed (bounded, not verified); the new `original_receipt` block gained `schema` and `authentication` keys (old
  correction receipts are untouched); `parse_json_bytes` error text for the underlying failure is reduced to its code; symlinked parent directories of the receipt/media paths are still not walked.

## Follow-up 3: portability of the reinspect tests and the media-claim consistency rule (evidence in `evidence/reinspect-portability-20261105/`)

* **Gap reported by the independent native-Windows run of `91ec2d4`** (`tests.test_reinspect_original_receipt`: 14 total, 2 pass, 11 skip, 1 fail). The fixture's fake ffprobe is a POSIX script with a
  shebang; Windows cannot start it by path, so the one non-POSIX-skipped completion test ended `SKIPPED_TOOL_UNAVAILABLE` instead of `REINSPECTED`. The production validation was not at fault, and
  the rejection behaviour was confirmed independently on Windows (8 malformed shapes through the real `main(argv)`, 0 launch attempts; exact-byte limit accepted, one byte over → `limit_input_bytes`).
* **Test repair only (no production validation weakened).**
  * Exact-byte-limit acceptance is now asserted at **parser** level (`read_original_receipt`, no tool); reinspect-level over-limit rejection is asserted under the no-Popen guard (no tool).
  * Every malformed-receipt rejection (library and public `main(argv)`) runs under `tests.helpers.no_process` and is no longer POSIX-skipped; a real interpreter child with its own Popen replaced by a
    raiser covers "no traceback, no launch" without any POSIX marker executable.
  * Completion cases (REINSPECTED, receipt written by the real muxer, legacy/extra-field/surrogate/hash/statuses) use the test-only adapter `tests/portable_tools.py`: the fake scripts run as owned
    **Python children through the real `runner.run_bounded`**. Scope label (also in the module docstring): evidence about orchestration and parsing only — **not** real ffprobe/ffmpeg, **not** Windows
    executable-path/PATHEXT handling, **not** signal or resource-limit behaviour. The helper idea follows the test-only reference `tests/portable_lifecycle.py` on the separate branch c7da5ac9
    (read only; nothing copied or merged; no runtime fix of that branch is assumed to exist here).
  * A tool problem in a completion test **fails** it (`status` must be `REINSPECTED`); it is never counted as a skip. An adapter canary proves a child really ran and a test proves rejections never
    start even the owned fake.
  * Result on Linux: 21 tests, 0 skipped (previously 14 tests of which 11 were POSIX-skipped on Windows). A Linux simulation of the Windows symptom (`windows_like_exec_simulation.py`: fake tools created
    non-executable, `os.access(X_OK)` true as on Windows) makes the old test fail exactly as reported (`'SKIPPED_TOOL_UNAVAILABLE' != 'REINSPECTED'`) and the new tests pass. **That is a simulation, not a
    Windows run; the repaired file has not been run on native Windows by this session.**
* **Media/plan output claims — assessment and consistency fix.** The Windows verification noted that `read_original_receipt` accepted `outputs[media].partial = true`, `bytes = float(correct size)` and
  `bytes = -1`. Assessment against the stated compatibility rule ("optional, but present-and-wrong is rejected"): these were **neither deliberately ignored nor required** — they were an oversight in
  applying that rule, since only `role` and `sha256` of the media entry were examined. A final media output with `partial: true`, or a byte count that is not an integer, is negative or differs
  from the file on disk, contradicts the receipt itself (the muxer writes `partial: false` and an exact integer for `media`) or the media being re-inspected. Now: `partial` present → must be exactly `false`
  (`original_receipt_inconsistent`); `bytes` present → plain non-negative integer (`original_receipt_shape`; float, boolean, string, negative rejected) and, for the media, equal to the real size
  (`original_receipt_media_mismatch`); `name` present → string; the same `partial`/`bytes`/`name` rules apply to the `plan` output. **Absent stays allowed** (legacy hand-written receipts), and no
  receipt written by the muxer (incl. the committed evidence receipts) is excluded. These are consistency checks, not authenticity: the SHA-256 match ties the receipt to the media.
* **Still claims, still unverified.** The copied packet summary is recorded as `claimed_audio_packets` with a new `claimed_audio_packets_basis` of `CLAIMED_UNVERIFIED …`; the original receipt's
  `authentication` note is unchanged (caller hash = byte integrity). A self-consistent forged receipt for the same media is accepted by design (tested as a documented limit).
* **Remaining limits.** Native Windows execution of the repaired tests, of `mux()` through the adapter, and of the real runner on Windows are UNVERIFIED by this session; the adapter is not a substitute
  for real ffprobe; symlinked parent directories of the receipt/media paths are not walked; `bytes`/`partial`/`name` of the *other* output roles (`ffprobe_output`, `media_partial`, …) are not examined.

## Follow-up 4: the recurring containment-handshake test failure (test-only; evidence in `evidence/containment-handshake-flake-20261105/`)
The test failure repeatedly recorded in earlier clean-export notes (`ParentHandshakeWithStubWorker.test_every_kind_of_bad_hello_is_refused_and_no_go_is_ever_sent`, `hello_unreadable` where
`hello_too_large` was expected) was isolated to the test stub, not to production: the stub's `huge`/`garbage` modes published `hello.json` non-atomically (a visible, still-empty file), while the real
worker publishes atomically; the parent refused every time (no GO, target never started) and only the refusal reason varied. The stub now uses the worker's temp-file + `os.replace` protocol, torn
publication is a deliberate deterministic test with an event barrier, and negative controls show the matrix assertions fail if refusal or no-GO detection is lost. No production file changed; nothing
is OPEN in production; the affected tests are POSIX-only (skipped on Windows), so there is no Windows evidence.

## Follow-up 5: `avmux reinspect` with media and output root on different Windows drives (evidence in `evidence/reinspect-cross-drive-20261105/`)
The independent Windows run showed `ValueError: Paths don't have the same drive` (untyped) from the output-overlaps-media containment test when the media and the output root were on different drives. The test now uses
`paths._inside`, which treats paths on different drives (or UNC shares) as not containing each other; the same-drive `output_overlaps_media` rejection is unchanged. No new path/root policy, parent-link rule, atomic open or API.
The cross-drive arithmetic is covered by ntpath-based tests that run on any OS (simulation, not a Windows run); a real C:/D: run is for the independent verifier.
