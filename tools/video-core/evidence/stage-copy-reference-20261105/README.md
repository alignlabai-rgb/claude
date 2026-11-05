# SIT-06 / GAP-12 stage-copy reference (UNREVIEWED, test-only, synthetic)

Additive, supplemental. **No** connection to `video_core` / `audiomix` / `avmux`, binder, executor or caller; no spec, schema, library or earlier-evidence change; no code introduction, merge, PR or deploy.
Basis: only the public `CONTRACT_JA.md` / `GAPS_AND_TASKS.md` at `a0acfdaf3409ff09e172865210e2878ad4962680`: **SIT-06** ("stage layout: read-only `assets/`; fresh `stage_audio_out/`; `mux_inputs/` = copy of stem + receipt + video, **re-hashed after copy**; fresh `mux_out/`; one new output name per attempt"),
**GAP-12** ("stem handoff geometry is nobody's job: avmux output may not sit in its asset root; audiomix cannot write into its own") and the CONTRACT_JA row "copy into the single asset root of the AV stage and re-hash before building the manifest".
A PM scope check found no overlapping implementation work; that is a scope check, not adoption authority.

## What it is

`stage_copy_reference.stage_copy(source_root, source_name, pin, dest_root, dest_name)` (stdlib only: `hashlib`, `os`, `re`, `stat`) copies **one small synthetic file** (<= 1 MiB) from a source root into a fresh name in a different, existing destination root and compares
size + SHA-256 of (1) the source before the copy, (2) the source after the copy and (3) the destination against a **pin the caller supplied in advance** (`{"bytes", "sha256"}`) - in the tests, literal constants and generator bytes, never values read back from the destination.
Result: `outcome` INTEGRITY_MATCH | REFUSED | INTERRUPTED | FAILED_VERIFICATION, a `reason`, `completed`, every comparison with expected/observed, the three observed digests plus the copy-stream digest, the root relationship, and the **exact residue** (final/partial exists, partial size and hash, destination listing).

A hash match is an **integrity comparison only** - not authenticity, qualification, adoption, readiness, execution permission, creative acceptance, atomicity or durability (`not_asserted` in every result).

## Behaviour (enforced here) and what it leaves behind

