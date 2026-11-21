"""Unit tests for Milestone M3 geodata, BIM/IFC and SimReady GLB assets."""

from __future__ import annotations

import hashlib
import json
import struct
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = REPO_ROOT / "assets"
GLB_DIR = ASSETS_DIR / "glb"
MANIFEST_PATH = ASSETS_DIR / "manifest.json"
ATTRIBUTIONS_PATH = ASSETS_DIR / "attributions.json"

GEODATA_FILES = [
    "context_neighborhood.glb",
    "verbund_st_jakob.glb",
    "verbund_sole_uno.glb",
    "verbund_ara_basel.glb",
    "verbund_davos.glb",
]

HARDWARE_FILES = [
    "hardware_server_rack.glb",
    "hardware_cdu_exchanger.glb",
    "hardware_buffer_tank.glb",
    "hardware_piping.glb",
    "hardware_valve_3way.glb",
    "hardware_power_panel.glb",
    "hardware_basement_enclosure.glb",
]


def _parse_glb_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    magic, version, total_len = struct.unpack("<4sII", raw[:12])
    assert magic == b"glTF", f"{path.name}: bad GLB magic {magic!r}"
    assert version == 2, f"{path.name}: unexpected glTF version {version}"
    assert total_len == len(raw), f"{path.name}: header length {total_len} != {len(raw)}"

    chunk_len, chunk_type = struct.unpack("<II", raw[12:20])
    assert chunk_type == 0x4E4F534A, f"{path.name}: first chunk is not JSON"
    return json.loads(raw[20 : 20 + chunk_len].decode("utf-8"))


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _mat4_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [sum(a[r][k] * b[k][c] for k in range(4)) for c in range(4)]
        for r in range(4)
    ]


