import json
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

from gel_codegen import compile_gel


ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("contact_editor_server", ROOT / "tools" / "contact-editor" / "server.py")
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


def test_game_connects_contact_handlers_without_overlapping_mode_flags():
    game = (ROOT / "game" / "game.gel").read_text(encoding="utf-8")
    entities = (ROOT / "game" / "entities.gel").read_text(encoding="utf-8")
    assert '#include "contact.gel"' in game
    assert "update_entity_contacts();" in game
    for handler in ("entity_on_contact", "entity_on_stay", "entity_on_exit"):
        assert f"{handler}(e1, e2);" in entities
        assert f"{handler}(e2, e1);" in entities
    assert "bit_set(Entities[slot].flags, ENTITY_FLAG_CONTACTABLE)" not in entities
    assert "bit_test(Entities[e1].capabilities, ENTITY_FLAG_CONTACTABLE)" in entities
    assert "bit_test(Entities[e2].capabilities, ENTITY_FLAG_CONTACTABLE)" in entities
    assert "!entity_is_active(e1) || !entity_is_active(e2)" in entities


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


def test_logical_not_group_from_visual_editor_compiles():
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the browser editor model")
    data = {"version": 1, "interactions": [
        {"self": "OBJ_A", "other": "OBJ_B", "enter": "hit", "stay": "", "exit": ""}
    ], "handlers": {"hit": [{
        "op": "if", "cond": [{"t": "group", "not": True, "items": [
            {"t": "ref", "name": "self", "category": "Locals"},
            {"t": "op", "op": "=="},
            {"t": "ref", "name": "other", "category": "Locals"}
        ]}], "then": [{"op": "return", "value": ""}], "else": []
    }]}}
    script = "const m=require('./tools/contact-editor/model'); console.log(m.generate(JSON.parse(process.argv[1])));"
    generated = subprocess.run(["node", "-e", script, json.dumps(data)], cwd=ROOT, check=True,
                               capture_output=True, text=True).stdout
    assert "if (!(self == other))" in generated
    compile_gel("enum Kind { OBJ_A = 1, OBJ_B = 2 }\n"
                "struct_table Entities[4] { u8 type; }\n"
                "fn init_game() { entity_on_contact(0, 1); }\n" + generated, {}, hot_reload=False)


def test_control_flow_blocks_generate_parseable_and_compilable_contact_code():
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the browser editor model")
    data = {
        "version": 1,
        "interactions": [{"self": "OBJ_A", "other": "OBJ_B", "enter": "hit",
                          "stay": "", "exit": ""}],
        "handlers": {"hit": [
            {"op": "declare", "type": "u8", "name": "count", "value": "0"},
            {"op": "while", "cond": "count < 2", "body": [
                {"op": "expr", "value": "count++"}]},
            {"op": "for_each", "table": "Entities", "iterator": "item", "body": [
                {"op": "if", "cond": "item == self", "then": [
                    {"op": "assign", "target": "count", "operator": "|=", "value": "1"}],
                 "else": []}]},
            {"op": "switch", "cond": "count", "cases": [
                {"value": "1", "body": [{"op": "return", "value": ""}]}],
             "default": [{"op": "return", "value": ""}]},
        ]},
    }
    script = "const m=require('./tools/contact-editor/model'); console.log(m.generate(JSON.parse(process.argv[1])));"
    generated = subprocess.run(["node", "-e", script, json.dumps(data)], cwd=ROOT, check=True,
                               capture_output=True, text=True).stdout
    src = ("enum Kind { OBJ_A = 1, OBJ_B = 2 }\n"
           "struct_table Entities[4] { u8 type; }\n"
           "fn init_game() { entity_on_contact(0, 1); }\n" + generated)
    server.check_gel_syntax(src)
    asm, _ = compile_gel(src, {}, hot_reload=False)
    assert "hit" in asm


