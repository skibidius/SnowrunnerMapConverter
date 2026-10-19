# SnowRunner Map Converter

<img src="docs/preview/hero.png" width="900" alt="Black River, Michigan">

Converts SnowRunner level paks to GLB, glTF, OBJ, PLY, STL, PNG and JSON.

## Requirements

- Python 3.10+
- `numpy`
- `Pillow` (for object textures from `editor.pak`)

```bash
python3 -m pip install -r requirements.txt
```

Full scene:

- `level_*.pak` — the level;
- `shared.pak` — object meshes;
- `editor.pak` — object textures.

All paks must be from the same game version. `shared.pak` and `editor.pak` are searched next to the level or can be passed with `--shared-pak` / `--editor-pak`.

Prebuilt binaries are in Releases.

The terminal has spinners for short operations and progress bars for objects, textures and tiles. Use `--no-tui` for logs/CI.

## Where the pak files are

Three kinds of files are used:

- `level_*.pak` — the level itself, required;
- `shared.pak` — object meshes, needed for the full scene;
- `editor.pak` — object textures, needed for textures.

They are usually in the game folder:

```
Windows (Steam): C:\Program Files (x86)\Steam\steamapps\common\SnowRunner\preload\paks\
Windows (Epic):  C:\Program Files\Epic Games\SnowRunner\preload\paks\
macOS (Steam):   ~/Library/Application Support/Steam/steamapps/common/SnowRunner/preload/paks/
Linux (Proton):  ~/.steam/steam/steamapps/common/SnowRunner/preload/paks/
```

Levels may be in the `levels` subfolder. Copy the needed `level_*.pak`, `shared.pak` and `editor.pak` into one folder, or pass them with `--shared-pak` and `--editor-pak`.

Full help:

```bash
python3 snowrunner_map_converter.py --where-paks
```

## Usage

```bash
python3 snowrunner_map_converter.py level_us_01_01.pak -f glb
```

If `shared.pak` is next to the level, objects are added automatically.

Terrain and water only:

```bash
python3 snowrunner_map_converter.py level_us_01_01.pak -f glb --terrain-only
```

Check the environment:

```bash
python3 snowrunner_map_converter.py --check
```

List archive contents:

```bash
python3 snowrunner_map_converter.py level_us_01_01.pak --list
```

Without `-f`, the script asks for a format. On first run it asks for a language.

## Language

```bash
python3 snowrunner_map_converter.py --lang ru ...
python3 snowrunner_map_converter.py --lang en ...
python3 snowrunner_map_converter.py --reset-config ...
```

Config:

- macOS: `~/Library/Application Support/SnowRunnerMapConverter/config.json`
- Windows: `%APPDATA%\SnowRunnerMapConverter\config.json`
- Linux: `~/.config/SnowRunnerMapConverter/config.json`

## Formats

| Format | Contents |
|---|---|
| `glb` | terrain, water, objects, materials, textures |
| `gltf` | same, separate `.gltf` + `.bin` |
| `obj` | terrain and water, `.obj` + `.mtl` |
| `ply` | terrain and water, binary PLY |
| `stl` | terrain and water, or a print model with `--print` |
| `png` | 16-bit heightmap |
| `raw` | raw `uint16` + JSON |
| `json` | metadata and objects |

## 3D printing

```bash
python3 snowrunner_map_converter.py level_us_01_01.pak --print -o map_print.stl
```

The model is the terrain extruded down to a flat base at `Z=0`, with no pedestal. Units are millimeters.

Options:

```
--print-width 200       model width, mm
--base-thickness 3      base thickness under the lowest terrain, mm
--print-quality ...     balanced (default), draft, high, max
--print-no-objects      terrain only, no objects
--print-full-objects    full tree meshes in balanced/draft
```

`balanced` replaces trees and bushes with simple print proxies. `high` and `max` use full meshes and produce much larger files.

## Splitting

```bash
python3 snowrunner_map_converter.py level_us_01_01.pak --print --split 2x2 -o map_print.stl
```

Output: `map_print_r1c1.stl` … `map_print_r2c2.stl` and `map_print_tiles.json`.

- `--split 2x2` — 2 columns × 2 rows; `3x3`, `3x2`, etc. are also supported.
- `--split-gap 0.2` — gap between tiles, mm.
- Tiles with `--print-no-objects` are watertight; with objects they contain separate shells that the slicer unions.

## Limitations

- Grass and small foliage from `data.stg` (`grass_*`, `leaves_*`, `debris_*`) are not imported.
- Physics, collision and gameplay zones are not exported.
- FBX is not written directly; use Blender.
- OBJ and PLY do not support the instanced full scene.

## Build

```bash
python3 -m pip install -r requirements.txt -r requirements-dev.txt
pyinstaller --onefile --name snowrunner-map-converter \
  --hidden-import sr_mesh --hidden-import sr_objects \
  --hidden-import sr_textures --hidden-import sr_gltf \
  --hidden-import sr_print --hidden-import sr_split \
  --hidden-import sr_i18n --hidden-import sr_tui snowrunner_map_converter.py
```

GitHub Actions builds Windows, Linux and macOS on push. A `v*` tag publishes the archives to Releases.

## License

MIT. Game files are not included in the repository.
