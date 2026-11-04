# Compatibility follow-up evidence (author's own checks, Linux cloud only)

Base commit `dce0a9d0faad6446aad71cb19a949d0be3785bcc` (first delivery; its code, plan hash and evidence are unchanged and **not** overwritten).
The supplied six-second baseline was **not rendered again** (baseline render count remains 1).

## Test counts (this follow-up, default suite, Linux x86_64)

| Python | tests | failures | errors | skipped |
|---|---|---|---|---|
| 3.11.18 | 194 | 0 | 0 | 3 (opt-in) |
| 3.12.3 | 194 | 0 | 0 | 3 (opt-in) |
| 3.13.18 | 194 | 0 | 0 | 3 (opt-in) |

First delivery for comparison: 152 tests, 2 skipped. See `default-suite.log`.

## Opt-in checks that touch real FFmpeg 6.1.1 (`integration-tests-and-real-listings.log`)

Run because the capability preflight code changed. These are **distinct synthetic cases, not the baseline**: file sources (generated mp4 + png + colour) at
30000/1001 → `READBACK_MATCH`; drawtext burn-in with a local font → encoded and read back; and a listing-only test that parses the *real* local
`-filters/-encoders/-demuxers` output and finds every required name (no render). Nothing here evaluates visual quality.

## Original cloud MP4 (recovery data; the bytes are not in the repository)

The file from the first delivery's evidence render was still present in the session scratch directory and was read without modification:
7665 bytes, SHA-256 `25d80ca487fa8423ab32a858e16a053fa3fbb551fcf7e5366418044c026c5ecf` (recomputed during this follow-up; equals `evidence/receipt.synthetic-6s.json`).
It was delivered to the requester as a response attachment and as base64 in the chat reply, not committed.

## Not shown
Any Windows execution; macOS; creative, audio or visual quality; the colour-conversion drift (still open, deliberately unchanged).
