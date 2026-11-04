# AAC packet side-data correction — re-inspection only (author's own run, Linux cloud, 2026-11-04, ffprobe 6.1.1-3ubuntu5)

**Re-inspection only.** The two existing cloud outputs from `evidence/avmux-synthetic/` were read (hashed and probed with ffprobe) and **not modified, remuxed, re-encoded or regenerated**. All earlier evidence and receipts are unchanged; these are NEW receipts in a fresh directory.

## What was wrong

The first AV-mux receipt summarised AAC packet side data as scalars (`skip_samples`, `discard_padding`) holding the first **non-zero** value. For the real MP4 that printed `discard_padding: null`, which conflated "ffprobe printed 0" with "absent".

## What the correction shows (`mp4.reinspection.receipt.json`)

Media: `output.mp4`, SHA-256 `543a712284260b5957eab1079e757fce602e221f4b435a5c7ffc2b2c72b36b90` — verified **before and after** (`unchanged: true`) and equal to the hash in the original receipt (`evidence/avmux-synthetic/mp4.receipt.json`, referenced by its own SHA-256 inside the new receipt).
Audio packets: 6. **One** Skip Samples record, on packet 0 (pts −1024): `skip_samples 1024`, **`discard_padding 0` (observed zero)**, `skip_reason 0`, `discard_reason 0`, no absent fields. The last packet has **no** side-data record at all (`last_packet_has_skip_record: false`) — which is why the decoder returned 5120 samples against 4800 declared (+320 untrimmed end padding), exactly as the original technical verdict (within the 1024-sample tolerance, not sample-exact) already said.
Per-field summary: `discard_padding.reported_in_records 1, observed_zero_in_records 1, nonzero []`; `skip_samples.nonzero [{packet_index 0, value 1024}]`. The `differences_from_original_summary` section states, field by field, that the original `null` meant "no non-zero value", not "absent".

`mov.reinspection.receipt.json` (output.mov, SHA-256 `3d7b56c90c8c3c904192cbea039f6ad50bac1c9f380d10d086b54ee5e86478ae`): 5 PCM packets, **0** side-data records — the PCM route reports no skip/padding data, and the receipt now says so explicitly instead of through nulls.

## Commands actually run (once each)

```bash
python3 -m video_core.avmux reinspect <scratch>/avmux-demo/out/mp4-demo/output.mp4 --expect-sha256 543a7122…6b90 --original-receipt evidence/avmux-synthetic/mp4.receipt.json --output-root <scratch>/avmux-reinspect --output-name aac-mp4 --reinspect   # 2026-11-04T21:50Z exit 0
python3 -m video_core.avmux reinspect <scratch>/avmux-demo/out/mov-demo/output.mov --expect-sha256 3d7b56c9…78ae --original-receipt evidence/avmux-synthetic/mov.receipt.json --output-root <scratch>/avmux-reinspect --output-name pcm-mov --reinspect   # exit 0
```
Both hashed media files were identical before and after. No media is committed; `*.ffprobe.packets.json` is ffprobe's raw JSON for the audio packets.

## Honest scope
Observation correction only. No quality, sync or creative evaluation; no change to either route's verdict; Windows/macOS not involved.
