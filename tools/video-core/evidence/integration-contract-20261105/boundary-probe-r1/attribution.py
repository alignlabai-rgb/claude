"""Attribution of observed changes between the source-pinned runs of ONE probe invocation (Python 3.11+ stdlib only).

Inputs: the per-source case files written by boundary_probe.py.  For every INPUT_CONTRACT expectation (layer INPUT_CONTRACT_EXPECTATION) it compares the outcome (MET / NOT_MET) of each source in
role order original -> candidate -> input and says WHERE it changed; for every OBSERVATION it only says whether the observed value is the same in all sources.
It never turns NOT_MET into a pass and never infers a gap from a sibling check: a finding is attributed per check, and checks that stay NOT_MET are listed as UNCHANGED_NOT_MET (remaining limits)."""
from __future__ import annotations

import glob
import json
import os

ROLE_ORDER = ("original", "candidate", "input")


def _load_cases(out_path, label):
    cases = {}
    for f in sorted(glob.glob(os.path.join(out_path, "sources", label, "cases", "*.json"))):
        with open(f, encoding="utf-8") as fh:
            c = json.load(fh)
        cases[c["case"]] = c
    return cases


def classify(seq):
    """seq: [(label, outcome)] in role order -> (attribution, changed_at)."""
    outs = [o for _, o in seq]
    if len(seq) == 1:
        return "SINGLE_SOURCE_" + outs[0], None
    if any(o not in ("MET", "NOT_MET") for o in outs):
        return "NOT_COMPARABLE", None
    if all(o == "MET" for o in outs):
        return "UNCHANGED_MET", None
    if all(o == "NOT_MET" for o in outs):
        return "UNCHANGED_NOT_MET", None
    first = outs[0]
    changes = [i for i in range(1, len(outs)) if outs[i] != outs[i - 1]]
    if len(changes) == 1:
        at = seq[changes[0]][0]
        return ("CHANGED_NOT_MET_TO_MET" if first == "NOT_MET" else "REGRESSED_MET_TO_NOT_MET"), at
    return "MIXED", seq[changes[0]][0]


def attribute(runs, out_path):
    runs = sorted(runs, key=lambda r: ROLE_ORDER.index(r["source_role"]) if r.get("source_role") in ROLE_ORDER else 99)
    labels = [r["source_label"] for r in runs]
    per = {lab: _load_cases(out_path, lab) for lab in labels}
    keys, meta = [], {}
    for lab in labels:
        for cid, c in per[lab].items():
            for chk in c["checks"]:
                if chk["layer"] in ("INPUT_CONTRACT_EXPECTATION", "OBSERVATION", "NOT_APPLICABLE", "SKIPPED"):
                    k = chk["key"]
                    if k not in meta:
                        keys.append(k)
                        meta[k] = {"case": cid, "layer": chk["layer"], "name": chk["name"], "finding": chk.get("finding")}
    rows, by_att, by_find = [], {}, {}
    for k in keys:
        seq, obs = [], {}
        for lab in labels:
            chk = next((c for c in per[lab].get(meta[k]["case"], {}).get("checks", []) if c["key"] == k), None)
            if chk is None:
                continue
            seq.append((lab, chk["outcome"]))
            obs[lab] = {"outcome": chk["outcome"], "observed": chk.get("observed"), **({"reason": chk["reason"]} if "reason" in chk else {})}
        same_obs = len({json.dumps(o.get("observed"), sort_keys=True) for o in obs.values()}) <= 1
        if meta[k]["layer"] == "INPUT_CONTRACT_EXPECTATION":
            att, at = classify(seq)
            row = {**meta[k], "key": k, "attribution": att, "changed_at": at, "same_observation_in_all_sources": same_obs, "by_source": obs}
            if att == "UNCHANGED_NOT_MET" and not same_obs:
                row["note"] = "NOT_MET in every source, but the observation differs between sources"
        elif meta[k]["layer"] == "OBSERVATION":
            att = "OBSERVATION_SAME" if same_obs else "OBSERVATION_DIFFERS"
            row = {**meta[k], "key": k, "attribution": att, "changed_at": None, "same_observation_in_all_sources": same_obs, "by_source": obs}
        else:
            att = meta[k]["layer"]
            row = {**meta[k], "key": k, "attribution": att, "changed_at": None, "by_source": obs}
        rows.append(row)
        by_att[att] = by_att.get(att, 0) + 1
        f = meta[k]["finding"] or "UNASSIGNED"
        by_find.setdefault(f, {})
        by_find[f][att] = by_find[f].get(att, 0) + 1
    inv = {}
    for lab in labels:
        for cid, c in per[lab].items():
            for i in c.get("invocations", []):
                inv.setdefault(f"{cid}::{i['label']}", {})[lab] = {"exit_code": i["exit_code"], "stdout_sha256_normalized": i.get("stdout_sha256_normalized"), "stdout_bytes_total": i.get("stdout_bytes_total"),
                                                                    "stderr_bytes_total": i.get("stderr_bytes_total")}
    inv_rows = [{"invocation": k, "same_normalized_stdout_in_all_sources": len({v["stdout_sha256_normalized"] for v in d.values()}) == 1, "same_exit_code_in_all_sources": len({v["exit_code"] for v in d.values()}) == 1, "by_source": d}
                for k, d in sorted(inv.items())]
    return {"attribution_version": 1, "source_order": labels, "source_roles": {r["source_label"]: r["source_role"] for r in runs}, "source_commits": {r["source_label"]: r.get("source_commit") for r in runs},
            "reading_guide": {"UNCHANGED_MET": "met in every source (positive control or behaviour the findings do not touch)", "CHANGED_NOT_MET_TO_MET": "not met before 'changed_at', met from there on: the change is attributed to that source's commit",
                              "UNCHANGED_NOT_MET": "not met in any source: a remaining limit or an expectation no source provides (never a pass)", "REGRESSED_MET_TO_NOT_MET": "met earlier, not met from 'changed_at': needs review",
                              "MIXED": "more than one change in role order: needs review", "OBSERVATION_SAME/OBSERVATION_DIFFERS": "documented producer behaviour without an expectation; not caller readiness",
                              "NOT_APPLICABLE / SKIPPED": "not run, with the reason recorded; no OS evidence is invented"},
            "summary": {"by_attribution": dict(sorted(by_att.items())), "by_finding": {k: dict(sorted(v.items())) for k, v in sorted(by_find.items())}},
            "checks": rows, "invocations": inv_rows}
