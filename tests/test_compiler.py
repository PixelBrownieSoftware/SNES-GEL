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


def test_unrelated_function_locals_reuse_ram():
    src = ("fn first() { u16 a = 1; }\n"
           "fn second() { u16 b = 2; }\n"
           "fn init_game() { first(); second(); }")
    asm, _ = compile_src(src)

    def first_local_store(name):
        body = asm.split(name + ":")[1].split("RTS")[0]
        return next(line.strip().split()[1] for line in body.splitlines()
                    if line.strip().startswith("STA $"))

    assert first_local_store("first") == first_local_store("second")


def test_nested_function_locals_do_not_share_ram():
    src = ("fn child() { u16 a = 1; }\n"
           "fn parent() { u16 b = 2; child(); b += 1; }\n"
           "fn init_game() { parent(); }")
    asm, _ = compile_src(src)

    def first_local_store(name):
        body = asm.split(name + ":")[1].split("RTS")[0]
        return next(line.strip().split()[1] for line in body.splitlines()
                    if line.strip().startswith("STA $"))

    assert first_local_store("parent") != first_local_store("child")


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


def test_abs_compiles_inline_for_signed_values():
    asm, _ = compile_src("i16 v = -5;\nu16 r;\nfn init_game() { r = abs(v); }")
    assert "EOR.w #$FFFF" in asm
    assert "JSR abs" not in asm


def test_integer_times_fixed_constant_is_fixed_point_result():
    asm, _ = compile_src("i8 h;\nf16 v;\nfn init_game() { v += h * 0.25; }")
    assert "JSR _gel_mul16" not in asm


def test_fixed_times_fixed_constant_uses_shifts():
    asm, _ = compile_src("f16 v;\nfn init_game() { v = v * 1.5; }")
    assert "JSR _gel_fmul" not in asm
    assert "ROR A" in asm


def test_sign_of_fixed_point_is_one_point_zero():
    asm, _ = compile_src("f16 v;\nf16 s;\nfn init_game() { s = -sign(v) * 0.25; }")
    assert "LDA.w #$FF00" in asm
    assert "JSR _gel_fmul" not in asm


def test_sign_is_inline():
    asm, _ = compile_src("i16 v;\ni16 s;\nfn init_game() { s = sign(v); }")
    assert "LDA.w #$FFFF" in asm
    assert "JSR sign" not in asm


def test_clamp_is_inline_and_validates_arguments():
    asm, _ = compile_src("i16 v;\nu16 w;\nfn init_game() { v = clamp(v, -8, 8); w = clamp(w, 0, 100); }")
    assert "JSR clamp" not in asm
    assert "TXA" in asm
    with pytest.raises(Exception):
        compile_src("u16 v;\nfn init_game() { v = clamp(v, 1); }")
