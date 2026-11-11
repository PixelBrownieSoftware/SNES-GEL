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
  gel-project.json  GEL editor project marker
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

Set `header.rom_mode` to `"lorom"` (default) or `"hirom"`. Set `header.fastrom`
to `true` (default) for FastROM or `false` for SlowROM; a physical FastROM
cartridge must support the faster access. HiROM stores GEL's 32 KiB level and
sound chunks in the upper half of 64 KiB banks, so the resulting ROM may be
larger than a LoROM build. ExLoROM and ExHiROM are not yet supported.
Restart Mesen after changing the mapping or built ROM size: hot reload cannot
replace the emulator's cartridge mapping or resize its ROM.

`gel-project.json` identifies a folder as a project that can be opened in the Visual GEL editor:

```json
{
  "format": "snes-gel-project",
  "version": 1,
  "name": "My Game"
}
```

In the Visual GEL editor, choose **Open Project** and select this file. The editor remembers the
last selected project in user-local settings; this path cache is not stored in the game folder.

## Assets

- Every PNG in `assets/` is converted to SNES tiles and a palette in `build\`, and exposed to
  GEL through the generated `build\assets.gel` (`#include "assets.gel"`).
- Levels: `level*.tmj` (Tiled) is converted to a tile map, a collision map and an object table.
  The tileset is `terrain.tsj`; a tile is solid when its `solid` property is set. If no `.tmj`
  exists the legacy `level.png` (colour to tile mapping) is used.
  Widths are multiples of 32 from 32 to 4064 tiles; heights are 32 to 4095 tiles.
  These are coordinate limits, not a guarantee that every combination fits: all levels,
  sound and other assets share the available ROM banks. Larger maps use uncompressed
  32×32 chunks, with 2 bytes per tile for the map and 1 for collision, padded to full
  chunks. Thus a shorter map can be wider for the same ROM budget, and vice versa.
  For example, 1024×96 tiles requires 10 logical 32 KiB banks including metadata;
  4064×32 requires 13. HiROM uses the upper 32 KiB of each 64 KiB bank, so its ROM
  image may be larger. A build reports how many banks are needed if it runs out.
  Small 32- and 64-wide maps keep their original one-bank layout. The example game's
  streamer scrolls large maps in both directions and keeps the camera within loaded VRAM.
  The optional stdlib modules `level-data.gel`, `level-collision.gel` and `level-stream.gel`
  provide packed object/collision access, solid/slope probes and streaming. Include
  `snes.gel` and `assets.gel` first, then `level-data.gel` before the other modules.
  The streamer expects BG2's map at VRAM word `$4000`, 8×8 tiles, and a 256×224 viewport.
  Call `level_stream_init(cam_x, cam_y)` while blanked, then call
  `level_stream(cam_x, cam_y)` early in VBlank, before setting scroll. Clamp desired camera
  positions to the world bounds and then to loaded coverage using `level_stream_clamp_x()`
  and `level_stream_clamp_y()`. The streamer uses direct-page scratch `$04`–`$09`; don't use
  it concurrently with code using those addresses. Its VBlank time checks assume NTSC timing.
  `collision_code_at` treats out-of-bounds as solid; `solid_at`, `solid_h`, and `solid_v`
  interpret the packed slope format (strip endpoints are inclusive). Camera following,
  movement, physics, and object spawning remain game-specific.
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
2. Resize or edit `level.tmj` in Tiled and save. The GEL level editor can paint these maps;
   resizing is done in Tiled. Large maps are displayed at a smaller scale in the editor.
   The bridge detects the `.tmj`/`.tsj` change, rebuilds and
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
