"""Shared test helpers for the Japanese-caption evidence suite (kept out of tools/video-core/tests on purpose)."""
import os
import sys
import unittest

SUITE = os.path.dirname(os.path.abspath(__file__))
EVID = os.path.dirname(SUITE)
ROOT = os.path.abspath(os.path.join(EVID, "..", ".."))
for _p in (ROOT, EVID):
    if _p not in sys.path:
        sys.path.insert(0, _p)

IPAG_PATH = "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf"
IPAG_SHA256 = "503af4a8b84d1079b8e2e358dc7f7a7fb8cb7a1f212f35eaef6782dbfc75a55e"
UNIFONT_JP_PATH = "/usr/share/fonts/opentype/unifont/unifont_jp.otf"
UNIFONT_JP_SHA256 = "218eb241082aa4f47399f7c2058e25abffa8d081b312542bc91740681f006597"


def file_if_hash(path, sha):
    """Bytes of a pre-installed font only if it is present AND is the exact build the committed report describes."""
    import hashlib
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        data = fh.read()
    return data if hashlib.sha256(data).hexdigest() == sha else None


def need_pil(test):
    try:
        import PIL.ImageFont  # noqa: F401
        return test
    except Exception:
        return unittest.skip("Pillow not importable")(test)
