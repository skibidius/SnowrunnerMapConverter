"""Static object placements from SnowRunner level data_for_ssl.bin."""
from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass

import numpy as np

from sr_mesh import euler_degrees_to_quaternion


@dataclass
class ObjectGroup:
    name: str
    mesh_name: str
    instances: list[tuple[float, float, float, int, int, int, float]]


def _mesh_candidates(name: str, mesh_names: set[str]) -> list[str]:
    base = name.rsplit("/", 1)[-1]
    cands = [m for m in mesh_names if m == base or m.endswith("_" + base)]
    if not cands:
        return []
    # Prefer the category that the level path tells us about.
    if name.startswith("landmarks/"):
        preferred = "landmarks_" + base
    elif name.startswith("plants/"):
        preferred = "plants_" + base
    elif name.startswith("models/"):
        preferred = "models_" + base
    else:
        preferred = ""
    for c in cands:
        if c == preferred:
            return [c]
    # Otherwise prefer landmarks, then models, then plants, then shortest.
    def rank(m: str) -> tuple[int, int]:
        order = 0
        if m.startswith("landmarks_"):
            order = 0
        elif m.startswith("models_"):
            order = 1
        elif m.startswith("plants_"):
            order = 2
        return (order, len(m))
    return sorted(cands, key=rank)


def parse_object_groups(data: bytes, mesh_names: set[str],
                        extent: float = 2016.0) -> list[ObjectGroup]:
    """Parse all landmark object groups from data_for_ssl.bin.

    Record layout (28 bytes):
      float32 x, y, z
      float32 scale (0 = default 1.0)
      uint8   rx, ry, rz (Euler angle = (byte - 128) * 180 / 127)
      uint8   random/flags[4]
      uint8   color/random[4]
    """
    limit = extent / 2.0 + extent * 0.15
    pat = re.compile(rb"[\x20-\x7e]{3,240}")
    groups: dict[str, ObjectGroup] = {}

    for m in pat.finditer(data):
        s = m.group()
        st, en = m.start(), m.end()
        if st < 2 or en + 5 > len(data) or data[en] != 0:
            continue
        n = struct.unpack_from("<H", data, st - 2)[0]
        if n != len(s) + 1:
            continue
        count = struct.unpack_from("<I", data, en + 1)[0]
        if not (1 <= count <= 300000):
            continue
        start = en + 5
        if start + count * 28 > len(data):
            continue

        # Validate a sample of records before accepting the group.
        sample = min(count, 24)
        good = 0
        for i in range(sample):
            x, y, z, sc = struct.unpack_from("<4f", data, start + i * 28)
            if (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)
                    and -limit <= x <= limit and -limit <= z <= limit
                    and -250.0 <= y <= 3000.0 and -0.01 <= sc <= 100.0):
                good += 1
        if good < sample * 0.8:
            continue

        try:
            text = s.decode("latin-1")
        except UnicodeDecodeError:
            continue
        if "/" not in text:
            continue
        cands = _mesh_candidates(text, mesh_names)
        if not cands:
            continue
        mesh_name = cands[0]

        instances: list[tuple[float, float, float, int, int, int, float]] = []
        for i in range(count):
            rec = data[start + i * 28:start + (i + 1) * 28]
            x, y, z, sc = struct.unpack_from("<4f", rec, 0)
            if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
                continue
            if not (-limit <= x <= limit and -limit <= z <= limit):
                continue
            if not (-250.0 <= y <= 3000.0):
                continue
            rx, ry, rz = rec[16], rec[17], rec[18]
            scale = float(sc) if 0.01 < sc < 100.0 else 1.0
            instances.append((float(x), float(y), float(z), rx, ry, rz, scale))

        if not instances:
            continue
        existing = groups.get(text)
        if existing is None:
            groups[text] = ObjectGroup(text, mesh_name, instances)
        else:
            existing.instances.extend(instances)
    return list(groups.values())


def instance_quaternion(rx_byte: int, ry_byte: int, rz_byte: int) -> tuple[float, float, float, float]:
    def angle(b: int) -> float:
        return (int(b) - 128.0) * (180.0 / 127.0)
    return euler_degrees_to_quaternion(angle(rx_byte), angle(ry_byte), angle(rz_byte))


def instance_matrix(inst: tuple[float, float, float, int, int, int, float]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (translation, quaternion, scale) for one instance."""
    x, y, z, rx, ry, rz, sc = inst
    return (np.array([x, y, z], dtype=np.float64),
            np.array(instance_quaternion(rx, ry, rz), dtype=np.float64),
            np.array([sc, sc, sc], dtype=np.float64))


def parse_all_points(data: bytes, extent: float = 2016.0) -> np.ndarray:
    """Return all plausible object positions from data_for_ssl.bin.

    Used as a fallback for height calibration when data.stg layout is unknown.
    """
    limit = extent / 2.0 + extent * 0.15
    pat = re.compile(rb"[\x20-\x7e]{3,240}")
    points: list[tuple[float, float, float]] = []
    for m in pat.finditer(data):
        s = m.group()
        st, en = m.start(), m.end()
        if st < 2 or en + 5 > len(data) or data[en] != 0:
            continue
        n = struct.unpack_from("<H", data, st - 2)[0]
        if n != len(s) + 1:
            continue
        count = struct.unpack_from("<I", data, en + 1)[0]
        if not (1 <= count <= 300000):
            continue
        start = en + 5
        if start + count * 28 > len(data):
            continue
        sample = min(count, 24)
        good = 0
        for i in range(sample):
            x, y, z, sc = struct.unpack_from("<4f", data, start + i * 28)
            if (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)
                    and -limit <= x <= limit and -limit <= z <= limit
                    and -250.0 <= y <= 3000.0 and -0.01 <= sc <= 100.0):
                good += 1
        if good < sample * 0.8:
            continue
        for i in range(count):
            x, y, z, sc = struct.unpack_from("<4f", data, start + i * 28)
            if (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)
                    and -limit <= x <= limit and -limit <= z <= limit
                    and -250.0 <= y <= 3000.0 and -0.01 <= sc <= 100.0):
                points.append((float(x), float(y), float(z)))
    return np.array(points, dtype=np.float64) if points else np.empty((0, 3))
