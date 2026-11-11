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


project = load("visual_project_declarations_test", "tools/visual-gel/project.py")
declarations = load("visual_declarations_test", "tools/visual-gel/declarations.py")
server = load("editor_declarations_test", "tools/editor/server.py")


def empty_symbols():
    return {key: {} for key in ("functions", "globals", "tables", "enums", "defines", "hardware", "bit")}


def test_discovers_only_declarations_and_preserves_comments_and_crlf(tmp_path):
    source = ('// #define HIDDEN 1\r\n#define SPEED 2 // keep\r\n'
              'persist u16 score = 0; // global comment\r\n'
              'struct_table Entities[10] {\r\n  u8 x; // field comment\r\n}\r\n'
              'fn main() { u8 local = 1; asm { NOP } }\r\n')
    (tmp_path / "game.gel").write_bytes(source.encode())
    entries = declarations.declarations(project, source)
    assert [(e["kind"], e["name"]) for e in entries] == [
        ("define", "SPEED"), ("global", "score"), ("table", "Entities")]
    assert entries[0]["code"] == "#define SPEED 2 // keep"
    assert entries[2]["code"].endswith("}")
    payload = {"name": "game.gel", "hash": project.text_hash(source), "kind": "table", "index": 2,
               "code": entries[2]["code"].replace("u8 x", "u16 x")}
    _, updated = declarations.apply(project, tmp_path, payload, empty_symbols())
    assert updated == source.replace("u8 x", "u16 x")


@pytest.mark.parametrize("kind,code", [
    ("define", "#define LIMIT 8"),
    ("global", "mmio u8 REG @ 0x2100;"),
    ("table", "struct_table Items[SIZE] @ ADDRESS interleaved {\n u8 type;\n u16 x;\n}"),
])
def test_add_declaration(kind, code, tmp_path):
    source = "fn main() {}\r\n"
    (tmp_path / "game.gel").write_bytes(source.encode())
    _, updated = declarations.apply(project, tmp_path, {
        "name": "game.gel", "hash": project.text_hash(source), "kind": kind, "index": None,
        "code": code}, empty_symbols())
    assert updated == source + "\r\n" + code.replace("\n", "\r\n") + "\r\n"
    assert declarations.declarations(project, updated)[0]["kind"] == kind


@pytest.mark.parametrize("kind,code", [
    ("define", "#define LIMIT"),
    ("define", "#define LIMIT 1\n#define OTHER 2"),
    ("define", "#define return 1"),
    ("global", "u8 x; u8 y;"),
    ("table", "struct_table T[2] { u8 x; } fn injected() {}"),
    ("table", "struct_table T[2] { }"),
    ("table", "struct_table T[2] { u8 x; u16 x; }"),
])
def test_rejects_invalid_or_multiple_declarations(kind, code):
    with pytest.raises(ValueError):
        declarations.validate(project, kind, code)


def test_stale_generated_and_duplicate_are_rejected(tmp_path):
    source = "#define LIMIT 1\n"
    (tmp_path / "game.gel").write_bytes(source.encode())
    (tmp_path / "contact.gel").write_bytes(source.encode())
    payload = {"name": "game.gel", "hash": project.text_hash(source), "kind": "define",
               "index": None, "code": "#define LIMIT 2"}
    symbols = empty_symbols()
    symbols["defines"]["LIMIT"] = "1"
    with pytest.raises(ValueError, match="already exists"):
        declarations.apply(project, tmp_path, payload, symbols)
    with pytest.raises(ValueError, match="changed"):
        declarations.apply(project, tmp_path, {**payload, "hash": "old"}, symbols)
    with pytest.raises(ValueError, match="generated"):
        declarations.apply(project, tmp_path, {**payload, "name": "contact.gel"}, symbols)
    with pytest.raises(ValueError, match="already exists"):
        declarations.apply(project, tmp_path, payload, empty_symbols())


def test_edit_define_and_global_preserves_surrounding_source(tmp_path):
    source = "#define SPEED 2 // comment\npersist u16 score = 0; // keep\nfn main() {}\n"
    path = tmp_path / "game.gel"
    path.write_bytes(source.encode())
    base = {"name": "game.gel", "hash": project.text_hash(source)}
    _, updated = declarations.apply(project, tmp_path, {
        **base, "kind": "define", "index": 0, "code": "#define SPEED 4 // comment"}, empty_symbols())
    assert updated == source.replace("SPEED 2", "SPEED 4")
    _, updated = declarations.apply(project, tmp_path, {
        **base, "kind": "global", "index": 1, "code": "persist u16 score = 9;"}, empty_symbols())
    assert updated == source.replace("score = 0", "score = 9")


