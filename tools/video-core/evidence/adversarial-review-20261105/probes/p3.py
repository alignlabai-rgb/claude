import sys, os, json, tempfile, struct, hashlib
sys.path.insert(0, "/home/user/claude/tools/video-core")
from video_core.avmux.plan import prepare_plan
d = tempfile.mkdtemp(dir="/tmp/claude-0/-home-user-claude/2633dc6b-3347-5caa-a7c7-bea109ee5d97/scratchpad")
def wav(n, ch=1):
    data = b"\x01\x00" * (n*ch)
    return b"RIFF"+struct.pack("<I",36+len(data))+b"WAVE"+b"fmt "+struct.pack("<IHHIIHH",16,1,ch,48000,48000*ch*2,ch*2,16)+b"data"+struct.pack("<I",len(data))+data
sha=lambda b: hashlib.sha256(b).hexdigest()
w=wav(4800); v=b"not really a video"
open(d+"/a.wav","wb").write(w); open(d+"/v.mp4","wb").write(v)
def manifest(rec_bytes):
    open(d+"/r.json","wb").write(rec_bytes)
    return json.dumps({"avmux_version":1,"route":"MOV_PCM_S16LE","selection":{"ref":"x"},
      "video":{"state":"USE","path":"v.mp4","sha256":sha(v),"declared":{"codec":"h264","width":64,"height":64,"fps":{"n":30,"d":1},"total_frames":3}},
      "audio":{"state":"USE","path":"a.wav","sha256":sha(w),"declared":{"sample_rate":48000,"channels":1,"sample_count":4800},"receipt":{"path":"r.json","sha256":sha(rec_bytes)}}}).encode()


good = {"status":"MIXED_VERIFIED","outputs":[{"role":"stem","sha256":sha(w)}],"readback":{"wav":{"sample_count":4800}}}
cases = {
 "forged-minimal-3-field-receipt": json.dumps(good).encode(),
 "list": b"[]", "null": b"null", "string": b'"x"', "number": b"1",
 "outputs-string": json.dumps({**good,"outputs":"x"}).encode(),
 "outputs-int-element": json.dumps({**good,"outputs":[1]}).encode(),
 "readback-list": json.dumps({**good,"readback":[]}).encode(),
 "readback-wav-string": json.dumps({**good,"readback":{"wav":"x"}}).encode(),
 "dup-status-last-wins": b'{"status":"HOLD","status":"MIXED_VERIFIED","outputs":[{"role":"stem","sha256":"%s"}],"readback":{"wav":{"sample_count":4800}}}' % sha(w).encode(),
 "float-count": json.dumps({**good,"readback":{"wav":{"sample_count":4800.0}}}).encode(),
 "readback-all_ok-false": json.dumps({**good,"readback":{"all_ok":False,"wav":{"sample_count":4800}}}).encode(),
 "stem-partial-flag": json.dumps({**good,"outputs":[{"role":"stem","sha256":sha(w),"partial":True}]}).encode(),
 "quality-evaluated-true": json.dumps({**good,"quality_evaluated":True}).encode(),
 "deep-nesting": b"["*100000 + b"]"*100000,
}
for k,rv in cases.items():
    try:
        pl = prepare_plan(manifest(rv), asset_root=d); print(k, "-> ACCEPTED", pl.canonical["audio"]["receipt"])
    except BaseException as e:
        print(k, "-> ", type(e).__name__, [i.code for i in getattr(e,"issues",[])] or str(e)[:70])
