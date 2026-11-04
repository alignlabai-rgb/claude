# Audio planner and PCM WAV mixer — generic spec v1 (`video_core.audiomix`)

Optional, offline, stdlib-only, separate from the video commands (which still render video-only with `-an`). It consumes **explicitly
selected, already-local, measured** audio ranges and writes one verified WAV **stem**. It is a generic component: it knows nothing about any
company or product, uses only synthetic tones in its fixtures, and is not wired into any workflow. It does not decide what audio is right; it
consumes a *qualified* manifest and must not duplicate or override any external creative or binding authority.

**Not done, on purpose:** speech/TTS or any voice service, network access, resampling, time stretching, automatic normalization/loudness,
phrase or boundary invention, muxing into video. No subprocess is ever launched by this package.

## Manifest (`schemas/audio-manifest.schema.json`, `manifest_version` 1)

| part | content |
|---|---|
| `timeline` | `fps {n,d}` (rational), `total_frames`, optional `episode_sha256` (caller assertion; checked only if `--episode` is given) |
| `output` | `sample_rate` 48000, `channels` 1 or 2, `bits` 16 |
| `scope.event_ids` | **explicit, non-empty** list of event ids the caller selects. There is no implicit "all". Only these are consumed. |
| `policy` | `gap`: `ALLOW_ZERO_FILL` \| `REQUIRE_FULL_COVERAGE` (required) · `overlap`: `REJECT` (default) \| `ALLOW_SUM` · `clipping`: `REJECT` (default) \| `CLAMP` |
| `assets[]` | `id`, `path` (relative, `.wav`), `sha256` (caller-provided, verified) |
| `events[]` | `id`, `source_id`, `state`, optional `role`, `decision_ref`, `provenance_ref`, frame range, and for `USE`: `asset`, `source_in_sample`, `source_out_sample`, optional `gain {num,den}`, `channel_rule` |

Integers are JSON integers (no floats, bools, NaN; duplicate keys, BOM, bad UTF-8 rejected). `id`/`source_id`/`asset`: `[a-zA-Z0-9_-]{1,64}`.
`decision_ref`/`provenance_ref`: opaque 1–128 character strings; `role`: `[a-zA-Z0-9_-]{1,32}`.
**These are caller assertions**: preserved verbatim in the plan and receipt, never authenticated, and not evidence of human adoption or creative approval.

### Event states

| state | meaning | requires | consumed when selected |
|---|---|---|---|
| `USE` | mix a source range | `asset`, `source_in_sample`, `source_out_sample`, `start_frame`, `end_frame` (all technical bindings) | mixed |
| `INTENTIONAL_SILENCE` | caller states silence here | `decision_ref`, frame range | written as zeros; counts as coverage |
| `OMIT` | caller states "no audio for this event" | `decision_ref` (range optional) | nothing; does **not** count as coverage |
| `MISSING` / `UNKNOWN` / `PROXY` | unresolved | nothing | **HOLD** — structured, no output, never silence |

Only `USE` may carry `asset`/`source_*`/`gain`/`channel_rule`. No speech field exists: caption-only or diegetic metadata is just an event
(`OMIT` or unresolved) with an opaque `role`. Unselected events are never verified or read; unselected unresolved events remain in the report
(`plan.unresolved`, receipt `unresolved_report`) and never block an otherwise valid selected scope.

## Timeline → samples (one documented rounding policy)

```
sample(frame) = floor( (2 * frame * fps_d * 48000 + fps_n) / (2 * fps_n) )      # exact integers, round half up
event span    = [ sample(start_frame), sample(end_frame) )
```

Applied independently to each **absolute** boundary, so there is no per-frame accumulation. At 30/1 every frame is 1600 samples. At 30000/1001
a frame is 1601.6 samples: boundaries are 0, 1602, 3203, 4805, 6406, 8008, … (lengths alternate 1601/1602) and frame 18000 is exactly 28,828,800
(a per-frame rounded length would be 7,200 samples late). `total_samples = sample(total_frames)`.
A `USE` event's source length must **equal** its timeline length exactly (`source_out_sample - source_in_sample`); a mismatch is rejected
(`range_length_mismatch`) — nothing is trimmed, padded or stretched. `source_out_sample` is checked against the real WAV sample count.

## Reject vs HOLD

* **Reject** (exception / exit 1, nothing created): invalid manifest, unsafe path, caller sha256 ≠ real hash, bad/truncated/lying WAV, source range outside the
  file, range-length mismatch, overlap conflicts, channel-rule problems, any limit exceeded, unsafe/existing output location.
* **HOLD** (`status: HOLD`, exit 5, nothing created): a selected event is `MISSING`/`UNKNOWN`/`PROXY`; a selected `USE` asset file does not exist;
  `policy.gap` is `REQUIRE_FULL_COVERAGE` and selected `USE` + `INTENTIONAL_SILENCE` events leave gaps (reported exactly). With `ALLOW_ZERO_FILL`, gaps are zero-filled and
  listed (`coverage.gaps`, warning `ZERO_FILL_APPLIED`).

## Overlap, gain, channels, clipping

