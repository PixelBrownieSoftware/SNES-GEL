# Sound in GEL

GEL uses [SNESMOD](https://github.com/mukunda-/snesmod) (MIT) for music and sound effects.
The SNES sound driver and SPC program are already included (`compiler\stdlib\snesmod.asm`,
`snesmod_spc.bin`). The only extra tool you need is **smconv**, which compresses your audio into a
soundbank. It is built from the SNESMOD source bundled in `tools\snesmod-main`.

## One-time setup

1. Install [Go](https://go.dev/dl/) 1.23 or newer.
2. Run `tools\setup_smconv.bat`. It builds `tools\smconv\smconv.exe`.

You can also put your own `smconv` on `PATH`. If a project has no sounds you can skip all of this.

## Adding sounds

- Sound effects: put `.wav` files in `<project>\sfx\`. The build packs them into one module and
  gives each an `SFX_<NAME>` constant (see `game\sfx`, and `game\build\soundbank.gel` after a build).
- Music: put Impulse Tracker `.it` files in `<project>\audio\`. Each gets a `MOD_<NAME>` constant.
- ROM space: the SPC has about 58000 bytes shared between samples and music. The build prints
  how much your sounds use.

## Using sounds in GEL

```
#include "sound.gel"

fn init_game() {
    std_snes_init();
    std_sound_init();
    u8 jump = std_sfx_load(SFX_JUMP);      // load once at startup
    std_sfx_play(jump, 15, 8, 8);          // slot, volume 0-15, pan 0-15 (8 = centre), pitch 0-15 (8 = normal)
    std_music_load(MOD_ECHO);
    std_music_play(0);
}
```

Call `std_sound_update()` once per frame if your loop doesn't already (see `game\game.gel`).
Other functions: `std_music_stop`, `std_music_volume`, `std_music_fade`.

## Echo

Echo is configured in `gel.json`:

```json
"sfx_echo": { "enabled": true, "delay": 3, "feedback": 40, "volume": 50, "channels": [8] }
```

## Credits

SNESMOD, Copyright 2009-2025 Mukunda Johnson, MIT licence (`tools\snesmod-main\LICENSING.txt`).
