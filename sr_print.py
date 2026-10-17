"""3D-print friendly STL export: extruded solid terrain (no pedestal)."""
from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Any

import numpy as np


STL_DTYPE = np.dtype([
    ("normal", "<f4", (3,)),
    ("vertices", "<f4", (3, 3)),
    ("attr", "<u2"),
])


def _grid_indices(n: int, step: int) -> np.ndarray:
    idx = np.arange(0, n, max(1, step), dtype=np.int64)
    if idx.size == 0 or idx[-1] != n - 1:
        idx = np.append(idx, n - 1)
    return idx


def _tri_dtype() -> np.dtype:
    return STL_DTYPE


class StlWriter:
    def __init__(self, path: Path):
        self.path = path
        self.f = path.open("wb")
        self.f.write(b"SnowRunner print map".ljust(80, b"\0"))
        self.f.write(struct.pack("<I", 0))
        self.count = 0

    def add(self, tri: np.ndarray) -> None:
        if len(tri) == 0:
            return
        v0 = tri[:, 0, :].astype(np.float64)
        v1 = tri[:, 1, :].astype(np.float64)
        v2 = tri[:, 2, :].astype(np.float64)
        n = np.cross(v1 - v0, v2 - v0)
        ln = np.linalg.norm(n, axis=1)
        ln[ln < 1e-12] = 1.0
        n /= ln[:, None]
        rec = np.empty(len(tri), dtype=STL_DTYPE)
        rec["normal"] = n.astype(np.float32)
        rec["vertices"] = tri.astype(np.float32)
        rec["attr"] = 0
        rec.tofile(self.f)
        self.count += len(tri)

    def close(self) -> None:
        self.f.seek(80)
        self.f.write(struct.pack("<I", self.count))
        self.f.close()


def _box_triangles(x0: float, x1: float, y0: float, y1: float,
                   z0: float, z1: float) -> np.ndarray:
    """Closed box, outward-facing, Z-up."""
    v = np.array([
        [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
        [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
    ], dtype=np.float64)
    faces = np.array([
        [0, 2, 1], [0, 3, 2],  # bottom -Z
        [4, 5, 6], [4, 6, 7],  # top +Z
        [0, 1, 5], [0, 5, 4],  # -Y
        [1, 2, 6], [1, 6, 5],  # +X
        [2, 3, 7], [2, 7, 6],  # +Y
        [3, 0, 4], [3, 4, 7],  # -X
    ], dtype=np.int64)
    return v[faces]


def _quat_matrix(q: tuple[float, float, float, float]) -> np.ndarray:
    x, y, z, w = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.array([
        [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)],
        [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)],
        [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)],
    ], dtype=np.float64)


def _boundary_loop(nr: int, nc: int) -> list[tuple[int, int]]:
    """CCW boundary of a row-major grid, viewed from +Z."""
    loop: list[tuple[int, int]] = []
    # left edge: top -> bottom
    for r in range(nr):
        loop.append((r, 0))
    # bottom edge: left -> right (exclude first corner)
    for c in range(1, nc):
        loop.append((nr - 1, c))
    # right edge: bottom -> top (exclude first corner)
    for r in range(nr - 2, -1, -1):
        loop.append((r, nc - 1))
    # top edge: right -> left (exclude both corners)
    for c in range(nc - 2, 0, -1):
        loop.append((0, c))
    return loop


VEGETATION_WORDS = (
    "tree", "birch", "spruce", "aspen", "tsuga", "oak", "maple", "pine",
    "bush", "stump", "swamp_stub", "forest", "plant", "grass", "willow",
    "cedar", "fir",
)


def _is_vegetation(name: str) -> bool:
    low = name.lower()
    return any(w in low for w in VEGETATION_WORDS)


