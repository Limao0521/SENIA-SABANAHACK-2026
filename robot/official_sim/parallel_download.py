#!/usr/bin/env python3
"""Parallel HTTP range downloader (the AgiBot mirror throttles each connection)."""
import os, sys, threading, time, urllib.request

CHUNK = 2 * 1024 * 1024


def size_of(url):
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as r:
        return int(r.headers["Content-Length"])


def fetch(url, out, workers):
    total = size_of(url)
    parts = [(s, min(s + CHUNK, total) - 1) for s in range(0, total, CHUNK)]
    tmp = out + ".parts"
    os.makedirs(tmp, exist_ok=True)
    todo = [p for p in parts if not (os.path.exists(f"{tmp}/{p[0]}")
                                     and os.path.getsize(f"{tmp}/{p[0]}") == p[1] - p[0] + 1)]
    lock = threading.Lock()

    def worker():
        while True:
            with lock:
                if not todo:
                    return
                a, b = todo.pop()
            for _ in range(20):
                try:
                    req = urllib.request.Request(url, headers={"Range": f"bytes={a}-{b}"})
                    with urllib.request.urlopen(req, timeout=120) as r:
                        data = r.read()
                    if len(data) == b - a + 1:
                        with open(f"{tmp}/{a}", "wb") as f:
                            f.write(data)
                        break
                except Exception:
                    time.sleep(2)
            else:
                print(f"giving up on {a}", file=sys.stderr)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(workers)]
    for t in threads:
        t.start()
    while any(t.is_alive() for t in threads):
        done = sum(os.path.getsize(f"{tmp}/{f}") for f in os.listdir(tmp))
        print(f"{os.path.basename(out)}: {done/1e6:.0f}/{total/1e6:.0f} MB", flush=True)
        time.sleep(30)
    with open(out, "wb") as f:
        for a, b in parts:
            with open(f"{tmp}/{a}", "rb") as p:
                f.write(p.read())
    if os.path.getsize(out) != total:
        sys.exit(f"{out}: incomplete")
    for f in os.listdir(tmp):
        os.remove(f"{tmp}/{f}")
    os.rmdir(tmp)
    print(f"{out}: done", flush=True)


if __name__ == "__main__":
    fetch(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 24)
