"""GEL build pipeline.

usage: python compiler/build.py [project_dir] [--release] [--no-opt] [--no-cache]

A project is a folder containing game.gel and (optionally) assets/*.png.
Everything generated goes to <project>/build/.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
# Piped output on Windows defaults to the ANSI code page, which can't print the status symbols.
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        _s.reconfigure(encoding="utf-8", errors="replace")

COMPILER_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(COMPILER_DIR)
sys.path.insert(0, COMPILER_DIR)

from gel_codegen import compile_gel  # noqa: E402
from build_cache import StageCache, make_key  # noqa: E402
import object_definitions  # noqa: E402

ASAR_DIR = os.path.join(ROOT, "tools", "asar")
EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""


def find_asar_library():
    """Bundled Asar DLL on Windows; libasar.so / libasar.dylib elsewhere (or set GEL_ASAR_LIB)."""
    if os.environ.get("GEL_ASAR_LIB"):
        return os.environ["GEL_ASAR_LIB"]
    if sys.platform == "win32":
        return os.path.join(ASAR_DIR, "asar64.dll" if sys.maxsize > 2**32 else "asar.dll")
    name = "libasar.dylib" if sys.platform == "darwin" else "libasar.so"
    local = os.path.join(ASAR_DIR, name)
    return local if os.path.isfile(local) else (shutil.which(name) or local)


ASAR_DLL = find_asar_library()
HOOK_TEMPLATE = os.path.join(ROOT, "tools", "mesen_gel_hook.lua")
STDLIB_DIR = os.path.join(COMPILER_DIR, "stdlib")

# Sound: the driver code sits in the main bank, its SPC program in the next bank, and the
# soundbank (which needs whole banks to itself) after that. All LoROM $8000-$FFFF banks.
SPC_PROGRAM_BANK = 0x81
SOUNDBANK_BANK = 0x82
CODE_LIMIT_WITH_ASSETS = 0xC000     # png_to_snes_tiles.py places assets from here
CODE_LIMIT = 0xFFC0                 # header / vectors
CONFIG_FILE = "gel.json"

RELEASE = "--release" in sys.argv    # release ROMs skip hot-reload state restore
NO_OPT = "--no-opt" in sys.argv      # skip the peephole optimiser
NO_CACHE = "--no-cache" in sys.argv  # rebuild every stage, ignoring (and refreshing) the cache
_args = [a for a in sys.argv[1:] if not a.startswith("--")]
PROJECT = os.path.abspath(_args[0] if _args else os.path.join(ROOT, "game"))
BUILD = os.path.join(PROJECT, "build")
LAYOUT_FILE = os.path.join(BUILD, "game.layout")   # persist-variable addresses from the last good build


def load_layout():
    try:
        with open(LAYOUT_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def assemble(asm_path, symbols_path):
    """Assemble through asar.dll in-process; returns (rom bytes, label table) or None."""
    sys.path.insert(0, os.path.join(ASAR_DIR, "bindings", "python"))
    import asar

    if not asar.init(ASAR_DLL):
        raise RuntimeError("asar.dll failed to initialise")
    try:
        ok, rom = asar.patch(asm_path, b"")
        for w in asar.getwarnings():
            print(f"  asar warning: {w.fullerrdata.decode()}")
        if not ok:
            print("❌ Asar Assembly Failed:")
            for e in asar.geterrors():
                print(f"  {e.fullerrdata.decode()}")
            return None

        labels = asar.getalllabels()
        with open(symbols_path, "w") as f:
            for name, addr in sorted(labels.items(), key=lambda kv: kv[1]):
                f.write(f"{addr:06X} {name}\n")
        return bytes(rom), labels
    finally:
        asar.close()


def load_config():
    """Optional <project>/gel.json: {"header": {title, rom_size_kb, sram_kb, region, developer, version}}."""
    path = os.path.join(PROJECT, CONFIG_FILE)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except ValueError as e:
        fail(f"{CONFIG_FILE}: {e}")


def finalize_rom(rom, header_cfg):
    """Pad to a power-of-two size (at least rom_size_kb), then fix the header size byte and checksum."""
    from gel_codegen import DEFAULT_HEADER, size_code
    want_kb = int({**DEFAULT_HEADER, **header_cfg}["rom_size_kb"])
    kb = 1
    while kb * 1024 < max(len(rom), want_kb * 1024, 32 * 1024):
        kb *= 2
    rom = bytearray(rom.ljust(kb * 1024, b"\xff"))
    rom[0x7FD7] = size_code(kb)
    rom[0x7FDC:0x7FE0] = bytes([0xFF, 0xFF, 0x00, 0x00])
    checksum = sum(rom) & 0xFFFF
    rom[0x7FDC:0x7FE0] = ((checksum ^ 0xFFFF) | (checksum << 16)).to_bytes(4, "little")
    return bytes(rom), kb


def find_smconv():
    return shutil.which("smconv") or next(
        (p for p in (os.path.join(ROOT, "tools", "smconv", "smconv" + EXE_SUFFIX),) if os.path.isfile(p)), None)


SOUND_OUTPUT_NAMES = ("sfx.it", "echo.it")


def sound_outputs():
    names = [n for n in os.listdir(BUILD) if n.startswith("soundbank.")] if os.path.isdir(BUILD) else []
    return names + [n for n in SOUND_OUTPUT_NAMES if os.path.isfile(os.path.join(BUILD, n))]


def build_soundbank(cache):
    """Cached wrapper: skips wav->it and smconv when audio/, sfx/, the sound settings and the
    converters are all unchanged."""
    cfg = load_config()
    files = [os.path.join(COMPILER_DIR, n) for n in ("build.py", "build_cache.py", "wav_to_it.py")]
    for sub, ext in (("audio", ".it"), ("sfx", ".wav")):
        d = os.path.join(PROJECT, sub)
        if os.path.isdir(d):
            files += [os.path.join(d, n) for n in os.listdir(d) if n.lower().endswith(ext)]
    smconv = find_smconv()
    if smconv:
        files.append(smconv)
    settings = json.dumps({"rate": cfg.get("sfx_rate"), "echo": cfg.get("sfx_echo"),
                           "smconv": bool(smconv)}, sort_keys=True)
    key = make_key(PROJECT, files, settings)
    hit = cache.lookup("sound", key)
    if hit:
        print("  sound: unchanged, using cached soundbank")
        path = hit["extra"].get("soundbank")
        return os.path.join(BUILD, path) if path else None
    cache.invalidate("sound")
    for name in sound_outputs():   # drop stale results so only this run's files are recorded
        os.remove(os.path.join(BUILD, name))
    result = _build_soundbank()
    cache.store("sound", key, sound_outputs(),
                {"soundbank": os.path.basename(result) if result else None})
    return result


def _build_soundbank():
    """Convert <project>/audio/*.it and <project>/sfx/*.wav with smconv. Returns the soundbank path, or None."""
    import wav_to_it
    audio_dir = os.path.join(PROJECT, "audio")
    sfx_dir = os.path.join(PROJECT, "sfx")
    modules = sorted(os.path.join(audio_dir, n) for n in os.listdir(audio_dir)
                     if n.lower().endswith(".it")) if os.path.isdir(audio_dir) else []
    bank_gel = os.path.join(BUILD, "soundbank.gel")
    ids = []
    sfx_ids = []
    result = None
    if os.path.isdir(sfx_dir):
        rate = int(load_config().get("sfx_rate", 16000))
        sfx_it = os.path.join(BUILD, "sfx.it")
        names = wav_to_it.convert_folder(sfx_dir, sfx_it, rate)
        if names:
            # The sfx module goes first so that sample N is soundbank source N.
            modules.insert(0, sfx_it)
            sfx_ids = [(n, i) for n, i, _ in names]
            brr = sum((l + 15) // 16 * 9 for i, l in {(i, l) for _, i, l in names})
            print(f"  sfx: {len(names)} sounds at {rate} Hz, about {brr} bytes of BRR (SPC has ~58000 shared with music)")
    echo_on = False
    echo_cfg = load_config().get("sfx_echo")
    if modules and isinstance(echo_cfg, dict) and echo_cfg.get("enabled", True):
        # A near-empty module whose song message carries the echo settings; the game loads it
        # once so the SPC sets echo up, then loads the sound effects as usual.
        message = "[[SNESMOD]]\n" + "\n".join([
            f"EDL {int(echo_cfg.get('delay', 3))}",
            f"EFB {int(echo_cfg.get('feedback', 40))}",
            f"EVOL {int(echo_cfg.get('volume', 50))}",
            f"EON {' '.join(str(c) for c in echo_cfg.get('channels', [8]))}",
        ])
        echo_it = os.path.join(BUILD, "echo.it")
        wav_to_it.write_it(echo_it, [("ECHO", [0] * 16)], 16000, "GEL echo", message)
        modules.append(echo_it)
        echo_on = True
    if modules:
        smconv = find_smconv()
        if smconv is None:
            fail("audio/ or sfx/ has sounds but smconv was not found. Run tools/setup_smconv.bat "
                 "(Windows) or tools/setup_smconv.sh, which need Go (see docs/SOUND.md), or put smconv in tools/smconv/ or on PATH.")
        base = os.path.join(BUILD, "soundbank")
        proc = subprocess.run([smconv, "-s", "-o", base, *modules], capture_output=True, text=True)
        if proc.returncode != 0:
            fail(f"smconv failed:\n{proc.stdout}{proc.stderr}")
        result = base + ".smbank"
        with open(base + ".inc") as f:
            ids = re.findall(r"^\s*(\w+)\s*=\s*(\d+)\s*$", f.read(), re.MULTILINE)
    with open(bank_gel, "w") as f:
        f.write("// Generated by build.py from audio/*.it and sfx/*.wav; do not edit.\n")
        f.write(f"#define SOUNDBANK_BANK 0x{SOUNDBANK_BANK:02X}\n")
        for name, value in ids + [(n, str(v)) for n, v in sfx_ids]:
            f.write(f"#define {name} {value}\n")
        # SFX_ECHO is 1 when gel.json has an enabled "sfx_echo" block; MOD_ECHO is its module.
        f.write(f"#define SFX_ECHO {1 if echo_on else 0}\n")
        if not echo_on:
            f.write("#define MOD_ECHO 0\n")
    return result


def sound_asm(soundbank):
    """Assembly that adds the SNESMOD driver, its SPC program and the soundbank."""
    posix = lambda p: p.replace("\\", "/")
    spc_bin = os.path.join(STDLIB_DIR, "snesmod_spc.bin")
    lines = [
        "\n; --- SNESMOD sound driver ---",
        f"!SPC_LEN = {os.path.getsize(spc_bin)}",
        f'incsrc "{posix(os.path.join(STDLIB_DIR, "snesmod.asm"))}"',
        f"\norg ${SPC_PROGRAM_BANK:02X}8000",
        "snesmod_spc:",
        f'    incbin "{posix(spc_bin)}"',
    ]
    if soundbank:
        size = os.path.getsize(soundbank)
        lines.append("\nsnesmod_soundbank = $%02X8000" % SOUNDBANK_BANK)
        for k in range((size + 0x7FFF) // 0x8000):
            lo, hi = k * 0x8000, min(size, (k + 1) * 0x8000)
            lines += [f"org ${SOUNDBANK_BANK + k:02X}8000", f'    incbin "{posix(soundbank)}":${lo:X}..${hi:X}']
    return "\n".join(lines) + "\n"


def parse_ram_vars(asm_text):
    """Every dp/RAM variable from the compiler's symbol summary: name -> (type, address)."""
    found = {}
    for m in re.finditer(r"^; ram\s+(\w+)\s+(\w+) @ \$([0-9A-Fa-f]+)", asm_text, re.MULTILINE):
        found[m.group(2)] = (m.group(1), int(m.group(3), 16))
    return found


def parse_slot_aliases(asm_text, source):
    """Resolve `#define name Table[0].field` aliases to the table field's address (slot 0),
    so tools can still find things like the player after it moved into an entity table."""
    fields, table = {}, None
    for line in asm_text.splitlines():
        m = re.match(r"^; table (\w+)\[", line)
        if m:
            table = m.group(1)
            continue
        m = re.match(r"^;\s+\.(\w+) (\w+) @ \$([0-9A-Fa-f]+)", line)
        if m and table:
            fields[(table, m.group(1))] = (m.group(2), int(m.group(3), 16))
        elif not line.startswith(";"):
            table = None
    found = {}
    for m in re.finditer(r"^\s*#define\s+(\w+)\s+(\w+)\[0\]\.(\w+)\s*$", source, re.MULTILINE):
        entry = fields.get((m.group(2), m.group(3)))
        if entry:
            found[m.group(1)] = entry
    return found


def write_hook(layout, source, asm_text=""):
    """Bake build paths, variable addresses, and collision dimensions into Mesen's hook."""
    with open(HOOK_TEMPLATE) as f:
        text = f.read()
    ram_vars = parse_ram_vars(asm_text)
    ram_vars.update(parse_slot_aliases(asm_text, source))
    lua_vars = ", ".join(f'{n} = {{"{t}", {a}}}' for n, (t, a) in sorted(ram_vars.items()))
    asset_header = os.path.join(BUILD, "assets.gel")
    asset_source = ""
    if os.path.isfile(asset_header):
        with open(asset_header, encoding="utf-8") as f:
            asset_source = f.read()
    asset_defines = {name: int(value, 0) for name, value in re.findall(
        r"^\s*#define\s+(LEVEL_WIDTH|LEVEL_HEIGHT)\s+(0x[0-9a-fA-F]+|[0-9]+)\b",
        asset_source, re.MULTILINE)}
    replacements = {
        "@BUILD_DIR@": BUILD.replace("\\", "/") + "/",
        "@OVERLAY_FILE@": PROJECT.replace("\\", "/") + "/debug_overlay.lua",
        "@GEL_VARS@": "{" + lua_vars + "}",
        "@LEVEL_WIDTH@": str(asset_defines.get("LEVEL_WIDTH", "nil")),
        "@LEVEL_HEIGHT@": str(asset_defines.get("LEVEL_HEIGHT", "nil")),
    }
    for name in ("player_x", "player_y", "cam_x", "cam_y"):
        entry = layout.get(name) or ram_vars.get(name)
        replacements[f"@{name.upper()}_ADDR@"] = str(entry[1]) if entry else "nil"
    for macro, placeholder in (("HB_W", "@COLLISION_WIDTH@"),
                               ("HB_H", "@COLLISION_HEIGHT@")):
        match = re.search(rf"^\s*#define\s+{macro}\s+(0x[0-9a-fA-F]+|[0-9]+)\b",
                          source, re.MULTILINE)
        replacements[placeholder] = str(int(match.group(1), 0)) if match else "nil"

    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    with open(os.path.join(BUILD, "gel_hook.lua"), "w") as f:
        f.write(text)


def asset_input_files(assets_dir):
    """Everything the converters can read: all files under assets/ except editor state, plus
    any external tileset a Tiled map points at (which may live outside assets/)."""
    skip = (".tiled-session", ".tiled-project")
    files = []
    for dirpath, _, names in os.walk(assets_dir):
        files += [os.path.join(dirpath, n) for n in names
                  if not n.startswith(".") and not n.endswith(skip)]
    for path in list(files):
        if path.lower().endswith(".tmj"):
            try:
                with open(path) as f:
                    for ts in json.load(f).get("tilesets", []):
                        if "source" in ts:
                            files.append(os.path.normpath(os.path.join(os.path.dirname(path), ts["source"])))
            except (OSError, ValueError, AttributeError):
                pass   # the converter reports the real error
    return files


ASSET_SCRIPTS = ("build.py", "build_cache.py", "png_to_snes_tiles.py", "level_pack.py", "metasprite_pack.py")


def build_assets(cache, assets_dir, first_free_bank):
    """Cached wrapper around png_to_snes_tiles.py. It converts into a scratch folder and only
    copies the results into build/ on success, so a failed conversion leaves build/ untouched."""
    try:
        import PIL
        pil = PIL.__version__
    except ImportError:
        pil = "?"
    files = asset_input_files(assets_dir) + [os.path.join(COMPILER_DIR, n) for n in ASSET_SCRIPTS]
    key = make_key(PROJECT, files, f"bank={first_free_bank:#x};pil={pil};py={sys.version_info[:2]}")
    hit = cache.lookup("assets", key)
    if hit:
        print("  assets: unchanged, using cached conversion")
        return
    old_outputs = cache.invalidate("assets")
    scratch = os.path.join(BUILD, "_assets_tmp")
    shutil.rmtree(scratch, ignore_errors=True)
    os.makedirs(scratch)
    try:
        subprocess.run([sys.executable, os.path.join(COMPILER_DIR, "png_to_snes_tiles.py"),
                        assets_dir, scratch, hex(first_free_bank)], check=True)
        produced = sorted(n for n in os.listdir(scratch) if os.path.isfile(os.path.join(scratch, n)))
        for stale in set(old_outputs) - set(produced):
            try:
                os.remove(os.path.join(BUILD, stale))
            except OSError:
                pass
        for name in produced:
            os.replace(os.path.join(scratch, name), os.path.join(BUILD, name))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    cache.store("assets", key, produced)


def fail(msg):
    print(f"❌ {msg}")
    sys.exit(1)


def build():
    print("=" * 60)
    print("          SNES-GEL Full ROM Build Pipeline")
    print("=" * 60)

    game_file = os.path.join(PROJECT, "game.gel")
    assets_dir = os.path.join(PROJECT, "assets")
    if not os.path.isfile(game_file):
        fail(f"'game.gel' not found in project folder '{PROJECT}'.")
    if not os.path.isfile(ASAR_DLL):
        fail(f"Asar library not found: {ASAR_DLL}\n"
             "  Windows ships one in tools/asar. On Linux/macOS build Asar from "
             "https://github.com/RPGHacker/asar and copy libasar.so / libasar.dylib to tools/asar/ "
             "(or set GEL_ASAR_LIB).")
    os.makedirs(BUILD, exist_ok=True)

    # 1. Sound first (its size decides which banks are left for level data), then assets
    cache = StageCache(BUILD, enabled=not NO_CACHE)
    t0 = time.time()
    soundbank = build_soundbank(cache)
    t_sound = time.time() - t0
    first_free_bank = SOUNDBANK_BANK
    if soundbank:
        size = os.path.getsize(soundbank)
        first_free_bank += (size + 0x7FFF) // 0x8000
    print("[1/4] Processing 'assets/' directory...")
    t0 = time.time()
    if os.path.isdir(assets_dir):
        build_assets(cache, assets_dir, first_free_bank)
    else:
        print("  (no assets/ folder, skipping)")
    t_assets = time.time() - t0
    if soundbank:
        print(f"  soundbank: {os.path.getsize(soundbank)} bytes")
    print(f"  timing: sound {t_sound:.1f}s, assets {t_assets:.1f}s")

    # 2. Compile game.gel to Assembly
    print("[2/4] Compiling 'game.gel' to 65C816 Assembly...")
    try:
        objects = object_definitions.load(os.path.join(PROJECT, "objects.json"))
        asset_header = os.path.join(BUILD, "assets.gel")
        if os.path.isfile(asset_header):
            with open(asset_header, encoding="utf-8") as f:
                asset_source = f.read()
        else:
            asset_source = ""
        generated = object_definitions.generate(objects, asset_source)
        with open(os.path.join(BUILD, "object-definitions.gel"), "w", encoding="utf-8") as f:
            f.write(generated)
        if any(obj.get("editable_fields") for obj in objects["objects"]):
            from gel_parser import parse_gel_code
            with open(game_file, encoding="utf-8") as f:
                object_tree = parse_gel_code(f.read(), PROJECT, [BUILD])
            if object_tree is None:
                raise ValueError("Cannot validate editable entity fields: game GEL syntax is invalid.")
            fields = object_definitions.entity_fields(object_tree)
            object_definitions.validate_fields(objects, fields)
            generated = object_definitions.generate(objects, asset_source, fields)
            with open(os.path.join(BUILD, "object-definitions.gel"), "w", encoding="utf-8") as f:
                f.write(generated)
        variant_asm = object_definitions.generate_asm(objects, asset_source)
    except (OSError, ValueError) as e:
        fail(f"Object definitions: {e}")
    with open(game_file, "r") as f:
        gel_code = f.read()

    header_cfg = load_config().get("header", {})
    try:
        asm_code, layout = compile_gel(gel_code, load_layout(), hot_reload=not RELEASE,
                                       base_dir=PROJECT, include_dirs=[BUILD], optimize=not NO_OPT,
                                       header=header_cfg)
    except Exception as e:
        fail(f"GEL Compilation Error:\n{e}")

    asm_code += "\n" + variant_asm + "\n_gel_code_end:\n"
    uses_sound = re.search(r"\b(?:JSR|JMP)\s+gel_snd_\w+", asm_code) is not None
    if uses_sound:
        asm_code += sound_asm(soundbank)

    assets_asm = os.path.join(BUILD, "assets.asm")
    has_assets = os.path.exists(assets_asm)
    if has_assets:
        with open(assets_asm, "r") as f:
            asm_code += "\n\n; --- Included Assets ---\n" + f.read()

    asm_path = os.path.join(BUILD, "build_output.asm")
    with open(asm_path, "w") as f:
        f.write(asm_code)
    print("✔ Generated 'build_output.asm'.")

    # 3. Assemble via Asar
    print("[3/4] Assembling 'build_output.asm' via asar.dll...")
    assembled = assemble(asm_path, os.path.join(BUILD, "game.sym"))
    if assembled is None:
        sys.exit(1)
    rom, labels = assembled
    code_end = labels.get("snesmod_code_end", labels.get("_gel_code_end", 0)) & 0xFFFF
    code_limit = CODE_LIMIT_WITH_ASSETS if has_assets else CODE_LIMIT
    if code_end > code_limit:
        fail(f"Code ends at $80{code_end:04X}, past the ${code_limit:04X} limit "
             f"({code_end - code_limit} bytes too many).")
    rom, rom_kb = finalize_rom(rom, header_cfg)
    with open(os.path.join(BUILD, "game.sfc"), "wb") as f:
        f.write(rom)
    size = len(rom)

    # Only commit the layout once the ROM exists, so it always matches the last good build.
    with open(LAYOUT_FILE, "w") as f:
        json.dump(layout, f, indent=1)
    if not RELEASE:
        write_hook(layout, gel_code, asm_code)

    print(f"[4/4] Successfully assembled 'game.sfc' ({size} bytes).")
    print("=" * 60)
    print("🎉 BUILD SUCCESSFUL! Produced 'build/game.sfc'")
    print("=" * 60)


if __name__ == "__main__":
    build()
