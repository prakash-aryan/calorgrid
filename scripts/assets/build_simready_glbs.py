"""Build compressed CC BY 4.0 SimReady basement hardware GLB assets.

Stage 1 runs locally or on an optional remote USD worker (configured via
``SIMREADY_REMOTE_HOST`` and ``SIMREADY_RAW_DIR``) using Python
(pxr, trimesh, PIL, open3d) to download public Hugging Face packages
(token=False) and convert USD stages to Y-up metric PBR GLB intermediates.

Stage 2 stages intermediates into a temporary directory locally, compresses
them with @gltf-transform/cli (Draco + WebP + meshoptimizer simplify), updates
assets/manifest.json and assets/attributions.json and cleans up staging files.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

REMOTE_HOST = os.environ.get("SIMREADY_REMOTE_HOST", "")
REMOTE_PYTHON = os.environ.get("SIMREADY_REMOTE_PYTHON", "python3")
REMOTE_BASE = os.environ.get(
    "SIMREADY_RAW_DIR", str(Path(tempfile.gettempdir()) / "calorgrid_simready")
)
REMOTE_RAW_SIMREADY = f"{REMOTE_BASE}/raw_simready"
REMOTE_RAW_GLB = f"{REMOTE_BASE}/raw_glb"
REMOTE_CACHED_SIMREADY = os.environ.get(
    "SIMREADY_CACHED_DIR", f"{REMOTE_BASE}/cached_simready"
)
LOCAL_STAGE_DIR = Path(tempfile.gettempdir()) / "calorgrid_glb_stage"

HARDWARE_SPECS: list[dict[str, Any]] = [
    {
        "name": "hardware_server_rack",
        "label": "Vertiv 16OCP ORV3 Compute Rack",
        "repo_id": "Vertiv/simready-assets",
        "pkg": "16OCP_ORV3_racks_Computer_racks",
        "usd_rel": (
            "16OCP_ORV3_racks_Computer_racks/simready_usd/"
            "_16OCP_ORV3_racks_Computer_racks_main.usd"
        ),
        "skip_internal": False,
        "pre_decimate_faces": 150000,
        "pre_decimate_ratio": 0.65,
        "simplify_ratio": 0.45,
        "tex_size": 1024,
        "attribution": "Vertiv (CC BY 4.0)",
    },
    {
        "name": "hardware_cdu_exchanger",
        "label": "Vertiv Liebert XDU1350 Coolant Distribution Unit",
        "repo_id": "Vertiv/simready-assets",
        "pkg": "XDU1350",
        "usd_rel": "XDU1350/simready_usd/XDU1350_TOP_main.usd",
        "skip_internal": True,
        "pre_decimate_faces": 50000,
        "pre_decimate_ratio": 0.35,
        "simplify_ratio": 0.25,
        "tex_size": 1024,
        "attribution": "Vertiv (CC BY 4.0)",
    },
    {
        "name": "hardware_power_panel",
        "label": "Vertiv NVD RPP 1200-480V Power Distribution Panel",
        "repo_id": "Vertiv/simready-assets",
        "pkg": "NVD_RPP_1200_480_XT210_FA",
        "usd_rel": "NVD_RPP_1200_480_XT210_FA/simready_usd/NVD_RPP_1200_480_XT210_FA_main.usd",
        "skip_internal": True,
        "pre_decimate_faces": 40000,
        "pre_decimate_ratio": 0.25,
        "simplify_ratio": 0.20,
        "tex_size": 1024,
        "attribution": "Vertiv (CC BY 4.0)",
    },
    {
        "name": "hardware_buffer_tank",
        "label": "Stainless Steel Buffer Vessel (1,000 L)",
        "repo_id": "nvidia/simready-assets",
        "pkg": "ibcTank_steel_stainless_a01",
        "fallback_local_pkg": "ibcTank_a02",
        "skip_internal": False,
        "pre_decimate_faces": 0,
        "pre_decimate_ratio": 1.0,
        "simplify_ratio": 0.25,
        "tex_size": 1024,
        "attribution": "NVIDIA (CC BY 4.0)",
    },
    {
        "name": "hardware_piping",
        "label": "Hydronic Manifold & Process Piping",
        "repo_id": "nvidia/simready-assets",
        "pkg": "pipe_plastic_gray_b08",
        "skip_internal": False,
        "pre_decimate_faces": 0,
        "pre_decimate_ratio": 1.0,
        "simplify_ratio": 0.25,
        "tex_size": 1024,
        "attribution": "NVIDIA (CC BY 4.0)",
    },
    {
        "name": "hardware_valve_3way",
        "label": "Industrial Valve Assembly",
        "repo_id": "ForgeCAD/simready-assets",
        "pkg": "gate_valve",
        "usd_rel": "gate_valve/simready_usd/gate_valve.usda",
        "skip_internal": False,
        "pre_decimate_faces": 0,
        "pre_decimate_ratio": 1.0,
        "simplify_ratio": 0.25,
        "tex_size": 1024,
        "attribution": "ForgeCAD (CC BY 4.0)",
    },
    {
        "name": "hardware_basement_enclosure",
        "label": "Concrete Basement Plant-Room Enclosure",
        "repo_id": None,
        "pkg": "enclosure_bundle",
        "skip_internal": False,
        "pre_decimate_faces": 0,
        "pre_decimate_ratio": 1.0,
        "simplify_ratio": 0.25,
        "tex_size": 512,
        "attribution": "NVIDIA (CC BY 4.0)",
    },
]

VERTIV_MDL_FALLBACK: dict[str, tuple[list[int], float, float]] = {
    "black": ([26, 28, 32, 255], 0.25, 0.65),
    "dark": ([38, 40, 45, 255], 0.25, 0.60),
    "rubber": ([30, 32, 35, 255], 0.05, 0.85),
    "aluminum": ([185, 192, 198, 255], 0.85, 0.32),
    "stainless": ([195, 200, 204, 255], 0.90, 0.28),
    "steel": ([145, 152, 160, 255], 0.80, 0.35),
    "blued": ([78, 98, 128, 255], 0.75, 0.38),
    "orange": ([220, 96, 28, 255], 0.10, 0.45),
    "retroreflective": ([228, 108, 32, 255], 0.15, 0.40),
    "whitemode": ([225, 228, 232, 255], 0.05, 0.60),
    "carpaint": ([42, 46, 52, 255], 0.35, 0.40),
    "copper": ([196, 118, 72, 255], 0.90, 0.30),
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Stage 1: USD -> Raw GLB Worker
# ---------------------------------------------------------------------------


def _resolve_asset_path(prim: Any, asset_attr: Any) -> Path | None:
    if asset_attr is None:
        return None
    resolved = getattr(asset_attr, "resolvedPath", "") or ""
    if resolved and Path(resolved).exists():
        return Path(resolved)
    raw = getattr(asset_attr, "path", "") or ""
    if not raw:
        return None
    for spec in prim.GetPrimStack():
        p = spec.layer.ComputeAbsolutePath(raw)
        if p and Path(p).exists():
            return Path(p)
    return None


def _load_pil_image(path: Path, max_px: int) -> Any:
    from PIL import Image

    img = Image.open(path)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    w, h = img.size
    if max(w, h) > max_px:
        scale = max_px / float(max(w, h))
        img = img.resize(
            (max(1, int(round(w * scale))), max(1, int(round(h * scale)))),
            Image.Resampling.LANCZOS,
        )
    return img


def _connected_prim(port: Any) -> Any:
    if not port or not port.HasConnectedSource():
        return None
    conn = port.GetConnectedSource()
    if not conn:
        return None
    src = conn[0]
    return src.GetPrim() if hasattr(src, "GetPrim") else src.source.GetPrim()


def _extract_uv_transform(
    tex_shader: Any,
) -> tuple[tuple[float, float], float, tuple[float, float]]:
    from pxr import UsdShade

    st_prim = _connected_prim(tex_shader.GetInput("st"))
    if st_prim is None:
        return (1.0, 1.0), 0.0, (0.0, 0.0)
    src_shader = UsdShade.Shader(st_prim)
    sid = src_shader.GetIdAttr().Get() or ""
    if "UsdTransform2d" not in sid:
        return (1.0, 1.0), 0.0, (0.0, 0.0)
    sc = src_shader.GetInput("scale")
    rot = src_shader.GetInput("rotation")
    tr = src_shader.GetInput("translation")
    scale_v = tuple(sc.Get()) if (sc and sc.Get() is not None) else (1.0, 1.0)
    rot_v = float(rot.Get()) if (rot and rot.Get() is not None) else 0.0
    tr_v = tuple(tr.Get()) if (tr and tr.Get() is not None) else (0.0, 0.0)
    return (float(scale_v[0]), float(scale_v[1])), rot_v, (float(tr_v[0]), float(tr_v[1]))


def _apply_uv_transform(
    uv: Any, uv_xform: tuple[tuple[float, float], float, tuple[float, float]]
) -> Any:
    import numpy as np

    (sx, sy), rot_deg, (tx, ty) = uv_xform
    out = uv.astype(np.float64).copy()
    out[:, 0] *= sx
    out[:, 1] *= sy
    if abs(rot_deg) > 1e-6:
        rad = math.radians(rot_deg)
        c, s = math.cos(rad), math.sin(rad)
        x0, y0 = out[:, 0].copy(), out[:, 1].copy()
        out[:, 0] = c * x0 - s * y0
        out[:, 1] = s * x0 + c * y0
    out[:, 0] += tx
    out[:, 1] += ty
    return out.astype(np.float32)


def _extract_pbr_from_material(
    mat_prim: Any,
    mesh_prim: Any,
    max_tex_px: int,
    tex_cache: dict[tuple[str, int], Any],
) -> tuple[dict[str, Any], tuple[tuple[float, float], float, tuple[float, float]]]:
    from pxr import UsdGeom, UsdShade

    default_xform = ((1.0, 1.0), 0.0, (0.0, 0.0))

    def cached_img(p: Path) -> Any:
        key = (str(p), max_tex_px)
        if key not in tex_cache:
            tex_cache[key] = _load_pil_image(p, max_tex_px)
        return tex_cache[key]

    if mat_prim and mat_prim.IsValid():
        mat_api = UsdShade.Material(mat_prim)
        surf_prim = _connected_prim(mat_api.GetSurfaceOutput())
        if surf_prim is not None:
            shader = UsdShade.Shader(surf_prim)
            if (shader.GetIdAttr().Get() or "") == "UsdPreviewSurface":
                uv_xform = default_xform
                mat_kwargs: dict[str, Any] = {
                    "name": mat_prim.GetName(),
                    "doubleSided": True,
                }
                diff_in = shader.GetInput("diffuseColor")
                diff_prim = _connected_prim(diff_in)
                if diff_prim is not None:
                    tx = UsdShade.Shader(diff_prim)
                    file_inp = tx.GetInput("file")
                    fpath = _resolve_asset_path(tx.GetPrim(), file_inp.Get() if file_inp else None)
                    if fpath is not None:
                        mat_kwargs["baseColorTexture"] = cached_img(fpath)
                        uv_xform = _extract_uv_transform(tx)
                elif diff_in and diff_in.Get() is not None:
                    c = diff_in.Get()
                    mat_kwargs["baseColorFactor"] = [
                        int(round(max(0.0, min(1.0, c[0])) * 255)),
                        int(round(max(0.0, min(1.0, c[1])) * 255)),
                        int(round(max(0.0, min(1.0, c[2])) * 255)),
                        255,
                    ]

                orm_path = None
                for attr_name in ("roughness", "metallic"):
                    conn_prim = _connected_prim(shader.GetInput(attr_name))
                    if conn_prim is not None:
                        tx = UsdShade.Shader(conn_prim)
                        file_inp = tx.GetInput("file")
                        orm_path = _resolve_asset_path(
                            tx.GetPrim(), file_inp.Get() if file_inp else None
                        )
                        if orm_path is not None:
                            break
                if orm_path is not None:
                    orm_img = cached_img(orm_path)
                    mat_kwargs["metallicRoughnessTexture"] = orm_img
                    mat_kwargs["occlusionTexture"] = orm_img
                    mat_kwargs["metallicFactor"] = 1.0
                    mat_kwargs["roughnessFactor"] = 1.0
                else:
                    m_in = shader.GetInput("metallic")
                    r_in = shader.GetInput("roughness")
                    mat_kwargs["metallicFactor"] = (
                        float(m_in.Get()) if (m_in and m_in.Get() is not None) else 0.1
                    )
                    mat_kwargs["roughnessFactor"] = (
                        float(r_in.Get()) if (r_in and r_in.Get() is not None) else 0.6
                    )

                norm_prim = _connected_prim(shader.GetInput("normal"))
                if norm_prim is not None:
                    tx = UsdShade.Shader(norm_prim)
                    file_inp = tx.GetInput("file")
                    npath = _resolve_asset_path(tx.GetPrim(), file_inp.Get() if file_inp else None)
                    if npath is not None:
                        mat_kwargs["normalTexture"] = cached_img(npath)

                return mat_kwargs, uv_xform

    # Fallback for MDL-only materials (Vertiv) using primvars and material name
    mat_name = mat_prim.GetName() if (mat_prim and mat_prim.IsValid()) else "vertiv_default"
    mlow = mat_name.lower()
    rgba = [120, 126, 134, 255]
    metallic = 0.55
    roughness = 0.45
    for k, (col, met, rgh) in VERTIV_MDL_FALLBACK.items():
        if k in mlow:
            rgba, metallic, roughness = list(col), met, rgh
            break

    pv_api = UsdGeom.PrimvarsAPI(mesh_prim)
    dc = pv_api.GetPrimvar("displayColor")
    if dc and dc.HasValue():
        vals = dc.Get()
        if vals and len(vals) > 0:
            c = vals[0]
            rgba = [
                int(round(max(0.0, min(1.0, c[0])) * 255)),
                int(round(max(0.0, min(1.0, c[1])) * 255)),
                int(round(max(0.0, min(1.0, c[2])) * 255)),
                255,
            ]
    mp = pv_api.GetPrimvar("metallic")
    if mp and mp.HasValue():
        v = mp.Get()
        if v is not None and len(v) > 0:
            metallic = float(v[0])
    rp = pv_api.GetPrimvar("roughness")
    if rp and rp.HasValue():
        v = rp.Get()
        if v is not None and len(v) > 0:
            roughness = float(v[0])

    return (
        {
            "name": mat_name,
            "baseColorFactor": rgba,
            "metallicFactor": metallic,
            "roughnessFactor": roughness,
            "doubleSided": True,
        },
        default_xform,
    )


def _read_mesh_uvs(mesh_prim: Any) -> tuple[Any, Any, str]:
    import numpy as np
    from pxr import UsdGeom

    pv_api = UsdGeom.PrimvarsAPI(mesh_prim)
    st = pv_api.GetPrimvar("st")
    if not st or not st.HasValue():
        return None, None, "none"
    vals = np.asarray(st.Get(), dtype=np.float32)
    if len(vals) == 0:
        return None, None, "none"
    idx = np.asarray(st.GetIndices(), dtype=np.int32) if st.IsIndexed() else None
    interp = st.GetInterpolation()
    return vals, idx, str(interp)


def _unweld_bucket(
    pts: Any,
    tri_v: Any,
    tri_c: Any,
    uv_vals: Any,
    uv_idx: Any,
    uv_interp: str,
    uv_xform: tuple[tuple[float, float], float, tuple[float, float]],
) -> tuple[Any, Any, Any]:
    import numpy as np

    if uv_vals is None or uv_interp not in ("faceVarying", "vertex", "varying"):
        u_idx, inv = np.unique(tri_v, return_inverse=True)
        return pts[u_idx], inv.reshape(-1, 3), None

    if uv_interp == "faceVarying":
        c_flat = tri_c.ravel()
        uv_corner_idx = uv_idx[c_flat] if uv_idx is not None else c_flat
        raw_uv = uv_vals[np.clip(uv_corner_idx, 0, len(uv_vals) - 1)]
    else:
        v_flat = tri_v.ravel()
        uv_vert_idx = uv_idx[v_flat] if uv_idx is not None else v_flat
        raw_uv = uv_vals[np.clip(uv_vert_idx, 0, len(uv_vals) - 1)]

    xformed_uv = _apply_uv_transform(raw_uv, uv_xform)
    v_flat = tri_v.ravel()
    uv_q = np.round(xformed_uv, 5)

    # Deduplicate (vertex_idx, u_q, v_q) cleanly using structured view
    keys = np.empty(
        len(v_flat),
        dtype=[("v", np.int32), ("u", np.float32), ("w", np.float32)],
    )
    keys["v"] = v_flat
    keys["u"] = uv_q[:, 0]
    keys["w"] = uv_q[:, 1]
    _, first_occ, inv = np.unique(keys, return_index=True, return_inverse=True)
    new_verts = pts[v_flat[first_occ]]
    new_uvs = xformed_uv[first_occ]
    new_faces = inv.reshape(-1, 3).astype(np.int32)
    return new_verts, new_faces, new_uvs


def _concat_meshes_preserving_uv(meshes: list[Any]) -> Any:
    import numpy as np
    import trimesh

    if len(meshes) == 1:
        return meshes[0]
    all_v = []
    all_f = []
    all_uv = []
    has_uv = all(
        hasattr(m.visual, "uv") and m.visual.uv is not None and len(m.visual.uv) == len(m.vertices)
        for m in meshes
    )
    offset = 0
    for m in meshes:
        all_v.append(m.vertices)
        all_f.append(m.faces + offset)
        if has_uv:
            all_uv.append(m.visual.uv)
        offset += len(m.vertices)
    verts = np.vstack(all_v).astype(np.float32)
    faces = np.vstack(all_f).astype(np.int32)
    mat = meshes[0].visual.material
    if has_uv:
        uv = np.vstack(all_uv).astype(np.float32)
        vis = trimesh.visual.TextureVisuals(uv=uv, material=mat)
    else:
        vis = trimesh.visual.TextureVisuals(material=mat)
    return trimesh.Trimesh(vertices=verts, faces=faces, visual=vis, process=False)


def _decimate_untextured(mesh: Any, target_faces: int) -> Any:
    import numpy as np
    import open3d as o3d
    import trimesh

    if len(mesh.faces) <= target_faces:
        return mesh
    o3m = o3d.geometry.TriangleMesh(
        o3d.utility.Vector3dVector(mesh.vertices.astype(np.float64)),
        o3d.utility.Vector3iVector(mesh.faces.astype(np.int32)),
    )
    o3m = o3m.simplify_quadric_decimation(target_number_of_triangles=int(target_faces))
    if len(o3m.triangles) == 0:
        return mesh
    out = trimesh.Trimesh(
        vertices=np.asarray(o3m.vertices, dtype=np.float32),
        faces=np.asarray(o3m.triangles, dtype=np.int32),
        process=False,
    )
    out.visual = trimesh.visual.TextureVisuals(material=mesh.visual.material)
    return out


def _clip_mesh_y_max(mesh: Any, y_max: float) -> Any:
    import numpy as np
    import trimesh

    verts = mesh.vertices.astype(np.float64)
    faces = mesh.faces.astype(np.int32)
    has_uv = (
        hasattr(mesh.visual, "uv")
        and mesh.visual.uv is not None
        and len(mesh.visual.uv) == len(verts)
    )
    uvs = mesh.visual.uv.astype(np.float64) if has_uv else None

    keep = verts[:, 1] <= y_max + 1e-5
    if keep.all():
        return mesh

    new_verts = verts.tolist()
    new_uvs = uvs.tolist() if uvs is not None else None
    new_faces: list[list[int]] = []

    def interp(i0: int, i1: int) -> int:
        y0, y1 = verts[i0, 1], verts[i1, 1]
        t = (y_max - y0) / (y1 - y0) if abs(y1 - y0) > 1e-9 else 0.5
        t = max(0.0, min(1.0, t))
        p = verts[i0] + t * (verts[i1] - verts[i0])
        p[1] = y_max
        idx = len(new_verts)
        new_verts.append(p.tolist())
        if new_uvs is not None and uvs is not None:
            uv_p = uvs[i0] + t * (uvs[i1] - uvs[i0])
            new_uvs.append(uv_p.tolist())
        return idx

    for f in faces:
        ins = [i for i in f if verts[i, 1] <= y_max]
        outs = [i for i in f if verts[i, 1] > y_max]
        if len(ins) == 3:
            new_faces.append(f.tolist())
        elif len(ins) == 0:
            continue
        elif len(ins) == 1:
            a = int(ins[0])
            b, c = int(outs[0]), int(outs[1])
            # Preserve winding order relative to original face
            pos_a = list(f).index(a)
            b = int(f[(pos_a + 1) % 3])
            c = int(f[(pos_a + 2) % 3])
            ab = interp(a, b)
            ac = interp(a, c)
            new_faces.append([a, ab, ac])
        else:
            o = int(outs[0])
            pos_o = list(f).index(o)
            a = int(f[(pos_o + 1) % 3])
            b = int(f[(pos_o + 2) % 3])
            oa = interp(o, a)
            ob = interp(o, b)
            new_faces.append([a, b, ob])
            new_faces.append([a, ob, oa])

    if not new_faces:
        return None
    v_arr = np.array(new_verts, dtype=np.float32)
    f_arr = np.array(new_faces, dtype=np.int32)
    u_idx, inv = np.unique(f_arr, return_inverse=True)
    v_comp = v_arr[u_idx]
    f_comp = inv.reshape(-1, 3)
    if new_uvs is not None:
        uv_arr = np.array(new_uvs, dtype=np.float32)[u_idx]
        vis = trimesh.visual.TextureVisuals(uv=uv_arr, material=mesh.visual.material)
    else:
        vis = trimesh.visual.TextureVisuals(material=mesh.visual.material)
    return trimesh.Trimesh(vertices=v_comp, faces=f_comp, visual=vis, process=False)


def _convert_usd_to_meshes(
    usd_path: Path,
    skip_internal: bool,
    max_tex_px: int,
    pre_decimate_faces: int,
    pre_decimate_ratio: float,
) -> list[tuple[str, Any]]:
    import numpy as np
    import trimesh
    from pxr import Usd, UsdGeom, UsdPhysics, UsdShade

    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        raise RuntimeError(f"Failed to open USD stage {usd_path}")

    # Disable physics and select clean visual variants when available
    model_prim = stage.GetPrimAtPath("/Model")
    if model_prim and model_prim.IsValid():
        vsets = model_prim.GetVariantSets()
        if vsets.HasVariantSet("physics"):
            vsets.GetVariantSet("physics").SetVariantSelection("none")
        if vsets.HasVariantSet("finish"):
            vsets.GetVariantSet("finish").SetVariantSelection("red_wheel")
    for p in stage.Traverse():
        if p.HasVariantSets():
            vsets = p.GetVariantSets()
            for vname in ("PhysX", "Newton", "MuJoCo"):
                if vsets.HasVariantSet(vname):
                    vs = vsets.GetVariantSet(vname)
                    if "Disabled" in vs.GetVariantNames():
                        vs.SetVariantSelection("Disabled")

    mpu = float(UsdGeom.GetStageMetersPerUnit(stage) or 1.0)
    up_axis = str(UsdGeom.GetStageUpAxis(stage) or "Z")
    xform_cache = UsdGeom.XformCache()
    tex_cache: dict[tuple[str, int], Any] = {}

    # Group meshes by material signature
    buckets: dict[str, list[Any]] = {}
    mat_objs: dict[str, Any] = {}
    tex_keys = (
        "baseColorTexture",
        "metallicRoughnessTexture",
        "occlusionTexture",
        "normalTexture",
    )

    predicate = Usd.TraverseInstanceProxies(
        Usd.PrimIsActive & Usd.PrimIsDefined & ~Usd.PrimIsAbstract
    )
    for prim in stage.Traverse(predicate):
        path_str = str(prim.GetPath())
        if any(
            tok in path_str for tok in ("ConnectionPoints", "Connection_Points", "PhysicsMaterials")
        ):
            continue
        if skip_internal and "/internal" in path_str.lower():
            continue
        if prim.IsA(UsdPhysics.Scene) or prim.IsA(UsdPhysics.Joint):
            continue
        if not prim.IsA(UsdGeom.Mesh):
            continue
        img_api = UsdGeom.Imageable(prim)
        purpose = img_api.GetPurposeAttr().Get()
        if purpose in ("guide", "proxy"):
            continue

        usd_mesh = UsdGeom.Mesh(prim)
        pts_raw = usd_mesh.GetPointsAttr().Get()
        fvc_raw = usd_mesh.GetFaceVertexCountsAttr().Get()
        fvi_raw = usd_mesh.GetFaceVertexIndicesAttr().Get()
        if not pts_raw or not fvc_raw or not fvi_raw:
            continue

        pts = np.asarray(pts_raw, dtype=np.float64) * mpu
        mat_world = np.array(xform_cache.GetLocalToWorldTransform(prim), dtype=np.float64).T
        mat_world[:3, 3] *= mpu
        pts_world = (mat_world[:3, :3] @ pts.T).T + mat_world[:3, 3]
        if up_axis == "Z":
            pts_world = np.column_stack([pts_world[:, 0], pts_world[:, 2], -pts_world[:, 1]])
        pts_world = pts_world.astype(np.float32)

        fvc = np.asarray(fvc_raw, dtype=np.int32)
        fvi = np.asarray(fvi_raw, dtype=np.int32)

        # Build triangle vertex indices and triangle corner indices + parent face index
        tri_v_list: list[list[int]] = []
        tri_c_list: list[list[int]] = []
        tri_face_id: list[int] = []
        c_off = 0
        for fi, cnt in enumerate(fvc.tolist()):
            if cnt >= 3:
                for k in range(1, cnt - 1):
                    tri_v_list.append(
                        [int(fvi[c_off]), int(fvi[c_off + k]), int(fvi[c_off + k + 1])]
                    )
                    tri_c_list.append([c_off, c_off + k, c_off + k + 1])
                    tri_face_id.append(fi)
            c_off += cnt
        if not tri_v_list:
            continue
        tri_v = np.array(tri_v_list, dtype=np.int32)
        tri_c = np.array(tri_c_list, dtype=np.int32)
        tri_fid = np.array(tri_face_id, dtype=np.int32)

        uv_vals, uv_idx, uv_interp = _read_mesh_uvs(prim)

        # Partition by UsdGeom.Subset face indices
        subsets = UsdGeom.Subset.GetAllGeomSubsets(UsdGeom.Imageable(prim))
        face_subsets: list[tuple[Any, Any]] = []
        assigned = np.zeros(len(fvc), dtype=bool)
        for sub in subsets:
            if sub.GetElementTypeAttr().Get() != "face":
                continue
            ind = sub.GetIndicesAttr().Get()
            if not ind:
                continue
            f_idx = np.asarray(ind, dtype=np.int32)
            f_idx = f_idx[(f_idx >= 0) & (f_idx < len(fvc))]
            if len(f_idx) == 0:
                continue
            assigned[f_idx] = True
            bound_mat, _ = UsdShade.MaterialBindingAPI(sub.GetPrim()).ComputeBoundMaterial()
            tri_mask = np.isin(tri_fid, f_idx)
            if tri_mask.any():
                face_subsets.append((bound_mat.GetPrim() if bound_mat else None, tri_mask))

        rem = np.nonzero(~assigned)[0]
        if len(rem) > 0:
            bound_mat, _ = UsdShade.MaterialBindingAPI(prim).ComputeBoundMaterial()
            tri_mask = np.isin(tri_fid, rem)
            if tri_mask.any():
                face_subsets.append((bound_mat.GetPrim() if bound_mat else None, tri_mask))

        for mprim, tri_mask in face_subsets:
            mat_kwargs, uv_xform = _extract_pbr_from_material(mprim, prim, max_tex_px, tex_cache)
            sig_parts = [mat_kwargs.get("name", "mat")]
            if "baseColorFactor" in mat_kwargs:
                sig_parts.append(str(mat_kwargs["baseColorFactor"]))
            for tk in tex_keys:
                if tk in mat_kwargs:
                    sig_parts.append(f"{tk}:{id(mat_kwargs[tk])}")
            sig = "|".join(sig_parts)

            v_sub, f_sub, uv_sub = _unweld_bucket(
                pts_world,
                tri_v[tri_mask],
                tri_c[tri_mask],
                uv_vals,
                uv_idx,
                uv_interp,
                uv_xform,
            )
            has_any_tex = any(k in mat_kwargs for k in tex_keys)
            if uv_sub is not None and has_any_tex:
                mat_sig = sig
                if mat_sig not in mat_objs:
                    mat_objs[mat_sig] = trimesh.visual.material.PBRMaterial(**mat_kwargs)
                vis = trimesh.visual.TextureVisuals(uv=uv_sub, material=mat_objs[mat_sig])
            else:
                mat_sig = f"{sig}|notex" if has_any_tex else sig
                if mat_sig not in mat_objs:
                    clean_kwargs = {k: v for k, v in mat_kwargs.items() if k not in tex_keys}
                    mat_objs[mat_sig] = trimesh.visual.material.PBRMaterial(**clean_kwargs)
                vis = trimesh.visual.TextureVisuals(material=mat_objs[mat_sig])
            tmesh = trimesh.Trimesh(vertices=v_sub, faces=f_sub, visual=vis, process=False)
            buckets.setdefault(mat_sig, []).append(tmesh)

    out_meshes: list[tuple[str, Any]] = []
    for idx, (sig, mlist) in enumerate(buckets.items()):
        merged = _concat_meshes_preserving_uv(mlist)
        mat = merged.visual.material
        has_tex = any(getattr(mat, attr, None) is not None for attr in tex_keys)
        if pre_decimate_faces > 0 and not has_tex and len(merged.faces) > pre_decimate_faces:
            target = max(1500, int(len(merged.faces) * pre_decimate_ratio))
            merged = _decimate_untextured(merged, target)
        node_name = f"part_{idx}_{merged.visual.material.name or 'mat'}".replace("/", "_")
        out_meshes.append((node_name, merged))
    return out_meshes


def _find_nvidia_root_usd(pkg_dir: Path) -> Path:
    meta = pkg_dir / ".metadata" / "com.nvidia.simready.root_usds.json"
    if meta.exists():
        data = json.loads(meta.read_text())
        entries = data.get("entries", [])
        if entries:
            cand = pkg_dir / entries[0]
            if cand.exists():
                return cand
    usds = sorted((pkg_dir / "simready_usd").glob("sm_*.usd"))
    if usds:
        return usds[0]
    raise FileNotFoundError(f"No root USD found in {pkg_dir}")


def _build_enclosure_scene(raw_dir: Path) -> Any:
    import numpy as np
    import trimesh

    floor_usd = _find_nvidia_root_usd(raw_dir / "environment_floor_a01")
    wall_usd = _find_nvidia_root_usd(raw_dir / "structure_wall_straight_a01")
    col_usd = _find_nvidia_root_usd(raw_dir / "structure_wall_column_a01")

    def prep_part(usd_p: Path, max_h: float | None = None) -> list[tuple[str, Any]]:
        parts = _convert_usd_to_meshes(
            usd_p, skip_internal=False, max_tex_px=512, pre_decimate_faces=0, pre_decimate_ratio=1.0
        )
        all_b = np.array([m.bounds for _, m in parts])
        min_pt = all_b[:, 0, :].min(axis=0)
        max_pt = all_b[:, 1, :].max(axis=0)
        cx = 0.5 * (min_pt[0] + max_pt[0])
        cz = 0.5 * (min_pt[2] + max_pt[2])
        min_y = min_pt[1]
        out = []
        for n, m in parts:
            m.apply_translation([-cx, -min_y, -cz])
            if max_h is not None:
                m = _clip_mesh_y_max(m, max_h)
                if m is None:
                    continue
            out.append((n, m))
        return out

    floor_parts = prep_part(floor_usd)
    wall_parts_raw = prep_part(wall_usd, max_h=2.66)
    col_parts = prep_part(col_usd, max_h=2.66)

    scene = trimesh.Scene()
    # Thin 0.02 m floor slab with y in [0.001, 0.021] to prevent cellar z-fighting
    floor_h = max(float(max(m.bounds[1][1] for _, m in floor_parts)), 1e-4)
    y_scale = 0.02 / floor_h
    floor_min_y = min(float(m.bounds[0][1]) for _, m in floor_parts) * y_scale
    for n, m in floor_parts:
        m2 = m.copy()
        m2.apply_scale([1.0, y_scale, 1.0])
        m2.apply_translation([0.0, 0.001 - floor_min_y, 0.0])
        scene.add_geometry(m2, node_name=f"floor_{n}", geom_name=f"floor_{n}")

    # Back wall along z = -2.65, y in [0.021, 2.681]
    for n, m in wall_parts_raw:
        mw = m.copy()
        ext = mw.extents
        if ext[0] < ext[2]:
            rot = trimesh.transformations.rotation_matrix(math.pi * 0.5, [0.0, 1.0, 0.0])
            mw.apply_transform(rot)
        mw.apply_translation([0.0, 0.021, -2.65])
        scene.add_geometry(mw, node_name=f"wall_back_{n}", geom_name=f"wall_back_{n}")

    # Left wall along x = -2.65, y in [0.021, 2.681]
    for n, m in wall_parts_raw:
        mw = m.copy()
        ext = mw.extents
        if ext[0] > ext[2]:
            rot = trimesh.transformations.rotation_matrix(math.pi * 0.5, [0.0, 1.0, 0.0])
            mw.apply_transform(rot)
        mw.apply_translation([-2.65, 0.021, 0.0])
        scene.add_geometry(mw, node_name=f"wall_left_{n}", geom_name=f"wall_left_{n}")

    # Corner column at (-2.65, 0.021, -2.65)
    for n, m in col_parts:
        mc = m.copy()
        mc.apply_translation([-2.65, 0.021, -2.65])
        scene.add_geometry(mc, node_name=f"col_corner_{n}", geom_name=f"col_corner_{n}")

    # Scale XZ to 7.00 x 5.20 m centered at (0, 0) with min_y = 0.001
    bounds = scene.bounds
    ext = bounds[1] - bounds[0]
    sx = 7.00 / max(float(ext[0]), 1e-4)
    sz = 5.20 / max(float(ext[2]), 1e-4)
    scale_xf = np.diag([sx, 1.0, sz, 1.0])
    for geom in scene.geometry.values():
        geom.apply_transform(scale_xf)
    b2 = scene.bounds
    cx = 0.5 * (float(b2[0][0]) + float(b2[1][0]))
    cz = 0.5 * (float(b2[0][2]) + float(b2[1][2]))
    dy = 0.001 - float(b2[0][1])
    for geom in scene.geometry.values():
        geom.apply_translation([-cx, dy, -cz])

    return scene


def _download_hf_package(repo_id: str, pkg: str, dest_root: Path) -> None:
    from huggingface_hub import HfApi, hf_hub_download
    from huggingface_hub.hf_api import RepoFile

    api = HfApi(token=False)
    items = api.list_repo_tree(repo_id, path_in_repo=pkg, recursive=True, repo_type="dataset")
    files = [item.path for item in items if isinstance(item, RepoFile)]
    if not files:
        raise RuntimeError(f"No files found in {repo_id}/{pkg}")
    for rel in files:
        target = dest_root / rel
        if not target.exists():
            hf_hub_download(
                repo_id=repo_id,
                filename=rel,
                repo_type="dataset",
                local_dir=str(dest_root),
                token=False,
            )


def _select_specs(only: list[str] | None) -> list[dict[str, Any]]:
    if not only:
        return list(HARDWARE_SPECS)
    valid_names = {s["name"] for s in HARDWARE_SPECS}
    unknown = sorted(set(only) - valid_names)
    if unknown:
        raise ValueError(f"Unknown --only target(s): {unknown}")
    return [s for s in HARDWARE_SPECS if s["name"] in only]


def run_remote_worker(only: list[str] | None = None) -> None:
    import trimesh

    Path(REMOTE_RAW_SIMREADY).mkdir(parents=True, exist_ok=True)
    Path(REMOTE_RAW_GLB).mkdir(parents=True, exist_ok=True)

    for spec in _select_specs(only):
        name = spec["name"]
        out_glb = Path(REMOTE_RAW_GLB) / f"{name}.glb"

        if name == "hardware_basement_enclosure":
            scene = _build_enclosure_scene(Path(REMOTE_CACHED_SIMREADY))
            scene.export(str(out_glb))
            print(
                f"[remote] {name}: raw={out_glb.stat().st_size} bytes, "
                f"bounds={scene.bounds.round(3).tolist()}",
                flush=True,
            )
            continue

        repo_id = spec["repo_id"]
        pkg = spec["pkg"]
        pkg_dir = Path(REMOTE_RAW_SIMREADY) / pkg
        try:
            if not pkg_dir.exists() or not any(pkg_dir.rglob("*.usd*")):
                _download_hf_package(repo_id, pkg, Path(REMOTE_RAW_SIMREADY))
        except Exception as exc:
            fb = spec.get("fallback_local_pkg")
            if fb and (Path(REMOTE_CACHED_SIMREADY) / fb).exists():
                print(
                    f"[remote] {name}: HF download failed ({exc}), using cached {fb}",
                    flush=True,
                )
                pkg_dir = Path(REMOTE_CACHED_SIMREADY) / fb
            else:
                raise

        if "usd_rel" in spec:
            usd_path = Path(REMOTE_RAW_SIMREADY) / spec["usd_rel"]
        else:
            usd_path = _find_nvidia_root_usd(pkg_dir)

        parts = _convert_usd_to_meshes(
            usd_path,
            skip_internal=spec["skip_internal"],
            max_tex_px=spec["tex_size"],
            pre_decimate_faces=spec["pre_decimate_faces"],
            pre_decimate_ratio=spec["pre_decimate_ratio"],
        )
        if not parts:
            raise RuntimeError(f"No meshes extracted from {usd_path}")

        scene = trimesh.Scene()
        for node_name, mesh in parts:
            scene.add_geometry(mesh, node_name=node_name, geom_name=node_name)

        bounds = scene.bounds
        cx = 0.5 * (bounds[0][0] + bounds[1][0])
        cz = 0.5 * (bounds[0][2] + bounds[1][2])
        min_y = bounds[0][1]
        for geom in scene.geometry.values():
            geom.apply_translation([-cx, -min_y, -cz])

        scene.export(str(out_glb))
        print(
            f"[remote] {name}: raw={out_glb.stat().st_size} bytes, "
            f"bounds={scene.bounds.round(3).tolist()}",
            flush=True,
        )


# ---------------------------------------------------------------------------
# Stage 2: Local Orchestrator & @gltf-transform/cli Compression
# ---------------------------------------------------------------------------


def check_remote_ready(remote_host: str) -> None:
    cmd = (
        "who && "
        "nvidia-smi --query-gpu=index,name,memory.used,memory.free,utilization.gpu "
        "--format=csv,noheader"
    )
    res = subprocess.run(["ssh", remote_host, cmd], check=True, capture_output=True, text=True)
    print("[remote status]\n" + res.stdout.strip())


def run_local_pipeline(
    skip_remote: bool = False,
    only: list[str] | None = None,
    remote_host: str = REMOTE_HOST,
) -> None:
    import numpy as np
    import trimesh

    specs = _select_specs(only)
    repo_root = Path(__file__).resolve().parents[2]
    assets_dir = repo_root / "assets"
    glb_dir = assets_dir / "glb"
    manifest_path = assets_dir / "manifest.json"
    attributions_path = assets_dir / "attributions.json"

    glb_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir(parents=True, exist_ok=True)

    if not skip_remote:
        if remote_host:
            check_remote_ready(remote_host)
            script_bytes = Path(__file__).read_bytes()
            remote_cmd = f"{REMOTE_PYTHON} - --remote-worker"
            if only:
                remote_cmd += " --only " + " ".join(s["name"] for s in specs)
            subprocess.run(
                ["ssh", remote_host, remote_cmd],
                input=script_bytes,
                check=True,
            )
        else:
            run_remote_worker(only=only)

    if LOCAL_STAGE_DIR.exists():
        shutil.rmtree(LOCAL_STAGE_DIR)
    LOCAL_STAGE_DIR.mkdir(parents=True, exist_ok=True)

    try:
        if remote_host:
            subprocess.run(
                [
                    "rsync",
                    "-az",
                    f"{remote_host}:{REMOTE_RAW_GLB}/",
                    str(LOCAL_STAGE_DIR) + "/",
                ],
                check=True,
            )
        else:
            raw_glb_dir = Path(REMOTE_RAW_GLB)
            for spec in specs:
                src_glb = raw_glb_dir / f"{spec['name']}.glb"
                if src_glb.is_file():
                    shutil.copy2(src_glb, LOCAL_STAGE_DIR / src_glb.name)

        entries: dict[str, Any] = {}
        total_bytes = 0
        for spec in specs:
            name = spec["name"]
            raw_glb = LOCAL_STAGE_DIR / f"{name}.glb"
            out_glb = glb_dir / f"{name}.glb"
            cmd = [
                "npx",
                "--yes",
                "@gltf-transform/cli",
                "optimize",
                str(raw_glb),
                str(out_glb),
                "--compress",
                "draco",
                "--texture-compress",
                "webp",
                "--texture-size",
                str(spec["tex_size"]),
                "--palette",
                "true",
                "--prune-solid-textures",
                "true",
                "--simplify",
                "true",
                "--simplify-ratio",
                str(spec["simplify_ratio"]),
                "--simplify-error",
                "0.002",
            ]
            subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(
                ["npx", "--yes", "@gltf-transform/cli", "validate", str(out_glb)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            reloaded = trimesh.load(str(out_glb))
            bounds = np.round(reloaded.bounds, 4).tolist()
            extents = np.round(reloaded.extents, 4).tolist()
            size_bytes = out_glb.stat().st_size
            total_bytes += size_bytes
            print(
                f"[{name}] {raw_glb.stat().st_size / 1024:.1f} KB -> {size_bytes / 1024:.1f} KB, "
                f"extents={extents}, min_y={bounds[0][1]:.4f}"
            )
            entries[name] = {
                "file": f"glb/{name}.glb",
                "label": spec["label"],
                "provenance_category": "geodata_cad_blueprint",
                "attribution": spec["attribution"],
                "bounds": bounds,
                "extents_m": extents,
                "bytes": size_bytes,
                "sha256": sha256_file(out_glb),
            }

        print(f"[hardware rebuilt] {total_bytes} bytes ({total_bytes / (1024 * 1024):.2f} MB)")

        manifest = (
            json.loads(manifest_path.read_text()) if manifest_path.exists() else {"assets": {}}
        )
        manifest.setdefault("assets", {}).update(entries)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

        attrib = (
            json.loads(attributions_path.read_text())
            if attributions_path.exists()
            else {"sources": {}, "mandatory_notices": []}
        )
        attrib.setdefault("sources", {}).update(
            {
                "nvidia_simready": {
                    "datasets": [
                        (
                            "nvidia/simready-assets "
                            "(ibcTank_steel_stainless_a01, pipe_plastic_gray_b08)"
                        ),
                        (
                            "NVIDIA SimReady basement enclosure "
                            "(environment_floor_a01, structure_wall_straight_a01, "
                            "structure_wall_column_a01)"
                        ),
                    ],
                    "attribution": "NVIDIA Corporation",
                    "license": "CC BY 4.0",
                },
                "vertiv_simready": {
                    "datasets": [
                        (
                            "Vertiv/simready-assets "
                            "(16OCP_ORV3_racks_Computer_racks, XDU1350, "
                            "NVD_RPP_1200_480_XT210_FA)"
                        )
                    ],
                    "attribution": "Vertiv",
                    "license": "CC BY 4.0",
                },
                "forgecad_simready": {
                    "datasets": ["ForgeCAD/simready-assets (gate_valve)"],
                    "attribution": "ForgeCAD",
                    "license": "CC BY 4.0",
                },
            }
        )
        notices = set(attrib.get("mandatory_notices", []))
        notices.add("SimReady 3D hardware assets: NVIDIA, Vertiv and ForgeCAD (CC BY 4.0)")
        attrib["mandatory_notices"] = sorted(notices)
        attributions_path.write_text(json.dumps(attrib, indent=2) + "\n")
    finally:
        if LOCAL_STAGE_DIR.exists():
            shutil.rmtree(LOCAL_STAGE_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build SimReady hardware GLB assets.")
    parser.add_argument(
        "--remote-worker",
        action="store_true",
        help="Run Stage 1 USD->raw GLB conversion on a remote USD worker host.",
    )
    parser.add_argument(
        "--skip-remote",
        action="store_true",
        help="Skip Stage 1 conversion and stage existing raw GLBs from the configured directory.",
    )
    parser.add_argument(
        "--remote-host",
        default=REMOTE_HOST,
        help="Optional SSH host for remote Stage 1 USD conversion (default: SIMREADY_REMOTE_HOST).",
    )
    parser.add_argument(
        "--only",
        nargs="*",
        default=None,
        help="Optional subset of hardware asset names to build (default: all).",
    )
    args = parser.parse_args()
    if args.remote_worker:
        run_remote_worker(only=args.only)
    else:
        run_local_pipeline(
            skip_remote=args.skip_remote,
            only=args.only,
            remote_host=args.remote_host,
        )


if __name__ == "__main__":
    main()
