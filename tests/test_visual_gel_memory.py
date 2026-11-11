import importlib.util
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("visual_memory", ROOT / "tools" / "visual-gel" / "memory.py")
memory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(memory)


def test_missing_build_and_compiled_symbol_layout(tmp_path):
    assert not memory.memory_layout(tmp_path)["available"]
    build = tmp_path / "build"
    build.mkdir()
    (build / "build_output.asm").write_text(
        "lorom\n; --- Symbols ---\n"
        "; ram  u8  active @ $42\n"
        "; mmio u16 REG @ $2100\n"
        "; table Entities[10], pool mask @ $44\n"
        ";   .type u8 @ $0600 (+1/slot)\n"
        ";   .x u16 @ $060A (+2/slot)\n"
        "; table Shadow[128]\n"
        ";   .x u8 @ $0200 (+4/slot)\n"
        "\n; --- Global initialisers ---\n",
        encoding="utf-8")
    result = memory.memory_layout(tmp_path)
    assert result["variables"] == [
        {"name": "active", "type": "u8", "kind": "ram", "address": 0x42},
        {"name": "REG", "type": "u16", "kind": "mmio", "address": 0x2100}]
    assert result["tables"][0] == {
        "name": "Entities", "size": 10, "mask": 0x44,
        "fields": [{"name": "type", "type": "u8", "address": 0x600, "stride": 1},
                   {"name": "x", "type": "u16", "address": 0x60A, "stride": 2}]}
    assert result["tables"][1]["mask"] is None
    assert result["tables"][1]["fields"][0]["stride"] == 4
    assert result["reserved"] == [
        {"name": "Compiler scratch", "start": 0, "end": 0x0F},
        {"name": "Sound driver", "start": 0xE8, "end": 0xFF},
        {"name": "Stack", "start": 0x1F00, "end": 0x1FFF},
    ]


def test_malformed_summary_is_not_silently_reported_as_empty(tmp_path):
    build = tmp_path / "build"
    build.mkdir()
    path = build / "build_output.asm"
    path.write_text("lorom\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no symbol summary"):
        memory.memory_layout(tmp_path)
    path.write_text("; --- Symbols ---\n;   .field u8 @ $0600 (+1/slot)\n", encoding="utf-8")
    with pytest.raises(ValueError, match="without a table"):
        memory.memory_layout(tmp_path)
