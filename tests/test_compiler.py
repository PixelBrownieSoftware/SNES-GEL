import pytest

from gel_codegen import compile_gel


def compile_src(src, **kw):
    return compile_gel(src, {}, hot_reload=False, **kw)


def test_minimal_program_compiles():
    asm, _ = compile_src("fn init_game() { }")
    assert "init_game:" in asm
    assert "JSR init_game" in asm


def test_globals_and_symbols_listed():
    asm, _ = compile_src("u8 lives = 3;\nfn init_game() { lives = lives + 1; }")
    assert "; ram  u8  lives @ $" in asm


def test_persist_variable_is_recorded_in_layout():
    _, layout = compile_src("persist u16 score = 0;\nfn init_game() { }")
    assert layout["score"][0] == "u16"


def test_persist_address_is_stable_across_builds():
    src = "persist u16 score = 0;\nfn init_game() { }"
    _, first = compile_gel(src, {}, hot_reload=True)
    _, second = compile_gel("u8 extra;\n" + src, first, hot_reload=True)
    assert second["score"] == first["score"]


def test_undeclared_variable_is_an_error():
    with pytest.raises(Exception) as err:
        compile_src("fn init_game() { nope = 1; }")
    assert "nope" in str(err.value)


def test_unknown_table_is_an_error():
    with pytest.raises(Exception):
        compile_src("fn init_game() { Missing[0].x = 1; }")


def test_table_field_store_keeps_offset_in_y():
    src = "struct_table T[8] { u16 x; }\nu8 i;\nfn init_game() { T[i].x = 5; T[i].x += 2; }"
    asm, _ = compile_src(src)
    body = asm.split("init_game:")[1].split("RTS")[0]
    assert "PHA" not in body


def test_table_store_with_table_read_value_uses_general_path():
    # The value reads another table (clobbers Y), so the offset must be saved around it.
    src = ("struct_table T[8] { u16 x; }\nu8 i;\nu8 j;\n"
           "fn init_game() { T[i].x = T[j].x; }")
    asm, _ = compile_src(src)
    assert "PHA" in asm.split("init_game:")[1]


def test_optimizer_can_be_disabled():
    src = "u8 a;\nfn init_game() { a = 1; if (a == 1) { a = 2; } }"
    assert compile_src(src, optimize=True)[0]
    assert compile_src(src, optimize=False)[0]


def test_enum_members_become_constants():
    asm, _ = compile_src("enum E { A, B = 5, C };\nu8 v;\nfn init_game() { v = C; }")
    assert "#$0006" in asm or "#$06" in asm
