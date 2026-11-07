import json
import re
from pathlib import Path


EXTENSION = Path(__file__).resolve().parents[1] / "snes-gel-vscode"
GRAMMAR = json.loads((EXTENSION / "syntaxes" / "gel.tmLanguage.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((EXTENSION / "package.json").read_text(encoding="utf-8"))
THEME_PATH = EXTENSION / MANIFEST["contributes"]["themes"][0]["path"]
THEME = json.loads(THEME_PATH.read_text(encoding="utf-8"))


def colors():
    return {
        scope: rule["settings"]["foreground"]
        for rule in THEME["tokenColors"]
        for scope in ([rule["scope"]] if isinstance(rule["scope"], str) else rule["scope"])
    }


def matching_rule(section, text):
    return next(
        (rule for rule in GRAMMAR["repository"][section]["patterns"]
         if "match" in rule and re.search(rule["match"], text)),
        None,
    )


def test_theme_is_contributed_and_scoped_to_gel():
    theme = MANIFEST["contributes"]["themes"][0]
    assert theme["label"] == "SNES-GEL Prototype"
    assert theme["uiTheme"] == "vs-dark"
    assert THEME["type"] == "dark"
    assert all(scope.endswith(".gel") for scope in colors())


def test_primitive_type_palette():
    for type_name, expected in {
        "u8": "#A5B4FF", "u16": "#849DFF", "i8": "#00ED20",
        "i16": "#16C431", "f16": "#00FFD0",
    }.items():
        rule = matching_rule("types", type_name + " value;")
        assert rule["name"] == f"storage.type.primitive.{type_name}.gel"
        assert colors()[rule["name"]] == expected
        assert not re.search(rule["match"], type_name + "_value")


def test_semantic_colors_match_declaration_colors():
    semantic = THEME["semanticTokenColors"]
    assert THEME["semanticHighlighting"] is True
    for type_name in ("u8", "u16", "i8", "i16", "f16", "f32"):
        assert semantic["gel" + type_name.upper()] == colors()[f"storage.type.primitive.{type_name}.gel"]
    assert semantic["gelEnum"] == colors()["constant.other.enum.member.gel"]
    assert semantic["gelDefine"] == colors()["entity.name.constant.macro.gel"]
    declared = {item["id"] for item in MANIFEST["contributes"]["semanticTokenTypes"]}
    assert set(semantic) == declared


def test_palette_scopes_for_representative_gel_syntax():
    for section, text, scope, color in [
        ("keywords", "if (v)", "keyword.control.gel", "#FFC90E"),
        ("keywords", "for_each(e)", "keyword.control.gel", "#FFC90E"),
        ("builtins", "bit_clear(v, 1)", "support.function.bit.gel", "#00E6FF"),
        ("builtins", "std_draw_frame()", "support.function.stdlib.gel", "#ED810E"),
        ("functions", "update_player()", "entity.name.function.call.gel", "#9E9575"),
        ("table-members", "Entities.for_each(e)", "variable.other.table.gel", "#ED1C24"),
    ]:
        rule = matching_rule(section, text)
        assert rule["name"] == scope
        assert colors()[scope] == color

    declarations = GRAMMAR["repository"]["declarations"]["patterns"]
    for text, expected in [
        ("fn update_player()", "entity.name.function.gel"),
        ("fn std_draw_frame()", "support.function.stdlib.gel"),
        ("struct_table Entities[10]", "entity.name.type.struct.gel"),
    ]:
        rule = next(rule for rule in declarations if re.search(rule["match"], text))
        assert rule["captures"]["2"]["name"] == expected


def test_define_enum_and_indexed_table_scopes():
    define = matching_rule("directives", "#define SPEED 2")
    assert colors()[define["captures"]["1"]["name"]] == "#C514E3"
    assert colors()[define["captures"]["2"]["name"]] == "#C514E3"

    enum = GRAMMAR["repository"]["enums"]
    assert re.search(enum["begin"], "enum Faction")
    assert colors()[enum["beginCaptures"]["2"]["name"]] == "#FF6DCF"
    member = matching_rule("enums", "PLAYER = 1")
    assert colors()[member["name"]] == "#FF6DCF"

    table = next(rule for rule in GRAMMAR["repository"]["table-members"]["patterns"]
                 if "begin" in rule)
    assert re.search(table["begin"], "Entities[std_anim_frame(a, b, c)].x")
    assert colors()[table["beginCaptures"]["1"]["name"]] == "#ED1C24"
    assert table["patterns"] == [{"include": "$self"}]


def test_other_compiler_builtins_remain_distinct_from_bit_helpers():
    for name in ("abs", "clamp", "sign", "peek8", "peek8l", "reg"):
        arguments = "A" if name == "reg" else "value"
        rule = matching_rule("builtins", f"{name}({arguments})")
        assert rule["name"] == "support.function.builtin.gel"
        assert colors()[rule["name"]] == "#ED810E"


def test_comments_keep_theme_default():
    assert "comment.line.double-slash.gel" not in colors()
