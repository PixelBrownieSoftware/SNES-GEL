# Getting started with GEL

GEL is a small C-like language that compiles to 65C816 assembly for the SNES. The compiler
(Python) turns `game.gel` plus your assets into a `.sfc` ROM using the Asar assembler.

## Requirements

- Windows, Linux or macOS. The compiler and launcher (`dev.py`) are plain Python. Windows is the
  tested platform; `dev.bat` and `dev.sh` are thin wrappers around `dev.py`
- Python 3.10+ with `pip install lark watchdog pillow`
- Asar: Windows uses the bundled `tools\asar\asar64.dll`. On Linux/macOS build Asar from
  https://github.com/RPGHacker/asar as a shared library and put `libasar.so` / `libasar.dylib` in
  `tools/asar/` (or point `GEL_ASAR_LIB` at it)
- Mesen 2 (https://www.mesen.ca): put it in `tools/mesen/` (`Mesen.exe` on Windows; not stored in git),
  or set `GEL_MESEN` to its path
- Optional: Go 1.23+ to build smconv for sound (`tools\setup_smconv.bat` or `tools/setup_smconv.sh`, see [SOUND.md](SOUND.md))
- Optional: the VS Code extension in `snes-gel-vscode\` (syntax highlighting)
- Optional: [Tiled](https://www.mapeditor.org) for level editing

## Run the example

```
dev.bat                 :: Windows; builds game\ and launches Mesen with hot reload
./dev.sh                # Linux/macOS, same thing
python dev.py mygame    # any platform, another project folder
python dev.py --no-emulator   # build and watch without launching Mesen
```

`dev.py` checks requirements, builds the ROM, starts Mesen and then watches the project.
Saving a `.gel`, `.png`, `.metasprite.json`, `.tmj`, `.tsj` or `.json` file rebuilds the ROM and
reloads it in Mesen. `persist` variables and the entity table survive the reload.

Build only:

```
python compiler\build.py game              :: debug build (hot-reload hook written)
python compiler\build.py game --release    :: release ROM, no hot-reload state restore
python compiler\build.py game --no-opt     :: skip the peephole optimiser
```

Output goes to `<project>\build\game.sfc` (plus `game.sym` and `game.layout`).

## Project layout

```
mygame/
  game.gel          main source (required); must define init_game()
  gel.json          ROM header and sound options (optional)
  assets/           *.png, level*.tmj + *.tsj (Tiled), *.metasprite.json
  sfx/              audio
  build/            generated, safe to delete
```

`gel.json`:

```json
{
  "header": { "title": "My Game", "rom_size_kb": 128, "sram_kb": 0, "region": 1 },
  "sfx_echo": { "enabled": false, "delay": 3, "feedback": 40, "volume": 50, "channels": [8] }
}
```

## Assets

- Every PNG in `assets/` is converted to SNES tiles and a palette in `build\`, and exposed to
  GEL through the generated `build\assets.gel` (`#include "assets.gel"`).
- Levels: `level*.tmj` (Tiled) is converted to a tile map, a collision map and an object table.
  The tileset is `terrain.tsj`; a tile is solid when its `solid` property is set. If no `.tmj`
  exists the legacy `level.png` (colour to tile mapping) is used.
- Slopes: add `slope_start` and `slope_end` properties (int or float, in pixels) to a tileset
  tile. `slope_start` is the surface Y at the tile's left edge, measured down from the tile's top;
  `slope_end` is the surface Y at the right edge. Both may lie outside 0-8 (-16 to 16) so
  adjoining ramp tiles meet across tile rows, and the change across one tile must be under 16
  pixels. Use fractions in eighths (e.g. 2.625 and 5.375) to build a straight ramp whose
  gradient is not a whole number of pixels per tile, such as 1-in-3. Horizontal flips reverse the
  endpoints; slopes cannot be vertically or diagonally flipped. The player walks onto a slope
  that blocks them at foot level by stepping up to 8 pixels (plain solid tiles stay walls) and
  walks down by snapping to the ground within 4 pixels. A collision-layer tile remains a
  full solid block. In Mesen, enable collision bounds with F2 and the debug overlay with F3 to
  draw nearby collision profiles: blue rises toward the right, red falls toward the right. Only
  profiles within 64 pixels of the player are drawn, and only in platformer mode.
- Metasprites are authored with the editor in `tools\metasprite-editor` and saved as
  `*.metasprite.json`.

## Level editing with Tiled

1. Open `game\assets\puzzleplatform.tiled-project`.
2. Edit `level.tmj` and save. The bridge detects the `.tmj`/`.tsj` change, rebuilds and
   reloads the running ROM.
3. Object layer entries become the `LEVEL_OBJECTS` table (`OBJ_*` types; see the `obj_*`
   helpers in `game\game.gel`).

## Compiler optimisations

- Constant folding and small-function inlining.
- Peephole pass (`compiler\gel_peephole.py`); disable with `--no-opt` when debugging.
- Table stores with a simple value (`Entities[i].x = v`, `Entities[i].x += 1`) keep the slot
  offset in Y instead of saving the value on the stack, and compound assignments compute the
  offset once. Values that read other tables, call functions, shift or multiply use the general path.
- Metasprite tiles are deduplicated (including flipped copies) and blank cells are dropped.
  Level and font tiles are not deduplicated, because their tile numbers are fixed by the tileset.

## Incremental builds

The sound bank and the converted assets (PNGs, Tiled levels, metasprites) are cached in
`build\cache.json`, keyed by a content hash of every input, the converter scripts and the tool
versions. Changing a tile reruns only the assets stage; changing a sound reruns only the sound
stage. A cached stage is reused only if its output files still exist and match their recorded
hashes, and a failed conversion never updates the cache. GEL compilation and Asar always run.
Use `--no-cache` to force a full rebuild, or delete the `build` folder.

## Tests

`py -3 -m pytest tests` runs the compiler, peephole, level-converter, cache and end-to-end build
tests. CI runs them on Windows (`.github\workflows\ci.yml`).

## Troubleshooting

- "python not found": the Windows Store `python` alias can be a broken stub. Install Python from
  python.org so `py -3` works.
- Build fails: the error shows the GEL file and line. The previous ROM is left untouched.
