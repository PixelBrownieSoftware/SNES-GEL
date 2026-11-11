"""ROM overview: read the last build's ROM layout and edit the ROM size settings in gel.json."""

import json
import os
import tempfile
from pathlib import Path

BANK_SIZE = 0x8000
ROM_KB = (32, 64, 128, 256, 512, 1024, 2048, 4096)
SRAM_KB = (0, 2, 4, 8, 16, 32, 64, 128)
ASSET_START_RANGE = (0x9000, 0xF000)
DEFAULT_ASSET_START = 0xC000
HEADER_DEFAULTS = {"title": "GEL GAME TITLE", "rom_size_kb": 32, "sram_kb": 0,
                   "region": 1, "developer": 0, "version": 0, "rom_mode": "lorom", "fastrom": True}


def read_config(project):
    path = Path(project) / "gel.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ValueError(f"gel.json is not valid JSON: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("gel.json must contain an object.")
    return data


def read_build_layout(project):
    path = Path(project) / "build" / "rom-layout.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def bank_role(layout, bank):
    if bank == 0x80:
        return "code"
    if bank == layout["spc_bank"]:
        return "spc"
    if layout["soundbank_bank"] <= bank < layout["first_free_bank"]:
        return "soundbank"
    return "data"


def describe(project):
    config = read_config(project)
    header = {**HEADER_DEFAULTS, **(config.get("header") or {})}
    layout_config = config.get("layout") or {}
    result = {
        "header": header,
        "asset_start": layout_config.get("asset_start", DEFAULT_ASSET_START),
        "asset_start_range": list(ASSET_START_RANGE),
        "rom_sizes": list(ROM_KB),
        "sram_sizes": list(SRAM_KB),
        "build": None,
    }
    layout = read_build_layout(project)
    if not layout:
        return result
    banks = [{**item, "role": bank_role(layout, item["bank"]), "size": BANK_SIZE}
             for item in layout["banks"]]
    used_banks = [b["bank"] for b in banks if b["used"]]
    stride = 0x10000 if layout.get("rom_mode") == "hirom" else BANK_SIZE
    needed_bytes = ((max(used_banks) - 0x80 + 1) * stride) if used_banks else stride
    needed_kb = 32
    while needed_kb * 1024 < needed_bytes:
        needed_kb *= 2
    result["build"] = {**{k: v for k, v in layout.items() if k != "banks"},
                       "banks": banks, "needed_kb": needed_kb,
                       "used_bytes": sum(b["used"] for b in banks)}
    return result


def clean_settings(payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected ROM settings.")
    out = {}
    for key, allowed, label in (("rom_size_kb", ROM_KB, "ROM size"), ("sram_kb", SRAM_KB, "Save RAM")):
        if key in payload:
            if type(payload[key]) is not int or payload[key] not in allowed:
                raise ValueError(f"{label} must be one of {', '.join(str(k) for k in allowed)} KB.")
            out[key] = payload[key]
    if "rom_mode" in payload:
        mode = payload["rom_mode"]
        if not isinstance(mode, str) or mode.lower() not in ("lorom", "hirom"):
            raise ValueError("ROM mapping must be LoROM or HiROM.")
        out["rom_mode"] = mode.lower()
    if "fastrom" in payload:
        if type(payload["fastrom"]) is not bool:
            raise ValueError("FastROM must be true or false.")
        out["fastrom"] = payload["fastrom"]
    if "title" in payload:
        title = payload["title"]
        if not isinstance(title, str) or not title.strip() or len(title) > 21 or not title.isascii() \
                or not title.isprintable():
            raise ValueError("The title must be 1 to 21 printable ASCII characters.")
        out["title"] = title
    for key, high in (("region", 14), ("developer", 255), ("version", 255)):
        if key in payload:
            if type(payload[key]) is not int or not 0 <= payload[key] <= high:
                raise ValueError(f"{key} must be a whole number from 0 to {high}.")
            out[key] = payload[key]
    asset_start = None
    if "asset_start" in payload:
        value = payload["asset_start"]
        low, high = ASSET_START_RANGE
        if type(value) is not int or not low <= value <= high or value % 0x100:
            raise ValueError(f"Asset start must be a multiple of 256 from ${low:04X} to ${high:04X}.")
        asset_start = value
    return out, asset_start


def apply_settings(project, payload):
    """Merge changed settings into gel.json, keeping every other key. Returns (path, text)."""
    header_changes, asset_start = clean_settings(payload)
    config = read_config(project)
    header = dict(config.get("header") or {})
    header.update(header_changes)
    if header:
        config["header"] = header
    if asset_start is not None:
        layout = dict(config.get("layout") or {})
        if asset_start == DEFAULT_ASSET_START:
            layout.pop("asset_start", None)
        else:
            layout["asset_start"] = asset_start
        if layout:
            config["layout"] = layout
        else:
            config.pop("layout", None)
    return Path(project) / "gel.json", json.dumps(config, indent=4) + "\n"


def atomic_write(path, text):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
            temporary = Path(out.name)
            out.write(text.encode("utf-8"))
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
