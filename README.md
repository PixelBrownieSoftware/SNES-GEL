<p align="center"><img src="snes-gel-vscode/icon.png" alt="GEL logo" width="128"></p>

# GEL: a language for SNES games

GEL is a small C-like language that compiles to 65C816 assembly and builds a ready-to-run
SNES ROM (`.sfc`). It has fixed-point maths, struct tables (structure-of-arrays for fast entity
code), hardware register access, a standard library for sprites, text, scrolling and sound, and
**live hot reload**: save a source file or asset and the running emulator picks up the new ROM
without losing game state.

## Why GEL exists

In late October I wondered what it would look like to build something like Naughty Dog's GOAL
(Game Object Assembly Lisp) for the SNES. C compilers for the SNES are readable, but the code they
produce tends to be slow. GEL tries to bridge that gap: a language made for the SNES that doesn't
make you read obscure code, with inline assembly there when you want it.

GEL was built with AI assistance: I started it with Gemini and then continued with GitHub Copilot.
I'm also considering a Mega Drive version, but I don't know when that will happen.

![The example platformer running in Mesen](docs/media/game.gif)

```
struct_table Entities[16] { u16 x; u16 y; u8 type; }

fn init_game() {
    std_snes_init();
    u8 first = Entities.create();
    Entities[first].x = 100;
    while (1) {
        Entities.for_each(e) { Entities[e].x += 1; }
        std_wait_vblank();
    }
}
```

## Features

- C-like syntax: `u8/i8/u16/i16/f16`, enums, structs, `switch`, inline `asm { }`, `#include`, `#define`
- Struct tables with `create()`, `free()` and `for_each()` for entity pools
- Optimising compiler: constant folding, function inlining, peephole pass
- Asset pipeline: PNG to SNES tiles/palettes, flip-aware deduplicated metasprites, sound via SNESMOD ([docs/SOUND.md](docs/SOUND.md))
- Tiled (`.tmj`) level import: tile map, collision, object layer, streamed row by row
- Hot reload through Mesen, including level and asset edits
- Metasprite editor (`tools/metasprite-editor/`): layered, animated sprites in the browser
- VS Code extension with syntax highlighting (`snes-gel-vscode/`)

The extension includes an optional **SNES-GEL Prototype** dark color theme. Select it with
VS Code's **Preferences: Color Theme** command after installing or running the extension.
Its explicit token colors apply only to GEL; unspecified colors, including comments, use
VS Code's dark defaults. Editing the extension source does not update an installed VSIX;
repackage and reinstall the extension, then reload the window to use the latest changes.
The theme uses semantic highlighting for declared primitive variables, table fields, enum
members and defines, including references in other workspace GEL files. Keep VS Code's
semantic highlighting enabled; ambiguous declarations across files may remain uncolored.

## Contact editor prototype

Open [the contact editor](tools/contact-editor/index.html) in a browser to configure directed
entity-type interactions for enter, stay and exit. Each cell names an existing GEL function
that accepts `(u8 self, u8 other)`. Save or load a JSON configuration and export the generated
`contact.gel`. Reverse interactions require separate rows. This is an authoring prototype:
the exported file must be included in the game and its dispatchers connected to the contact
runtime manually; it does not implement collision detection or activate the existing dispatcher.

With the shared server, recipient choices come from objects marked Contactable in `objects.json`;
the other participant can be any defined object. Use **Refresh objects and symbols** after edits
in the object editor. Existing unknown names are retained as unresolved choices and must be
replaced before saving with the server; they are not silently renamed. Classes without generated
`OBJ_*` constants are flagged until a level containing them is built.

Handler blocks use the shared visual GEL modules in `tools/visual-gel/`. Symbol slots open a
searchable picker; choosing a symbol replaces the slot, while literals and manual expressions
can be entered separately. Undo/redo buttons (or Ctrl+Z and Ctrl+Shift+Z outside text fields)
cover interaction and block edits. Online saves and exports are checked against the compiler's
GEL grammar; this checks syntax, not whether symbols and types will compile or work in-game.
Offline exports remain available but cannot receive this grammar check.

