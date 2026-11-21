"""Build compressed LV95 Basel and Verbund geodata GLB assets.

Sources:
- swisstopo swissBUILDINGS3D 3.0 (3D Tiles .b3dm)
- swisstopo swissALTI3D 2 m DEM (STAC + /vsicurl/)
- swisstopo swissimage-dop10 0.1 m orthophoto (STAC + /vsicurl/)
- Copernicus GLO-30 DEM and EOX Sentinel-2 cloudless fallbacks
- OpenStreetMap via overpass.osm.ch (roads, waterways and swimming pools)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import DracoPy
import numpy as np
import rasterio
import requests
import trimesh
from PIL import Image
from pygltflib import GLTF2
from pyproj import Transformer
from rasterio.enums import Resampling
from requests.adapters import HTTPAdapter
from scipy.spatial.transform import Rotation
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon
from urllib3.util.retry import Retry

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")

REPO_ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = REPO_ROOT / "assets"
GLB_DIR = ASSETS_DIR / "glb"
MANIFEST_PATH = ASSETS_DIR / "manifest.json"
ATTRIBUTIONS_PATH = ASSETS_DIR / "attributions.json"

TILESET_URL = "https://3d.geo.admin.ch/ch.swisstopo.swissbuildings3d.3d/v1/20260520/tileset.json"
STAC_ALTI_URL = "https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.swissalti3d/items"
STAC_IMG_URL = (
    "https://data.geo.admin.ch/api/stac/v0.9/collections/ch.swisstopo.swissimage-dop10/items"
)
OVERPASS_URL = "https://overpass.osm.ch/api/interpreter"

BASEL_CUTAWAY_UUID = "7C71C414-5FE4-494C-983C-DC302CF5C16F"
BASEL_CUTAWAY_BOX = (-4.5, 4.5, -5.0, 5.0)

SITES: list[dict[str, Any]] = [
    {
        "name": "context_neighborhood",
        "label": "Basel Reference Site (Klingental 15 & Mittlere Bruecke)",
        "lat": 47.561804,
        "lon": 7.590508,
        "radius_m": 280.0,
        "isolate_uuid": BASEL_CUTAWAY_UUID,
        "cutout_box": BASEL_CUTAWAY_BOX,
        "step_m": 4.0,
        "budget_bytes": 5 * 1024 * 1024,
    },
    {
        "name": "verbund_st_jakob",
        "label": "Gartenbad St. Jakob Basel",
        "lat": 47.5384131,
        "lon": 7.6208085,
        "radius_m": 220.0,
        "isolate_uuid": None,
        "cutout_box": None,
        "step_m": 4.0,
        "budget_bytes": int(1.5 * 1024 * 1024),
    },
    {
        "name": "verbund_sole_uno",
        "label": "Sole Uno Rheinfelden",
        "lat": 47.5577448,
        "lon": 7.800038,
        "radius_m": 200.0,
        "isolate_uuid": None,
        "cutout_box": None,
        "step_m": 4.0,
        "budget_bytes": int(1.5 * 1024 * 1024),
    },
    {
        "name": "verbund_ara_basel",
        "label": "ARA Basel / ProRheno",
        "lat": 47.5828809,
        "lon": 7.5996807,
        "radius_m": 250.0,
        "isolate_uuid": None,
        "cutout_box": None,
        "step_m": 4.0,
        "budget_bytes": int(1.5 * 1024 * 1024),
    },
    {
        "name": "verbund_davos",
        "label": "Davos Eau-la-la Alpine Profile",
        "lat": 46.8005013,
        "lon": 9.8306546,
        "radius_m": 200.0,
        "isolate_uuid": None,
        "cutout_box": None,
        "step_m": 4.0,
        "budget_bytes": int(1.5 * 1024 * 1024),
    },
]

LL_TO_LV95 = Transformer.from_crs("EPSG:4326", "EPSG:2056", always_xy=True)
LV95_TO_LL = Transformer.from_crs("EPSG:2056", "EPSG:4326", always_xy=True)
ECEF_TO_LV95 = Transformer.from_crs("EPSG:4978", "EPSG:2056", always_xy=True)


def make_session() -> requests.Session:
    sess = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    return sess


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_b3dm_tiles(
    sess: requests.Session, lon: float, lat: float, radius_m: float
) -> list[str]:
    dlat = radius_m / 111000.0
    dlon = radius_m / (111320.0 * math.cos(math.radians(lat)))
    lon_min, lon_max = lon - dlon, lon + dlon
    lat_min, lat_max = lat - dlat, lat + dlat
    cache: dict[str, Any] = {}

    def get_ts(url: str) -> Any:
        if url not in cache:
            resp = sess.get(url, timeout=30)
            resp.raise_for_status()
            cache[url] = resp.json()
        return cache[url]

    out: list[str] = []

    def walk(node: dict[str, Any], base: str, inherited_refine: str = "ADD") -> None:
        bv = node.get("boundingVolume", {})
        if "region" in bv:
            w, s, e, n = map(math.degrees, bv["region"][:4])
            if e < lon_min or w > lon_max or n < lat_min or s > lat_max:
                return
        refine = node.get("refine", inherited_refine)
        children = node.get("children", [])
        uri = node.get("content", {}).get("uri")
        if uri:
            full = urljoin(base, uri)
            if uri.endswith(".json"):
                sub = get_ts(full)
                walk(sub["root"], full, refine)
            elif uri.endswith(".b3dm"):
                if not (refine == "REPLACE" and children):
                    out.append(full)
        for ch in children:
            walk(ch, base, refine)

    root = get_ts(TILESET_URL)
    walk(root["root"], TILESET_URL)
    return sorted(set(out))


def parse_b3dm(bdata: bytes, tmp_dir: Path) -> tuple[dict[str, Any], dict[str, Any], GLTF2]:
    magic, _, _, ft_json_len, ft_bin_len, bt_json_len, _ = struct.unpack("<4sIIIIII", bdata[:28])
    if magic != b"b3dm":
        raise ValueError(f"Unexpected tile magic: {magic!r}")
    off = 28
    ft = json.loads(bdata[off : off + ft_json_len].decode("utf-8"))
    off += ft_json_len + ft_bin_len
    bt = json.loads(bdata[off : off + bt_json_len].decode("utf-8")) if bt_json_len else {}
    off += bt_json_len
    # Skip bt_bin_len if present in header
    _, _, _, _, _, _, bt_bin_len = struct.unpack("<4sIIIIII", bdata[:28])
    off += bt_bin_len
    tmp_glb = tmp_dir / "tile_decode.glb"
    tmp_glb.write_bytes(bdata[off:])
    gltf = GLTF2().load_binary(str(tmp_glb))
    return ft, bt, gltf


def decode_tile_lv95_primitives(
    bdata: bytes, tmp_dir: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ft, bt, gltf = parse_b3dm(bdata, tmp_dir)
    rtc = np.array(ft["RTC_CENTER"], dtype=np.float64)
    blob = gltf.binary_blob()
    prims: list[dict[str, Any]] = []

    for node in gltf.nodes:
        if node.mesh is None:
            continue
        t = np.array(node.translation or [0.0, 0.0, 0.0], dtype=np.float64)
        rot = Rotation.from_quat(node.rotation).as_matrix() if node.rotation else np.eye(3)
        mesh = gltf.meshes[node.mesh]
        for prim in mesh.primitives:
            ext = (prim.extensions or {}).get("KHR_draco_mesh_compression")
            if not ext:
                continue
            bv = gltf.bufferViews[ext["bufferView"]]
            dm = DracoPy.decode(blob[bv.byteOffset : bv.byteOffset + bv.byteLength])
            pts = np.asarray(dm.points, dtype=np.float64)
            faces = np.asarray(dm.faces, dtype=np.int32)
            p_gltf = (rot @ pts.T).T + t
            p_cesium = np.column_stack([p_gltf[:, 0], -p_gltf[:, 2], p_gltf[:, 1]])
            e_arr, n_arr, h_arr = ECEF_TO_LV95.transform(*(p_cesium + rtc).T)

            if "_BATCHID" in ext.get("attributes", {}):
                uid = ext["attributes"]["_BATCHID"]
                bids = np.rint(
                    np.asarray(dm.get_attribute_by_unique_id(uid)["data"]).ravel()
                ).astype(np.int32)
            else:
                bids = np.zeros(len(pts), dtype=np.int32)

            kind = "wall"
            if prim.material is not None:
                pbr = gltf.materials[prim.material].pbrMetallicRoughness
                if pbr and pbr.baseColorFactor:
                    bcf = pbr.baseColorFactor
                    if bcf[0] > 0.6 and bcf[1] < 0.4:
                        kind = "roof"

            prims.append(
                {
                    "E": np.asarray(e_arr, dtype=np.float64),
                    "N": np.asarray(n_arr, dtype=np.float64),
                    "H": np.asarray(h_arr, dtype=np.float64),
                    "faces": faces,
                    "bids": bids,
                    "kind": kind,
                }
            )
    return bt, prims


def find_building_lv95_center(
    sess: requests.Session, tiles: list[str], target_uuid: str, tmp_dir: Path
) -> tuple[float, float, dict[str, bytes]]:
    tile_cache: dict[str, bytes] = {}
    pts_e: list[np.ndarray] = []
    pts_n: list[np.ndarray] = []
    for url in tiles:
        bdata = sess.get(url, timeout=30).content
        tile_cache[url] = bdata
        bt, prims = decode_tile_lv95_primitives(bdata, tmp_dir)
        uuids = bt.get("UUID", [])
        if target_uuid not in uuids:
            continue
        target_bid = uuids.index(target_uuid)
        for p in prims:
            face_bids = p["bids"][p["faces"][:, 0]]
            mask = face_bids == target_bid
            if not mask.any():
                continue
            v_idx = np.unique(p["faces"][mask])
            pts_e.append(p["E"][v_idx])
            pts_n.append(p["N"][v_idx])
    if not pts_e:
        raise RuntimeError(f"Target UUID {target_uuid} not found in {len(tiles)} tiles")
    all_e = np.concatenate(pts_e)
    all_n = np.concatenate(pts_n)
    return (
        float(0.5 * (all_e.min() + all_e.max())),
        float(0.5 * (all_n.min() + all_n.max())),
        tile_cache,
    )


def _dedup_trimesh(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    if len(mesh.faces) == 0:
        return mesh
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int32)
    q = np.rint(verts[faces] * 1000.0).astype(np.int64)
    v0, v1, v2 = q[:, 0, :], q[:, 1, :], q[:, 2, :]
    non_deg = (
        np.any(v0 != v1, axis=1)
        & np.any(v1 != v2, axis=1)
        & np.any(v0 != v2, axis=1)
    )
    if not non_deg.any():
        return trimesh.Trimesh(
            vertices=np.zeros((0, 3), dtype=np.float32),
            faces=np.zeros((0, 3), dtype=np.int32),
            process=False,
        )
    faces_nd = faces[non_deg]
    q_nd = q[non_deg]
    sv = np.empty((len(q_nd), 3), dtype=[("x", np.int64), ("y", np.int64), ("z", np.int64)])
    sv["x"] = q_nd[:, :, 0]
    sv["y"] = q_nd[:, :, 1]
    sv["z"] = q_nd[:, :, 2]
    sv_sorted = np.sort(sv, axis=1)
    _, first_idx = np.unique(sv_sorted, axis=0, return_index=True)
    kept_faces = faces_nd[np.sort(first_idx)]
    u_idx, inv = np.unique(kept_faces, return_inverse=True)
    new_verts = verts[u_idx].astype(np.float32)
    new_faces = inv.reshape(-1, 3).astype(np.int32)
    return trimesh.Trimesh(vertices=new_verts, faces=new_faces, process=False)


def _clip_poly_halfspace(
    poly: list[np.ndarray], axis: int, sign: float, bound: float
) -> list[np.ndarray]:
    if not poly:
        return []
    out: list[np.ndarray] = []
    n = len(poly)
    for i in range(n):
        a = poly[i]
        b = poly[(i + 1) % n]
        va = sign * (float(a[axis]) - bound)
        vb = sign * (float(b[axis]) - bound)
        if va >= 0.0 and vb >= 0.0:
            out.append(b)
        elif va >= 0.0 and vb < 0.0:
            t = max(0.0, min(1.0, va / (va - vb)))
            out.append(a + t * (b - a))
        elif va < 0.0 and vb >= 0.0:
            t = max(0.0, min(1.0, va / (va - vb)))
            out.append(a + t * (b - a))
            out.append(b)
    return out


def _clip_mesh_outside_xz_box(
    verts: np.ndarray,
    faces: np.ndarray,
    box: tuple[float, float, float, float],
    pad: float = 0.08,
) -> tuple[np.ndarray, np.ndarray]:
    if len(faces) == 0:
        return verts, faces
    x0, x1 = float(box[0]) - pad, float(box[1]) + pad
    z0, z1 = float(box[2]) - pad, float(box[3]) + pad

    tri_pts = verts[faces].astype(np.float64)
    tx0 = tri_pts[:, :, 0].min(axis=1)
    tx1 = tri_pts[:, :, 0].max(axis=1)
    tz0 = tri_pts[:, :, 2].min(axis=1)
    tz1 = tri_pts[:, :, 2].max(axis=1)
    outside = (tx1 <= x0) | (tx0 >= x1) | (tz1 <= z0) | (tz0 >= z1)
    if outside.all():
        return verts, faces

    regions = (
        ((0, -1.0, x0),),
        ((0, 1.0, x1),),
        ((0, 1.0, x0), (0, -1.0, x1), (2, -1.0, z0)),
        ((0, 1.0, x0), (0, -1.0, x1), (2, 1.0, z1)),
    )

    extra_verts: list[np.ndarray] = []
    extra_faces: list[list[int]] = []
    base_v = len(verts)

    for tri in tri_pts[~outside]:
        base_poly = [tri[0], tri[1], tri[2]]
        for planes in regions:
            poly = base_poly
            for axis, sign, bound in planes:
                poly = _clip_poly_halfspace(poly, axis, sign, bound)
                if len(poly) < 3:
                    break
            if len(poly) < 3:
                continue
            cleaned: list[np.ndarray] = []
            for p in poly:
                if not cleaned or float(np.linalg.norm(p - cleaned[-1])) > 1e-7:
                    cleaned.append(p)
            if len(cleaned) >= 2 and float(np.linalg.norm(cleaned[0] - cleaned[-1])) <= 1e-7:
                cleaned.pop()
            if len(cleaned) < 3:
                continue
            p0 = cleaned[0]
            for k in range(1, len(cleaned) - 1):
                p1, p2 = cleaned[k], cleaned[k + 1]
                area2 = float(np.linalg.norm(np.cross(p1 - p0, p2 - p0)))
                if area2 <= 1e-10:
                    continue
                idx0 = base_v + len(extra_verts)
                extra_verts.extend([p0, p1, p2])
                extra_faces.append([idx0, idx0 + 1, idx0 + 2])

    kept_faces = faces[outside]
    if extra_faces:
        all_v = np.vstack([verts.astype(np.float64), np.asarray(extra_verts, dtype=np.float64)])
        all_f = np.vstack([kept_faces, np.asarray(extra_faces, dtype=np.int32)])
    else:
        all_v = verts.astype(np.float64)
        all_f = kept_faces

    if len(all_f) == 0:
        return np.zeros((0, 3), dtype=np.float32), np.zeros((0, 3), dtype=np.int32)
    u_idx, inv = np.unique(all_f, return_inverse=True)
    return all_v[u_idx].astype(np.float32), inv.reshape(-1, 3).astype(np.int32)


def extract_building_buckets(
    tile_bytes_map: dict[str, bytes],
    sess: requests.Session,
    tiles: list[str],
    e0: float,
    n0: float,
    z0: float,
    radius_m: float,
    isolate_uuid: str | None,
    tmp_dir: Path,
    cutout_box: tuple[float, float, float, float] | None = None,
) -> dict[tuple[str, str], list[trimesh.Trimesh]]:
    groups: dict[tuple[str, str], list[trimesh.Trimesh]] = {
        ("neighborhood", "roof"): [],
        ("neighborhood", "wall"): [],
        ("cutaway", "roof"): [],
        ("cutaway", "wall"): [],
    }
    for url in tiles:
        bdata = tile_bytes_map.get(url)
        if bdata is None:
            bdata = sess.get(url, timeout=30).content
        bt, prims = decode_tile_lv95_primitives(bdata, tmp_dir)
        uuids = bt.get("UUID", [])
        target_bid = uuids.index(isolate_uuid) if (isolate_uuid and isolate_uuid in uuids) else -1

        for p in prims:
            v_three = np.column_stack([p["E"] - e0, p["H"] - z0, -(p["N"] - n0)]).astype(np.float32)
            faces = p["faces"]
            face_bids = p["bids"][faces[:, 0]]
            face_centers_xz = v_three[faces].mean(axis=1)[:, [0, 2]]
            in_rad = np.hypot(face_centers_xz[:, 0], face_centers_xz[:, 1]) <= radius_m

            buckets = [
                ("cutaway", in_rad & (face_bids == target_bid)),
                ("neighborhood", in_rad & (face_bids != target_bid)),
            ]
            for scope, mask in buckets:
                if not mask.any():
                    continue
                sub_f = faces[mask]
                u_idx, inv = np.unique(sub_f, return_inverse=True)
                sub_v = v_three[u_idx]
                sub_f_compact = inv.reshape(-1, 3)
                if scope == "neighborhood" and cutout_box is not None:
                    sub_v, sub_f_compact = _clip_mesh_outside_xz_box(
                        sub_v, sub_f_compact, cutout_box, pad=0.08
                    )
                    if len(sub_f_compact) == 0:
                        continue
                mesh = trimesh.Trimesh(vertices=sub_v, faces=sub_f_compact, process=False)
                if scope == "cutaway" and p["kind"] == "wall":
                    sliced = trimesh.intersections.slice_mesh_plane(
                        mesh, plane_normal=[0.0, 1.0, 0.0], plane_origin=[0.0, -0.05, 0.0]
                    )
                    if sliced is not None and len(sliced.faces) > 0:
                        mesh = sliced
                groups[(scope, p["kind"])].append(mesh)
    return groups


def fetch_newest_stac_assets(
    sess: requests.Session, stac_url: str, lon: float, lat: float, half_m: float, suffix: str
) -> list[str]:
    dlon = half_m / (111320.0 * math.cos(math.radians(lat))) + 0.0025
    dlat = half_m / 111000.0 + 0.0025
    url = f"{stac_url}?bbox={lon - dlon},{lat - dlat},{lon + dlon},{lat + dlat}&limit=25"
    resp = sess.get(url, timeout=30)
    resp.raise_for_status()
    feats = resp.json().get("features", [])
    by_tile: dict[str, tuple[str, str]] = {}
    for feat in feats:
        parts = feat["id"].split("_")
        tid = parts[-1]
        yr = parts[1] if len(parts) > 1 else "0000"
        for k, asset in feat.get("assets", {}).items():
            if k.endswith(suffix):
                if tid not in by_tile or yr > by_tile[tid][0]:
                    by_tile[tid] = (yr, asset["href"])
    return [href for _, href in by_tile.values()]


def copernicus_dem_fill(e_pts: np.ndarray, n_pts: np.ndarray, mask: np.ndarray) -> np.ndarray:
    filled = np.full(len(e_pts), np.nan, dtype=np.float32)
    if not mask.any():
        return filled
    lon_pts, lat_pts = LV95_TO_LL.transform(e_pts[mask], n_pts[mask])
    tiles_needed = {
        (int(math.floor(lat)), int(math.floor(lon))) for lon, lat in zip(lon_pts, lat_pts)
    }
    idx_mask = np.nonzero(mask)[0]
    for lat0, lon0 in tiles_needed:
        ns = f"N{lat0:02d}" if lat0 >= 0 else f"S{abs(lat0):02d}"
        ew = f"E{lon0:03d}" if lon0 >= 0 else f"W{abs(lon0):03d}"
        tile_id = f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"
        cog_url = f"/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/{tile_id}/{tile_id}.tif"
        try:
            with rasterio.open(cog_url) as src:
                b = src.bounds
                sub = (
                    (lon_pts >= b.left)
                    & (lon_pts <= b.right)
                    & (lat_pts >= b.bottom)
                    & (lat_pts <= b.top)
                )
                if sub.any():
                    vals = np.array(
                        [
                            v[0]
                            for v in src.sample(
                                list(zip(lon_pts[sub].tolist(), lat_pts[sub].tolist()))
                            )
                        ],
                        dtype=np.float32,
                    )
                    ok = np.isfinite(vals) & (vals > -1000.0)
                    filled[idx_mask[sub][ok]] = vals[ok]
        except Exception:
            continue
    return filled


def sample_dem_points(
    dem_srcs: list[rasterio.DatasetReader],
    e_pts: np.ndarray,
    n_pts: np.ndarray,
    fallback_z: float,
) -> np.ndarray:
    z = np.full(len(e_pts), np.nan, dtype=np.float32)
    for src in dem_srcs:
        b = src.bounds
        in_b = (
            (e_pts >= b.left)
            & (e_pts <= b.right)
            & (n_pts >= b.bottom)
            & (n_pts <= b.top)
            & np.isnan(z)
        )
        if in_b.any():
            coords = [(float(e), float(n)) for e, n in zip(e_pts[in_b], n_pts[in_b])]
            vals = np.array([v[0] for v in src.sample(coords)], dtype=np.float32)
            valid = np.isfinite(vals) & (vals > -1000.0)
            z[np.nonzero(in_b)[0][valid]] = vals[valid]
    missing = np.isnan(z)
    if missing.any():
        cop = copernicus_dem_fill(e_pts, n_pts, missing)
        ok = ~np.isnan(cop)
        z[ok] = cop[ok]
    z = np.where(np.isnan(z), fallback_z, z)
    return z


def eox_fill(
    sess: requests.Session,
    img: np.ndarray,
    e_min: float,
    n_max: float,
    px_per_m: float,
) -> np.ndarray:
    mask = img.sum(axis=-1) == 0
    if not mask.any():
        return img
    rr, cc = np.nonzero(mask)
    e_arr = e_min + (cc + 0.5) / px_per_m
    n_arr = n_max - (rr + 0.5) / px_per_m
    lon, lat = LV95_TO_LL.transform(e_arr, n_arr)
    z = 14
    n_tiles = 2**z
    xt = (np.asarray(lon) + 180.0) / 360.0 * n_tiles
    lat_rad = np.radians(lat)
    yt = (1.0 - np.log(np.tan(lat_rad) + 1.0 / np.cos(lat_rad)) / math.pi) / 2.0 * n_tiles
    cache: dict[tuple[int, int], np.ndarray | None] = {}
    out = img.copy()
    for tx, ty in set(zip(xt.astype(int).tolist(), yt.astype(int).tolist())):
        r = sess.get(
            f"https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless_3857/default/g/{z}/{ty}/{tx}.jpg",
            timeout=60,
        )
        cache[(tx, ty)] = (
            np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB")) if r.ok else None
        )
    for (tx, ty), tile in cache.items():
        if tile is None:
            continue
        sel = (xt.astype(int) == tx) & (yt.astype(int) == ty)
        px = ((xt[sel] - tx) * 256).astype(int).clip(0, 255)
        py = ((yt[sel] - ty) * 256).astype(int).clip(0, 255)
        out[rr[sel], cc[sel]] = tile[py, px]
    return out


def build_terrain_grid_axes(
    half_m: float, step_m: float, cutout_box: tuple[float, float, float, float] | None
) -> tuple[np.ndarray, np.ndarray]:
    if cutout_box is None:
        xs = np.arange(-half_m, half_m + step_m * 0.5, step_m)
        ns = np.arange(-half_m, half_m + step_m * 0.5, step_m)
        return xs, ns
    x0, x1, z0_box, z1_box = cutout_box
    n_min_local, n_max_local = -z1_box, -z0_box
    xs_outer_l = np.arange(-half_m, -16.0, step_m)
    xs_outer_r = np.arange(16.0 + step_m, half_m + 0.1, step_m)
    xs_fine = np.arange(-16.0, 16.01, 1.0)
    xs = np.unique(np.round(np.concatenate([xs_outer_l, xs_fine, xs_outer_r, [x0, x1]]), 4))

    ns_outer_b = np.arange(-half_m, -16.0, step_m)
    ns_outer_t = np.arange(16.0 + step_m, half_m + 0.1, step_m)
    ns_fine = np.arange(-16.0, 16.01, 1.0)
    ns = np.unique(
        np.round(np.concatenate([ns_outer_b, ns_fine, ns_outer_t, [n_min_local, n_max_local]]), 4)
    )
    return xs, ns


def build_ortho_terrain(
    sess: requests.Session,
    lat: float,
    lon: float,
    e0: float,
    n0: float,
    half_m: float,
    step_m: float,
    tex_px: int,
    cutout_box: tuple[float, float, float, float] | None,
) -> tuple[trimesh.Trimesh, float, Any]:
    dem_urls = fetch_newest_stac_assets(sess, STAC_ALTI_URL, lon, lat, half_m, "_2_2056_5728.tif")
    dem_srcs = [rasterio.open(f"/vsicurl/{u}") for u in dem_urls]
    try:
        z0 = float(sample_dem_points(dem_srcs, np.array([e0]), np.array([n0]), 250.0)[0])
        xs, ns = build_terrain_grid_axes(half_m, step_m, cutout_box)
        gx, gn = np.meshgrid(xs, ns)
        ge = e0 + gx.ravel()
        gn_abs = n0 + gn.ravel()
        z_arr = sample_dem_points(dem_srcs, ge, gn_abs, z0)

        def dem_local_fn(x_arr: np.ndarray, z_three_arr: np.ndarray) -> np.ndarray:
            e_q = e0 + x_arr
            n_q = n0 - z_three_arr
            return sample_dem_points(dem_srcs, e_q, n_q, z0) - z0

        # Sample terrain elevation grid first, keep dem_srcs open for OSM draping
        img_urls = fetch_newest_stac_assets(sess, STAC_IMG_URL, lon, lat, half_m, "_0.1_2056.tif")
        img_canvas = np.zeros((tex_px, tex_px, 3), dtype=np.uint8)
        e_min, e_max = e0 - half_m, e0 + half_m
        n_min, n_max = n0 - half_m, n0 + half_m
        px_per_m = tex_px / (2.0 * half_m)

        for u in img_urls:
            with rasterio.open(f"/vsicurl/{u}") as s:
                b = s.bounds
                oe0, oe1 = max(e_min, b.left), min(e_max, b.right)
                on0, on1 = max(n_min, b.bottom), min(n_max, b.top)
                if oe1 <= oe0 or on1 <= on0:
                    continue
                win = rasterio.windows.from_bounds(oe0, on0, oe1, on1, s.transform)
                c0 = int(round((oe0 - e_min) * px_per_m))
                c1 = int(round((oe1 - e_min) * px_per_m))
                r0 = int(round((n_max - on1) * px_per_m))
                r1 = int(round((n_max - on0) * px_per_m))
                w_px, h_px = max(1, c1 - c0), max(1, r1 - r0)
                arr = s.read(
                    [1, 2, 3], window=win, out_shape=(3, h_px, w_px), resampling=Resampling.average
                )
                img_canvas[r0:r1, c0:c1] = np.moveaxis(arr, 0, -1)

        img_canvas = eox_fill(sess, img_canvas, e_min, n_max, px_per_m)
        # Subtle daylight grade: lift shadows slightly and warm green vegetation tone
        arr_f = np.clip(img_canvas.astype(np.float32) / 255.0, 0.0, 1.0)
        arr_f = np.power(arr_f, 1.0 / 1.08)
        arr_f[..., 1] = np.clip(arr_f[..., 1] * 1.03, 0.0, 1.0)
        arr_f[..., 2] = np.clip(arr_f[..., 2] * 0.96, 0.0, 1.0)
        graded_canvas = np.clip(arr_f * 255.0, 0, 255).astype(np.uint8)
        tex_img = Image.fromarray(graded_canvas)

        verts = np.column_stack([gx.ravel(), z_arr - z0, -gn.ravel()]).astype(np.float32)
        u_uv = (gx.ravel() + half_m) / (2.0 * half_m)
        v_uv = (gn.ravel() + half_m) / (2.0 * half_m)
        uv = np.column_stack([u_uv, v_uv]).astype(np.float32)

        nx, ny = len(xs), len(ns)
        faces = []
        for j in range(ny - 1):
            for i in range(nx - 1):
                a = j * nx + i
                b = a + 1
                c = a + nx
                d = c + 1
                if cutout_box is not None:
                    cx = 0.25 * (verts[a, 0] + verts[b, 0] + verts[c, 0] + verts[d, 0])
                    cz = 0.25 * (verts[a, 2] + verts[b, 2] + verts[c, 2] + verts[d, 2])
                    if (
                        cutout_box[0] <= cx <= cutout_box[1]
                        and cutout_box[2] <= cz <= cutout_box[3]
                    ):
                        continue
                faces.append([a, b, d])
                faces.append([a, d, c])

        mat = trimesh.visual.material.PBRMaterial(
            name="terrain_orthophoto",
            baseColorTexture=tex_img,
            metallicFactor=0.0,
            roughnessFactor=0.92,
        )
        terrain_mesh = trimesh.Trimesh(
            vertices=verts,
            faces=np.array(faces, dtype=np.int32),
            visual=trimesh.visual.TextureVisuals(uv=uv, material=mat),
            process=False,
        )

        # Precompute a dense 2 m DEM grid cache in memory for exact OSM vertex draping
        # without remote I/O per road vertex.
        dem_step = 2.0
        dxs = np.arange(-half_m, half_m + dem_step * 0.5, dem_step)
        dns = np.arange(-half_m, half_m + dem_step * 0.5, dem_step)
        dgx, dgn = np.meshgrid(dxs, dns)
        dz = sample_dem_points(dem_srcs, e0 + dgx.ravel(), n0 + dgn.ravel(), z0) - z0
        dz_grid = dz.reshape(len(dns), len(dxs))

        def fast_dem_local(x_arr: np.ndarray, z_three_arr: np.ndarray) -> np.ndarray:
            n_local = -z_three_arr
            fi = np.clip((x_arr + half_m) / dem_step, 0.0, len(dxs) - 1.001)
            fj = np.clip((n_local + half_m) / dem_step, 0.0, len(dns) - 1.001)
            i0 = np.floor(fi).astype(np.int32)
            j0 = np.floor(fj).astype(np.int32)
            fx = fi - i0
            fy = fj - j0
            return (
                dz_grid[j0, i0] * (1.0 - fx) * (1.0 - fy)
                + dz_grid[j0, i0 + 1] * fx * (1.0 - fy)
                + dz_grid[j0 + 1, i0] * (1.0 - fx) * fy
                + dz_grid[j0 + 1, i0 + 1] * fx * fy
            )

        return terrain_mesh, z0, fast_dem_local, graded_canvas
    finally:
        for src in dem_srcs:
            src.close()


def extract_vegetation_coords(
    img_rgb: np.ndarray,
    half_m: float,
    dem_fn: Any,
    building_meshes: list[trimesh.Trimesh],
    water_meshes: list[trimesh.Trimesh],
    road_meshes: list[trimesh.Trimesh],
    cutout_box: tuple[float, float, float, float] | None,
    site_name: str,
) -> dict[str, list[list[float]]]:
    """Detect real tree canopies and shrubs directly from swisstopo swissIMAGE."""
    h_px, w_px, _ = img_rgb.shape
    a = img_rgb.astype(np.float32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    green_ex = g - 0.5 * (r + b)

    # Rasterize actual building/water/road triangle samples into a 2 m exclusion grid
    cell = 2.0
    gn = int(math.ceil(2.0 * half_m / cell))
    excl = np.zeros((gn, gn), dtype=bool)

    def mark_meshes(meshes: list[trimesh.Trimesh], dilate_cells: int) -> None:
        pts_list: list[np.ndarray] = []
        for m in meshes:
            if len(m.vertices) == 0 or len(m.faces) == 0:
                continue
            v = np.asarray(m.vertices, dtype=np.float32)
            f = np.asarray(m.faces, dtype=np.int32)
            tris = v[f]
            cents = tris.mean(axis=1)
            pts_list.append(v[:, [0, 2]])
            pts_list.append(cents[:, [0, 2]])
            # Subsample midpoints of edges so large roofs/water polygons stay covered
            pts_list.append((0.5 * (tris[:, 0, :] + tris[:, 1, :]))[:, [0, 2]])
            pts_list.append((0.5 * (tris[:, 1, :] + tris[:, 2, :]))[:, [0, 2]])
            pts_list.append((0.5 * (tris[:, 2, :] + tris[:, 0, :]))[:, [0, 2]])
        if not pts_list:
            return
        pts = np.vstack(pts_list)
        ix = np.clip(np.floor((pts[:, 0] + half_m) / cell).astype(np.int32), 0, gn - 1)
        iz = np.clip(np.floor((pts[:, 1] + half_m) / cell).astype(np.int32), 0, gn - 1)
        for dz in range(-dilate_cells, dilate_cells + 1):
            for dx in range(-dilate_cells, dilate_cells + 1):
                excl[np.clip(iz + dz, 0, gn - 1), np.clip(ix + dx, 0, gn - 1)] = True

    mark_meshes(building_meshes, dilate_cells=2)
    mark_meshes(water_meshes, dilate_cells=2)
    mark_meshes(road_meshes, dilate_cells=1)

    rng = np.random.default_rng(abs(hash(site_name)) % (2**31))
    trees: list[list[float]] = []
    shrubs: list[list[float]] = []

    step_tree = 8.0 if site_name != "context_neighborhood" else 9.0
    xs = np.arange(-half_m + 8.0, half_m - 8.0, step_tree)
    zs = np.arange(-half_m + 8.0, half_m - 8.0, step_tree)
    for gx in xs:
        for gz in zs:
            x = float(gx + rng.uniform(-3.0, 3.0))
            z = float(gz + rng.uniform(-3.0, 3.0))
            if x * x + z * z > (half_m - 7.0) ** 2:
                continue
            if cutout_box is not None:
                if (cutout_box[0] - 5.0) <= x <= (cutout_box[1] + 5.0) and (
                    cutout_box[2] - 5.0
                ) <= z <= (cutout_box[3] + 5.0):
                    continue
            if abs(x) < 11.0 and abs(z) < 11.0 and site_name != "context_neighborhood":
                continue
            ix = int(np.clip((x + half_m) / cell, 0, gn - 1))
            iz = int(np.clip((z + half_m) / cell, 0, gn - 1))
            if excl[iz, ix]:
                continue
            px = int(np.clip((x + half_m) / (2.0 * half_m) * (w_px - 1), 3, w_px - 4))
            py = int(np.clip((z + half_m) / (2.0 * half_m) * (h_px - 1), 3, h_px - 4))
            ge_patch = float(green_ex[py - 3 : py + 4, px - 3 : px + 4].mean())
            lum_patch = float(lum[py - 3 : py + 4, px - 3 : px + 4].mean())
            lum_std = float(lum[py - 3 : py + 4, px - 3 : px + 4].std())
            y = float(dem_fn(np.array([x]), np.array([z]))[0])
            if ge_patch > 5.5 and lum_patch < 155.0 and lum_std > 5.0:
                sc = round(float(rng.uniform(0.72, 1.25)), 2)
                yaw = round(float(rng.uniform(0.0, 6.283)), 2)
                trees.append([round(x, 2), round(y - 0.15, 2), round(z, 2), sc, yaw])
            elif ge_patch > 8.0 and lum_patch < 190.0:
                sc = round(float(rng.uniform(0.85, 1.55)), 2)
                yaw = round(float(rng.uniform(0.0, 6.283)), 2)
                shrubs.append([round(x, 2), round(y - 0.08, 2), round(z, 2), sc, yaw])

    # Cap smoothly for crisp 60 FPS WebGL instancing
    if len(trees) > 280:
        idx = rng.choice(len(trees), size=280, replace=False)
        trees = [trees[i] for i in idx]
    if len(shrubs) > 300:
        idx = rng.choice(len(shrubs), size=300, replace=False)
        shrubs = [shrubs[i] for i in idx]
    print(f"  [{site_name}] vegetation detected: {len(trees)} trees, {len(shrubs)} shrubs")
    return {"trees": trees, "shrubs": shrubs}


def _ribbon_from_line(
    coords_xz: np.ndarray, width: float, dem_fn: Any, y_offset: float
) -> list[trimesh.Trimesh]:
    out: list[trimesh.Trimesh] = []
    for k in range(len(coords_xz) - 1):
        p0, p1 = coords_xz[k], coords_xz[k + 1]
        d = p1 - p0
        length = float(np.linalg.norm(d))
        if length < 0.25:
            continue
        perp = np.array([-d[1], d[0]], dtype=np.float64) / length * (width * 0.5)
        corners_xz = np.array([p0 - perp, p0 + perp, p1 + perp, p1 - perp], dtype=np.float64)
        ys = dem_fn(corners_xz[:, 0], corners_xz[:, 1]) + y_offset
        verts = np.column_stack([corners_xz[:, 0], ys, corners_xz[:, 1]]).astype(np.float32)
        out.append(trimesh.Trimesh(vertices=verts, faces=[[0, 1, 2], [0, 2, 3]], process=False))
    return out


def fetch_osm_features(
    sess: requests.Session,
    lat: float,
    lon: float,
    e0: float,
    n0: float,
    radius_m: float,
    dem_fn: Any,
    cutout_box: tuple[float, float, float, float] | None,
) -> tuple[list[trimesh.Trimesh], list[trimesh.Trimesh]]:
    query = f"""
    [out:json][timeout:30];
    (
      way["highway"](around:{int(radius_m)},{lat},{lon});
      way["waterway"](around:{int(radius_m)},{lat},{lon});
      way["natural"="water"](around:{int(radius_m)},{lat},{lon});
      way["leisure"="swimming_pool"](around:{int(radius_m)},{lat},{lon});
      way["amenity"="swimming_pool"](around:{int(radius_m)},{lat},{lon});
      way["man_made"~"wastewater_plant|storage_tank"](around:{int(radius_m)},{lat},{lon});
    );
    out geom tags;
    """
    data = None
    for attempt in range(5):
        resp = sess.post(
            OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": "calorgrid-m3/1.0", "Accept": "application/json"},
            timeout=35,
        )
        try:
            parsed = resp.json()
            if "elements" in parsed:
                data = parsed
                break
        except Exception:
            pass
        time.sleep(2 + attempt * 2)
    if not data or "elements" not in data:
        raise RuntimeError("Overpass query failed after retries")

    clip_disk = Point(0.0, 0.0).buffer(radius_m - 0.5, resolution=48)
    cut_poly = (
        Polygon(
            [
                (cutout_box[0], cutout_box[2]),
                (cutout_box[1], cutout_box[2]),
                (cutout_box[1], cutout_box[3]),
                (cutout_box[0], cutout_box[3]),
            ]
        )
        if cutout_box is not None
        else None
    )

    roads: list[trimesh.Trimesh] = []
    water: list[trimesh.Trimesh] = []

    for el in data.get("elements", []):
        geom = el.get("geometry", [])
        if len(geom) < 2:
            continue
        tags = el.get("tags", {})
        pts_en = np.array(
            [LL_TO_LV95.transform(g["lon"], g["lat"]) for g in geom], dtype=np.float64
        )
        xz = np.column_stack([pts_en[:, 0] - e0, -(pts_en[:, 1] - n0)])

        if "highway" in tags:
            hw = tags["highway"]
            w = (
                6.0
                if hw in ("primary", "secondary", "tertiary")
                else (4.0 if hw in ("residential", "unclassified") else 2.2)
            )
            clipped = LineString(xz).intersection(clip_disk)
            if cut_poly is not None and not clipped.is_empty:
                clipped = clipped.difference(cut_poly)
            lines = (
                [clipped]
                if isinstance(clipped, LineString)
                else (list(clipped.geoms) if isinstance(clipped, MultiLineString) else [])
            )
            for ln in lines:
                if len(ln.coords) >= 2:
                    roads.extend(_ribbon_from_line(np.asarray(ln.coords), w, dem_fn, 0.08))
        elif (
            tags.get("leisure") == "swimming_pool"
            or tags.get("amenity") == "swimming_pool"
            or tags.get("natural") == "water"
            or "waterway" in tags
        ):
            is_closed = len(xz) >= 4 and np.allclose(xz[0], xz[-1], atol=0.5)
            if is_closed:
                try:
                    poly = Polygon(xz)
                    if not poly.is_valid:
                        poly = poly.buffer(0)
                    clipped = poly.intersection(clip_disk)
                    if cut_poly is not None and not clipped.is_empty:
                        clipped = clipped.difference(cut_poly)
                    polys = (
                        [clipped]
                        if isinstance(clipped, Polygon)
                        else (list(clipped.geoms) if isinstance(clipped, MultiPolygon) else [])
                    )
                    for p_sub in polys:
                        if p_sub.area <= 4.0:
                            continue
                        v2d, f2d = trimesh.creation.triangulate_polygon(p_sub, engine="earcut")
                        ys = dem_fn(v2d[:, 0], v2d[:, 1]) + 0.12
                        v3d = np.column_stack([v2d[:, 0], ys, v2d[:, 1]]).astype(np.float32)
                        water.append(
                            trimesh.Trimesh(vertices=v3d, faces=f2d[:, ::-1], process=False)
                        )
                except Exception:
                    pass
            elif "waterway" in tags:
                w = 18.0 if tags["waterway"] == "river" else 4.0
                clipped = LineString(xz).intersection(clip_disk)
                lines = (
                    [clipped]
                    if isinstance(clipped, LineString)
                    else (list(clipped.geoms) if isinstance(clipped, MultiLineString) else [])
                )
                for ln in lines:
                    if len(ln.coords) >= 2:
                        water.extend(_ribbon_from_line(np.asarray(ln.coords), w, dem_fn, 0.08))
    return roads, water


def build_site(sess: requests.Session, cfg: dict[str, Any], tmp_dir: Path) -> dict[str, Any]:
    name = cfg["name"]
    lat = cfg["lat"]
    lon = cfg["lon"]
    radius_m = cfg["radius_m"]
    isolate_uuid = cfg["isolate_uuid"]
    cutout_box = cfg["cutout_box"]

    t0 = time.time()
    tiles = collect_b3dm_tiles(sess, lon, lat, radius_m)
    tile_bytes_map: dict[str, bytes] = {}

    if isolate_uuid:
        e0, n0, tile_bytes_map = find_building_lv95_center(sess, tiles, isolate_uuid, tmp_dir)
    else:
        e0, n0 = LL_TO_LV95.transform(lon, lat)

    terrain_mesh, z0, dem_fn, graded_canvas = build_ortho_terrain(
        sess,
        lat,
        lon,
        e0,
        n0,
        half_m=radius_m,
        step_m=cfg["step_m"],
        tex_px=2048,
        cutout_box=cutout_box,
    )
    groups = extract_building_buckets(
        tile_bytes_map,
        sess,
        tiles,
        e0,
        n0,
        z0,
        radius_m,
        isolate_uuid,
        tmp_dir,
        cutout_box=cutout_box,
    )
    roads, water = fetch_osm_features(sess, lat, lon, e0, n0, radius_m, dem_fn, cutout_box)
    veg_coords = extract_vegetation_coords(
        graded_canvas,
        radius_m,
        dem_fn,
        groups[("neighborhood", "wall")]
        + groups[("neighborhood", "roof")]
        + groups[("cutaway", "wall")],
        water,
        roads,
        cutout_box,
        name,
    )
    veg_path = ASSETS_DIR / "vegetation_coords.json"
    veg_all = json.loads(veg_path.read_text()) if veg_path.exists() else {}
    veg_all[name] = veg_coords
    veg_path.write_text(json.dumps(veg_all, separators=(",", ":")) + "\n")

    scene = trimesh.Scene()

    def add_group(
        meshes: list[trimesh.Trimesh], node_name: str, rgba: list[float], dedup: bool = False
    ) -> None:
        if not meshes:
            return
        merged = trimesh.util.concatenate(meshes)
        if dedup:
            merged = _dedup_trimesh(merged)
            if len(merged.faces) == 0:
                return
        merged.visual = trimesh.visual.TextureVisuals(
            material=trimesh.visual.material.PBRMaterial(
                name=node_name,
                baseColorFactor=rgba,
                metallicFactor=0.04,
                roughnessFactor=0.84,
            )
        )
        scene.add_geometry(merged, node_name=node_name, geom_name=node_name)

    add_group(
        groups[("neighborhood", "wall")],
        "neighborhood_buildings_walls",
        [0.91, 0.88, 0.82, 1.0],
        dedup=True,
    )
    add_group(
        groups[("neighborhood", "roof")],
        "neighborhood_buildings_roofs",
        [0.56, 0.29, 0.23, 1.0],
        dedup=True,
    )
    add_group(
        groups[("cutaway", "wall")],
        "cutaway_house_walls",
        [0.93, 0.90, 0.85, 1.0],
        dedup=True,
    )
    add_group(
        groups[("cutaway", "roof")],
        "cutaway_house_roof",
        [0.60, 0.28, 0.21, 1.0],
        dedup=True,
    )
    scene.add_geometry(terrain_mesh, node_name="terrain_ground", geom_name="terrain_ground")
    add_group(roads, "osm_roads", [0.28, 0.30, 0.33, 1.0])
    add_group(water, "osm_water_pools", [0.13, 0.50, 0.69, 1.0])

    raw_glb = tmp_dir / f"{name}_raw.glb"
    out_glb = GLB_DIR / f"{name}.glb"
    scene.export(str(raw_glb))
    for tex_cap in (2048, 1536, 1024):
        subprocess.run(
            [
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
                str(tex_cap),
                "--join",
                "false",
                "--flatten",
                "false",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if out_glb.stat().st_size < cfg["budget_bytes"]:
            break

    reloaded = trimesh.load(str(out_glb))
    size_bytes = out_glb.stat().st_size
    gltf = GLTF2().load_binary(str(out_glb))
    blob = gltf.binary_blob()
    building_nodes = {
        "neighborhood_buildings_walls",
        "neighborhood_buildings_roofs",
        "cutaway_house_walls",
        "cutaway_house_roof",
    }
    for node in gltf.nodes:
        if node.mesh is None or node.name not in building_nodes:
            continue
        prim = gltf.meshes[node.mesh].primitives[0]
        ext = (prim.extensions or {}).get("KHR_draco_mesh_compression")
        if not ext:
            continue
        bv = gltf.bufferViews[ext["bufferView"]]
        dm = DracoPy.decode(blob[bv.byteOffset : bv.byteOffset + bv.byteLength])
        pts = np.asarray(dm.points, dtype=np.float64).reshape(-1, 3)
        faces = np.asarray(dm.faces, dtype=np.int32).reshape(-1, 3)
        tris = pts[faces]
        cents = tris.mean(axis=1)
        uniq = len(np.unique(np.round(cents, 3), axis=0))
        dup_ratio = (len(faces) - uniq) / max(len(faces), 1)
        assert dup_ratio < 0.01, f"{name}/{node.name}: dup_ratio={dup_ratio:.4f}"
        if cutout_box is not None and node.name.startswith("neighborhood_"):
            bx0, bx1, bz0, bz1 = cutout_box
            in_c = (
                (cents[:, 0] >= bx0)
                & (cents[:, 0] <= bx1)
                & (cents[:, 2] >= bz0)
                & (cents[:, 2] <= bz1)
            )
            in_v = (
                (tris[:, :, 0] >= bx0)
                & (tris[:, :, 0] <= bx1)
                & (tris[:, :, 2] >= bz0)
                & (tris[:, :, 2] <= bz1)
            ).any(axis=1)
            assert not in_c.any() and not in_v.any(), (
                f"{name}/{node.name}: {int(in_c.sum())} centroids / "
                f"{int(in_v.sum())} verts inside cutout_box"
            )

    node_names = sorted({n.name for n in gltf.nodes if n.name})
    mean_nrm = terrain_mesh.face_normals.mean(axis=0).round(4).tolist()
    elapsed = round(time.time() - t0, 1)
    print(
        f"[{name}] bytes={size_bytes} ({size_bytes / 1024:.1f} KB), "
        f"nodes={node_names}, terrain_mean_normal={mean_nrm}, time={elapsed}s"
    )

    return {
        "file": f"glb/{name}.glb",
        "label": cfg["label"],
        "provenance_category": "geodata_cad_blueprint",
        "lv95_origin": {"E0": round(e0, 3), "N0": round(n0, 3), "Z0": round(z0, 3)},
        "wgs84": {"lat": lat, "lon": lon},
        "radius_m": radius_m,
        "cutaway_uuid": isolate_uuid,
        "cutaway_box": list(cutout_box) if cutout_box is not None else None,
        "nodes": node_names,
        "bounds": np.round(reloaded.bounds, 3).tolist(),
        "bytes": size_bytes,
        "budget_bytes": cfg["budget_bytes"],
        "sha256": sha256_file(out_glb),
    }


def update_metadata(site_entries: dict[str, Any]) -> None:
    manifest = json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else {"assets": {}}
    manifest.setdefault("coordinate_frame", {})["threejs"] = "Y-up meters, 1 unit = 1 m"
    manifest.setdefault("coordinate_frame", {})["basel_lv95_epsg"] = "EPSG:2056"
    manifest.setdefault("assets", {}).update(site_entries)
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")

    attrib = (
        json.loads(ATTRIBUTIONS_PATH.read_text())
        if ATTRIBUTIONS_PATH.exists()
        else {"sources": {}, "mandatory_notices": []}
    )
    attrib.setdefault("sources", {}).update(
        {
            "swisstopo": {
                "datasets": [
                    "swissBUILDINGS3D 3.0",
                    "swissALTI3D 2 m DEM",
                    "swissimage-dop10",
                ],
                "attribution": "(c) swisstopo",
                "license": "swisstopo Open Government Data (OGD) terms of use",
            },
            "openstreetmap": {
                "datasets": ["Overpass API overpass.osm.ch"],
                "attribution": "(c) OpenStreetMap contributors, ODbL",
                "license": "ODbL 1.0",
            },
        }
    )
    notices = set(attrib.get("mandatory_notices", []))
    notices.add("(c) swisstopo")
    notices.add("(c) OpenStreetMap contributors, ODbL")
    attrib["mandatory_notices"] = sorted(notices)
    ATTRIBUTIONS_PATH.write_text(json.dumps(attrib, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Basel and Verbund geodata GLB assets.")
    parser.add_argument(
        "--sites",
        nargs="*",
        default=None,
        help="Optional subset of site names to build (default: all 5).",
    )
    args = parser.parse_args()

    GLB_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_DIR.mkdir(parents=True, exist_ok=True)

    selected = [s for s in SITES if not args.sites or s["name"] in args.sites]
    sess = make_session()
    entries: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="cg_geo_") as tmp:
        tmp_dir = Path(tmp)
        for cfg in selected:
            entries[cfg["name"]] = build_site(sess, cfg, tmp_dir)
    update_metadata(entries)


if __name__ == "__main__":
    main()
