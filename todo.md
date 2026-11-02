# GEL TODO

How to add a task: add a line `- [ ] Short description` under the matching section
(or under Inbox if unsure). Mark it done by changing `[ ]` to `[x]`.
You can also ask me: "add to todo: <task>".

## Visual GEL language (idea)
- [ ] A visual, node-based editor similar to Scratch or Unreal Blueprints, where you arrange code and see the links between code and variables. Syntax errors can't happen because only valid next steps are offered, and invalid operations (for example multiplying a fixed-point value by an integer when that isn't supported) simply can't be connected. It might run on a SNES VM; what that would need is still open.

## Level editing
- [x] Support Tiled (.tmx/.tmj) maps as the level source instead of level.png (.tmj done; level*.tmj overrides the PNG)
- [x] Converter: read the tile layer and a collision layer or per-tile "solid" property from Tiled
- [x] Dynamic entity table (game/entities.gel): created from Tiled objects, Gem and Bouncer work
- [ ] Move the player into the entity table (physics and collision shared by all entities). Done: all player state, animation state, and shared hit_*/move_x/move_y/update_anim that take an entity slot; entities with MODE_FLAG_PHYSICS run entity_physics (gravity, ground, anim). Left: per-type metasprite/clip mapping (update_anim still uses the player's clips), per-entity hitbox size, and giving real entity types (enemies, pushables) the physics flag
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