Value pickers show scanned `#define` replacements. For a known assignment or variable type,
simple literal defines are labelled when they fit and disabled when they are fractional for
an integer or outside its range. Complex defines remain selectable but are marked unknown;
build to check them. Integer defines assigned to `f16` are raw bits, not scaled whole numbers.

## Visual GEL editor

Open **Visual GEL editor** from the shared editor home page. Choose a project `.gel` file and
function to inspect its real source. Assignments, declarations, expressions, returns and control
flow headers are editable, including inside existing nested blocks. Existing control flow appears
as nested visual blocks rather than raw braces; it stays in place so its comments, assembly and
other source are preserved. Its conditions, loop headers
and case values can be edited without rewriting its body. New `if`, `while`, `for_each`, `switch`
and other statement blocks can be nested and added at the end of the function or inside existing
control-flow bodies. Header expressions containing comments and inline assembly remain read-only.
Loop iterator references can be entered manually
with **Use value**. Undo and redo apply to the currently selected function.

Saving patches only edited statements and additions, preserving the rest of the file. The editor
checks the resulting function against the compiler grammar and rejects a save if the file changed
since it was opened. It does not validate names, types or game behavior: build the game after
editing. Generated `contact.gel` is read-only here; edit it through the contact editor instead.
Both editors use the same visual block and symbol-picker modules.
Use the **+** control at an insertion point to open searchable statement choices; Escape
closes the choices. This control is shared by the visual GEL and contact editors. After
updating the editor, save any work and reload the browser page to load the new interface;
the function editor's Reload button only reloads the project file.
To negate an expression or condition, select one or more adjacent values and choose
**! selected**. The editor wraps the selected expression in `!(...)`, so NOT applies to
the whole selection. Click the displayed **!** to remove it. Switch case labels only
accept constants, so they do not offer this control.

Use **Edit parameters** on a function to add, remove, rename or change parameter types.
The same dialog sets an optional return type. Annotated returning functions appear in
the **Returning functions** value picker for assignments and conditions, with editable
argument slots. Return-type changes preview known calls but do not rewrite them; check
their behavior after changing the type. Untyped functions retain the legacy `u16` call type.
Save or undo pending function edits first. Provide one argument expression for each new
parameter; it is inserted at every existing call. Preview lists affected project-root
`.gel` files and calls, then asks for confirmation before writing. Renames update the
function's variable references without changing callers. Removed parameters must no longer
be used in the function; removal is rejected when an argument might have side effects
(including variable reads, which may be MMIO). Type changes leave arguments unchanged:
build to check types and semantics. Project files are rechecked before applying; if they
changed, preview again.
Parameter type and name fields use the GEL type colours; the in-page preview highlights
the files that will be modified. Expand a file to see its modified functions, or expand
an affected-call entry to see which functions call the edited function and how often.

Refactoring is intentionally conservative: generated contact handlers, assembly and macros
referencing changed names, calls outside parseable functions, commented argument lists,
ambiguous definitions, and unsupported syntax must be handled manually. It scans only the
top-level project `.gel` files available in the editor, not included files outside that
folder, generated files in `build`, or external code. Check those separately.

Use **+ Function** to create an empty function in the selected file. Select a function and
choose **Remove function** to delete it after confirmation. Save or undo pending visual
edits first. Removal is blocked when possible references are found in top-level project GEL
files or the contact configuration; those checks are conservative and do not cover external
or generated build files. Generated `contact.gel` cannot be changed here. Build after changes.

