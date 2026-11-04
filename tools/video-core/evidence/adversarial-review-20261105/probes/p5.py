import sys, os, json, time, signal, subprocess, tempfile, struct, hashlib, stat
ROOT="/home/user/claude/tools/video-core"
S="/tmp/claude-0/-home-user-claude/2633dc6b-3347-5caa-a7c7-bea109ee5d97/scratchpad"
PY=sys.executable
FAKE = '''#!%s
import os, sys, json, time
here = os.path.dirname(os.path.abspath(__file__))
name = os.path.basename(sys.argv[0]); args = sys.argv[1:]
beh = json.load(open(os.path.join(here, "behavior.json")))
with open(os.path.join(here, "calls.log"), "a") as f: f.write(json.dumps([name]+args)+"\\n")
if "-version" in args:
    if beh.get("sleep_on_version") == name:
        open(os.path.join(here, "started."+name), "w").close(); time.sleep(8)
    print(name+" version 0.0-fake"); sys.exit(0)
if name == "ffprobe" and "-i" in args:
    p = args[args.index("-i")+1]
    if beh.get("sleep_on_probe") and p.endswith(beh["sleep_on_probe"]):
        open(os.path.join(here, "started.probe"), "w").close(); time.sleep(8)
    print(json.dumps({"streams": [], "format": {}})); sys.exit(0)
LIST = {"-encoders": [" A....D pcm_s16le","  A....D aac"," V....D libx264"], "-decoders": [" VFS..D h264"," A....D pcm_s16le"," A....D aac"],
        "-muxers": ["  E mov","  E mp4","  E framemd5","  E framehash","  E s16le"], "-demuxers": [" DE mov,mp4,m4a"," D  wav"], "-filters": []}
for flag,lines in LIST.items():
    if flag in args:
        print(chr(10).join(lines)); sys.exit(0)
sys.exit(0)
''' % PY
d = tempfile.mkdtemp(dir=S)
tools = os.path.join(d, "tools"); os.mkdir(tools)
for n in ("ffmpeg","ffprobe"):
    p=os.path.join(tools,n); open(p,"w").write(FAKE); os.chmod(p,0o755)
def wav(n, ch=1):
    data = b"\x01\x00"*(n*ch)
    return b"RIFF"+struct.pack("<I",36+len(data))+b"WAVE"+b"fmt "+struct.pack("<IHHIIHH",16,1,ch,48000,48000*ch*2,ch*2,16)+b"data"+struct.pack("<I",len(data))+data
sha=lambda b: hashlib.sha256(b).hexdigest()
assets=os.path.join(d,"assets"); out=os.path.join(d,"out"); os.mkdir(assets); os.mkdir(out)
w=wav(4800); v=b"fake-video"
open(assets+"/a.wav","wb").write(w); open(assets+"/v.mp4","wb").write(v)
man=json.dumps({"avmux_version":1,"route":"MOV_PCM_S16LE","selection":{"ref":"x"},
 "video":{"state":"USE","path":"v.mp4","sha256":sha(v),"declared":{"codec":"h264","width":64,"height":64,"fps":{"n":30,"d":1},"total_frames":3}},
 "audio":{"state":"USE","path":"a.wav","sha256":sha(w),"declared":{"sample_rate":48000,"channels":1,"sample_count":4800}}})
open(d+"/m.json","w").write(man)
def scenario(beh, marker):
    json.dump(beh, open(tools+"/behavior.json","w")); 
    for f in os.listdir(tools):
        if f.startswith("started.") or f=="calls.log": os.remove(os.path.join(tools,f))
    p = subprocess.Popen([PY,"-m","video_core.avmux","mux",d+"/m.json","--asset-root",assets,"--output-root",out,"--output-name","o%d"%int(time.time()*1000),"--mux","--ffmpeg",tools+"/ffmpeg","--ffprobe",tools+"/ffprobe","--probe-timeout-seconds","20"],
        cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"})
    t0=time.time()
    while not os.path.exists(os.path.join(tools,marker)) and time.time()-t0<6: time.sleep(0.05)
    started = os.path.exists(os.path.join(tools,marker))
    p.send_signal(signal.SIGTERM)
    try: o,e = p.communicate(timeout=15)
    except subprocess.TimeoutExpired: p.kill(); o,e=p.communicate(); print("HARNESS: had to kill")
    calls=[json.loads(l) for l in open(tools+"/calls.log")] if os.path.exists(tools+"/calls.log") else []
    print("marker", started, "rc", p.returncode, "calls after:", [ (c[0], [a for a in c[1:] if a.startswith('-') ][:3] ) for c in calls])
    
    try:
        rcpt=json.loads(o.decode()); print("receipt status:", rcpt.get("status") or rcpt, "reasons:", rcpt.get("reasons"), "written:", rcpt.get("written"), "stage", rcpt.get("stage"))
    except Exception as ex: print("stdout not json:", o.decode()[:300])
    print("stderr:", e.decode()[-200:]); 
    for dd in sorted(os.listdir(out)):
        print("  out dir:", dd, sorted(os.listdir(os.path.join(out,dd))))
        rp=os.path.join(out,dd,"receipt.json")
        if os.path.exists(rp): print("  on-disk receipt status:", json.load(open(rp)).get("status"))
print("=== SIGTERM during ffmpeg -version probe"); scenario({"sleep_on_version":"ffmpeg"}, "started.ffmpeg")
print("=== SIGTERM during video preflight probe"); scenario({"sleep_on_probe":".mp4"}, "started.probe")
