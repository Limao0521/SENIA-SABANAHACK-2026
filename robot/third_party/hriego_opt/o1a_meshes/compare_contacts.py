"""Contact equivalence of variants vs the full-mesh baseline on N random configurations (root pose near the ground + joints uniform in range).
usage: compare_contacts.py <ik|sonic> <variant> [N] -> out/contacts_<name>_<variant>_<mj>.json. Checks MuJoCo's claim: mesh collisions use the convex hull."""
import sys, json, collections
import numpy as np, mujoco
from paths_o1a import *
name, var = sys.argv[1], sys.argv[2]; N = int(sys.argv[3]) if len(sys.argv) > 3 else 1500
mv = mujoco.__version__
b = mujoco.MjModel.from_xml_path(str(SRC[name])); v = mujoco.MjModel.from_binary_path(str(MODELS / f"{name}_{var}_{mv}.mjb")) if var in ("v1", "v2") else mujoco.MjModel.from_xml_path(str(MODELS / f"{name}_{var}.xml"))
keep = [g for g in range(b.ngeom) if not (b.geom_contype[g] == 0 and b.geom_conaffinity[g] == 0)]; assert len(keep) == v.ngeom
inv = {g: i for i, g in enumerate(keep)}
db, dv = mujoco.MjData(b), mujoco.MjData(v); rng = np.random.default_rng(0)
lo, hi = b.jnt_range[1:, 0], b.jnt_range[1:, 1]   # joint 0 = free
types = collections.Counter(); bk = collections.defaultdict(lambda: [0, 0.0, 0, 0]); same_set = 0; maxd = maxp = maxn = 0.0; ndiff_cfg = 0; tot = 0; extra = collections.Counter()
for it in range(N):
    q = b.qpos0.copy(); q[2] = rng.uniform(0.2, 0.9); q[3:7] = rng.normal(size=4) if it % 3 else [1, 0, 0, 0]; q[3:7] /= np.linalg.norm(q[3:7])
    q[7:] = rng.uniform(lo, hi)[: b.nq - 7]
    if name == "ik" and it % 2: q[:2] = 0
    db.qpos[:] = q; dv.qpos[:] = q; mujoco.mj_forward(b, db); mujoco.mj_forward(v, dv)
    kb = collections.defaultdict(list); kv = collections.defaultdict(list)
    for c in db.contact[:db.ncon]: kb[(int(c.geom1), int(c.geom2))].append((float(c.dist), c.pos.copy(), c.frame[:3].copy()))
    for c in dv.contact[:dv.ncon]: kv[(keep[int(c.geom1)], keep[int(c.geom2)])].append((float(c.dist), c.pos.copy(), c.frame[:3].copy()))
    tot += db.ncon; cfg_bad = False
    for k, l in kb.items(): types[(mujoco.mjtGeom(b.geom_type[k[0]]).name[7:], mujoco.mjtGeom(b.geom_type[k[1]]).name[7:])] += len(l)
    for k in set(kb) | set(kv):
        tn = (mujoco.mjtGeom(b.geom_type[k[0]]).name[7:], mujoco.mjtGeom(b.geom_type[k[1]]).name[7:])
        l1, l2 = sorted(kb.get(k, []), key=lambda x: x[0]), sorted(kv.get(k, []), key=lambda x: x[0])
        dmin = min([x[0] for x in l1 + l2]); B = "ge-5mm" if dmin >= -0.005 else ("-5mm..-2cm" if dmin >= -0.02 else "deeper-2cm")
        if len(l1) != len(l2): extra[tn + ("count_%d_vs_%d" % (len(l1), len(l2)),)] += 1; bk[B][3] += 1; cfg_bad = True; continue
        for a, c2 in zip(l1, l2):
            d = abs(a[0] - c2[0]); bk[B][0] += 1; bk[B][1] = max(bk[B][1], d); bk[B][2] += d > 1e-9; maxd = max(maxd, d); maxp = max(maxp, np.abs(a[1] - c2[1]).max()); maxn = max(maxn, np.abs(a[2] - c2[2]).max())
            if d > 1e-9: extra[tn + ("dist_differs",)] += 1; cfg_bad = True
    if cfg_bad: ndiff_cfg += 1
    else: same_set += 1
res = dict(model=name, variant=var, mujoco=mv, N=N, contacts_total=tot, configs_same_pairset=same_set, configs_diff=ndiff_cfg, max_dist_diff=maxd, max_pos_diff=float(maxp), max_normal_diff=float(maxn),
           by_depth={k: dict(n=v[0], max_dist_diff=v[1], n_dist_differs=v[2], n_count_mismatch_pairs=v[3]) for k, v in bk.items()}, pair_types=[[*k, n] for k, n in types.items()], mismatches=[[*k, n] for k, n in extra.items()])
json.dump(res, open(HERE / f"out/contacts_{name}_{var}_{mv}.json", "w"), indent=1); print(json.dumps(res))
