# Recovery-only transport of the candidate evidence at 440aea7

Purpose: carry the **exact committed** `portable-candidate-092b420/**` evidence (commit `440aea7cb35a1a4c088c0785d6be5e05dcb021d0`) in one object. Nothing was rerun or regenerated; every member is the committed git blob (blob id recomputed and compared).
This directory is a NEW sibling (`portable-candidate-recovery/`), outside the packed scope, so nothing is packed recursively. The existing 901-file transport (`portable-recovery/`) and its payload are **not** packed again.
It recovers already-public, authorized repository content; it adds no new data.

| Object | bytes | sha256 |
|---|---|---|
| `portable-candidate-440aea7.zip` (create-only, deflate 9, fixed 1980-01-01 timestamps) | 958251 | `995eef5f8e21fdbaf60eb9598a9aa9ac2c66ea7d7549726fe7f0a352eb69cbcc` |
| `portable-candidate-440aea7.zip.b64` (ASCII, RFC 4648, 76 columns, LF) | 1294480 | `656e0aabde7efdc612c0206c1cfb69386fb0dff6d4f21b3b3ec147d05c5a5bc0` |
| `MANIFEST.json` (per member: path, bytes, sha256, git blob id) and `MANIFEST.sha256.txt` | | commit-pinned in git |

**Members: 1071 of 1071 files in the complete pinned git tree under the scope, 2,375,428 bytes. Excluded: 0. Omitted: 0.** (The count comes from `git ls-tree -r` of the pinned commit, not from a truncated compare list; all 1071 are regular files, mode 100644.)
Enforced exclusions (none triggered): `.git` entries, absolute paths, `..`, backslashes/drive colons, non-regular git modes, anything outside `portable-candidate-092b420/**`, this directory, `portable-recovery/`, any working-tree or newly generated file.
Member paths are relative to `tools/video-core/evidence/integration-contract-20261105/` and all start with `portable-candidate-092b420/`. A content scan found no local path or e-mail pattern, no non-UTF-8 member and no `.git`/`__pycache__` member (the stored results already carry `<REPO>`/`<SCRATCH>` placeholders; see their `REDACTIONS.json`).

## Verify and restore (Python 3.11+ stdlib; no git needed)
```powershell
# put portable-candidate-440aea7.zip.b64, MANIFEST.json and verify_transport.py in one folder, then:
py -3 -I -B verify_transport.py --manifest MANIFEST.json --b64 portable-candidate-440aea7.zip.b64 --extract-to C:\vcp\recovered-candidate-440aea7
# or:   --zip portable-candidate-440aea7.zip      inside a git checkout you may add:  --git-commit 440aea7cb35a1a4c088c0785d6be5e05dcb021d0 --repo <checkout>
```
Exit 0 and `VERIFIED` only if the container hashes equal the manifest, the member set equals the manifest exactly, all paths are safe, and every size, sha256 and git blob id matches. Extraction is create-only (an existing directory is refused; missing parents are created).

## What was verified (Linux; `VERIFICATION_LOG.txt`)
Zip and base64 each verify 1071/1071 against the manifest **and** the committed git blobs; a fresh `git archive` export compared file by file with both extractions: identical lists, 0 differing files; the git tree count/bytes at the commit (1071 / 2,375,428) equal the manifest. Negative controls all rejected (bit flip in zip, character change in base64, extra members `../escape.txt`, `/abs/x.txt`, `a\b.txt`, `.git/config`, `portable/extra.txt`, a missing member, an altered member). Not verified: Windows.

## Preserved failed attempt
`attempts/VERIFICATION_LOG.first-attempt.txt`: my first log run used an ad-hoc comparison that prefixed member paths twice (they already start with `portable-candidate-092b420/`), so its file-list comparison was meaningless and was discarded; the archives and manifest were not affected. The regenerated log compares correctly.
