# Third-party components

GEL itself is MIT licensed (see `LICENSE`), including the example game's art and sound effects, which are original work by Pixel Brownie Software. It bundles or uses the following.

| Component | Where | Licence |
|-----------|-------|---------|
| [SNESMOD](https://github.com/mukunda-/snesmod) and smconv, by Mukunda Johnson | `tools/snesmod-main`, `compiler/stdlib/snesmod.asm`, `compiler/stdlib/snesmod_spc.bin` | MIT (`tools/snesmod-main/LICENSING.txt`) |
| [Asar](https://github.com/RPGHacker/asar) SNES assembler | `tools/asar` | Multi-licensed: GPL-3.0, LGPL-3.0 and WTFPL. GEL uses it unmodified as a separate DLL. |
| [Mesen 2](https://www.mesen.ca) emulator | not included; downloaded by the user into `tools/mesen` | GPL-3.0 |
| Python packages `lark`, `watchdog`, `pillow` | installed with pip | MIT/BSD, Apache-2.0, HPND |
| Go modules used by smconv | fetched by `go build` | see `tools/snesmod-main/smconv/go.mod` |

The licence texts for Asar are kept alongside it in `tools/asar`. Mesen is never redistributed by
this repository.

ROMs you build contain the SNESMOD SPC program, so keep the SNESMOD MIT notice with your project
credits if you distribute them.
