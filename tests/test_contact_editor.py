import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gel_codegen import compile_gel


ROOT = Path(__file__).resolve().parents[1]


def test_generated_contact_handlers_compile():
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the browser editor model")
    data = {
        "version": 1,
        "interactions": [
            {"self": "OBJ_BULLET", "other": "OBJ_PLAYER", "enter": "hit",
             "stay": "", "exit": "leave"},
            {"self": "OBJ_PLAYER", "other": "OBJ_BULLET", "enter": "",
             "stay": "touch", "exit": ""},
        ],
    }
    script = "const m=require('./tools/contact-editor/model'); console.log(m.generate(JSON.parse(process.argv[1])));"
    generated = subprocess.run(
        ["node", "-e", script, json.dumps(data)], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout
    src = """
        enum ObjectType { OBJ_BULLET = 1, OBJ_PLAYER = 2 }
        struct_table Entities[4] { u8 type; }
        fn hit(u8 self, u8 other) { }
        fn leave(u8 self, u8 other) { }
        fn touch(u8 self, u8 other) { }
        fn init_game() { entity_on_contact(0, 1); entity_on_stay(0, 1); entity_on_exit(0, 1); }
    """ + generated
    asm, _ = compile_gel(src, {}, hot_reload=False)
    for handler in ("hit", "leave", "touch"):
        assert f"JSR {handler}" in asm or f"; inline {handler}" in asm


def test_visual_handler_scripts_compile():
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the browser editor model")
    data = {
        "version": 1,
        "interactions": [
            {"self": "OBJ_BULLET", "other": "OBJ_PLAYER", "enter": "hit",
             "stay": "", "exit": ""},
        ],
        "handlers": {"hit": [
            {"op": "set", "target": "Entities[other].vx", "value": "1.5"},
            {"op": "add", "target": "Entities[other].vx", "value": "FRICTION"},
            {"op": "if", "cond": "Entities[other].vx > 0.5",
             "then": [{"op": "call", "fn": "slow", "args": "0.2"}], "else": []},
        ]},
    }
    script = "const m=require('./tools/contact-editor/model'); console.log(m.generate(JSON.parse(process.argv[1])));"
    generated = subprocess.run(
        ["node", "-e", script, json.dumps(data)], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout
    src = """
        #define FRICTION 0.1
        enum ObjectType { OBJ_BULLET = 1, OBJ_PLAYER = 2 }
        struct_table Entities[4] { u8 type; f16 vx; }
        fn slow(f16 amount) { }
        fn init_game() { entity_on_contact(0, 1); entity_on_stay(0, 1); entity_on_exit(0, 1); }
    """ + generated
    asm, _ = compile_gel(src, {}, hot_reload=False)
    assert "hit" in asm


@pytest.mark.parametrize("bad", [
    {"op": "set", "target": "x; y", "value": "1"},
    {"op": "set", "target": "x", "value": "1; asm {}"},
    {"op": "call", "fn": "if", "args": ""},
    {"op": "if", "cond": "(1", "then": [], "else": []},
])
def test_visual_handler_scripts_reject_unsafe_code(bad):
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the browser editor model")
    data = {"version": 1, "interactions": [], "handlers": {"hit": [bad]}}
    script = "const m=require('./tools/contact-editor/model'); m.validateHandlers(JSON.parse(process.argv[1]));"
    result = subprocess.run(["node", "-e", script, json.dumps(data)], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode != 0


def test_symbol_scan_and_server_roundtrip(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT / "tools" / "contact-editor"))
    import server

    (tmp_path / "game.gel").write_text(
        "#define SPEED 2\nenum Kind { A, B = 2 }\n"
        "struct_table Ents[4] { u8 type; f16 vx; }\n"
        "u8 counter;\nfn go(u8 e) { u8 local = 1; }\n", encoding="utf-8")
    symbols = server.build_symbols(tmp_path)
    assert symbols["tables"]["Ents"] == {"type": "u8", "vx": "f16"}
    assert symbols["enums"]["Kind"] == ["A", "B"]
    assert "SPEED" in symbols["defines"]
    assert symbols["globals"]["counter"] == "u8"
    assert "local" not in symbols["globals"]
    assert symbols["functions"]["go"]["params"] == ["u8 e"]
    assert "peek8" in symbols["hardware"]


def test_create_and_free_blocks_compile():
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the browser editor model")
    data = {
        "version": 1,
        "interactions": [{"self": "OBJ_BULLET", "other": "OBJ_PLAYER", "enter": "hit",
                          "stay": "", "exit": ""}],
        "handlers": {"hit": [
            {"op": "create", "table": "Entities", "var": "spawned"},
            {"op": "create", "table": "Entities", "var": ""},
            {"op": "free", "target": "Entities[self]"},
        ]},
    }
    script = "const m=require('./tools/contact-editor/model'); console.log(m.generate(JSON.parse(process.argv[1])));"
    generated = subprocess.run(
        ["node", "-e", script, json.dumps(data)], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout
    assert "u8 spawned = Entities.create();" in generated
    assert "Entities[self].free();" in generated
    src = """
        enum ObjectType { OBJ_BULLET = 1, OBJ_PLAYER = 2 }
        struct_table Entities[4] { u8 type; }
        fn init_game() { entity_on_contact(0, 1); entity_on_stay(0, 1); entity_on_exit(0, 1); }
    """ + generated
    compile_gel(src, {}, hot_reload=False)


@pytest.mark.parametrize("kind,value,ok", [
    ("u8", "255", True), ("u8", "346", False), ("u8", "2.6", False), ("u8", "-1", False),
    ("u8", "0xFF", True), ("u8", "0x100", False), ("i8", "-128", True), ("i8", "128", False),
    ("u16", "65535", True), ("u16", "65536", False), ("i16", "-32769", False),
    ("f16", "2.6", True), ("f16", "128", False), ("f16", "-128", True), ("u8", "x + 1", True),
    ("u8", "BIG", False), ("u8", "SMALL", True),
])
def test_value_range_checks(kind, value, ok):
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the browser editor model")
    script = ("const m=require('./tools/contact-editor/model');"
              "console.log(JSON.stringify(m.checkValue(process.argv[1], process.argv[2], {BIG:'300', SMALL:'7'})));")
    out = subprocess.run(["node", "-e", script, kind, value], cwd=ROOT, check=True,
                         capture_output=True, text=True).stdout.strip()
    assert (json.loads(out) == "") is ok


def test_bracketed_expressions_compile():
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the browser editor model")
    data = {
        "version": 1,
        "interactions": [{"self": "OBJ_BULLET", "other": "OBJ_PLAYER", "enter": "hit",
                          "stay": "", "exit": ""}],
        "handlers": {"hit": [
            {"op": "add", "target": "Entities[other].x", "value": "(Entities[self].x + 2) * 3"},
        ]},
    }
    script = "const m=require('./tools/contact-editor/model'); console.log(m.generate(JSON.parse(process.argv[1])));"
    generated = subprocess.run(
        ["node", "-e", script, json.dumps(data)], cwd=ROOT, check=True,
        capture_output=True, text=True,
    ).stdout
    assert "Entities[other].x += (Entities[self].x + 2) * 3;" in generated
    src = """
        enum ObjectType { OBJ_BULLET = 1, OBJ_PLAYER = 2 }
        struct_table Entities[4] { u8 type; u8 x; }
        fn init_game() { entity_on_contact(0, 1); entity_on_stay(0, 1); entity_on_exit(0, 1); }
    """ + generated
    compile_gel(src, {}, hot_reload=False)


def _node(script, *args):
    return subprocess.run(["node", "-e", script, *args], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.strip()


def test_expression_text_becomes_real_structure():
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the browser editor model")
    out = _node("const m=require('./tools/contact-editor/model');"
                "console.log(JSON.stringify(m.parseExpr(process.argv[1])));",
                "(Entities[self].x + 2) * -speed")
    tree = json.loads(out)
    assert [item["t"] for item in tree] == ["group", "op", "val"]
    assert [item["t"] for item in tree[0]["items"]] == ["val", "op", "val"]
    assert tree[0]["items"][0]["text"] == "Entities[self].x"
    assert tree[2]["text"] == "-speed"


def test_structured_expressions_generate_and_reject_bad_shapes():
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the browser editor model")
    script = ("const m=require('./tools/contact-editor/model');"
              "const g=JSON.parse(process.argv[1]);"
              "try{console.log(m.generate(g));}catch(e){console.log('ERR '+e.message);}")
    val = lambda t: {"t": "val", "text": t}
    op = lambda o: {"t": "op", "op": o}

    def run(expr):
        data = {"version": 1, "interactions": [{"self": "A", "other": "B", "enter": "hit", "stay": "", "exit": ""}],
                "handlers": {"hit": [{"op": "set", "target": "x", "value": expr}]}}
        return _node(script, json.dumps(data))

    good = run([{"t": "group", "items": [val("a"), op("+"), val("b")]}, op("*"), val("2")])
    assert "x = (a + b) * 2;" in good
    assert run([val("a"), op("+")]).startswith("ERR")
    assert run([val("a"), val("b")]).startswith("ERR")
    assert run([val("a"), op("; asm"), val("b")]).startswith("ERR")
    assert run([{"t": "group", "items": []}]).startswith("ERR")


def test_selected_range_is_wrapped_in_brackets():
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the browser editor model")
    script = ("const m=require('./tools/contact-editor/model');"
              "const e=m.parseExpr('2 + 5 + 5 + 8 + 3'); m.wrapRange(e,2,6);"
              "console.log(JSON.stringify(e)+'|'+m.exprText(e));")
    tree, text = _node(script).split("|")
    items = json.loads(tree)
    assert [i["t"] for i in items] == ["val", "op", "group", "op", "val"]
    assert text == "2 + (5 + 5 + 8) + 3"
    bad = subprocess.run(["node", "-e", "const m=require('./tools/contact-editor/model');m.wrapRange(m.parseExpr('1 + 2'),1,2)"],
                         cwd=ROOT, capture_output=True, text=True)
    assert bad.returncode != 0


def test_symbol_scan_includes_generated_object_types(tmp_path):
    import sys
    sys.path.insert(0, str(ROOT / "tools" / "contact-editor"))
    import server

    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "assets.gel").write_text(
        "#define OBJ_GEM 2\n#define OBJ_BOUNCER 3\n", encoding="utf-8")
    symbols = server.build_symbols(tmp_path)
    assert {"OBJ_GEM", "OBJ_BOUNCER"} <= set(symbols["defines"])
    assert symbols["defineValues"]["OBJ_GEM"] == "2"
