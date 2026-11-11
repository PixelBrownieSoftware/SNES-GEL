"""Read the compiler's latest generated RAM and MMIO symbol summary."""

import re
from pathlib import Path

from gel_codegen import DP_START, SOUND_DP, STACK_TOP, TABLE_END

VARIABLE = re.compile(r"^; (ram|mmio)\s+(\w+)\s+(\w+) @ \$([0-9A-Fa-f]+)$")
TABLE = re.compile(r"^; table (\w+)\[(\d+)\](?:, pool mask @ \$([0-9A-Fa-f]+))?$")
FIELD = re.compile(r"^;\s+\.(\w+) (\w+) @ \$([0-9A-Fa-f]+) \(\+(\d+)/slot\)$")


def memory_layout(project):
    reserved = [
        {"name": "Compiler scratch", "start": 0, "end": DP_START - 1},
        {"name": "Sound driver", "start": SOUND_DP[0], "end": SOUND_DP[1]},
        {"name": "Stack", "start": TABLE_END + 1, "end": STACK_TOP},
    ]
    path = Path(project) / "build" / "build_output.asm"
    if not path.is_file():
        return {"available": False, "variables": [], "tables": [], "reserved": reserved}
    lines = path.read_text(encoding="utf-8").splitlines()
    try:
        start = lines.index("; --- Symbols ---") + 1
    except ValueError as error:
        raise ValueError("The compiler output has no symbol summary. Rebuild the game.") from error
    variables, tables = [], []
    table = None
    for line in lines[start:]:
        if not line.startswith(";"):
            break
        if match := VARIABLE.fullmatch(line):
            kind, typ, name, address = match.groups()
            variables.append({"name": name, "type": typ, "kind": kind,
                              "address": int(address, 16)})
            table = None
        elif match := TABLE.fullmatch(line):
            name, size, mask = match.groups()
            table = {"name": name, "size": int(size),
                     "mask": int(mask, 16) if mask else None, "fields": []}
            tables.append(table)
        elif match := FIELD.fullmatch(line):
            if table is None:
                raise ValueError("Invalid compiler symbol summary: field without a table.")
            name, typ, address, stride = match.groups()
            table["fields"].append({"name": name, "type": typ,
                                    "address": int(address, 16), "stride": int(stride)})
        elif line.strip():
            raise ValueError(f"Unrecognized compiler symbol summary line: {line}")
    return {"available": True, "variables": variables, "tables": tables, "reserved": reserved}
