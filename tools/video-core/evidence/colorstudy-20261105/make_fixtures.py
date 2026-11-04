#!/usr/bin/env python3
"""Original synthetic fixture generator (stdlib only, no download, no existing media).

Writes, next to this script:
  fixtures/patches.json         the 32 numbered flat patches (name, nominal RGB, position)
  fixtures/chart.png            the 256x128 sharp reference chart (8-bit RGB PNG, lossless)
  fixtures/chart_bt709_ideal.yuv420p   exact-rational BT.709 limited-range yuv420p of the chart (source for the counterexample E11 only)
  fixtures/fixtures.sha256
Deterministic: running it twice yields identical bytes (for a given zlib build; the sha256 file records this build's result).
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import study_lib as L  # noqa: E402


def main():
    out = os.path.join(HERE, "fixtures")
    os.makedirs(out, exist_ok=True)
    patches = L.patch_table()
    rows = L.nominal_rgb_frame(patches)
    files = {
        "patches.json": (json.dumps({"width": L.W, "height": L.H, "patch": L.PATCH, "cols": L.COLS, "rows": L.ROWS,
                                     "lcg": "x'=(1103515245*x+12345) mod 2^31, byte=(x>>16)&255, seed 20261105, patches 29-31",
                                     "patches": patches}, indent=1) + "\n").encode(),
        "chart.png": L.png_bytes(rows),
        "chart_bt709_ideal.yuv420p": L.ideal_yuv420p_bytes(patches, "bt709"),
    }
    lines = []
    for name, data in files.items():
        with open(os.path.join(out, name), "wb") as fh:
            fh.write(data)
        lines.append(f"{hashlib.sha256(data).hexdigest()}  {name}")
    with open(os.path.join(out, "fixtures.sha256"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    back = L.png_read_rgb(files["chart.png"])
    assert back == rows, "PNG self-check failed"
    print("\n".join(lines))


if __name__ == "__main__":
    main()
