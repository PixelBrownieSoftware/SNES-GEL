"""Discover and patch project-file declarations without rewriting surrounding source."""

import hashlib
import difflib
import json
import re
from pathlib import Path

from lark import UnexpectedInput


IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
TYPES = r"(?:u8|u16|i8|i16|f16|f32)"
RESERVED = {"u8", "i8", "u16", "i16", "f16", "f32", "if", "else", "switch",
            "case", "default", "break", "return", "while", "for_each", "fn", "enum",
            "struct", "struct_table", "asm", "static", "dp", "mmio", "persist",
            "extern", "interleaved", "create", "free"}


def declarations(project, source):
    masked = project.mask_comments(source)
    entries = []
    directives = list(re.finditer(rf"(?m)^[ \t]*#define[ \t]+({IDENT})\b[^\r\n]*", masked))
    for match in directives:
        start = masked.find("#", match.start(), match.end())
        entries.append({"kind": "define", "name": match.group(1), "start": start,
                        "end": match.end(), "code": source[start:match.end()]})
    masked = re.sub(r"(?m)^[ \t]*#[^\r\n]*", lambda m: " " * len(m.group()), masked)
    scan = re.sub(r'"[^"\r\n]*"|\'[^\'\r\n]*\'', lambda m: " " * len(m.group()), masked)
    tokens = list(re.finditer(rf"{IDENT}|[{{}};]", scan))
    depth = 0
    i = 0
    while i < len(tokens):
        token = tokens[i]
        word = token.group()
        if depth == 0 and word == "struct_table":
            header = re.match(rf"struct_table\s+({IDENT})\s*\[[^\]]+\][^{{;]*\{{", scan[token.start():])
            if header:
                opening = token.start() + header.end() - 1
                closing = scan.find("}", opening + 1)
                if closing >= 0 and "{" not in scan[opening + 1:closing]:
                    end = closing + 1
                    suffix = re.match(r"[ \t]*;", scan[end:])
                    if suffix:
                        end += suffix.end()
                    entries.append({"kind": "table", "name": header.group(1), "start": token.start(),
                                    "end": end, "code": source[token.start():end]})
                    while i < len(tokens) and tokens[i].start() < end:
                        i += 1
                    continue
        if depth == 0 and (word in ("dp", "mmio", "persist") or re.fullmatch(TYPES, word)):
            match = re.match(rf"(?:(?:dp|mmio|persist)\s+)?{TYPES}\s+({IDENT})\s*(?=[=@;])",
                             scan[token.start():])
            if match:
                end = scan.find(";", token.start() + match.end())
                if end >= 0 and not re.search(r"[{}]", scan[token.start():end]):
                    end += 1
                    entries.append({"kind": "global", "name": match.group(1), "start": token.start(),
                                    "end": end, "code": source[token.start():end]})
                    while i < len(tokens) and tokens[i].start() < end:
                        i += 1
                    continue
        if word == "{":
            depth += 1
        elif word == "}":
            depth = max(0, depth - 1)
        i += 1
    return sorted(entries, key=lambda item: item["start"])


def validate(project, kind, code):
    if not isinstance(code, str) or not code.strip():
        raise ValueError("Enter a declaration.")
    code = code.strip()
    masked = project.mask_comments(code)
    if kind == "define":
        if "\n" in code or "\r" in code:
            raise ValueError("A define must be a single line.")
        match = re.fullmatch(rf"#define[ \t]+({IDENT})[ \t]+(\S.*)", masked)
        if not match:
            raise ValueError("Use #define NAME followed by a value.")
        if match.group(1) in RESERVED:
            raise ValueError("Define name is a reserved GEL keyword.")
        return code, match.group(1)
    if kind not in ("global", "table"):
        raise ValueError("Unknown declaration kind.")
    # Macro-expanded numeric positions are legal after preprocessing.
    parseable = re.sub(rf"(?<=\[)\s*{IDENT}\s*(?=\])", "1", masked)
    parseable = re.sub(rf"(?<=@)\s*{IDENT}\b", " 1", parseable)
    try:
        tree = project.parser().parse(parseable)
    except UnexpectedInput as error:
        raise ValueError(f"Invalid GEL declaration at line {error.line}, column {error.column}.") from error
    expected = "global_decl" if kind == "global" else "struct_table_decl"
    if len(tree.children) != 1 or tree.children[0].data != expected:
        raise ValueError("Enter exactly one declaration of the selected kind.")
    node = tree.children[0]
    name = next(str(child) for child in node.children
                if getattr(child, "type", None) == "IDENTIFIER")
    if name in RESERVED:
        raise ValueError("Declaration name is a reserved GEL keyword.")
    if kind == "table":
        fields = _fields(project, code)
        if len(set(fields)) != len(fields):
            raise ValueError("Table field names must be unique.")
        if any(field in RESERVED for field in fields):
            raise ValueError("Table field names cannot be reserved GEL keywords.")
    return code, name


