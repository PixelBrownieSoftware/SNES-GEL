GEL Metasprite Editor (layers + animation)

Windows: run start.bat to serve the editor locally and open it in your browser. The default assets
folder is game/assets; pass another assets folder as an argument to start.bat to use a different
project. The server reads and updates metasprite-project.json, which lists the separate
<name>.metasprite.json files. It also discovers PNG images and converts BMP images to PNG beside
the originals. RGB BMP conversion quantizes to 15 colors and reserves palette index 0 as
transparent; existing alpha is preserved, but an opaque RGB BMP does not identify a transparent
background, so its background stays opaque. Review quantized output if exact colors matter.

Alternatively, open index.html directly in Chrome/Edge and use the browser folder picker. This
mode does not provide the server's project catalog or automatic BMP conversion.

WORKFLOW
1. Use New project to start a blank metasprite or copy an existing project (including the current
   project), enter its new name, and Save project to create a separate JSON file. A copy reuses the
   original layer images, frames, and clips; edits to those shared source images affect both
   projects. For a blank project, choose images from the asset list (or add PNG files), place at
   least one visible tile, and save. With the
   server, choose an existing project from the Project menu (the first listed project loads at
   startup); the server keeps the project manifest updated.
   In browser-folder mode, pick your game's assets/ folder; if it holds a
   <name>.metasprite.json it loads. Every layer PNG should be an indexed (palette) image, colour 0
   = transparent.
2. Layers (left): back to front, last layer draws in front. Each layer has an
   OBJ palette slot (P0-P7). Different skin tones = same head PNG with another
   palette, in another slot. Select a layer to view its 16 indexed colors; index 0 is transparent.
3. Pick tiles on the sheet (click, drag, or double-click a whole cell; set the
   cell size next to "Fill from selection"). Use "Fill from selection" to place
   a whole cell at once, or Stamp to place tiles by hand.
4. Frames (under the canvas): + Blank, Duplicate, reorder, delete. Onion skin
   shows the previous frame. "Layer -> all frames" copies parts that never move
   (head, hair, torso) into every frame, so you only edit the moving layers.
5. Select tool: drag a box (or click a tile); Shift adds. When you let go, the
   editor switches to Move. Move tool: drag the selected tiles (8 px grid);
   pressing on empty space returns to Select. Arrows nudge (Shift = 1 px). Flip H / Flip V (H / V keys) mirror the
   selection and set the hardware flip flag, so a back arm can reuse the front
   arm's tiles at no ROM cost. Delete erases.
6. Anchors tool: click the canvas to place the sprite anchor (the mirror axis
   used for left/right facing) and hitbox anchor (click the tool again to
   switch). The red box is the hitbox; set its size in the footer.
7. Clips (right): a clip is a list of (frame, ticks). 1 tick = 1/60 s. Press
   Play to preview, optionally mirrored.
8. Save project writes <name>.metasprite.json into the folder (new layer PNGs
   are copied there too). With the bridge running, the ROM rebuilds.
   Without a folder it downloads the JSON with the art embedded.
   Reload art re-reads the PNGs after you re-export from Aseprite.

WHAT THE BUILD DOES (compiler/metasprite_pack.py)
- Drops transparent tiles, merges overlapping tiles of layers that share a
  palette slot into one sprite, and de-duplicates tiles across all frames and
  layers, including horizontally / vertically flipped copies.
- PNGs used by a project are not packed a second time as standalone assets.
- Prints a size report; the build fails if assets overflow the 32KB ROM.
- The Optimisation report in the editor shows the same numbers live:
  sprites per frame (of 128), unique tiles (of 256), sprites per scanline
  (of 32) and ROM bytes.

USING IT IN GEL (name "player" -> prefix PLAYER_MS_)
  std_load_palette(8, PLAYER_MS_PAL0, 16);          // one per used slot
  std_load_vram(0x0000, PLAYER_MS_TILES, PLAYER_MS_BYTE_LEN);
  std_draw_frame(PLAYER_MS_FRAMES, frame, x, y, attr, 0);
      // attr: priority + OAM_HFLIP to face the other way (whole frame mirrors)
  Offsets: PLAYER_MS_OFF_X_NORMAL, PLAYER_MS_OFF_X_MIRROR, PLAYER_MS_OFF_Y
  Clips:   PLAYER_MS_ANIMS, PLAYER_MS_ANIM_<CLIP>, PLAYER_MS_FRAME_<FRAME>
      frame = std_anim_frame(PLAYER_MS_ANIMS, clip, step);
      hold  = std_anim_ticks(PLAYER_MS_ANIMS, clip, step);
      step  = std_anim_next(PLAYER_MS_ANIMS, clip, step);  // wraps if looping
  Keep a tick counter in the game; advance `step` when it reaches `hold`.
  A second character at another VRAM offset: pass tile_base to std_draw_frame.

NOTES
- Old gel-metasprite-v1 projects still load (as one layer, one frame). The
  v1 "Save GEL config" export was replaced by the packer above; existing
  generated *_metasprite.gel files keep working.
- Mesen F2 shows the player collision box overlay (tools/mesen_gel_hook.lua).
- Ctrl+Z undoes edits; "," and "." switch frames.
- metasprite-pack.js holds the size estimate and mirrors the Python packer.
