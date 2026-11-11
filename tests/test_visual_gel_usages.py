import importlib.util
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


project = load("usage_project", "tools/visual-gel/project.py")
usages = load("visual_usages", "tools/visual-gel/usages.py")


SOURCE = (
    "#define LIMIT 3\r\n"
    "#define ALIAS Entities[0].health\r\n"
    "struct_table Entities[4] { u8 health; }\r\n"
    "struct_table Others[4] { u8 health; }\r\n"
    "u8 score;\r\n"
    "fn one(u8 score) {\r\n"
    "  // score LIMIT Entities[0].health\r\n"
    '  print("score LIMIT Entities[0].health");\r\n'
    "  Entities[Others[score].health].health = LIMIT;\r\n"
    "  Entities.for_each(item) { score = Entities[item].health; }\r\n"
    "}\r\n"
    "fn two() { score = LIMIT; }\r\n"
)


@pytest.fixture
def fixture_project(tmp_path):
    (tmp_path / "game.gel").write_bytes(SOURCE.encode("utf-8"))
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "extra.gel").write_text("fn extra() { score = LIMIT; }\n", encoding="utf-8")
    return tmp_path


def test_globals_and_defines_find_possible_uses_with_locations(fixture_project):
    out = usages.find(project, fixture_project, {"kind": "global", "symbol": "score"})
    assert [(item["file"], item["line"]) for item in out["results"]] == [
        ("game.gel", 5), ("game.gel", 6), ("game.gel", 9),
        ("game.gel", 10), ("game.gel", 12), ("nested/extra.gel", 1)]
    assert "shadowed" in out["warning"]
    statement = out["results"][2]
    assert statement["function"] == 0 and statement["part"] is not None
    fn = project.functions(SOURCE)[0]
    part = fn["statements"][statement["part"]]
    assert "Others[score]" in fn["source"][part["start"]:part["end"]]
    assert out["results"][-1]["function"] is None
    defines = usages.find(project, fixture_project, {"kind": "define", "symbol": "LIMIT"})
    assert [item["line"] for item in defines["results"] if item["file"] == "game.gel"] == [1, 9, 12]


def test_fields_match_only_qualified_table_and_nested_brackets(fixture_project):
    out = usages.find(project, fixture_project, {"kind": "field", "table": "Entities", "symbol": "health"})
    assert [item["line"] for item in out["results"]] == [2, 9, 10]
    assert "alias" in out["warning"]
    other = usages.find(project, fixture_project, {"kind": "field", "table": "Others", "symbol": "health"})
    assert [item["line"] for item in other["results"]] == [9]


def test_local_scope_iterator_and_stale_hash(fixture_project):
    doc = project.read_document(fixture_project, "game.gel")
    assert "item" in doc["functions"][0]["locals"]
    query = {"kind": "local", "symbol": "score", "file": "game.gel",
             "hash": doc["hash"], "function": 0}
    out = usages.find(project, fixture_project, query)
    assert [item["line"] for item in out["results"]] == [6, 9, 10]
    iterator = usages.find(project, fixture_project, {**query, "symbol": "item"})
    assert [item["line"] for item in iterator["results"]] == [10, 10]
    with pytest.raises(ValueError, match="changed"):
        usages.find(project, fixture_project, {**query, "hash": "old"})
    with pytest.raises(ValueError, match="not declared"):
        usages.find(project, fixture_project, {**query, "symbol": "LIMIT"})
    with pytest.raises(ValueError, match="Invalid GEL filename"):
        usages.find(project, fixture_project, {**query, "file": "../game.gel"})


def test_utf8_crlf_column_and_repeated_occurrences(tmp_path):
    source = "// café\r\nfn run() { score = score; }\r\n"
    (tmp_path / "game.gel").write_bytes(source.encode("utf-8"))
    results = usages.find(project, tmp_path, {"kind": "global", "symbol": "score"})["results"]
    assert [(item["line"], item["column"]) for item in results] == [(2, 12), (2, 20)]
    assert results[0]["hash"] == project.text_hash(source)


def test_usage_endpoint_validates_queries_and_reads_selected_project(fixture_project):
    server = load("usage_server", "tools/editor/server.py")
    editor = server.EditorServer(("127.0.0.1", 0), fixture_project)
    thread = threading.Thread(target=editor.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{editor.server_port}/api/gel-usages"
        def post(data):
            request = Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"})
            with urlopen(request) as response:
                return json.load(response)
        assert post({"kind": "define", "symbol": "LIMIT"})["results"]
        with pytest.raises(HTTPError) as error:
            post({"kind": "field", "table": "../escape", "symbol": "x"})
        assert error.value.code == 400
    finally:
        editor.shutdown()
        editor.server_close()
        thread.join()
