# Recovery-only transport of the portable harness at 8cab6e4

Purpose: carry the **exact committed** files of `portable/` + `PORTABLE_HANDOFF.md` (commit `8cab6e44b7626924c9ad3a46814c7e57b575b5bc`) in one object instead of 901 individual fetches. It is **not** a build artifact: nothing was rerun or regenerated; every member is the committed git blob (blob id recomputed and compared).
This directory sits OUTSIDE the packed scope (`portable-recovery/` is a sibling of `portable/`), so nothing is packed recursively.

| Object | bytes | sha256 |
|---|---|---|
| `portable-8cab6e4.zip` (create-only, deflate 9, fixed 1980-01-01 timestamps) | 717474 | `4fc5e5325ab12d6cccb6f25283625dd3bf95fe43fd362d4890e33943bb21373c` |
| `portable-8cab6e4.zip.b64` (ASCII, RFC 4648, 76 columns, LF) | 969220 | `0a8dafe4a6f77570a8629e48f7a4e95806b73d4b3923b3b8de6b92e35e188ada` |
| `MANIFEST.json` (per member: path, bytes, sha256, git blob id) | | commit-pinned in git |
| `MANIFEST.sha256.txt` (`sha256  path`, `sha256sum -c` compatible from the extraction root) | | |

**Members: 901 of 901 committed files in scope, 1,829,295 bytes. Excluded: 0. Omitted: 0.** Enforced exclusions (none triggered): `.git` entries, absolute paths, `..`, backslashes/drive colons, non-regular git modes, anything outside `portable/**` and `PORTABLE_HANDOFF.md`, this directory, and any working-tree or newly generated file.
Member paths are relative to `tools/video-core/evidence/integration-contract-20261105/` (e.g. `portable/portable_harness.py`, `PORTABLE_HANDOFF.md`). A content scan found no local path, e-mail or credential pattern in any member.

## Verify and restore (Python 3.11+ stdlib; no git needed)
```powershell
# put portable-8cab6e4.zip.b64, MANIFEST.json and verify_transport.py in one folder, then:
py -3 -I -B verify_transport.py --manifest MANIFEST.json --b64 portable-8cab6e4.zip.b64 --extract-to C:\vcp\recovered-8cab6e4
# or from the binary zip:   --zip portable-8cab6e4.zip
# inside a git checkout you may add:  --git-commit 8cab6e44b7626924c9ad3a46814c7e57b575b5bc --repo <checkout>
```
Exit 0 and the line `VERIFIED` only if: the base64/zip hashes equal the manifest; the member set equals the manifest exactly; all paths are safe; every member's size and sha256 match; every blob id matches. Extraction is create-only (an existing directory is refused).
To run the harness from the restored tree: `py -3 -B C:\vcp\recovered-8cab6e4\portable\portable_harness.py --library-root <tools\video-core of a checkout> --scratch-root C:\vcp\scratch --out-dir C:\vcp\out-001 --strict` (the PC00 cross-check of the closed job's fixture index is SKIPPED with a reason if `..\fixtures` is absent).

## What was verified (Linux; see `VERIFICATION_LOG.txt`)
Zip and base64 both verify 901/901 against the manifest **and** against the committed git blobs; an independent `git archive 8cab6e4` export compared file by file with the extracted tree: 901 files, identical lists, 0 differing bytes; both extractions identical to each other. Negative controls all rejected: one bit flipped in the zip, one character changed in the base64, extra members named `../escape.txt`, `/abs/x.txt`, `a\b.txt`, `.git/config`, `portable/extra.txt`, a missing member, an altered member.
Not verified: Windows (nothing was run there).

## Preserved failed first attempt of the verifier
The first `verify_transport.py` rejected the tampered zip/base64 with exit 1 but via an uncaught `BadZipFile` traceback after recording the hash mismatch (`zip_one_bit_flipped`, `b64_one_char_changed`). It now stops cleanly as soon as the container hash disagrees with the manifest, and extraction onto an existing directory prints `REFUSED` instead of a `FileExistsError` traceback. The transport bytes themselves were not affected.
