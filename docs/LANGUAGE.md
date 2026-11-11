# GEL language reference

Source of truth: `compiler\snes_gel.lark`. Statements end with `;`, blocks use `{}`,
comments are `//`.

## Types

| Type | Meaning |
|------|---------|
| `u8` / `i8` | 8-bit unsigned / signed |
| `u16` / `i16` | 16-bit unsigned / signed |
| `f16` | signed 8.8 fixed point (literals like `1.5`) |

## Preprocessor

```
#include "snes.gel"          // project folder, compiler\stdlib, or build\
#define GRAVITY 0.3          // textual macro; may expand to an expression such as Entities[0].x
```

## Variables

```
u8  lives = 3;               // normal global (RAM)
dp  u16 pad1_held;           // direct page: faster access, limited space
persist u16 cam_x = 0;       // survives hot reload
mmio u8 INIDISP @ 0x2100;    // hardware register at a fixed address
extern SOME_LABEL;           // label defined in assembly / generated assets
```

Locals are declared inside functions: `u8 i = 0;`.
Function parameters and locals have static RAM slots. GEL reuses those
addresses between functions that cannot be active at the same time; globals
remain in their own storage, with no runtime stack allocation for locals.

## Functions

```
fn add(u8 a, u8 b) -> u8 { return a + b; }
fn is_alive(u8 health) -> u8 { return health != 0; }
static fn helper() { ... }              // used by the stdlib for internal functions
fn init_game() { u8 sum = add(1, 2); if (is_alive(sum)) { sum += 1; } }
```

Return annotations are optional: existing unannotated functions retain the legacy `u16`
expression type. An annotated function must return a value on every path; a bare `return;`
is not allowed. The compiler recognizes complete `if`/`else` and `switch`/`default` branches,
but does not assume loops execute. `u8` results are truncated to eight bits and zero-extended;
`i8` results are truncated and sign-extended. Other results retain their 16-bit representation.
Numeric return expressions and assignments use existing implicit GEL behavior: there is no
automatic integer-to-fixed-point conversion or strict type checking. Return an `f16` expression
when a fixed-point value is intended.

Inline assembly: `asm { LDA #$00 }`. Read a register: `reg(A)`.
The ROM boots into `init_game()`, which should set up hardware and then run the main loop.

`peek8(address)` reads a byte from the current data bank, and
`peek8l(bank, address)` reads a byte from a 24-bit address. The bank may be a
constant or a runtime expression; the address is a 16-bit offset.

## Statements and operators

```
x = 1;  x += 2;                 // = += -= *= /= &= |= ^= <<= >>=
if (a) { } else if (b) { } else { }
while (cond) { }
switch (v) { case 1: ... break; case NAME: ... break; default: ... }
x++;  --x;
```

Expressions: `+ - * /`, `<< >>`, `& | ^ ~`, `&& || !`, `== != < > <= >=`, unary `-`.

`&`, `|`, `^`, and `~` are bitwise operators: they operate on the individual bits of integer
values. For example, `x & 7` keeps only the lowest three bits of `x`, which is useful for finding
its position within an 8-pixel tile. `&&`, `||`, and `!` are logical operators: they combine or
invert true/false conditions. For example, `a & b` combines bits, while `a && b` is true only when
both `a` and `b` are nonzero.

Literals: decimal, `0x1F`, `'c'`, `1.5`, `"text"`.

## Enums

```
enum Faction { PLAYER, ENEMY = 5, NEUTRAL };   // members become constants
```

## Structs and struct tables

```
struct Point { u8 x; u8 y; };

struct_table Entities[32] {          // structure-of-arrays: one array per field
    u16 x;
    u16 y;
    u8  type;
}
struct_table Oam[128] @ 0x0300 interleaved { ... }   // fields stored together (hardware layouts)
```

```
u8 e = Entities.create();                       // allocate a slot
Entities[e].x = 10;
Entities[e].free();                             // release the slot
Entities.for_each(e) { Entities[e].x += 1; }    // iterate live slots
```

## Standard library (`compiler\stdlib`)

- `snes.gel`: hardware registers, `std_snes_init`, `std_wait_vblank`, `std_input_update`
  (`pad1_held`, `pad1_pressed`), OAM and sprites (`std_draw_sprite`, `std_draw_metasprite`,
  `std_draw_frame`), animation (`std_anim_*`), VRAM and palette loading, text (`std_print`,
  `std_print_dec`, `std_print_hex`), scrolling and level helpers, `abs16`.
- `gui.gel`: `std_gui_move_selection(selected, count, previous_button, next_button, wrap)`,
  `std_gui_move_grid_selection(selected, count, columns, up, down, left, right, wrap)`,
  `std_gui_list_scroll_start(selected, first_visible, visible_count)`,
  `std_gui_button_pressed(button_mask)` and `std_gui_button_held(button_mask)`. Include it
  after `snes.gel`; it provides input and list/grid state helpers, not a renderer, so projects
  can draw menus in their own style. Grid movement preserves the column when moving vertically
  and clamps to the last item in a shorter final row. Horizontal movement stays within a row;
  `wrap` wraps at the grid edges. `std_gui_list_scroll_start` returns the first item index that
  keeps the selection within the visible range. Update input before using these helpers:

  ```
  std_input_update();
  selection = std_gui_move_selection(selection, item_count, BTN_UP, BTN_DOWN, 1);
  first_visible = std_gui_list_scroll_start(selection, first_visible, visible_rows);
  if (std_gui_button_pressed(BTN_A)) { confirm_selection(); }
  ```

- `sound.gel`: SNESMod wrappers: `std_sfx_load`, `std_sfx_play`, `std_music_load`,
  `std_music_play`, `std_music_stop`, `std_music_volume`, `std_music_fade`.

See `game\game.gel` for a complete example (entities, streaming levels, physics, sound).
