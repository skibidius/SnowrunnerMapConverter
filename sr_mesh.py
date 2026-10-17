"""Minimal CombineXMesh parser for SnowRunner/MudRunner meshes.

Based on the public MIT-licensed Snowrunner-Model-Importer by Brooen:
https://github.com/Brooen/Snowrunner-Model-Importer
"""
from __future__ import annotations

import math
import re
import struct
from typing import Any

import numpy as np

ITEM_POSITION = 0x0000
ITEM_UV = 0x0005
ITEM_NORMAL = 0x0105
ITEM_UNK205 = 0x0205
ITEM_UNK305 = 0x0305
ITEM_WEIGHT = 0x0405
ITEM_LINK = 0x0505
ITEM_UV2 = 0x0605

ITEM_SIZES = {
    ITEM_POSITION: 12, ITEM_UV: 8, ITEM_NORMAL: 4, ITEM_UNK205: 4,
    ITEM_UNK305: 4, ITEM_WEIGHT: 4, ITEM_LINK: 4, ITEM_UV2: 8,
}


class MeshFormatError(Exception):
    pass


class Reader:
    __slots__ = ("d", "o")

    def __init__(self, data: bytes):
        self.d = data
        self.o = 0

    def tell(self) -> int:
        return self.o

    def seek(self, o: int) -> None:
        self.o = o

    def read(self, n: int) -> bytes:
        b = self.d[self.o:self.o + n]
        if len(b) != n:
            raise MeshFormatError(f"Unexpected end of file at 0x{self.o:X} (wanted {n} bytes)")
        self.o += n
        return b

    def _adv(self, n: int) -> int:
        o = self.o
        if o + n > len(self.d):
            raise MeshFormatError(f"Unexpected end of file at 0x{o:X} (wanted {n} bytes)")
        self.o = o + n
        return o

    def s32(self) -> int:
        return struct.unpack_from("<i", self.d, self._adv(4))[0]

    def u32(self) -> int:
        return struct.unpack_from("<I", self.d, self._adv(4))[0]

    def s16(self) -> int:
        return struct.unpack_from("<h", self.d, self._adv(2))[0]

    def u16(self) -> int:
        return struct.unpack_from("<H", self.d, self._adv(2))[0]

    def f32(self) -> float:
        return struct.unpack_from("<f", self.d, self._adv(4))[0]

    def f32s(self, n: int) -> tuple[float, ...]:
        return struct.unpack_from("<%df" % n, self.d, self._adv(4 * n))

    def name(self) -> str:
        ln = self.s32()
        if ln <= 0 or ln > 4096:
            raise MeshFormatError(f"Bad string length {ln} at 0x{self.o - 4:X}")
        s = self.read(ln - 1).decode("cp1251", errors="replace")
        self.read(1)
        return s

    def matrix_rows(self) -> list[list[float]]:
        return [list(self.f32s(4)) for _ in range(4)]


def _parse_vertex_type(r: Reader) -> list[tuple[int, int, int]]:
    count = r.s32()
    if count < 0 or count > 64:
        raise MeshFormatError(f"Bad vertexType count {count} at 0x{r.tell() - 4:X}")
    defs = []
    for _ in range(count):
        r.s16()
        off = r.s16()
        dt = r.u16()
        it = r.u16()
        if it not in ITEM_SIZES:
            raise MeshFormatError(f"Unknown vertex item type 0x{it:04X}")
        defs.append((off, dt, it))
    r.s32()
    r.s32()
    return defs


def _parse_vertices(r: Reader, defs: list[tuple[int, int, int]], count: int) -> dict[str, Any]:
    positions: list[tuple[float, float, float]] = []
    normals: list[tuple[float, float, float]] | None = [] if any(d[2] == ITEM_NORMAL for d in defs) else None
    uv: list[tuple[float, float]] | None = [] if any(d[2] == ITEM_UV for d in defs) else None
    uv2: list[tuple[float, float]] | None = [] if any(d[2] == ITEM_UV2 for d in defs) else None
    weights: list[tuple[float, float, float, float]] | None = [] if any(d[2] == ITEM_WEIGHT for d in defs) else None
    links: list[tuple[int, int, int, int]] | None = [] if any(d[2] == ITEM_LINK for d in defs) else None

    for _ in range(count):
        for _, _, it in defs:
            if it == ITEM_POSITION:
                positions.append(r.f32s(3))
            elif it == ITEM_UV:
                uv.append(r.f32s(2))
            elif it == ITEM_NORMAL:
                b = r.read(4)
                normals.append(tuple(
                    (float(c) - 128.0) / (127.0 if c >= 128 else 128.0)
                    for c in b[:3]))
            elif it == ITEM_UV2:
                uv2.append(r.f32s(2))
            elif it == ITEM_WEIGHT:
                b = r.read(4)
                weights.append((b[0] / 255.0, b[1] / 255.0, b[2] / 255.0, b[3] / 255.0))
            elif it == ITEM_LINK:
                links.append(tuple(r.read(4)))
            else:
                r.read(ITEM_SIZES[it])

    return {"positions": positions, "normals": normals, "uv": uv, "uv2": uv2,
            "weights": weights, "links": links}


