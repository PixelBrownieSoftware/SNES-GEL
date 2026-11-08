import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / "visual-gel" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


project = load("visual_signature_project", "project.py")
signature = load("visual_signature", "signature.py")


def request(folder, params):
    doc = project.read_document(folder, "game.gel")
    return {"name": "game.gel", "hash": doc["hash"], "function": 0, "params": params}


def test_return_type_preview_and_parameter_edit(tmp_path):
    (tmp_path / "game.gel").write_text("fn f(u8 x) { return x; }\nfn g() { f(1); }\n", encoding="utf-8")
    (tmp_path / "other.gel").write_text("fn h() { if (f(2)) { return; } }\n", encoding="utf-8")
    payload = request(tmp_path, [{"origin": 0, "type": "u8", "name": "x"}])
    payload["returnType"] = "i8"
    result = signature.prepare(project, tmp_path, payload)
    assert "fn f(u8 x) -> i8 { return x; }" in result["changes"]["game.gel"]
    assert result["return_changed"]
    assert result["calls"] == {"game.gel": 1, "other.gel": 1}
    assert result["changed_functions"] == {"game.gel": ["f"]}
    assert result["call_functions"] == {
        "game.gel": [{"name": "g", "count": 1}],
        "other.gel": [{"name": "h", "count": 1}],
    }
    (tmp_path / "game.gel").write_text(result["changes"]["game.gel"], encoding="utf-8")
    payload = request(tmp_path, [{"origin": 0, "type": "u8", "name": "y"}])
    result = signature.prepare(project, tmp_path, payload)
    assert "fn f(u8 y) -> i8 { return y; }" in result["changes"]["game.gel"]
    payload["returnType"] = None
    result = signature.prepare(project, tmp_path, payload)
    assert "->" not in result["changes"]["game.gel"]


def test_return_type_edit_preserves_parameter_format_and_comments(tmp_path):
    path = tmp_path / "game.gel"
    path.write_text("fn f(u8 x, /* keep ) */  u8 y) { return x; }\n", encoding="utf-8")
    payload = request(tmp_path, [{"origin": 0, "type": "u8", "name": "x"},
                                 {"origin": 1, "type": "u8", "name": "y"}])
    payload["returnType"] = "i8"
    result = signature.prepare(project, tmp_path, payload)
    assert result["changes"]["game.gel"] == project.read_source(path).replace(") {", ") -> i8 {")
    path.write_text("fn f(u8 x, u8 y) -> /* keep */ i8 { return x; }\n", encoding="utf-8")
    payload = request(tmp_path, payload["params"])
    payload["returnType"] = "u8"
    with pytest.raises(ValueError, match="return type contains comments"):
        signature.prepare(project, tmp_path, payload)


def test_preview_changes_calls_and_parameter_uses_without_writing(tmp_path):
    source = "fn heal(u8 amount, u8 kind) { amount += kind; }\nfn local() { heal(1, 2); }\n"
    (tmp_path / "game.gel").write_text(source, encoding="utf-8")
    (tmp_path / "other.gel").write_text("fn other() { heal(3, heal(4, 5)); }\n", encoding="utf-8")
    params = [{"origin": 0, "type": "u16", "name": "value"},
              {"origin": 1, "type": "u8", "name": "kind"},
              {"origin": None, "type": "u8", "name": "extra", "argument": "0"}]
    result = signature.prepare(project, tmp_path, request(tmp_path, params))
    assert "fn heal(u16 value, u8 kind, u8 extra) { value += kind; }" in result["changes"]["game.gel"]
    assert "heal(1, 2, 0)" in result["changes"]["game.gel"]
    assert "heal(3, heal(4, 5, 0), 0)" in result["changes"]["other.gel"]
    assert result["calls"] == {"game.gel": 1, "other.gel": 2}
    assert result["changed_functions"] == {"game.gel": ["heal", "local"], "other.gel": ["other"]}
    assert result["call_functions"] == {
        "game.gel": [{"name": "local", "count": 1}],
        "other.gel": [{"name": "other", "count": 2}],
    }
    assert result["type_changed"]
    assert (tmp_path / "game.gel").read_text(encoding="utf-8") == source