def apply(project, folder, payload, symbols):
    if not isinstance(payload, dict):
        raise ValueError("Expected a declaration edit.")
    path = project.file_path(folder, payload.get("name"))
    if path.name == "contact.gel":
        raise ValueError("contact.gel is generated by the contact editor; edit it there.")
    source = project.read_source(path)
    if project.text_hash(source) != payload.get("hash"):
        raise ValueError("File changed since it was opened. Reload it before editing declarations.")
    kind = payload.get("kind")
    code, name = validate(project, kind, payload.get("code"))
    index = payload.get("index")
    entries = declarations(project, source)
    old = None
    if index is not None:
        if type(index) is not int or index < 0 or index >= len(entries) or entries[index]["kind"] != kind:
            raise ValueError("Declaration is unavailable; reload the file.")
        old = entries[index]
    if not old or old["name"] != name:
        if any(name in symbols[key] for key in ("functions", "globals", "tables", "enums", "defines", "hardware", "bit")) or any(
                name in values for values in symbols["enums"].values()):
            raise ValueError(f"Symbol '{name}' already exists.")
        for candidate in sorted(path.parent.glob("*.gel")):
            if candidate.is_symlink() or candidate.resolve().parent != path.parent:
                raise ValueError("Project GEL files must not be symbolic links.")
            if any(entry["name"] == name for entry in declarations(project, project.read_source(candidate))):
                raise ValueError(f"Symbol '{name}' already exists in {candidate.name}.")
    newline = "\r\n" if "\r\n" in source else "\n"
    code = code.replace("\r\n", "\n").replace("\r", "\n").replace("\n", newline)
    if old:
        return path, source[:old["start"]] + code + source[old["end"]:]
    separator = "" if not source else newline if source.endswith(("\n", "\r")) else newline * 2
    return path, source + separator + code + newline


def _fields(project, code):
    masked = project.mask_comments(code)
    body = masked[masked.find("{") + 1:masked.rfind("}")]
    return [match.group(1) for match in re.finditer(rf"\b{TYPES}\s+({IDENT})\s*;", body)]


