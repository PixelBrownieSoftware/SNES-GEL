import json

import pytest

import level_pack


def write_map(path, width=32, height=32, tiles=None, solid_ids=(1,), objects=()):
    data = [0] * (width * height)
    for (x, y), gid in (tiles or {}).items():
        data[y * width + x] = gid
    layers = [{"type": "tilelayer", "name": "Tile Layer 1", "data": data}]
    if objects:
        layers.append({"type": "objectgroup", "name": "objs", "objects": list(objects)})
    tileset = {"firstgid": 1, "tilewidth": 8, "tileheight": 8,
               "tiles": [{"id": i, "properties": [{"name": "solid", "type": "bool", "value": True}]}
                         for i in solid_ids]}
    with open(path, "w") as f:
        json.dump({"width": width, "height": height, "tilewidth": 8, "tileheight": 8,
                   "layers": layers, "tilesets": [tileset]}, f)


def convert(tmp_path, **kw):
    tmj = tmp_path / "level.tmj"
    write_map(tmj, **kw)
    out = tmp_path / "out"
    out.mkdir()
    gel, asm = [], []
    level_pack.reset(0x84)
    level_pack.process_tiled_level(str(tmj), "level", str(out), gel, asm, 0xC000)
    return out, gel, asm


def test_known_map_gives_expected_collision_and_map_bytes(tmp_path):
    # Tiled gid 2 = tile id 1 (solid); gid 3 = tile id 2 (not solid)
    out, gel, _ = convert(tmp_path, tiles={(0, 0): 2, (1, 0): 3, (5, 7): 2})
    coll = (out / "level_coll.bin").read_bytes()
    assert len(coll) == 32 * 32
    assert coll[0] == 1 and coll[1] == 0 and coll[7 * 32 + 5] == 1
    assert sum(coll) == 2
    tilemap = (out / "level_map.bin").read_bytes()
    assert len(tilemap) == 32 * 32 * 2
    first = int.from_bytes(tilemap[0:2], "little")
    assert first & 0x3FF == 1 and first & level_pack.LEVEL_PALETTE_BITS
    assert "#define LEVEL_WIDTH 32" in gel
    assert "#define LEVEL_HEIGHT 32" in gel


def test_objects_become_an_object_table(tmp_path):
    out, gel, _ = convert(tmp_path, objects=[
        {"id": 1, "x": 16, "y": 24, "class": "PlayerStart"},
        {"id": 2, "x": 40, "y": 8, "name": "Gem", "properties": [{"name": "param", "value": 3}]},
    ])
    table = (out / "level_objects.bin").read_bytes()
    assert table[0] == 2
    assert table[1:7] == bytes([1, 16, 0, 24, 0, 0])
    assert table[7:13] == bytes([2, 40, 0, 8, 0, 3])
    assert "#define OBJ_PLAYERSTART 1" in gel
    assert "#define OBJ_GEM 2" in gel


def test_wide_map_uses_two_pages(tmp_path):
    out, gel, _ = convert(tmp_path, width=64, height=32)
    assert (out / "level_coll.bin").stat().st_size == 64 * 32
    assert "#define LEVEL_PAGES 2" in gel


def test_bad_width_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        convert(tmp_path, width=40, height=32)
