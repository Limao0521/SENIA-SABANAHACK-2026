"""La escena MuJoCo convertida en mallas para el visor WebXR (three.js en las Quest).

Las gafas dibujan la escena ellas mismas (a 72-90 Hz y con el giro de cabeza local, sin latencia de red); el PC solo
manda en cada ciclo dónde están los cuerpos que se mueven. Por eso la escena se exporta una vez:

- ``scene.json``: materiales, texturas, luces y trozos de malla. Los cuerpos fijos se funden en un trozo por material
  ya colocado en el mundo (pocas llamadas de dibujo); cada cuerpo móvil (robot, puertas) lleva sus trozos en su propio
  sistema y el visor los mueve con las poses que llegan por WebSocket, en el orden de ``bodies``.
- ``scene.bin``: los arrays (float32 posiciones/normales/uv, uint32 índices), referenciados como ``[offset, n]``.
- ``tex/<i>.png``: las texturas 2D tal cual las tiene MuJoCo (fila 0 = v 0; el visor no les da la vuelta).

Todo va en coordenadas MuJoCo (z arriba); el visor gira la raíz para pasar a y arriba. Las mallas STL del robot tienen
~1,5 millones de triángulos: se simplifican por agrupación de vértices (``_decimate``) para que las Quest vayan fluidas.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import mujoco
import numpy as np

VISIBLE_GROUPS = (0, 1, 2)          # lo que dibuja el visor de MuJoCo por defecto (el 2 es el techo)
DEFAULT_RGBA = (0.5, 0.5, 0.5, 1.0)
PLANE_HALF = 60.0                   # m, medio lado del suelo "infinito"
SEGMENTS = 24                       # divisiones de cilindros, cápsulas y esferas
MESH_FACES_MAX = 6000               # triángulos por malla tras simplificar (las grandes)
MESH_KEEP = 2000                    # por debajo de esto la malla se deja como está

_GT = mujoco.mjtGeom
_RGB = int(mujoco.mjtTextureRole.mjTEXROLE_RGB)


# ----------------------------------------------------------------------------------------------
# Primitivas (en el sistema de la geometría)
# ----------------------------------------------------------------------------------------------
@dataclass
class Part:
    pos: np.ndarray                  # (n, 3)
    nrm: np.ndarray | None           # (n, 3) o None (sombreado plano en el visor)
    uv: np.ndarray | None            # (n, 2)
    idx: np.ndarray                  # (m, 3)

    def transformed(self, R: np.ndarray, t: np.ndarray) -> "Part":
        return Part(self.pos @ R.T + t, None if self.nrm is None else self.nrm @ R.T, self.uv, self.idx)


def _uv_scale(coord: np.ndarray, half: np.ndarray, repeat, uniform: bool) -> np.ndarray:
    """Coordenadas de textura como MuJoCo: con ``texuniform`` se repite por metro; si no, una vez por cara."""
    if uniform:
        return coord * repeat
    return (coord / (2 * np.where(half > 0, half, 1)) + 0.5) * repeat


def _box(size, repeat, uniform) -> Part:
    pos, nrm, uv, idx = [], [], [], []
    for axis in range(3):
        a, b = [i for i in range(3) if i != axis]          # ejes de la cara: x→(y,z), y→(x,z), z→(x,y)
        for sign in (1, -1):
            n = np.zeros(3)
            n[axis] = sign
            corners = []
            for ca, cb in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
                p = np.zeros(3)
                p[axis], p[a], p[b] = sign * size[axis], ca * size[a], cb * size[b]
                corners.append(p)
            corners = np.array(corners)
            base = len(pos) * 4
            pos.append(corners)
            nrm.append(np.tile(n, (4, 1)))
            uv.append(_uv_scale(corners[:, [a, b]], size[[a, b]], repeat, uniform))
            idx.append(np.array([[0, 1, 2], [0, 2, 3]]) + base)     # _fix_winding los orienta hacia fuera
    part = Part(np.concatenate(pos), np.concatenate(nrm), np.concatenate(uv), np.concatenate(idx))
    return _fix_winding(part)


def _fix_winding(part: Part) -> Part:
    """Da la vuelta a los triángulos cuya normal geométrica no coincide con la normal de sus vértices."""
    if part.nrm is None:
        return part
    p = part.pos[part.idx]
    face_n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
    flip = np.einsum("ij,ij->i", face_n, part.nrm[part.idx[:, 0]]) < 0
    idx = part.idx.copy()
    idx[flip] = idx[flip][:, [0, 2, 1]]
    return Part(part.pos, part.nrm, part.uv, idx)


def _grid_indices(rows: int, cols: int) -> np.ndarray:
    """Triángulos de una rejilla de (rows+1)×(cols+1) vértices."""
    r, c = np.meshgrid(np.arange(rows), np.arange(cols), indexing="ij")
    a = r * (cols + 1) + c
    b, d = a + cols + 1, a + 1
    e = a + cols + 2
    return np.stack([np.stack([a, b, e], -1), np.stack([a, e, d], -1)], 2).reshape(-1, 3)


def _sphere(radii, repeat, uniform, z_offset=0.0, theta=(0.0, math.pi)) -> Part:
    """Esfera o elipsoide (``radii``), o un casquete (``theta``) desplazado ``z_offset`` (para las cápsulas)."""
    rows, cols = SEGMENTS // 2, SEGMENTS
    th = np.linspace(theta[0], theta[1], rows + 1)
    ph = np.linspace(0, 2 * math.pi, cols + 1)
    T, P = np.meshgrid(th, ph, indexing="ij")
    unit = np.stack([np.sin(T) * np.cos(P), np.sin(T) * np.sin(P), np.cos(T)], -1).reshape(-1, 3)
    radii = np.asarray(radii, float)
    pos = unit * radii + [0, 0, z_offset]
    nrm = unit / radii
    nrm /= np.linalg.norm(nrm, axis=1, keepdims=True)
    uv = np.stack([P.ravel() / (2 * math.pi), T.ravel() / math.pi], -1) * repeat
    return _fix_winding(Part(pos, nrm, uv, _grid_indices(rows, cols)))


def _disk(radius, z, up: bool, repeat, uniform) -> Part:
    ph = np.linspace(0, 2 * math.pi, SEGMENTS + 1)[:-1]
    ring = np.stack([radius * np.cos(ph), radius * np.sin(ph), np.full_like(ph, z)], -1)
    pos = np.vstack([[0, 0, z], ring])
    nrm = np.tile([0, 0, 1 if up else -1], (len(pos), 1)).astype(float)
    uv = _uv_scale(pos[:, :2], np.array([radius, radius]), repeat, uniform)
    k = np.arange(SEGMENTS)
    idx = np.stack([np.zeros_like(k), 1 + k, 1 + (k + 1) % SEGMENTS], -1)
    return _fix_winding(Part(pos, nrm, uv, idx))


def _tube(radius, half, repeat, uniform) -> Part:
    ph = np.linspace(0, 2 * math.pi, SEGMENTS + 1)
    z = np.array([-half, half])
    Z, P = np.meshgrid(z, ph, indexing="ij")
    pos = np.stack([radius * np.cos(P), radius * np.sin(P), Z], -1).reshape(-1, 3)
    nrm = np.stack([np.cos(P), np.sin(P), np.zeros_like(P)], -1).reshape(-1, 3)
    if uniform:
        uv = np.stack([P.ravel() * radius, Z.ravel()], -1) * repeat
    else:
        uv = np.stack([P.ravel() / (2 * math.pi), (Z.ravel() + half) / (2 * half)], -1) * repeat
    return _fix_winding(Part(pos, nrm, uv, _grid_indices(1, SEGMENTS)))


def _merge(parts: list[Part]) -> Part:
    pos, nrm, uv, idx, n = [], [], [], [], 0
    for p in parts:
        pos.append(p.pos)
        nrm.append(p.nrm)
        uv.append(p.uv)
        idx.append(p.idx + n)
        n += len(p.pos)
    return Part(np.concatenate(pos), None if any(x is None for x in nrm) else np.concatenate(nrm),
                None if any(x is None for x in uv) else np.concatenate(uv), np.concatenate(idx))


def _decimate(vert: np.ndarray, face: np.ndarray, target: int) -> tuple[np.ndarray, np.ndarray]:
    """Simplifica una malla agrupando los vértices en celdas cúbicas, cada vez más grandes hasta bajar de
    ``target`` triángulos. Basta para piezas que se ven a un metro o más."""
    extent = float(np.max(vert.max(0) - vert.min(0)))
    cell = extent / 200
    while True:
        key = np.floor(vert / cell).astype(np.int64)
        _, inverse, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
        inverse = inverse.ravel()
        new_vert = np.zeros((len(counts), 3))
        np.add.at(new_vert, inverse, vert)
        new_vert /= counts[:, None]
        f = inverse[face]
        f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
        _, keep = np.unique(np.sort(f, axis=1), axis=0, return_index=True)
        f = f[np.sort(keep)]
        if len(f) <= target or cell > extent / 8:
            return new_vert, f
        cell *= 1.3


def _mesh(model: mujoco.MjModel, mesh: int) -> Part:
    va, vn = model.mesh_vertadr[mesh], model.mesh_vertnum[mesh]
    fa, fn = model.mesh_faceadr[mesh], model.mesh_facenum[mesh]
    vert = model.mesh_vert[va:va + vn].astype(float)
    face = model.mesh_face[fa:fa + fn].astype(np.int64)
    ta = model.mesh_texcoordadr[mesh]
    if ta >= 0:                         # con textura: un vértice por esquina de cara, con su uv y su normal
        tex = model.mesh_texcoord[ta:ta + model.mesh_texcoordnum[mesh]]
        ftex = model.mesh_facetexcoord[fa:fa + fn]
        pos = vert[face].reshape(-1, 3)
        uv = tex[ftex].reshape(-1, 2)
        p = vert[face]
        n = np.cross(p[:, 1] - p[:, 0], p[:, 2] - p[:, 0])
        n /= np.linalg.norm(n, axis=1, keepdims=True)
        nrm = np.repeat(n, 3, axis=0)
        return Part(pos, nrm, uv, np.arange(len(pos)).reshape(-1, 3))
    if fn > MESH_KEEP:
        vert, face = _decimate(vert, face, max(MESH_KEEP, min(MESH_FACES_MAX, fn // 12)))
    return Part(vert, None, None, face)         # sin normales: el visor la sombrea plana


def geom_part(model: mujoco.MjModel, g: int, repeat=(1.0, 1.0), uniform=False) -> Part | None:
    t, size = int(model.geom_type[g]), model.geom_size[g]
    repeat = np.asarray(repeat, float)
    if t == _GT.mjGEOM_BOX:
        return _box(size, repeat, uniform)
    if t == _GT.mjGEOM_SPHERE:
        return _sphere([size[0]] * 3, repeat, uniform)
    if t == _GT.mjGEOM_ELLIPSOID:
        return _sphere(size, repeat, uniform)
    if t == _GT.mjGEOM_CYLINDER:
        r, h = size[0], size[1]
        return _merge([_tube(r, h, repeat, uniform), _disk(r, h, True, repeat, uniform),
                       _disk(r, -h, False, repeat, uniform)])
    if t == _GT.mjGEOM_CAPSULE:
        r, h = size[0], size[1]
        return _merge([_tube(r, h, repeat, uniform),
                       _sphere([r] * 3, repeat, uniform, h, (0.0, math.pi / 2)),
                       _sphere([r] * 3, repeat, uniform, -h, (math.pi / 2, math.pi))])
    if t == _GT.mjGEOM_PLANE:
        half = np.array([size[0] or PLANE_HALF, size[1] or PLANE_HALF, 0.0])
        return _box(half, repeat, uniform if size[0] else True)
    if t == _GT.mjGEOM_MESH:
        return _mesh(model, int(model.geom_dataid[g]))
    return None


def _plane_fix(part: Part, t: int) -> Part:
    """Del suelo solo interesa la cara de arriba (el resto de la caja plana queda bajo tierra)."""
    if t != _GT.mjGEOM_PLANE:
        return part
    return Part(part.pos[16:20], part.nrm[16:20], part.uv[16:20], part.idx[8:10] - 16)


# ----------------------------------------------------------------------------------------------
# Escena
# ----------------------------------------------------------------------------------------------
@dataclass
class _Blob:
    data: bytearray = field(default_factory=bytearray)

    def add(self, arr: np.ndarray, dtype) -> list[int]:
        a = np.ascontiguousarray(arr, dtype=dtype).ravel()
        offset = len(self.data)
        self.data += a.tobytes()
        return [offset, int(a.size)]


def _material_of(model: mujoco.MjModel, g: int, flat: bool) -> tuple:
    mid = int(model.geom_matid[g])
    rgba = tuple(float(x) for x in model.geom_rgba[g])
    if mid < 0:
        return rgba, 0.0, 0.5, 0.5, -1, flat
    tex = int(model.mat_texid[mid, _RGB])
    if tex >= 0 and int(model.tex_type[tex]) != int(mujoco.mjtTexture.mjTEXTURE_2D):
        tex = -1
    if np.allclose(rgba, DEFAULT_RGBA):
        rgba = tuple(float(x) for x in model.mat_rgba[mid])
    return (rgba, float(model.mat_emission[mid]), float(model.mat_specular[mid]), float(model.mat_shininess[mid]),
            tex, flat)


def _texture_png(model: mujoco.MjModel, tex: int, path: Path) -> None:
    from PIL import Image
    w, h, c = int(model.tex_width[tex]), int(model.tex_height[tex]), int(model.tex_nchannel[tex])
    a = model.tex_data[model.tex_adr[tex]:model.tex_adr[tex] + w * h * c].reshape(h, w, c)
    Image.fromarray(np.ascontiguousarray(a[..., :3] if c >= 3 else a[..., 0])).save(path, optimize=False)


def _sky(model: mujoco.MjModel) -> list[list[float]]:
    """Colores arriba y en el horizonte del cielo degradado (si hay skybox)."""
    for t in range(model.ntex):
        if int(model.tex_type[t]) == int(mujoco.mjtTexture.mjTEXTURE_SKYBOX):
            w, h, c = int(model.tex_width[t]), int(model.tex_height[t]), int(model.tex_nchannel[t])
            a = model.tex_data[model.tex_adr[t]:model.tex_adr[t] + w * h * c].reshape(h, w, c)[..., :3] / 255.0
            return [a[h // 12].mean(0).tolist(), a[h // 2 - 2].mean(0).tolist()]
    return [[0.55, 0.7, 0.88], [0.92, 0.93, 0.95]]


def _lights(model: mujoco.MjModel, data: mujoco.MjData) -> list[dict]:
    out = []
    for i in range(model.nlight):
        if not model.light_active[i] or model.body_weldid[model.light_bodyid[i]] != 0:
            continue
        kind = int(model.light_type[i])
        out.append({"type": {int(mujoco.mjtLightType.mjLIGHT_DIRECTIONAL): "directional",
                             int(mujoco.mjtLightType.mjLIGHT_SPOT): "spot"}.get(kind, "point"),
                    "pos": data.light_xpos[i].tolist(), "dir": data.light_xdir[i].tolist(),
                    "diffuse": model.light_diffuse[i].tolist(), "ambient": model.light_ambient[i].tolist(),
                    "specular": model.light_specular[i].tolist(), "cutoff": float(model.light_cutoff[i]),
                    "exponent": float(model.light_exponent[i])})
    return out


def export_scene(model: mujoco.MjModel, data: mujoco.MjData, out_dir: Path,
                 groups=VISIBLE_GROUPS) -> dict:
    """Escribe ``scene.json``, ``scene.bin`` y ``tex/*.png`` en ``out_dir`` y devuelve el JSON (con
    ``bodies``: los ids MuJoCo de los cuerpos móviles, en el orden en que llegan sus poses)."""
    out_dir = Path(out_dir)
    (out_dir / "tex").mkdir(parents=True, exist_ok=True)
    mujoco.mj_forward(model, data)
    materials: dict[tuple, int] = {}
    static: dict[int, list[Part]] = {}
    moving: dict[int, dict[int, list[Part]]] = {}
    for g in range(model.ngeom):
        if int(model.geom_group[g]) not in groups or model.geom_rgba[g][3] == 0:
            continue
        t = int(model.geom_type[g])
        flat = bool(t == _GT.mjGEOM_MESH and model.mesh_texcoordadr[model.geom_dataid[g]] < 0)
        key = _material_of(model, g, flat)
        if key[0][3] == 0:
            continue
        mid = int(model.geom_matid[g])
        repeat = model.mat_texrepeat[mid] if mid >= 0 else (1.0, 1.0)
        uniform = bool(model.mat_texuniform[mid]) if mid >= 0 else False
        part = geom_part(model, g, repeat, uniform)
        if part is None:
            continue
        part = _plane_fix(part, t)
        m = materials.setdefault(key, len(materials))
        body = int(model.geom_bodyid[g])
        if model.body_weldid[body] == 0 and model.body_mocapid[body] < 0:
            static.setdefault(m, []).append(part.transformed(data.geom_xmat[g].reshape(3, 3), data.geom_xpos[g]))
        else:
            R = np.zeros(9)
            mujoco.mju_quat2Mat(R, model.geom_quat[g])
            moving.setdefault(body, {}).setdefault(m, []).append(part.transformed(R.reshape(3, 3),
                                                                                 model.geom_pos[g]))
    blob = _Blob()

    def chunk(m: int, parts: list[Part]) -> dict:
        p = _merge(parts)
        c = {"material": m, "pos": blob.add(p.pos, np.float32), "idx": blob.add(p.idx, np.uint32)}
        if p.nrm is not None:
            c["nrm"] = blob.add(p.nrm, np.float32)
        if p.uv is not None and list(materials)[m][4] >= 0:
            c["uv"] = blob.add(p.uv, np.float32)
        return c

    textures = {}
    mats = []
    for (rgba, emission, specular, shininess, tex, flat), m in materials.items():
        if tex >= 0 and tex not in textures:
            textures[tex] = len(textures)
            _texture_png(model, tex, out_dir / "tex" / f"{textures[tex]}.png")
        mats.append({"rgba": list(rgba), "emission": emission, "specular": specular, "shininess": shininess,
                     "texture": textures.get(tex, -1), "flat": flat})
    scene = {
        "materials": mats,
        "textures": [f"tex/{i}.png" for i in range(len(textures))],
        "static": [chunk(m, parts) for m, parts in sorted(static.items())],
        "bodies": [], "body_names": [], "body_chunks": [],
        "lights": _lights(model, data),
        "headlight": {"ambient": model.vis.headlight.ambient.tolist(),
                      "diffuse": model.vis.headlight.diffuse.tolist(),
                      "active": bool(model.vis.headlight.active)},
        "sky": _sky(model),
    }
    for body, by_mat in sorted(moving.items()):
        scene["bodies"].append(body)
        scene["body_names"].append(mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_BODY, body) or str(body))
        scene["body_chunks"].append([chunk(m, parts) for m, parts in sorted(by_mat.items())])
    scene["triangles"] = int(sum(c["idx"][1] for c in scene["static"]) +
                             sum(c["idx"][1] for cs in scene["body_chunks"] for c in cs)) // 3
    (out_dir / "scene.bin").write_bytes(bytes(blob.data))
    (out_dir / "scene.json").write_text(json.dumps(scene))
    return scene
