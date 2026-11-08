import importlib.util
import json
import subprocess
import sys
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import object_definitions
from gel_codegen import compile_gel


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gel_editor_server", ROOT / "tools" / "editor" / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


def object_data():
    return {"version": 1, "objects": [
        {"class": "PlayerStart", "flags": {"movable": True, "ghost": False,
                                           "gravity": True, "contactable": True}},
        {"class": "Future", "flags": {"movable": False, "ghost": True,
                                       "gravity": False, "contactable": False}},
    ]}


def test_generated_capabilities_follow_tiled_ids_and_compile():
    data = object_data()
    generated = object_definitions.generate(data, "#define OBJ_PLAYERSTART 7\n")
    assert "if (type == 7) { return 25; }" in generated
    assert "Future" not in generated
    assert "if (type == 3) { return 25; }" in object_definitions.generate(
        data, "#define OBJ_PLAYERSTART 3\n#define OBJ_FUTURE 4\n")
    assert "if (type == 4) { return 2; }" in object_definitions.generate(
        data, "#define OBJ_FUTURE 4\n")
    compile_gel(generated + """
        struct_table Entities[4] { u8 capabilities; }
        fn init_game() { Entities[0].capabilities = object_capabilities(7); }
    """, {}, hot_reload=False)


def variant_data():
    data = object_data()
    data["objects"][0]["editable_fields"] = ["w", "vx"]
    data["objects"][0]["variants"] = [
        {"id": 2, "name": "Large", "fields": {"w": 20, "vx": 1},
         "values": {"heal_amount": 20, "penalty": -2}, "metasprite_id": 3, "palette_id": 1},
        {"id": 4, "name": "Small", "fields": {"w": 8}, "values": {"heal_amount": 0}},
    ]
    return data


def test_variant_rom_records_and_field_overrides_compile():
    data = variant_data()
    object_definitions.validate_fields(data, {"w": "u8", "vx": "f16"})
    header = "#define OBJ_PLAYERSTART 7\n"
    gel = object_definitions.generate(data, header, {"w": "u8", "vx": "f16"})
    asm = object_definitions.generate_asm(data, header)
    assert "#define OBJECT_VALUE_HEAL_AMOUNT 3" in gel
    assert "Entities[slot].w = 20;" in gel
    assert "Entities[slot].vx = 1.0;" in gel
    assert "db 7, 2, 3, 20, 0" in asm
    assert "db 7, 2, 4, 254, 255" in asm
    assert "db 7, 4, 3, 0, 0" in asm
    assert "db 7, 2, 1, 3, 0" in asm
    assert "db 7, 2, 2, 1, 0" in asm
    src = gel + """
        struct_table Entities[4] { u8 w; f16 vx; }
        u16 heal;
        fn init_game() {
            object_apply_variant(0, 7, 2);
            if (object_variant_has(7, 2, OBJECT_VALUE_HEAL_AMOUNT)) {
                heal = object_variant_value(7, 2, OBJECT_VALUE_HEAL_AMOUNT);
            }
        }
    """
    for optimize in (False, True):
        compiled, _ = compile_gel(src, {}, hot_reload=False, optimize=optimize)
        assert "_gel_object_variant_values" in compiled


@pytest.mark.parametrize("mutate", [
    lambda d: d["objects"][0]["variants"].append(d["objects"][0]["variants"][0]),
    lambda d: d["objects"][0]["variants"][0].update(id=256),
    lambda d: d["objects"][0]["variants"][0].update(palette_id=8),
    lambda d: d["objects"][0]["variants"][0]["values"].update(heal_amount=1.5),
    lambda d: d["objects"][0]["variants"][0]["fields"].update(type=1),
    lambda d: d["objects"][0]["editable_fields"].append("param"),
])
def test_invalid_variants_rejected(mutate):
    data = variant_data()
    mutate(data)
    with pytest.raises(ValueError):
        object_definitions.validate(data)