def test_increment_and_bit_set_blocks_compile_for_eight_and_sixteen_bit_targets():
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the browser editor model")
    data = {"version": 1,
            "interactions": [{"self": "OBJ_A", "other": "OBJ_B", "enter": "hit",
                              "stay": "", "exit": ""}],
            "handlers": {"hit": [
                {"op": "increment", "target": "Entities[self].flags", "prefix": False},
                {"op": "decrement", "target": "counter", "prefix": True},
                {"op": "call", "fn": "bit_set", "args": "Entities[self].flags, 3"},
                {"op": "call", "fn": "bit_set", "args": "counter, 14"}]}}
    script = "const m=require('./tools/contact-editor/model'); console.log(m.generate(JSON.parse(process.argv[1])));"
    generated = subprocess.run(["node", "-e", script, json.dumps(data)], cwd=ROOT, check=True,
                               capture_output=True, text=True).stdout
    src = ("enum Kind { OBJ_A = 1, OBJ_B = 2 }\n"
           "struct_table Entities[4] { u8 type; u8 flags; }\n"
           "u16 counter;\nfn init_game() { entity_on_contact(0, 1); }\n" + generated)
    server.check_gel_syntax(src)
    asm, _ = compile_gel(src, {}, hot_reload=False)
    assert "#$4000" in asm


def test_bit_clear_test_and_peek_calls_in_shared_expressions_compile():
    if not shutil.which("node"):
        pytest.skip("Node.js is required to exercise the browser editor model")
    data = {"version": 1,
            "interactions": [{"self": "OBJ_A", "other": "OBJ_B", "enter": "hit",
                              "stay": "", "exit": ""}],
            "handlers": {"hit": [
                {"op": "if", "cond": "!bit_test(Entities[self].flags, 3)",
                 "then": [{"op": "call", "fn": "bit_clear", "args": "Entities[self].flags, 3"}],
                 "else": []},
                {"op": "set", "target": "counter", "value": "peek8(counter)"},
                {"op": "declare", "type": "u8", "name": "local", "value": "peek8l(1, counter)"},
            ]}}
    script = "const m=require('./tools/contact-editor/model'); console.log(m.generate(JSON.parse(process.argv[1])));"
    generated = subprocess.run(["node", "-e", script, json.dumps(data)], cwd=ROOT, check=True,
                               capture_output=True, text=True).stdout
    src = ("enum Kind { OBJ_A = 1, OBJ_B = 2 }\n"
           "struct_table Entities[4] { u8 type; u8 flags; }\n"
           "u16 counter;\nfn init_game() { entity_on_contact(0, 1); }\n" + generated)
    server.check_gel_syntax(src)
    asm, _ = compile_gel(src, {}, hot_reload=False)
    assert "AND.w #$0008" in asm
    assert "LDA.l $010000,X" in asm


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
    (tmp_path / "game.gel").write_text(
        "#define SPEED 2\nenum Kind { A, B = 2 }\n"
        "struct_table Ents[4] { u8 type; f16 vx; }\n"
        "u8 counter;\nfn go(u8 e) { u8 local = 1; }\n"
        "fn health(u8 e) -> i8 { return e; }\n", encoding="utf-8")
    symbols = server.build_symbols(tmp_path)
    assert symbols["tables"]["Ents"] == {"type": "u8", "vx": "f16"}
    assert symbols["enums"]["Kind"] == ["A", "B"]
    assert symbols["enumValues"]["A"] == 0
    assert symbols["enumValues"]["B"] == 2
    assert "SPEED" in symbols["defines"]
    assert symbols["globals"]["counter"] == "u8"
    assert "local" not in symbols["globals"]
    assert symbols["functions"]["go"]["params"] == ["u8 e"]
    assert symbols["functions"]["go"]["returnType"] is None
    assert symbols["functions"]["health"]["returnType"] == "i8"
    assert symbols["functions"]["health"]["params"] == ["u8 e"]
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
    (tmp_path / "build").mkdir()
    (tmp_path / "build" / "assets.gel").write_text(
        "#define OBJ_GEM 2\n#define OBJ_BOUNCER 3\n", encoding="utf-8")
    symbols = server.build_symbols(tmp_path)
    assert {"OBJ_GEM", "OBJ_BOUNCER"} <= set(symbols["defines"])
    assert symbols["defineValues"]["OBJ_GEM"] == "2"
