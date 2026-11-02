# Changelog

## 0.1.0 - first public release

- GEL compiler: C-like language to 65C816 assembly, built with Asar.
- Language features: tables with named fields, pools, `f16` fixed point, `persist` data, inlining.
- Optimisations: constant folding, peephole pass, cheaper table stores.
- Tiled (`.tmj`) level support with hot reload, metasprite tooling and a sound bank via SNESMod.
- Incremental build cache for the sound and assets stages (`--no-cache` to disable).
- VS Code extension with GEL syntax highlighting.
- Example platformer in `game/`, documentation in `docs/`, test suite and CI.
