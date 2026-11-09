import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("visual_project", ROOT / "tools" / "visual-gel" / "project.py")
project = importlib.util.module_from_spec(spec)
spec.loader.exec_module(project)


def test_real_game_functions_are_discovered():
    doc = project.read_document(ROOT / "game", "game.gel")
    assert any(fn["name"] == "init_game" for fn in doc["functions"])
    assert sum(len(fn["statements"]) for fn in doc["functions"]) > 50
    assert not [fn["error"] for fn in doc["functions"] if fn["error"]]
    def indexes(nodes):
        for node in nodes:
            if node["kind"] == "part":
                yield node["index"]
            for branch in node.get("branches", []):
                yield from indexes(branch["items"])
                yield from indexes(branch["body"])
    for fn in doc["functions"]:
        assert sorted(indexes(fn["display"])) == list(range(len(fn["statements"])))


def test_patches_only_selected_statements_preserving_unsupported_code_and_comments(tmp_path):
    source = ('#define LIMIT 3\r\n// header\r\n'
              'fn sample(u8 e) {\r\n'
              '    // keep this\r\n'
              '    while (e < LIMIT) {\r\n'
              '        e += 1; // keep inline comment\r\n'
              '    }\r\n'
              '    asm { NOP }\r\n'
              '}\r\n'
              'fn other() { u8 untouched = 1; }\r\n')
    (tmp_path / "game.gel").write_bytes(source.encode())
    doc = project.read_document(tmp_path, "game.gel")
    fn = doc["functions"][0]
    assert fn["locals"] == ["e"]
    assert [item["block"]["op"] for item in fn["statements"] if not item.get("insertion")] == ["header", "add"]
    path, result = project.apply_changes(tmp_path, {
        "name": "game.gel", "hash": doc["hash"], "function": 0,
        "changes": [{"index": 1, "code": "e += 2;"}], "addition": ""})
    assert path == tmp_path / "game.gel"
    assert result == source.replace("e += 1;", "e += 2;")


MOVE_SOURCE = ("fn sample(u8 e) {\n"
               "    u8 a = 1;\n"
               "    a += e; // use a\n"
               "    if (e > 1) {\n"
               "        u8 b = a;\n"
               "        b += 1;\n"
               "    }\n"
               "    while (a < 5) {\n"
               "        a += 1;\n"
               "    }\n"
               "    e = 2;\n"
               "}\n")


def move_payload(tmp_path, names, **extra):
    (tmp_path / "game.gel").write_text(MOVE_SOURCE, encoding="utf-8", newline="")
    doc = project.read_document(tmp_path, "game.gel")
    body = doc["functions"][0]["source"]
    offsets = {"decl a": "u8 a", "use a": "a += e", "if": "if (e", "while": "while", "e=2": "e = 2"}
    units = [body.index(offsets[name]) for name in names]
    root = doc["functions"][0]["rootBlock"]
    return {"name": "game.gel", "hash": doc["hash"], "function": 0, "units": units, **extra}, root, body


def test_moving_control_block_carries_its_contents(tmp_path):
    payload, root, body = move_payload(tmp_path, ["while"])
    payload["target"] = {"block": root, "before": body.index("if (e")}
    _, out = project.apply_moves(tmp_path, payload)
    assert out.index("while (a < 5) {\n        a += 1;\n    }") < out.index("if (e > 1)")
    assert out.count("a += 1;") == 1


def test_moves_that_break_variable_scope_are_rejected(tmp_path):
    payload, root, body = move_payload(tmp_path, ["decl a"])
    payload["target"] = {"block": root, "before": None}
    with pytest.raises(ValueError):
        project.apply_moves(tmp_path, payload)
    payload, root, body = move_payload(tmp_path, ["use a"])
    payload["target"] = {"block": root, "before": body.index("u8 a")}
    with pytest.raises(ValueError):
        project.apply_moves(tmp_path, payload)