The **Declarations & memory** tab lists the editor's project symbols,
including defines, globals, enums, functions and struct fields. It is read-only. Compiled
RAM and MMIO addresses, field strides and pool masks come from `build/build_output.asm`;
they are not live values or ROM locations and can be stale, including after a failed build.
Rebuild and refresh the panel to update them.
The compiled memory size view uses the same name filter, plus a category filter, to compare
reported RAM variables, table fields, pool masks and MMIO registers. Select individual items
or all visible items to sum their sizes; selections remain when filtered out. RAM and MMIO
totals are separate and sum reported sizes, not unique addresses. Interleaved table fields
may occupy noncontiguous bytes, and compiler function frames and other unreported allocations
are not included. The bars compare each item with the largest currently visible item.
The overall occupancy bar independently shows reported allocations in low WRAM
`$0000–$1FFF` (8 KiB), divided into RAM variables, struct table fields, pool masks and
overlapping categories. It counts each address once and excludes MMIO and addresses outside
that window. Space not reported by the compiler is not necessarily free: function frames,
stack, scratch and other reservations are not represented.

The shared block editor provides increment and decrement blocks (prefix or postfix). For
`bit_set`, `bit_clear` and `bit_test`, select the target and bit index or choose a bit from the diagram; the diagram
shows the mask, not the current runtime value. Known 8-bit targets offer bits 0–7, while
other known types use the compiler's 0–15 range. Unknown types offer 0–15 with a warning;
the game build verifies types, symbolic indices and hardware-register restrictions.
The shared editor also shows configurable arguments for `peek8` (bank-0 address) and
`peek8l` (constant bank and address), including recognizable calls in expression terms.

To embed the visual block editor in another tool, load `visual-gel/model.js`,
`visual-gel/editor.js`, `visual-gel/history.js` and `visual-gel/editor.css`. Create an editor
with `createVisualGelEditor({ status, locals })`, set its symbol index with
`setIndex(buildIndex(symbols))`, and use `renderBlocks` and `addBar` with the host's statement
list and change callback. `locals` supplies names usable as table indices; the contact editor
passes `self` and `other`. `VisualGelModel.validateStatements` and `emit` can validate and
generate a standalone block list. The shared server's `POST /api/validate-gel` accepts
`{"gel": "..."}` for compiler-grammar syntax checking.

## Local editors

Run `tools/editor/start.bat` on Windows, or `python tools/editor/server.py` on any platform,
then open `http://127.0.0.1:8765/`. One loopback-only server hosts the object, contact and
metasprite editors for the same project. Pass a game folder as the first argument to use another
project; `--assets-dir` overrides its assets folder. The existing editor launchers also start
this server. Run only one server per port.

The object editor saves `objects.json`: each definition matches a Tiled object class (or its
fallback name) case-insensitively. Use letters, digits and underscores; the level build still
assigns numeric IDs. Definitions for classes not present in any built level are retained for
later. On build, the sample game initializes `Entities[].capabilities` from these definitions.
Movable means an object may receive movement intent from a controller or a behavior, not that it
automatically receives controller input. It can also be moved by forces without intent.
Ghost, Gravity and Contactable are independent flags.

These are **capability definitions**, not a finished generic object runtime: the sample game's
existing physics, movement and contact behavior do not yet read these flags. In particular,
changing a checkbox does not currently change gameplay. `Entities[].flags` remains the legacy
mode/contact state so the editor's definitions do not collide with player mode bits.

### Object variants

Expand **Variants and editable fields** for an object, enable the `Entities` fields that its
variants may override (for example `w` and `h`), then add variants. Each variant is automatically
assigned the lowest available ID from 0 to 255 for that object; existing IDs do not change when
variants are removed, and freed IDs can be reused. Entity field entries use the GEL type colours.
Set the Tiled object's `param` property to the desired variant ID and rebuild the game.
Missing or unmatched variants keep the normal spawn defaults; an omitted `param` is 0, so a
configured variant 0 applies to those objects. PlayerStart's `param` selects the player variant.
Overrides initialize entity fields after the normal defaults; later gameplay code can still change
them. The sample's entity contact checks use `w` and `h`, but individual game systems must use
the fields for them to have an effect.

