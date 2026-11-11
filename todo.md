# GEL TODO

How to add a task: add a line `- [ ] Short description` under the matching section
(or under Inbox if unsure). Mark it done by changing `[ ]` to `[x]`.
You can also ask me: "add to todo: <task>".

## Visual GEL language
The "game" folder will be in a seperate locaiton. The entire editor will be run through a start.bat file. It will save and load a folder locaiton where the game-specific fils will be. There will also be a play button that will build and run the game (it will setup the live-editing bridge too).
The tile editor now opens Tiled maps, paints tiles, and edits existing objects' positions and variant parameters. Still needed: expanding map and tileset editing (including tile properties), and surfacing more hardware constraints as the editor grows.

I also want to add a new feature into the GEL language which is C-like unions. This is useful for reusing the objects and variants' variables without adding new bespoke ones. A nice memory saver. With the objects themselves, I may use a scratchpad of and array of 6 u16s (which i'm thinknig could be interpereted into f16,i8,u8s with some smart coding - not sure if htat's possible). Every entity would effectively have 12 bytes of custom variables.

- [ ] Filter add-block choices by the current context and expected expression type; the current menu offers the same statement kinds in every insertion slot.
- [ ] Generate the menu from the real grammar and type rules (`snes_gel.lark` and the compiler's type table), so the editor and the compiler can't disagree. For example, an f16 value times an integer is not offered when the compiler doesn't support it.
- [ ] Expand source-to-block editing beyond the supported subset; unsupported source is currently preserved read-only.
- Scope note: this guarantees no syntax errors (and can prevent type errors), not logic errors or hardware limits. Compiling to GEL means no SNES VM is needed.

## Entites
Finish the semantics of the entity capability flags. The flags are declared and editable, and Contactable is used by contact dispatch. Movable, Gravity, and Ghost still need their behavior wired into movement and collision.
- Movable entities may receive movement intent from a controller or behavior; there is no separate Inputable flag.

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

### Contact events
The entity table already tracks touching pairs and dispatches enter, stay, and exit events in both directions. Finish connecting the Contact Editor's configured directed interactions and handler scripts to this runtime; the editor currently warns that exported handlers are not automatically connected.

## Metasprite Editor
There should be an option to save and load palletes. We may need to look into how the SNES loads palletes.

## GEL Language
With bits, instead of needing to put 0x0X have somethign like 00001010 or 0000000000000000 for 16 bits. Still have the option of 0x0X.

## Level editing
- [ ] Finish entity generalization: add per-type metasprite/clip mapping (update_anim still uses the player's clips, PLAYER_MS_ANIMS), and give real entity types (enemies, pushables) the physics capability.
- [ ] Separate PNG tile art for gems (they currently reuse the bullet tile)
- [ ] Support multiple layers (background, foreground, collision)

## Build / tooling
- [ ] Move the level colour-to-tile mapping out of png_to_snes_tiles.py into a config file (owner: Pixel Brownie)
