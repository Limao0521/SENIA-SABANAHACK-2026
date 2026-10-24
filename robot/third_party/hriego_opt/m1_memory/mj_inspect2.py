import sys, os, mujoco, numpy as np
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture"); import paths as P; sys.path.insert(0, str(P.MOTION_DIR)); import x2_retarget as X
sys.path.insert(0, "/home/luis/GitHub/x2_ultra/HRIEGO_gesture/opt/m1_memory"); from memlib import snap, trim
snap("pre"); m = mujoco.MjModel.from_xml_path(str(X.IK_XML)); snap("model"); trim(); snap("model_trim")
contype = m.geom_contype; conaff = m.geom_conaffinity; mesh = m.geom_dataid
vis = [(g, mujoco.mj_id2name(m, mujoco.mjtObj.mjOBJ_GEOM, g)) for g in range(m.ngeom) if contype[g] == 0 and conaff[g] == 0]
print("geoms", m.ngeom, "visual-only(contype=conaff=0)", len(vis), file=sys.stderr)
def vcount(msk):
    ids = set(int(mesh[g]) for g in range(m.ngeom) if msk[g] and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH)
    return len(ids), sum(int(m.mesh_vertnum[i]) for i in ids)
print("collision meshes used / verts:", vcount([(contype[g] != 0 or conaff[g] != 0) for g in range(m.ngeom)]), "visual:", vcount([(contype[g] == 0 and conaff[g] == 0) for g in range(m.ngeom)]), file=sys.stderr)
# lean: MjSpec, delete visual-only geoms and unused meshes
spec = mujoco.MjSpec.from_file(str(X.IK_XML))
for g in list(spec.geoms):
    if g.contype == 0 and g.conaffinity == 0: spec.delete(g)
used = {g.meshname for g in spec.geoms if g.meshname}
for me in list(spec.meshes):
    if me.name not in used: spec.delete(me)
snap("pre_lean_compile"); m2 = spec.compile(); snap("lean_model"); trim(); snap("lean_model_trim")
print("lean nmeshvert", m2.nmeshvert, "nbuffer", m2.nbuffer, "ngeom", m2.ngeom, file=sys.stderr)
mujoco.MjSpec  # done
