"""Convex hull of every collision STL (raw file frame, float32 subset of the original vertices) -> models/hull_meshes/<stem>.STL (binary, outward-oriented).
Cross-checks the hull vertex count against MuJoCo's own hull (mesh_graph). Run in any env with scipy+mujoco (default: gmr)."""
import json, struct, sys
import numpy as np, mujoco
from scipy.spatial import ConvexHull
from paths_o1a import *
MESHDIR = SRC["sonic"].parent / "../urdf/x2_ultra/meshes"   # identical files to the IK model's meshdir (cmp: 42/42 same)
OUT = MODELS / "hull_meshes"; OUT.mkdir(parents=True, exist_ok=True)

def read_stl(p):
    b = open(p, "rb").read()
    n = struct.unpack("<I", b[80:84])[0]
    if 84 + 50 * n == len(b):
        a = np.frombuffer(b, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]), count=n, offset=84)
        return a["v"].reshape(-1, 3)
    raise SystemExit(f"{p}: not binary STL")

def write_stl(p, V, F):
    t = V[F]; nrm = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]); nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-30)
    rec = np.zeros(len(F), dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])); rec["n"] = nrm; rec["v"] = t
    with open(p, "wb") as f: f.write(b"hull of " + p.name.encode() + b" " * 80); f.seek(80); f.write(struct.pack("<I", len(F))); f.write(rec.tobytes())

def mj_hull_count(path):
    s = mujoco.MjSpec(); s.meshdir = str(path.parent); mm = s.add_mesh(); mm.name = "m"; mm.file = path.name
    g = s.worldbody.add_geom(); g.type = mujoco.mjtGeom.mjGEOM_MESH; g.meshname = "m"; m = s.compile()
    return int(m.mesh_graph[m.mesh_graphadr[0]]), int(m.mesh_vertnum[0])

res = {}
for p in sorted(MESHDIR.glob("*.STL")):
    V = np.unique(read_stl(p), axis=0).astype(np.float32)          # exact dedup, keeps float32 values
    h = ConvexHull(V.astype(np.float64)); F = h.simplices.copy()
    t = V[F].astype(np.float64); nrm = np.cross(t[:, 1] - t[:, 0], t[:, 2] - t[:, 0]); flip = (nrm * h.equations[:, :3]).sum(1) < 0; F[flip] = F[flip][:, ::-1]
    used = np.unique(F); remap = -np.ones(len(V), int); remap[used] = np.arange(len(used)); Vh, Fh = V[used], remap[F]
    write_stl(OUT / p.name, Vh, Fh)
    nh_o, nv_o = mj_hull_count(p); nh_h, nv_h = mj_hull_count(OUT / p.name)
    res[p.name] = dict(nv_orig=nv_o, mj_hull_orig=nh_o, scipy_hull=len(used), mj_hull_of_hullfile=nh_h, mj_nvert_hullfile=nv_h, nface=len(Fh))
    print(f"{p.name:34s} orig {nv_o:6d}v  mujoco-hull {nh_o:5d}  scipy-hull {len(used):5d}  hullfile {nv_h:5d}v / mj-hull {nh_h:5d}  {len(Fh)}f", flush=True)
json.dump(res, open(HERE / "out/hull_counts.json", "w"), indent=1)
