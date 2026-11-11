import importlib.util
import io
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from PIL import Image

import level_pack


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gel_tile_test_server", ROOT / "tools" / "editor" / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
levels = server.tile_levels


@pytest.fixture
def project(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "terrain.png").write_bytes((ROOT / "game" / "assets" / "terrain.png").read_bytes())
    (assets / "terrain.tsj").write_bytes((ROOT / "game" / "assets" / "terrain.tsj").read_bytes())
    (assets / "level.tmj").write_bytes((ROOT / "game" / "assets" / "level.tmj").read_bytes())
    (assets / "gem.metasprite.json").write_bytes((ROOT / "game" / "assets" / "gem.metasprite.json").read_bytes())
    (tmp_path / "objects.json").write_bytes((ROOT / "game" / "objects.json").read_bytes())
    return tmp_path


def test_real_map_read_and_edit_preserves_unedited_content(project):
    assets = project / "assets"
    loaded = levels.load(assets, project, "level.tmj")
    assert (loaded["map"]["width"], loaded["map"]["height"], loaded["max_height"]) == (64, 130, 4095)
    assert loaded["sprites"][0]["id"] == 0
    before = loaded["map"]
    obj = next(layer for layer in before["layers"] if layer["type"] == "objectgroup")["objects"][0]
    target, content = levels.apply(assets, project, {
        "name": "level.tmj", "project_path": loaded["project_path"], "hash": loaded["hash"],
        "tiles": [{"layer": loaded["editable_layers"][0], "index": 0, "gid": loaded["firstgid"] + 2}],
        "objects": [{"id": obj["id"], "x": 42, "y": 53, "param": 1}],
    })
    after = json.loads(content)
    assert target == assets / "level.tmj"
    assert after["layers"][0]["data"][0] == loaded["firstgid"] + 2
    edited = next(layer for layer in after["layers"] if layer["type"] == "objectgroup")["objects"][0]
    assert (edited["x"], edited["y"]) == (42, 53)
    assert next(prop["value"] for prop in edited["properties"] if prop["name"] == "param") == 1
    assert after["tilesets"] == before["tilesets"]
    assert after["layers"][0]["data"][1:] == before["layers"][0]["data"][1:]
    assert (assets / "level.tmj").read_bytes() != content
    target.write_bytes(content)
    output = project / "build"
    output.mkdir()
    level_pack.reset()
    level_pack.process_tiled_level(str(target), "level", str(output), [], [], 0xC000)
    assert (output / "level_map.bin").is_file()


def test_changes_reject_stale_hash_bad_ids_and_coordinates(project):
    assets = project / "assets"
    loaded = levels.load(assets, project, "level.tmj")
    base = {"name": "level.tmj", "project_path": loaded["project_path"], "hash": loaded["hash"]}
    with pytest.raises(ValueError, match="project changed"):
        levels.apply(assets, project, {**base, "project_path": "different project"})
    with pytest.raises(ValueError, match="changed on disk"):
        levels.apply(assets, project, {**base, "hash": "stale"})
    with pytest.raises(ValueError, match="tile layer"):
        levels.apply(assets, project, {**base, "tiles": [{"layer": 999, "index": 0, "gid": 1}]})
    with pytest.raises(ValueError, match="position"):
        levels.apply(assets, project, {**base, "objects": [{"id": 1, "x": -1, "y": 0, "param": 0}]})
    with pytest.raises(ValueError, match="filename"):
        levels.load(assets, project, "..\\level.tmj")


@pytest.mark.parametrize(("width", "height"), [(256, 256), (4064, 32), (32, 4095), (96, 65)])
def test_large_map_loads_and_edits_across_page_boundaries(project, width, height):
    assets = project / "assets"
    path = assets / "level.tmj"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["width"], data["height"] = width, height
    layer = next(layer for layer in data["layers"] if layer["type"] == "tilelayer")
    layer["width"], layer["height"] = width, height
    layer["data"] = [0] * (width * height)
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = levels.load(assets, project, "level.tmj")
    assert loaded["max_height"] == 4095
    _, content = levels.apply(assets, project, {
        "name": "level.tmj", "project_path": loaded["project_path"], "hash": loaded["hash"],
        "tiles": [{"layer": layer["id"], "index": width * height - 1, "gid": loaded["firstgid"]}],
    })
    assert json.loads(content)["layers"][0]["data"][-1] == loaded["firstgid"]


def test_stamped_flipped_tiles_preserve_tiled_flags(project):
    assets = project / "assets"
    loaded = levels.load(assets, project, "level.tmj")
    gid = 0xC0000000 | loaded["firstgid"]
    _, content = levels.apply(assets, project, {
        "name": loaded["name"], "project_path": loaded["project_path"], "hash": loaded["hash"],
        "tiles": [{"layer": loaded["editable_layers"][0], "index": 0, "gid": gid}],
    })
    assert json.loads(content)["layers"][0]["data"][0] == gid
    with pytest.raises(ValueError, match="tileset ID"):
        levels.apply(assets, project, {
            "name": loaded["name"], "project_path": loaded["project_path"], "hash": loaded["hash"],
            "tiles": [{"layer": loaded["editable_layers"][0], "index": 0, "gid": 0x80000000 | 999}],
        })


def test_tile_editor_server_roundtrip(project):
    editor = server.EditorServer(("127.0.0.1", 0), project)
    thread = threading.Thread(target=editor.serve_forever)
    thread.start()
    base = f"http://127.0.0.1:{editor.server_address[1]}"
    try:
        with urlopen(base + "/tile-editor/") as response:
            assert b"GEL Level Editor" in response.read()
        with urlopen(base + "/api/tile-editor/images/terrain.png") as response:
            image = Image.open(io.BytesIO(response.read()))
            assert image.mode == "P"
            assert image.info["transparency"] == 0
        with urlopen(base + "/api/tile-editor/maps/level.tmj") as response:
            loaded = json.load(response)
        payload = {"name": "level.tmj", "project_path": loaded["project_path"], "hash": loaded["hash"], "tiles": [
            {"layer": loaded["editable_layers"][0], "index": 0, "gid": 2}], "objects": []}
        request = Request(base + "/api/tile-editor/maps", data=json.dumps(payload).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request) as response:
            result = json.load(response)
        assert result["map"]["layers"][0]["data"][0] == 2
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 400
    finally:
        editor.shutdown()
        editor.server_close()
        thread.join()