def test_move_preview_lists_valid_and_invalid_targets(tmp_path):
    payload, _, _ = move_payload(tmp_path, ["while"], preview=True)
    result = project.preview_moves(tmp_path, payload)
    assert result["targets"]
    assert any(item["ok"] for item in result["targets"])


def test_comments_can_be_edited_and_deleted(tmp_path):
    source = ("fn sample(u8 e) {\n"
              "    // keep this\n"
              "    e += 1; // trailing\n"
              "    /* block\n"
              "       note */\n"
              "    e += 2;\n"
              "}\n")
    (tmp_path / "game.gel").write_text(source, encoding="utf-8", newline="")
    doc = project.read_document(tmp_path, "game.gel")
    fn = doc["functions"][0]
    assert [c["text"] for c in fn["comments"]] == ["// keep this", "// trailing", "/* block\n       note */"]
    payload = {"name": "game.gel", "hash": doc["hash"], "function": 0, "changes": [], "addition": ""}
    _, result = project.apply_changes(tmp_path, {**payload, "comment_changes": [
        {"index": 0, "text": "new words"}, {"index": 1, "text": None}, {"index": 2, "text": None}]})
    assert result == ("fn sample(u8 e) {\n    // new words\n    e += 1;\n    e += 2;\n}\n")
    with pytest.raises(ValueError):
        project.apply_changes(tmp_path, {**payload, "comment_changes": [{"index": 0, "text": "a\nb"}]})
    with pytest.raises(ValueError):
        project.apply_changes(tmp_path, {**payload, "comment_changes": [{"index": 2, "text": "x */ y"}]})


def test_control_headers_and_returns_are_editable_without_losing_source(tmp_path):
    source = (
        "fn sample(u8 x) {\r\n"
        "  if (x > 1) { // keep comment\r\n"
        "    asm { NOP }\r\n"
        "    return x;\r\n"
        "  } else if (x < 2) { x++; } else { return; }\r\n"
        "  while (x) { x *= 2; }\r\n"
        "  Entities.for_each(item) { x = item; }\r\n"
        "  switch (x) { case 2: x = 1; break; default: x = 2; }\r\n"
        "  u8 value = 2;\r\n"
        "}\r\n"
    )
    (tmp_path / "game.gel").write_bytes(source.encode())
    doc = project.read_document(tmp_path, "game.gel")
    parts = doc["functions"][0]["statements"]
    editable = [(i, p) for i, p in enumerate(parts) if not p.get("insertion")]
    assert [p["block"]["op"] for _, p in editable] == [
        "header", "return", "header", "increment", "return", "header", "assign",
        "foreachHeader", "set", "header", "header", "set", "set", "declare"]
    assert [p["block"]["label"] for _, p in editable if p["block"]["op"] == "header"] == [
        "if condition", "else if condition", "while condition", "switch value", "case value"]
    changes = [
        {"index": editable[0][0], "code": "x >= 4"},
        {"index": editable[1][0], "code": "return 3;"},
        {"index": editable[2][0], "code": "x == 0"},
        {"index": editable[5][0], "code": "x > 0"},
        {"index": editable[7][0], "code": "Entities.for_each(other)"},
        {"index": editable[10][0], "code": "3"},
    ]
    _, result = project.apply_changes(tmp_path, {
        "name": "game.gel", "hash": doc["hash"], "function": 0,
        "changes": changes, "addition": ""})
    expected = (source.replace("x > 1", "x >= 4").replace("return x;", "return 3;")
                .replace("x < 2", "x == 0").replace("while (x)", "while (x > 0)")
                .replace("for_each(item)", "for_each(other)").replace("case 2:", "case 3:"))
    assert result == expected


