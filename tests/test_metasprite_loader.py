from pathlib import Path

import metasprite_pack

ASSETS = Path(__file__).resolve().parents[1] / "game" / "assets"


def pack_all(tmp_path):
    infos, pal, offset = [], 0, 0xC000
    for path, data in metasprite_pack.load_projects(str(ASSETS)):
        info = {}
        offset = metasprite_pack.pack_project(path, data, str(ASSETS), str(tmp_path), [], [], offset,
                                              pal_base=pal, info=info)
        pal += len(info["slots"])
        infos.append(info)
    metasprite_pack.write_loader(infos, str(tmp_path))
    return infos, (tmp_path / "metasprites.gel").read_text(encoding="utf-8")


def test_loader_covers_every_project_with_consecutive_ids_tiles_and_palettes(tmp_path):
    infos, text = pack_all(tmp_path)
    assert len(infos) >= 2
    assert f"#define MS_COUNT {len(infos)}" in text
    assert f"struct_table MetaspriteHeader[{len(infos)}]" in text
    tile = 0
    for n, info in enumerate(infos):
        p = info["prefix"]
        assert f"#define {p}_ID {n}" in text
        assert f"#define {p}_TILE_BASE {tile}" in text
        assert f"MetaspriteHeader[{n}].tile_base = {tile};" in text
        assert f"std_load_vram({tile} * 16, {p}_TILES, {p}_BYTE_LEN);" in text
        tile += info["tile_count"]
    assert f"#define MS_TILE_END {tile}" in text
    bases = [info["pal_base"] for info in infos]
    assert bases == sorted(set(bases))
    assert f"#define MS_PAL_END {bases[-1] + len(infos[-1]['slots'])}" in text
