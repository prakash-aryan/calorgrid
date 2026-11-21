"""Build the Draco-compressed IFC 4.3 reference house shell GLB.

Federates Building-Architecture.ifc, Building-Structural.ifc and Building-Hvac.ifc
from buildingSMART/Sample-Test-Files (CC BY 4.0), deduplicates by GlobalId, aligns
to the Basel Klingental 15 slot at (0, 0, 0), adds a recessed concrete cellar bay
and preserves all 6 X-Ray cutaway parent groups.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import ifcopenshell
import ifcopenshell.geom
import numpy as np
import requests
import trimesh
from pygltflib import GLTF2

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = REPO_ROOT / "assets"
GLB_DIR = ASSETS_DIR / "glb"
MANIFEST_PATH = ASSETS_DIR / "manifest.json"
ATTRIBUTIONS_PATH = ASSETS_DIR / "attributions.json"

BASE_RAW_URL = (
    "https://raw.githubusercontent.com/buildingSMART/Sample-Test-Files/main/"
    "IFC%204.3.2.0%20(IFC%204.3%20ADD2)/Simple-Scene/"
)

IFC_LAYERS = [
    ("arch", "Building-Architecture.ifc"),
    ("struct", "Building-Structural.ifc"),
    ("hvac", "Building-Hvac.ifc"),
]

EXCLUDE_CLASSES = {
    "IfcSite",
    "IfcBuilding",
    "IfcBuildingStorey",
    "IfcSpace",
    "IfcSpatialZone",
    "IfcBuildingElementProxy",
    "IfcEarthworksFill",
    "IfcOpeningElement",
    "IfcFooting",
}

CUTAWAY_GROUPS = [
    "shell_roof",
    "shell_walls_front",
    "shell_walls_rear_side",
    "shell_slabs",
    "shell_interior",
    "shell_hvac_chimney",
]

R_ZUP_TO_YUP = np.array(
    [
        [1.0, 0.0, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, -1.0, 0.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ],
    dtype=np.float64,
)

BLUEPRINT_DISCLAIMER = (
    "Open reference IFC 4.3 house blueprint (buildingSMART Simple-Scene), "
    "not a surveyed private Basel residence"
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def classify_group(ifc_cls: str, name: str) -> str:
    n = name.lower()
    if ifc_cls in ("IfcBeam", "IfcDiscreteAccessory") or "roof" in n:
        return "shell_roof"
    if ifc_cls in ("IfcChimney", "IfcDuctSegment", "IfcAirTerminal"):
        return "shell_hvac_chimney"
    if ifc_cls == "IfcFurniture":
        return "shell_interior"
    if ifc_cls == "IfcSlab":
        return "shell_slabs"
    if ("front" in n and "front_right" not in n) or "right_front" in n or "front_right" in n:
        return "shell_walls_front"
    return "shell_walls_rear_side"


def make_box_mesh(
    bmin: list[float], bmax: list[float], rgba: list[int], mat_name: str
) -> trimesh.Trimesh:
    size = np.array(bmax, dtype=np.float64) - np.array(bmin, dtype=np.float64)
    center = (np.array(bmin, dtype=np.float64) + np.array(bmax, dtype=np.float64)) * 0.5
    mesh = trimesh.creation.box(extents=size)
    mesh.apply_translation(center)
    mat = trimesh.visual.material.PBRMaterial(
        name=mat_name,
        baseColorFactor=rgba,
        metallicFactor=0.05,
        roughnessFactor=0.88,
        doubleSided=True,
    )
    mesh.visual = trimesh.visual.TextureVisuals(material=mat)
    return mesh


def download_ifcs(dest_dir: Path) -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    for layer, fname in IFC_LAYERS:
        target = dest_dir / fname
        if not target.exists():
            resp = requests.get(f"{BASE_RAW_URL}{fname}", timeout=60)
            resp.raise_for_status()
            target.write_bytes(resp.content)
        out.append((layer, target))
    return out


def build_ifc_glb(scale: float = 1.45, ifc_dir: Path | None = None) -> dict[str, Any]:
    GLB_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="cg_ifc_") as tmp:
        tmp_dir = Path(tmp)
        layer_files = download_ifcs(ifc_dir if ifc_dir is not None else tmp_dir)

        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)

        scene = trimesh.Scene()
        for grp in CUTAWAY_GROUPS:
            scene.graph.update(frame_to=grp, frame_from=scene.graph.base_frame, matrix=np.eye(4))

        cx_raw, cz_raw = 5.80, -6.00
        seen_gids: set[str] = set()
        child_count = 0

        for layer, fpath in layer_files:
            ifc_file = ifcopenshell.open(str(fpath))
            for product in ifc_file.by_type("IfcProduct"):
                if any(product.is_a(cls) for cls in EXCLUDE_CLASSES):
                    continue
                if not product.Representation:
                    continue
                gid = product.GlobalId
                if gid in seen_gids:
                    continue
                try:
                    shape = ifcopenshell.geom.create_shape(settings, product)
                except Exception:
                    continue
                verts = np.array(shape.geometry.verts, dtype=np.float64).reshape(-1, 3)
                faces = np.array(shape.geometry.faces, dtype=np.int64).reshape(-1, 3)
                if len(verts) == 0 or len(faces) == 0:
                    continue
                seen_gids.add(gid)

                rgba = [200, 200, 200, 255]
                mat_name = "ifc_default"
                if shape.geometry.materials:
                    m0 = shape.geometry.materials[0]
                    mat_name = getattr(m0, "name", "ifc_default")
                    if hasattr(m0, "diffuse"):
                        d = m0.diffuse
                        alpha = 1.0 - float(getattr(m0, "transparency", 0.0) or 0.0)
                        rgba = [
                            int(round(max(0.0, min(1.0, d.r())) * 255)),
                            int(round(max(0.0, min(1.0, d.g())) * 255)),
                            int(round(max(0.0, min(1.0, d.b())) * 255)),
                            int(round(max(0.0, min(1.0, alpha)) * 255)),
                        ]

                clean_name = (product.Name or product.is_a()).replace(" ", "_").replace("_-_", "_")
                node_name = f"{layer}__{product.is_a()}__{clean_name}__{product.id()}"
                grp = classify_group(product.is_a(), clean_name)

                metallic = 0.7 if "metal" in mat_name.lower() else 0.05
                roughness = 0.35 if "metal" in mat_name.lower() else 0.85
                mat = trimesh.visual.material.PBRMaterial(
                    name=f"{mat_name}__{grp}",
                    baseColorFactor=rgba,
                    metallicFactor=metallic,
                    roughnessFactor=roughness,
                    doubleSided=True,
                )
                mesh = trimesh.Trimesh(vertices=verts, faces=faces, process=False)
                mesh.visual = trimesh.visual.TextureVisuals(material=mat)
                mesh.apply_transform(R_ZUP_TO_YUP)
                mesh.apply_translation([-cx_raw, 0.0, -cz_raw])
                mesh.apply_scale(scale)

                scene.add_geometry(
                    mesh, node_name=node_name, geom_name=node_name, parent_node_name=grp
                )
                child_count += 1

        bx0 = (3.0 - cx_raw) * scale
        bx1 = (8.6 - cx_raw) * scale
        bz0 = (-9.0 - cz_raw) * scale
        bz1 = (-4.8 - cz_raw) * scale
        wt = 0.20 * scale
        y_slab_bottom = -0.25 * scale
        y_cellar_top = -3.20
        y_cellar_bot = -3.45

        concrete_rgba = [125, 130, 138, 255]
        cellar_parts = [
            (
                "basement__IfcSlab__cellar_floor",
                "shell_slabs",
                [bx0, y_cellar_bot, bz0],
                [bx1, y_cellar_top, bz1],
            ),
            (
                "basement__IfcWall__cellar_wall_back",
                "shell_walls_rear_side",
                [bx0, y_cellar_top, bz0],
                [bx1, y_slab_bottom, bz0 + wt],
            ),
            (
                "basement__IfcWall__cellar_wall_left",
                "shell_walls_rear_side",
                [bx0, y_cellar_top, bz0 + wt],
                [bx0 + wt, y_slab_bottom, bz1 - wt],
            ),
            (
                "basement__IfcWall__cellar_wall_right",
                "shell_walls_rear_side",
                [bx1 - wt, y_cellar_top, bz0 + wt],
                [bx1, y_slab_bottom, bz1 - wt],
            ),
            (
                "basement__IfcWall__cellar_wall_front",
                "shell_walls_front",
                [bx0, y_cellar_top, bz1 - wt],
                [bx1, y_slab_bottom, bz1],
            ),
        ]
        for name, grp, bmin, bmax in cellar_parts:
            box = make_box_mesh(bmin, bmax, concrete_rgba, f"concrete_basement__{grp}")
            scene.add_geometry(box, node_name=name, geom_name=name, parent_node_name=grp)
            child_count += 1

        raw_glb = tmp_dir / "house_bim_shell_raw.glb"
        out_glb = GLB_DIR / "house_bim_shell.glb"
        scene.export(str(raw_glb))
        subprocess.run(
            ["npx", "--yes", "@gltf-transform/cli", "draco", str(raw_glb), str(out_glb)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    reloaded = trimesh.load(str(out_glb))
    gltf = GLTF2().load_binary(str(out_glb))
    node_names = sorted({n.name for n in gltf.nodes if n.name})
    size_bytes = out_glb.stat().st_size

    interior_bounds = {
        "x": [round(bx0 + wt, 3), round(bx1 - wt, 3)],
        "y": [round(y_cellar_top, 3), round(y_slab_bottom, 3)],
        "z": [round(bz0 + wt, 3), round(bz1 - wt, 3)],
        "size_m": [
            round((bx1 - wt) - (bx0 + wt), 3),
            round(y_slab_bottom - y_cellar_top, 3),
            round((bz1 - wt) - (bz0 + wt), 3),
        ],
    }

    print(
        f"[house_bim_shell] bytes={size_bytes} ({size_bytes / 1024:.1f} KB), "
        f"geoms={len(reloaded.geometry)}, scale={scale}, interior={interior_bounds['size_m']}"
    )

    return {
        "file": "glb/house_bim_shell.glb",
        "label": "Open Reference IFC 4.3 House & Basement Shell",
        "provenance_category": "geodata_cad_blueprint",
        "disclaimer": BLUEPRINT_DISCLAIMER,
        "scale": scale,
        "cutaway_groups": CUTAWAY_GROUPS,
        "child_mesh_count": child_count,
        "nodes": node_names,
        "bounds": np.round(reloaded.bounds, 3).tolist(),
        "basement_interior_bounds": interior_bounds,
        "bytes": size_bytes,
        "budget_bytes": 1024 * 1024,
        "sha256": sha256_file(out_glb),
    }


def update_metadata(entry: dict[str, Any]) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else {"assets": {}}
    manifest.setdefault("assets", {})["house_bim_shell"] = entry
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")

    attrib = (
        json.loads(ATTRIBUTIONS_PATH.read_text())
        if ATTRIBUTIONS_PATH.exists()
        else {"sources": {}, "mandatory_notices": []}
    )
    attrib.setdefault("sources", {})["buildingsmart_ifc"] = {
        "datasets": [
            "Building-Architecture.ifc",
            "Building-Structural.ifc",
            "Building-Hvac.ifc",
        ],
        "attribution": "(C) buildingSMART International Ltd.",
        "license": "CC BY 4.0",
        "disclaimer": BLUEPRINT_DISCLAIMER,
    }
    notices = set(attrib.get("mandatory_notices", []))
    notices.add(BLUEPRINT_DISCLAIMER)
    attrib["mandatory_notices"] = sorted(notices)
    ATTRIBUTIONS_PATH.write_text(json.dumps(attrib, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build house_bim_shell.glb from IFC 4.3 files.")
    parser.add_argument("--scale", type=float, default=1.45, help="Uniform house scale factor.")
    parser.add_argument(
        "--ifc-dir",
        type=Path,
        default=None,
        help="Optional local directory containing cached Building-*.ifc files.",
    )
    args = parser.parse_args()
    entry = build_ifc_glb(scale=args.scale, ifc_dir=args.ifc_dir)
    update_metadata(entry)


if __name__ == "__main__":
    main()