def test_header_with_comments_remains_preserved(tmp_path):
    source = "fn sample(u8 x) { if (x /* keep */ > 1) { return x; } }"
    (tmp_path / "game.gel").write_text(source, encoding="utf-8")
    doc = project.read_document(tmp_path, "game.gel")
    parts = doc["functions"][0]["statements"]
    assert [p["block"]["op"] for p in parts if not p.get("insertion")] == ["return"]
    _, result = project.apply_changes(tmp_path, {
        "name": "game.gel", "hash": doc["hash"], "function": 0,
        "changes": [{"index": 0, "code": "return 2;"}], "addition": ""})
    assert result == source.replace("return x;", "return 2;")


def test_inserts_into_existing_blocks_and_switch_cases(tmp_path):
    source = ("fn sample(u8 x) {\r\n"
              "  if (x) { x = 1; }\r\n"
              "  switch (x) { case 1: x = 2; break; default: x = 3; }\r\n"
              "}\r\n")
    (tmp_path / "game.gel").write_bytes(source.encode())
    doc = project.read_document(tmp_path, "game.gel")
    parts = doc["functions"][0]["statements"]
    insertions = [(i, part) for i, part in enumerate(parts) if part.get("insertion")]
    assert [p["label"] for _, p in insertions] == [
        "Add inside this block", "Add to this case", "Add to default"]
    changes = [{"index": i, "code": "\r\n    x += 1;\r\n"} for i, _ in insertions]
    _, result = project.apply_changes(tmp_path, {
        "name": "game.gel", "hash": doc["hash"], "function": 0,
        "changes": changes, "addition": ""})
    assert result.count("x += 1;") == 3
    assert "x += 1;\r\nbreak;" in result
    assert result.count("\n") == result.count("\r\n")


def test_visual_layout_nests_controls_without_exposing_structural_punctuation():
    source = (
        "fn sample(u8 x) {\n"
        "  // outside\n"
        "  if (x) { // first body\n"
        "    while (x) { x -= 1; }\n"
        "  } /* between */ else if (x > 1) { return x; }\n"
        "  else { asm { NOP } }\n"
        "  Entities.for_each(item) { x = item; }\n"
        "  switch (x) { case 1: x = 2; break; default: x = 3; }\n"
        "}\n"
    )
    fn = project.functions(source)[0]

    def flatten(items):
        for item in items:
            yield item
            for branch in item.get("branches", []):
                yield from flatten(branch["items"])
                yield from flatten(branch["body"])

    items = list(flatten(fn["display"]))
    raw = [item["text"].strip() for item in items if item["kind"] == "raw"]
    assert raw == ["asm { NOP }"]
    assert [fn["comments"][item["index"]]["text"] for item in items if item["kind"] == "comment"] == [
        "// outside", "// first body", "/* between */"]
    assert [item["style"] for item in items if item["kind"] == "control"] == [
        "if", "while", "for_each", "switch", "cases"]
    assert len(fn["display"][1]["branches"]) == 3
    indexes = [item["index"] for item in items if item["kind"] == "part"]
    assert sorted(indexes) == list(range(len(fn["statements"])))
    assert fn["signature"] == "fn sample(u8 x)"


def test_comment_in_control_header_is_displayed_without_structural_fragments():
    source = "fn sample(u8 x) { if /* before */ (x /* inside */ > 1) /* after */ { return; } }"
    fn = project.functions(source)[0]
    branch = fn["display"][0]["branches"][0]
    texts = [fn["comments"][item["index"]]["text"] if item["kind"] == "comment" else item.get("text")
             for item in branch["items"]]
    assert texts == ["/* before */", "if condition", "x /* inside */ > 1", "/* after */"]
    assert branch["body"][0]["kind"] == "part"


def test_increment_and_decrement_import_preserves_prefix_and_target():
    fn = project.functions("fn f(u16 x) { x++; --x; Entities[x].flags--; ++x; }")[0]
    blocks = [part["block"] for part in fn["statements"]]
    assert blocks == [
        {"op": "increment", "target": "x", "prefix": False},
        {"op": "decrement", "target": "x", "prefix": True},
        {"op": "decrement", "target": "Entities[x].flags", "prefix": False},
        {"op": "increment", "target": "x", "prefix": True}]
    assert fn["localTypes"] == {"x": "u16"}


