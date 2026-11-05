"""The precise comparison rule of portable-r2 (Python 3.11+ stdlib only; pure; no I/O).

PROBLEM (finding H-1 of the closed `portable/` harness). Expected values and live observations are Python objects (tuples inside), while a reviewed profile is a JSON file (lists only).
`==` between a tuple and a list is False, so a profile read from disk could not match most multi-field observations - and the opposite shortcut, comparing `json.dumps` text or
stringifying anything unusual, would make `1 == True == 1.0 == "1"` problems invisible. This module defines ONE structural rule used for every comparison in r2.

THE RULE.  Both sides are first converted to a canonical JSON-compatible value (`canon`), then compared by `equivalent`:
  * allowed scalars:  None, bool, int, finite float, str.  Everything else (bytes, bytearray, set, frozenset, Fraction, Decimal, datetime, objects, ...) is UNSUPPORTED and raises
    UnsupportedValue. NaN and +-Infinity raise too. A str must be encodable as UTF-8 (a lone surrogate is unsupported). Nothing is ever stringified, hex-encoded or repr()ed.
  * arrays:  list and tuple are the SAME kind (that is the intended equivalence); order and length are significant; elements are compared with the same rule.
  * objects: dict with str keys. int keys (not bool) are canonicalised to their decimal string, exactly what JSON itself does; a collision (1 and "1") is UNSUPPORTED; any other key type
    (bool, tuple, None, float, ...) is UNSUPPORTED. Key ORDER is not significant (JSON objects are unordered); key SET and every value are.
  * scalar types are preserved and never coerced:  True != 1,  1 != 1.0,  0.0 != -0.0,  "1" != 1,  None != False,  None != missing key,  [] != {} != None != "".
  * nesting depth above MAX_DEPTH (64) is UNSUPPORTED (no unbounded recursion).
`equivalent(a, b)` is therefore symmetric, reflexive for every supported value, and invariant under a JSON write/read round trip of either side:  equivalent(x, json.loads(json.dumps(x)))
for every supported x. (Proved by tests/test_json_equiv.py.)
"""
from __future__ import annotations

import math

MAX_DEPTH = 64
_SCALARS = (bool, int, float, str, type(None))


class UnsupportedValue(ValueError):
    """A value that cannot be compared under the JSON-compatible rule (never stringified)."""


def canon(x, _path="$", _depth=0):
    if _depth > MAX_DEPTH:
        raise UnsupportedValue(f"{_path}: nesting deeper than {MAX_DEPTH}")
    t = type(x)
    if x is None or t is bool or t is int or t is str:
        if t is str:
            try:
                x.encode("utf-8")
            except UnicodeEncodeError:
                raise UnsupportedValue(f"{_path}: string is not encodable as UTF-8 (lone surrogate)") from None
        return x
    if t is float:
        if not math.isfinite(x):
            raise UnsupportedValue(f"{_path}: non-finite float {x!r}")
        return x
    if t is list or t is tuple:
        return [canon(v, f"{_path}[{i}]", _depth + 1) for i, v in enumerate(x)]
    if t is dict:
        out = {}
        for k, v in x.items():
            tk = type(k)
            if tk is str:
                key = k
                try:
                    key.encode("utf-8")
                except UnicodeEncodeError:
                    raise UnsupportedValue(f"{_path}: object key is not encodable as UTF-8") from None
            elif tk is int:
                key = str(k)                      # what JSON itself does with an int key; bool is a different type and is rejected below
            else:
                raise UnsupportedValue(f"{_path}: object key of type {tk.__name__} is unsupported")
            if key in out:
                raise UnsupportedValue(f"{_path}: object keys collide after JSON key canonicalisation ({key!r})")
            out[key] = canon(v, f"{_path}.{key}", _depth + 1)
        return out
    raise UnsupportedValue(f"{_path}: unsupported type {t.__name__}")


def _eq(a, b):
    ta, tb = type(a), type(b)
    if ta is not tb:
        return False                              # bool vs int, int vs float, str vs int, None vs anything else, list vs dict ...
    if ta is float:
        return a == b and math.copysign(1.0, a) == math.copysign(1.0, b)
    if ta is list:
        return len(a) == len(b) and all(_eq(x, y) for x, y in zip(a, b))
    if ta is dict:
        return a.keys() == b.keys() and all(_eq(a[k], b[k]) for k in a)
    return a == b


def equivalent(a, b) -> bool:
    """True iff canon(a) and canon(b) are structurally identical under the rule above. Raises UnsupportedValue for either side if it cannot be canonicalised."""
    return _eq(canon(a), canon(b))
