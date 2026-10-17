# SnowRunner Map Converter

<img src="docs/preview/hero.png" width="900" alt="SnowRunner Map Converter">

Converts SnowRunner level paks to GLB, glTF, OBJ, PLY, STL, PNG and JSON.

- [Русская документация](README.ru.md)
- [English documentation](README.en.md)

## Quick start

```bash
python3 -m pip install -r requirements.txt
python3 snowrunner_map_converter.py level_us_01_01.pak -f glb
```

Full scene requires `shared.pak` (meshes) and `editor.pak` (textures) from the same game version. Place them next to the level pak or pass `--shared-pak` / `--editor-pak`.

```bash
python3 snowrunner_map_converter.py level_us_01_01.pak --print --split 2x2 -o map_print.stl
```

Prebuilt binaries: Releases.