def test_server_roundtrip_and_conflict(tmp_path):
    source = "#define SPEED 2 // speed\nfn main() {}\n"
    path = tmp_path / "game.gel"
    path.write_text(source, encoding="utf-8")
    editor = server.EditorServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=editor.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{editor.server_port}"
    try:
        with urlopen(base + "/api/gel-declarations/game.gel") as response:
            doc = json.load(response)
        payload = {"name": "game.gel", "hash": doc["hash"], "kind": "define", "index": 0,
                   "code": "#define SPEED 3 // speed"}
        request = Request(base + "/api/gel-declarations", json.dumps(payload).encode(),
                          {"Content-Type": "application/json"}, method="POST")
        with urlopen(request) as response:
            result = json.load(response)
        assert result["declarations"][0]["code"] == payload["code"]
        assert path.read_text(encoding="utf-8") == source.replace("SPEED 2", "SPEED 3")
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 400
    finally:
        editor.shutdown()
        editor.server_close()
        thread.join()


def test_preview_renames_table_and_field_references_without_touching_comments(tmp_path):
    source = "struct_table Items[2] { u8 x; // keep\n}\nfn main() { Items[0].x = 1; }\n"
    other = "#define FIRST Items[0].x // Items[0].x\nfn other() { Items[1].x++; }\n"
    (tmp_path / "game.gel").write_bytes(source.encode())
    (tmp_path / "other.gel").write_bytes(other.encode())
    payload = {"name": "game.gel", "hash": project.text_hash(source), "kind": "table", "index": 0,
               "code": "struct_table Actors[2] { u8 position; // keep\n}",
               "fieldRenames": [{"origin": 0, "name": "position"}]}
    result = declarations.prepare_rename(project, tmp_path, payload, empty_symbols())
    assert "Actors[0].position = 1" in result["changes"]["game.gel"]
    assert result["changes"]["other.gel"] == other.replace(
        "FIRST Items[0].x", "FIRST Actors[0].position").replace("Items[1].x", "Actors[1].position")
    assert result["counts"]["other.gel"] == 4
    assert (tmp_path / "game.gel").read_bytes() == source.encode()


def test_preview_rename_define_and_shadow_rejection(tmp_path):
    source = "#define SPEED 2\nu8 count;\nfn main() { count = SPEED; }\n"
    path = tmp_path / "game.gel"
    path.write_bytes(source.encode())
    payload = {"name": "game.gel", "hash": project.text_hash(source), "kind": "define",
               "index": 0, "code": "#define RATE 2", "fieldRenames": []}
    result = declarations.prepare_rename(project, tmp_path, payload, empty_symbols())
    assert result["changes"]["game.gel"] == source.replace("SPEED", "RATE")
    literal = '#define SPEED 2\nfn main() { print("SPEED"); }\n'
    path.write_bytes(literal.encode())
    with pytest.raises(ValueError, match="in a string"):
        declarations.prepare_rename(project, tmp_path, {
            **payload, "hash": project.text_hash(literal)}, empty_symbols())
    shadow = "u8 count;\nfn main(u8 count) { count = 1; }\n"
    path.write_bytes(shadow.encode())
    with pytest.raises(ValueError, match="shadows"):
        declarations.prepare_rename(project, tmp_path, {
            **payload, "hash": project.text_hash(shadow), "kind": "global",
            "code": "u8 score;"}, empty_symbols())