def prepare_rename(project, folder, payload, symbols):
    """Preview a conservative rename across project-root GEL files."""
    path, updated = apply(project, folder, payload, symbols)
    original = {}
    for candidate in sorted(Path(folder).glob("*.gel")):
        if candidate.is_symlink() or candidate.resolve().parent != Path(folder).resolve():
            raise ValueError("Project GEL files must not be symbolic links.")
        original[candidate.name] = project.read_source(candidate)
    entries = declarations(project, original[path.name])
    index = payload.get("index")
    if type(index) is not int or index < 0 or index >= len(entries):
        raise ValueError("Select an existing declaration to rename.")
    old = entries[index]
    _, new_name = validate(project, old["kind"], payload.get("code"))
    old_name = old["name"]
    field_pairs = payload.get("fieldRenames", [])
    if not isinstance(field_pairs, list):
        raise ValueError("Invalid field renames.")
    pairs = {}
    old_fields = _fields(project, old["code"]) if old["kind"] == "table" else []
    new_fields = _fields(project, payload["code"]) if old["kind"] == "table" else []
    for pair in field_pairs:
        if not isinstance(pair, dict) or type(pair.get("origin")) is not int:
            raise ValueError("Invalid field rename.")
        origin, name = pair["origin"], pair.get("name")
        if origin < 0 or origin >= len(old_fields) or not isinstance(name, str) or name not in new_fields:
            raise ValueError("Invalid field rename.")
        previous = old_fields[origin]
        if previous in pairs or (name in old_fields and name != previous) or (name != previous and previous in new_fields):
            raise ValueError("Duplicate or ambiguous field rename.")
        if name != previous:
            pairs[previous] = name
    if len(set(new_fields)) != len(new_fields):
        raise ValueError("Table field names must be unique.")
    if old_name == new_name and not pairs:
        raise ValueError("No names have changed.")
    if sum(entry["name"] == old_name for source in original.values()
           for entry in declarations(project, source)) != 1:
        raise ValueError("The declaration has multiple definitions; rename it manually.")
    sensitive = {old_name, new_name, *pairs, *pairs.values()}
    config = Path(folder) / "contacts.json"
    if config.is_file():
        config_text = config.read_text(encoding="utf-8")
        if any(re.search(rf"\b{re.escape(name)}\b", config_text) for name in sensitive):
            raise ValueError("Contact configuration refers to a changed name; update it in the contact editor first.")
    sources = dict(original)
    sources[path.name] = updated
    counts = {}
    for filename, source in list(sources.items()):
        masked = project.mask_comments(source)
        if old["kind"] == "define" and old_name != new_name:
            for literal in re.finditer(r'"[^"\r\n]*"|\'[^\'\r\n]*\'', masked):
                if any(re.search(rf"\b{re.escape(name)}\b", literal.group()) for name in (old_name, new_name)):
                    raise ValueError(f"{filename} contains a changed define name in a string; edit it manually.")
        masked = re.sub(r'"[^"\r\n]*"|\'[^\'\r\n]*\'', lambda m: " " * len(m.group()), masked)
        if any(re.search(rf"\b{re.escape(name)}\b", asm.group())
               for asm in re.finditer(r"\basm\s*\{[^}]*\}", masked, re.S) for name in sensitive):
            raise ValueError(f"{filename} contains assembly mentioning a changed name; edit it manually.")
        current_entries = declarations(project, source)
        edited = current_entries[index] if filename == path.name else None
        excluded = (edited["start"], edited["end"]) if edited else (-1, -1)
        if old_name != new_name:
            for match in re.finditer(rf"\b{re.escape(new_name)}\b", masked):
                if not excluded[0] <= match.start() < excluded[1]:
                    raise ValueError(f"{filename} already contains '{new_name}'; rename it manually to avoid changing its meaning.")
        for fn in project.functions(source):
            fn_text = masked[fn["start"]:fn["end"]]
            if fn["error"] and any(re.search(rf"\b{re.escape(name)}\b", fn_text) for name in sensitive):
                raise ValueError(f"Cannot safely scan {filename}: function {fn['name']} cannot be parsed.")
            if old["kind"] != "define" and old_name != new_name and (
                    old_name in fn["locals"] or new_name in fn["locals"]):
                raise ValueError(f"{filename}: a local or parameter shadows a changed name.")
        tokens = list(re.finditer(rf"{IDENT}|[^\s]", masked))
        replacements = []
        qualified_fields = set()
        if pairs:
            for i, token in enumerate(tokens):
                if (not re.fullmatch(IDENT, token.group()) or i + 1 >= len(tokens)
                        or tokens[i + 1].group() != "["):
                    continue
                level, j = 1, i + 2
                while j < len(tokens) and level:
                    level += (tokens[j].group() == "[") - (tokens[j].group() == "]")
                    j += 1
                if level or j + 1 >= len(tokens) or tokens[j].group() != ".":
                    continue
                field = tokens[j + 1]
                if field.group() in pairs:
                    if (token.group() not in (old_name, new_name) and
                            (token.group() not in symbols["tables"] or token.group() in symbols["defines"])):
                        raise ValueError(f"{filename} has an ambiguous table reference through {token.group()}; edit it manually.")
                    qualified_fields.add(field.start())
                    if token.group() in (old_name, new_name):
                        replacements.append((field.start(), field.end(), pairs[field.group()]))
            for i, token in enumerate(tokens):
                if (token.group() in pairs and i and tokens[i - 1].group() == "."
                        and token.start() not in qualified_fields
                        and not excluded[0] <= token.start() < excluded[1]):
                    raise ValueError(f"{filename} has an ambiguous .{token.group()} reference; edit it manually.")
                line_start = masked.rfind("\n", 0, token.start()) + 1
                if (token.group() in pairs and token.start() not in qualified_fields
                        and not excluded[0] <= token.start() < excluded[1]
                        and re.match(r"[ \t]*#define\b", masked[line_start:token.start()])):
                    raise ValueError(f"{filename} has a macro mentioning field {token.group()}; edit it manually.")
        if old_name != new_name:
            for i, token in enumerate(tokens):
                if token.group() != old_name or excluded[0] <= token.start() < excluded[1]:
                    continue
                if old["kind"] != "define" and i and tokens[i - 1].group() == ".":
                    raise ValueError(f"{filename} contains an ambiguous .{old_name}; edit it manually.")
                replacements.append((token.start(), token.end(), new_name))
        if replacements and filename == "contact.gel":
            raise ValueError("Generated contact.gel refers to a changed name; update the contact editor first.")
        counts[filename] = len(replacements)
        for start, end, value in sorted(replacements, reverse=True):
            source = source[:start] + value + source[end:]
        sources[filename] = source
    changes = {name: text for name, text in sources.items() if text != original[name]}
    hashes = {name: project.text_hash(text) for name, text in original.items()}
    token = hashlib.sha256(json.dumps({"hashes": hashes, "changes": changes}, sort_keys=True).encode()).hexdigest()
    return {"changes": changes, "hashes": hashes, "token": token, "counts": counts}