def test_rename_does_not_change_fields_strings_comments_or_other_calls(tmp_path):
    (tmp_path / "game.gel").write_text(
        'fn f(u8 x) { Entities[x].x = x; // x\n return x; }\nfn other() { f(1); }\n', encoding="utf-8")
    params = [{"origin": 0, "type": "u8", "name": "y"}]
    result = signature.prepare(project, tmp_path, request(tmp_path, params))
    expected = project.read_source(tmp_path / "game.gel").replace("u8 x", "u8 y").replace(
        "Entities[x].x = x", "Entities[y].x = y").replace("return x;", "return y;")
    assert result["changes"]["game.gel"] == expected
    assert result["calls"] == {}
    assert result["call_functions"] == {}
    assert result["changed_functions"] == {"game.gel": ["f"]}


def test_preview_lists_recursive_and_distinct_callers(tmp_path):
    (tmp_path / "game.gel").write_text(
        "fn f(u8 x) { if (x) { f(1); } }\n"
        "fn first() { f(f(1)); f(2); }\nfn second() { f(3); }\n", encoding="utf-8")
    params = [{"origin": 0, "type": "u8", "name": "x"},
              {"origin": None, "type": "u8", "name": "extra", "argument": "0"}]
    result = signature.prepare(project, tmp_path, request(tmp_path, params))
    assert result["calls"] == {"game.gel": 5}
    assert result["call_functions"] == {"game.gel": [
        {"name": "f", "count": 1}, {"name": "first", "count": 3}, {"name": "second", "count": 1}]}
    assert result["changed_functions"] == {"game.gel": ["f", "first", "second"]}


def test_unchanged_signature_has_no_affected_functions(tmp_path):
    (tmp_path / "game.gel").write_text("fn f(u8 x) { return x; }\nfn g() { f(1); }\n", encoding="utf-8")
    result = signature.prepare(project, tmp_path, request(tmp_path, [{"origin": 0, "type": "u8", "name": "x"}]))
    assert result["changes"] == {}
    assert result["changed_functions"] == {}
    assert result["call_functions"] == {}


def test_removal_rejects_live_parameter_and_side_effects(tmp_path):
    path = tmp_path / "game.gel"
    path.write_text("fn f(u8 x) { return x; }\nfn g() { f(1); }\n", encoding="utf-8")
    with pytest.raises(ValueError, match="still used"):
        signature.prepare(project, tmp_path, request(tmp_path, []))
    path.write_text("fn f(u8 x) { return; }\nfn g() { f(other()); }\n", encoding="utf-8")
    with pytest.raises(ValueError, match="side effects"):
        signature.prepare(project, tmp_path, request(tmp_path, []))
    path.write_text("fn f(u8 x) { return; }\nfn g() { f(1 + 2); }\n", encoding="utf-8")
    result = signature.prepare(project, tmp_path, request(tmp_path, []))
    assert "fn g() { f(); }" in result["changes"]["game.gel"]


def test_invalid_arguments_and_conflicting_names_are_rejected(tmp_path):
    (tmp_path / "game.gel").write_text("fn f(u8 x) { u8 local = 1; }\n", encoding="utf-8")
    with pytest.raises(ValueError, match="conflicts"):
        signature.prepare(project, tmp_path, request(tmp_path, [{"origin": 0, "type": "u8", "name": "local"}]))
    with pytest.raises(ValueError, match="single GEL expression"):
        signature.prepare(project, tmp_path, request(tmp_path, [
            {"origin": 0, "type": "u8", "name": "x"},
            {"origin": None, "type": "u8", "name": "n", "argument": "1, 2"}]))
    with pytest.raises(ValueError, match="single GEL expression"):
        signature.prepare(project, tmp_path, request(tmp_path, [
            {"origin": 0, "type": "u8", "name": "x"},
            {"origin": None, "type": "u8", "name": "n", "argument": "1); other(); check(2"}]))


