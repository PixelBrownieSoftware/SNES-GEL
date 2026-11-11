import importlib.util
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


server = load("removal_server", "tools/editor/server.py")
project, declarations, usages, removal = (server.visual_project, server.visual_declarations,
                                         server.visual_usages, server.visual_removal)


def preview(tmp_path, source, kind, symbol, **extra):
    (tmp_path / "game.gel").write_bytes(source.encode())
    return removal.prepare(project, declarations, usages, tmp_path, {
        "kind": kind, "symbol": symbol, "name": "game.gel",
        "hash": project.text_hash(source), "index": 0, **extra})


def test_global_reads_and_writes_are_previewed_without_touching_comments(tmp_path):
    source = "u8 score;\nfn main() { score = side(); u8 x = score; score++; /* score */ }\n"
    result = preview(tmp_path, source, "global", "score")
    assert result["changes"]["game.gel"] == "\nfn main() {  u8 x = 0;  /* score */ }\n"
    assert any("-fn main()" in line for line in result["diffs"]["game.gel"])
    assert (tmp_path / "game.gel").read_bytes() == source.encode()
    project.parser().parse(project.mask_comments(result["changes"]["game.gel"]))


def test_define_reads_and_qualified_field_writes(tmp_path):
    define = "#define GRAVITY 2\nu8 initial = GRAVITY;\nfn main() { u8 x = GRAVITY; }\n"
    output = preview(tmp_path, define, "define", "GRAVITY")["changes"]["game.gel"]
    assert "u8 x = 0;" in output and "u8 initial = 0;" in output
    field = ("struct_table Entities[4] { u8 x; u8 y; }\n"
             "struct_table Other[4] { u8 x; }\n"
             "fn main() { Entities[0].x = side(); u8 v = Entities[1].x; Other[0].x = 2; }\n")
    result = preview(tmp_path, field, "field", "x", table="Entities")["changes"]["game.gel"]
    assert "struct_table Entities[4] {  u8 y; }" in result
    assert "side()" not in result and "u8 v = 0;" in result
    assert "Other[0].x = 2;" in result
    project.parser().parse(result)


def test_local_removal_and_unsafe_cases_are_rejected(tmp_path):
    source = "fn one(u8 arg) { u8 value = 2; value += arg; u8 x = value; }\n"
    result = preview(tmp_path, source, "local", "value", function=0)["changes"]["game.gel"]
    assert "value" not in result and "u8 x = 0;" in result
    with pytest.raises(ValueError, match="parameters"):
        preview(tmp_path, source, "local", "arg", function=0)
    with pytest.raises(ValueError, match="embedded"):
        preview(tmp_path, "u8 score; fn main() { u8 x = score++; }", "global", "score")
    with pytest.raises(ValueError, match="shadows"):
        preview(tmp_path, "u8 score; fn main(u8 score) { score = 1; }", "global", "score")


def test_field_alias_and_nested_files_are_not_silently_modified(tmp_path):
    source = "struct_table Entities[2] { u8 x; u8 y; }\nfn main() { Alias[0].x = 1; }"
    with pytest.raises(ValueError, match="ambiguous"):
        preview(tmp_path, source, "field", "x", table="Entities")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "other.gel").write_text("fn x() { score = 1; }")
    with pytest.raises(ValueError, match="nested"):
        preview(tmp_path, "u8 score;\n", "global", "score")


def test_endpoint_preview_apply_and_stale_preview(tmp_path):
    source = "u8 score;\nfn main() { score = 1; u8 x = score; }\n"
    path = tmp_path / "game.gel"
    path.write_bytes(source.encode())
    editor = server.EditorServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=editor.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{editor.server_port}/api/gel-variable-removal"
    def post(data):
        req = Request(url, json.dumps(data).encode(), {"Content-Type": "application/json"}, method="POST")
        with urlopen(req) as response:
            return json.load(response)
    try:
        payload = {"kind": "global", "symbol": "score", "name": "game.gel",
                   "hash": project.text_hash(source), "index": 0}
        result = post(payload)
        assert path.read_bytes() == source.encode()
        (tmp_path / "other.gel").write_text("fn other() {}\n")
        with pytest.raises(HTTPError) as error:
            post({**payload, "apply": True, "token": result["token"]})
        assert error.value.code == 400
        result = post(payload)
        post({**payload, "apply": True, "token": result["token"]})
        assert "u8 x = 0;" in path.read_text()
    finally:
        editor.shutdown()
        editor.server_close()
        thread.join()
