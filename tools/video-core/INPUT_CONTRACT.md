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
* Not touched on purpose (other owners): `paths.check_media_for_inspect` (F-PATH-1, no native reparse-point claim from a stub), `runner.py` F-RUN-1/F-RUN-2,
  F-INT-1, F-RCPT-1.
* Windows, macOS: not run. The new tests are bounded and POSIX-neutral except the raw-byte and fake-tool cases, which skip elsewhere.
