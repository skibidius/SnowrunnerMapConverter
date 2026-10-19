#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SnowRunner Map Converter
========================

Convert a SnowRunner level .pak (or an already unpacked level folder) into a
normal 3D / image format:

  * GLB / glTF 2.0
  * Wavefront OBJ (+ MTL)
  * binary PLY
  * binary STL
  * 16-bit heightmap PNG
  * raw heightmap + JSON metadata

The terrain is reconstructed from the level's *_height_map.pct texture.
The vertical scale is detected automatically from data.stg (object meshes /
placements) and can be overridden with --max-height or --height-scale.

Requirements:
  python3 -m pip install numpy
  (Pillow is not required; the PNG writer is built in.)

Examples:
  python3 snowrunner_map_converter.py level_us_01_01.pak
  python3 snowrunner_map_converter.py level_us_01_01.pak -f obj --step 4
  python3 snowrunner_map_converter.py level_us_01_01.pak -f glb --step 2 -o map.glb
  python3 snowrunner_map_converter.py unpacked/ -f png --textures
  python3 snowrunner_map_converter.py level_us_01_01.pak --list
"""

from __future__ import annotations

import argparse
import csv
import json
from contextlib import nullcontext
import math
import os
import re
import struct
import subprocess
import sys
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

try:
    import numpy as np
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "Ошибка: нужен numpy.\n"
        "Установите его командой:  python3 -m pip install numpy\n"
    )
    raise SystemExit(1)

def _setup_console() -> None:
    """Use UTF-8 for console output; Windows cmd defaults to cp1252/cp866."""
    if sys.platform.startswith("win"):
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_setup_console()

try:
    import sr_mesh
    import sr_objects
    import sr_textures
    from sr_gltf import GltfBuilder, MeshPart
except ImportError:
    sr_mesh = None  # type: ignore[assignment]
    sr_objects = None  # type: ignore[assignment]
    sr_textures = None  # type: ignore[assignment]
    GltfBuilder = None  # type: ignore[assignment]
    MeshPart = None  # type: ignore[assignment]

try:
    import sr_tui
except ImportError:
    sr_tui = None  # type: ignore[assignment]

try:
    import sr_print
except ImportError:
    sr_print = None  # type: ignore[assignment]

try:
    import sr_split
except ImportError:
    sr_split = None  # type: ignore[assignment]

try:
    import sr_i18n
    from sr_i18n import t
except ImportError:  # pragma: no cover
    sr_i18n = None  # type: ignore[assignment]
    def t(key: str, **kwargs: object) -> str:  # type: ignore[misc]
        return key.format(**kwargs) if kwargs else key


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GAME_BLOCK_METERS = 24.0          # one terrain block in SnowRunner
TERRAIN_NODES_PER_BLOCK = 48      # 48 cells per block -> 0.5 m grid
DEFAULT_FALLBACK_MAX_HEIGHT = 200.0
PCT_MAGIC = b"TCIP"
NODATA_FLOAT = -3.0e38

FORMAT_NAMES = {
    0x00: "B8G8R8A8_UNORM",
    0x03: "R8_UNORM",
    0x0C: "BC1_UNORM",
    0x0F: "BC3_UNORM",
    0x16: "B8G8R8X8_UNORM",
    0x2A: "R32_FLOAT",
    0x33: "BC7_UNORM",
    0x34: "BC7_UNORM",
    0x35: "R16G16_FLOAT",
    0x37: "R16_UNORM",
}

FORMAT_BYTES = {
    0x00: 4,
    0x03: 1,
    0x16: 4,
    0x2A: 4,
    0x35: 4,
    0x37: 2,
}

DXGI_FORMATS = {
    0x00: 87,   # B8G8R8A8_UNORM
    0x03: 61,   # R8_UNORM
    0x0C: 71,   # BC1_UNORM
    0x0F: 77,   # BC3_UNORM
    0x16: 88,   # B8G8R8X8_UNORM
    0x2A: 41,   # R32_FLOAT
    0x33: 98,   # BC7_UNORM
    0x34: 98,   # BC7_UNORM
    0x35: 34,   # R16G16_FLOAT
    0x37: 56,   # R16_UNORM
}

VERSION = "1.1.2"


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def eprint(*args, **kwargs) -> None:
    print(*args, file=sys.stderr, **kwargs)


def human_size(n: int | float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} TB"


def safe_stem(path: Path) -> str:
    stem = path.stem
    return stem or "snowrunner_map"


def ensure_parent(path: Path) -> None:
    if path.parent and str(path.parent) not in ("", "."):
        path.parent.mkdir(parents=True, exist_ok=True)


def normalize_zip_name(name: str) -> str:
    return name.replace("\\", "/")


# ---------------------------------------------------------------------------
# File store (directory, .pak/ZIP, or single file)
# ---------------------------------------------------------------------------

class FileStore:
    """Reads files from a directory, a ZIP/.pak archive, or a single file."""

    def __init__(self, path: Path):
        self.path = path
        self._zip: zipfile.ZipFile | None = None
        self._entries: dict[str, object] = {}

        if path.is_dir():
            for p in path.rglob("*"):
                if p.is_file():
                    self._entries.setdefault(p.name, p)
        elif zipfile.is_zipfile(path):
            self._zip = zipfile.ZipFile(path)
            for name in self._zip.namelist():
                base = normalize_zip_name(name).rsplit("/", 1)[-1]
                if base:
                    self._entries.setdefault(base, name)
        elif path.is_file():
            self._entries[path.name] = path
        else:
            raise FileNotFoundError(f"Не найден входной файл/папка: {path}")

    def __enter__(self) -> "FileStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._zip is not None:
            self._zip.close()
            self._zip = None

    def names(self) -> list[str]:
        return sorted(self._entries)

    def has(self, name: str) -> bool:
        return name in self._entries

    def find_suffix(self, suffix: str) -> str | None:
        suffix = suffix.lower()
        candidates = [n for n in self._entries if n.lower().endswith(suffix)]
        return sorted(candidates, key=len)[0] if candidates else None

    def find_contains(self, needle: str) -> str | None:
        needle = needle.lower()
        candidates = [n for n in self._entries if needle in n.lower()]
        return sorted(candidates, key=len)[0] if candidates else None

    def read(self, name: str) -> bytes:
        entry = self._entries[name]
        if isinstance(entry, Path):
            return entry.read_bytes()
        if self._zip is None:
            raise RuntimeError("Архив закрыт")
        return self._zip.read(str(entry))

    def read_range(self, name: str, offset: int, size: int) -> bytes:
        entry = self._entries[name]
        if isinstance(entry, Path):
            with entry.open("rb") as f:
                f.seek(offset)
                return f.read(size)
        if self._zip is None:
            raise RuntimeError("Архив закрыт")
        with self._zip.open(str(entry)) as f:
            f.seek(offset)
            return f.read(size)

    def size(self, name: str) -> int:
        entry = self._entries[name]
        if isinstance(entry, Path):
            return entry.stat().st_size
        if self._zip is None:
            raise RuntimeError("Архив закрыт")
        return self._zip.getinfo(str(entry)).file_size


# ---------------------------------------------------------------------------
# Swarm Engine .pct textures
# ---------------------------------------------------------------------------

@dataclass
class PctTexture:
    name: str
    data: bytes
    width: int
    height: int
    depth: int
    faces: int
    format_id: int
    mip_count: int
    top_mip_size: int
    payload_offset: int
    payload_end: int

    @property
    def format_name(self) -> str:
        return FORMAT_NAMES.get(self.format_id, f"UNKNOWN_0x{self.format_id:02X}")

    @property
    def payload(self) -> memoryview:
        return memoryview(self.data)[self.payload_offset:self.payload_end]

    def array(self) -> np.ndarray:
        """Return the first mip as a numpy array, if the format is uncompressed."""
        bpp = FORMAT_BYTES.get(self.format_id)
        if bpp is None:
            raise ValueError(f"{self.name}: формат {self.format_name} сжат/не поддерживается")
        count = self.width * self.height * max(1, self.depth) * max(1, self.faces)
        if self.format_id == 0x37:
            dtype = "<u2"
        elif self.format_id == 0x2A:
            dtype = "<f4"
        elif self.format_id == 0x35:
            dtype = "<f2"
        else:
            dtype = "u1"
        need = count * bpp
        if self.payload_offset + need > self.payload_end:
            raise ValueError(f"{self.name}: недостаточно данных для первого mip")
        arr = np.frombuffer(self.data, dtype=dtype, count=count,
                            offset=self.payload_offset)
        if self.format_id in (0x00, 0x16):
            arr = arr.reshape(-1, 4)
        elif self.format_id == 0x35:
            arr = arr.reshape(-1, 2)
        else:
            arr = arr.reshape((max(1, self.depth) * max(1, self.faces),
                               self.height, self.width))
            if arr.shape[0] == 1:
                arr = arr[0]
        return arr


def parse_pct(name: str, data: bytes) -> PctTexture:
    if len(data) < 0x52:
        raise ValueError(f"{name}: слишком маленький .pct")
    if data[0x06:0x0A] != PCT_MAGIC:
        raise ValueError(f"{name}: нет сигнатуры TCIP")

    width, height, depth, faces = struct.unpack_from("<4I", data, 0x10)
    format_id = struct.unpack_from("<I", data, 0x26)[0]
    mip_count = struct.unpack_from("<I", data, 0x30)[0]
    table_end = struct.unpack_from("<Q", data, 0x36)[0]
    top_mip_size = struct.unpack_from("<I", data, 0x3E)[0]

    if table_end + 8 > len(data):
        raise ValueError(f"{name}: повреждена таблица mip-уровней")

    marker = struct.unpack_from("<H", data, table_end)[0]
    if marker == 0x0104:
        terminal = struct.unpack_from("<Q", data, table_end + 2)[0]
    elif marker == 0x00FF:
        terminal = table_end
    else:
        raise ValueError(f"{name}: неизвестный layout marker 0x{marker:04X}")

    payload_offset = terminal + 6
    payload_end = len(data) - 6  # engine footer: 01 00 + uint32 file size
    if payload_offset < 0 or payload_offset >= payload_end:
        raise ValueError(f"{name}: некорректное смещение данных")

    return PctTexture(
        name=name, data=data,
        width=width, height=height, depth=max(1, depth), faces=max(1, faces),
        format_id=format_id, mip_count=max(1, mip_count),
        top_mip_size=top_mip_size,
        payload_offset=payload_offset, payload_end=payload_end,
    )


# ---------------------------------------------------------------------------
# data.stg
# ---------------------------------------------------------------------------

@dataclass
class StgInfo:
    offsets: tuple[int, int, int, int]
    size: int
    section1_vertices: np.ndarray | None = None
    section1_inlier_info: str = ""
    objects: list[dict] = field(default_factory=list)


def read_stg_header(store: FileStore, name: str) -> tuple[int, int, int, int]:
    raw = store.read_range(name, 0, 16)
    if len(raw) != 16:
        raise ValueError("data.stg: не удалось прочитать заголовок")
    offsets = struct.unpack("<4I", raw)
    size = store.size(name)
    if offsets[3] and offsets[3] != size:
        # Some versions store a different value here; only warn in verbose mode.
        pass
    # The three values are section starts, not necessarily sorted: some
    # levels store the vegetation section before the mesh section.
    if not (16 < offsets[0] < size and offsets[1] < size and offsets[2] <= size):
        raise ValueError(
            "data.stg: заголовок не похож на SnowRunner scene (offset chain "
            f"{offsets})."
        )
    return offsets  # type: ignore[return-value]


def stg_object_end(offsets: tuple[int, int, int, int]) -> int:
    """End of the object/scene section (the earliest typed section start)."""
    return min(offsets[0], offsets[1], offsets[2], offsets[3])


def stg_mesh_bounds(offsets: tuple[int, int, int, int]) -> tuple[int, int]:
    """Start/end of the static mesh section (32-byte vertex records)."""
    start = offsets[0]
    ends = [o for o in (offsets[1], offsets[2], offsets[3]) if o > start]
    end = min(ends) if ends else offsets[3]
    return start, end


def extract_section1_vertices(section: bytes) -> np.ndarray:
    """Extract 32-byte vertex records from the mesh section of data.stg.

    SnowRunner stores this section as records of 32 bytes:
        [0..10]  packed data (normals/color/...)
        [11..22] float32 position (x, y, z)
        [23..26] float32 (often 2.0)
        [27..30] packed normal/color
        [31]     record separator (0)

    The exact meaning of the other fields is not needed for height calibration.
    """
    if len(section) < 32 * 20:
        return np.empty((0, 3), dtype=np.float32)

    best: np.ndarray | None = None
    best_count = 0
    for sep_mode in (0, 1, 2):
        n = len(section) // 32
        arr = np.frombuffer(section[: n * 32], dtype=np.uint8).reshape(n, 32)
        pos = np.frombuffer(arr[:, 11:23].tobytes(), dtype="<f4").reshape(n, 3)
        w = np.frombuffer(arr[:, 23:27].tobytes(), dtype="<f4").ravel()
        base = (
            np.isfinite(pos).all(axis=1)
            & (np.abs(pos[:, 0]) < 5000.0)
            & (np.abs(pos[:, 1]) < 5000.0)
            & (np.abs(pos[:, 2]) < 5000.0)
            & (w > 0.1)
            & (w < 100.0)
        )
        if sep_mode == 0:
            valid = base & (arr[:, 31] == 0)
        elif sep_mode == 1:
            valid = base & (arr[:, 31] <= 1)
        else:
            valid = base
        cnt = int(valid.sum())
        if cnt > best_count:
            best_count = cnt
            best = pos[valid]
    if best is None:
        return np.empty((0, 3), dtype=np.float32)
    return best.astype(np.float64)


def read_stg_section1(store: FileStore, name: str, off0: int, off1: int) -> np.ndarray:
    if off1 <= off0 or (off1 - off0) > 64 * 1024 * 1024:
        return np.empty((0, 3), dtype=np.float64)
    section = store.read_range(name, off0, off1 - off0)
    return extract_section1_vertices(section)


def extract_objects(store: FileStore, name: str, object_end: int,
                    map_extent: float) -> list[dict]:
    """Extract object/actor placements from the scene section.

    The object table is a binary stream with many record kinds. This function
    uses a conservative heuristic: a uint16-length-prefixed printable path,
    followed by NUL and 8 float32 values. It finds all ambient actor / prop
    placements that use this common record layout.
    """
    if object_end <= 16 or object_end > 64 * 1024 * 1024:
        return []
    section = store.read_range(name, 16, object_end - 16)
    pat = re.compile(rb"[\x20-\x7e]{3,240}")
    limit = map_extent / 2.0 + map_extent * 0.10
    seen: set[tuple[str, int, int, int]] = set()
    result: list[dict] = []

    for m in pat.finditer(section):
        s = m.group()
        st, en = m.start(), m.end()
        if b"/" not in s:
            continue
        if st < 2 or en >= len(section) or section[en] != 0:
            continue
        n = struct.unpack_from("<H", section, st - 2)[0]
        if n != len(s) + 1:
            continue
        if en + 1 + 32 > len(section):
            continue
        vals = struct.unpack_from("<8f", section, en + 1)
        x, y, z = vals[:3]
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            continue
        if not (-limit <= x <= limit and -limit <= z <= limit):
            continue
        if not (-200.0 <= y <= 3000.0):
            continue
        try:
            text = s.decode("latin-1")
        except UnicodeDecodeError:
            continue
        key = (text, int(round(x)), int(round(y)), int(round(z)))
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "name": text,
            "x": round(float(x), 4),
            "y": round(float(y), 4),
            "z": round(float(z), 4),
            "params": [round(float(v), 6) if math.isfinite(v) else None for v in vals[3:]],
        })
    result.sort(key=lambda o: (o["name"], o["x"], o["z"]))
    return result


# ---------------------------------------------------------------------------
# Height calibration
# ---------------------------------------------------------------------------

def _sample_heightmap(height: np.ndarray, xs: np.ndarray, zs: np.ndarray,
                      extent_x: float, extent_z: float) -> tuple[np.ndarray, np.ndarray]:
    H, W = height.shape
    sx = extent_x / max(1, W - 1)
    sz = extent_z / max(1, H - 1)
    u = np.rint((xs + extent_x / 2.0) / sx).astype(np.int64)
    v = np.rint((zs + extent_z / 2.0) / sz).astype(np.int64)
    ok = (u >= 0) & (u < W) & (v >= 0) & (v < H)
    return height[v[ok], u[ok]].astype(np.float64), ok


def robust_height_fit(points: np.ndarray, height: np.ndarray,
                      extent_x: float, extent_z: float,
                      initial_scale: float) -> dict | None:
    """Fit y = scale * raw_height + offset using the thickest residual mode."""
    if points is None or len(points) < 30:
        return None
    xs = points[:, 0]
    ys = points[:, 1]
    zs = points[:, 2]
    raw, ok = _sample_heightmap(height, xs, zs, extent_x, extent_z)
    ys = ys[ok]
    good = (ys > 0.5) & np.isfinite(ys) & (raw > 50.0)
    raw = raw[good]
    ys = ys[good]
    if len(raw) < 30:
        return None

    scale = float(initial_scale)
    offset = 0.0
    keep = np.ones(len(raw), dtype=bool)
    mode = 0.0
    for _ in range(12):
        resid = ys - scale * raw - offset
        lo, hi = np.quantile(resid, [0.02, 0.98])
        if hi - lo < 0.5:
            hi = lo + 0.5
        hist, edges = np.histogram(resid, bins=160, range=(lo, hi))
        k = int(np.argmax(hist))
        mode = float((edges[k] + edges[k + 1]) * 0.5)
        keep = np.abs(resid - mode) < 2.0
        if int(keep.sum()) < 20:
            break
        A = np.column_stack([raw[keep], np.ones(int(keep.sum()))])
        current_y = ys[keep]
        coef, *_ = np.linalg.lstsq(A, current_y, rcond=None)
        # Trim outliers a few times.
        for _ in range(4):
            pred = A @ coef
            r = current_y - pred
            med = float(np.median(r))
            mad = float(np.median(np.abs(r - med))) * 1.4826
            if mad < 1e-9:
                break
            k2 = np.abs(r - med) < 3.0 * mad
            if int(k2.sum()) < 20:
                break
            A = A[k2]
            current_y = current_y[k2]
            coef, *_ = np.linalg.lstsq(A, current_y, rcond=None)
        new_scale = float(coef[0])
        new_offset = float(coef[1])
        if not math.isfinite(new_scale) or new_scale <= 0:
            return None
        if abs(new_scale - scale) < 1e-9 and abs(new_offset - offset) < 1e-6:
            scale, offset = new_scale, new_offset
            break
        scale, offset = new_scale, new_offset

    resid = ys - scale * raw - offset
    inliers = int((np.abs(resid - mode) < 2.0).sum())
    ratio = inliers / max(1, len(resid))
    if not (1e-5 < scale < 0.05):
        return None
    return {
        "scale": scale,
        "offset": offset,
        "inliers": inliers,
        "total": int(len(resid)),
        "ratio": ratio,
        "mode": mode,
        "max_height": scale * 65535.0 + offset,
    }


def detect_height_scale(height: np.ndarray, store: FileStore | None,
                        stg_name: str | None, ssl_name: str | None,
                        extent_x: float, extent_z: float,
                        args: argparse.Namespace) -> dict:
    fallback_max = float(args.max_height)
    fallback_scale = fallback_max / 65535.0
    if args.height_scale is not None:
        return {
            "scale": float(args.height_scale), "offset": 0.0,
            "max_height": float(args.height_scale) * 65535.0,
            "source": t("calib_manual"),
            "inliers": None, "total": None, "ratio": None,
        }

    candidates: list[dict] = []
    if store is not None and stg_name is not None:
        try:
            offsets = read_stg_header(store, stg_name)
            mesh_start, mesh_end = stg_mesh_bounds(offsets)
            verts = read_stg_section1(store, stg_name, mesh_start, mesh_end)
            fit = robust_height_fit(verts, height, extent_x, extent_z, fallback_scale)
            if fit is not None:
                fit["source"] = t("calib_meshes", inliers=fit['inliers'],
                                  total=fit['total'], ratio=fit['ratio']*100)
                candidates.append(fit)
        except Exception as exc:  # noqa: BLE001
            eprint(f"  ! Не удалось извлечь вершины для калибровки: {exc}")

    if store is not None and stg_name is not None:
        try:
            offsets = read_stg_header(store, stg_name)
            objs = extract_objects(store, stg_name, stg_object_end(offsets),
                                   max(extent_x, extent_z))
            if objs:
                pts = np.array([[o["x"], o["y"], o["z"]] for o in objs], dtype=np.float64)
                fit = robust_height_fit(pts, height, extent_x, extent_z, fallback_scale)
                if fit is not None:
                    fit["source"] = t("calib_objects", inliers=fit['inliers'],
                                      total=fit['total'], ratio=fit['ratio']*100)
                    candidates.append(fit)
        except Exception as exc:  # noqa: BLE001
            eprint(f"  ! Не удалось извлечь объекты для калибровки: {exc}")

    # data_for_ssl.bin is the most reliable fallback for levels with an
    # unusual data.stg header layout.
    if store is not None and ssl_name is not None and sr_objects is not None:
        try:
            pts = sr_objects.parse_all_points(store.read(ssl_name),
                                              max(extent_x, extent_z))
            fit = robust_height_fit(pts, height, extent_x, extent_z, fallback_scale)
            if fit is not None:
                fit["source"] = t("calib_ssl_objects", inliers=fit['inliers'],
                                  total=fit['total'], ratio=fit['ratio']*100)
                candidates.append(fit)
        except Exception as exc:  # noqa: BLE001
            eprint(f"  ! Не удалось извлечь объекты data_for_ssl для калибровки: {exc}")

    if candidates:
        # Prefer a candidate with a high inlier ratio; prefer mesh vertices on ties.
        candidates.sort(key=lambda c: (c["ratio"], c["total"]), reverse=True)
        best = candidates[0]
        if best["ratio"] >= 0.08:
            return best

    return {
        "scale": fallback_scale, "offset": 0.0,
        "max_height": fallback_max,
        "source": t("calib_fallback", maxh=fallback_max),
        "inliers": None, "total": None, "ratio": None,
    }


# ---------------------------------------------------------------------------
# Mesh construction
# ---------------------------------------------------------------------------

@dataclass
class Mesh:
    name: str
    positions: np.ndarray          # (N, 3) float32
    normals: np.ndarray | None     # (N, 3) float32
    uvs: np.ndarray | None         # (N, 2) float32
    indices: np.ndarray            # (M, 3) uint32
    material: str = "terrain"
    color: tuple[float, float, float] = (0.65, 0.65, 0.60)
    alpha: float = 1.0

    @property
    def vertex_count(self) -> int:
        return int(len(self.positions))

    @property
    def triangle_count(self) -> int:
        return int(len(self.indices))


def grid_indices(n: int, step: int) -> np.ndarray:
    step = max(1, int(step))
    idx = np.arange(0, n, step, dtype=np.int64)
    if idx.size == 0 or idx[-1] != n - 1:
        idx = np.append(idx, n - 1)
    return idx


def build_terrain_mesh(height_raw: np.ndarray, extent_x: float, extent_z: float,
                       height_scale: float, height_offset: float,
                       step: int, name: str = "terrain") -> Mesh:
    H, W = height_raw.shape
    rows = grid_indices(H, step)
    cols = grid_indices(W, step)
    if rows.size < 2 or cols.size < 2:
        raise ValueError("Слишком маленькая карта высот")

    heights = height_raw[np.ix_(rows, cols)].astype(np.float64)
    heights = heights * height_scale + height_offset

    xs = (cols.astype(np.float64) - (W - 1) * 0.5) * (extent_x / max(1, W - 1))
    zs = (rows.astype(np.float64) - (H - 1) * 0.5) * (extent_z / max(1, H - 1))
    xx, zz = np.meshgrid(xs, zs)

    positions = np.empty((heights.size, 3), dtype=np.float32)
    positions[:, 0] = xx.ravel()
    positions[:, 1] = heights.ravel()
    positions[:, 2] = zz.ravel()

    # Normals from the height field.
    if heights.shape[0] >= 2 and heights.shape[1] >= 2:
        dhdz, dhdx = np.gradient(heights, zs, xs, edge_order=1)
    else:
        dhdz = np.zeros_like(heights)
        dhdx = np.zeros_like(heights)
    nx = -dhdx
    ny = np.ones_like(heights)
    nz = -dhdz
    inv = 1.0 / np.sqrt(nx * nx + ny * ny + nz * nz)
    normals = np.empty((heights.size, 3), dtype=np.float32)
    normals[:, 0] = (nx * inv).ravel()
    normals[:, 1] = (ny * inv).ravel()
    normals[:, 2] = (nz * inv).ravel()

    u = (xx - xs[0]) / max(1e-12, xs[-1] - xs[0])
    v = (zz - zs[0]) / max(1e-12, zs[-1] - zs[0])
    uvs = np.empty((heights.size, 2), dtype=np.float32)
    uvs[:, 0] = u.ravel()
    uvs[:, 1] = v.ravel()

    nr = heights.shape[0]
    nc = heights.shape[1]
    r = np.arange(nr - 1, dtype=np.uint32)[:, None]
    c = np.arange(nc - 1, dtype=np.uint32)[None, :]
    a = r * np.uint32(nc) + c
    b = a + 1
    c2 = a + np.uint32(nc)
    d = c2 + 1
    # Winding: (a, c2, b) and (b, c2, d) face +Y.
    indices = np.empty(((nr - 1) * (nc - 1) * 2, 3), dtype=np.uint32)
    indices[0::2, 0] = a.ravel()
    indices[0::2, 1] = c2.ravel()
    indices[0::2, 2] = b.ravel()
    indices[1::2, 0] = b.ravel()
    indices[1::2, 1] = c2.ravel()
    indices[1::2, 2] = d.ravel()

    return Mesh(name=name, positions=positions, normals=normals, uvs=uvs,
                indices=indices, material="terrain",
                color=(0.58, 0.63, 0.48))


def build_water_mesh(water: np.ndarray, extent_x: float, extent_z: float,
                     step: int, offset: float, name: str = "water") -> Mesh | None:
    H, W = water.shape
    rows = grid_indices(H, step)
    cols = grid_indices(W, step)
    if rows.size < 2 or cols.size < 2:
        return None

    values = water[np.ix_(rows, cols)].astype(np.float64)
    valid = np.isfinite(values) & (values > NODATA_FLOAT)
    if not valid.any():
        return None

    xs = np.linspace(-extent_x / 2.0, extent_x / 2.0, W)
    zs = np.linspace(-extent_z / 2.0, extent_z / 2.0, H)
    xs = xs[cols]
    zs = zs[rows]
    xx, zz = np.meshgrid(xs, zs)

    positions = np.empty((values.size, 3), dtype=np.float32)
    positions[:, 0] = xx.ravel()
    positions[:, 1] = np.where(valid, values, 0.0).ravel() + offset
    positions[:, 2] = zz.ravel()

    normals = np.zeros((values.size, 3), dtype=np.float32)
    normals[:, 1] = 1.0

    uvs = np.empty((values.size, 2), dtype=np.float32)
    uvs[:, 0] = ((xx - xs[0]) / max(1e-12, xs[-1] - xs[0])).ravel()
    uvs[:, 1] = ((zz - zs[0]) / max(1e-12, zs[-1] - zs[0])).ravel()

    nr, nc = values.shape
    r = np.arange(nr - 1, dtype=np.int64)[:, None]
    c = np.arange(nc - 1, dtype=np.int64)[None, :]
    a = r * nc + c
    b = a + 1
    c2 = a + nc
    d = c2 + 1
    face_ok = valid.ravel()[a] & valid.ravel()[b] & valid.ravel()[c2] & valid.ravel()[d]
    if not face_ok.any():
        return None
    a = a[face_ok]
    b = b[face_ok]
    c2 = c2[face_ok]
    d = d[face_ok]
    indices = np.empty((a.size * 2, 3), dtype=np.uint32)
    indices[0::2, 0] = a
    indices[0::2, 1] = c2
    indices[0::2, 2] = b
    indices[1::2, 0] = b
    indices[1::2, 1] = c2
    indices[1::2, 2] = d

    # Drop vertices that are not referenced by any water triangle.
    used = np.unique(indices)
    remap = np.full(len(positions), -1, dtype=np.int64)
    remap[used] = np.arange(len(used), dtype=np.int64)
    positions = positions[used]
    normals = normals[used]
    uvs = uvs[used]
    indices = remap[indices].astype(np.uint32)

    return Mesh(name=name, positions=positions, normals=normals, uvs=uvs,
                indices=indices, material="water",
                color=(0.16, 0.38, 0.66), alpha=0.55)


def build_marker_mesh(objects: Sequence[dict], size: float,
                      name: str = "objects") -> Mesh | None:
    if not objects:
        return None
    half = size * 0.5
    # 24 vertices (4 per face) so that every face has a correct normal.
    face_defs = [
        ([-half, -half, -half], [half, -half, -half], [half, half, -half], [-half, half, -half], [0, 0, -1]),
        ([half, -half, half], [-half, -half, half], [-half, half, half], [half, half, half], [0, 0, 1]),
        ([-half, -half, half], [-half, -half, -half], [-half, half, -half], [-half, half, half], [-1, 0, 0]),
        ([half, -half, -half], [half, -half, half], [half, half, half], [half, half, -half], [1, 0, 0]),
        ([-half, -half, -half], [half, -half, -half], [half, -half, half], [-half, -half, half], [0, -1, 0]),
        ([-half, half, half], [half, half, half], [half, half, -half], [-half, half, -half], [0, 1, 0]),
    ]
    cube_vertices = np.array([v for a, b, c, d, n in face_defs for v in (a, b, c, d)], dtype=np.float32)
    cube_normals = np.array([n for a, b, c, d, n in face_defs for _ in range(4)], dtype=np.float32)
    # Two triangles per face, CCW when viewed from outside.
    cube_faces = []
    for face in range(6):
        o = face * 4
        cube_faces.extend([[o, o + 1, o + 2], [o, o + 2, o + 3]])
    cube_faces = np.array(cube_faces, dtype=np.uint32)

    n = len(objects)
    positions = np.empty((n * 24, 3), dtype=np.float32)
    normals = np.empty((n * 24, 3), dtype=np.float32)
    uvs = np.zeros((n * 24, 2), dtype=np.float32)
    indices = np.empty((n * 12, 3), dtype=np.uint32)
    for i, obj in enumerate(objects):
        center = np.array([obj["x"], obj["y"], obj["z"]], dtype=np.float32)
        positions[i * 24:(i + 1) * 24] = cube_vertices + center
        normals[i * 24:(i + 1) * 24] = cube_normals
        indices[i * 12:(i + 1) * 12] = cube_faces + np.uint32(i * 24)
    return Mesh(name=name, positions=positions, normals=normals, uvs=uvs,
                indices=indices, material="objects",
                color=(0.85, 0.25, 0.15))


# ---------------------------------------------------------------------------
# PNG writer (no external dependencies)
# ---------------------------------------------------------------------------

def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + tag + payload +
            struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))


def write_png(path: Path, pixels: np.ndarray, bit_depth: int = 8,
              color_type: int = 0) -> None:
    """Write a PNG.

    color_type: 0 = grayscale, 2 = RGB, 6 = RGBA.
    bit_depth: 8 or 16 (16 is written big-endian).
    """
    ensure_parent(path)
    h, w = pixels.shape[:2]
    if bit_depth == 16:
        if pixels.dtype != np.uint16:
            pixels = np.clip(pixels, 0, 65535).astype(np.uint16)
        raw = pixels.astype(">u2").tobytes()
        bpp = 2 * (1 if color_type == 0 else 3 if color_type == 2 else 4)
    else:
        if pixels.dtype != np.uint8:
            pixels = np.clip(pixels, 0, 255).astype(np.uint8)
        raw = pixels.tobytes()
        bpp = 1 if color_type == 0 else 3 if color_type == 2 else 4

    row_len = w * bpp
    filtered = bytearray()
    for y in range(h):
        filtered.append(0)  # filter type: None
        filtered.extend(raw[y * row_len:(y + 1) * row_len])

    ihdr = struct.pack(">IIBBBBB", w, h, bit_depth, color_type, 0, 0, 0)
    data = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(filtered), 6))
        + _png_chunk(b"IEND", b"")
    )
    path.write_bytes(data)


def colorize_heightmap(height: np.ndarray, water: np.ndarray | None = None) -> np.ndarray:
    """Return an RGB hillshade preview of the heightmap."""
    h = height.astype(np.float64)
    lo, hi = np.quantile(h, [0.01, 0.995])
    if hi <= lo:
        hi = lo + 1.0
    t = np.clip((h - lo) / (hi - lo), 0.0, 1.0)

    # Simple terrain palette: deep water/lowland -> green -> brown -> rock/snow.
    stops = np.array([0.00, 0.10, 0.28, 0.50, 0.72, 1.00])
    colors = np.array([
        [40, 68, 105],
        [66, 112, 88],
        [104, 137, 82],
        [150, 145, 100],
        [158, 137, 118],
        [242, 242, 238],
    ], dtype=np.float64)
    rgb = np.empty(h.shape + (3,), dtype=np.float64)
    for ch in range(3):
        rgb[..., ch] = np.interp(t, stops, colors[:, ch])

    # Hillshade.
    gy, gx = np.gradient(h)
    nx = -gx
    ny = np.ones_like(h)
    nz = -gy
    inv = 1.0 / np.sqrt(nx * nx + ny * ny + nz * nz)
    nx *= inv
    ny *= inv
    nz *= inv
    light = np.array([-0.45, 0.75, -0.48], dtype=np.float64)
    light /= np.linalg.norm(light)
    shade = np.clip(nx * light[0] + ny * light[1] + nz * light[2], 0.0, 1.0)
    shade = 0.55 + 0.45 * shade
    rgb *= shade[..., None]

    if water is not None and water.size:
        H, W = h.shape
        wh, ww = water.shape
        yi = np.clip((np.arange(H) * (wh / H)).astype(int), 0, wh - 1)
        xi = np.clip((np.arange(W) * (ww / W)).astype(int), 0, ww - 1)
        w = water[np.ix_(yi, xi)]
        wm = np.isfinite(w) & (w > NODATA_FLOAT)
        # water is drawn where the water surface is above the terrain
        wm &= (w > h)
        rgb[wm] = rgb[wm] * 0.35 + np.array([35, 95, 165]) * 0.65

    return np.clip(rgb, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------

def _format_float(v: float) -> str:
    # Compact but precise enough for terrain data.
    return f"{v:.5f}".rstrip("0").rstrip(".") if abs(v) < 1e6 else f"{v:.3f}"


def export_obj(path: Path, meshes: Sequence[Mesh], write_normals: bool = False,
               write_uvs: bool = True) -> None:
    ensure_parent(path)
    mtl_path = path.with_suffix(".mtl")
    materials = {m.material: m for m in meshes}
    with mtl_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(f"# SnowRunner Map Converter {VERSION}\n")
        for key, mesh in materials.items():
            r, g, b = mesh.color
            f.write(f"\nnewmtl {key}\n")
            f.write(f"Kd {r:.4f} {g:.4f} {b:.4f}\n")
            f.write("Ka 0 0 0\nKs 0.05 0.05 0.05\nNs 10\nillum 2\n")
            if mesh.alpha < 1.0:
                f.write(f"d {mesh.alpha:.4f}\n")
                f.write("illum 4\n")

    v_off = 1
    vt_off = 1
    vn_off = 1
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(f"# SnowRunner Map Converter {VERSION}\n")
        f.write(f"# meshes: {len(meshes)}\n")
        f.write(f"mtllib {mtl_path.name}\n")
        for mesh in meshes:
            f.write(f"\no {mesh.name}\n")
            pos = mesh.positions
            for i in range(0, len(pos), 50000):
                chunk = pos[i:i + 50000]
                f.write("".join(
                    f"v {_format_float(float(x))} {_format_float(float(y))} {_format_float(float(z))}\n"
                    for x, y, z in chunk
                ))
            if write_uvs and mesh.uvs is not None:
                uvs = mesh.uvs
                for i in range(0, len(uvs), 50000):
                    chunk = uvs[i:i + 50000]
                    f.write("".join(
                        f"vt {_format_float(float(u))} {_format_float(float(v))}\n"
                        for u, v in chunk
                    ))
            if write_normals and mesh.normals is not None:
                normals = mesh.normals
                for i in range(0, len(normals), 50000):
                    chunk = normals[i:i + 50000]
                    f.write("".join(
                        f"vn {_format_float(float(x))} {_format_float(float(y))} {_format_float(float(z))}\n"
                        for x, y, z in chunk
                    ))
            f.write(f"usemtl {mesh.material}\n")
            idx = mesh.indices.astype(np.int64)
            has_uv = write_uvs and mesh.uvs is not None
            has_nrm = write_normals and mesh.normals is not None
            for i in range(0, len(idx), 50000):
                chunk = idx[i:i + 50000]
                lines = []
                for a, b, c in chunk:
                    va = a + v_off
                    vb = b + v_off
                    vc = c + v_off
                    if has_uv and has_nrm:
                        ta, tb, tc = a + vt_off, b + vt_off, c + vt_off
                        na, nb, nc = a + vn_off, b + vn_off, c + vn_off
                        lines.append(f"f {va}/{ta}/{na} {vb}/{tb}/{nb} {vc}/{tc}/{nc}\n")
                    elif has_uv:
                        ta, tb, tc = a + vt_off, b + vt_off, c + vt_off
                        lines.append(f"f {va}/{ta} {vb}/{tb} {vc}/{tc}\n")
                    elif has_nrm:
                        na, nb, nc = a + vn_off, b + vn_off, c + vn_off
                        lines.append(f"f {va}//{na} {vb}//{nb} {vc}//{nc}\n")
                    else:
                        lines.append(f"f {va} {vb} {vc}\n")
                f.write("".join(lines))
            v_off += mesh.vertex_count
            if has_uv:
                vt_off += mesh.vertex_count
            if has_nrm:
                vn_off += mesh.vertex_count


def export_ply(path: Path, meshes: Sequence[Mesh], include_colors: bool = True) -> None:
    ensure_parent(path)
    total_v = sum(m.vertex_count for m in meshes)
    total_f = sum(m.triangle_count for m in meshes)

    vertex_fields = [
        ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
        ("nx", "<f4"), ("ny", "<f4"), ("nz", "<f4"),
    ]
    if include_colors:
        vertex_fields += [("red", "u1"), ("green", "u1"), ("blue", "u1")]
    vertex_dtype = np.dtype(vertex_fields)
    face_dtype = np.dtype([("n", "u1"), ("a", "<i4"), ("b", "<i4"), ("c", "<i4")])

    header = (
        "ply\n"
        "format binary_little_endian 1.0\n"
        f"comment SnowRunner Map Converter {VERSION}\n"
        f"element vertex {total_v}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property float nx\nproperty float ny\nproperty float nz\n"
    )
    if include_colors:
        header += "property uchar red\nproperty uchar green\nproperty uchar blue\n"
    header += (
        f"element face {total_f}\n"
        "property list uchar int vertex_indices\n"
        "end_header\n"
    )

    with path.open("wb") as f:
        f.write(header.encode("ascii"))
        # PLY requires all vertices first, then all faces.
        for mesh in meshes:
            n = mesh.vertex_count
            v = np.empty(n, dtype=vertex_dtype)
            v["x"], v["y"], v["z"] = mesh.positions[:, 0], mesh.positions[:, 1], mesh.positions[:, 2]
            if mesh.normals is not None:
                v["nx"], v["ny"], v["nz"] = mesh.normals[:, 0], mesh.normals[:, 1], mesh.normals[:, 2]
            else:
                v["nx"], v["ny"], v["nz"] = 0.0, 1.0, 0.0
            if include_colors:
                r, g, b = mesh.color
                v["red"] = int(round(r * 255))
                v["green"] = int(round(g * 255))
                v["blue"] = int(round(b * 255))
            v.tofile(f)

        offset = 0
        for mesh in meshes:
            m = mesh.triangle_count
            faces = np.empty(m, dtype=face_dtype)
            faces["n"] = 3
            faces["a"] = mesh.indices[:, 0].astype(np.int32) + offset
            faces["b"] = mesh.indices[:, 1].astype(np.int32) + offset
            faces["c"] = mesh.indices[:, 2].astype(np.int32) + offset
            faces.tofile(f)
            offset += mesh.vertex_count


def export_stl(path: Path, meshes: Sequence[Mesh]) -> None:
    ensure_parent(path)
    total_f = sum(m.triangle_count for m in meshes)
    triangle_dtype = np.dtype([
        ("normal", "<f4", (3,)),
        ("vertices", "<f4", (3, 3)),
        ("attr", "<u2"),
    ])
    with path.open("wb") as f:
        header = f"SnowRunner Map Converter {VERSION}".encode("ascii")[:80]
        f.write(header.ljust(80, b"\0"))
        f.write(struct.pack("<I", total_f))
        for mesh in meshes:
            pos = mesh.positions.astype(np.float64)
            idx = mesh.indices.astype(np.int64)
            v0 = pos[idx[:, 0]]
            v1 = pos[idx[:, 1]]
            v2 = pos[idx[:, 2]]
            nrm = np.cross(v1 - v0, v2 - v0)
            length = np.linalg.norm(nrm, axis=1)
            length[length < 1e-12] = 1.0
            nrm /= length[:, None]
            tri = np.empty(len(idx), dtype=triangle_dtype)
            tri["normal"] = nrm.astype(np.float32)
            tri["vertices"][:, 0, :] = v0.astype(np.float32)
            tri["vertices"][:, 1, :] = v1.astype(np.float32)
            tri["vertices"][:, 2, :] = v2.astype(np.float32)
            tri["attr"] = 0
            tri.tofile(f)


STL_TRIANGLE_DTYPE = np.dtype([
    ("normal", "<f4", (3,)),
    ("vertices", "<f4", (3, 3)),
    ("attr", "<u2"),
])


def _write_stl_triangles(f, tri_vertices: np.ndarray) -> None:
    """Write an (M, 3, 3) float array as binary STL records."""
    if len(tri_vertices) == 0:
        return
    v0 = tri_vertices[:, 0, :].astype(np.float64)
    v1 = tri_vertices[:, 1, :].astype(np.float64)
    v2 = tri_vertices[:, 2, :].astype(np.float64)
    nrm = np.cross(v1 - v0, v2 - v0)
    length = np.linalg.norm(nrm, axis=1)
    length[length < 1e-12] = 1.0
    nrm /= length[:, None]
    rec = np.empty(len(tri_vertices), dtype=STL_TRIANGLE_DTYPE)
    rec["normal"] = nrm.astype(np.float32)
    rec["vertices"] = tri_vertices.astype(np.float32)
    rec["attr"] = 0
    rec.tofile(f)


def _quat_to_matrix(q: Sequence[float]) -> np.ndarray:
    x, y, z, w = (float(v) for v in q)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return np.array([
        [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy)],
        [2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx)],
        [2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)],
    ], dtype=np.float64)


def export_stl_full(path: Path, base_meshes: Sequence[Mesh], ssl_name: str,
                    level_store: FileStore, shared_store: FileStore,
                    extent_x: float, extent_z: float,
                    args: argparse.Namespace, verbose: bool = False) -> dict:
    """Bake terrain, water and all static objects into a single binary STL."""
    if sr_mesh is None or sr_objects is None:
        raise RuntimeError("Не найдены модули sr_mesh/sr_objects")

    ensure_parent(path)
    mesh_names = set(shared_store.names())
    groups = sr_objects.parse_object_groups(level_store.read(ssl_name), mesh_names,
                                            max(extent_x, extent_z))
    if args.object_limit:
        groups = groups[: args.object_limit]

    prototypes: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    object_triangles = 0
    skipped: list[str] = []
    for group in groups:
        if group.mesh_name in prototypes:
            object_triangles += len(group.instances) * len(prototypes[group.mesh_name][1])
            continue
        try:
            parsed = sr_mesh.parse_combinexmesh(shared_store.read(group.mesh_name))
            parts = sr_mesh.extract_visual_parts(parsed)
            pos_list = []
            idx_list = []
            offset = 0
            for part in parts:
                pos_list.append(part["positions"])
                idx_list.append(part["indices"].astype(np.int64) + offset)
                offset += len(part["positions"])
            if not pos_list:
                raise ValueError("no visual geometry")
            positions = np.concatenate(pos_list).astype(np.float64)
            indices = np.concatenate(idx_list)
            prototypes[group.mesh_name] = (positions, indices)
            object_triangles += len(group.instances) * len(indices)
            if verbose:
                print(t("prototype_added", mesh=group.mesh_name, tris=len(indices)))
        except Exception as exc:  # noqa: BLE001
            skipped.append(f"{group.mesh_name}: {exc}")
            if verbose:
                print(t("prototype_skipped", mesh=group.mesh_name, error=exc))

    base_triangles = sum(m.triangle_count for m in base_meshes)
    total_triangles = base_triangles + object_triangles
    print(t("stl_bake_start", triangles=total_triangles))

    written = 0
    with path.open("wb") as f:
        f.write(f"SnowRunner Map Converter {VERSION}".encode("ascii")[:80].ljust(80, b"\0"))
        f.write(struct.pack("<I", 0))  # placeholder, fixed at the end

        for mesh in base_meshes:
            tri = mesh.positions.astype(np.float64)[mesh.indices.astype(np.int64)]
            _write_stl_triangles(f, tri)
            written += len(tri)

        progress = None
        if sr_tui is not None:
            total = sum(len(g.instances) for g in groups)
            if total:
                progress = sr_tui.Progress(t("tui_instances"), total)
        for group in groups:
            proto = prototypes.get(group.mesh_name)
            if proto is None:
                if progress is not None:
                    progress.advance(len(group.instances))
                continue
            positions, indices = proto
            chunk: list[np.ndarray] = []
            for inst in group.instances:
                tr, q, sc = sr_objects.instance_matrix(inst)
                rot = _quat_to_matrix(q)
                verts = (positions * float(sc[0])) @ rot.T + tr
                chunk.append(verts[indices])
                if len(chunk) >= 400:
                    tri = np.concatenate(chunk, axis=0)
                    _write_stl_triangles(f, tri)
                    written += len(tri)
                    chunk.clear()
            if chunk:
                tri = np.concatenate(chunk, axis=0)
                _write_stl_triangles(f, tri)
                written += len(tri)
            if progress is not None:
                progress.advance(len(group.instances))
        if progress is not None:
            progress.close()
        f.seek(80)
        f.write(struct.pack("<I", written))

    if verbose:
        print(t("stl_written", triangles=written))
    return {
        "groups": len(groups),
        "instances": sum(len(g.instances) for g in groups),
        "prototypes": len(prototypes),
        "triangles": written,
        "skipped_groups": skipped,
    }


def _jsonable(obj):
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, Path):
        return str(obj)
    return obj


def _build_gltf(meshes: Sequence[Mesh], metadata: dict, buffer_uri: str | None,
                binary_blob: bytearray) -> dict:
    buffer_views: list[dict] = []
    accessors: list[dict] = []
    gltf_meshes: list[dict] = []
    nodes: list[dict] = []
    materials: list[dict] = []

    material_index: dict[str, int] = {}
    for mesh in meshes:
        if mesh.material not in material_index:
            material_index[mesh.material] = len(materials)
            base = {
                "name": mesh.material,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [mesh.color[0], mesh.color[1], mesh.color[2], mesh.alpha],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.95 if mesh.material == "terrain" else 0.55,
                },
                "doubleSided": mesh.material in ("water", "terrain"),
            }
            if mesh.alpha < 1.0:
                base["alphaMode"] = "BLEND"
            materials.append(base)

    def add_view(data: np.ndarray | bytes, target: int | None = None) -> int:
        while len(binary_blob) % 4:
            binary_blob.append(0)
        offset = len(binary_blob)
        raw = data.tobytes() if isinstance(data, np.ndarray) else data
        binary_blob.extend(raw)
        view = {"buffer": 0, "byteOffset": offset, "byteLength": len(raw)}
        if target is not None:
            view["target"] = target
        buffer_views.append(view)
        return len(buffer_views) - 1

    def add_accessor(view: int, component_type: int, count: int, typ: str,
                     minmax: bool = False, data: np.ndarray | None = None) -> int:
        acc = {
            "bufferView": view,
            "componentType": component_type,
            "count": count,
            "type": typ,
        }
        if minmax and data is not None and len(data):
            mins = data.min(axis=0).astype(float).tolist()
            maxs = data.max(axis=0).astype(float).tolist()
            acc["min"] = mins
            acc["max"] = maxs
        accessors.append(acc)
        return len(accessors) - 1

    for mesh in meshes:
        pos = np.ascontiguousarray(mesh.positions, dtype="<f4")
        view_pos = add_view(pos, 34962)
        acc_pos = add_accessor(view_pos, 5126, len(pos), "VEC3", True, pos)

        attributes = {"POSITION": acc_pos}
        if mesh.normals is not None:
            nrm = np.ascontiguousarray(mesh.normals, dtype="<f4")
            view_n = add_view(nrm, 34962)
            attributes["NORMAL"] = add_accessor(view_n, 5126, len(nrm), "VEC3")
        if mesh.uvs is not None:
            uv = np.ascontiguousarray(mesh.uvs, dtype="<f4")
            view_uv = add_view(uv, 34962)
            attributes["TEXCOORD_0"] = add_accessor(view_uv, 5126, len(uv), "VEC2")

        idx = np.ascontiguousarray(mesh.indices.reshape(-1), dtype="<u4")
        view_idx = add_view(idx, 34963)
        acc_idx = add_accessor(view_idx, 5125, len(idx), "SCALAR")

        gltf_meshes.append({
            "name": mesh.name,
            "primitives": [{
                "attributes": attributes,
                "indices": acc_idx,
                "material": material_index[mesh.material],
                "mode": 4,
            }],
        })
        nodes.append({"name": mesh.name, "mesh": len(gltf_meshes) - 1})

    gltf = {
        "asset": {
            "version": "2.0",
            "generator": f"SnowRunner Map Converter {VERSION}",
            "extras": _jsonable(metadata),
        },
        "scene": 0,
        "scenes": [{"name": "SnowRunner map", "nodes": list(range(len(nodes)))}],
        "nodes": nodes,
        "meshes": gltf_meshes,
        "materials": materials,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{
            "byteLength": len(binary_blob),
            **({"uri": buffer_uri} if buffer_uri else {}),
        }],
    }
    return gltf


def export_glb(path: Path, meshes: Sequence[Mesh], metadata: dict) -> None:
    ensure_parent(path)
    blob = bytearray()
    gltf = _build_gltf(meshes, metadata, None, blob)
    while len(blob) % 4:
        blob.append(0)
    gltf["buffers"][0]["byteLength"] = len(blob)

    json_bytes = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4:
        json_bytes += b" "

    total = 12 + 8 + len(json_bytes) + 8 + len(blob)
    with path.open("wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<I4s", len(json_bytes), b"JSON"))
        f.write(json_bytes)
        f.write(struct.pack("<I4s", len(blob), b"BIN\x00"))
        f.write(blob)


def export_gltf(path: Path, meshes: Sequence[Mesh], metadata: dict) -> None:
    ensure_parent(path)
    bin_path = path.with_suffix(".bin")
    blob = bytearray()
    gltf = _build_gltf(meshes, metadata, bin_path.name, blob)
    while len(blob) % 4:
        blob.append(0)
    gltf["buffers"][0]["byteLength"] = len(blob)
    bin_path.write_bytes(blob)
    path.write_text(json.dumps(gltf, ensure_ascii=False, indent=2), encoding="utf-8")


def export_heightmap_png(path: Path, height: np.ndarray, preview: bool = True,
                         water: np.ndarray | None = None) -> None:
    ensure_parent(path)
    write_png(path, height, bit_depth=16, color_type=0)
    if preview:
        p = path.with_name(path.stem + "_preview" + path.suffix)
        write_png(p, colorize_heightmap(height, water), bit_depth=8, color_type=2)


def export_raw_heightmap(path: Path, height: np.ndarray, metadata: dict) -> None:
    ensure_parent(path)
    path.write_bytes(height.astype("<u2").tobytes())
    meta_path = path.with_suffix(path.suffix + ".json")
    meta_path.write_text(json.dumps(_jsonable(metadata), ensure_ascii=False, indent=2), encoding="utf-8")


def export_objects(path: Path, objects: Sequence[dict]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(_jsonable(list(objects)), ensure_ascii=False, indent=2), encoding="utf-8")
    csv_path = path.with_suffix(".csv")
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["name", "x", "y", "z", "param4", "param5", "param6", "param7", "param8"])
        for o in objects:
            writer.writerow([o["name"], o["x"], o["y"], o["z"], *o.get("params", [])])


# ---------------------------------------------------------------------------
# Texture export
# ---------------------------------------------------------------------------

def write_dds(path: Path, pct: PctTexture) -> bool:
    """Wrap the raw .pct payload in a DDS/DX10 container."""
    ensure_parent(path)
    bpp = FORMAT_BYTES.get(pct.format_id)
    compressed = pct.format_id in (0x0C, 0x0F, 0x33, 0x34)
    width, height = pct.width, pct.height
    depth = max(1, pct.depth)
    faces = max(1, pct.faces)
    mips = max(1, pct.mip_count)

    flags = 0x00001007  # CAPS | HEIGHT | WIDTH | PIXELFORMAT
    caps = 0x1000
    caps2 = 0
    if mips > 1:
        flags |= 0x20000
        caps |= 0x400008
    if depth > 1:
        flags |= 0x800000
        caps |= 0x8
        caps2 |= 0x200000
    if faces == 6:
        caps |= 0x8
        caps2 |= 0xFE00

    if compressed:
        flags |= 0x80000
        pitch = pct.top_mip_size
    else:
        flags |= 0x8
        pitch = width * max(1, bpp or 4)

    dds_header = struct.pack(
        "<4sIIIIIII11I",
        b"DDS ", 124, flags, height, width, pitch, depth, mips,
        *([0] * 11),
    )
    pixel_format = struct.pack("<II4sIIIII", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
    caps_data = struct.pack("<IIIII", caps, caps2, 0, 0, 0)
    dx10 = struct.pack("<IIIII", DXGI_FORMATS.get(pct.format_id, 98),
                       4 if depth > 1 else 3,
                       0x4 if faces == 6 else 0,
                       faces, 0)
    payload = bytes(pct.payload)
    path.write_bytes(dds_header + pixel_format + caps_data + dx10 + payload)
    return True


def export_textures(store: FileStore, out_dir: Path, height_name: str | None,
                    verbose: bool = False) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    exported: list[dict] = []
    names = [n for n in store.names() if n.lower().endswith(".pct")
             and not n.lower().endswith(".pct_header")]
    progress = None
    if sr_tui is not None and names:
        progress = sr_tui.Progress(t("tui_textures"), len(names))
    for idx, name in enumerate(names, 1):
        if progress is not None:
            progress.update(idx - 1)
        try:
            pct = parse_pct(name, store.read(name))
            stem = Path(name).stem
            info = {
                "name": name, "width": pct.width, "height": pct.height,
                "format": pct.format_name, "mips": pct.mip_count,
            }
            if pct.format_id in (0x03, 0x37):
                arr = pct.array()
                p = out_dir / f"{stem}.png"
                write_png(p, arr, bit_depth=16 if pct.format_id == 0x37 else 8,
                          color_type=0)
                info["file"] = str(p)
            elif pct.format_id == 0x2A:
                arr = pct.array().astype("<f4")
                raw_path = out_dir / f"{stem}.f32"
                raw_path.write_bytes(arr.tobytes())
                info["file"] = str(raw_path)
                valid = np.isfinite(arr) & (arr > NODATA_FLOAT)
                if valid.any():
                    lo, hi = np.quantile(arr[valid], [0.01, 0.99])
                    if hi > lo:
                        prev = np.clip((arr - lo) / (hi - lo), 0, 1)
                        prev[~valid] = 0
                        p = out_dir / f"{stem}_preview.png"
                        write_png(p, (prev * 255).astype(np.uint8), 8, 0)
                        info["preview"] = str(p)
            elif pct.format_id in (0x00, 0x16):
                arr = pct.array().reshape(pct.height, pct.width, 4)
                p = out_dir / f"{stem}.png"
                write_png(p, arr[..., [2, 1, 0]], bit_depth=8, color_type=2)
                info["file"] = str(p)
            else:
                p = out_dir / f"{stem}.dds"
                write_dds(p, pct)
                info["file"] = str(p)
            exported.append(info)
            if verbose:
                print(f"    {name}: {pct.width}x{pct.height} {pct.format_name} -> {info.get('file', '-')}")
        except Exception as exc:  # noqa: BLE001
            eprint(f"  ! {name}: {exc}")
        if progress is not None:
            progress.update(idx)
    if progress is not None:
        progress.close()
    return exported


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def resolve_input(path: Path) -> FileStore:
    return FileStore(path)


def pick_main_files(store: FileStore) -> tuple[str | None, str | None, str | None, str | None]:
    """Return (height_map, water_map, data_stg, data_for_ssl)."""
    pct_names = [n for n in store.names()
                 if n.lower().endswith(".pct") and not n.lower().endswith(".pct_header")]
    height = None
    water = None
    for n in pct_names:
        low = n.lower()
        if low.endswith("_water_height_map.pct"):
            water = n
        elif low.endswith("_height_map.pct"):
            height = n
    if height is None:
        # Fallback: any height map.
        for n in pct_names:
            if "height_map" in n.lower() and "water" not in n.lower():
                height = n
                break
    stg = store.find_suffix("data.stg")
    ssl = store.find_suffix("data_for_ssl.bin")
    return height, water, stg, ssl


def terrain_extent(width: int, height: int) -> tuple[float, float]:
    """SnowRunner terrain is 24 m per block and 48 cells per block."""
    bx = (width - 1) / TERRAIN_NODES_PER_BLOCK
    bz = (height - 1) / TERRAIN_NODES_PER_BLOCK
    if abs(bx - round(bx)) <= 0.02 and abs(bz - round(bz)) <= 0.02:
        return (max(1, round(bx)) * GAME_BLOCK_METERS,
                max(1, round(bz)) * GAME_BLOCK_METERS)
    # Unknown grid layout: fall back to the usual 0.5 m cell size.
    return ((width - 1) * 0.5, (height - 1) * 0.5)


def choose_format_interactive() -> str:
    print("\n" + t("format_title"))
    options = [
        ("glb", t("fmt_glb")),
        ("obj", t("fmt_obj")),
        ("ply", t("fmt_ply")),
        ("stl", t("fmt_stl")),
        ("gltf", t("fmt_gltf")),
        ("png", t("fmt_png")),
        ("raw", t("fmt_raw")),
        ("json", t("fmt_json")),
    ]
    for i, (key, desc) in enumerate(options, 1):
        print(f"  {i}) {key:<5} {desc}")
    while True:
        try:
            raw = input(t("format_prompt")).strip().lower()
        except EOFError:
            return "glb"
        if not raw:
            return "glb"
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        if raw in {k for k, _ in options}:
            return raw
        print("  " + t("invalid_choice"))


def make_output_path(input_path: Path, output: Path | None, fmt: str) -> Path:
    ext = {"glb": ".glb", "obj": ".obj", "ply": ".ply", "stl": ".stl",
           "gltf": ".gltf", "png": ".png", "raw": ".u16", "json": ".json"}[fmt]
    if output is None:
        return input_path.with_name(safe_stem(input_path) + "_map" + ext)
    if output.is_dir():
        return output / (safe_stem(input_path) + "_map" + ext)
    if output.suffix:
        return output
    return output.with_suffix(ext)


def print_archive_info(store: FileStore) -> None:
    print(t("archive", path=store.path))
    print(t("files_count", count=len(store.names())))
    for n in store.names():
        try:
            size = human_size(store.size(n))
        except Exception:  # noqa: BLE001
            size = "?"
        print(f"  {n:<70} {size:>10}")
    height, water, stg, ssl = pick_main_files(store)
    print("\n" + t("detected"))
    print(t("height_map", value=height or "—"))
    print(t("water_map", value=water or "—"))
    print(t("data_stg", value=stg or "—"))
    print(t("data_ssl", value=ssl or "—"))
    shared = find_pak(None, "shared.pak", store.path)
    editor = find_pak(None, "editor.pak", store.path)
    print(t("shared_pak", path=shared or "—"))
    print(t("editor_pak", path=editor) if editor else t("editor_pak_missing"))


def find_pak(explicit: str | None, filename: str, input_path: Path) -> Path | None:
    """Find shared.pak / editor.pak next to the level or in the current folder."""
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    candidates = [
        input_path.parent / filename,
        Path.cwd() / filename,
        input_path.parent.parent / filename,
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _object_color(material: str) -> tuple[float, float, float, float]:
    low = material.lower()
    if "bark" in low or "trunk" in low or "wood" in low:
        return (0.42, 0.30, 0.19, 1.0)
    if "atlas" in low or "leaf" in low or "leaves" in low or "grass" in low or "foliage" in low:
        return (0.32, 0.52, 0.27, 1.0)
    if "rock" in low or "stone" in low:
        return (0.55, 0.55, 0.52, 1.0)
    return (0.66, 0.64, 0.58, 1.0)


def build_full_scene(builder, ssl_name: str, level_store: FileStore,
                     shared_store: FileStore, editor_store: FileStore | None,
                     extent_x: float, extent_z: float,
                     args: argparse.Namespace, verbose: bool = False) -> dict:
    """Add all static objects from data_for_ssl.bin to a GltfBuilder."""
    if sr_mesh is None or sr_objects is None:
        raise RuntimeError("Не найдены модули sr_mesh/sr_objects")

    mesh_names = set(shared_store.names())
    ssl_data = level_store.read(ssl_name)
    groups = sr_objects.parse_object_groups(ssl_data, mesh_names, max(extent_x, extent_z))
    if args.object_limit:
        groups = groups[: args.object_limit]
    if verbose:
        print(t("object_groups", groups=len(groups),
                instances=sum(len(g.instances) for g in groups)))

    textures = None
    if editor_store is not None and not args.no_textures and sr_textures is not None:
        if getattr(sr_textures, "Image", None) is None:
            eprint(t("error_pillow"))
        else:
            textures = sr_textures.EditorTextures(editor_store, verbose=verbose)

    prototypes: dict[str, int] = {}
    skipped_groups: list[str] = []
    material_cache: dict[str, int] = {}
    total_instances = 0
    total_expected = sum(len(g.instances) for g in groups)
    progress = None
    if sr_tui is not None and total_expected > 0:
        progress = sr_tui.Progress(t("tui_instances"), total_expected)

    for group in groups:
        if group.mesh_name not in prototypes:
            try:
                parsed = sr_mesh.parse_combinexmesh(shared_store.read(group.mesh_name))
                parts_raw = sr_mesh.extract_visual_parts(parsed)
                if not parts_raw:
                    raise ValueError("no visual geometry")
                xml_materials = sr_mesh.parse_xml_materials(parsed["xml"])
                parts = [
                    MeshPart(p["positions"], p["normals"], p["uvs"],
                             p["indices"], p["material"])
                    for p in parts_raw
                ]

                def matfn(part, xml_materials=xml_materials):
                    name = part.material
                    cache_key = name
                    if cache_key in material_cache:
                        return material_cache[cache_key]
                    props = xml_materials.get(name, {})
                    tex_idx = None
                    if textures is not None:
                        ref = props.get("AlbedoMap", "")
                        if ref:
                            png = textures.albedo_png(ref)
                            if png:
                                tex_idx = builder.add_texture(png, ref)
                    alpha_kill = props.get("AlphaKill", "").lower() == "true"
                    blending = props.get("Blending", "").lower()
                    alpha_mode = "MASK" if alpha_kill else ("BLEND" if blending == "alpha" else "OPAQUE")
                    double_sided = props.get("TwoSided", "").lower() == "true"
                    color = (1.0, 1.0, 1.0, 1.0) if tex_idx is not None else _object_color(name)
                    idx = builder.material(name, texture=tex_idx, color=color,
                                           alpha_mode=alpha_mode, double_sided=double_sided)
                    material_cache[cache_key] = idx
                    return idx

                mesh_idx = builder.add_mesh(group.mesh_name, parts, matfn)
                prototypes[group.mesh_name] = mesh_idx
                if verbose:
                    print(t("prototype_added", mesh=group.mesh_name,
                            tris=sum(len(p.indices) for p in parts)))
            except Exception as exc:  # noqa: BLE001
                skipped_groups.append(f"{group.mesh_name}: {exc}")
                if verbose:
                    print(t("prototype_skipped", mesh=group.mesh_name, error=exc))
                if progress is not None:
                    progress.advance(len(group.instances))
                continue

        mesh_idx = prototypes[group.mesh_name]
        for inst in group.instances:
            tr, q, sc = sr_objects.instance_matrix(inst)
            builder.add_node(mesh_idx, tuple(tr), tuple(q), tuple(sc))
            total_instances += 1
        if progress is not None:
            progress.advance(len(group.instances))

    if progress is not None:
        progress.close()
    return {
        "groups": len(groups),
        "instances": total_instances,
        "prototypes": len(prototypes),
        "skipped_groups": skipped_groups,
        "textures": builder.stats.textures,
    }


def convert(args: argparse.Namespace) -> int:
    input_path = Path(args.input).expanduser()
    if not input_path.exists():
        eprint(t("error_input_missing", path=input_path))
        eprint(t("paks_hint_short"))
        return 2

    split_x = split_y = 1
    if args.split:
        args.print_mode = True
        raw = args.split.lower().replace(" ", "")
        try:
            sx, sy = raw.split("x")
            split_x, split_y = int(sx), int(sy)
            if split_x < 1 or split_y < 1:
                raise ValueError
        except Exception:
            eprint(t("error_split_value"))
            return 2

    fmt = args.format
    if fmt is None:
        if args.print_mode:
            fmt = "stl"
        else:
            fmt = choose_format_interactive() if sys.stdin.isatty() else "glb"
    fmt = fmt.lower()
    if fmt not in ("glb", "obj", "ply", "stl", "gltf", "png", "raw", "json"):
        eprint(t("error_format", fmt=fmt))
        return 2
    if args.split and fmt != "stl":
        eprint(t("error_split_format"))
        return 2

    store = resolve_input(input_path)
    try:
        if args.list:
            print_archive_info(store)
            return 0

        height_name, water_name, stg_name, ssl_name = pick_main_files(store)
        if height_name is None:
            eprint(t("error_no_height"))
            eprint(t("error_no_height_hint"))
            return 3

        if args.verbose:
            print_archive_info(store)

        print(t("map", name=height_name))
        t0 = time.time()
        with _spinner(t("tui_reading_height")):
            height_pct = parse_pct(height_name, store.read(height_name))
        if height_pct.format_id != 0x37:
            eprint(f"  ! height map имеет формат {height_pct.format_name}, ожидался R16_UNORM")
        height = height_pct.array()
        if height.ndim != 2:
            eprint(t("error_height_2d"))
            return 4
        H, W = height.shape
        extent_x, extent_z = terrain_extent(W, H)
        spacing = extent_x / max(1, W - 1)
        print(t("size", w=W, h=H, spacing=spacing, ex=extent_x, ez=extent_z))

        water = None
        if water_name is not None:
            try:
                with _spinner(t("tui_reading_water")):
                    water_pct = parse_pct(water_name, store.read(water_name))
                water = water_pct.array()
                if water.ndim != 2:
                    water = None
                else:
                    finite = water[np.isfinite(water)] if np.isfinite(water).any() else None
                    print(t("water", w=water.shape[1], h=water.shape[0],
                            lo=np.nanmin(finite) if finite is not None else "—",
                            hi=np.nanmax(finite) if finite is not None else "—"))
            except Exception as exc:  # noqa: BLE001
                eprint(t("error_water", error=exc))
                water = None

        # Height calibration.
        max_height_explicit = args.max_height is not None
        if max_height_explicit and args.height_scale is None:
            # A user-specified max height is a hard override, not a fallback.
            args.height_scale = float(args.max_height) / 65535.0
        if args.max_height is None and args.height_scale is None:
            args.max_height = DEFAULT_FALLBACK_MAX_HEIGHT
        if args.height_scale is not None:
            args.max_height = float(args.height_scale) * 65535.0
        if args.max_height is None:
            args.max_height = DEFAULT_FALLBACK_MAX_HEIGHT

        with _spinner(t("tui_calibrating")):
            calib = detect_height_scale(height, store, stg_name, ssl_name,
                                        extent_x, extent_z, args)
        hscale = float(calib["scale"])
        hoffset = float(calib.get("offset", 0.0))
        print(t("height_scale", scale=hscale, maxh=calib["max_height"]))
        print(t("calibration", source=calib["source"]))
        if args.verbose and calib.get("inliers") is not None:
            print(t("inliers", inliers=calib["inliers"], total=calib["total"], ratio=calib["ratio"]*100, mode=calib["mode"]))

        # Objects (optional but useful for metadata).
        objects: list[dict] = []
        if stg_name is not None and (args.objects or args.markers or fmt == "json"):
            try:
                offsets = read_stg_header(store, stg_name)
                objects = extract_objects(store, stg_name, stg_object_end(offsets),
                                          max(extent_x, extent_z))
                print(t("objects_actors", count=len(objects)))
            except Exception as exc:  # noqa: BLE001
                eprint(t("error_objects", error=exc))

        out_path = make_output_path(input_path, Path(args.output).expanduser() if args.output else None, fmt)
        ensure_parent(out_path)

        metadata = {
            "generator": f"SnowRunner Map Converter {VERSION}",
            "source": str(input_path),
            "map": height_name.replace("_height_map.pct", ""),
            "terrain": {
                "width": W, "height": H,
                "spacing_m": spacing,
                "extent_m": [extent_x, extent_z],
                "height_scale_m_per_unit": hscale,
                "height_offset_m": hoffset,
                "max_height_m": calib["max_height"],
                "height_source": calib["source"],
                "height_inliers": calib.get("inliers"),
                "height_total": calib.get("total"),
            },
            "water": None if water is None else {
                "width": int(water.shape[1]), "height": int(water.shape[0]),
            },
            "objects_count": len(objects),
            "options": {
                "step": args.step,
                "water": not args.no_water,
                "markers": args.markers,
            },
            "format": fmt,
        }

        if args.print_mode:
            if fmt != "stl":
                eprint(t("error_print_format"))
                return 2
            if sr_print is None:
                eprint("  ! sr_print module is unavailable.")
                return 5
            shared_path = find_pak(args.shared_pak, "shared.pak", input_path)
            include_objects = not args.print_no_objects
            shared_store = None
            groups = []
            if include_objects:
                if shared_path is None or ssl_name is None or sr_objects is None:
                    eprint(t("error_full_unavailable"))
                else:
                    shared_store = FileStore(shared_path)
                    try:
                        groups = sr_objects.parse_object_groups(
                            store.read(ssl_name), set(shared_store.names()),
                            max(extent_x, extent_z))
                        if args.object_limit:
                            groups = groups[: args.object_limit]
                    except Exception as exc:  # noqa: BLE001
                        eprint(t("error_objects", error=exc))
                        groups = []

            print(t("print_start", width=args.print_width,
                    base=args.base_thickness, step=args.step))
            simplify = (args.print_quality in ("balanced", "draft")
                        and not args.print_full_objects)
            if args.print_no_objects:
                objects_mode = t("print_objects_none")
            elif simplify:
                objects_mode = t("print_objects_proxy")
            else:
                objects_mode = t("print_objects_full")
            print(t("print_objects_mode", mode=objects_mode))
            if args.split:
                if sr_split is None:
                    eprint("  ! sr_split module is unavailable.")
                    return 5
                print(t("print_split_start", nx=split_x, ny=split_y,
                        count=split_x * split_y, gap=args.split_gap))
                try:
                    manifest = sr_split.write_split_stl(
                        out_path, height, extent_x, extent_z, args.step,
                        hscale, hoffset,
                        print_width_mm=args.print_width,
                        base_thickness_mm=args.base_thickness,
                        split_x=split_x, split_y=split_y,
                        gap_mm=args.split_gap,
                        simplify_vegetation=simplify,
                        objects=groups,
                        shared_store=shared_store,
                        ssl_name=ssl_name,
                        verbose=args.verbose,
                    )
                finally:
                    if shared_store is not None:
                        shared_store.close()
                total = sum(int(ti["triangles"]) for ti in manifest["tiles"])
                print(t("print_split_done", count=len(manifest["tiles"]),
                        manifest=str(out_path.with_name(out_path.stem + "_tiles.json"))))
                print(t("stl_written", triangles=total))
                if args.preview:
                    prev_path = out_path.with_name(out_path.stem + "_preview.png")
                    write_png(prev_path, colorize_heightmap(height, water),
                              bit_depth=8, color_type=2)
                    print(t("preview_saved", path=prev_path))
                maybe_open(args, out_path)
                return 0

            try:
                result = sr_print.write_print_stl(
                    out_path, height, extent_x, extent_z, args.step,
                    hscale, hoffset,
                    print_width_mm=args.print_width,
                    base_thickness_mm=args.base_thickness,
                    simplify_vegetation=simplify,
                    objects=groups,
                    shared_store=shared_store,
                    level_store=store,
                    ssl_name=ssl_name,
                    verbose=args.verbose,
                )
            finally:
                if shared_store is not None:
                    shared_store.close()

            print(t("print_scale", scale=result["scale_mm_per_m"],
                    w=extent_x * result["scale_mm_per_m"],
                    d=extent_z * result["scale_mm_per_m"]))
            print(t("print_height", h=result["total_height_mm"]))
            print(t("print_done", path=out_path))
            print(t("stl_written", triangles=result["triangles"]))
            if args.preview:
                prev_path = out_path.with_name(out_path.stem + "_preview.png")
                write_png(prev_path, colorize_heightmap(height, water),
                          bit_depth=8, color_type=2)
                print(t("preview_saved", path=prev_path))
            maybe_open(args, out_path)
            return 0

        if fmt in ("png", "raw"):
            if fmt == "png":
                with _spinner(t("tui_export", fmt="PNG")):
                    export_heightmap_png(out_path, height, preview=not args.no_preview, water=water)
                print(t("done_simple", path=out_path))
                if not args.no_preview:
                    print(f"       {out_path.with_name(out_path.stem + '_preview' + out_path.suffix)}")
            else:
                export_raw_heightmap(out_path, height, metadata)
                print(t("done_raw", path=out_path))
            if args.textures:
                tex_dir = out_path.parent / (out_path.stem + "_textures")
                print(t("textures_export", path=tex_dir))
                infos = export_textures(store, tex_dir, height_name, args.verbose)
                metadata["textures"] = infos
                print(t("textures_exported", count=len(infos)))
            if args.objects and objects:
                op = out_path.with_name(out_path.stem + "_objects.json")
                export_objects(op, objects)
                print(t("objects_saved", path=op))
            maybe_open(args, out_path)
            return 0

        if fmt == "json":
            meta_path = out_path.with_suffix(".json") if out_path.suffix != ".json" else out_path
            data = dict(metadata)
            data["objects"] = objects
            meta_path.write_text(json.dumps(_jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")
            print(t("done_simple", path=meta_path))
            maybe_open(args, meta_path)
            return 0

        # Build meshes.
        print(t("building_mesh", step=args.step))
        with _spinner(t("tui_terrain")):
            terrain = build_terrain_mesh(height, extent_x, extent_z, hscale, hoffset,
                                         args.step, "terrain")
        meshes: list[Mesh] = [terrain]
        print(t("terrain_stats", v=terrain.vertex_count, t=terrain.triangle_count))

        if water is not None and not args.no_water:
            water_step = args.water_step
            if water_step is None:
                # The water map is ~6 m per texel. Do not make it finer than
                # the terrain mesh unless the user explicitly asks for it.
                water_step = max(1, int(round(args.step * 0.5 / 6.0)))
            with _spinner(t("tui_water_mesh")):
                water_mesh = build_water_mesh(water, extent_x, extent_z,
                                              max(1, water_step), args.water_offset, "water")
            if water_mesh is not None:
                meshes.append(water_mesh)
                print(t("water_stats", v=water_mesh.vertex_count, t=water_mesh.triangle_count))

        if args.markers and objects:
            markers = build_marker_mesh(objects, args.marker_size, "objects")
            if markers is not None:
                meshes.append(markers)
                print(t("markers_stats", n=len(objects), t=markers.triangle_count))

        metadata["mesh"] = {
            "vertices": sum(m.vertex_count for m in meshes),
            "triangles": sum(m.triangle_count for m in meshes),
        }

        # Full scene with static objects from shared.pak / editor.pak.
        shared_path = find_pak(args.shared_pak, "shared.pak", input_path)
        editor_path = find_pak(args.editor_pak, "editor.pak", input_path)
        full_mode = args.full
        if full_mode is None:
            full_mode = (shared_path is not None and ssl_name is not None)
        if full_mode and (shared_path is None or ssl_name is None or sr_mesh is None):
            eprint(t("error_full_unavailable"))
            eprint(t("paks_hint_short"))
            full_mode = False
        if full_mode and fmt not in ("glb", "gltf", "stl"):
            eprint(t("error_full_format"))
            eprint(t("error_full_format2"))
            full_mode = False

        if full_mode:
            shared_store = FileStore(shared_path)
            if fmt == "stl":
                try:
                    print(t("shared_pak", path=shared_path))
                    base_meshes = [m for m in meshes if m.name != "objects"]
                    stats = export_stl_full(out_path, base_meshes, ssl_name,
                                            store, shared_store,
                                            extent_x, extent_z, args, args.verbose)
                    metadata["full_scene"] = stats
                    metadata["objects_count"] = stats["instances"]
                    print(t("objects_placed", count=stats["instances"]))
                    print(t("unique_meshes", count=stats["prototypes"]))
                    print(t("stl_stats", t=stats["triangles"]))
                    if args.preview:
                        prev_path = out_path.with_name(out_path.stem + "_preview.png")
                        write_png(prev_path, colorize_heightmap(height, water),
                                  bit_depth=8, color_type=2)
                        print(t("preview_saved", path=prev_path))
                    elapsed = time.time() - t0
                    print(t("done", seconds=elapsed,
                            size=human_size(out_path.stat().st_size)))
                    maybe_open(args, out_path)
                    return 0
                finally:
                    shared_store.close()

            if GltfBuilder is None or MeshPart is None:
                shared_store.close()
                eprint(t("error_sr_gltf"))
                return 5
            editor_store = FileStore(editor_path) if editor_path is not None else None
            try:
                print(t("shared_pak", path=shared_path))
                print(t("editor_pak", path=editor_path) if editor_path else t("editor_pak_missing"))
                print(t("full_start"))
                builder = GltfBuilder(metadata)
                part_terrain = MeshPart(terrain.positions, terrain.normals,
                                        terrain.uvs, terrain.indices, "terrain")
                mi_terrain = builder.add_mesh("terrain", [part_terrain],
                                              lambda p: builder.material(
                                                  "terrain", color=(0.58, 0.63, 0.48, 1.0),
                                                  double_sided=False))
                builder.add_node(mi_terrain, name="terrain")

                if water is not None and not args.no_water:
                    water_step = args.water_step
                    if water_step is None:
                        water_step = max(1, int(round(args.step * 0.5 / 6.0)))
                    water_mesh = build_water_mesh(water, extent_x, extent_z,
                                                  max(1, water_step), args.water_offset, "water")
                    if water_mesh is not None:
                        part_water = MeshPart(water_mesh.positions, water_mesh.normals,
                                              water_mesh.uvs, water_mesh.indices, "water")
                        mi_water = builder.add_mesh("water", [part_water],
                                                    lambda p: builder.material(
                                                        "water", color=(0.16, 0.38, 0.66, 0.55),
                                                        alpha_mode="BLEND", double_sided=True))
                        builder.add_node(mi_water, name="water")

                full_stats = build_full_scene(builder, ssl_name, store,
                                              shared_store, editor_store,
                                              extent_x, extent_z, args, args.verbose)
                metadata["full_scene"] = full_stats
                metadata["objects_count"] = full_stats["instances"]
                print(t("objects_placed", count=full_stats["instances"]))
                print(t("unique_meshes", count=full_stats["prototypes"]))
                print(t("textures_count", count=full_stats["textures"]))
                if full_stats["skipped_groups"]:
                    print(t("skipped_groups", count=len(full_stats["skipped_groups"])))
                print(t("verts_tris", v=builder.stats.vertices, t=builder.stats.triangles))

                print(t("export_to", fmt=fmt.upper(), path=out_path))
                with _spinner(t("tui_export", fmt=fmt.upper())):
                    if fmt == "glb":
                        builder.export_glb(out_path)
                    else:
                        builder.export_gltf(out_path)
                    print(f"       {out_path.with_suffix('.bin')}")

                if args.preview:
                    prev_path = out_path.with_name(out_path.stem + "_preview.png")
                    write_png(prev_path, colorize_heightmap(height, water),
                              bit_depth=8, color_type=2)
                    print(t("preview_saved", path=prev_path))

                elapsed = time.time() - t0
                print(t("done", seconds=elapsed, size=human_size(out_path.stat().st_size)))
                maybe_open(args, out_path)
                return 0
            finally:
                if editor_store is not None:
                    editor_store.close()
                shared_store.close()

        print(t("export_to", fmt=fmt.upper(), path=out_path))
        if fmt == "glb":
            with _spinner(t("tui_export", fmt="GLB")):
                export_glb(out_path, meshes, metadata)
        elif fmt == "gltf":
            with _spinner(t("tui_export", fmt="glTF")):
                export_gltf(out_path, meshes, metadata)
            print(f"       {out_path.with_suffix('.bin')}")
        elif fmt == "obj":
            with _spinner(t("tui_export", fmt="OBJ")):
                export_obj(out_path, meshes, write_normals=args.obj_normals,
                           write_uvs=not args.no_uv)
            print(f"       {out_path.with_suffix('.mtl')}")
        elif fmt == "ply":
            with _spinner(t("tui_export", fmt="PLY")):
                export_ply(out_path, meshes, include_colors=not args.no_colors)
        elif fmt == "stl":
            with _spinner(t("tui_export", fmt="STL")):
                export_stl(out_path, meshes)
        else:
            eprint(t("format_not_implemented", fmt=fmt))
            return 2

        if args.preview and fmt in ("glb", "gltf", "obj", "ply", "stl"):
            prev_path = out_path.with_name(out_path.stem + "_preview.png")
            write_png(prev_path, colorize_heightmap(height, water),
                      bit_depth=8, color_type=2)
            print(t("preview_saved", path=prev_path))

        if args.objects and objects:
            op = out_path.with_name(out_path.stem + "_objects.json")
            export_objects(op, objects)
            print(t("objects_saved", path=op))

        if args.textures:
            tex_dir = out_path.parent / (out_path.stem + "_textures")
            print(t("textures_export", path=tex_dir))
            infos = export_textures(store, tex_dir, height_name, args.verbose)
            metadata["textures"] = infos
            print(t("textures_exported", count=len(infos)))

        elapsed = time.time() - t0
        print(t("done", seconds=elapsed, size=human_size(out_path.stat().st_size)))
        maybe_open(args, out_path)
        return 0
    finally:
        store.close()


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=t("help_description"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples / Примеры:\n"
            "  python3 snowrunner_map_converter.py level_us_01_01.pak\n"
            "  python3 snowrunner_map_converter.py level_us_01_01.pak -f obj --step 4\n"
            "  python3 snowrunner_map_converter.py level_us_01_01.pak -f glb --full -o map.glb\n"
            "  python3 snowrunner_map_converter.py unpacked/ -f png --textures\n"
        ),
    )
    p.add_argument("input", nargs="?", help=t("help_input"))
    p.add_argument("output", nargs="?", help=t("help_output"))
    p.add_argument("-o", "--output-opt", dest="output_opt", metavar="PATH",
                   help=t("help_output_opt"))
    p.add_argument("-f", "--format", choices=["glb", "gltf", "obj", "ply", "stl", "png", "raw", "json"],
                   help=t("help_format"))
    p.add_argument("--list", action="store_true", help=t("list_option"))
    p.add_argument("--step", type=int, default=None,
                   help=t("help_step"))
    p.add_argument("--water-step", type=int, default=None,
                   help=t("help_water_step"))
    p.add_argument("--water-offset", type=float, default=0.05,
                   help=t("help_water_offset"))
    p.add_argument("--no-water", action="store_true", help=t("help_no_water"))
    p.add_argument("--max-height", type=float, default=None,
                   help=t("help_max_height"))
    p.add_argument("--height-scale", type=float, default=None,
                   help=t("help_height_scale"))
    p.add_argument("--markers", action="store_true", help=t("help_markers"))
    p.add_argument("--marker-size", type=float, default=2.0, help=t("help_marker_size"))
    p.add_argument("--objects", action="store_true", help=t("help_objects"))
    p.add_argument("--obj-normals", action="store_true", help=t("help_obj_normals"))
    p.add_argument("--no-uv", action="store_true", help=t("help_no_uv"))
    p.add_argument("--no-colors", action="store_true", help=t("help_no_colors"))
    p.add_argument("--textures", action="store_true", help=t("help_textures"))
    p.add_argument("--full", dest="full", action="store_true", default=None,
                   help=t("help_full"))
    p.add_argument("--terrain-only", dest="full", action="store_false",
                   help=t("help_terrain_only"))
    p.add_argument("--shared-pak", metavar="PATH",
                   help=t("help_shared_pak"))
    p.add_argument("--editor-pak", metavar="PATH",
                   help=t("help_editor_pak"))
    p.add_argument("--no-textures", action="store_true",
                   help=t("help_no_textures"))
    p.add_argument("--object-limit", type=int, default=None,
                   help=t("help_object_limit"))
    p.add_argument("--print", dest="print_mode", action="store_true",
                   help=t("help_print"))
    p.add_argument("--print-width", type=float, default=200.0,
                   help=t("help_print_width"))
    p.add_argument("--base-thickness", type=float, default=3.0,
                   help=t("help_base_thickness"))
    p.add_argument("--print-no-objects", action="store_true",
                   help=t("help_print_no_objects"))
    p.add_argument("--print-quality", choices=["draft", "balanced", "high", "max"],
                   default="balanced", help=t("help_print_quality"))
    p.add_argument("--print-full-objects", action="store_true",
                   help=t("help_print_full_objects"))
    p.add_argument("--split", metavar="NxM",
                   help=t("help_split"))
    p.add_argument("--split-gap", type=float, default=0.2,
                   help=t("help_split_gap"))
    p.add_argument("--preview", action="store_true", help=t("help_preview"))
    p.add_argument("--no-preview", action="store_true", help=t("help_no_preview"))
    p.add_argument("-v", "--verbose", action="store_true", help=t("help_verbose"))
    p.add_argument("--check", action="store_true", help=t("help_check"))
    p.add_argument("--no-tui", action="store_true", help=t("help_no_tui"))
    p.add_argument("--where-paks", action="store_true", help=t("help_where_paks"))
    p.add_argument("--open", dest="open_output", action="store_true",
                   help=t("help_open"))
    p.add_argument("--lang", choices=["ru", "en"], help=t("help_lang"))
    p.add_argument("--reset-config", action="store_true", help=t("help_reset_config"))
    p.add_argument("--version", action="version", version=f"SnowRunner Map Converter {VERSION}")
    return p


def _spinner(text: str):
    return sr_tui.Spinner(text) if sr_tui is not None else nullcontext()


def open_in_file_manager(path: Path) -> None:
    try:
        target = path.parent if path.is_file() else path
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        elif sys.platform.startswith("win"):
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception:
        pass


def maybe_open(args: argparse.Namespace, path: Path) -> None:
    if getattr(args, "open_output", False):
        open_in_file_manager(path)


def print_pak_help() -> None:
    print(t("paks_title"))
    print(t("paks_needed"))
    print(t("paks_level"))
    print(t("paks_shared"))
    print(t("paks_editor"))
    print(t("paks_where"))
    print(t("paks_paths"))
    print(t("paks_note"))


def run_check(args: argparse.Namespace) -> int:
    import platform

    print(t("check_title"))
    print(t("check_python", version=platform.python_version()))
    try:
        print(t("check_numpy", version=np.__version__))
    except Exception:
        print(t("check_numpy", version=t("check_missing")))
    try:
        from PIL import __version__ as pil_version  # type: ignore
        print(t("check_pillow", version=pil_version))
    except Exception:
        print(t("check_pillow", version=t("check_missing")))

    modules = []
    for name, mod in [("sr_mesh", sr_mesh), ("sr_objects", sr_objects),
                      ("sr_textures", sr_textures), ("sr_gltf", GltfBuilder),
                      ("sr_print", sr_print), ("sr_split", sr_split)]:
        modules.append(f"{name}={'OK' if mod is not None else 'MISSING'}")
    print(t("check_modules", status=", ".join(modules)))
    if sr_i18n is not None:
        print(t("check_config", path=sr_i18n.CONFIG_PATH,
                lang=sr_i18n.LANG))

    if args.input:
        p = Path(args.input).expanduser()
        print(t("check_input", path=p))
        if p.exists():
            try:
                store = FileStore(p)
                h, w, stg, ssl = pick_main_files(store)
                print(t("height_map", value=h or "—"))
                print(t("water_map", value=w or "—"))
                print(t("data_stg", value=stg or "—"))
                print(t("data_ssl", value=ssl or "—"))
                shared = find_pak(args.shared_pak, "shared.pak", p)
                editor = find_pak(args.editor_pak, "editor.pak", p)
                print(t("shared_pak", path=shared or "—"))
                print(t("editor_pak", path=editor) if editor else t("editor_pak_missing"))
                store.close()
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {exc}")
    else:
        paks = sorted(Path.cwd().glob("*.pak"))
        if paks:
            print(t("check_paks", status=", ".join(p.name for p in paks)))
        else:
            print(t("check_paks", status=t("check_none")))
    print(t("check_hint"))
    print(t("paks_hint_short"))
    print(t("check_done"))
    return 0


def interactive_setup(args: argparse.Namespace) -> None:
    if not args.input:
        try:
            args.input = input(t("input_prompt")).strip()
        except EOFError:
            args.input = ""
    if not args.format:
        args.format = choose_format_interactive()
    if str(args.format).lower() == "stl":
        try:
            raw = input(t("wizard_print_ask")).strip().lower()
        except EOFError:
            raw = "y"
        if raw in ("", "y", "yes", "д", "да"):
            args.print_mode = True
            try:
                q = input(t("wizard_quality")).strip().lower()
            except EOFError:
                q = ""
            qmap = {"1": "balanced", "2": "high", "3": "max", "4": "draft",
                    "balanced": "balanced", "high": "high",
                    "max": "max", "draft": "draft"}
            args.print_quality = qmap.get(q, "balanced")
            try:
                sp = input(t("wizard_split")).strip().lower()
            except EOFError:
                sp = ""
            if sp in ("2", "2x2"):
                args.split = "2x2"
            elif sp in ("3", "3x3"):
                args.split = "3x3"
            try:
                ob = input(t("wizard_objects")).strip().lower()
            except EOFError:
                ob = "y"
            args.print_no_objects = ob in ("n", "no", "н", "нет")
    if not args.output:
        try:
            out = input(t("wizard_output")).strip()
        except EOFError:
            out = ""
        if out:
            args.output = out


def main(argv: Sequence[str] | None = None) -> int:
    # Language must be known before the full parser builds localized help.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", choices=["ru", "en"])
    pre.add_argument("--reset-config", action="store_true")
    pre_args, _ = pre.parse_known_args(argv)
    if sr_i18n is not None:
        sr_i18n.init_language(pre_args.lang, pre_args.reset_config)

    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.output_opt:
        args.output = args.output_opt
    if sr_tui is not None:
        sr_tui.configure(False if args.no_tui else None)
    if args.where_paks:
        print_pak_help()
        return 0
    if args.check:
        return run_check(args)
    if not args.input:
        if sys.stdin.isatty():
            interactive_setup(args)
        if not args.input:
            parser.error(t("error_no_input"))
    elif sys.stdin.isatty() and not args.format and not args.print_mode:
        interactive_setup(args)
    if args.split:
        args.print_mode = True
    if args.step is None:
        if args.print_mode:
            args.step = {"draft": 8, "balanced": 4, "high": 2, "max": 1}[args.print_quality]
        else:
            args.step = 4
    if args.step < 1:
        parser.error(t("error_step"))
    if args.water_step is not None and args.water_step < 1:
        parser.error(t("error_water_step"))
    return convert(args)


if __name__ == "__main__":
    raise SystemExit(main())
