"""Shared JSON-lines protocol for the persistent workers.
stdin: one JSON request per line; stdout: one JSON reply per line (ready line first). All other output goes to stderr."""
import json, os, sys, time, traceback

def rss_mb():
    d = {}
    for l in open("/proc/self/status"):
        if l.startswith(("VmRSS", "VmHWM")): d[l.split(":")[0]] = int(l.split()[1]) / 1024
    return round(d.get("VmRSS", 0)), round(d.get("VmHWM", 0))

def serve(name, load_fn, handle_fn):
    """load_fn() -> dict of extra info; handle_fn(req) -> dict (timing etc.). Protocol stdout is saved then sys.stdout -> stderr."""
    out = sys.stdout; sys.stdout = sys.stderr
    def send(o): out.write(json.dumps(o) + "\n"); out.flush()
    t0 = time.monotonic(); info = load_fn() or {}
    rss, hwm = rss_mb()
    send({"ready": True, "worker": name, "pid": os.getpid(), "load_s": round(time.monotonic() - t0, 2), "rss_mb": rss, "hwm_mb": hwm, **info})
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        req = json.loads(line)
        if req.get("cmd") == "quit": break
        t = time.monotonic()
        try:
            res = handle_fn(req); ok = True
        except Exception as e:
            traceback.print_exc(); res = {"error": repr(e)}; ok = False
        import gc, ctypes; gc.collect()
        try: ctypes.CDLL("libc.so.6").malloc_trim(0)
        except Exception: pass
        rss, hwm = rss_mb()
        send({"id": req.get("id"), "ok": ok, "wall_s": round(time.monotonic() - t, 3), "rss_mb": rss, "hwm_mb": hwm, **res})