* Arguments are strict (pin = exactly `bytes` strict int and `sha256` 64 lowercase hex; names `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, no reserved device name, no trailing dot, not ending `.partial` - the public output-name shape, re-stated, nothing imported); invalid -> REFUSED before any filesystem action.
* Roots must be existing directories and **DISJOINT after `realpath`**. Distinguished and named: `SAME_ROOT` (an alias such as `root/.` or the same path resolves to the same directory), `DEST_INSIDE_SOURCE_ROOT` (descendant), `SOURCE_INSIDE_DEST_ROOT` (ancestor). Anything but DISJOINT is refused; nothing is created.
* The source must be a regular file: a symlink or a Windows reparse point is refused (nothing is followed; no link is created or tried on Windows). The source must equal the pin **before** anything is created (REFUSED `SOURCE_PIN_MISMATCH`, nothing created).
* An existing final name -> REFUSED `DESTINATION_EXISTS`; an existing `<name>.partial` -> REFUSED `PARTIAL_EXISTS`. Existing outputs are never overwritten, truncated or deleted (tests compare their bytes afterwards). The partial is created with exclusive create (`'xb'`); a file that appears between the check and the open is still refused (test `c08` race).
* The copy goes to `<name>.partial`, is verified (source re-hash, destination re-hash), and **only then** given its final name by a **create-only** step (see "Promotion boundary"). **A partial is never promoted and never reported complete.**
* On any failure after the partial was created, **the helper leaves its own `<name>.partial` in place** (it deletes nothing) and records it: for an interrupted copy the first 192 bytes (3 chunks of 64) with their hash; after a source swap or a corrupted destination the whole 1080-byte partial with `completed: false`. The final name never appears. A caller must treat the partial as evidence, not as an input - and SIT-05 ("never leave a `.partial` under a media extension in the asset root") is respected only because the destination root is a *different* directory.
* Test cleanup deletes only each case's own `TemporaryDirectory`.

## The 12 cases (exact results at the first checkpoint aa228ce, identical on Python 3.11.18 / 3.12.3 / 3.13.18; `results/cases-py*.json`; re-run after the promotion fix on 3.13 only: unchanged, `results/cases-after-promotion-fix-py3.13.json`)

| # | case | outcome / reason | left in the destination |
|---|---|---|---|
| c01 | normal copy (1080 B) and zero-byte copy | INTEGRITY_MATCH; 7 of 7 comparisons MATCH incl. the copy-stream digest | `stem_copy.wav` / `empty_copy.bin`, no partial |
| c02 | source size differs from the supplied pin | REFUSED `SOURCE_PIN_MISMATCH` (size MISMATCH, sha MATCH) | nothing |
| c03 | same size, different bytes (pin for DUMMY, file is OTHER) | REFUSED `SOURCE_PIN_MISMATCH` (size MATCH, sha MISMATCH: only the hash caught it) | nothing |
| c04 | copy interrupted after chunk 3 (simulated device error via the test seam) | INTERRUPTED `COPY_INTERRUPTED`, not complete | `stem_copy.wav.partial` = 192 B, hash recorded; no final |
| c05 | source replaced while it is being read (1 byte, same size) | FAILED_VERIFICATION `SOURCE_CHANGED_DURING_COPY` (pre-check MATCH, post re-hash MISMATCH) | `.partial` 1080 B, no final |
| c06 | destination corrupted before its re-hash | FAILED_VERIFICATION `DESTINATION_MISMATCH` (source re-hash MATCH, size MATCH, sha MISMATCH) | `.partial` 1080 B, no final |
| c07 | final name already exists | REFUSED `DESTINATION_EXISTS`, earlier bytes untouched | only the earlier file |
| c08 | `.partial` already exists; and one that appears just before the exclusive create | REFUSED `PARTIAL_EXISTS` both times, earlier/racing bytes untouched, `created_by_this_call: false` | only that partial |
| c09 | destination root = source root (`root/.`, same path) | REFUSED `ROOT_SAME_ROOT` | source dir unchanged |
| c10 | destination inside / around the source root; disjoint roots with a missing source | REFUSED `ROOT_DEST_INSIDE_SOURCE_ROOT`, `ROOT_SOURCE_INSIDE_DEST_ROOT`, `SOURCE_MISSING` | nothing added |
| c11 | 18 invalid arguments (8 pin shapes incl. bool/float size, uppercase sha, extra key; 9 names; chunk size 0) | REFUSED `INVALID_ARGUMENT` x 18, no check run, nothing created | nothing |
| c12 | source is a symlink (POSIX only; skipped on Windows, not attempted) | REFUSED `SOURCE_NOT_REGULAR_FILE` | nothing |

Also tested: the pins equal the literal constants and the generator bytes; an audit hook shows the helper only opens/renames/lists paths inside the case's temp directory and starts no process, network, link, delete or env action; the helper's imports are exactly `hashlib, os, re, stat`; results are JSON-safe.
`selfcheck.py`: six one-line mutants of a COPY (re-hash skipped, exclusive create replaced by overwrite, freshness check dropped, root relationship ignored, destination hash taken from the stream, promotion despite failure) are all killed on all three interpreters.

## Promotion boundary - defect found after the first checkpoint (aa228ce), reproduced, fixed

The first version promoted with `os.rename` after a `lexists(final)` check. On POSIX `rename` replaces an existing destination, so a writer that created the final name in between had its file silently overwritten - contradicting "existing outputs are never overwritten".
**Reproduction first** (`tests/test_promotion_race.py`; the wrapper creates the final name with exclusive create `'xb'` and then calls the original `os.rename`/`os.link`): against the first version the test failed with *"the other writer's final was overwritten"* (kept: `results/prefix-race-reproduction-py3.13.{stdout,stderr}.txt`; the other three failures there are only the not-yet-existing new API).
**Minimal fix:** `_promotion_method()` -> POSIX: `os.link(partial, final, follow_symlinks=False)` for a regular-file partial inside the same owned directory (create-only: `FileExistsError` if the final name exists), then `os.unlink` of the helper's OWN partial name; Windows: `os.rename`, which refuses an existing destination (**not exercised on real Windows here**);
other platforms, a non-regular partial or a filesystem/OS that refuses the link (`OSError`/`NotImplementedError`): **HOLD `PROMOTION_UNAVAILABLE`**, partial kept, nothing promoted. `FileExistsError` -> FAILED_VERIFICATION `PROMOTION_BLOCKED`, `completed: false`; the other writer's final is neither read nor changed nor deleted, our verified partial is kept.
The result now carries `promotion: {method, completed, partial_removed}`. If removing our partial after a successful link failed, the verified final exists and the extra partial name stays and is reported in `residue` (not hidden).
Regression cases (4, `results/fixed-py3.13.*`): race -> sentinel bytes retained, `PROMOTION_BLOCKED`, partial 1080 B kept; normal promotion -> one name, `st_nlink == 1`, only the helper's own partial removed; link refused (simulated EPERM) -> HOLD, partial kept; no known safe method -> HOLD, partial kept.
The audit-hook test was updated: the helper may now `link`/`unlink` inside the owned temp, and the only deletion allowed is its own `<name>.partial`. `selfcheck.py` gained S7 (plain `os.rename` again) - killed by the regression tests (7/7 mutants killed, 1 Python).
Not claimed: closing the TOCTOU on rewriting the partial/source, durability, or any behaviour on real Windows, macOS or exotic filesystems; the first checkpoint's results (`results/*-py3.11/3.12/3.13.*` without "fixed"/"after") are kept unchanged and describe aa228ce.

## Limits and open questions (stated, not decided)

* **Not a transaction.** Promotion no longer replaces a file that another writer creates at the final name in the window (fixed, below), but a REWRITE of the verified partial or of the source between verification and promotion is not prevented or detected after the link, and nothing is durable beyond what the OS gives. No OS lock, permission, ACL, read-only flag or copy-on-write is added or tested. `fsync` is a request, not a durability proof.
* Root comparison is `realpath` + `normcase`: case-folding, 8.3 names, junctions and mount aliases are not examined; a directory symlink alias of the source root is covered only by the realpath logic, not by a separate test; Windows junction/link/permission behaviour was neither attempted nor observed.
* No trusted-root or absolute-path policy is defined (the caller picks both roots); SIT-06's directory names are examples. Who owns the destination directory's creation and cleanup, what to do with a leftover `.partial`, and a new output name per attempt are caller decisions.
* One file per call, <= 1 MiB; the receipt/video/stem triple and any manifest rewrite stay with the caller. Matching hashes say nothing about what the bytes are or whether anyone may use them.
* The "interrupted" and "source swapped" cases use a test-only seam (`hook`) that simulates the fault deterministically; real disk or concurrent-writer behaviour was not tested.

## Run

```bash
cd tools/video-core/evidence/stage-copy-reference-20261105
python3 -B -W error::ResourceWarning -m unittest discover -s tests -v 2>err.txt; echo "exit $?"; cat err.txt      # exit 0 alone is not proof: read stderr (Warning / Exception ignored)
python3 -B report.py --out /tmp/cases.json        # exact per-case results
python3 -B selfcheck.py --out /tmp/selfcheck.json
```
