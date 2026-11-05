# Recovery-only transport of boundary-probe-r1 at e0af65b

Carries the **exact committed** `tools/video-core/evidence/integration-contract-20261105/boundary-probe-r1/**` (source commit `e0af65bdb9bd3d7c1d166bf7918676d8853c6cee`,
commit tree `df253be6aebb384492a25bbaebf2345d7487a316`, scope subtree `570149b0199288d1f038d1e45456384cf354f262`) in a few objects instead of 1,238 small fetches. Nothing was rerun, rerendered or
regenerated: every member is the committed git blob (blob id recomputed from the bytes and compared). Per-invocation `stdout.bin` / `stderr.bin` are evidence and are included byte for byte.
The packed scope does not include this directory, `portable/`, `portable-r2/`, `portable-recovery/`, `portable-candidate-*` or any other file. It recovers already-public repository content; it adds no new data.

**Members: 1,238 of the 1,238 files in the pinned git tree under the scope (506 `*.bin`), 6,871,248 bytes, of which 305 are zero-byte files (retained). Excluded: 0. Omitted: 0.**
(`git ls-tree -r -l` of the commit, not a truncated compare list; all are regular files, mode 100644.) Member names are relative to `tools/video-core/evidence/integration-contract-20261105/`
and all start with `boundary-probe-r1/`; `MANIFEST.json` also gives each full `repo_path`.

| Object | bytes | sha256 |
|---|---|---|
| `boundary-probe-e0af65b.zip` (create-only, deflate 9, fixed 1980-01-01 timestamps, members sorted by path, no links/directories/encryption) | 1169700 | `9f330ebbd54d335871e61b00671d156e0378c6330d3cbd2431712f444c8e48a6` |
| base64 text of the whole zip (RFC 4648, 76-column LF, ASCII), **concatenation of the 4 chunks in order** | 1580122 | `1e74b2120cfac38b62f5a2f0f3d7ea32460028c4954d1b6f532224c4c5b6a2ef` |
| `….zip.b64.part001` | 524216 | `ca47db47545479986ad85830358449dd445a81105cc8543ae99a94417b85e50e` |
| `….zip.b64.part002` | 524216 | `83498488954f983bab94236ed8143b7e86f4ea7df061d37ff87ee0e3db810c47` |
| `….zip.b64.part003` | 524216 | `532e2dbaf1a28f25cdaf94f37d68e460389fe7a1e0f083781c59a9560ee7ba03` |
| `….zip.b64.part004` | 7474 | `27549c051229d674999a3f11cb941ccc34e2bfb126bf0a0f7b8e6484cd4c36ea` |

Each chunk is at most 512 KiB (524,288 bytes) and ends on a line boundary. `MANIFEST.json` also holds, per member: `repo_path`, `path`, `bytes`, `git_blob_id`, `sha256`; plus totals and the chunk list.
`MANIFEST.sha256.txt` is the same sha256 list in `sha256sum` form. The transport commit is the git commit that introduces this directory (a commit cannot contain its own id).

## Verify and restore (Python 3.11+ stdlib; no git needed)
```powershell
# put verify_transport.py, MANIFEST.json and the four .part files in one folder, then:
py -3 -I -B verify_transport.py --manifest MANIFEST.json --parts-dir . --extract-to C:\vcp\recovered-boundary-probe-e0af65b
# or, with the zip:   --zip boundary-probe-e0af65b.zip       inside a git checkout you may add:  --git-commit e0af65bdb9bd3d7c1d166bf7918676d8853c6cee --repo <checkout>
```
Exit 0 and `VERIFIED` only if: every chunk exists, is ASCII, <= 512 KiB and matches its pin; no extra `.part` file; the concatenation matches the total hash and decodes strictly; the zip matches its hash; no unsafe, duplicate, directory,
encrypted, link or casefold-colliding member; the member set equals the manifest exactly; every size, sha256 and recomputed git blob id matches (zero-byte files included); totals match. Extraction is create-only.

## What was verified (Linux; `VERIFICATION_LOG.txt`)
Parts and zip both verify 1238/1238 against the manifest **and** the committed git blobs at `e0af65b`; a fresh `git archive` export of the scope compared with both extractions: identical file lists (1238), `diff -r` shows 0 differing files, including all 506 `*.bin`
(305 zero-byte) and 6,871,248 bytes; the git tree count/bytes equal the manifest; a rebuild into a fresh directory is byte-identical (same Python/zlib; the ZIP bytes could differ on another zlib, which is why the hashes are pinned in the manifest).
`negative_controls.py`: 20/20 controls behave as required (bit flip; chunk changed/missing/extra/non-ASCII/swapped; members `../escape.txt`, `/abs/x.txt`, backslash, `.git/config`, outside scope, missing, duplicate, casefold clash; one `stdout.bin` altered or emptied; a zero-byte file given content; existing extraction target).
A content scan of the payload found no session path or e-mail pattern. Not verified: Windows execution of the verifier by me; the Windows coordinator's own verification is separate.

## Preserved attempt
`attempts/FIRST_ATTEMPT_NOTE.txt`: a defect of my throw-away test mutator (not of the verifier) and a reused scratch name in the first ad-hoc check; replaced by the committed `negative_controls.py`.
