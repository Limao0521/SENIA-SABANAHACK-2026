"""Derive model variants from the audited sources by TEXT edits (never edits the originals; no MjSpec.to_xml round-trip, which rounds attributes).
usage: build_variants.py <ik|sonic> <v1|v2|v2h{N}> [--mjb]
 v1 : every <geom class="visual" .../> (contype=conaffinity=0, density 0) removed + mesh assets no geom references any more; meshdir made absolute.
 v2 : v1 + meshdir -> hull_meshes/ (convex-hull STLs from build_hulls.py, same file names/scales). The pelvis (the only body whose mass comes from
      its collision-mesh geom) gets an explicit <inertial> frozen at full precision from the baseline compile, so mass/inertia do not change.
 v2h{N}: v2 + maxhullvert=N on every mesh (approximate; changes collisions; report-only).
Writes models/<name>_<variant>.xml (+ .mjb tagged with the MuJoCo version when --mjb)."""
import sys, re
import numpy as np, mujoco
from paths_o1a import *
name, var = sys.argv[1], sys.argv[2]; mjb = "--mjb" in sys.argv
src = SRC[name]; text = src.read_text()
base = mujoco.MjModel.from_xml_path(str(src))
md = re.search(r'meshdir="([^"]*)"', text).group(1); abs_md = str((src.parent / md).resolve())
lines = text.split("\n"); keep = []; nvis = 0; inc = False   # inc: inside a multi-line XML comment (those lines are never touched)
def code(ls):
    global inc
    for l in ls:
        was = inc
        if "<!--" in l and "-->" not in l.split("<!--", 1)[1]: inc = True
        if inc and "-->" in l: inc = False
        yield l, (was or "<!--" in l.lstrip()[:4])
for l, com in code(lines):
    if not com and re.match(r'\s*<geom\b', l) and 'class="visual"' in l: nvis += 1; continue
    keep.append((l, com))
used = set(re.findall(r'\bmesh="([^"]+)"', "\n".join(l for l, c in keep if not c))); out = []; nmesh_drop = 0
for l, com in keep:
    mm = re.match(r'\s*<mesh\s+name="([^"]+)"', l)
    if not com and mm and mm.group(1) not in used: nmesh_drop += 1; continue
    out.append(l)
text = "\n".join(out); rel = abs_md
if var.startswith("v2"):
    rel = "hull_meshes"
    if var.startswith("v2h"):
        text, n = re.subn(r'(<mesh\s+name="[^"]+"\s+file="[^"]+")', r'\1 maxhullvert="%d"' % int(var[3:]), text); print("maxhullvert on", n, "meshes")
    i = mujoco.mj_name2id(base, mujoco.mjtObj.mjOBJ_BODY, "pelvis")
    assert base.body_mass[i] > 0
    # bodies that take mass from geoms: all others have explicit <inertial>
    nb = int(re.search(r'<body name="pelvis"', text) is not None)
    ine = ('      <inertial pos="%s" quat="%s" mass="%s" diaginertia="%s"/>   <!-- frozen from the full-mesh baseline compile -->'
           % (*[" ".join(repr(float(x)) for x in v) for v in (base.body_ipos[i], base.body_iquat[i])], repr(float(base.body_mass[i])), " ".join(repr(float(x)) for x in base.body_inertia[i])))
    text, n = re.subn(r'(<freejoint name="floating_base_joint"\s*/>)', r'\1\n' + ine.replace("\\", "\\\\"), text, count=1); assert n == 1
    others = [b for b in range(1, base.nbody) if base.body_mass[b] > 0 and b != i]
text, n = re.subn(r'meshdir="[^"]*"', f'meshdir="{rel}"', text, count=1); assert n == 1
hdr = f"<!-- derived by opt/o1a_meshes/build_variants.py {name} {var} from {src} (never edited): {nvis} visual geoms and {nmesh_drop} unused meshes removed -->\n"
if text.startswith("<?xml"): text = re.sub(r"(<\?xml[^>]*>\n)", r"\1" + hdr.replace("\\", "\\\\"), text, count=1)
else: text = hdr + text
out = MODELS / f"{name}_{var}.xml"; out.write_text(text)
m = mujoco.MjModel.from_xml_path(str(out))
print(name, var, f"removed {nvis} visual geoms, {nmesh_drop} meshes;", "ngeom", m.ngeom, "nmesh", m.nmesh, "nmeshvert", m.nmeshvert, "nbuffer MB", round(m.nbuffer / 2**20, 1))
for k in ("body_mass", "body_inertia", "body_ipos", "body_iquat", "body_subtreemass", "body_pos", "body_quat", "jnt_range", "actuator_gear", "dof_armature"):
    a, b = getattr(base, k), getattr(m, k); print(f"  {k} max|diff| vs baseline {np.abs(a - b).max():.3e}")
if mjb:
    p = MODELS / f"{name}_{var}_{mujoco.__version__}.mjb"; mujoco.mj_saveModel(m, str(p)); print("mjb", p.name, round(p.stat().st_size / 2**20, 1), "MB")
