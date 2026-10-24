import json, sys, numpy as np
a = json.load(open(sys.argv[1])); b = json.load(open(sys.argv[2])); cs = []; bx = 0.0; same = 0; tot = 0; nf = 0; dsc = 0.0; mism = []
for k in a["imgs"]:
    A, B = a["imgs"][k], b["imgs"][k]
    if len(A["boxes"]) != len(B["boxes"]): mism.append((k, "nfaces", len(A["boxes"]), len(B["boxes"]))); continue
    for i in range(len(A["boxes"])):
        nf += 1; bx = max(bx, float(np.abs(np.array(A["boxes"][i]) - np.array(B["boxes"][i])).max()))
        ea, eb = np.array(A["emb"][i]), np.array(B["emb"][i]); cs.append(float(ea @ eb / np.linalg.norm(ea) / np.linalg.norm(eb)))
    for da, db in zip(A["dec"], B["dec"]):
        tot += 1; same += (da["top1"], da["status"]) == (db["top1"], db["status"]); dsc = max(dsc, abs(da["s1"] - db["s1"]))
print(dict(faces=nf, cos_min=min(cs), cos_mean=float(np.mean(cs)), box_maxdiff_px=bx, decisions=tot, same=same, max_top1_score_diff=dsc, mismatches=mism))