def prepare_impact(project, folder, payload, symbols):
    """Prepare a confirmed rename or type change, including possible affected uses."""
    path, updated = apply(project, folder, payload, symbols)
    source = project.read_source(path)
    entries = declarations(project, source)
    index = payload.get("index")
    if type(index) is not int or index < 0 or index >= len(entries):
        raise ValueError("Select an existing declaration.")
    old = entries[index]
    code = payload["code"]
    _, new_name = validate(project, old["kind"], code)
    renames = payload.get("fieldRenames", [])
    if not isinstance(renames, list):
        raise ValueError("Invalid field renames.")
    type_changes = []
    if old["kind"] == "global":
        pattern = rf"\b({TYPES})\s+{IDENT}\s*(?=[=@;])"
        before = re.search(pattern, project.mask_comments(old["code"]))
        after = re.search(pattern, project.mask_comments(code))
        if not before or not after:
            raise ValueError("Cannot determine the global type.")
        if before.group(1) != after.group(1):
            type_changes.append({"name": old["name"], "before": before.group(1), "after": after.group(1)})
    elif old["kind"] == "table":
        def typed_fields(text):
            masked = project.mask_comments(text)
            body = masked[masked.find("{") + 1:masked.rfind("}")]
            return [(m.group(2), m.group(1)) for m in re.finditer(rf"\b({TYPES})\s+({IDENT})\s*;", body)]
        old_fields, new_fields = typed_fields(old["code"]), dict(typed_fields(code))
        origins = payload.get("fieldOrigins", [])
        if not isinstance(origins, list):
            raise ValueError("Invalid original field list.")
        seen = set()
        for field in origins:
            if not isinstance(field, dict) or type(field.get("origin")) is not int or not isinstance(field.get("name"), str):
                raise ValueError("Invalid original field.")
            origin, name = field["origin"], field["name"]
            if origin < 0 or origin >= len(old_fields) or origin in seen or name not in new_fields:
                raise ValueError("Invalid original field.")
            seen.add(origin)
            previous, old_type = old_fields[origin]
            if old_type != new_fields[name]:
                type_changes.append({"name": f"{old['name']}.{previous}", "before": old_type, "after": new_fields[name]})
    if old["name"] != new_name or renames:
        result = prepare_rename(project, folder, payload, symbols)
    elif type_changes:
        paths = sorted(Path(folder).glob("*.gel"))
        if any(p.is_symlink() or p.resolve().parent != Path(folder).resolve() for p in paths):
            raise ValueError("Project GEL files must not be symbolic links.")
        originals = {p.name: project.read_source(p) for p in paths}
        result = {"changes": {path.name: updated} if updated != source else {},
                  "hashes": {name: project.text_hash(text) for name, text in originals.items()}, "counts": {}}
    else:
        raise ValueError("No name or type changes to preview.")
    originals = {name: project.read_source(Path(folder) / name) for name in result["hashes"]}
    diffs = {}
    for name, changed in result["changes"].items():
        diffs[name] = list(difflib.unified_diff(
            originals[name].splitlines(), changed.splitlines(), fromfile=name, tofile=name, lineterm=""))
    terms = {old["name"]}
    terms.update(item["name"].split(".")[-1] for item in type_changes)
    terms.update(pair["name"] for pair in renames)
    terms.update(_fields(project, old["code"]) if renames else [])
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(term) for term in sorted(terms)) + r")\b")
    references = []
    for filename, text in originals.items():
        masked = project.mask_comments(text)
        masked = re.sub(r'"[^"\r\n]*"|\'[^\'\r\n]*\'', lambda m: " " * len(m.group()), masked)
        for lineno, (line, visible) in enumerate(zip(text.splitlines(), masked.splitlines()), 1):
            if pattern.search(visible):
                references.append({"file": filename, "line": lineno, "text": line.strip()})
    config = Path(folder) / "contacts.json"
    config_text = config.read_text(encoding="utf-8") if config.is_file() else None
    if config_text is not None:
        for lineno, line in enumerate(config_text.splitlines(), 1):
            if pattern.search(line):
                references.append({"file": "contacts.json", "line": lineno, "text": line.strip()})
    digest = {"hashes": result["hashes"], "changes": result["changes"], "config": config_text,
              "type_changes": type_changes, "references": references}
    result.update({"token": hashlib.sha256(json.dumps(digest, sort_keys=True).encode()).hexdigest(),
                   "diffs": diffs, "references": references, "type_changes": type_changes})
    return result
