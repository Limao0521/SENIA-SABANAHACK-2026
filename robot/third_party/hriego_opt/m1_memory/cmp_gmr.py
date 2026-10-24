import sys, numpy as np
from pathlib import Path
H = Path(__file__).resolve().parent / "out"
a, b = sys.argv[1], sys.argv[2]
for i, n in enumerate(sys.argv[3].split(",")):
    fa = H / f"{a}_{i}_{n}/retarget/{n}_x2_gmr_full.npz"; fb = H / f"{b}_{i}_{n}/retarget/{n}_x2_gmr_full.npz"
    za, zb = np.load(fa), np.load(fb); k = [x for x in za.files if za[x].dtype.kind == "f" and za[x].ndim == 2][:3]
    print(n, {x: float(np.abs(za[x] - zb[x]).max()) for x in k if za[x].shape == zb[x].shape}, za.files[:6])