* Selected events must not overlap (`policy.overlap` `REJECT`); `ALLOW_SUM` permits overlapping `USE` events up to `max_concurrent`. `INTENTIONAL_SILENCE` may never overlap a consumed event. Touching ranges are not overlapping.
* Gain is an exact rational `num/den` (`den` ≤ 65536, ratio ≤ 4): per-event sample = round-half-away-from-zero(sample·num/den), integer arithmetic only. No dB floats, no automatic gain.
* Source/output channels must be reconciled by an explicit `channel_rule`: equal counts → `COPY` (default); mono→stereo → `MONO_TO_STEREO_DUPLICATE`; stereo→mono → `STEREO_TO_MONO_LEFT|RIGHT|AVERAGE` (average = half-away rounded). Any other pairing is rejected.
* Events are summed as Python ints per output sample. A sum outside int16 is clipping: `REJECT` stops at the first clipped chunk, keeps the partial and reports the first clipped sample; `CLAMP` saturates and the status is `MIXED_WITH_CLAMPING` with exact counts and peaks.

## Inputs

48000 Hz, signed 16-bit little-endian PCM WAV, mono or stereo. Parsed strictly (`wav_*` codes): other formats (float, extensible, 8/24/32-bit, other rates/channel counts), `block_align`/`byte_rate` that lie, truncation, RIFF size ≠ real size, streaming sizes, misordered/duplicate chunks are rejected. Extra chunks (e.g. `LIST`) are skipped. Paths follow the video rules (relative, `/`, no `..`/`:`/`\`/leading `-`/`.`; symlinks **and Windows reparse points/junctions** rejected; regular files only; extension `.wav`).

## CLI — `python -m video_core.audiomix`

| command | effect |
|---|---|
| `plan MANIFEST` (default) | validate + verify the selected assets (read-only) + print the canonical plan and `plan_sha256`. Creates nothing. |
| `validate MANIFEST` | manifest/timeline/overlap/gap analysis only; reads no asset. |
| `mix MANIFEST --output-root R [--output-name N] --mix` | mix into the **fresh** directory `R/N` (default `stem-<plan hash prefix>`); without `--mix` a dry run. |

Common: `--asset-root DIR`, `--episode EPISODE.json` (timeline must match its fps/frames), limits `--max-events --max-assets --max-frames --max-output-samples --max-source-bytes
--max-total-source-bytes --max-concurrent --max-mix-work --chunk-samples --max-runtime-seconds`. Exit codes: `0` ok/`MIXED_VERIFIED` · `1` rejected · `2` usage · `4` mixed-but-not-verified · `5` HOLD · `130` cancelled/interrupted.
SIGTERM sets a cancel flag that is honoured between chunks.

## Output and receipt

`R/N/` holds `stem.wav` (or `stem.wav.partial` if the run did not finish — its header describes the planned length, so it is *not* a valid complete file), `plan.canonical.json`
(its SHA-256 is `plan_sha256`) and `receipt.json` (created `IN_PROGRESS`, replaced atomically; a receipt still `IN_PROGRESS` means UNKNOWN). The receipt pins the manifest hash, each asset's SHA-256 before and after, the plan hash,
the stem's SHA-256 and **actual** WAV properties re-read with the strict parser, sample counts, per-channel peaks, clipped-sample counts, limits and wall time; `quality_evaluated` is always `false`.
Statuses: `MIXED_VERIFIED`, `MIXED_WITH_CLAMPING`, `CLIPPING_REJECTED`, `TIMEOUT`, `CANCELLED`, `INTERRUPTED`, `READBACK_MISMATCH`, `UNKNOWN` (e.g. an input changed during the run), `HOLD` (no directory). Nothing is retried; partial files are preserved.
Work is chunked (`chunk_samples` output samples in memory at a time); cancellation and the runtime bound are checked before each chunk.

## Limits (defaults; may be lowered, ceilings fixed)

manifest 4 MiB · 1024 events · 256 assets · 18000 frames · 28.8 M output samples (10 min) · 512 MiB per asset / 2 GiB selected total · 8 concurrent events · 200 M mix-work (sample-channels) · chunk 48000 samples (64…1,048,576) · 300 s runtime.

## Seam for a later mux (not implemented here)

The plan's `mux_interface` block (`stem`, container, rate, channels, bits, `start_offset_samples: 0`, `duration_samples`, `timeline_fps`, `timeline_frames`, `duration_seconds`) is the whole contract. A later, **separately validated** mux stage must: accept only a stem whose receipt is `MIXED_VERIFIED` and whose SHA-256 it re-checks; compare the stem duration with the video's measured duration and decide explicitly (it must not trim, pad or stretch silently); choose and record the audio codec/container; keep the video-only path (`-an`) as the default; and produce its own receipt with its own readback. Nothing in this component claims a muxed or production video.

## Evidence

`evidence/stage-b-audio-stem/`: the manifest, plan, receipt, an independent verification (`verify_stem_independent.py`, imports nothing from `video_core`) and the generator for the synthetic tone assets. The 4800-sample stem itself is delivered outside the repository.
