"""End-to-end build tests: they run the real build.py on a tiny generated project.
Skipped where the Asar DLL can't be used (it ships as a Windows DLL)."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import wave

import pytest
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_PY = os.path.join(ROOT, "compiler", "build.py")

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="bundled Asar is a Windows DLL")

GAME = '#include "snes.gel"\n#include "assets.gel"\nfn init_game() { std_snes_init(); while (1) { } }\n'


def smconv_available():
    return bool(shutil.which("smconv")) or os.path.isfile(os.path.join(ROOT, "tools", "smconv", "smconv.exe"))


def make_png(path, color):
    img = Image.new("P", (16, 16), 0)
    img.putpalette([0, 0, 0, *color] + [0, 0, 0] * 14)
    for x in range(8):
        for y in range(8):
            img.putpixel((x, y), 1)
    img.save(path)


def make_wav(path, n=800):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"".join((i % 40 * 300 - 6000).to_bytes(2, "little", signed=True) for i in range(n)))


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj"
    (p / "assets").mkdir(parents=True)
    (p / "game.gel").write_text(GAME)
    make_png(p / "assets" / "hero.png", (255, 0, 0))
    return p


def run_build(project, *flags):
    r = subprocess.run([sys.executable, BUILD_PY, str(project), *flags],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, r.stdout + r.stderr


@pytest.mark.parametrize("mode", ["exlorom", "exhirom"])
def test_unsupported_mapping_does_not_replace_existing_rom(project, mode):
    rc, out = run_build(project)
    assert rc == 0, out
    before = (project / "build" / "game.sfc").read_bytes()
    (project / "gel.json").write_text(json.dumps({"header": {"rom_mode": mode}}))
    rc, out = run_build(project)
    assert rc != 0
    assert "supported mappings are LoROM and HiROM" in out
    assert (project / "build" / "game.sfc").read_bytes() == before


def test_lorom_header_and_checksum(project):
    rc, out = run_build(project)
    assert rc == 0, out
    rom = (project / "build" / "game.sfc").read_bytes()
    assert rom[0x7FD5] == 0x30
    assert 1024 << rom[0x7FD7] == len(rom)
    complement = int.from_bytes(rom[0x7FDC:0x7FDE], "little")
    checksum = int.from_bytes(rom[0x7FDE:0x7FE0], "little")
    assert complement ^ checksum == 0xFFFF
    assert sum(rom) & 0xFFFF == checksum


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(ROOT, "tools", "mesen", "Mesen.exe")),
    reason="Mesen is required",
)
@pytest.mark.parametrize("flags", [("--release",), ("--release", "--no-opt")])
def test_runtime_bank_reads_in_mesen(project, flags):
    (project / "game.gel").write_text(
        'mmio u8 memory @ 0x1800; u8 bank; u8 result;\n'
        'fn address() -> u16 { return 0x1800; }\n'
        'fn init_game() { memory = 171; bank = 0x7E; '
        'result = peek8l(bank, address()); while (1) { } }\n',
        encoding="utf-8")
    rc, out = run_build(project, *flags)
    assert rc == 0, out
    asm = (project / "build" / "build_output.asm").read_text()
    match = re.search(r"; ram\s+u8\s+result @ \$([0-9A-Fa-f]+)", asm)
    assert match
    address = int(match.group(1), 16)
    script = project / "verify-bank.lua"
    script.write_text(
        'local frame = 0\n'
        'emu.addEventCallback(function()\n'
        '  frame = frame + 1\n'
        f'  local value = emu.read({address}, emu.memType.snesWorkRam)\n'
        '  if value == 171 or frame >= 120 then\n'
        '    local file = assert(io.open(os.getenv("GEL_MAPPING_RESULT"), "w"))\n'
        '    file:write(tostring(value)); file:close(); emu.stop(0)\n'
        '  end\n'
        'end, emu.eventType.endFrame)\n', encoding="utf-8")
    result = project / "mesen-result.txt"
    env = {**os.environ, "GEL_MAPPING_RESULT": str(result)}
    run = subprocess.run([os.path.join(ROOT, "tools", "mesen", "Mesen.exe"), "--testrunner",
                          str(project / "build" / "game.sfc"), str(script)],
                         cwd=project, env=env, capture_output=True, text=True, timeout=90)
    assert run.returncode == 0, run.stdout + run.stderr
    assert result.read_text() == "171", run.stdout + run.stderr


@pytest.mark.parametrize("header,error", [
    ({"fastrom": "false"}, "fastrom must be true or false"),
    ({"rom_mode": "hirom", "rom_size_kb": 8192}, "limited to 4096 KB"),
])
def test_invalid_settings_preserve_previous_rom(project, header, error):
    rc, out = run_build(project)
    assert rc == 0, out
    before = (project / "build" / "game.sfc").read_bytes()
    (project / "gel.json").write_text(json.dumps({"header": header}))
    rc, out = run_build(project)
    assert rc != 0 and error in out
    assert (project / "build" / "game.sfc").read_bytes() == before


@pytest.mark.parametrize("mode,fast,offset,map_byte", [
    ("lorom", True, 0x7FC0, 0x30), ("lorom", False, 0x7FC0, 0x20),
    ("hirom", True, 0xFFC0, 0x31), ("hirom", False, 0xFFC0, 0x21),
])
def test_mapping_build_header_boot_and_level_data(project, mode, fast, offset, map_byte):
    level = {
        "width": 32, "height": 32, "tilewidth": 8, "tileheight": 8,
        "layers": [{"type": "tilelayer", "name": "Tile Layer 1", "data": [0] * 1024}],
        "tilesets": [{"firstgid": 1, "tilewidth": 8, "tileheight": 8, "tiles": []}],
    }
    (project / "assets" / "level.tmj").write_text(json.dumps(level), encoding="utf-8")
    (project / "gel.json").write_text(json.dumps({"header": {"rom_mode": mode, "fastrom": fast}}))
    rc, out = run_build(project, "--release")
    assert rc == 0, out
    rom = (project / "build" / "game.sfc").read_bytes()
    assert rom[offset + 21] == map_byte
    assert 1024 << rom[offset + 23] == len(rom)
    reset = int.from_bytes(rom[offset + 60:offset + 62], "little")
    assert reset == 0x8000
    boot_offset = 0x8000 if mode == "hirom" else 0
    assert rom[boot_offset:boot_offset + 10] == bytes([
        0x78, 0x18, 0xFB, 0xE2, 0x20, 0xA9, int(fast), 0x8D, 0x0D, 0x42,
    ])
    checksum = int.from_bytes(rom[offset + 30:offset + 32], "little")
    assert int.from_bytes(rom[offset + 28:offset + 30], "little") ^ checksum == 0xFFFF
    assert sum(rom) & 0xFFFF == checksum
    assets = (project / "build" / "assets.gel").read_text()
    bank = int(re.search(r"#define LEVEL_BANK 0x([0-9A-F]+)", assets).group(1), 16)
    physical = ((bank - 0x80) * 0x10000 + 0x8000 if mode == "hirom"
                else (bank - 0x80) * 0x8000)
    level_data = (project / "build" / "level_map.bin").read_bytes()
    assert rom[physical:physical + len(level_data)] == level_data
    layout = json.loads((project / "build" / "rom-layout.json").read_text())
    assert layout["rom_mode"] == mode and layout["fastrom"] is fast
    assert layout["banks"][bank - 0x80]["used"] >= len(level_data)


@pytest.mark.skipif(not smconv_available(), reason="smconv not available")
@pytest.mark.parametrize("mode", ["lorom", "hirom"])
def test_soundbank_crossing_bank_boundary(project, mode):
    (project / "sfx").mkdir()
    make_wav(project / "sfx" / "beep.wav", n=60000)
    (project / "game.gel").write_text(
        '#include "snes.gel"\n#include "sound.gel"\n'
        'fn init_game() { std_sound_init(); std_sfx_load(SFX_BEEP); while (1) { } }\n',
        encoding="utf-8")
    (project / "gel.json").write_text(json.dumps({"header": {"rom_mode": mode}}))
    rc, out = run_build(project, "--release")
    assert rc == 0, out
    rom = (project / "build" / "game.sfc").read_bytes()
    soundbank = (project / "build" / "soundbank.smbank").read_bytes()
    assert len(soundbank) > 0x8000
    stride = 0x10000 if mode == "hirom" else 0x8000
    upper = 0x8000 if mode == "hirom" else 0
    for chunk in range((len(soundbank) + 0x7FFF) // 0x8000):
        source = soundbank[chunk * 0x8000:(chunk + 1) * 0x8000]
        physical = (2 + chunk) * stride + upper
        assert rom[physical:physical + len(source)] == source
    spc = (project / "build" / "build_output.asm").read_text()
    assert "org $818000" in spc
    assert "snesmod_soundbank = $828000" in spc


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(ROOT, "tools", "mesen", "Mesen.exe")) or not smconv_available(),
    reason="Mesen and smconv are required",
)
@pytest.mark.parametrize("mode,fast", [
    ("lorom", True), ("lorom", False), ("hirom", True), ("hirom", False),
])
def test_mapping_runs_level_and_sound_in_mesen(project, mode, fast):
    level = {
        "width": 32, "height": 32, "tilewidth": 8, "tileheight": 8,
        "layers": [{"type": "tilelayer", "name": "Tile Layer 1", "data": [0] * 1024}],
        "tilesets": [{"firstgid": 1, "tilewidth": 8, "tileheight": 8, "tiles": []}],
    }
    (project / "assets" / "level.tmj").write_text(json.dumps(level), encoding="utf-8")
    (project / "sfx").mkdir()
    make_wav(project / "sfx" / "beep.wav", n=60000)
    (project / "game.gel").write_text(
        '#include "snes.gel"\n#include "sound.gel"\n#include "assets.gel"\n'
        'u8 result;\n'
        'fn init_game() { std_snes_init(); std_sound_init(); std_sfx_load(SFX_BEEP); '
        'result = peek8l(LEVEL_BANK, LEVEL_MAP + 1); while (1) { } }\n',
        encoding="utf-8")
    (project / "gel.json").write_text(json.dumps({"header": {"rom_mode": mode, "fastrom": fast}}))
    rc, out = run_build(project, "--release")
    assert rc == 0, out
    layout = json.loads((project / "build" / "game.layout").read_text())
    # Non-persistent variables are listed in the assembly symbol summary.
    asm = (project / "build" / "build_output.asm").read_text()
    match = re.search(r"; ram\s+u8\s+result @ \$([0-9A-Fa-f]+)", asm)
    assert match, layout
    address = int(match.group(1), 16)
    script = project / "verify.lua"
    script.write_text(
        f'local addr = {address}\n'
        'local frame = 0\n'
        'emu.addEventCallback(function()\n'
        '  frame = frame + 1\n'
        '  local value = emu.read(addr, emu.memType.snesWorkRam)\n'
        '  if value == 4 or frame >= 600 then\n'
        '    local file = assert(io.open(os.getenv("GEL_MAPPING_RESULT"), "w"))\n'
        '    file:write(tostring(value)); file:close(); emu.stop(0)\n'
        '  end\n'
        'end, emu.eventType.endFrame)\n', encoding="utf-8")
    result = project / "mesen-result.txt"
    env = {**os.environ, "GEL_MAPPING_RESULT": str(result)}
    run = subprocess.run([os.path.join(ROOT, "tools", "mesen", "Mesen.exe"), "--testrunner",
                          str(project / "build" / "game.sfc"), str(script)],
                         cwd=project, env=env, capture_output=True, text=True, timeout=90)
    assert run.returncode == 0, run.stdout + run.stderr
    assert result.read_text() == "4", run.stdout + run.stderr


@pytest.mark.skipif(
    not os.path.isfile(os.path.join(ROOT, "tools", "mesen", "Mesen.exe")),
    reason="Mesen is required",
)
@pytest.mark.parametrize("new_mode,new_size,expected", [
    ("hirom", 256, "2"), ("lorom", 256, "1"), ("hirom", 512, "1"),
])
def test_hot_reload_rejects_mapping_and_size_changes(project, new_mode, new_size, expected):
    source = "u8 result; fn init_game() { result = %d; while (1) { } }\n"
    (project / "game.gel").write_text(source % 1, encoding="utf-8")
    config = project / "gel.json"
    config.write_text(json.dumps({"header": {"rom_mode": "hirom", "rom_size_kb": 256}}))
    rc, out = run_build(project)
    assert rc == 0, out
    initial_rom = project / "initial.sfc"
    initial_rom.write_bytes((project / "build" / "game.sfc").read_bytes())
    initial_hook = project / "initial-hook.lua"
    initial_hook.write_text((project / "build" / "gel_hook.lua").read_text(), encoding="utf-8")
    asm = (project / "build" / "build_output.asm").read_text()
    match = re.search(r"; ram\s+u8\s+result @ \$([0-9A-Fa-f]+)", asm)
    assert match
    address = int(match.group(1), 16)
    (project / "game.gel").write_text(source % 2, encoding="utf-8")
    config.write_text(json.dumps({"header": {"rom_mode": new_mode, "rom_size_kb": new_size}}))
    rc, out = run_build(project)
    assert rc == 0, out
    script = project / "reload-check.lua"
    script.write_text(
        'dofile(os.getenv("GEL_INITIAL_HOOK"))\n'
        'local frame = 0\n'
        'emu.addEventCallback(function()\n'
        '  frame = frame + 1\n'
        '  if frame == 10 then\n'
        '    local file = assert(io.open(os.getenv("GEL_SIGNAL"), "w"))\n'
        '    file:write("9999999999\\nchange.gel\\n" .. os.getenv("GEL_NEW_MODE") .. "\\n")\n'
        '    file:close()\n'
        '  elseif frame == 150 then\n'
        '    local file = assert(io.open(os.getenv("GEL_MAPPING_RESULT"), "w"))\n'
        f'    file:write(tostring(emu.read({address}, emu.memType.snesWorkRam)))\n'
        '    file:close(); emu.stop(0)\n'
        '  end\n'
        'end, emu.eventType.endFrame)\n', encoding="utf-8")
    result = project / "mesen-result.txt"
    env = {**os.environ, "GEL_INITIAL_HOOK": str(initial_hook),
           "GEL_SIGNAL": str(project / "build" / "hotreload.signal"),
           "GEL_NEW_MODE": new_mode, "GEL_MAPPING_RESULT": str(result)}
    run = subprocess.run([os.path.join(ROOT, "tools", "mesen", "Mesen.exe"), "--testrunner",
                          str(initial_rom), str(script)],
                         cwd=project, env=env, capture_output=True, text=True, timeout=90)
    assert run.returncode == 0, run.stdout + run.stderr
    assert result.read_text() == expected, run.stdout + run.stderr


def test_build_links_variant_data_and_initializers(project):
    level = {
        "width": 32, "height": 32, "tilewidth": 8, "tileheight": 8,
        "layers": [
            {"type": "tilelayer", "name": "Tile Layer 1", "data": [0] * 1024},
            {"type": "objectgroup", "name": "objects", "objects": [
                {"id": 1, "class": "Gem", "x": 8, "y": 16,
                 "properties": [{"name": "param", "type": "int", "value": 2}]}]}],
        "tilesets": [{"firstgid": 1, "tilewidth": 8, "tileheight": 8, "tiles": []}],
    }
    (project / "assets" / "level.tmj").write_text(json.dumps(level), encoding="utf-8")
    objects = {"version": 1, "objects": [{
        "class": "Gem", "flags": {"movable": False, "ghost": False, "gravity": False, "contactable": True},
        "editable_fields": ["w", "vx"], "variants": [{
            "id": 2, "name": "Health", "fields": {"w": 20, "vx": 1},
            "values": {"heal_amount": 20}, "metasprite_id": None, "palette_id": None}]}]}
    (project / "objects.json").write_text(json.dumps(objects), encoding="utf-8")
    (project / "game.gel").write_text("""
        #include "assets.gel"
        #include "object-definitions.gel"
        struct_table Entities[4] { u8 w; f16 vx; }
        u16 heal;
        fn init_game() {
            object_apply_variant(0, OBJ_GEM, 2);
            heal = object_variant_value(OBJ_GEM, 2, OBJECT_VALUE_HEAL_AMOUNT);
        }
    """, encoding="utf-8")
    rc, out = run_build(project, "--release")
    assert rc == 0, out
    gel = (project / "build" / "object-definitions.gel").read_text()
    assert "Entities[slot].vx = 1.0;" in gel
    sym = (project / "build" / "game.sym").read_text()
    match = re.search(r"^([0-9A-Fa-f]{6}) _gel_object_variant_values$", sym, re.MULTILINE)
    assert match
    address = int(match.group(1), 16)
    assert address >> 16 == 0x80
    rom = (project / "build" / "game.sfc").read_bytes()
    offset = (address & 0xFFFF) - 0x8000
    assert rom[offset:offset + 5] == bytes([1, 2, 3, 20, 0])
    assert (project / "build" / "level_objects.bin").read_bytes() == bytes([1, 1, 8, 0, 16, 0, 2])


def rom_hash(project):
    with open(project / "build" / "game.sfc", "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def test_second_build_uses_cache_and_gives_identical_rom(project):
    rc, out = run_build(project)
    assert rc == 0, out
    assert "unchanged" not in out
    first = rom_hash(project)
    rc, out = run_build(project)
    assert rc == 0, out
    assert "assets: unchanged" in out and "sound: unchanged" in out
    assert rom_hash(project) == first


def test_cached_build_matches_a_forced_full_build(project):
    run_build(project)
    run_build(project)
    cached = rom_hash(project)
    rc, out = run_build(project, "--no-cache")
    assert rc == 0, out
    assert "unchanged" not in out
    assert rom_hash(project) == cached


def test_changing_an_asset_rebuilds_assets_but_not_sound(project):
    run_build(project)
    before = rom_hash(project)
    make_png(project / "assets" / "hero.png", (0, 255, 0))
    rc, out = run_build(project)
    assert rc == 0, out
    assert "assets: unchanged" not in out
    assert "sound: unchanged" in out
    assert rom_hash(project) != before


def test_corrupted_cached_output_is_rebuilt(project):
    run_build(project)
    good = rom_hash(project)
    pal = project / "build" / "hero.pal"
    pal.write_bytes(b"\x00" * pal.stat().st_size)
    rc, out = run_build(project)
    assert rc == 0, out
    assert "assets: unchanged" not in out
    assert rom_hash(project) == good


def test_deleted_cached_output_is_rebuilt(project):
    run_build(project)
    good = rom_hash(project)
    os.remove(project / "build" / "hero.bin")
    rc, out = run_build(project)
    assert rc == 0, out
    assert (project / "build" / "hero.bin").exists()
    assert rom_hash(project) == good


def test_failed_conversion_is_not_cached_and_recovers(project):
    run_build(project)
    good = rom_hash(project)
    (project / "assets" / "level.tmj").write_text("{ this is not json")
    rc, out = run_build(project)
    assert rc != 0
    (project / "assets" / "level.tmj").unlink()
    rc, out = run_build(project)
    assert rc == 0, out
    assert "assets: unchanged" not in out
    assert rom_hash(project) == good


def test_editor_session_files_do_not_invalidate(project):
    run_build(project)
    (project / "assets" / "x.tiled-session").write_text("{}")
    rc, out = run_build(project)
    assert "assets: unchanged" in out


def test_stale_outputs_of_removed_assets_are_cleaned(project):
    make_png(project / "assets" / "extra.png", (0, 0, 255))
    run_build(project)
    assert (project / "build" / "extra.bin").exists()
    os.remove(project / "assets" / "extra.png")
    rc, out = run_build(project)
    assert rc == 0, out
    assert not (project / "build" / "extra.bin").exists()


@pytest.mark.skipif(not smconv_available(), reason="smconv not built (tools/setup_smconv.bat)")
def test_sound_changes_rebuild_sound_only(project):
    (project / "sfx").mkdir()
    make_wav(project / "sfx" / "beep.wav")
    rc, out = run_build(project)
    assert rc == 0, out
    first = rom_hash(project)
    rc, out = run_build(project)
    assert "sound: unchanged" in out and "assets: unchanged" in out
    assert rom_hash(project) == first
    make_wav(project / "sfx" / "beep.wav", n=1200)
    rc, out = run_build(project)
    assert rc == 0, out
    assert "sound: unchanged" not in out
