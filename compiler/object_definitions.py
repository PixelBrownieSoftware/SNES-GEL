"""Validation and GEL generation for object-editor capability definitions."""

import json
import math
import re
from pathlib import Path

FLAGS = {"movable": 0, "ghost": 1, "gravity": 3, "contactable": 4}
CLASS_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
RESERVED_FIELDS = {"type", "x", "y", "param", "capabilities"}
RANGES = {"u8": (0, 255), "i8": (-128, 127), "u16": (0, 65535),
          "i16": (-32768, 32767), "f16": (-128, 127 + 255 / 256)}


def validate(data):
    if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("objects"), list):
        raise ValueError("Expected a version 1 object configuration.")
    names = set()
    for obj in data["objects"]:
        if not isinstance(obj, dict) or not isinstance(obj.get("class"), str) or not CLASS_NAME.fullmatch(obj["class"]):
            raise ValueError("Object classes must be identifiers containing only letters, digits and underscores.")
        name = obj["class"].upper()
        if name in names:
            raise ValueError(f"Duplicate object class: {obj['class']}.")
        names.add(name)
        flags = obj.get("flags")
        if not isinstance(flags, dict) or set(flags) != set(FLAGS) or any(type(value) is not bool for value in flags.values()):
            raise ValueError(f"Object {obj['class']} must specify all four boolean flags.")
        fields = obj.get("editable_fields", [])
        if (not isinstance(fields, list) or any(not isinstance(f, str) or not CLASS_NAME.fullmatch(f)
                or f in RESERVED_FIELDS for f in fields) or len(set(fields)) != len(fields)):
            raise ValueError(f"Object {obj['class']} has invalid or duplicate editable entity fields.")
        variants = obj.get("variants", [])
        if not isinstance(variants, list):
            raise ValueError(f"Object {obj['class']} variants must be a list.")
        variant_ids, variant_names = set(), set()
        for variant in variants:
            if not isinstance(variant, dict):
                raise ValueError(f"Object {obj['class']} has an invalid variant.")
            vid, label = variant.get("id"), variant.get("name")
            if type(vid) is not int or not 0 <= vid <= 255 or vid in variant_ids:
                raise ValueError(f"Object {obj['class']} variant IDs must be unique bytes.")
            if not isinstance(label, str) or not label.strip() or label.casefold() in variant_names:
                raise ValueError(f"Object {obj['class']} variant names must be nonempty and unique.")
            variant_ids.add(vid)
            variant_names.add(label.casefold())
            overrides, values = variant.get("fields", {}), variant.get("values", {})
            if not isinstance(overrides, dict) or not set(overrides).issubset(fields):
                raise ValueError(f"Variant {label} uses fields not enabled for {obj['class']}.")
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in overrides.values()):
                raise ValueError(f"Variant {label} entity values must be finite numbers.")
            if not isinstance(values, dict):
                raise ValueError(f"Variant {label} values must be an object.")
            for key, value in values.items():
                if (not isinstance(key, str) or not CLASS_NAME.fullmatch(key) or
                        key.lower() in ("metasprite_id", "palette_id") or
                        type(value) is not int or not -32768 <= value <= 65535):
                    raise ValueError(f"Variant {label} custom values require identifiers and 16-bit integers.")
            for key, maximum in (("metasprite_id", 255), ("palette_id", 7)):
                value = variant.get(key)
                if value is not None and (type(value) is not int or not 0 <= value <= maximum):
                    raise ValueError(f"Variant {label} {key} must be between 0 and {maximum}.")
    keys = [key.upper() for obj in data["objects"] for variant in obj.get("variants", [])
            for key in variant.get("values", {})]
    spellings = {}
    for obj in data["objects"]:
        for variant in obj.get("variants", []):
            for key in variant.get("values", {}):
                if key.upper() in spellings and spellings[key.upper()] != key:
                    raise ValueError(f"Custom value names differ only by case: {key}.")
                spellings[key.upper()] = key
    if len(set(keys)) > 253:
        raise ValueError("At most 253 distinct custom value names are supported.")
    return data


def validate_fields(data, fields):
    validate(data)
    for obj in data["objects"]:
        for field in obj.get("editable_fields", []):
            if field not in fields or fields[field] not in RANGES:
                raise ValueError(f"Object {obj['class']} references unknown or unsupported Entities field '{field}'.")
        for variant in obj.get("variants", []):
            for field, value in variant.get("fields", {}).items():
                typ = fields[field]
                lo, hi = RANGES[typ]
                if not lo <= value <= hi or (typ != "f16" and (type(value) is not int)):
                    raise ValueError(f"Variant {variant['name']} field {field} must fit {typ}.")


def entity_fields(tree):
    tables = [item for item in tree.children if getattr(item, "data", None) == "struct_table_decl"
              and str(item.children[0]) == "Entities"]
    if len(tables) != 1:
        raise ValueError("Editable object fields require exactly one Entities struct_table.")
    return {str(field.children[1]): str(field.children[0]) for field in tables[0].children
            if getattr(field, "data", None) == "table_field"}


