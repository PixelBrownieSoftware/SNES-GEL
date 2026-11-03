# GEL TODO

How to add a task: add a line `- [ ] Short description` under the matching section
(or under Inbox if unsure). Mark it done by changing `[ ]` to `[x]`.
You can also ask me: "add to todo: <task>".

## Visual GEL language (idea)
A guided, block-based way to write GEL that looks like GEL code but can't have syntax errors. Think of it as an inverse parser: instead of the programmer writing text and the parser rejecting mistakes, the editor tracks what is valid at each point and only offers that.
- [ ] Blocks are laid out top to bottom, like the lines of a GEL function. A plus icon at the bottom of a block list opens a menu of only the blocks that are valid there (statements, or expressions that fit the expected type). Nested blocks (if, while, for_each) have their own plus.
- [ ] Generate the menu from the real grammar and type rules (`snes_gel.lark` and the compiler's type table), so the editor and the compiler can't disagree. For example, an f16 value times an integer is not offered when the compiler doesn't support it.
- [ ] Emit normal `.gel` text, and load existing `.gel` files back into blocks by parsing them, so users can switch between text and blocks and learn the language from the blocks.
- [ ] Prototype on a small subset (variables, if, while, `Entities.for_each`, table fields, a few `std_*` calls), then try loading `player.gel` to see how much real code it covers. Inline `asm { }` stays text-only.
- Scope note: this guarantees no syntax errors (and can prevent type errors), not logic errors or hardware limits. Compiling to GEL means no SNES VM is needed.

## Metasprite Editor
- [x] Use a local Python server and Windows launcher so the editor opens at a defined assets folder without repeatedly choosing files.
- [x] Keep each metasprite in its own JSON and list projects in `metasprite-project.json`.
- [x] Discover PNG assets in the folder and make them assignable as layers; convert BMPs to PNG automatically.
- [x] Assign OBJ palette slots per layer (P0-P7; already supported by the editor).
- [x] Create new metasprite projects from the editor and view indexed PNG palettes.

## Tiles
Add in support for sloped tiles where the player can walk up them, a bit like sonic. Have multiple fixed degrees. I'm sure there's a way to calculate such tiles.

## GEL Language
With bits, instead of needing to put 0x0X have somethign like 00001010 or 0000000000000000 for 16 bits. Still have the option of 0x0X.

## Level editing
- [x] Support Tiled (.tmx/.tmj) maps as the level source instead of level.png (.tmj done; level*.tmj overrides the PNG)
- [x] Converter: read the tile layer and a collision layer or per-tile "solid" property from Tiled
- [x] Dynamic entity table (game/entities.gel): created from Tiled objects, Gem and Bouncer work
- [ ] Move the player into the entity table (physics and collision shared by all entities). Done: all player state, animation state, and shared hit_*/move_x/move_y/update_anim that take an entity slot; entities with MODE_FLAG_PHYSICS run entity_physics (gravity, ground, anim). Per-entity hitbox size (w, h) is also done. Left: per-type metasprite/clip mapping (update_anim still uses the player's clips, PLAYER_MS_ANIMS), and giving real entity types (enemies, pushables) the physics flag
- [ ] Separate PNG tile art for gems (they currently reuse the bullet tile)
- [x] Taller maps: any height up to the 32 KB level bank (~162 rows at 64 wide, ~325 at 32 wide), streamed row by row. Still TODO: widths over 64, multi-bank levels
- [x] Define the tileset (terrain.png) as a Tiled tileset so tile IDs stay in sync with the game (game/assets/terrain.tsj)
- [ ] Support multiple layers (background, foreground, collision)
- [x] Support entity or object layers (spawn points, entities, pickups) exported to GEL data (done: OBJ_* types, LEVEL_OBJECTS table, obj_* helpers in game.gel)
- [x] Hot reload the level when the Tiled file is saved (gel_bridge.py now watches .tmj/.tsj/.json)
- [ ] Keep level.png working as a fallback (colour-to-tile mapping is in LEVEL_COLOR_TO_TILE)

## Build / tooling
- [ ] Move the level colour-to-tile mapping out of png_to_snes_tiles.py into a config file (owner: Pixel Brownie)
- [x] Make the build script work without the Windows Store python alias (dev.bat picks `py -3` first)
- [x] Add an asset converter test (tests/test_level_pack.py)


## Done
- [x] Level PNG read by RGB colour instead of palette index
- [x] Fix converter crash on Windows console (non-ASCII characters in print)