def test_entity_overrides_checked_against_real_field_types():
    data = variant_data()
    with pytest.raises(ValueError, match="unknown"):
        object_definitions.validate_fields(data, {"w": "u8"})
    data["objects"][0]["variants"][0]["fields"]["w"] = 256
    with pytest.raises(ValueError, match="fit u8"):
        object_definitions.validate_fields(data, {"w": "u8", "vx": "f16"})


def test_variant_server_validates_and_persists_entity_fields(tmp_path):
    (tmp_path / "game.gel").write_text("struct_table Entities[4] { u8 w; f16 vx; }\nfn init_game() {}\n", encoding="utf-8")
    editor = server.EditorServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=editor.serve_forever)
    thread.start()
    url = f"http://127.0.0.1:{editor.server_address[1]}/api/objects"
    def post(data):
        req = Request(url, data=json.dumps(data).encode(), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req) as response:
            return json.loads(response.read())
    try:
        data = variant_data()
        assert post(data)["saved"] == "objects.json"
        assert json.loads((tmp_path / "objects.json").read_text(encoding="utf-8")) == data
        data["objects"][0]["variants"][0]["fields"]["w"] = 256
        with pytest.raises(HTTPError) as error:
            post(data)
        assert error.value.code == 400
        assert json.loads((tmp_path / "objects.json").read_text(encoding="utf-8"))["objects"][0]["variants"][0]["fields"]["w"] == 20
    finally:
        editor.shutdown()
        editor.server_close()
        thread.join()


@pytest.mark.parametrize("data", [
    {"version": 1, "objects": [{"class": "../bad", "flags": {}}]},
    {"version": 1, "objects": [{"class": "Thing", "flags": {"movable": True}}]},
    {"version": 1, "objects": [
        {"class": "Thing", "flags": dict.fromkeys(object_definitions.FLAGS, False)},
        {"class": "thing", "flags": dict.fromkeys(object_definitions.FLAGS, False)},
    ]},
])
def test_invalid_definitions_rejected(data):
    with pytest.raises(ValueError):
        object_definitions.validate(data)


