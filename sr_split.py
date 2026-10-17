"""Split a 3D-print STL model into rectangular tiles for gluing."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

import sr_print


def _clip_triangles_axis(tri: np.ndarray, axis: int, value: float,
                         keep_greater: bool) -> np.ndarray:
    """Clip a triangle soup (M,3,3) by axis >= value or axis <= value."""
    if len(tri) == 0:
        return tri
    d = tri[:, :, axis] - value
    if not keep_greater:
        d = -d
    out: list[np.ndarray] = []
    for t, dv in zip(tri, d):
        inside = dv >= -1e-7
        if inside.all():
            out.append(t)
            continue
        if not inside.any():
            continue
        poly: list[np.ndarray] = []
        for i in range(3):
            j = (i + 1) % 3
            if inside[i]:
                poly.append(t[i])
            if inside[i] != inside[j]:
                dd = dv[i] - dv[j]
                if abs(dd) < 1e-12:
                    continue
                u = float(dv[i] / dd)
                poly.append(t[i] + u * (t[j] - t[i]))
        if len(poly) >= 3:
            for i in range(1, len(poly) - 1):
                out.append(np.array([poly[0], poly[i], poly[i + 1]]))
    return np.array(out, dtype=np.float64) if out else np.empty((0, 3, 3))


def _clip_box(tri: np.ndarray, xmin: float, xmax: float,
              ymin: float, ymax: float) -> np.ndarray:
    tri = _clip_triangles_axis(tri, 0, xmin, True)
    tri = _clip_triangles_axis(tri, 0, xmax, False)
    tri = _clip_triangles_axis(tri, 1, ymin, True)
    tri = _clip_triangles_axis(tri, 1, ymax, False)
    return tri


def write_split_stl(base_path: Path, height_raw: np.ndarray,
                    extent_x: float, extent_z: float, step: int,
                    height_scale: float, height_offset: float,
                    print_width_mm: float = 200.0,
                    base_thickness_mm: float = 3.0,
                    split_x: int = 2, split_y: int = 2,
                    gap_mm: float = 0.2,
                    simplify_vegetation: bool = True,
                    objects: list[Any] | None = None,
                    shared_store: Any = None,
                    ssl_name: str | None = None,
                    verbose: bool = False) -> dict:
    H, W = height_raw.shape
    rows = sr_print._grid_indices(H, step)
    cols = sr_print._grid_indices(W, step)
    nr, nc = len(rows), len(cols)
    heights = height_raw[np.ix_(rows, cols)].astype(np.float64) * height_scale + height_offset

    scale = float(print_width_mm) / max(1e-9, extent_x)
    x_world = (cols.astype(np.float64) - (W - 1) * 0.5) * (extent_x / max(1, W - 1))
    z_world = (rows.astype(np.float64) - (H - 1) * 0.5) * (extent_z / max(1, H - 1))
    xx, zz = np.meshgrid(x_world, z_world)
    min_h = float(heights.min())

    # Object prototypes + floor shift (same logic as the single-file print writer).
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
                    if simplify_vegetation and sr_print._is_vegetation(group.name):
                        positions, indices = sr_print._vegetation_proxy(positions)
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

    z2 = float(base_thickness_mm) + object_floor_shift
    top = np.empty((nr, nc, 3), dtype=np.float64)
    top[:, :, 0] = xx * scale
    top[:, :, 1] = -zz * scale
    top[:, :, 2] = z2 + (heights - min_h) * scale

    if split_x < 1 or split_y < 1:
        raise ValueError("split_x/split_y must be >= 1")
    col_edges = np.unique(np.linspace(0, nc - 1, split_x + 1).round().astype(int))
    row_edges = np.unique(np.linspace(0, nr - 1, split_y + 1).round().astype(int))
    if (len(col_edges) - 1 != split_x or len(row_edges) - 1 != split_y
            or len(col_edges) < 2 or len(row_edges) < 2):
        raise ValueError("too many tiles for this mesh resolution")

    half_gap = float(gap_mm) * 0.5
    tile_info: list[dict[str, Any]] = []
    writers: list[sr_print.StlWriter] = []
    tile_bounds: list[tuple[float, float, float, float, float, float]] = []

    for ri in range(len(row_edges) - 1):
        for ci in range(len(col_edges) - 1):
            r0, r1 = int(row_edges[ri]), int(row_edges[ri + 1])
            c0, c1 = int(col_edges[ci]), int(col_edges[ci + 1])
            sub = top[r0:r1 + 1, c0:c1 + 1, :].copy()
            snr, snc = sub.shape[0], sub.shape[1]
            if snr < 2 or snc < 2:
                continue

            # Inset internal cut faces by gap/2 so glued parts have clearance.
            if ci > 0:
                sub[:, 0, 0] += half_gap
            if ci < len(col_edges) - 2:
                sub[:, -1, 0] -= half_gap
            if ri > 0:
                sub[:, 0, 1] -= half_gap
            if ri < len(row_edges) - 2:
                sub[:, -1, 1] += half_gap

            tile_path = base_path.with_name(
                f"{base_path.stem}_r{ri + 1}c{ci + 1}{base_path.suffix}")
            writer = sr_print.StlWriter(tile_path)
            sr_print.write_grid_solid(writer, sub.reshape(-1, 3), snr, snc)
            xmin, xmax = float(sub[:, :, 0].min()), float(sub[:, :, 0].max())
            ymin, ymax = float(sub[:, :, 1].min()), float(sub[:, :, 1].max())
            zmin, zmax = 0.0, float(sub[:, :, 2].max())
            tile_bounds.append((xmin, xmax, ymin, ymax, zmin, zmax))
            writers.append(writer)
            tile_info.append({
                "file": tile_path.name,
                "row": ri + 1,
                "col": ci + 1,
                "bounds": [xmin, xmax, ymin, ymax, zmin, zmax],
            })

    # Write object shells into the overlapping tiles.
    if objects and shared_store is not None and object_cache:
        import sr_objects
        eps = 1e-6
        for group in objects:
            proto = object_cache.get(group.mesh_name)
            if proto is None:
                continue
            positions, indices = proto
            for inst in group.instances:
                tr, q, sc = sr_objects.instance_matrix(inst)
                rot = sr_print._quat_matrix(q)
                world = (positions * float(sc[0])) @ rot.T + tr
                p = np.empty_like(world)
                p[:, 0] = world[:, 0] * scale
                p[:, 1] = -world[:, 2] * scale
                p[:, 2] = z2 + (world[:, 1] - min_h) * scale
                tri = p[indices]
                bmin = tri.reshape(-1, 3).min(axis=0)
                bmax = tri.reshape(-1, 3).max(axis=0)
                for ti, (xmin, xmax, ymin, ymax, _zmin, _zmax) in enumerate(tile_bounds):
                    if bmax[0] < xmin - eps or bmin[0] > xmax + eps:
                        continue
                    if bmax[1] < ymin - eps or bmin[1] > ymax + eps:
                        continue
                    if (bmin[0] >= xmin - eps and bmax[0] <= xmax + eps
                            and bmin[1] >= ymin - eps and bmax[1] <= ymax + eps):
                        writers[ti].add(tri)
                    else:
                        clipped = _clip_box(tri, xmin, xmax, ymin, ymax)
                        if len(clipped):
                            writers[ti].add(clipped)

    for w in writers:
        w.close()
    for ti, w in zip(tile_info, writers):
        ti["triangles"] = w.count

    manifest = {
        "grid": [split_x, split_y],
        "gap_mm": gap_mm,
        "print_width_mm": print_width_mm,
        "base_thickness_mm": base_thickness_mm,
        "scale_mm_per_m": scale,
        "object_floor_shift_mm": object_floor_shift,
        "tiles": tile_info,
    }
    manifest_path = base_path.with_name(base_path.stem + "_tiles.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    return manifest
