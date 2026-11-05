# GEL TODO

How to add a task: add a line `- [ ] Short description` under the matching section
(or under Inbox if unsure). Mark it done by changing `[ ]` to `[x]`.
You can also ask me: "add to todo: <task>".

## Visual GEL language (idea)
A guided, block-based way to write GEL that looks like GEL code but can't have syntax errors. Think of it as an inverse parser: instead of the programmer writing text and the parser rejecting mistakes, the editor tracks what is valid at each point and only offers that.
There will also be a way of knowing where in memory the struct tables will go. With setting variables and passing variables into functions the thing will look into everything that has an appropriate variable type. Including struct_tables. I am thinking of when a new variable is created (global) there will be a JSON that caches them.
Like the metasprite editor, the visual GEL will use a python server and JSON to store code. You can convert a GEL file into a JSON file and vice versa.

- [ ] Blocks are laid out top to bottom, like the lines of a GEL function. A plus icon at the bottom of a block list opens a menu of only the blocks that are valid there (statements, or expressions that fit the expected type). Nested blocks (if, while, for_each) have their own plus.
- [ ] Generate the menu from the real grammar and type rules (`snes_gel.lark` and the compiler's type table), so the editor and the compiler can't disagree. For example, an f16 value times an integer is not offered when the compiler doesn't support it.
- [ ] Emit normal `.gel` text, and load existing `.gel` files back into blocks by parsing them, so users can switch between text and blocks and learn the language from the blocks.
- [ ] Prototype on a small subset (variables, if, while, `Entities.for_each`, table fields, a few `std_*` calls), then try loading `player.gel` to see how much real code it covers. Inline `asm { }` stays text-only.
- Scope note: this guarantees no syntax errors (and can prevent type errors), not logic errors or hardware limits. Compiling to GEL means no SNES VM is needed.

## Entites
Make it so that all entites have flags such as:
- Contactable: When an entity hits something, things happen. It's when an entity hits another entity. As long as this is on, an engity can react to any other entity, stationary or not. This is different from dispolacement collisions (like what entities have with tiles).
- Movable: Entities can move and have a velocity of where they will go. In an entity without gravity, they can move wherever they want. Without input, the movable can have no thought of its own. They can only be moved by something else i.e. another entity or a object that represents a force vector.
- Gravity: Entities obey the laws of gravity. They can fall. Ones that don't can potentially fly. Flying and swimming would pretty much be the same mechanic.
- Ghost: Entities can go through walls and other entites (in a physicis way, not a contactable way). Ghosts can still contact entities and trigger whatever the event is.
- Inputable: Entities can be controlled by an input vector.
With the contactable, they may be assigned a resuable interaction type/handler when in contact with another entity. My strategy to looking at this is "Divide and conquer". Instead of thinking in terms of bullets, players, throwables, coins - I think in terms of their very simple components.
The flags need to be able to be combined with each other in every single way.
A bullet is a contactable entity that moves, but dosen't obey gravity.
A player is a contactable entity that obeys gravity. I may need some more flags if need be.
A current is a contactable entity that can't move and dosne't obey the law of gravity.

Codewise, the rules will be strictly established, but will expect to have a large amount of combinations.
With entities, I want to make it so that the ones that can move are able to be controlled by an input represented by flags (i.e. up, down, left, right). They will have no idea where the input comes from. In global memory (a persist varaible), there will be a special index which whatever entity it indexes on, controller inputs can be used as its input. This is important for behaviours.
For example whether an entity will go left or right on it's own accord will be dependent on a flag being set to true. This flag can be turned on by behaviours or input from a controller. The flag gives teh ability to have an object change its velocity. There will be a flag for up and down, another for left and right. There will be a flag if up/down and left/right are pressed as well.
horz_pressed -> 0 (this representing if either horizontal buttons are being pressed)  horiz_flag -> 0 (1 and 0 being left and right). It will be impossible to have two opposing keys pressed at once.
The movement flags are basically:
if (horiz_pressed){
    if (test_bit(horz_flag)){
        vx -= speed
    } else {
        vx += speed
    }
}
left_dir is a flag that can be triggered by behaviours or the player's input. The engine has no idea or care about who pressed it.
For the current game I'm working on, there wont' be health.

There could be an entity editor which it can be assigned a metasprite (animaddr). They will have an ID that can be set in a tiled editor.

### Contact events
When two entities touch, send a contact event to the touched entity using `entity_on_contact(self, other)`. For example, when a player touches a coin, the coin's handler can increment the coin counter and remove the coin. I want to support this without writing lots of bespoke code.

- `entity_on_contact(self, other)` runs once when a pair starts touching.
- `entity_on_stay(self, other)` runs on each update while the pair remains touching.
- `entity_on_exit(self, other)` runs once when the pair stops touching.

The direction I am taking this is what Alan Kay originally meant by object-oriented programming: messages. A messaging matrix like Clickteam Fusion could define what happens when Entity A touches Entity B, and separately what happens when Entity B touches Entity A. Each directed interaction can change global or entity variables, such as velocity, and may include conditionals.

Store each touching pair of entity instances (the numbered `Entities` slots) once:

```gel
struct_table CollidingEntities [X] {
    u8 idself;
    u8 idcolliding;
}
```

Check entity pairs for new overlaps each update. Use the contact table to call stay handlers only for pairs already touching and to detect when a pair separates. A directed interaction runs only when it is configured and its recipient is Contactable. Call it as `entity_on_*(self, other)`, with `self` as the recipient and `other` as the initiator; when both directions are configured, call A -> B before B -> A. If either entity is destroyed while touching, dispatch its configured exit interactions before removing the pair or reusing its slot.

The messaging actions will be GEL functions that can directly alter variables.
There will be a centralised function that is generated which would be
```gel
fn entity_on_contact(u8 self, u8 other) {
    u8 self_type = Entities[self].type;
    u8 other_type = Entities[other].type;

    switch (self_type) {
        case OBJ_BULLET:
            switch (other_type) {
                case OBJ_CHARA:
                    bullet_to_chara_contact(self, other);
                    break;
            }
            break;
    }
}
```
This would be in a "contact.gel" file.

## Metasprite Editor
With a metasprite, with each layer, there may be an option to make it so that it can use a different image i.e. the front arm layer using a sleeved as well as a sleveless image. They can be toggled.
Also make it so that with each frames layers can be be rearranged or hidden i.e. hair behind a character's head which is made invisible if it's not seen.
Make it so that each layer has its own anchor (which can unlock a lot of other possiblities). With the layer based anchor, you will be able to see it in the editor. You cannot remove or add a layer. You can shift them. You cannot change the quantity of frames.
I am thinking that what this saves in assets.asm is the base sprite, plus alterations. For example if I do lady 2 on player's metasprite, it will have instructions for changing the hair on these frames which state so. Or change position of this arms. It's pretty much: [Original] +  [Overwrites]
I'll call this feature Metasprite remix/overwiting (remix sounds cooler).

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