def test_preview_token_changes_when_other_files_change(tmp_path):
    (tmp_path / "game.gel").write_text("fn f(u8 x) { return; }\n", encoding="utf-8")
    other = tmp_path / "other.gel"
    other.write_text("fn g() { f(1); }\n", encoding="utf-8")
    payload = request(tmp_path, [])
    before = signature.prepare(project, tmp_path, payload)["token"]
    other.write_text("fn g() { f(2); }\n", encoding="utf-8")
    assert signature.prepare(project, tmp_path, payload)["token"] != before


def test_new_parameter_does_not_shadow_global_read(tmp_path):
    (tmp_path / "game.gel").write_text("fn f(u8 x) { x = health; }\n", encoding="utf-8")
    with pytest.raises(ValueError, match="shadow"):
        signature.prepare(project, tmp_path, request(tmp_path, [
            {"origin": 0, "type": "u8", "name": "health"}]))


def test_removing_register_read_is_blocked(tmp_path):
    (tmp_path / "game.gel").write_text("fn f(u8 x) { return; }\nfn g() { f(REG); }\n", encoding="utf-8")
    with pytest.raises(ValueError, match="side effects"):
        signature.prepare(project, tmp_path, request(tmp_path, []))


def test_type_only_keeps_call_spacing_and_comments(tmp_path):
    (tmp_path / "game.gel").write_text("fn f(u8 x) { return; }\nfn g() { f( /* hi */ 1 ); }\n", encoding="utf-8")
    result = signature.prepare(project, tmp_path, request(tmp_path, [
        {"origin": 0, "type": "u16", "name": "x"}]))
    assert "f( /* hi */ 1 )" in result["changes"]["game.gel"]
    assert result["calls"] == {"game.gel": 1}


def test_contact_handlers_and_macros_are_not_silently_modified(tmp_path):
    (tmp_path / "game.gel").write_text("fn f(u8 x) { return; }\n", encoding="utf-8")
    (tmp_path / "contacts.json").write_text('{"interactions":[{"enter":"f"}]}', encoding="utf-8")
    with pytest.raises(ValueError, match="contact editor"):
        signature.prepare(project, tmp_path, request(tmp_path, []))
    (tmp_path / "contacts.json").write_text('{"interactions":[]}', encoding="utf-8")
    (tmp_path / "other.gel").write_text("#define CALL_F f\n", encoding="utf-8")
    with pytest.raises(ValueError, match="macro"):
        signature.prepare(project, tmp_path, request(tmp_path, []))


def test_contact_handler_blocks_calls_and_reordering(tmp_path):
    (tmp_path / "game.gel").write_text("fn f(u8 x, u8 y) { return; }\n", encoding="utf-8")
    (tmp_path / "contacts.json").write_text(
        '{"interactions":[],"handlers":{"hit":[{"op":"call","fn":"f"}]}}', encoding="utf-8")
    with pytest.raises(ValueError, match="contact editor calls"):
        signature.prepare(project, tmp_path, request(tmp_path, [
            {"origin": 0, "name": "x", "type": "u16"}, {"origin": 1, "name": "y", "type": "u8"}]))
    with pytest.raises(ValueError, match="Reordering"):
        signature.prepare(project, tmp_path, request(tmp_path, [
            {"origin": 1, "name": "y", "type": "u8"}, {"origin": 0, "name": "x", "type": "u8"}]))


def test_new_argument_cannot_insert_old_signature_call(tmp_path):
    (tmp_path / "game.gel").write_text("fn f(u8 x) { return; }\nfn g() { f(1); }\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cannot call"):
        signature.prepare(project, tmp_path, request(tmp_path, [
            {"origin": 0, "name": "x", "type": "u8"},
            {"origin": None, "name": "y", "type": "u8", "argument": "f(1)"}]))
    result = signature.prepare(project, tmp_path, request(tmp_path, [
        {"origin": 0, "name": "x", "type": "u8"},
        {"origin": None, "name": "y", "type": "u8", "argument": "other(1, 2)"}]))
    assert "f(1, other(1, 2))" in result["changes"]["game.gel"]