def test_shared_server_routes_and_object_roundtrip(tmp_path):
    editor = server.EditorServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=editor.serve_forever)
    thread.start()
    base = f"http://127.0.0.1:{editor.server_address[1]}"

    def get(path):
        with urlopen(base + path) as response:
            return response.read()

    def post(path, data):
        request = Request(base + path, data=json.dumps(data).encode("utf-8"),
                          headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request) as response:
            return json.loads(response.read())

    try:
        for path in ("/", "/object-editor/", "/contact-editor/", "/metasprite-editor/", "/visual-gel/"):
            assert b"<!doctype html>" in get(path).lower()
        assert b"createVisualGelEditor" in get("/visual-gel/editor.js")
        for path in ("/visual-gel/", "/contact-editor/"):
            page = get(path)
            assert b"editor.js?v=define-values-1" in page
            assert b"editor.css?v=" in page
            assert b"model.js?v=define-values-1" in page
        assert b'el("details", "add-bar")' in get("/visual-gel/editor.js?v=logical-not-1")
        assert b".add-toggle" in get("/visual-gel/editor.css?v=logical-not-1")
        (tmp_path / "game.gel").write_text("fn test() { u8 n = 0; n += 1; }\n", encoding="utf-8")
        assert json.loads(get("/api/gel-files"))["files"] == ["game.gel"]
        doc = json.loads(get("/api/gel-files/game.gel"))
        updated = post("/api/gel-files", {"name": "game.gel", "hash": doc["hash"], "function": 0,
                                         "changes": [{"index": 0, "code": "n += 2;"}], "addition": ""})
        assert updated["hash"] != doc["hash"]
        assert "n += 2;" in (tmp_path / "game.gel").read_text(encoding="utf-8")
        with pytest.raises(HTTPError) as error:
            post("/api/gel-files", {"name": "game.gel", "hash": doc["hash"], "function": 0,
                                    "changes": [], "addition": ""})
        assert error.value.code == 400
        assert json.loads(get("/api/objects")) == {"version": 1, "objects": []}
        assert post("/api/objects", object_data())["saved"] == "objects.json"
        assert json.loads(get("/api/objects")) == object_data()
        assert json.loads((tmp_path / "objects.json").read_text(encoding="utf-8")) == object_data()
        with pytest.raises(HTTPError) as error:
            post("/api/objects", {"version": 1, "objects": [{"class": "bad", "flags": {}}]})
        assert error.value.code == 400
        assert json.loads(get("/api/objects")) == object_data()

        assert json.loads(get("/api/contacts"))["interactions"] == []
        assert "tables" in json.loads(get("/api/symbols"))
        assert post("/api/validate-gel", {"gel": "fn good() { u8 x = 1; }"}) == {"valid": True}
        with pytest.raises(HTTPError) as error:
            post("/api/validate-gel", {"gel": "fn bad() { u8 x = 1 +; }"})
        assert error.value.code == 400
        assert post("/api/contacts", {"config": {"version": 1, "interactions": []},
                                       "gel": "fn good() {}\n"})["saved"] == ["contacts.json", "contact.gel"]
        with pytest.raises(HTTPError) as error:
            post("/api/contacts", {"config": {"version": 1, "interactions": []},
                                    "gel": "fn bad() { u8 x = 1 +; }"})
        assert error.value.code == 400
        assert (tmp_path / "contact.gel").read_text(encoding="utf-8") == "fn good() {}\n"
        assert json.loads(get("/api/catalog"))["projects"] == []
        project = {"format": "gel-metasprite-v2", "name": "hero"}
        assert post("/api/projects/hero.metasprite.json", project)["projects"] == ["hero.metasprite.json"]
        assert json.loads(get("/api/projects/hero.metasprite.json")) == project
        with pytest.raises(HTTPError) as error:
            get("/api/assets/%2e%2e%2fsecret.png")
        assert error.value.code == 400
        with pytest.raises(HTTPError) as error:
            get("/editor/server.py")
        assert error.value.code == 404
        request = Request(base + "/api/objects", data=json.dumps(object_data()).encode("utf-8"),
                          headers={"Origin": "http://example.com"}, method="POST")
        with pytest.raises(HTTPError) as error:
            urlopen(request)
        assert error.value.code == 403
    finally:
        editor.shutdown()
        editor.server_close()
        thread.join()


@pytest.mark.parametrize("editor", ["contact-editor", "metasprite-editor"])
def test_legacy_server_entrypoints_launch_shared_server(editor):
    result = subprocess.run([sys.executable, str(ROOT / "tools" / editor / "server.py"), "--help"],
                            check=True, capture_output=True, text=True)
    assert "Local server for the GEL editors" in result.stdout


def test_signature_preview_confirm_and_stale_project(tmp_path):
    source = "fn f(u8 x) { return x; }\n"
    (tmp_path / "game.gel").write_bytes(source.encode())
    caller = tmp_path / "other.gel"
    caller.write_bytes(b"fn g() { f(1); }\n")
    editor = server.EditorServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=editor.serve_forever)
    thread.start()
    base = f"http://127.0.0.1:{editor.server_address[1]}"

    def post(data):
        req = Request(base + "/api/gel-signature", data=json.dumps(data).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req) as response:
            return json.loads(response.read())

    try:
        payload = {"name": "game.gel", "hash": server.visual_project.text_hash(source), "function": 0,
                   "params": [{"origin": 0, "name": "x", "type": "u8"},
                              {"origin": None, "name": "n", "type": "u8", "argument": "0"}]}
        preview = post(payload)
        assert preview["files"] == ["game.gel", "other.gel"]
        assert preview["calls"] == {"other.gel": 1}
        assert preview["changed_functions"] == {"game.gel": ["f"], "other.gel": ["g"]}
        assert preview["call_functions"] == {"other.gel": [{"name": "g", "count": 1}]}
        assert caller.read_bytes() == b"fn g() { f(1); }\n"
        caller.write_bytes(b"fn g() { f(2); }\n")
        with pytest.raises(HTTPError) as error:
            post({**payload, "apply": True, "token": preview["token"]})
        assert error.value.code == 400
        assert (tmp_path / "game.gel").read_bytes() == source.encode()
        preview = post(payload)
        result = post({**payload, "apply": True, "token": preview["token"]})
        assert "u8 n" in result["document"]["functions"][0]["signature"]
        assert caller.read_bytes() == b"fn g() { f(2, 0); }\n"
    finally:
        editor.shutdown()
        editor.server_close()
        thread.join()


