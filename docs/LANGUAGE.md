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
fn add(u8 a, u8 b) { return a + b; }
static fn helper() { ... }              // used by the stdlib for internal functions
fn fast(u8 v = reg(A)) { ... }          // parameter passed in a register
```

Inline assembly: `asm { LDA #$00 }`. Read a register: `reg(A)`.
The ROM boots into `init_game()`, which should set up hardware and then run the main loop.

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
- `sound.gel`: SNESMod wrappers: `std_sfx_load`, `std_sfx_play`, `std_music_load`,
  `std_music_play`, `std_music_stop`, `std_music_volume`, `std_music_fade`.

See `game\game.gel` for a complete example (entities, streaming levels, physics, sound).