def _vegetation_proxy(positions: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """A 6-sided cone/rough tree proxy in the same local frame as the mesh."""
    mn = positions.min(axis=0)
    mx = positions.max(axis=0)
    cx = (mn[0] + mx[0]) * 0.5
    cz = (mn[2] + mx[2]) * 0.5
    r = max(mx[0] - mn[0], mx[2] - mn[2]) * 0.5
    h = mx[1] - mn[1]
    if r < 1e-6 or h < 1e-6:
        return np.empty((0, 3), dtype=np.float64), np.empty((0, 3), dtype=np.int64)
    ring = []
    for i in range(6):
        a = 2.0 * math.pi * i / 6.0
        ring.append([cx + r * math.cos(a), mn[1], cz + r * math.sin(a)])
    vertices = np.array(ring + [[cx, mx[1], cz], [cx, mn[1], cz]], dtype=np.float64)
    tris = []
    for i in range(6):
        j = (i + 1) % 6
        tris.append([i, j, 6])       # side
        tris.append([7, j, i])       # base
    return vertices, np.array(tris, dtype=np.int64)


def write_print_stl(path: Path, height_raw: np.ndarray,
                    extent_x: float, extent_z: float, step: int,
                    height_scale: float, height_offset: float,
                    print_width_mm: float = 200.0,
                    base_thickness_mm: float = 3.0,
                    simplify_vegetation: bool = True,
                    objects: list[Any] | None = None,
                    shared_store: Any = None,
                    level_store: Any = None,
                    ssl_name: str | None = None,
                    verbose: bool = False) -> dict:
    """Write a watertight print model: terrain surface extruded down to Z=0.

    Coordinates are converted to Z-up and scaled so the map width equals
    `print_width_mm`. The flat bottom is at Z=0; `base_thickness_mm` is the
    material thickness under the lowest terrain point.
    """
    H, W = height_raw.shape
    rows = _grid_indices(H, step)
    cols = _grid_indices(W, step)
    nr, nc = len(rows), len(cols)
    heights = height_raw[np.ix_(rows, cols)].astype(np.float64) * height_scale + height_offset

    scale = float(print_width_mm) / max(1e-9, extent_x)
    x_world = (cols.astype(np.float64) - (W - 1) * 0.5) * (extent_x / max(1, W - 1))
    z_world = (rows.astype(np.float64) - (H - 1) * 0.5) * (extent_z / max(1, H - 1))
    xx, zz = np.meshgrid(x_world, z_world)
    min_h = float(heights.min())

    # Pre-parse object prototypes and make sure their lowest point stays above
    # the print bed. Some rocks/trees have geometry below the terrain minimum.
    object_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    object_floor_shift = 0.0
    if objects and shared_store is not None:
        import sr_mesh
        object_min_y = float("inf")
        for group in objects:
            if group.mesh_name not in object_cache:
                try:
                    parsed = sr_mesh.parse_combinexmesh(shared_store.read(group.mesh_name))
                    parts = sr_mesh.extract_visual_parts(parsed)
                    pos_list = []
                    idx_list = []
                    off = 0
                    for part in parts:
                        pos_list.append(part["positions"])
                        idx_list.append(part["indices"].astype(np.int64) + off)
                        off += len(part["positions"])
                    if not pos_list:
                        continue
                    positions = np.concatenate(pos_list).astype(np.float64)
                    indices = np.concatenate(idx_list)
                    if simplify_vegetation and _is_vegetation(group.name):
                        positions, indices = _vegetation_proxy(positions)
                    object_cache[group.mesh_name] = (positions, indices)
                except Exception:
                    continue
            proto = object_cache.get(group.mesh_name)
            if proto is None:
                continue
            local = proto[0][:, 1]
            bound = max(abs(float(local.min())), abs(float(local.max())))
            for inst in group.instances:
                object_min_y = min(object_min_y, float(inst[1]) - bound * float(inst[6]))
        if object_min_y < min_h:
            object_floor_shift = (min_h - object_min_y) * scale

    z0 = 0.0                                  # flat bottom
    z2 = float(base_thickness_mm) + object_floor_shift
    px = xx * scale
    py = -zz * scale
    pz = z2 + (heights - min_h) * scale

    top = np.empty((nr * nc, 3), dtype=np.float64)
    top[:, 0] = px.ravel()
    top[:, 1] = py.ravel()
    top[:, 2] = pz.ravel()

    # Top surface: (a, c, b) and (b, c, d), +Z normals after the Y-up -> Z-up map.
    r = np.arange(nr - 1, dtype=np.int64)[:, None]
    c = np.arange(nc - 1, dtype=np.int64)[None, :]
    a = r * nc + c
    b = a + 1
    c2 = a + nc
    d = c2 + 1
    top_idx = np.empty(((nr - 1) * (nc - 1) * 2, 3), dtype=np.int64)
    top_idx[0::2, 0] = a.ravel()
    top_idx[0::2, 1] = c2.ravel()
    top_idx[0::2, 2] = b.ravel()
    top_idx[1::2, 0] = b.ravel()
    top_idx[1::2, 1] = c2.ravel()
    top_idx[1::2, 2] = d.ravel()

    writer = StlWriter(path)
    try:
        writer.add(top[top_idx])

        # Side walls around the map boundary, down to Z=0.
        loop = _boundary_loop(nr, nc)
        wall_tris = []
        for i in range(len(loop)):
            r0, c0 = loop[i]
            r1, c1 = loop[(i + 1) % len(loop)]
            i0 = r0 * nc + c0
            i1 = r1 * nc + c1
            t0 = top[i0]
            t1 = top[i1]
            b0 = np.array([t0[0], t0[1], z0])
            b1 = np.array([t1[0], t1[1], z0])
            wall_tris.append(np.array([[t0, b0, b1], [t0, b1, t1]]))
        if wall_tris:
            writer.add(np.concatenate(wall_tris, axis=0))

        # Bottom of the map slab, facing -Z. A fan keeps the same boundary
        # vertices as the side walls, avoiding T-junctions.
        x0, x1 = float(top[:, 0].min()), float(top[:, 0].max())
        y0, y1 = float(top[:, 1].min()), float(top[:, 1].max())
        center = np.array([(x0 + x1) * 0.5, (y0 + y1) * 0.5, z0])
        bottom_tris = []
        for i in range(len(loop)):
            r0, c0 = loop[i]
            r1, c1 = loop[(i + 1) % len(loop)]
            i0 = r0 * nc + c0
            i1 = r1 * nc + c1
            b0 = np.array([top[i0, 0], top[i0, 1], z0])
            b1 = np.array([top[i1, 0], top[i1, 1], z0])
            bottom_tris.append(np.array([center, b1, b0]))
        if bottom_tris:
            writer.add(np.stack(bottom_tris, axis=0))

        object_tris = 0
        if objects and shared_store is not None and ssl_name is not None:
            import sr_objects
            groups = objects
            for group in groups:
                proto = object_cache.get(group.mesh_name)
                if proto is None:
                    continue
                positions, indices = proto
                chunk = []
                for inst in group.instances:
                    tr, q, sc = sr_objects.instance_matrix(inst)
                    rot = _quat_matrix(q)
                    world = (positions * float(sc[0])) @ rot.T + tr
                    # Y-up world -> Z-up print coords.
                    p = np.empty_like(world)
                    p[:, 0] = world[:, 0] * scale
                    p[:, 1] = -world[:, 2] * scale
                    p[:, 2] = z2 + (world[:, 1] - min_h) * scale
                    chunk.append(p[indices])
                    if len(chunk) >= 300:
                        tri = np.concatenate(chunk, axis=0)
                        writer.add(tri)
                        object_tris += len(tri)
                        chunk.clear()
                if chunk:
                    tri = np.concatenate(chunk, axis=0)
                    writer.add(tri)
                    object_tris += len(tri)
            if verbose:
                print(f"  print objects: {object_tris:,} triangles")
    finally:
        writer.close()

    total_height = z2 + (float(heights.max()) - min_h) * scale
    return {
        "triangles": writer.count,
        "object_triangles": object_tris if objects else 0,
        "print_width_mm": print_width_mm,
        "base_thickness_mm": base_thickness_mm,
        "scale_mm_per_m": scale,
        "total_height_mm": total_height,
        "object_floor_shift_mm": object_floor_shift,
        "map_width_mm": extent_x * scale,
        "map_depth_mm": extent_z * scale,
    }


def write_grid_solid(writer: StlWriter, top: np.ndarray, nr: int, nc: int,
                     z0: float = 0.0) -> None:
    """Write a closed solid from a row-major terrain grid (Z-up)."""
    r = np.arange(nr - 1, dtype=np.int64)[:, None]
    c = np.arange(nc - 1, dtype=np.int64)[None, :]
    a = r * nc + c
    b = a + 1
    c2 = a + nc
    d = c2 + 1
    top_idx = np.empty(((nr - 1) * (nc - 1) * 2, 3), dtype=np.int64)
    top_idx[0::2, 0] = a.ravel()
    top_idx[0::2, 1] = c2.ravel()
    top_idx[0::2, 2] = b.ravel()
    top_idx[1::2, 0] = b.ravel()
    top_idx[1::2, 1] = c2.ravel()
    top_idx[1::2, 2] = d.ravel()
    writer.add(top[top_idx])

    loop = _boundary_loop(nr, nc)
    walls = []
    for i in range(len(loop)):
        r0, c0 = loop[i]
        r1, c1 = loop[(i + 1) % len(loop)]
        t0 = top[r0 * nc + c0]
        t1 = top[r1 * nc + c1]
        b0 = np.array([t0[0], t0[1], z0])
        b1 = np.array([t1[0], t1[1], z0])
        walls.append(np.array([[t0, b0, b1], [t0, b1, t1]]))
    if walls:
        writer.add(np.concatenate(walls, axis=0))

    x0, x1 = float(top[:, 0].min()), float(top[:, 0].max())
    y0, y1 = float(top[:, 1].min()), float(top[:, 1].max())
    center = np.array([(x0 + x1) * 0.5, (y0 + y1) * 0.5, z0])
    bottom = []
    for i in range(len(loop)):
        r0, c0 = loop[i]
        r1, c1 = loop[(i + 1) % len(loop)]
        b0 = np.array([top[r0 * nc + c0, 0], top[r0 * nc + c0, 1], z0])
        b1 = np.array([top[r1 * nc + c1, 0], top[r1 * nc + c1, 1], z0])
        bottom.append(np.array([center, b1, b0]))
    if bottom:
        writer.add(np.stack(bottom, axis=0))
