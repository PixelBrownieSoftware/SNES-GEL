import json

import pytest

import level_pack


def write_map(path, width=32, height=32, tiles=None, solid_ids=(1,), slope_profiles=None, objects=()):
    data = [0] * (width * height)
    for (x, y), gid in (tiles or {}).items():
        data[y * width + x] = gid
    layers = [{"type": "tilelayer", "name": "Tile Layer 1", "data": data}]
    if objects:
        layers.append({"type": "objectgroup", "name": "objs", "objects": list(objects)})
    tile_props = []
    for i in set(solid_ids) | set(slope_profiles or {}):
        properties = []
        if i in solid_ids:
            properties.append({"name": "solid", "type": "bool", "value": True})
        if slope_profiles and i in slope_profiles:
            start, end = slope_profiles[i]
            properties.extend([
                {"name": "slope_start", "type": "int", "value": start},
                {"name": "slope_end", "type": "int", "value": end},
            ])
        tile_props.append({"id": i, "properties": properties})
    tileset = {"firstgid": 1, "tilewidth": 8, "tileheight": 8, "tiles": tile_props}
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


@pytest.mark.parametrize("bank_limit", [0xC0, 0x100])
def test_bank_limit_rejects_mirrored_or_out_of_range_banks(tmp_path, bank_limit, capsys):
    level_pack.reset(bank_limit - 1, bank_limit)
    ids = [[0] * 32 for _ in range(32)]
    gel, asm = [], []
    level_pack.emit_level(ids, None, "level", "level", str(tmp_path), gel, asm, 0xC000)
    assert f"#define LEVEL_BANK 0x{bank_limit - 1:02X}" in gel
    with pytest.raises(SystemExit):
        level_pack.emit_level(ids, None, "level2", "level2", str(tmp_path), gel, asm, 0xC000)
    assert "no addressable ROM bank remains" in capsys.readouterr().out
    level_pack.reset()


