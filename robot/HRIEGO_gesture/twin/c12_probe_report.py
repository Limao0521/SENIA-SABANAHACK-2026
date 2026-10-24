import sys, json, numpy as np
from c12_io import load
base = sys.argv[1]; d = load(base + ".csv"); ev = json.load(open(base + ".json"))["events"]
A = [k for k in d if k.startswith("meas_")]; C = [k for k in d if k.startswith("cmd_")]; M = np.stack([d[a] for a in A], 1); t = d["t"]; ph = d["phase"]
ipd = np.where(ph == "post_delete")[0][0]; t0 = t[ipd]; rel = t - t0
sp = np.abs(np.gradient(M, axis=0) / np.gradient(t)[:, None]).max(1)
T0 = ev[0]["t"]
for e in ev:
    e = dict(e); e["t"] = round(e["t"] - T0, 3)
    if "p0" in e: e["p0"] = [round(x, 2) for x in e["p0"]]
    print(e)
for i in range(ipd - 3, len(t), int(sys.argv[2]) if len(sys.argv) > 2 else 12):
    print(ph[i], round(rel[i], 2), "spd", round(sp[i], 2), "ps", d["player_state"][i], str(d["mode"][i])[:5], np.round(M[i][[0, 3, 4, 7, 10]], 2), "wst", round(d["wmeas_twist_waist"][i], 3), "hd", round(d["wmeas_turn_head"][i], 3), "z", round(d["pelvis_z"][i], 3), "tilt", round(d["tilt_deg"][i], 1))