def test_rename_rejects_ambiguous_fields_and_contact_references(tmp_path):
    source = "struct_table T[2] { u8 x; }\n#define ACCESS ALIAS.x\nfn main() { T[0].x = 1; }\n"
    path = tmp_path / "game.gel"
    path.write_bytes(source.encode())
    payload = {"name": "game.gel", "hash": project.text_hash(source), "kind": "table", "index": 0,
               "code": "struct_table T[2] { u8 y; }", "fieldRenames": [{"origin": 0, "name": "y"}]}
    with pytest.raises(ValueError, match="ambiguous"):
        declarations.prepare_rename(project, tmp_path, payload, empty_symbols())
    (tmp_path / "contacts.json").write_text('{"handler": "T[0].x"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Contact configuration"):
        declarations.prepare_rename(project, tmp_path, payload, empty_symbols())


def test_field_rename_does_not_change_other_table_fields(tmp_path):
    source = "struct_table T[2] { u8 x; }\nfn main() { Other[0].x = T[0].x; }\n"
    (tmp_path / "game.gel").write_bytes(source.encode())
    symbols = empty_symbols()
    symbols["tables"]["Other"] = {"x": "u8"}
    result = declarations.prepare_rename(project, tmp_path, {
        "name": "game.gel", "hash": project.text_hash(source), "kind": "table", "index": 0,
        "code": "struct_table T[2] { u8 y; }", "fieldRenames": [{"origin": 0, "name": "y"}],
    }, symbols)
    assert "Other[0].x = T[0].y;" in result["changes"]["game.gel"]


def test_rename_server_previews_confirms_and_rejects_stale_preview(tmp_path):
    source = "#define SPEED 2\nfn main() { u8 value = SPEED; }\n"
    path = tmp_path / "game.gel"
    path.write_bytes(source.encode())
    other = tmp_path / "other.gel"
    other.write_bytes(b"fn other() { u8 value = SPEED; }\n")
    editor = server.EditorServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=editor.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{editor.server_port}/api/gel-declaration-rename"
    payload = {"name": "game.gel", "hash": project.text_hash(source), "kind": "define",
               "index": 0, "code": "#define RATE 2", "fieldRenames": []}

    def post(data):
        request = Request(url, json.dumps(data).encode(), {"Content-Type": "application/json"}, method="POST")
        with urlopen(request) as response:
            return json.load(response)

    try:
        preview = post(payload)
        assert preview["files"] == ["game.gel", "other.gel"]
        assert path.read_bytes() == source.encode()
        other.write_bytes(b"fn other() { u8 value = SPEED + 1; }\n")
        with pytest.raises(HTTPError) as error:
            post({**payload, "apply": True, "token": preview["token"]})
        assert error.value.code == 400
        assert path.read_bytes() == source.encode()
        preview = post(payload)
        post({**payload, "apply": True, "token": preview["token"]})
        assert b"#define RATE 2" in path.read_bytes()
        assert b"value = RATE + 1" in other.read_bytes()
    finally:
        editor.shutdown()
        editor.server_close()
        thread.join()


def test_type_change_preview_shows_real_edits_and_possible_references(tmp_path):
    source = "u8 score = 0;\nfn main() { score = 4; }\n"
    other = "fn other() { u16 copy = score; }\n"
    (tmp_path / "game.gel").write_bytes(source.encode())
    (tmp_path / "other.gel").write_bytes(other.encode())
    (tmp_path / "contacts.json").write_bytes(b'{"value": "score"}\n')
    payload = {"name": "game.gel", "hash": project.text_hash(source), "kind": "global",
               "index": 0, "code": "u16 score = 0;", "fieldRenames": [], "fieldOrigins": []}
    result = declarations.prepare_impact(project, tmp_path, payload, empty_symbols())
    assert result["type_changes"] == [{"name": "score", "before": "u8", "after": "u16"}]
    assert result["changes"] == {"game.gel": source.replace("u8 score", "u16 score")}
    assert {(item["file"], item["line"]) for item in result["references"]} == {
        ("game.gel", 1), ("game.gel", 2), ("other.gel", 1), ("contacts.json", 1)}
    assert "-u8 score = 0;" in result["diffs"]["game.gel"]
    token = result["token"]
    (tmp_path / "contacts.json").write_bytes(b'{"value": "score + 1"}\n')
    assert declarations.prepare_impact(project, tmp_path, payload, empty_symbols())["token"] != token


def test_table_field_type_change_and_rename_preview(tmp_path):
    source = "struct_table T[2] { u8 x; }\nfn main() { T[0].x = 1; }\n"
    (tmp_path / "game.gel").write_bytes(source.encode())
    payload = {"name": "game.gel", "hash": project.text_hash(source), "kind": "table", "index": 0,
               "code": "struct_table T[2] { u16 y; }", "fieldRenames": [{"origin": 0, "name": "y"}],
               "fieldOrigins": [{"origin": 0, "name": "y"}]}
    result = declarations.prepare_impact(project, tmp_path, payload, empty_symbols())
    assert result["type_changes"] == [{"name": "T.x", "before": "u8", "after": "u16"}]
    assert "T[0].y = 1" in result["changes"]["game.gel"]
    assert any("T[0].x" in item["text"] for item in result["references"])
    payload.update(code="struct_table T[2] { u16 x; }", fieldRenames=[],
                   fieldOrigins=[{"origin": 0, "name": "x"}])
    result = declarations.prepare_impact(project, tmp_path, payload, empty_symbols())
    assert "T[0].x = 1" in result["changes"]["game.gel"]
    assert result["type_changes"][0]["after"] == "u16"


def test_type_change_confirmation_rechecks_contact_configuration(tmp_path):
    source = "u8 score;\nfn main() { score = 1; }\n"
    path = tmp_path / "game.gel"
    path.write_bytes(source.encode())
    config = tmp_path / "contacts.json"
    config.write_bytes(b'{"value": "score"}\n')
    editor = server.EditorServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=editor.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{editor.server_port}/api/gel-declaration-rename"
    payload = {"name": "game.gel", "hash": project.text_hash(source), "kind": "global",
               "index": 0, "code": "u16 score;", "fieldRenames": [], "fieldOrigins": []}

    def post(data):
        request = Request(url, json.dumps(data).encode(), {"Content-Type": "application/json"}, method="POST")
        with urlopen(request) as response:
            return json.load(response)

    try:
        preview = post(payload)
        assert preview["type_changes"][0]["after"] == "u16"
        assert any(item["file"] == "contacts.json" for item in preview["references"])
        config.write_bytes(b'{"value": "score + 1"}\n')
        with pytest.raises(HTTPError) as error:
            post({**payload, "apply": True, "token": preview["token"]})
        assert error.value.code == 400
        assert path.read_bytes() == source.encode()
        preview = post(payload)
        post({**payload, "apply": True, "token": preview["token"]})
        assert path.read_bytes() == source.replace("u8 score", "u16 score").encode()
    finally:
        editor.shutdown()
        editor.server_close()
        thread.join()
