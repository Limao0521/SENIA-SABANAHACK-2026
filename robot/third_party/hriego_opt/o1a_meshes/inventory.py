"""Inventory of meshes/geoms/model memory. usage: inventory.py <ik|sonic> -> out/inventory_<name>_<mujoco>.json"""
import sys, json, collections
import numpy as np, mujoco
from paths_o1a import *
name = sys.argv[1]
spec = mujoco.MjSpec.from_file(str(SRC[name])); m = spec.compile()
print("mujoco", mujoco.__version__, name, "nbuffer MB", m.nbuffer / 2**20)
inv = {"mujoco": mujoco.__version__, "nbuffer": int(m.nbuffer), "ngeom": m.ngeom, "nmesh": m.nmesh, "nmeshvert": m.nmeshvert, "nmeshface": m.nmeshface,
       "nmeshgraph": m.nmeshgraph, "nbody": m.nbody, "njnt": m.njnt, "nu": m.nu}
# model memory breakdown: arrays by name (all numpy ndarray attrs of the model)
fields = {}
for k in dir(m):
    if k.startswith("_"): continue
    try: v = getattr(m, k)
    except Exception: continue
    if isinstance(v, np.ndarray): fields[k] = int(v.nbytes)
inv["fields_top"] = sorted(fields.items(), key=lambda x: -x[1])[:15]; inv["fields_sum"] = sum(fields.values())
mesh_geoms = collections.defaultdict(list)
for g in range(m.ngeom):
    if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH: mesh_geoms[int(m.geom_dataid[g])].append(g)
rows = []
for i in range(m.nmesh):
    nv, nf = int(m.mesh_vertnum[i]), int(m.mesh_facenum[i]); gs = mesh_geoms.get(i, [])
    gi = [dict(geom=mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g) or f"#{g}", body=mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_BODY, int(m.geom_bodyid[g])), contype=int(m.geom_contype[g]),
               conaffinity=int(m.geom_conaffinity[g]), group=int(m.geom_group[g])) for g in gs]
    ghull = int(m.mesh_graphadr[i]) if m.mesh_graphadr[i] >= 0 else -1
    nhull = int(m.mesh_graph[ghull]) if ghull >= 0 else 0   # graph header: numvert first
    kind = "unused" if not gs else ("collision" if any(x["contype"] or x["conaffinity"] for x in gi) else "visual")
    rows.append(dict(mesh=mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_MESH, i), nvert=nv, nface=nf, nhullvert=nhull, kind=kind, geoms=gi))
inv["meshes"] = rows
inv["by_kind"] = {k: dict(n=sum(r["kind"] == k for r in rows), nvert=sum(r["nvert"] for r in rows if r["kind"] == k), nface=sum(r["nface"] for r in rows if r["kind"] == k)) for k in ("collision", "visual", "unused")}
gt = collections.Counter(); 
for g in range(m.ngeom): gt[(mujoco.mjtGeom(m.geom_type[g]).name, int(m.geom_contype[g]), int(m.geom_conaffinity[g]), int(m.geom_group[g]))] += 1
inv["geom_types"] = [[*k, v] for k, v in sorted(gt.items())]
json.dump(inv, open(HERE / f"out/inventory_{name}_{mujoco.__version__}.json", "w"), indent=1)
print(json.dumps({k: inv[k] for k in ("by_kind", "geom_types", "fields_top", "fields_sum", "nmeshvert", "nmeshface", "nmeshgraph")}, indent=0))
for r in rows: print(f'{r["mesh"]:32s} {r["nvert"]:7d}v {r["nface"]:7d}f hull={r["nhullvert"]:5d} {r["kind"]:9s}', [(x["geom"], x["contype"], x["conaffinity"], x["group"]) for x in r["geoms"]])
