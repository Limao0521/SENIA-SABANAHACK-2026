import subprocess, sys, json, time, os
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/m1_memory"); import memlib
R = "/home/luis/GitHub/x2_ultra/experiments/hriego_v2_tts_eval_20261021"
p = subprocess.Popen([R + "/.venv/bin/python", "/home/luis/GitHub/x2_ultra/hriego_v2/supertonic_worker.py", R + "/model"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, stderr=subprocess.DEVNULL)
def rd():
    while True:
        l = p.stdout.readline()
        if l.startswith("HRIEGO_WORKER_JSON="): return json.loads(l.split("=", 1)[1])
def pm(tag):
    s = memlib.smaps(p.pid); print(tag, {k: round(s[k]) for k in ("Rss", "Pss", "Pss_Anon", "Pss_File")}, "uss", round(s["Private_Clean"] + s["Private_Dirty"]), flush=True)
print(rd()); pm("supertonic_ready")
for i in range(3):
    p.stdin.write(json.dumps(dict(speech="Hola, bienvenido al laboratorio. Puedo guiarte hasta la sala de reuniones si lo deseas.", state="GREETING", output=f"/tmp/claude-1000/-home-luis-GitHub-Laplace/765527b2-9c3c-4d1f-9432-f440505f4552/scratchpad/st{i}.wav")) + "\n"); p.stdin.flush(); print(rd())
pm("supertonic_after_3_synth"); p.stdin.close(); p.wait(); 