def _parse_submesh_header(r: Reader) -> dict[str, Any]:
    return {
        "materialIndex": r.u32(),
        "triangleOffset": r.u32(),
        "triangleCount": r.u32(),
        "vertexOffset": r.u32(),
        "vertexCount": r.u32(),
    }


def _parse_mesh(r: Reader, link_in_count: int) -> dict[str, Any]:
    m: dict[str, Any] = {}
    m["vertexCount"] = r.s32()
    m["triangleCount"] = r.s32()
    m["name"] = r.name()
    r.s32()
    material_count = r.s32()
    r.s32()
    if not (0 <= material_count <= 4096):
        raise MeshFormatError(f"Bad material count {material_count}")
    m["materials"] = [r.name() for _ in range(material_count)]

    link_out = r.s32()
    if not (0 <= link_out <= 4096):
        raise MeshFormatError(f"Bad linkOutCount {link_out}")
    m["linkOutCount"] = link_out
    m["linkMatrices"] = [r.matrix_rows() for _ in range(link_out)]
    r.s16()

    if link_out == 0:
        r.f32s(6)
        submesh_count = r.s32()
        m["submeshes"] = [_parse_submesh_header(r) for _ in range(submesh_count)]
        for s in m["submeshes"]:
            s["indices"] = None
        m["linkedNodes"] = []
        defs = _parse_vertex_type(r)
        m["vertexDefs"] = defs
        m["vdata"] = _parse_vertices(r, defs, m["vertexCount"])
        m["triangles"] = list(struct.iter_unpack("<3H", r.read(6 * m["triangleCount"])))
        if link_in_count != 0:
            r.s16()
    else:
        r.s16()
        submesh_count = r.s32()
        index_counts = [r.s32() for _ in range(submesh_count)]
        subs = []
        for i in range(submesh_count):
            s = _parse_submesh_header(r)
            s["indices"] = [r.s32() for _ in range(index_counts[i])]
            subs.append(s)
        m["submeshes"] = subs
        m["linkedNodes"] = [r.s16() for _ in range(link_out)]
        r.f32s(6)
        r.read(4 * 6)
        defs = _parse_vertex_type(r)
        m["vertexDefs"] = defs
        m["vdata"] = _parse_vertices(r, defs, m["vertexCount"])
        m["triangles"] = list(struct.iter_unpack("<3H", r.read(6 * m["triangleCount"])))
        r.s16()

    flag = r.s16()
    if 4 <= flag <= 17:
        r.read(1)
        count = r.s16()
        r.read(1)
        r.read(count + 16)
        flag2 = r.s16()
        if flag2 > 100:
            r.matrix_rows()
    if flag > 100:
        r.matrix_rows()
    return m


def _parse_node(r: Reader) -> dict[str, Any]:
    n: dict[str, Any] = {}
    n["parentId"] = r.s16()
    n["id"] = r.s16()
    n["linkInCount"] = r.s16()
    r.s16()
    n["name"] = r.name()
    n["matrix"] = r.matrix_rows()
    pos = r.tell()
    vertex_count = r.s32()
    r.seek(pos)
    if vertex_count != 0:
        n["mesh"] = _parse_mesh(r, n["linkInCount"])
    else:
        r.read(4)
        n["mesh"] = None
    return n


def parse_combinexmesh(data: bytes) -> dict[str, Any]:
    r = Reader(data)
    xml_length = r.s32()
    if not (2 < xml_length < len(data)):
        raise MeshFormatError("Not a CombineXMesh file (bad XML header length)")
    xml = r.read(xml_length - 2).decode("cp1251", errors="replace")
    if "<" not in xml[:64]:
        raise MeshFormatError("Not a CombineXMesh file (missing XML header)")
    r.read(6)

    node_count = r.s32()
    r.f32s(6)
    r.s32()
    if not (0 < node_count <= 65535):
        raise MeshFormatError(f"Bad node count {node_count}")

    nodes = []
    for i in range(node_count):
        try:
            nodes.append(_parse_node(r))
        except MeshFormatError as e:
            raise MeshFormatError(f"Failed parsing node {i}/{node_count}: {e}") from e
    return {"xml": xml, "nodes": nodes}


