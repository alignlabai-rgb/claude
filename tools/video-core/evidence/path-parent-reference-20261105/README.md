# Path parent-reference review and opt-in reference helper (2026-11-05)

Source pinned to commit `019d059995f00e927e687e34c4385e7fcee5bfb0` (git blob ids below). **Evidence-only**: this directory is the only addition; no `video_core` file, spec, schema, example or earlier
evidence is modified, no CLI flag or route is added, nothing is enabled. Linux x86_64, Python 3.11 / 3.12 / 3.13. Everything runs in one owned temporary directory created by the tests; no user files, no
secrets, no network, no media, no privilege request; no Windows symlink/junction was or will be created by this code.

## Policies kept distinct (none is called a bug here)
* **Root alias — intentional, caller's choice.** `paths.real_directory` (blob `ed0d06d`, l.127) resolves the caller-chosen ROOT (`--asset-root`, `--output-root`) with `realpath(strict=True)`, so a root that is itself
  an alias is accepted and resolved. That is the existing, documented root policy and this review does not touch it.
* **Descendants below the root.** `paths.resolve_asset` (l.164) rejects any link-like component (symlink or Windows reparse attribute) by `os.lstat` on **every** component, requires a regular file, checks the real
  path stays inside the resolved root, a size cap, and path syntax via `check_relative_path` (relative, `/`, no `..`/absolute/drive/`:`/backslash/control/option-like/hidden/Windows-unsafe/reserved names, extension allow-list).
* **Arbitrary absolute paths** named on a command line are a third thing: there is no root to resolve against, so "descendant of the root" is not defined for them. Policy for them is an owner decision.

## What each current read route checks vs leaves unresolved (commit `019d059`)
| route (source) | leaf symlink | symlinked **parent** dir | Windows reparse attribute | regular-file / size / ext | other |
|---|---|---|---|---|---|
| asset sources via `--asset-root` → `resolve_asset` (`paths.py` l.164) | rejected (`lstat`) | rejected (every component `lstat`-ed) | rejected, leaf **and** parents (`is_link_like`) | yes / `max_bytes` / allow-list | root alias resolved first (policy above) |
| `inspect MEDIA` → `check_media_for_inspect` (`paths.py` l.221; `cli.py` l.166) | rejected (`lstat` leaf, `is_link_like`) | **not examined** (path is only `abspath`-ed) | leaf only | regular file yes / no size cap / extension allow-list | protocol-like, option-like, empty/NUL refused; no root |
| `avmux reinspect MEDIA` → same function (`avmux/reinspect.py` l.79) | as above | **not examined** | leaf only | as above | media is later SHA-256-verified against `--expect-sha256` |
| `avmux reinspect --original-receipt` (`reinspect.py` l.90–98) | rejected (`os.path.islink`) | **not examined** | **not examined** (`islink`/`isfile` follow or ignore reparse points) | `isfile` yes / `max_input_bytes` | bytes read once, hashed and parsed strictly; SHA-256 recorded (integrity, not authenticity) |
| episode / manifest / audio manifest files (`cli.py` `_read_episode` l.49; `audiomix/cli.py` and `avmux/cli.py` `_read`) | **followed** (`os.stat`, `open`) | followed | not examined | `S_ISREG` on the target / `max_input_bytes` | these are CLI input documents, not source assets; no root concept |

Behaviours in the table that are claims about "not examined"/"followed" are pinned by `test_04`, `test_05`, `test_10` (they assert today's behaviour; they do not call it wrong).

## The reference helper (`reference_helper.py`, 2 thin functions)
`resolve_under_trusted_root(trusted_root, rel, …)` and `media_under_trusted_root(trusted_root, rel)` delegate to `paths.resolve_asset` unchanged (`test_11` proves identical results/rejections on a corpus), so there is
no second policy: the root is the caller's explicit trusted choice and may be an alias; `rel` is relative and syntax-checked; every component below the root is `lstat`-ed link-like; regular file, size cap, extension.
It is imported by nothing in `video_core` (`test_12`). It is a sketch of what an opted-in route could reuse, not a recommendation to adopt it.

## Results (12 cases, one run per interpreter, no matrix, no full suite)
| interpreter | total | pass | skip | fail | log |
|---|---|---|---|---|---|
| 3.11 | 12 | 12 | 0 | 0 | `results/final.python3.11.log` |
| 3.12 | 12 | 12 | 0 | 0 | `results/final.python3.12.log` |
| 3.13 | 12 | 12 | 0 | 0 | `results/final.python3.13.log` |
Failed attempts (kept): (1) `results/attempt1.python3.13.log` 12/12 pass, then one cosmetic edit of `test_10` (deterministic output names instead of `hash()`). (2) The first three "final" runs passed
against a provisional README; once the real README was written, `test_12` FAILED (`results/attempt2.python3.13.FAILED-test12-README-marker-case.log`) because its marker `not atomic` is capitalised "Not atomic" in the README — a bug of mine in the
test (case-sensitive match), fixed by comparing case-insensitively; the production tree was not involved. The three runs in the table are of the committed test file against the committed README, made after that fix.
Run: `python3 evidence/path-parent-reference-20261105/test_reference_helper.py` from `tools/video-core`. POSIX-only cases (real symlinks: `test_02`, `03`, `04`, `09`, `10`) would be skipped, not faked, where unprivileged symlink creation is unavailable (e.g. Windows without rights); this
repository's Linux run executed all 12.

## Limits (not hidden)
* **Not atomic / no TOCTOU prevention.** `test_09` demonstrates deterministically that a file swapped for a symlink after resolution is followed by a plain `open()`. Detection by `lstat` is a point-in-time check; open-by-descriptor
  with no-follow semantics (or equivalent per-platform handling) would be a separate, owner-decided change. Not claimed, not attempted here.
* **Windows is stat-stub only.** `test_05` patches `lstat` to carry `FILE_ATTRIBUTE_REPARSE_POINT`; it says nothing about real junctions, mount points, cloud placeholders or `os.path.islink` semantics on Windows. No real Windows run by this session.
* A symlinked/aliased ROOT is accepted by design; the helper does not and should not detect it. Components above the root are therefore never examined.
* Hard links, bind mounts, case-insensitive-filesystem aliasing, 8.3 short names and NTFS alternate data streams are not examined by any route and not by this helper.

## HOLD — OWNER DECISION required before any production change (nothing changed)
1. **Which route(s), if any, should take a trusted root + relative path** instead of an absolute path: `inspect`, `avmux reinspect` media, `--original-receipt`, episode/manifest inputs? (An added flag or changed argument shape is a CLI/API change.)
2. **What a trusted root is for those routes** (a new `--media-root` / `--input-root`? the existing `--asset-root`? the current directory?) and whether root aliases stay accepted (today they do, deliberately).
3. **What to do with arbitrary absolute paths** that remain allowed today: keep, deprecate, or restrict (breaking for existing users and for the committed evidence workflows).
4. **Whether parent-link rejection for absolute paths is wanted at all**, given that it needs a definition of "parent" without a root (e.g. every component up to the filesystem root, which also rejects common OS symlinked temp/home layouts) — likely too broad without a root.
5. **Whether to invest in atomic open** (descriptor-relative, no-follow) rather than more `lstat`-detection, and for which platforms.
Until those are decided, the existing behaviour above stands unchanged and is documented; this candidate adds only the reference helper, 12 tests and this table.