def load(path):
    path = Path(path)
    if not path.exists():
        return {"version": 1, "objects": []}
    return validate(json.loads(path.read_text(encoding="utf-8")))


def object_ids(data, assets_header):
    ids = {name: int(number) for name, number in re.findall(
        r"^#define OBJ_([A-Z0-9_]+) ([0-9]+)\s*$", assets_header, re.MULTILINE)}
    for obj in data["objects"]:
        name = obj["class"].upper()
        if name not in ids:
            next_id = max(ids.values(), default=0) + 1
            if next_id > 255:
                raise ValueError("Object classes exceed the 255 available object type IDs.")
            ids[name] = next_id
    return ids


def variant_records(data, assets_header):
    validate(data)
    ids = object_ids(data, assets_header)
    keys = ["metasprite_id", "palette_id"] + sorted(
        {key for obj in data["objects"] for variant in obj.get("variants", [])
         for key in variant.get("values", {})}, key=str.upper)
    entries = []
    for obj in data["objects"]:
        obj_id = ids.get(obj["class"].upper())
        if obj_id is None:
            continue
        for variant in obj.get("variants", []):
            values = dict(variant.get("values", {}))
            for key in keys[:2]:
                if variant.get(key) is not None:
                    values[key] = variant[key]
            for key, value in values.items():
                entries.append((obj_id, variant["id"], keys.index(key) + 1, value & 0xFFFF))
    if len(entries) > 65535 // 5:
        raise ValueError("Object variant data exceeds the 16-bit addressable table size.")
    return keys, entries


def generate(data, assets_header, field_types=None):
    keys, entries = variant_records(data, assets_header)
    asset_ids = {name: int(number) for name, number in re.findall(
        r"^#define OBJ_([A-Z0-9_]+) ([0-9]+)\s*$", assets_header, re.MULTILINE)}
    ids = object_ids(data, assets_header)
    lines = ["// Generated from objects.json and Tiled object classes."]
    for name, number in ids.items():
        if name not in asset_ids:
            lines.append(f"#define OBJ_{name} {number}")
    lines.append("fn object_capabilities(u8 type) {")
    for obj in data["objects"]:
        name = obj["class"].upper()
        if name in ids:
            mask = sum(1 << bit for flag, bit in FLAGS.items() if obj["flags"][flag])
            lines.append(f"    if (type == {ids[name]}) {{ return {mask}; }}")
    lines.extend(["    return 0;", "}", ""])
    for i, key in enumerate(keys, 1):
        lines.append(f"#define OBJECT_VALUE_{key.upper()} {i}")
    lines += [
        "extern _gel_object_variant_values;",
        "fn object_variant_find(u8 type, u8 variant, u8 key) -> u16 {",
        "    u16 p = _gel_object_variant_values;",
        f"    u16 remaining = {len(entries)};",
        "    while (remaining) {",
        "        if (peek8(p) == type && peek8(p + 1) == variant && peek8(p + 2) == key) { return p + 3; }",
        "        p += 5; remaining--;",
        "    }",
        "    return 0;",
        "}",
        "fn object_variant_has(u8 type, u8 variant, u8 key) -> u8 {",
        "    return object_variant_find(type, variant, key) != 0;",
        "}",
        "fn object_variant_value(u8 type, u8 variant, u8 key) -> u16 {",
        "    u16 p = object_variant_find(type, variant, key);",
        "    if (p == 0) { return 0; }",
        "    return peek8(p) | (peek8(p + 1) << 8);",
        "}",
    ]
    if any(v.get("fields") for obj in data["objects"] for v in obj.get("variants", [])):
        lines.append("fn object_apply_variant(u16 slot, u8 type, u8 variant) {")
        for obj in data["objects"]:
            obj_id = ids.get(obj["class"].upper())
            if obj_id is None:
                continue
            for variant in obj.get("variants", []):
                if not variant.get("fields"):
                    continue
                lines.append(f"    if (type == {obj_id} && variant == {variant['id']}) {{")
                for field, value in variant["fields"].items():
                    literal = format(value, ".12f").rstrip("0").rstrip(".") if isinstance(value, float) else str(value)
                    if field_types and field_types.get(field) == "f16" and "." not in literal:
                        literal += ".0"
                    lines.append(f"        Entities[slot].{field} = {literal};")
                lines.append("    }")
        lines.append("}")
    else:
        lines.append("fn object_apply_variant(u16 slot, u8 type, u8 variant) { }")
    lines.append("")
    return "\n".join(lines)


def generate_asm(data, assets_header):
    _, entries = variant_records(data, assets_header)
    lines = ["_gel_object_variant_values:"]
    for obj_id, variant_id, key_id, raw in entries:
        lines.append(f"    db {obj_id}, {variant_id}, {key_id}, {raw & 255}, {raw >> 8}")
    if len(lines) == 1:
        lines.append("    db 0")
    return "\n".join(lines) + "\n"