def test_stale_and_invalid_edits_are_rejected(tmp_path):
    path = tmp_path / "game.gel"
    path.write_text("fn sample() { x = 1; }\n", encoding="utf-8")
    doc = project.read_document(tmp_path, path.name)
    payload = {"name": path.name, "hash": doc["hash"], "function": 0,
               "changes": [{"index": 0, "code": "x = ;"}], "addition": ""}
    with pytest.raises(ValueError, match="syntax error"):
        project.apply_changes(tmp_path, payload)
    payload["changes"][0]["code"] = "x = 2;"
    path.write_text("fn sample() { x = 3; }\n", encoding="utf-8")
    with pytest.raises(ValueError, match="changed since"):
        project.apply_changes(tmp_path, payload)
    assert path.read_text(encoding="utf-8") == "fn sample() { x = 3; }\n"


def test_comments_braces_strings_and_unparseable_function(tmp_path):
    source = 'fn first() { call("{"); // }\n x = 1; }\nfn broken() { x = ; }\nfn last() { x = 2; }\n'
    (tmp_path / "game.gel").write_text(source, encoding="utf-8")
    doc = project.read_document(tmp_path, "game.gel")
    assert [f["name"] for f in doc["functions"]] == ["first", "broken", "last"]
    assert doc["functions"][1]["error"]
    assert doc["functions"][2]["statements"][0]["block"]["target"] == "x"


def test_rejects_generated_contact_file_and_path_escape(tmp_path):
    (tmp_path / "contact.gel").write_text("fn test() { x = 1; }", encoding="utf-8")
    doc = project.read_document(tmp_path, "contact.gel")
    with pytest.raises(ValueError, match="generated"):
        project.apply_changes(tmp_path, {"name": "contact.gel", "hash": doc["hash"],
                                        "function": 0, "changes": [], "addition": ""})
    with pytest.raises(ValueError, match="filename"):
        project.read_document(tmp_path, "../contact.gel")


def test_add_and_remove_functions_preserve_source_and_newlines(tmp_path):
    path = tmp_path / "game.gel"
    original = b"// keep\r\nstatic fn old() { old(); }\r\nfn other() { return; }\r\n"
    path.write_bytes(original)
    symbols = {key: {} for key in ("functions", "globals", "tables", "enums", "defines", "hardware", "bit")}
    payload = {"name": "game.gel", "hash": project.text_hash(original.decode()),
               "action": "add", "function_name": "new_func"}
    _, updated = project.change_function(tmp_path, payload, symbols)
    assert updated.endswith("fn new_func() {\r\n}\r\n")
    assert updated.startswith(original.decode())
    assert not [fn["error"] for fn in project.functions(updated) if fn["error"]]
    payload.update(action="remove", function=0)
    _, removed = project.change_function(tmp_path, payload, symbols)
    assert removed == "// keep\r\n\r\nfn other() { return; }\r\n"


def test_function_mutations_reject_invalid_names_references_and_stale_edits(tmp_path):
    path = tmp_path / "game.gel"
    source = "fn first() { return; }\nfn second() { first(); }\n"
    path.write_bytes(source.encode())
    symbols = {key: {} for key in ("functions", "globals", "tables", "enums", "defines", "hardware", "bit")}
    payload = {"name": "game.gel", "hash": project.text_hash(source), "action": "add"}
    for name in ("bad name", "if", "first"):
        with pytest.raises(ValueError):
            project.change_function(tmp_path, {**payload, "function_name": name}, symbols)
    with pytest.raises(ValueError, match="possible references.*game.gel:2"):
        project.change_function(tmp_path, {**payload, "action": "remove", "function": 0}, symbols)
    with pytest.raises(ValueError, match="changed since"):
        project.change_function(tmp_path, {**payload, "hash": "stale", "function_name": "new"}, symbols)
    (tmp_path / "contact.gel").write_bytes(b"fn generated() {}")
    with pytest.raises(ValueError, match="generated"):
        project.change_function(tmp_path, {**payload, "name": "contact.gel"}, symbols)
    assert path.read_bytes() == source.encode()