def test_large_level_pages_and_collision_are_banked(tmp_path):
    out, gel, asm = convert(tmp_path, width=256, height=256,
                            tiles={(0, 0): 2, (31, 255): 2, (32, 0): 2, (255, 255): 2})
    assert "#define LEVEL_BANKED 1" in gel
    assert "#define LEVEL_BANK 0x84" in gel
    assert "#define LEVEL_MAP_BANK 0x85" in gel
    assert "#define LEVEL_COLL_BANK 0x89" in gel
    assert "#define LEVEL_SLOPES 0x8000" in gel
    assert len((out / "level_map.bin").read_bytes()) == 256 * 256 * 2
    coll = (out / "level_coll_chunks.bin").read_bytes()
    for x, y in ((0, 0), (31, 255), (32, 0), (255, 255)):
        chunk = (y // 32) * 8 + x // 32
        assert coll[chunk * 1024 + (y % 32) * 32 + x % 32] == 1
        assert (out / "level_coll.bin").read_bytes()[y * 256 + x] == 1
    assert sum(line.startswith("org $") for line in asm) >= 8
    assert any(line == "org $8A8000" for line in asm)


def test_large_level_metadata_and_bank_allocation(tmp_path):
    out, gel, asm = convert(
        tmp_path, width=256, height=256, tiles={(31, 5): 3, (32, 5): 3},
        solid_ids=(), slope_profiles={2: (0, 8)},
        objects=({"id": 1, "class": "Gem", "x": 1800, "y": 1200},))
    assert "#define LEVEL_SLOPES 0x8000" in gel
    assert "#define LEVEL_OBJECTS 0x8008" in gel
    assert "#define LEVEL_OBJECT_COUNT 1" in gel
    assert (out / "level_objects.bin").read_bytes() == bytes([1, 1, 8, 7, 176, 4, 0])
    assert len((out / "level_slopes.bin").read_bytes()) == 8
    collision = (out / "level_coll_chunks.bin").read_bytes()
    assert collision[5 * 32 + 31] == 0x80
    assert collision[1024 + 5 * 32] == 0x80
    assert "org $848000" in asm
    ids = [[0] * 32 for _ in range(32)]
    next_gel = []
    level_pack.emit_level(ids, None, "next", "next", str(out), next_gel, [], 0xC000)
    assert "#define NEXT_BANK 0x8B" in next_gel


@pytest.mark.parametrize(("width", "height", "expected"), [
    (4064, 32, 13),
    (32, 4095, 13),
    (96, 65, 3),
    (256, 256, 7),
    (64, 157, 1),
    (64, 158, 3),
])
def test_chunk_bank_budget(width, height, expected):
    assert level_pack.level_size_error(width, height) is None
    assert level_pack.level_bank_count(width, height) == expected


@pytest.mark.parametrize(("width", "height"), [(31, 32), (33, 32), (4096, 32),
                                               (32, 31), (32, 4096)])
def test_dimension_limits(width, height):
    assert level_pack.level_size_error(width, height)


def test_large_level_rejects_bank_exhaustion_before_allocation(capsys):
    level_pack.reset(0xBE, 0xC0)
    with pytest.raises(SystemExit):
        level_pack.check_level_size("level", 96, 65)
    assert "no addressable ROM bank remains" in capsys.readouterr().out
    assert level_pack._next_bank[0] == 0xBE
    level_pack.reset()


def test_partial_chunk_rows_are_padded(tmp_path):
    out, gel, _ = convert(tmp_path, width=96, height=65, tiles={(95, 64): 2})
    assert "#define LEVEL_BANKED 1" in gel
    collision = (out / "level_coll_chunks.bin").read_bytes()
    last_chunk = ((64 // 32) * 3 + 95 // 32) * 1024
    assert collision[last_chunk + 31] == 1
    assert collision[last_chunk + 32:last_chunk + 1024] == b"\x01" * (31 * 32)
    tilemap = (out / "level_map_chunks.bin").read_bytes()
    assert int.from_bytes(tilemap[last_chunk * 2 + 62:last_chunk * 2 + 64], "little") == 0x401
    assert tilemap[last_chunk * 2 + 64:last_chunk * 2 + 2048] == bytes(31 * 64)


def slope_surfaces(out, coll_byte):
    """Surface heights (pixels from the tile top, biased by 8) of a slope cell's 8 columns."""
    assert coll_byte & 0x80
    table = (out / "level_slopes.bin").read_bytes()
    index = (coll_byte & 0x7F) * 8
    return list(table[index:index + 8])


def profile(start, delta):
    """Expected biased surfaces for a (start, delta) profile given in pixels."""
    return [level_pack._slope_surface((round(start * 8), round(delta * 8)), px) + 8 for px in range(8)]


def slope_entry(out, coll_byte):
    return slope_surfaces(out, coll_byte)


def test_slope_collision_codes_and_horizontal_flip(tmp_path):
    # Tiled gid 3 is local tile 2; horizontal flip reverses the profile endpoints.
    out, gel, _ = convert(tmp_path, tiles={(0, 0): 3, (1, 0): 0x80000003},
                          slope_profiles={2: (5, 1)})
    coll = (out / "level_coll.bin").read_bytes()
    assert slope_entry(out, coll[0]) == profile(5, -4)
    assert slope_entry(out, coll[1]) == profile(1, 4)
    assert any(line.startswith("#define LEVEL_SLOPES ") for line in gel)


def test_slope_endpoint_can_extend_into_neighboring_tile_row(tmp_path):
    out, _, _ = convert(tmp_path, tiles={(0, 0): 3}, slope_profiles={2: (5, -3)})
    coll = (out / "level_coll.bin").read_bytes()
    assert slope_entry(out, coll[0]) == profile(5, -8)


def test_flipped_slope_is_packed_in_its_surface_row(tmp_path):
    out, _, _ = convert(tmp_path, tiles={(0, 0): 0x80000003},
                        slope_profiles={2: (7, 10)})
    coll = (out / "level_coll.bin").read_bytes()
    assert coll[0] == 0x40
    assert slope_entry(out, coll[32]) == profile(2, -3)


def test_empty_tiles_beside_slopes_are_marked_near_slope(tmp_path):
    out, _, _ = convert(tmp_path, tiles={(0, 1): 3}, slope_profiles={2: (0, 7)})
    coll = (out / "level_coll.bin").read_bytes()
    assert coll[0] == 0x40
    assert coll[64] == 0x40
    assert coll[1] == 0


def test_slope_starting_on_the_tile_bottom_stays_in_its_own_row(tmp_path):
    out, _, _ = convert(tmp_path, tiles={(0, 0): 3, (0, 1): 2}, slope_profiles={2: (8, 0)})
    coll = (out / "level_coll.bin").read_bytes()
    assert slope_entry(out, coll[0]) == profile(8, -8)
    assert coll[32] == 1


def test_fractional_endpoints_make_a_straight_ramp(tmp_path):
    # A 1-in-3 ramp over three tiles (eighth-pixel endpoints) rises evenly, with no kinks.
    out, _, _ = convert(tmp_path, tiles={(0, 0): 3, (1, 0): 4, (2, 0): 5},
                        slope_profiles={2: (0, 2.625), 3: (2.625, 5.375), 4: (5.375, 8)})
    coll = (out / "level_coll.bin").read_bytes()
    surfaces = [s - 8 for x in range(3) for s in slope_entry(out, coll[x])]
    assert surfaces[-1] == 8
    assert all(0 <= b - a <= 1 for a, b in zip(surfaces, surfaces[1:]))
    assert all(abs(s - (i + 1) / 3) <= 1 for i, s in enumerate(surfaces))


def test_slope_too_steep_for_one_tile_is_rejected(tmp_path):
    with pytest.raises(SystemExit):
        convert(tmp_path, tiles={(0, 0): 3}, slope_profiles={2: (0, 16)})


def test_slope_endpoints_must_differ(tmp_path):
    with pytest.raises(SystemExit):
        convert(tmp_path, tiles={(0, 0): 3}, slope_profiles={2: (4, 4)})


def test_slope_tiles_cannot_be_vertically_flipped(tmp_path):
    with pytest.raises(SystemExit):
        convert(tmp_path, tiles={(0, 0): 0x40000003}, slope_profiles={2: (5, 1)})


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
