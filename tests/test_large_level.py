import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "compiler" / "build.py"
MESEN = ROOT / "tools" / "mesen" / "Mesen.exe"
pytestmark = pytest.mark.skipif(sys.platform != "win32" or not MESEN.is_file(), reason="Mesen and Asar required")


@pytest.mark.parametrize("mode", ["lorom", "hirom"])
def test_full_game_large_level_in_mesen(tmp_path, mode):
    project = tmp_path / "game"
    shutil.copytree(ROOT / "game", project, ignore=shutil.ignore_patterns("build"))
    path = project / "assets" / "level.tmj"
    data = json.loads(path.read_text(encoding="utf-8"))
    width, height = 1024, 96
    data["width"], data["height"] = width, height
    for layer in data["layers"]:
        if layer.get("type") == "tilelayer":
            layer["width"], layer["height"] = width, height
            layer["data"] = [1 + (x // 7 + y // 5) % 7
                             for y in range(height) for x in range(width)]
    path.write_text(json.dumps(data), encoding="utf-8")
    config_path = project / "gel.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["header"]["rom_mode"] = mode
    config_path.write_text(json.dumps(config), encoding="utf-8")
    build = subprocess.run([sys.executable, str(BUILD), str(project), "--release"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert build.returncode == 0, build.stdout + build.stderr
    asm = (project / "build" / "build_output.asm").read_text()

    def address(name):
        found = re.search(rf"; ram\s+\w+\s+{name} @ \$([0-9A-Fa-f]+)", asm)
        assert found, name
        return int(found.group(1), 16)

    actor = re.search(r"; table Entities\[10\].*?;   \.x u16 @ \$([0-9A-Fa-f]+).*?"
                      r";   \.y u16 @ \$([0-9A-Fa-f]+)", asm, re.DOTALL)
    assert actor
    actor_x, actor_y = (int(value, 16) for value in actor.groups())
    script = project / "full-game.lua"
    script.write_text(
        f"local camx, camy, ready, hud, actorx, actory = {address('cam_x')}, {address('cam_y')}, "
        f"{address('level_stream_ready')}, {address('hud_char')}, {actor_x}, {actor_y}\n"
        'local wram, vram = emu.memType.snesWorkRam, emu.memType.snesVideoRam\n'
        'local function ww(a,v) emu.write(a,v & 255,wram); emu.write(a+1,(v >> 8) & 255,wram) end\n'
        'local function rw(a) return emu.read(a,wram) | (emu.read(a+1,wram) << 8) end\n'
        'local function check(x,y)\n'
        '  local wx, wy = x // 8, y // 8\n'
        '  local off = ((wx // 32) % 2) * 2048 + (wy % 32) * 64 + (wx % 32) * 2\n'
        '  local actual = emu.read(0x8000+off,vram) | (emu.read(0x8001+off,vram) << 8)\n'
        '  return actual == (((wx // 7 + wy // 5) % 7) | 0x400)\n'
        'end\n'
        'local frame, active = 0, 0\n'
        'emu.addEventCallback(function()\n'
        ' frame = frame + 1\n'
        ' if emu.read(ready,wram) ~= 0 then active = active + 1 end\n'
        ' local x,y = rw(camx),rw(camy)\n'
        ' if active > 10 and (not check(x,y) or not check(x+255,y) or '
        'not check(x,y+223) or not check(x+255,y+223)) then\n'
        '   local f=assert(io.open(os.getenv("GEL_STREAM_RESULT"),"w")); '
        'f:write(string.format("bad frame %d camera %d,%d",frame,x,y)); f:close(); emu.stop(0)\n'
        ' elseif active == 2000 then\n'
        '   local f=assert(io.open(os.getenv("GEL_STREAM_RESULT"),"w")); '
        'f:write(x == 6881 and y == 304 and "ok" or string.format("stuck %d,%d",x,y)); f:close(); emu.stop(0)\n'
        ' elseif active > 10 then\n'
        '   ww(actorx,7000); ww(actory,400); emu.write(hud,255,wram)\n'
        ' elseif frame > 900 then\n'
        '   local f=assert(io.open(os.getenv("GEL_STREAM_RESULT"),"w")); '
        'f:write("game did not start"); f:close(); emu.stop(0)\n'
        ' end\n'
        'end, emu.eventType.endFrame)\n', encoding="utf-8")
    result = project / "full-game.txt"
    run = subprocess.run([str(MESEN), "--testrunner", str(project / "build" / "game.sfc"), str(script)],
                         cwd=project, env={**os.environ, "GEL_STREAM_RESULT": str(result)},
                         capture_output=True, text=True, timeout=240)
    assert run.returncode == 0, run.stdout + run.stderr
    assert result.read_text() == "ok", (mode, result.read_text())


@pytest.mark.parametrize("mode", ["lorom", "hirom"])
@pytest.mark.parametrize(("width", "height"), [(4064, 32), (32, 4095), (96, 65)])
def test_extreme_chunked_levels_in_mesen(tmp_path, mode, width, height):
    assets = tmp_path / "assets"
    assets.mkdir()
    image = Image.new("P", (16, 16))
    image.putpalette([0, 0, 0] * 256)
    image.save(assets / "terrain.png")
    tiles = [1 + (x // 7 + y // 5) % 7 for y in range(height) for x in range(width)]
    data = {
        "width": width, "height": height, "tilewidth": 8, "tileheight": 8,
        "layers": [{"type": "tilelayer", "name": "tiles", "data": tiles}],
        "tilesets": [{"firstgid": 1, "tilewidth": 8, "tileheight": 8, "tiles": []}],
    }
    (assets / "level.tmj").write_text(json.dumps(data), encoding="utf-8")
    (tmp_path / "gel.json").write_text(json.dumps({"header": {"rom_mode": mode}}), encoding="utf-8")
    source = (ROOT / "game" / "game.gel").read_text(encoding="utf-8")
    camera = "i16 cam_calc_x" + source.split("i16 cam_calc_x", 1)[1]
    camera = camera.split("// Advances the selected clip", 1)[0]
    reader = (ROOT / "game" / "level-data.gel").read_text(encoding="utf-8")
    stream = (ROOT / "game" / "level-stream.gel").read_text(encoding="utf-8")
    max_x, max_y = width * 8 - 256, height * 8 - 224
    start_x, start_y = max(0, max_x - 128), max(0, max_y - 128)
    (tmp_path / "game.gel").write_text(
        '#include "snes.gel"\n#include "assets.gel"\n'
        '#define CAM_OFFSET_X 0\n#define CAM_OFFSET_Y 0\n'
        'u16 cam_x; u16 cam_y; u8 collision_result; '
        'struct_table Entities[1] { u16 x; u16 y; }\n'
        + reader + stream + camera +
        f'\nfn init_game() {{ std_snes_init(); cam_x = {start_x}; cam_y = {start_y}; '
        f'Entities[0].x = {start_x}; Entities[0].y = {start_y}; '
        'level_stream_init(); INIDISP = 15; while (1) { std_wait_vblank(); '
        'level_stream(); update_camera(0); std_set_scroll(cam_x & 0x1FF, cam_y & 0xFF); '
        'collision_result = collision_code_at(cam_x, cam_y); } }\n',
        encoding="utf-8")
    build = subprocess.run([sys.executable, str(BUILD), str(tmp_path), "--release"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert build.returncode == 0, build.stdout + build.stderr
    asm = (tmp_path / "build" / "build_output.asm").read_text()

    def address(name):
        found = re.search(rf"; ram\s+\w+\s+{name} @ \$([0-9A-Fa-f]+)", asm)
        assert found, name
        return int(found.group(1), 16)

    actor = re.search(r"; table Entities\[1\].*?;   \.x u16 @ \$([0-9A-Fa-f]+).*?"
                      r";   \.y u16 @ \$([0-9A-Fa-f]+)", asm, re.DOTALL)
    assert actor
    actor_x, actor_y = (int(value, 16) for value in actor.groups())
    script = tmp_path / "extreme.lua"
    script.write_text(
        f"local camx, camy, collision, actorx, actory = {address('cam_x')}, {address('cam_y')}, "
        f"{address('collision_result')}, {actor_x}, {actor_y}\n"
        f"local maxx, maxy, startx, starty = {max_x}, {max_y}, {start_x}, {start_y}\n"
        'local wram, vram = emu.memType.snesWorkRam, emu.memType.snesVideoRam\n'
        'local function ww(a,v) emu.write(a,v & 255,wram); emu.write(a+1,(v >> 8) & 255,wram) end\n'
        'local function rw(a) return emu.read(a,wram) | (emu.read(a+1,wram) << 8) end\n'
        'local function check(x,y)\n'
        '  local wx, wy = x // 8, y // 8\n'
        '  local off = ((wx // 32) % 2) * 2048 + (wy % 32) * 64 + (wx % 32) * 2\n'
        '  local actual = emu.read(0x8000+off,vram) | (emu.read(0x8001+off,vram) << 8)\n'
        '  return actual == (((wx // 7 + wy // 5) % 7) | 0x400)\n'
        'end\n'
        'local frame = 0\n'
        'emu.addEventCallback(function()\n'
        ' frame = frame + 1\n'
        ' local x,y = rw(camx),rw(camy)\n'
        ' local expected = ((x // 8 // 7 + y // 8 // 5) % 7 == 0) and 0 or 1\n'
        ' if frame > 2 and (not check(x,y) or not check(x+255,y) or '
        'not check(x,y+223) or not check(x+255,y+223) or emu.read(collision,wram) ~= expected) then\n'
        '   local f=assert(io.open(os.getenv("GEL_STREAM_RESULT"),"w")); '
        'f:write(string.format("bad frame %d camera %d,%d collision %d expected %d",frame,x,y,emu.read(collision,wram),expected)); '
        'f:close(); emu.stop(0)\n'
        ' elseif frame == 100 then\n'
        '   local f=assert(io.open(os.getenv("GEL_STREAM_RESULT"),"w")); '
        'f:write("ok"); f:close(); emu.stop(0)\n'
        ' elseif frame > 2 then\n'
        '   local d = frame < 55 and (frame-2)*5 or (100-frame)*5\n'
        '   ww(actorx,math.min(maxx,startx+d)); ww(actory,math.min(maxy,starty+d))\n'
        ' end\n'
        'end, emu.eventType.endFrame)\n', encoding="utf-8")
    result = tmp_path / "extreme.txt"
    run = subprocess.run([str(MESEN), "--testrunner", str(tmp_path / "build" / "game.sfc"), str(script)],
                         cwd=tmp_path, env={**os.environ, "GEL_STREAM_RESULT": str(result)},
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert result.read_text() == "ok", (mode, width, height, result.read_text())


@pytest.mark.parametrize("mode", ["lorom", "hirom"])
def test_large_level_streaming_and_collision_in_mesen(tmp_path, mode):
    assets = tmp_path / "assets"
    assets.mkdir()
    image = Image.new("P", (16, 16))
    image.putpalette([0, 0, 0] * 256)
    image.save(assets / "terrain.png")
    tiles = [1 + (x // 7 + y // 5) % 7 for y in range(256) for x in range(256)]
    map_data = {
        "width": 256, "height": 256, "tilewidth": 8, "tileheight": 8,
        "layers": [{"type": "tilelayer", "name": "tiles", "width": 256, "height": 256, "data": tiles}],
        "tilesets": [{"firstgid": 1, "tilewidth": 8, "tileheight": 8, "tiles": []}],
    }
    (assets / "level.tmj").write_text(json.dumps(map_data), encoding="utf-8")
    (tmp_path / "gel.json").write_text(json.dumps({"header": {"rom_mode": mode}}), encoding="utf-8")
    source = (ROOT / "game" / "game.gel").read_text(encoding="utf-8")
    stream = (ROOT / "game" / "level-stream.gel").read_text(encoding="utf-8")
    reader = (ROOT / "game" / "level-data.gel").read_text(encoding="utf-8")
    (tmp_path / "game.gel").write_text(
        '#include "snes.gel"\n#include "assets.gel"\n'
        'u16 cam_x; u16 cam_y; u8 collision_result;\n' + reader + stream +
        '\nfn init_game() { std_snes_init(); level_stream_init(); INIDISP = 15; '
        'while (1) { std_wait_vblank(); level_stream(); '
        'std_set_scroll(cam_x & 0x1FF, cam_y & 0xFF); '
        'collision_result = collision_code_at(cam_x, cam_y); } }\n',
        encoding="utf-8")
    build = subprocess.run([sys.executable, str(BUILD), str(tmp_path), "--release"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert build.returncode == 0, build.stdout + build.stderr
    asm = (tmp_path / "build" / "build_output.asm").read_text()

    def address(name):
        found = re.search(rf"; ram\s+\w+\s+{name} @ \$([0-9A-Fa-f]+)", asm)
        assert found, name
        return int(found.group(1), 16)

    targets = [(0, 0), (256, 256), (512, 512), (1024, 1024),
               (1792, 1824), (800, 200), (16, 16), (0, 0)]
    target_lua = ", ".join(f"{{{x},{y}}}" for x, y in targets)
    script = tmp_path / "stream.lua"
    script.write_text(
        f"local targets = {{{target_lua}}}\n"
        f"local camx, camy, collision = {address('cam_x')}, {address('cam_y')}, {address('collision_result')}\n"
        'local wram, vram = emu.memType.snesWorkRam, emu.memType.snesVideoRam\n'
        'local function ww(a,v) emu.write(a,v & 255,wram); emu.write(a+1,(v >> 8) & 255,wram) end\n'
        'local frame, index = 0, 1\n'
        'local file = assert(io.open(os.getenv("GEL_STREAM_RESULT"), "w"))\n'
        'emu.addEventCallback(function()\n'
        '  frame = frame + 1\n'
        '  if frame == 1 then ww(camx,targets[index][1]); ww(camy,targets[index][2]) end\n'
        '  if frame == 600 then\n'
        '    file:write(tostring(emu.read(collision,wram)) .. " ")\n'
        '    for a=0x8000,0x8FFF do file:write(string.format("%02x",emu.read(a,vram))) end\n'
        '    file:write("\\n"); index=index+1; frame=0\n'
        '    if index > #targets then file:close(); emu.stop(0) end\n'
        '  end\n'
        'end, emu.eventType.endFrame)\n', encoding="utf-8")
    result = tmp_path / "stream.txt"
    run = subprocess.run([str(MESEN), "--testrunner", str(tmp_path / "build" / "game.sfc"), str(script)],
                         cwd=tmp_path, env={**os.environ, "GEL_STREAM_RESULT": str(result)},
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    lines = result.read_text().splitlines()
    assert len(lines) == len(targets)
    for (x, y), line in zip(targets, lines):
        collision, data = line.split()
        tile_x, tile_y = x // 8, y // 8
        assert int(collision) == int(tiles[tile_y * 256 + tile_x] != 1), (mode, x, y)
        vram = bytes.fromhex(data)
        for wy in range(tile_y, min(tile_y + 28, 256)):
            for wx in range(tile_x, min(tile_x + 32, 256)):
                offset = ((wx // 32) % 2) * 2048 + (wy % 32) * 64 + (wx % 32) * 2
                expected = (tiles[wy * 256 + wx] - 1) | 0x400
                actual = int.from_bytes(vram[offset:offset + 2], "little")
                assert actual == expected, (mode, x, y, wx, wy, hex(actual), hex(expected))

    motion = tmp_path / "motion.lua"
    motion.write_text(
        f"local camx, camy = {address('cam_x')}, {address('cam_y')}\n"
        'local wram, vram = emu.memType.snesWorkRam, emu.memType.snesVideoRam\n'
        'local function ww(a,v) emu.write(a,v & 255,wram); emu.write(a+1,(v >> 8) & 255,wram) end\n'
        'local function check(x,y)\n'
        '  local wx, wy = x // 8, y // 8\n'
        '  local off = ((wx // 32) % 2) * 2048 + (wy % 32) * 64 + (wx % 32) * 2\n'
        '  local actual = emu.read(0x8000+off,vram) | (emu.read(0x8001+off,vram) << 8)\n'
        '  local expected = ((wx // 7 + wy // 5) % 7) | 0x400\n'
        '  return actual == expected\n'
        'end\n'
        'local frame, x, y = 0, 0, 0\n'
        'emu.addEventCallback(function()\n'
        '  frame = frame + 1\n'
        '  if frame > 2 and (not check(x,y) or not check(x+255,y) or '
        'not check(x,y+223) or not check(x+255,y+223)) then\n'
        '    local file = assert(io.open(os.getenv("GEL_STREAM_RESULT"), "w"))\n'
        '    file:write(string.format("bad frame %d camera %d,%d",frame,x,y)); file:close(); emu.stop(0)\n'
        '  elseif frame == 700 then\n'
        '    local file = assert(io.open(os.getenv("GEL_STREAM_RESULT"), "w"))\n'
        '    file:write("ok"); file:close(); emu.stop(0)\n'
        '  elseif frame > 2 then\n'
        '    if frame < 360 then x=math.min(1792,x+5); y=math.min(1824,y+5)\n'
        '    else x=math.max(0,x-5); y=math.max(0,y-5) end\n'
        '    ww(camx,x); ww(camy,y)\n'
        '  end\n'
        'end, emu.eventType.endFrame)\n', encoding="utf-8")
    run = subprocess.run([str(MESEN), "--testrunner", str(tmp_path / "build" / "game.sfc"), str(motion)],
                         cwd=tmp_path, env={**os.environ, "GEL_STREAM_RESULT": str(result)},
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert result.read_text() == "ok"

    camera = "i16 cam_calc_x" + source.split("i16 cam_calc_x", 1)[1]
    camera = camera.split("// Advances the selected clip", 1)[0]
    (tmp_path / "game.gel").write_text(
        '#include "snes.gel"\n#include "assets.gel"\n'
        '#define CAM_OFFSET_X 0\n#define CAM_OFFSET_Y 0\n'
        'u16 cam_x; u16 cam_y; struct_table Entities[1] { u16 x; u16 y; }\n'
        + reader + stream + camera +
        '\nfn init_game() { std_snes_init(); level_stream_init(); INIDISP = 15; '
        'while (1) { std_wait_vblank(); level_stream(); update_camera(0); '
        'std_set_scroll(cam_x & 0x1FF, cam_y & 0xFF); } }\n',
        encoding="utf-8")
    build = subprocess.run([sys.executable, str(BUILD), str(tmp_path), "--release"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert build.returncode == 0, build.stdout + build.stderr
    asm = (tmp_path / "build" / "build_output.asm").read_text()
    actor = re.search(r"; table Entities\[1\].*?;   \.x u16 @ \$([0-9A-Fa-f]+).*?"
                      r";   \.y u16 @ \$([0-9A-Fa-f]+)", asm, re.DOTALL)
    assert actor
    actor_x, actor_y = (int(value, 16) for value in actor.groups())
    teleport = tmp_path / "teleport.lua"
    teleport.write_text(
        f"local camx, camy, actorx, actory = {address('cam_x')}, {address('cam_y')}, {actor_x}, {actor_y}\n"
        'local wram, vram = emu.memType.snesWorkRam, emu.memType.snesVideoRam\n'
        'local function ww(a,v) emu.write(a,v & 255,wram); emu.write(a+1,(v >> 8) & 255,wram) end\n'
        'local function rw(a) return emu.read(a,wram) | (emu.read(a+1,wram) << 8) end\n'
        'local function check(x,y)\n'
        '  local wx, wy = x // 8, y // 8\n'
        '  local off = ((wx // 32) % 2) * 2048 + (wy % 32) * 64 + (wx % 32) * 2\n'
        '  local actual = emu.read(0x8000+off,vram) | (emu.read(0x8001+off,vram) << 8)\n'
        '  return actual == (((wx // 7 + wy // 5) % 7) | 0x400)\n'
        'end\n'
        'local frame = 0\n'
        'emu.addEventCallback(function()\n'
        '  frame = frame + 1\n'
        '  if frame == 10 then ww(actorx,1792); ww(actory,1824) end\n'
        '  local x,y = rw(camx),rw(camy)\n'
        '  if frame > 10 and (not check(x,y) or not check(x+255,y) or '
        'not check(x,y+223) or not check(x+255,y+223)) then\n'
        '    local file = assert(io.open(os.getenv("GEL_STREAM_RESULT"), "w"))\n'
        '    file:write(string.format("bad frame %d camera %d,%d",frame,x,y)); file:close(); emu.stop(0)\n'
        '  elseif frame == 700 then\n'
        '    local file = assert(io.open(os.getenv("GEL_STREAM_RESULT"), "w"))\n'
        '    file:write(x == 1792 and y == 1824 and "ok" or string.format("stuck %d,%d",x,y))\n'
        '    file:close(); emu.stop(0)\n'
        '  end\n'
        'end, emu.eventType.endFrame)\n', encoding="utf-8")
    run = subprocess.run([str(MESEN), "--testrunner", str(tmp_path / "build" / "game.sfc"), str(teleport)],
                         cwd=tmp_path, env={**os.environ, "GEL_STREAM_RESULT": str(result)},
                         capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert result.read_text() == "ok"