def test_signature_write_failure_rolls_back_previous_files(tmp_path, monkeypatch):
    source = "fn f(u8 x) { return x; }\n"
    (tmp_path / "game.gel").write_bytes(source.encode())
    caller = tmp_path / "other.gel"
    caller.write_bytes(b"fn g() { f(1); }\n")
    editor = server.EditorServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=editor.serve_forever)
    thread.start()
    base = f"http://127.0.0.1:{editor.server_address[1]}"
    original_write = server.contacts.atomic_write
    writes = []

    def failing_write(path, data):
        writes.append(path.name)
        if path.name == "other.gel":
            raise OSError("test write failure")
        original_write(path, data)

    def post(data):
        req = Request(base + "/api/gel-signature", data=json.dumps(data).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req) as response:
            return json.loads(response.read())

    try:
        payload = {"name": "game.gel", "hash": server.visual_project.text_hash(source), "function": 0,
                   "params": [{"origin": 0, "name": "x", "type": "u8"},
                              {"origin": None, "name": "n", "type": "u8", "argument": "0"}]}
        preview = post(payload)
        monkeypatch.setattr(server.contacts, "atomic_write", failing_write)
        with pytest.raises(HTTPError) as error:
            post({**payload, "apply": True, "token": preview["token"]})
        assert error.value.code == 500
        assert writes == ["game.gel", "other.gel", "game.gel"]
        assert (tmp_path / "game.gel").read_bytes() == source.encode()
        assert caller.read_bytes() == b"fn g() { f(1); }\n"
    finally:
        editor.shutdown()
        editor.server_close()
        thread.join()


def test_function_mutations_through_server(tmp_path):
    source = "fn first() { return; }\nfn caller() { first(); }\n"
    target = tmp_path / "game.gel"
    target.write_bytes(source.encode())
    editor = server.EditorServer(("127.0.0.1", 0), tmp_path)
    thread = threading.Thread(target=editor.serve_forever)
    thread.start()
    base = f"http://127.0.0.1:{editor.server_address[1]}"

    def post(data):
        req = Request(base + "/api/gel-functions", data=json.dumps(data).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(req) as response:
            return json.loads(response.read())

    try:
        payload = {"name": "game.gel", "hash": server.visual_project.text_hash(source)}
        with pytest.raises(HTTPError) as error:
            post({**payload, "action": "remove", "function": 0})
        assert error.value.code == 400
        assert b"game.gel:2" in error.value.read()
        assert target.read_bytes() == source.encode()
        result = post({**payload, "action": "add", "function_name": "new_func"})
        assert [f["name"] for f in result["functions"]] == ["first", "caller", "new_func"]
        (tmp_path / "contacts.json").write_text(
            '{"interactions":[{"enter":"new_func"}],"handlers":{}}', encoding="utf-8")
        with pytest.raises(HTTPError) as error:
            post({"name": "game.gel", "hash": result["hash"], "action": "remove", "function": 2})
        assert b"contact editor references" in error.value.read()
        (tmp_path / "contacts.json").write_text('{"interactions":[],"handlers":{}}', encoding="utf-8")
        removed = post({"name": "game.gel", "hash": result["hash"], "action": "remove", "function": 2})
        assert [f["name"] for f in removed["functions"]] == ["first", "caller"]
        with pytest.raises(HTTPError) as error:
            post({"name": "game.gel", "hash": result["hash"], "action": "remove", "function": 1})
        assert b"changed since" in error.value.read()
    finally:
        editor.shutdown()
        editor.server_close()
        thread.join()
