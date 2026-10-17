"""glTF 2.0 / GLB scene builder with instancing and embedded textures."""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import numpy as np


@dataclass
class MeshPart:
    positions: np.ndarray
    normals: np.ndarray | None
    uvs: np.ndarray | None
    indices: np.ndarray
    material: str = "default"


@dataclass
class SceneStats:
    vertices: int = 0
    triangles: int = 0
    instances: int = 0
    prototypes: int = 0
    textures: int = 0


class GltfBuilder:
    def __init__(self, metadata: dict[str, Any]):
        self.metadata = metadata
        self.blob = bytearray()
        self.buffer_views: list[dict[str, Any]] = []
        self.accessors: list[dict[str, Any]] = []
        self.meshes: list[dict[str, Any]] = []
        self.materials: list[dict[str, Any]] = []
        self.nodes: list[dict[str, Any]] = []
        self.images: list[dict[str, Any]] = []
        self.textures: list[dict[str, Any]] = []
        self.samplers: list[dict[str, Any]] = [{
            "magFilter": 9729, "minFilter": 9987,
            "wrapS": 10497, "wrapT": 10497,
        }]
        self.scene_nodes: list[int] = []
        self._material_cache: dict[tuple, int] = {}
        self._texture_cache: dict[str, int] = {}
        self.stats = SceneStats()

    # ------------------------------------------------------------------
    # Low-level buffer/accessor helpers
    # ------------------------------------------------------------------
    def _align(self) -> None:
        while len(self.blob) % 4:
            self.blob.append(0)

    def _add_view(self, data: bytes | np.ndarray, target: int | None = None) -> int:
        self._align()
        offset = len(self.blob)
        raw = data.tobytes() if isinstance(data, np.ndarray) else data
        self.blob.extend(raw)
        view: dict[str, Any] = {
            "buffer": 0, "byteOffset": offset, "byteLength": len(raw),
        }
        if target is not None:
            view["target"] = target
        self.buffer_views.append(view)
        return len(self.buffer_views) - 1

    def _add_accessor(self, view: int, component_type: int, count: int,
                      typ: str, data: np.ndarray | None = None,
                      minmax: bool = False, normalized: bool = False) -> int:
        acc: dict[str, Any] = {
            "bufferView": view,
            "componentType": component_type,
            "count": count,
            "type": typ,
        }
        if normalized:
            acc["normalized"] = True
        if minmax and data is not None and len(data):
            acc["min"] = data.min(axis=0).astype(float).tolist()
            acc["max"] = data.max(axis=0).astype(float).tolist()
        self.accessors.append(acc)
        return len(self.accessors) - 1

    # ------------------------------------------------------------------
    # Textures / materials
    # ------------------------------------------------------------------
    def add_texture(self, png_bytes: bytes, name: str = "texture") -> int:
        key = name
        if key in self._texture_cache:
            return self._texture_cache[key]
        view = self._add_view(png_bytes)
        image_idx = len(self.images)
        self.images.append({
            "name": name,
            "bufferView": view,
            "mimeType": "image/png",
        })
        tex_idx = len(self.textures)
        self.textures.append({"sampler": 0, "source": image_idx, "name": name})
        self._texture_cache[key] = tex_idx
        self.stats.textures = len(self.textures)
        return tex_idx

    def material(self, name: str, texture: int | None = None,
                 color: tuple[float, float, float, float] = (0.7, 0.7, 0.7, 1.0),
                 alpha_mode: str = "OPAQUE", double_sided: bool = False) -> int:
        key = (name, texture, alpha_mode, double_sided, tuple(round(c, 4) for c in color))
        if key in self._material_cache:
            return self._material_cache[key]
        mat: dict[str, Any] = {
            "name": name,
            "pbrMetallicRoughness": {
                "baseColorFactor": list(color),
                "metallicFactor": 0.0,
                "roughnessFactor": 0.85,
            },
            "doubleSided": bool(double_sided),
        }
        if texture is not None:
            mat["pbrMetallicRoughness"]["baseColorTexture"] = {"index": texture, "texCoord": 0}
        if alpha_mode and alpha_mode != "OPAQUE":
            mat["alphaMode"] = alpha_mode
            if alpha_mode == "MASK":
                mat["alphaCutoff"] = 0.5
        idx = len(self.materials)
        self.materials.append(mat)
        self._material_cache[key] = idx
        return idx

    # ------------------------------------------------------------------
    # Meshes / nodes
    # ------------------------------------------------------------------
    def add_mesh(self, name: str, parts: list[MeshPart],
                 material_fn: Callable[[MeshPart], int]) -> int:
        primitives: list[dict[str, Any]] = []
        for part in parts:
            pos = np.ascontiguousarray(part.positions, dtype="<f4")
            if len(pos) == 0 or len(part.indices) == 0:
                continue
            view_pos = self._add_view(pos, 34962)
            acc_pos = self._add_accessor(view_pos, 5126, len(pos), "VEC3", pos, True)
            attrs: dict[str, int] = {"POSITION": acc_pos}
            if part.normals is not None and len(part.normals) == len(pos):
                nrm = np.ascontiguousarray(part.normals, dtype="<f4")
                view_n = self._add_view(nrm, 34962)
                attrs["NORMAL"] = self._add_accessor(view_n, 5126, len(nrm), "VEC3")
            if part.uvs is not None and len(part.uvs) == len(pos):
                uv = np.ascontiguousarray(part.uvs, dtype="<f4")
                view_uv = self._add_view(uv, 34962)
                attrs["TEXCOORD_0"] = self._add_accessor(view_uv, 5126, len(uv), "VEC2")
            idx = np.ascontiguousarray(part.indices.reshape(-1), dtype="<u4")
            view_idx = self._add_view(idx, 34963)
            acc_idx = self._add_accessor(view_idx, 5125, len(idx), "SCALAR")
            primitives.append({
                "attributes": attrs,
                "indices": acc_idx,
                "material": material_fn(part),
                "mode": 4,
            })
            self.stats.vertices += len(pos)
            self.stats.triangles += len(part.indices)
        mesh_idx = len(self.meshes)
        self.meshes.append({"name": name, "primitives": primitives})
        self.stats.prototypes = len(self.meshes)
        return mesh_idx

    def add_node(self, mesh: int | None, translation: tuple[float, float, float] | None = None,
                 rotation: tuple[float, float, float, float] | None = None,
                 scale: tuple[float, float, float] | None = None,
                 name: str | None = None) -> int:
        node: dict[str, Any] = {}
        if name:
            node["name"] = name
        if mesh is not None:
            node["mesh"] = mesh
        if translation is not None:
            node["translation"] = [float(v) for v in translation]
        if rotation is not None:
            node["rotation"] = [float(v) for v in rotation]
        if scale is not None:
            node["scale"] = [float(v) for v in scale]
        idx = len(self.nodes)
        self.nodes.append(node)
        self.scene_nodes.append(idx)
        self.stats.instances += 1
        return idx

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    def _json(self) -> dict[str, Any]:
        return {
            "asset": {
                "version": "2.0",
                "generator": "SnowRunner Map Converter",
                "extras": self.metadata,
            },
            "scene": 0,
            "scenes": [{"name": "SnowRunner map", "nodes": self.scene_nodes}],
            "nodes": self.nodes,
            "meshes": self.meshes,
            "materials": self.materials,
            "accessors": self.accessors,
            "bufferViews": self.buffer_views,
            "buffers": [{"byteLength": len(self.blob)}],
            **({"images": self.images, "textures": self.textures,
                "samplers": self.samplers} if self.images else {}),
        }

    def export_glb(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        while len(self.blob) % 4:
            self.blob.append(0)
        gltf = self._json()
        gltf["buffers"][0]["byteLength"] = len(self.blob)
        json_bytes = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        while len(json_bytes) % 4:
            json_bytes += b" "
        total = 12 + 8 + len(json_bytes) + 8 + len(self.blob)
        with path.open("wb") as f:
            f.write(struct.pack("<4sII", b"glTF", 2, total))
            f.write(struct.pack("<I4s", len(json_bytes), b"JSON"))
            f.write(json_bytes)
            f.write(struct.pack("<I4s", len(self.blob), b"BIN\x00"))
            f.write(self.blob)

    def export_gltf(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        bin_path = path.with_suffix(".bin")
        while len(self.blob) % 4:
            self.blob.append(0)
        gltf = self._json()
        gltf["buffers"][0]["byteLength"] = len(self.blob)
        gltf["buffers"][0]["uri"] = bin_path.name
        bin_path.write_bytes(self.blob)
        path.write_text(json.dumps(gltf, ensure_ascii=False, indent=2), encoding="utf-8")
