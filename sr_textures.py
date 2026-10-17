"""Resolve SnowRunner material texture references against editor.pak."""
from __future__ import annotations

import io
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

try:
    from sr_i18n import t
except ImportError:  # pragma: no cover
    def t(key, **kwargs):
        return key.format(**kwargs) if kwargs else key


class EditorTextures:
    def __init__(self, store, verbose: bool = False):
        self.store = store
        self.verbose = verbose
        self.index: dict[str, list[str]] = defaultdict(list)
        for name in store.names():
            low = name.lower()
            if low.endswith((".dds", ".tga", ".png")):
                stem = low.rsplit(".", 1)[0]
                self.index[stem].append(name)
        self._png_cache: dict[str, bytes | None] = {}

    @staticmethod
    def _normalize(ref: str) -> tuple[str, str]:
        ref = ref.replace("\\", "/").strip()
        base = ref.rsplit("/", 1)[-1]
        stem = base.rsplit(".", 1)[0].lower()
        flat = ref.rsplit(".", 1)[0].replace("/", "_").lower()
        return flat, stem

    def resolve(self, ref: str) -> str | None:
        if not ref:
            return None
        flat, stem = self._normalize(ref)
        for q in (flat, stem):
            hit = self.index.get(q)
            if hit:
                return self._prefer(hit)
        # Generated names often append suffixes: foo__d.tga -> foo__d_a.dds.
        for q in (flat, stem):
            for key, values in self.index.items():
                if key.startswith(q + "__") or key.startswith(q + "_"):
                    return self._prefer(values)
        return None

    @staticmethod
    def _prefer(values: list[str]) -> str:
        for ext in (".dds", ".tga", ".png"):
            for v in values:
                if v.lower().endswith(ext):
                    return v
        return values[0]

    def png_bytes(self, entry: str) -> bytes | None:
        if entry in self._png_cache:
            return self._png_cache[entry]
        if Image is None:
            self._png_cache[entry] = None
            return None
        try:
            raw = self.store.read(entry)
            im = Image.open(io.BytesIO(raw))
            im.load()
            if im.mode != "RGBA":
                im = im.convert("RGBA")
            out = io.BytesIO()
            im.save(out, format="PNG", optimize=False)
            data = out.getvalue()
            self._png_cache[entry] = data
            if self.verbose:
                print(t("texture_loaded", entry=entry, w=im.size[0], h=im.size[1]))
            return data
        except Exception as exc:  # noqa: BLE001
            if self.verbose:
                print(f"    ! texture {entry}: {exc}")
            self._png_cache[entry] = None
            return None

    def albedo_png(self, ref: str) -> bytes | None:
        entry = self.resolve(ref)
        if entry is None:
            return None
        return self.png_bytes(entry)