Variants can also hold named custom values such as `heal_amount`. These are stored in ROM, not
in each entity, and shared behavior can read them using the entity's type and `param`:

```gel
if (object_variant_has(Entities[self].type, Entities[self].param, OBJECT_VALUE_HEAL_AMOUNT)) {
    u16 amount = object_variant_value(Entities[self].type, Entities[self].param, OBJECT_VALUE_HEAL_AMOUNT);
    // Apply amount to the character's health here.
}
```

The constant is generated from the value name; use it rather than hardcoding its numeric ID.
Custom values are integers from -32768 to 65535, stored as raw 16-bit values; the accessor returns
`u16` bits. It returns zero when a value is missing, so use `object_variant_has` to distinguish a
missing value from a stored zero. This data does not implement healing or any other behavior by
itself. Optional metasprite and palette IDs are stored as `OBJECT_VALUE_METASPRITE_ID` and
`OBJECT_VALUE_PALETTE_ID`; they do not change rendering. Definitions for classes absent from
built levels remain in the editor but are not emitted into runtime data until the class is used.

The sample's `game/level-data.gel` reads packed object records and collision/slope data.
Spawning, collision responses and tile streaming remain in the game systems that consume it.

## Quick start

1. Install Python 3.10+ and `pip install lark watchdog pillow`
2. Download [Mesen 2](https://www.mesen.ca) and put it in `tools/mesen/` (or set `GEL_MESEN`)
3. Run `dev.bat` on Windows, `./dev.sh` on Linux/macOS, or `python dev.py` anywhere. It builds `game/`, launches Mesen and watches for changes

On Linux/macOS you also need Asar built as a shared library, see [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).

Full instructions: [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).
Language reference: [docs/LANGUAGE.md](docs/LANGUAGE.md). Sound setup (needs Go once): [docs/SOUND.md](docs/SOUND.md).

Release build: `python compiler/build.py game --release`

## Metasprite editor

A browser-based editor for building multi-part sprites from layered images (head, hair, torso, legs,
arms...) and animating them. On Windows, run `tools/metasprite-editor/start.bat` to start the local
server and open the editor. It uses `game/assets/metasprite-project.json` to list the separate
`<name>.metasprite.json` projects, and makes PNGs in the assets folder available as layers. BMPs are
converted to indexed PNGs automatically. The editor also remains usable directly from `index.html`
with its browser folder picker. Project files are compiled into tiles and animation data; Flip H /
Flip V reuse tiles at no ROM cost, and the build deduplicates the rest. See
[tools/metasprite-editor/README.txt](tools/metasprite-editor/README.txt) for usage.

## Repository layout

| Path | Contents |
|------|----------|
| `compiler/` | parser, grammar, code generator, peephole optimiser, asset converters, `stdlib/` |
| `game/` | example platformer (entities, physics, Tiled level, sound) |
| `tools/` | hot-reload bridge, Mesen hook, test scripts, metasprite editor, bundled Asar and SNESMod tools |
| `snes-gel-vscode/` | VS Code language extension |
| `docs/` | documentation |

## Status

GEL is young and the language will change. See [todo.md](todo.md) for what is planned.
Windows is the main tested platform (CI runs there). The launcher and build scripts are written
to work on Linux and macOS too, but that path is untested.

## Third-party components

- [Asar](https://github.com/RPGHacker/asar) assembler (GPL-3.0 / LGPL-3.0 / WTFPL), in `tools/asar`
- [SNESMod](https://github.com/mukunda-/snesmod) sound driver and smconv (MIT), in `tools/`
- [Mesen 2](https://www.mesen.ca) emulator (GPL-3.0), downloaded separately and not redistributed

Details in [THIRD_PARTY.md](THIRD_PARTY.md).

## License

MIT, see [LICENSE](LICENSE). This covers the example game in `game/` too: the character, gem and
shell art and all sound effects were created by Pixel Brownie Software, and the example has no music.

Contributions are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md) and [CHANGELOG.md](CHANGELOG.md).
