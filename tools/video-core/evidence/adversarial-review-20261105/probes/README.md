# Exploratory probes (NOT part of the suite)

Throw-away scripts used to form hypotheses before the formal tests existed. They contain environment-specific absolute paths
(the review session's scratchpad), write only to temp directories, and are kept so the path from hypothesis to reproducer stays visible.
Each `pN.out` is the output of one re-run at the end of the review. The formal, deterministic, bounded equivalents live in `../test_adv_*.py`.

| probe | question | became |
|---|---|---|
| p1 | numeric edge values | F-NUM-1 |
| p2 | lone-surrogate key through CLIs; zero-sample event at 60000 fps | F-CLI-1, F-AUD-1 |
| p3 | malformed / forged audio receipts | F-REC-1, F-REC-2 |
| p4 | drain limit vs fast-exiting child | F-RUN-1 |
| p5 | SIGTERM during version probe / preflight probe (avmux, fake tools) | F-INT-1 |
| p6 | Ctrl-C during pipe drain | F-RUN-2 |