def _node_local_matrix(nd: dict[str, Any]) -> list[list[float]]:
    mat = nd.get("matrix")
    if mat and len(mat) == 16:
        # glTF matrices are column-major
        return [
            [float(mat[c * 4 + r]) for c in range(4)]
            for r in range(4)
        ]
    tx, ty, tz = [float(v) for v in nd.get("translation", [0.0, 0.0, 0.0])]
    qx, qy, qz, qw = [float(v) for v in nd.get("rotation", [0.0, 0.0, 0.0, 1.0])]
    sx, sy, sz = [float(v) for v in nd.get("scale", [1.0, 1.0, 1.0])]
    xx, yy, zz = qx * qx, qy * qy, qz * qz
    xy, xz, yz = qx * qy, qx * qz, qy * qz
    wx, wy, wz = qw * qx, qw * qy, qw * qz
    return [
        [(1.0 - 2.0 * (yy + zz)) * sx, 2.0 * (xy - wz) * sy, 2.0 * (xz + wy) * sz, tx],
        [2.0 * (xy + wz) * sx, (1.0 - 2.0 * (xx + zz)) * sy, 2.0 * (yz - wx) * sz, ty],
        [2.0 * (xz - wy) * sx, 2.0 * (yz + wx) * sy, (1.0 - 2.0 * (xx + yy)) * sz, tz],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _gltf_world_bounds(gltf: dict[str, Any]) -> tuple[list[float], list[float]]:
    nodes = gltf.get("nodes", [])
    meshes = gltf.get("meshes", [])
    accessors = gltf.get("accessors", [])

    parent_of: dict[int, int] = {}
    for idx, node in enumerate(nodes):
        for ch in node.get("children", []):
            parent_of[ch] = idx

    def world_mat(idx: int) -> list[list[float]]:
        chain: list[int] = []
        cur: int | None = idx
        while cur is not None:
            chain.append(cur)
            cur = parent_of.get(cur)
        m = [
            [1.0, 0.0, 0.0, 0.0],
            [0.0, 1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ]
        for nid in reversed(chain):
            m = _mat4_mul(m, _node_local_matrix(nodes[nid]))
        return m

    gmin = [float("inf"), float("inf"), float("inf")]
    gmax = [float("-inf"), float("-inf"), float("-inf")]
    for idx, node in enumerate(nodes):
        m_idx = node.get("mesh")
        if m_idx is None:
            continue
        wm = world_mat(idx)
        for prim in meshes[m_idx].get("primitives", []):
            pos_idx = prim.get("attributes", {}).get("POSITION")
            if pos_idx is None:
                continue
            acc = accessors[pos_idx]
            if "min" not in acc or "max" not in acc:
                continue
            bmin = [float(v) for v in acc["min"]]
            bmax = [float(v) for v in acc["max"]]
            for x in (bmin[0], bmax[0]):
                for y in (bmin[1], bmax[1]):
                    for z in (bmin[2], bmax[2]):
                        wx = wm[0][0] * x + wm[0][1] * y + wm[0][2] * z + wm[0][3]
                        wy = wm[1][0] * x + wm[1][1] * y + wm[1][2] * z + wm[1][3]
                        wz = wm[2][0] * x + wm[2][1] * y + wm[2][2] * z + wm[2][3]
                        gmin = [min(gmin[0], wx), min(gmin[1], wy), min(gmin[2], wz)]
                        gmax = [max(gmax[0], wx), max(gmax[1], wy), max(gmax[2], wz)]
    return gmin, gmax


def test_required_glbs_exist_and_meet_size_budgets() -> None:
    expected = GEODATA_FILES + ["house_bim_shell.glb"] + HARDWARE_FILES
    for fname in expected:
        p = GLB_DIR / fname
        assert p.is_file(), f"Missing GLB: {p}"
        assert p.stat().st_size > 1024, f"GLB unexpectedly tiny: {p}"

    ctx_bytes = (GLB_DIR / "context_neighborhood.glb").stat().st_size
    assert ctx_bytes < 5 * 1024 * 1024, f"context_neighborhood.glb too large: {ctx_bytes}"

    for fname in GEODATA_FILES[1:]:
        b = (GLB_DIR / fname).stat().st_size
        assert b < int(1.5 * 1024 * 1024), f"{fname} exceeds 1.5 MB: {b}"

    ifc_bytes = (GLB_DIR / "house_bim_shell.glb").stat().st_size
    assert ifc_bytes < 1024 * 1024, f"house_bim_shell.glb exceeds 1 MB: {ifc_bytes}"

    hw_total = sum((GLB_DIR / f).stat().st_size for f in HARDWARE_FILES)
    assert hw_total <= 16 * 1024 * 1024, f"hardware_*.glb total exceeds 16 MB: {hw_total}"

    all_total = sum(p.stat().st_size for p in GLB_DIR.glob("*.glb"))
    assert all_total < 25 * 1024 * 1024, f"Total GLB size exceeds 25 MB: {all_total}"


def test_geodata_draco_webp_and_cutaway_nodes() -> None:
    for fname in GEODATA_FILES:
        gltf = _parse_glb_json(GLB_DIR / fname)
        exts = set(gltf.get("extensionsUsed", []))
        assert "KHR_draco_mesh_compression" in exts, f"{fname} missing Draco compression"
        assert "EXT_texture_webp" in exts, f"{fname} missing WebP terrain texture"

    ctx = _parse_glb_json(GLB_DIR / "context_neighborhood.glb")
    nodes = ctx.get("nodes", [])
    node_names = {n.get("name") for n in nodes if n.get("name")}
    required_nodes = {
        "neighborhood_buildings_walls",
        "neighborhood_buildings_roofs",
        "cutaway_house_walls",
        "cutaway_house_roof",
        "terrain_ground",
        "osm_roads",
        "osm_water_pools",
    }
    assert required_nodes <= node_names, f"Missing geodata nodes: {required_nodes - node_names}"

    meshes = ctx.get("meshes", [])
    accessors = ctx.get("accessors", [])
    tri_counts: dict[str, int] = {}
    for nd in nodes:
        nm = nd.get("name")
        if nm in ("cutaway_house_walls", "cutaway_house_roof", "neighborhood_buildings_walls"):
            m = meshes[nd["mesh"]]
            tri_counts[nm] = sum(
                int(accessors[p["indices"]]["count"]) // 3
                for p in m.get("primitives", [])
                if "indices" in p
            )
    assert 0 < tri_counts["cutaway_house_walls"] <= 110, tri_counts
    assert 0 < tri_counts["cutaway_house_roof"] <= 65, tri_counts
    assert 0 < tri_counts["neighborhood_buildings_walls"] <= 32000, tri_counts


def test_ifc_shell_draco_cutaway_hierarchy_and_clearance() -> None:
    gltf = _parse_glb_json(GLB_DIR / "house_bim_shell.glb")
    exts = set(gltf.get("extensionsUsed", []))
    assert "KHR_draco_mesh_compression" in exts, "house_bim_shell.glb missing Draco compression"

    nodes = gltf.get("nodes", [])
    node_names = {n.get("name") for n in nodes if n.get("name")}
    expected_groups = {
        "shell_roof",
        "shell_walls_front",
        "shell_walls_rear_side",
        "shell_slabs",
        "shell_interior",
        "shell_hvac_chimney",
    }
    assert expected_groups <= node_names, f"Missing cutaway groups: {expected_groups - node_names}"

    mesh_nodes = [n for n in nodes if "mesh" in n]
    assert len(mesh_nodes) >= 29, f"Expected >= 29 child meshes, found {len(mesh_nodes)}"

    for bad in ("origin", "geo-reference"):
        assert not any(bad in name.lower() for name in node_names), f"Helper node leaked: {bad}"

    manifest = json.loads(MANIFEST_PATH.read_text())
    ifc_meta = manifest["assets"]["house_bim_shell"]
    interior = ifc_meta["basement_interior_bounds"]
    width_x, height_y, depth_z = interior["size_m"]
    assert height_y >= 2.65, f"Basement clear height {height_y} m < 2.65 m"
    assert height_y - 2.24 >= 0.40, "Insufficient ceiling clearance above 2.24 m ORV3 rack"
    assert width_x >= 6.0 and depth_z >= 4.5, f"Basement footprint too small: {interior}"


def test_simready_hardware_draco_grounded_and_clean() -> None:
    manifest = json.loads(MANIFEST_PATH.read_text())
    interior = manifest["assets"]["house_bim_shell"]["basement_interior_bounds"]
    width_x, height_y, depth_z = [float(v) for v in interior["size_m"]]

    for fname in HARDWARE_FILES:
        gltf = _parse_glb_json(GLB_DIR / fname)
        exts = set(gltf.get("extensionsUsed", []))
        assert "KHR_draco_mesh_compression" in exts, f"{fname} missing Draco compression"
        assert len(gltf.get("meshes", [])) > 0, f"{fname} has no meshes"

        node_names = {n.get("name", "") for n in gltf.get("nodes", [])}
        for forbidden in ("ConnectionPoints", "Connection_Points", "PhysicsMaterials"):
            assert not any(forbidden in n for n in node_names), f"{fname} leaked {forbidden}"

        gmin, gmax = _gltf_world_bounds(gltf)
        min_y = gmin[1]
        ext = [gmax[i] - gmin[i] for i in range(3)]
        assert -0.02 <= min_y <= 0.02, f"{fname} not grounded at y=0 (min_y={min_y})"

        fits_xz = (ext[0] <= width_x and ext[2] <= depth_z) or (
            ext[2] <= width_x and ext[0] <= depth_z
        )
        assert ext[1] <= height_y and fits_xz, (
            f"{fname} extents {ext} exceed basement interior {[width_x, height_y, depth_z]}"
        )

        if fname == "hardware_basement_enclosure.glb":
            assert min_y >= -0.002, f"{fname} floor min_y z-fighting risk ({min_y})"
            assert ext[0] <= 7.2 and ext[1] <= 2.75 and ext[2] <= 5.3, (
                f"{fname} extents exceed target enclosure envelope ({ext})"
            )


def test_all_textured_primitives_have_texcoords() -> None:
    all_glbs = GEODATA_FILES + ["house_bim_shell.glb"] + HARDWARE_FILES
    for fname in all_glbs:
        gltf = _parse_glb_json(GLB_DIR / fname)
        materials = gltf.get("materials", [])
        for m_idx, mesh in enumerate(gltf.get("meshes", [])):
            for p_idx, prim in enumerate(mesh.get("primitives", [])):
                mat_idx = prim.get("material")
                if mat_idx is None:
                    continue
                mat = materials[mat_idx]
                pbr = mat.get("pbrMetallicRoughness", {})
                tex_refs = [
                    pbr.get("baseColorTexture"),
                    pbr.get("metallicRoughnessTexture"),
                    mat.get("normalTexture"),
                    mat.get("occlusionTexture"),
                    mat.get("emissiveTexture"),
                ]
                attrs = prim.get("attributes", {})
                for tex in tex_refs:
                    if tex is None:
                        continue
                    tc_idx = tex.get("texCoord", 0)
                    assert f"TEXCOORD_{tc_idx}" in attrs, (
                        f"{fname} mesh[{m_idx}].prim[{p_idx}] missing TEXCOORD_{tc_idx}"
                    )


def test_manifest_and_attributions_complete() -> None:
    assert MANIFEST_PATH.is_file(), "Missing assets/manifest.json"
    assert ATTRIBUTIONS_PATH.is_file(), "Missing assets/attributions.json"

    manifest = json.loads(MANIFEST_PATH.read_text())
    assets = manifest.get("assets", {})
    expected_keys = {Path(f).stem for f in GEODATA_FILES + ["house_bim_shell.glb"] + HARDWARE_FILES}
    assert expected_keys <= set(assets.keys()), (
        f"Missing manifest keys: {expected_keys - set(assets.keys())}"
    )

    for key in expected_keys:
        entry = assets[key]
        glb_path = ASSETS_DIR / entry["file"]
        assert glb_path.is_file(), f"Manifest file missing: {glb_path}"
        assert entry["bytes"] == glb_path.stat().st_size, f"Byte mismatch for {key}"
        assert entry["sha256"] == _sha256(glb_path), f"SHA-256 mismatch for {key}"

    attrib = json.loads(ATTRIBUTIONS_PATH.read_text())
    text_blob = json.dumps(attrib)
    for required in (
        "(c) swisstopo",
        "(c) OpenStreetMap contributors, ODbL",
        "CC BY 4.0",
        "NVIDIA",
        "Vertiv",
        "ForgeCAD",
        "buildingSMART",
        "not a surveyed private Basel residence",
    ):
        assert required in text_blob, f"Missing required attribution/notice: {required}"
