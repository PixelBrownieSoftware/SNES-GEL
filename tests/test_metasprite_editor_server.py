import json
import os
import sys
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools", "metasprite-editor"))
import server


def test_catalog_discovers_projects_and_converts_bmp(tmp_path):
    (tmp_path / "player.metasprite.json").write_text("{}", encoding="utf-8")
    Image.new("RGB", (32, 8), (20, 120, 200)).save(tmp_path / "body.bmp")
    Image.new("RGB", (8, 8), (0, 0, 0)).save(tmp_path / "standalone.png")

    catalog = server.project_catalog(tmp_path)

    assert catalog["projects"] == ["player.metasprite.json"]
    assert catalog["images"] == ["body.bmp.png", "standalone.png"]
    assert json.loads((tmp_path / server.MANIFEST_NAME).read_text(encoding="utf-8")) == catalog["manifest"]
    with Image.open(tmp_path / "body.bmp.png") as converted:
        assert converted.mode == "P"
        assert converted.info["transparency"] == 0
        assert min(converted.tobytes()) >= 1
        assert max(converted.tobytes()) <= 15


def test_server_saves_projects_assets_and_updates_manifest(tmp_path):
    editor = server.EditorServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=editor.serve_forever)
    thread.start()
    base = f"http://127.0.0.1:{editor.server_address[1]}"
    try:
        with urlopen(f"{base}/api/catalog") as response:
            assert response.status == 200
            assert json.loads(response.read())["projects"] == []

        png_path = tmp_path / "layer.png"
        Image.new("RGB", (8, 8), (255, 0, 0)).save(png_path)
        request = Request(
            f"{base}/api/assets/layer.png",
            data=png_path.read_bytes(),
            headers={"Content-Type": "image/png"},
            method="POST",
        )
        with urlopen(request) as response:
            assert response.status == 201

        project = {"format": "gel-metasprite-v2", "name": "hero"}
        request = Request(
            f"{base}/api/projects/hero.metasprite.json",
            data=json.dumps(project).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            catalog = json.loads(response.read())
        assert catalog["projects"] == ["hero.metasprite.json"]
        assert catalog["images"] == ["layer.png"]
        assert json.loads((tmp_path / server.MANIFEST_NAME).read_text(encoding="utf-8"))["projects"] == [
            "hero.metasprite.json"
        ]

        try:
            urlopen(f"{base}/api/assets/%2e%2e%2fsecret.png")
        except HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError("Path traversal request was unexpectedly accepted.")
    finally:
        editor.shutdown()
        editor.server_close()
        thread.join()
