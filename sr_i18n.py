"""Configuration and tiny localization layer for the converter."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def _config_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
    return Path(base) / "SnowRunnerMapConverter"


CONFIG_PATH = _config_dir() / "config.json"
LANG = "ru"

TEXTS: dict[str, dict[str, str]] = {
    "ru": {
        "choose_lang_title": "Первый запуск: выберите язык / First run: choose language",
        "choose_lang_prompt": "  1) Русский\n  2) English\nНомер [1]: ",
        "choose_lang_saved": "Язык сохранён в конфиге: {path}",
        "invalid_choice": "Некорректный выбор.",
        "format_title": "Выберите формат результата:",
        "format_prompt": "Номер или имя формата [1/glb]: ",
        "fmt_glb": "GLB (glTF 2.0) — рекомендуется для Blender/Three.js/просмотрщиков",
        "fmt_obj": "Wavefront OBJ + MTL — универсальный текстовый формат",
        "fmt_ply": "PLY (binary) — меши с цветом вершин",
        "fmt_stl": "STL (binary) — для 3D-печати и CAD",
        "fmt_gltf": "glTF 2.0 (отдельные .gltf + .bin)",
        "fmt_png": "PNG 16-bit — карта высот",
        "fmt_raw": "RAW .u16 + JSON — сырые высоты",
        "fmt_json": "JSON — метаданные и объекты",
        "archive": "Архив/папка: {path}",
        "files_count": "Файлов: {count}",
        "detected": "Определено:",
        "height_map": "  height map  : {value}",
        "water_map": "  water map   : {value}",
        "data_stg": "  data.stg    : {value}",
        "data_ssl": "  data_for_ssl: {value}",
        "map": "Карта: {name}",
        "size": "  Размер: {w}x{h}, сетка {spacing:.4f} м, охват {ex:g}x{ez:g} м",
        "water": "  Вода: {w}x{h}, диапазон {lo}..{hi} м",
        "height_scale": "  Масштаб высот: {scale:.8f} м/ед. (макс. {maxh:.2f} м)",
        "calibration": "  Калибровка: {source}",
        "calib_meshes": "вершины мешей data.stg: {inliers}/{total} ({ratio:.1f}%)",
        "calib_objects": "объекты data.stg: {inliers}/{total} ({ratio:.1f}%)",
        "calib_ssl_objects": "объекты data_for_ssl.bin: {inliers}/{total} ({ratio:.1f}%)",
        "calib_manual": "задано вручную (--max-height / --height-scale)",
        "calib_fallback": "резервное значение max_height={maxh:g} м",
        "inliers": "    inliers={inliers}/{total} ({ratio:.1f}%), mode={mode:.3f} м",
        "objects_actors": "  Объекты/акторы (data.stg): {count}",
        "building_mesh": "Построение сетки (step={step}) ...",
        "terrain_stats": "  terrain: {v:,} вершин, {t:,} треугольников",
        "water_stats": "  water:   {v:,} вершин, {t:,} треугольников",
        "markers_stats": "  objects: {n} маркеров, {t:,} треугольников",
        "shared_pak": "  shared.pak: {path}",
        "editor_pak": "  editor.pak: {path}",
        "editor_pak_missing": "  editor.pak: — (без текстур объектов)",
        "full_start": "  Полная сцена: рельеф + объекты + текстуры ...",
        "object_groups": "  Групп объектов: {groups}, инстансов: {instances:,}",
        "texture_loaded": "    texture: {entry} -> {w}x{h}",
        "prototype_added": "    + {mesh}: {tris:,} треуг.",
        "prototype_skipped": "    ! пропущен {mesh}: {error}",
        "objects_placed": "  Объектов размещено: {count:,}",
        "unique_meshes": "  Уникальных мешей: {count:,}",
        "textures_count": "  Текстур: {count:,}",
        "skipped_groups": "  Пропущено групп: {count}",
        "verts_tris": "  Вершин: {v:,}, треугольников: {t:,}",
        "export_to": "Экспорт в {fmt}: {path}",
        "preview_saved": "Превью: {path}",
        "objects_saved": "Объекты: {path}",
        "textures_export": "Экспорт текстур в {path} ...",
        "textures_exported": "  Экспортировано {count} текстур",
        "done": "Готово за {seconds:.1f} с: {size}",
        "done_simple": "Готово: {path}",
        "done_raw": "Готово: {path} (+ .json)",
        "done_gltf_bin": "       {path}",
        "error_no_height": "Ошибка: в архиве не найден *_height_map.pct.",
        "error_no_height_hint": "Убедитесь, что это level_*.pak из SnowRunner (или распакованная папка уровня).",
        "error_height_2d": "Ошибка: height map не является 2D-текстурой",
        "error_water": "  ! Не удалось прочитать water height map: {error}",
        "error_objects": "  ! Не удалось извлечь объекты: {error}",
        "error_full_unavailable": "  ! Полная сцена недоступна: нужны shared.pak и data_for_ssl.bin.",
        "error_full_format": "  ! Полная сцена с объектами поддерживается для GLB/glTF/STL.",
        "error_full_format2": "    Для OBJ/PLY будет экспортирован только рельеф и вода.",
        "error_sr_gltf": "  ! Модуль sr_gltf недоступен.",
        "error_pillow": "  ! Pillow не установлен: объекты будут без текстур.",
        "stl_bake_start": "  STL: запекание {triangles:,} треугольников (рельеф + вода + объекты) ...",
        "stl_written": "  STL: записано {triangles:,} треугольников",
        "stl_stats": "  Треугольников: {t:,}",
        "error_format": "Ошибка: неизвестный формат '{fmt}'",
        "error_input_missing": "Ошибка: путь не существует: {path}",
        "error_no_input": "не указан входной файл",
        "error_step": "--step должен быть >= 1",
        "error_water_step": "--water-step должен быть >= 1",
        "input_prompt": "Путь к .pak или папке уровня: ",
        "format_not_implemented": "Ошибка: формат {fmt} не реализован",
        "input_prompt": "Путь к .pak или папке уровня: ",
        "wizard_output": "Выходной файл/папка (Enter — по умолчанию): ",
        "wizard_print_ask": "Настроить 3D-печать? [Y/n]: ",
        "wizard_quality": "Качество печати:\n  1) balanced (рекомендуется)\n  2) high\n  3) max\n  4) draft\nНомер [1]: ",
        "wizard_split": "Разрезать на плитки?\n  0) нет\n  2) 2x2\n  3) 3x3\nНомер [0]: ",
        "wizard_objects": "Добавить деревья/здания в печать? [Y/n]: ",
        "check_title": "Проверка окружения SnowRunner Map Converter",
        "check_python": "  Python: {version}",
        "check_numpy": "  numpy: {version}",
        "check_pillow": "  Pillow: {version}",
        "check_modules": "  Модули: {status}",
        "check_config": "  Конфиг: {path} (язык: {lang})",
        "check_paks": "  Pak-файлы: {status}",
        "check_input": "  Уровень: {path}",
        "check_ok": "OK",
        "check_missing": "НЕТ",
        "check_none": "не найдены",
        "check_hint": "Для полной сцены нужны shared.pak и editor.pak рядом с уровнем.",
        "check_done": "Проверка завершена.",
        "help_description": "Конвертер карт SnowRunner (.pak / папка) в GLB, OBJ, PLY, STL, PNG и JSON",
        "list_option": "только показать содержимое архива",
        "help_input": ".pak, папка с распакованным уровнем или отдельный .pct",
        "help_output": "выходной файл/папка (необязательно)",
        "help_output_opt": "выходной файл/папка (альтернатива позиционному аргументу)",
        "help_format": "формат результата (если не указан, будет запрошен)",
        "help_step": "шаг прореживания сетки: 1=полное 0.5 м, 2=1 м, 4=2 м (по умолчанию 4)",
        "help_water_step": "шаг сетки воды (по умолчанию подбирается под --step)",
        "help_water_offset": "подъём поверхности воды в метрах (по умолчанию 0.05)",
        "help_no_water": "не добавлять поверхность воды",
        "help_max_height": "максимальная высота уровня в метрах (по умолчанию авто/200)",
        "help_height_scale": "масштаб высот в метрах на единицу R16 (переопределяет калибровку)",
        "help_markers": "добавить кубики-маркеры объектов",
        "help_marker_size": "размер маркера в метрах",
        "help_objects": "экспортировать объекты в JSON/CSV",
        "help_obj_normals": "писать нормали в OBJ (файл больше)",
        "help_no_uv": "не писать UV в OBJ",
        "help_no_colors": "не писать цвета вершин в PLY",
        "help_textures": "экспортировать все .pct текстуры",
        "help_full": "полная сцена: рельеф + все объекты/деревья/здания из shared.pak",
        "help_terrain_only": "только рельеф и вода (без объектов)",
        "help_shared_pak": "путь к shared.pak (по умолчанию ищется рядом с уровнем)",
        "help_editor_pak": "путь к editor.pak (нужен для текстур объектов)",
        "help_no_textures": "не встраивать текстуры объектов",
        "help_object_limit": "ограничить число групп объектов (для тестов)",
        "help_print": "STL для 3D-печати: рельеф, экструдированный в сплошное основание",
        "help_print_width": "ширина модели для печати в мм (по умолчанию 200)",
        "help_base_thickness": "толщина основания под самым низким рельефом в мм (по умолчанию 3)",
        "help_print_no_objects": "не добавлять объекты (только рельеф, watertight)",
        "help_print_quality": "качество печати: balanced=шаг 4 (2 м, по умолчанию), draft=8, high=2, max=1",
        "help_print_full_objects": "в balanced/draft не упрощать деревья, а печатать полные меши",
        "help_split": "разрезать печатную модель на NxM плиток (например, 2x2 или 3x2)",
        "help_split_gap": "зазор между плитками в мм (по умолчанию 0.2)",
        "error_split_format": "Ошибка: --split работает только в режиме --print (STL).",
        "error_split_value": "Ошибка: --split должен быть в формате NxM, например 2x2.",
        "print_split_start": "  Разрезка на {nx}x{ny}: {count} плиток, зазор {gap:g} мм ...",
        "print_split_done": "  Готово: {count} плиток. Манифест: {manifest}",
        "print_start": "  STL для 3D-печати: ширина {width:g} мм, основание {base:g} мм, шаг {step} ...",
        "print_objects_mode": "  Объекты: {mode}",
        "print_objects_proxy": "деревья/кусты заменены на печатные прокси",
        "print_objects_full": "полные меши объектов",
        "print_objects_none": "объекты отключены",
        "print_scale": "  Масштаб печати: {scale:.5f} мм/м, размер карты {w:.1f}x{d:.1f} мм",
        "print_height": "  Высота модели: {h:.1f} мм (рельеф + основание)",
        "print_done": "  Печатная модель готова: {path}",
        "error_print_format": "Ошибка: --print работает только с -f stl.",
        "help_preview": "сделать PNG-превью рельефа",
        "help_no_preview": "не делать превью для PNG",
        "help_verbose": "подробный вывод",
        "help_check": "проверить окружение, зависимости и найденные pak-файлы",
        "help_open": "открыть папку с результатом после конвертации",
        "help_lang": "язык интерфейса: ru или en",
        "help_reset_config": "сбросить конфиг и снова спросить язык",
        "help_version": "показать версию",
    },
    "en": {
        "choose_lang_title": "First run: choose language / Первый запуск: выберите язык",
        "choose_lang_prompt": "  1) Русский\n  2) English\nNumber [2]: ",
        "choose_lang_saved": "Language saved to config: {path}",
        "invalid_choice": "Invalid choice.",
        "format_title": "Choose output format:",
        "format_prompt": "Number or format name [1/glb]: ",
        "fmt_glb": "GLB (glTF 2.0) — recommended for Blender/Three.js/viewers",
        "fmt_obj": "Wavefront OBJ + MTL — universal text format",
        "fmt_ply": "PLY (binary) — mesh with vertex colors",
        "fmt_stl": "STL (binary) — for 3D printing and CAD",
        "fmt_gltf": "glTF 2.0 (separate .gltf + .bin)",
        "fmt_png": "PNG 16-bit — heightmap",
        "fmt_raw": "RAW .u16 + JSON — raw heights",
        "fmt_json": "JSON — metadata and objects",
        "archive": "Archive/folder: {path}",
        "files_count": "Files: {count}",
        "detected": "Detected:",
        "height_map": "  height map  : {value}",
        "water_map": "  water map   : {value}",
        "data_stg": "  data.stg    : {value}",
        "data_ssl": "  data_for_ssl: {value}",
        "map": "Map: {name}",
        "size": "  Size: {w}x{h}, cell {spacing:.4f} m, extent {ex:g}x{ez:g} m",
        "water": "  Water: {w}x{h}, range {lo}..{hi} m",
        "height_scale": "  Height scale: {scale:.8f} m/unit (max {maxh:.2f} m)",
        "calibration": "  Calibration: {source}",
        "calib_meshes": "data.stg mesh vertices: {inliers}/{total} ({ratio:.1f}%)",
        "calib_objects": "data.stg objects: {inliers}/{total} ({ratio:.1f}%)",
        "calib_ssl_objects": "data_for_ssl.bin objects: {inliers}/{total} ({ratio:.1f}%)",
        "calib_manual": "manual override (--max-height / --height-scale)",
        "calib_fallback": "fallback max_height={maxh:g} m",
        "inliers": "    inliers={inliers}/{total} ({ratio:.1f}%), mode={mode:.3f} m",
        "objects_actors": "  Objects/actors (data.stg): {count}",
        "building_mesh": "Building mesh (step={step}) ...",
        "terrain_stats": "  terrain: {v:,} vertices, {t:,} triangles",
        "water_stats": "  water:   {v:,} vertices, {t:,} triangles",
        "markers_stats": "  objects: {n} markers, {t:,} triangles",
        "shared_pak": "  shared.pak: {path}",
        "editor_pak": "  editor.pak: {path}",
        "editor_pak_missing": "  editor.pak: — (objects without textures)",
        "full_start": "  Full scene: terrain + objects + textures ...",
        "object_groups": "  Object groups: {groups}, instances: {instances:,}",
        "texture_loaded": "    texture: {entry} -> {w}x{h}",
        "prototype_added": "    + {mesh}: {tris:,} triangles",
        "prototype_skipped": "    ! skipped {mesh}: {error}",
        "objects_placed": "  Objects placed: {count:,}",
        "unique_meshes": "  Unique meshes: {count:,}",
        "textures_count": "  Textures: {count:,}",
        "skipped_groups": "  Skipped groups: {count}",
        "verts_tris": "  Vertices: {v:,}, triangles: {t:,}",
        "export_to": "Exporting {fmt}: {path}",
        "preview_saved": "Preview: {path}",
        "objects_saved": "Objects: {path}",
        "textures_export": "Exporting textures to {path} ...",
        "textures_exported": "  Exported {count} textures",
        "done": "Done in {seconds:.1f} s: {size}",
        "done_simple": "Done: {path}",
        "done_raw": "Done: {path} (+ .json)",
        "done_gltf_bin": "       {path}",
        "error_no_height": "Error: *_height_map.pct not found in the archive.",
        "error_no_height_hint": "Make sure this is a SnowRunner level_*.pak (or an unpacked level folder).",
        "error_height_2d": "Error: height map is not a 2D texture",
        "error_water": "  ! Could not read water height map: {error}",
        "error_objects": "  ! Could not extract objects: {error}",
        "error_full_unavailable": "  ! Full scene unavailable: shared.pak and data_for_ssl.bin are required.",
        "error_full_format": "  ! Full scene with objects is supported for GLB/glTF/STL.",
        "error_full_format2": "    OBJ/PLY will contain terrain and water only.",
        "error_sr_gltf": "  ! sr_gltf module is unavailable.",
        "error_pillow": "  ! Pillow is not installed: objects will be untextured.",
        "stl_bake_start": "  STL: baking {triangles:,} triangles (terrain + water + objects) ...",
        "stl_written": "  STL: wrote {triangles:,} triangles",
        "stl_stats": "  Triangles: {t:,}",
        "error_format": "Error: unknown format '{fmt}'",
        "error_input_missing": "Error: path does not exist: {path}",
        "error_no_input": "no input file specified",
        "error_step": "--step must be >= 1",
        "error_water_step": "--water-step must be >= 1",
        "input_prompt": "Path to .pak or level folder: ",
        "format_not_implemented": "Error: format {fmt} is not implemented",
        "input_prompt": "Path to .pak or level folder: ",
        "wizard_output": "Output file/folder (Enter for default): ",
        "wizard_print_ask": "Set up 3D printing? [Y/n]: ",
        "wizard_quality": "Print quality:\n  1) balanced (recommended)\n  2) high\n  3) max\n  4) draft\nNumber [1]: ",
        "wizard_split": "Split into tiles?\n  0) no\n  2) 2x2\n  3) 3x3\nNumber [0]: ",
        "wizard_objects": "Include trees/buildings in the print? [Y/n]: ",
        "check_title": "SnowRunner Map Converter environment check",
        "check_python": "  Python: {version}",
        "check_numpy": "  numpy: {version}",
        "check_pillow": "  Pillow: {version}",
        "check_modules": "  Modules: {status}",
        "check_config": "  Config: {path} (language: {lang})",
        "check_paks": "  Pak files: {status}",
        "check_input": "  Level: {path}",
        "check_ok": "OK",
        "check_missing": "MISSING",
        "check_none": "none found",
        "check_hint": "Full scene needs shared.pak and editor.pak next to the level.",
        "check_done": "Check complete.",
        "help_description": "SnowRunner map converter (.pak / folder) to GLB, OBJ, PLY, STL, PNG and JSON",
        "list_option": "only list archive contents",
        "help_input": ".pak, an unpacked level folder, or a single .pct",
        "help_output": "output file/folder (optional)",
        "help_output_opt": "output file/folder (alternative to positional argument)",
        "help_format": "output format (if omitted, you will be asked)",
        "help_step": "mesh decimation: 1=full 0.5 m, 2=1 m, 4=2 m (default 4)",
        "help_water_step": "water grid step (default: chosen from --step)",
        "help_water_offset": "water surface lift in meters (default 0.05)",
        "help_no_water": "do not add the water surface",
        "help_max_height": "maximum level height in meters (default auto/200)",
        "help_height_scale": "height scale in meters per R16 unit (overrides calibration)",
        "help_markers": "add cube markers for objects",
        "help_marker_size": "marker size in meters",
        "help_objects": "export objects to JSON/CSV",
        "help_obj_normals": "write normals to OBJ (larger file)",
        "help_no_uv": "do not write UVs to OBJ",
        "help_no_colors": "do not write vertex colors to PLY",
        "help_textures": "export all .pct textures",
        "help_full": "full scene: terrain + all objects/trees/buildings from shared.pak",
        "help_terrain_only": "terrain and water only (no objects)",
        "help_shared_pak": "path to shared.pak (default: search next to the level)",
        "help_editor_pak": "path to editor.pak (needed for object textures)",
        "help_no_textures": "do not embed object textures",
        "help_object_limit": "limit the number of object groups (for testing)",
        "help_print": "3D-print STL: terrain extruded into a solid base",
        "help_print_width": "printed model width in mm (default 200)",
        "help_base_thickness": "base thickness under the lowest terrain in mm (default 3)",
        "help_print_no_objects": "do not include objects (terrain only, watertight)",
        "help_print_quality": "print quality: balanced=step 4 (2 m, default), draft=8, high=2, max=1",
        "help_print_full_objects": "in balanced/draft keep full tree meshes instead of print proxies",
        "help_split": "split the print model into NxM tiles (for example 2x2 or 3x2)",
        "help_split_gap": "gap between tiles in mm (default 0.2)",
        "error_split_format": "Error: --split works only in --print (STL) mode.",
        "error_split_value": "Error: --split must be in NxM format, e.g. 2x2.",
        "print_split_start": "  Splitting into {nx}x{ny}: {count} tiles, gap {gap:g} mm ...",
        "print_split_done": "  Done: {count} tiles. Manifest: {manifest}",
        "print_start": "  3D-print STL: width {width:g} mm, base {base:g} mm, step {step} ...",
        "print_objects_mode": "  Objects: {mode}",
        "print_objects_proxy": "trees/bushes replaced with print proxies",
        "print_objects_full": "full object meshes",
        "print_objects_none": "objects disabled",
        "print_scale": "  Print scale: {scale:.5f} mm/m, map size {w:.1f}x{d:.1f} mm",
        "print_height": "  Model height: {h:.1f} mm (terrain + base)",
        "print_done": "  Print model ready: {path}",
        "error_print_format": "Error: --print works only with -f stl.",
        "help_preview": "write a PNG terrain preview",
        "help_no_preview": "do not write a PNG preview",
        "help_verbose": "verbose output",
        "help_check": "check environment, dependencies and detected pak files",
        "help_open": "open the output folder after conversion",
        "help_lang": "interface language: ru or en",
        "help_reset_config": "reset config and ask for language again",
        "help_version": "show version",
    },
}


def set_language(lang: str) -> None:
    global LANG
    LANG = "en" if str(lang).lower().startswith("en") else "ru"


def t(key: str, **kwargs: Any) -> str:
    table = TEXTS.get(LANG) or TEXTS["ru"]
    text = table.get(key) or TEXTS["ru"].get(key) or key
    try:
        return text.format(**kwargs)
    except (KeyError, ValueError):
        return text


def load_config() -> dict[str, Any]:
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(cfg: dict[str, Any]) -> None:
    try:
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    except Exception:
        pass


def init_language(requested: str | None = None, reset: bool = False) -> str:
    cfg = {} if reset else load_config()
    if requested:
        cfg["language"] = "en" if requested.lower().startswith("en") else "ru"
        save_config(cfg)
        set_language(cfg["language"])
        return cfg["language"]
    lang = cfg.get("language")
    if lang in ("ru", "en"):
        set_language(lang)
        return lang
    if sys.stdin.isatty():
        print(t("choose_lang_title") if LANG in TEXTS else "First run: choose language")
        # The console may not be initialized yet; print both options.
        print("  1) Русский\n  2) English")
        try:
            raw = input("Number / номер [1]: ").strip().lower()
        except EOFError:
            raw = "1"
        lang = "en" if raw in ("2", "en", "english", "английский") else "ru"
    else:
        lang = "ru"
    cfg["language"] = lang
    save_config(cfg)
    set_language(lang)
    return lang