def parse_xml_materials(xml: str) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    for tag, block in re.findall(r"<(MaterialOverride|Material)\b(.*?)/>", xml, re.S):
        attrs = dict(re.findall(r'(\w+)\s*=\s*"([^"]*)"', block))
        name = attrs.get("TargetMaterialName") if tag == "MaterialOverride" else attrs.get("Name")
        if name:
            merged = result.setdefault(name, {})
            merged.update(attrs)
    return result


def _world_matrices(nodes: list[dict[str, Any]]) -> list[np.ndarray]:
    by_id: dict[int, int] = {}
    for i, n in enumerate(nodes):
        by_id.setdefault(int(n["id"]), i)
    worlds: list[np.ndarray | None] = [None] * len(nodes)

    def world(i: int) -> np.ndarray:
        cached = worlds[i]
        if cached is not None:
            return cached
        n = nodes[i]
        local = np.array(n["matrix"], dtype=np.float64).T  # column-vector
        pid = int(n["parentId"])
        if pid >= 0 and pid in by_id and by_id[pid] != i:
            m = world(by_id[pid]) @ local
        else:
            m = local
        worlds[i] = m
        return m

    for i in range(len(nodes)):
        world(i)
    return [w if w is not None else np.eye(4) for w in worlds]


def extract_visual_parts(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """Return LOD0 visual geometry grouped by material.

    Each part is a dict with:
      positions (N,3) float32, normals (N,3) or None, uvs (N,2) or None,
      indices (M,3) uint32, material (str).
    """
    nodes = parsed["nodes"]
    worlds = _world_matrices(nodes)
    parts: list[dict[str, Any]] = []
    skip_words = ("cdt", "lod", "billboard", "shadow", "phys", "collision")

    for i, node in enumerate(nodes):
        m = node.get("mesh")
        if not m:
            continue
        lname = (node.get("name") or "").lower()
        mname = (m.get("name") or "").lower()
        if any(w in lname or w in mname for w in skip_words):
            continue
        materials = list(m.get("materials") or [])
        if not materials or all(x.lower() in ("physmat", "phys_mat") for x in materials):
            continue
        positions = np.asarray(m["vdata"]["positions"], dtype=np.float64)
        if len(positions) == 0 or m["triangleCount"] <= 0:
            continue

        world = worlds[i]
        rot = world[:3, :3]
        trans = world[:3, 3]
        pos = positions @ rot.T + trans

        normals = None
        nraw = m["vdata"].get("normals")
        if nraw:
            n = np.asarray(nraw, dtype=np.float64)
            try:
                nmat = np.linalg.inv(rot).T
            except np.linalg.LinAlgError:
                nmat = rot
            n = n @ nmat.T
            lengths = np.linalg.norm(n, axis=1)
            lengths[lengths < 1e-12] = 1.0
            n = n / lengths[:, None]
            normals = n.astype(np.float32)

        uvs = None
        uraw = m["vdata"].get("uv")
        if uraw:
            uvs = np.asarray(uraw, dtype=np.float32)

        triangles = np.asarray(m["triangles"], dtype=np.int64)
        if len(triangles) == 0:
            continue

        submeshes = m.get("submeshes") or []
        ranges: list[tuple[int, int, int]] = []
        if submeshes:
            for s in submeshes:
                mi = int(s.get("materialIndex", 0))
                t0 = int(s.get("triangleOffset", 0))
                tc = int(s.get("triangleCount", 0))
                if tc > 0:
                    ranges.append((mi, t0, min(t0 + tc, len(triangles))))
        else:
            ranges.append((0, 0, len(triangles)))

        for mi, t0, t1 in ranges:
            if t0 >= t1:
                continue
            tri = triangles[t0:t1]
            used = np.unique(tri.ravel())
            if len(used) == 0:
                continue
            remap = np.full(len(positions), -1, dtype=np.int64)
            remap[used] = np.arange(len(used), dtype=np.int64)
            idx = remap[tri].astype(np.uint32)
            mat = materials[mi] if 0 <= mi < len(materials) else materials[0]
            parts.append({
                "positions": pos[used].astype(np.float32),
                "normals": None if normals is None else normals[used],
                "uvs": None if uvs is None else uvs[used],
                "indices": idx,
                "material": mat,
            })
    return parts


def euler_degrees_to_quaternion(rx: float, ry: float, rz: float) -> tuple[float, float, float, float]:
    """XYZ Euler (degrees) -> quaternion (x, y, z, w)."""
    hx, hy, hz = (math.radians(rx) * 0.5, math.radians(ry) * 0.5,
                  math.radians(rz) * 0.5)
    cx, sx = math.cos(hx), math.sin(hx)
    cy, sy = math.cos(hy), math.sin(hy)
    cz, sz = math.cos(hz), math.sin(hz)
    return (
        sx * cy * cz - cx * sy * sz,
        cx * sy * cz + sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
        cx * cy * cz + sx * sy * sz,
    )
