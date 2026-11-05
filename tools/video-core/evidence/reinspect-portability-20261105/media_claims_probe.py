#!/usr/bin/env python3
"""Records how read_original_receipt treats the media/plan output claims the independent Windows run asked about. Observation only; run from tools/video-core of any commit."""
import hashlib, inspect, json, os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, os.getcwd())
from video_core.avmux.original_receipt import read_original_receipt
from video_core.errors import VideoCoreError
from video_core.limits import Limits

MEDIA = b"ORIGINAL-AAC-OUTPUT-BYTES"
SHA = hashlib.sha256(MEDIA).hexdigest()
takes_size = "media_bytes" in inspect.signature(read_original_receipt).parameters


def doc(**media):
    m = {"role": "media", "name": "output.mp4", "sha256": SHA, "bytes": len(MEDIA), "partial": False}
    m.update(media)
    return json.dumps({"schema": "video_core.avmux_receipt/1", "status": "MUXED_TECHNICAL_WITHIN_TOLERANCE", "outputs": [m]}).encode()


cases = {"control: exact claims": doc(), "media.partial = true": doc(partial=True), "media.bytes = float(correct size)": doc(bytes=float(len(MEDIA))),
         "media.bytes = -1": doc(bytes=-1), "media.bytes = true": doc(bytes=True), "media.bytes = another size": doc(bytes=len(MEDIA) + 1),
         "no partial/bytes/name (legacy)": json.dumps({"status": "MUXED_TECHNICAL_WITHIN_TOLERANCE", "outputs": [{"role": "media", "sha256": SHA}]}).encode()}
out = {"read_original_receipt_accepts_media_bytes_argument": takes_size}
for k, v in cases.items():
    try:
        kw = {"media_bytes": len(MEDIA)} if takes_size else {}
        read_original_receipt(v, expected_sha256=SHA, limits=Limits(), **kw)
        out[k] = "ACCEPTED"
    except VideoCoreError as e:
        out[k] = "REJECTED:" + e.issues[0].code
print(json.dumps(out, indent=1, ensure_ascii=False))