def test_function_removal_finds_cross_file_and_macro_references(tmp_path):
    source = "fn helper() { return; }\n"
    (tmp_path / "game.gel").write_bytes(source.encode())
    other = tmp_path / "other.gel"
    other.write_bytes(b"// helper()\nfn g() { helper(); }\n")
    symbols = {key: {} for key in ("functions", "globals", "tables", "enums", "defines", "hardware", "bit")}
    payload = {"name": "game.gel", "hash": project.text_hash(source), "action": "remove", "function": 0}
    with pytest.raises(ValueError, match="other.gel:2"):
        project.change_function(tmp_path, payload, symbols)
    other.write_bytes(b'#define CALL_HELPER helper\nfn g() { return; }\n')
    with pytest.raises(ValueError, match="other.gel:1"):
        project.change_function(tmp_path, payload, symbols)
    other.write_bytes(b'// helper()\nfn g() { log("helper"); }\n')
    _, result = project.change_function(tmp_path, payload, symbols)
    assert result == "\n"


def test_control_blocks_can_be_removed_but_not_when_scope_breaks(tmp_path):
    payload, _, _ = move_payload(tmp_path, ["while"])
    _, out = project.apply_delete(tmp_path, payload)
    assert "while" not in out and "a += 1;" not in out
    payload, _, _ = move_payload(tmp_path, ["decl a"])
    with pytest.raises(ValueError):
        project.apply_delete(tmp_path, payload)


def test_switch_case_can_be_removed(tmp_path):
    source = ("fn f(u8 e) {\n    switch (e) {\n        case 1:\n            e = 2;\n            break;\n"
              "        case 3:\n            e = 4;\n            break;\n    }\n}\n")
    (tmp_path / "game.gel").write_text(source, encoding="utf-8", newline="")
    doc = project.read_document(tmp_path, "game.gel")
    body = doc["functions"][0]["source"]
    payload = {"name": "game.gel", "hash": doc["hash"], "function": 0, "case": body.index("case 1")}
    _, out = project.apply_delete(tmp_path, payload)
    assert "case 1" not in out and "e = 2" not in out
    assert "case 3:\n            e = 4;\n            break;" in out

def test_copy_and_paste_statements(tmp_path):
    payload, root, body = move_payload(tmp_path, ["while", "e=2"], copy=True)
    copied = project.copy_code(tmp_path, payload)
    assert copied["count"] == 2
    assert copied["lines"] == ["while (a < 5) {", "    a += 1;", "}", "e = 2;"]
    payload, root, body = move_payload(tmp_path, [])
    payload.pop("units")
    payload.update(paste=True, lines=copied["lines"], target={"block": root, "before": None})
    _, out = project.apply_paste(tmp_path, payload)
    assert out.count("while (a < 5) {") == 2
    assert out.endswith("    e = 2;\n}\n")
    payload["target"] = {"before": body.index("e = 2")}
    _, out = project.apply_paste(tmp_path, payload)
    assert out.count("e = 2;") == 2 and out.index("while (a < 5) {\n        a += 1;\n    }\n    while") < out.index("e = 2;")


def test_paste_rejects_scope_breaks_and_bad_code(tmp_path):
    payload, root, body = move_payload(tmp_path, [])
    payload.pop("units")
    payload.update(paste=True, target={"block": root, "before": None})
    with pytest.raises(ValueError):
        project.apply_paste(tmp_path, {**payload, "lines": ["u8 a = 2;"]})
    with pytest.raises(ValueError):
        project.apply_paste(tmp_path, {**payload, "lines": ["a += ;"]})
    with pytest.raises(ValueError):
        project.apply_paste(tmp_path, {**payload, "lines": []})
