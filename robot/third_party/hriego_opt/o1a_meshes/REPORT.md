# O1a - Mesh diet for the GMR (IK) and SONIC MuJoCo models

Measured 2026-10-22, CPU only, x86. Sources (never edited): IK `x2_mocap_toe_proxy.xml` ($X2_IK_DIR), SONIC `x2_ultra.xml` ($X2_EXTERNAL_ROOT). MuJoCo 3.13.0 (env gmr) and 3.11.0 (env physicalai).
Validation: the 10 clips of `t2_streamtalk_precision/clips.json`, fp16_s1 inputs (`t3_gmr_sonic/out/native/fp16_s1/*/inputs`). Single runs; "baseline" = full model loaded from its own .mjb (identical to T3's stored XML-based outputs: diff 0.0).

## 1. Inventory
- Both models: 84 (IK) / 85 (SONIC, +floor plane) geoms; 37 distinct meshes, 977 076 vertices, 1 960 796 faces, 368 577 graph ints. The 42 STL files are byte-identical in both models (cmp 42/42).
- Geoms: 35 visual mesh geoms (group 1, contype=conaffinity=0, density 0), 24 collision mesh geoms (group 3, contype=conaffinity=1; one uses scale 1 0.9 1), 24 foot spheres (r=0.005), 1 head cylinder, (SONIC: floor plane). Collision pair types seen in 1500 random poses: mesh-mesh, sphere-mesh, plane-mesh, cylinder-mesh, plane-sphere, plane-cylinder, sphere-cylinder.
- Meshes: 13 visual-only (328 037 v / 657 285 f: imu_*, head_*, waist_*, wrist_yaw x2, ankle_roll x2, hip_roll x2 (the unscaled copies)); 24 used by collision geoms (649 039 v / 1 303 511 f). Most of the 24 are also referenced by a visual geom in the same body (shared mesh asset). No unused meshes.
- MuJoCo hull sizes (mesh_graph) of the collision meshes: 494 to 2842 vertices (total 24 607) vs 649 039 raw.
- Model memory (3.13, IK, nbuffer 339.9 MB): bvh_aabb 188 MB, bvh_child 31, mesh_face 23.5, mesh_facenormal 23.5, mesh_facetexcoord 23.5, bvh_depth 15.7, bvh_nodeid 15.7, mesh_normal 11.7, mesh_vert 11.7, mesh_polymapadr/num 3.9 each, mesh_graph 1.5. Everything else is < 1 MB. i.e. >99 % of the model is mesh data, 55 % is the per-triangle BVH. SONIC: 344.7 MB, same breakdown. Full per-mesh table: `out/inventory_*.json`, `logs/inv_*.log`.
- Claim "MuJoCo collides meshes via their convex hull": confirmed for geometry. For all 24 collision mesh geoms the world positions of the hull vertices (via mesh_graph) of baseline and V2 agree to 1.5e-8 m (Hausdorff). Hull vertex count from MuJoCo == scipy ConvexHull == hull-file re-hull, for all 42 STL. But contact *solutions* are not bit-identical (see 4.3).

## 2. Variants
- V1: all `class="visual"` geoms and then-unreferenced meshes removed (text edit of the source XML; 35 geoms, 13 meshes). Masses, inertias, joint ranges, body poses identical (diff 0.0).
- V2: V1 + collision meshes replaced by their convex-hull STLs (`models/hull_meshes/`, 4.2 MB, vertices are an exact float32 subset of the originals, outward-oriented binary STL). The pelvis is the only body whose mass comes from its collision-mesh geom; it gets an explicit `<inertial>` frozen (full precision) from the baseline compile, so mass/inertia diff = 0.0.
- V2h64: V2 with maxhullvert=64 (SONIC only, report only): nbuffer 13.5 MB but contacts clearly change (see 4.3). Not recommended.
- V3 (capsule limbs): NOT done (time box; it would change contacts by construction).

## 3. Size and RAM (fresh process, after load + MjData + mj_setCacheCapacity(0) + malloc_trim; model PSS = PSS delta over a bare `import mujoco`)
mjb sizes per version are identical to within 3 KB: baseline IK 340 MB / SONIC 345 MB (3.13 and 3.11).

| model | variant | .mjb MB | nbuffer MB | from .mjb: load s / model PSS MB / peak RSS MB | from XML: load s / model PSS MB / peak RSS MB |
|---|---|---|---|---|---|
| IK | baseline | 340 | 339.9 | 0.42 / 345 / 726 | 0.66 / 1369 / 1895 |
| IK | V1 | 227 | 227.1 | 0.30 / 231 / 500 | 0.51 / 943 / 1369 |
| IK | V2 | 11.9 | 11.9 | 0.019 / 13 / 70 | 0.037 / 54 / 114 |
| SONIC | baseline | 345 | 344.7 | 0.43 / 350 / 736 | 0.60 / 1349 / 1895 |
| SONIC | V1 | 232 | 231.9 | 0.29 / 236 / 510 | 0.41 / 964 / 1378 |
| SONIC | V2 | 16.6 | 16.6 | 0.019 / 18 / 79 | 0.037 / 63 / 123 |

(3.13 numbers; 3.11 within 5 %: e.g. SONIC V2 mjb 0.022 s / 18 MB / 94 MB peak.) Raw rows: `out/membench.jsonl`. Total process RSS after load (bare python+mujoco+numpy ~ 45 MB): V2 60-80 MB, V1 280-300 MB, baseline 390-410 MB.
Speed (IK ms per frame = whole retarget_one wall incl. SMPL-X/FK, mean of 10 clips; SONIC = ms per 50 Hz control tick incl. ONNX): baseline 8.1 / 4.1, V1 7.5 / 3.7, V2 5.8 / 3.1 (pure mj_step 0.03-0.09 ms in all, no meaningful change). Single runs, noise ~10 %.

## 4. Validation
### 4.1 GMR (native_qp, env gmr 3.13, `drv_gmr.py`, `cmp_gmr.py`, `out/gmr_cmp.json`)
Baseline run twice: bit-identical to itself and to T3's stored XML-based qpos (diff 0.0).
- V1: qpos max abs diff 0.0 on all 10 clips, QC dicts identical, all PASS.
- V2: qpos max diff per clip: c001 2.7e-7, c004 4.2e-7, c005 3.0e-4, **c010 5.1e-2 rad** (frames 146-165, end of clip), c016 6.0e-8, c018 8.5e-6, t00 1.2e-7, t01 0.0, t02 2.1e-7, t05 1.2e-7. All 10 still PASS and discrete QC fields (selfcol_frac, at_limit, fail, status) identical, but float QC fields (sole_min, sole_stance_med, max_joint_vel...) differ in the 7th-3rd digit. Not accepted by the 1e-6 gate (3 of 10 clips above it).
### 4.2 SONIC (physicalai 3.11, convert_one + rollout_one with the MJCF redirected, same GMR reference for all variants, `drv_sonic.py`, `cmp_sonic.py`, `out/sonic_cmp.json`)
Baseline twice: identical (0.0) and identical to T3's stored traces.
- V1: actual joints, pelvis z, tilt, contact_count: max diff 0.0 on all 10 clips and all ticks; same trace length.
- V2: 6 clips exactly 0.0; c004 joints 7.6e-6 rad / tilt 5.9e-5 deg; c010 joints 2.1e-4 rad / pelvis z 3.3e-6 m / tilt 1.5e-3 deg and 2 ticks (of 376) with contact_count differing by 1; c016 6.0e-7; c018 1.9e-6; t02 3.8e-5. Same trace lengths, no falls. Not accepted by the 1e-6 gate (4 clips above), though sub-milliradian.
### 4.3 Why V2 is not bit-identical (explained, not noise-free)
Hull vertex sets are identical in the world, but MuJoCo recentres each mesh on its own centre of mass / principal axes, which differ for hull vs the full (non-convex) surface. The geom pose compensates exactly, but GJK/EPA (ccd, tolerance 1e-6) then starts from other local frames and converges to slightly different contacts. On 1500 random poses (SONIC, mjb 3.11): V1 contacts identical (0.0 on dist/pos/normal, all 1500 configs). V2: contacts with dist >= -5 mm differ up to 1.1e-5, between -5 mm and -2 cm up to 2.6 mm, deeper than 2 cm (unphysical random overlaps) up to 13 cm (EPA multi-valued); contact counts per pair also differ in some configs (mesh-plane and mesh-mesh, tie-breaking among up to 4 contacts). In the real clips the effect shows only where arms graze the torso/legs (c010 end, c005 frame 223-236) and in GMR through the collision-avoidance distance (mj_geomDistance) in the QP. Setting mesh refpos/refquat does not help (it moves the geometry). A bit-exact V2 would need to patch mesh_vert/geom_pos in the compiled model (version-fragile); not attempted.

## 5. Recommendation
1. **Adopt V1 now** for both models (and keep the .mjb-at-load policy of M1/M2): bit-identical GMR and SONIC outputs on 10/10 clips, QC identical; saves 113 MB (IK) / 113 MB (SONIC) of model per process (340->227, 345->232 MB), ~110 MB PSS each, load 0.42->0.30 s, and the XML-parse path 1.37 GB -> 0.94 GB if someone still parses XML. Combined with M1's .mjb: GMR+SONIC models in one process = 227+232 = 459 MB (was 685).
2. **V2 is the big win (-96 %: 12 MB and 17 MB, load 20 ms, whole process ~70 MB) but not output-identical.** Use it only after an acceptance pass at the X2 level (SONIC differences are <= 2.1e-4 rad / 1.5e-3 deg tilt, GMR up to 5e-2 rad in the last frames of one clip) or if the owner accepts these. It also removes the need for the mjb trick (XML parse 54-63 MB, 37 ms), which is attractive for the Orin NX. If chosen, keep the hull in the repo (4.2 MB) and the XMLs here as the provenance.
3. Do not use maxhullvert reductions (V2h64: contact counts/dist change by mm).
4. Files in `models/` (sha256 in `models/SHA256SUMS`): `ik_v1.xml`, `ik_v2.xml`, `sonic_v1.xml`, `sonic_v2.xml`, `sonic_v2h64.xml`(report-only), `hull_meshes/*.STL`, and `{ik,sonic}_{v1,v2}_{3.11.0,3.13.0}.mjb` (precompiled; regenerate if MuJoCo changes). The V1 XMLs reference the original mesh dir by absolute path (`/home/luis/GitHub/Laplace/Documentacion/.../meshes` via the source `meshdir`); V2 XMLs use the relative `hull_meshes/`. Each XML starts with a provenance comment (source path, edits). Redirect in code: the same `from_xml_path` -> `from_binary_path` hook used in M2's `gmr_stream.py`/`sonic_stream.py` (point `MJB_DIR` and the names `ik_v1_<ver>.mjb`/`sonic_v1_<ver>.mjb`).

## 6. Regenerate
`./make_all.sh` (hulls with scipy in env gmr; derived XMLs; .mjb for 3.13 and 3.11; baseline .mjb into out/; SHA256SUMS). Individual steps: `build_hulls.py`, `build_variants.py <ik|sonic> <v1|v2|v2hN> [--mjb]`, `make_full_mjb.py`. Inventory: `inventory.py <ik|sonic>`; contacts: `compare_contacts.py <ik|sonic> <v1|v2> [N]`; memory: `mem_bench.py <ik|sonic> <full|v1|v2> <mjb|xml>`; validation: `drv_gmr.py <full|v1|v2>` + `cmp_gmr.py` (env gmr), `drv_sonic.py <variant>` + `cmp_sonic.py` (env physicalai). `out/` (1.4 GB: baseline mjb, traces, qpos) should not be committed. Nothing is left running; no git commit was made.
