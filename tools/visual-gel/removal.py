"""Preview conservative removal of project variables and their references."""

import difflib
import hashlib
import json
import re
from pathlib import Path

from lark import Tree
from lark.exceptions import UnexpectedInput


def prepare(project, declarations, usages, folder, payload):
    if not isinstance(payload, dict) or payload.get("kind") not in ("global", "define", "field", "local"):
        raise ValueError("Invalid removal request.")
    kind, name = payload["kind"], payload.get("symbol")
    if not isinstance(name, str) or not usages.IDENTIFIER.fullmatch(name):
        raise ValueError("Invalid variable name.")
    root = Path(folder).resolve()
    path = project.file_path(root, payload.get("name"))
    if path.name == "contact.gel":
        raise ValueError("Generated contact.gel cannot be edited here.")
    paths = sorted(root.glob("*.gel"))
    if any(p.is_symlink() or p.resolve().parent != root for p in paths):
        raise ValueError("Project GEL files must not be symbolic links.")
    originals = {p.name: project.read_source(p) for p in paths}
    known_tables = {entry["name"] for text in originals.values()
                    for entry in declarations.declarations(project, text) if entry["kind"] == "table"}
    define_names = {entry["name"] for text in originals.values()
                    for entry in declarations.declarations(project, text) if entry["kind"] == "define"}
    source = originals[path.name]
    if project.text_hash(source) != payload.get("hash"):
        raise ValueError("File changed since it was opened. Reload before removing a variable.")
    declaration = None
    table = payload.get("table")
    function_index = payload.get("function")
    if kind == "local":
        local_functions = project.functions(source)
        if type(function_index) is not int or not 0 <= function_index < len(local_functions):
            raise ValueError("Function is unavailable.")
    if kind != "local":
        entries = declarations.declarations(project, source)
        index = payload.get("index")
        if type(index) is not int or index < 0 or index >= len(entries):
            raise ValueError("Declaration is unavailable; reload the file.")
        declaration = entries[index]
        expected = "table" if kind == "field" else kind
        if declaration["kind"] != expected or (kind != "field" and declaration["name"] != name):
            raise ValueError("Declaration changed; reload the file.")
        if kind == "field":
            if table != declaration["name"]:
                raise ValueError("Table changed; reload the file.")
            if declarations._fields(project, declaration["code"]).count(name) != 1:
                raise ValueError("Field is unavailable or ambiguous.")
            if len(declarations._fields(project, declaration["code"])) == 1:
                raise ValueError("A struct table must retain at least one field.")
        elif sum(e["name"] == name for text in originals.values()
                 for e in declarations.declarations(project, text)) != 1:
            raise ValueError("The declaration has multiple definitions; remove it manually.")
    config = root / "contacts.json"
    if config.is_file() and re.search(rf"\b{re.escape(name)}\b", config.read_text(encoding="utf-8")):
        raise ValueError("Contact configuration mentions this name; review it in the contact editor first.")

    changes = {}
    for filename, text in originals.items():
        masked = usages._mask(project, text)
        hits = list(re.finditer(rf"\b{re.escape(name)}\b", masked))
        if kind == "define":
            visible = project.mask_comments(text)
            for literal in re.finditer(r'"[^"\r\n]*"|\'[^\'\r\n]*\'', visible):
                if re.search(rf"\b{re.escape(name)}\b", literal.group()):
                    raise ValueError(f"{filename} contains the define in a string; review it manually.")
        if not hits and filename != path.name:
            continue
        if filename == "contact.gel" and hits and kind != "local":
            raise ValueError("Generated contact.gel mentions this name; update the contact editor first.")
        functions = project.functions(text)
        edits = []
        excluded = None
        if filename == path.name and declaration:
            if kind == "field":
                span = re.search(rf"\b{declarations.TYPES}\s+{re.escape(name)}\s*;", masked[declaration["start"]:declaration["end"]])
                if not span:
                    raise ValueError("Cannot locate the table field.")
                excluded = (declaration["start"] + span.start(), declaration["start"] + span.end())
            else:
                excluded = (declaration["start"], declaration["end"])
            edits.append((*excluded, ""))
        for entry in declarations.declarations(project, text):
            if entry["kind"] != "global" or (filename == path.name and entry == declaration):
                continue
            if not any(entry["start"] <= hit.start() < entry["end"] for hit in hits):
                continue
            try:
                tree = project.parser().parse(project.mask_comments(entry["code"]))
            except UnexpectedInput as error:
                raise ValueError(f"{filename}: cannot parse a global initializer; edit it manually.") from error
            for node in tree.iter_subtrees_topdown():
                if ((kind in ("global", "define") and node.data == "var_ref" and str(node.children[0]) == name)
                        or (kind == "field" and node.data == "field_ref" and str(node.children[0]) == table
                            and str(node.children[-1]) == name)):
                    edits.append((entry["start"] + node.meta.start_pos, entry["start"] + node.meta.end_pos, "0"))
        for index, fn in enumerate(functions):
            if kind == "local" and (filename != path.name or index != function_index):
                continue
            relevant = [m for m in hits if fn["start"] <= m.start() < fn["end"]]
            if not relevant:
                continue
            if fn["error"]:
                raise ValueError(f"{filename}: function {fn['name']} cannot be parsed; review it manually.")
            if kind == "global" and name in fn["locals"]:
                raise ValueError(f"{filename}: function {fn['name']} shadows {name}; review it manually.")
            fragment = text[fn["start"]:fn["end"]]
            tree = project.parser().parse(project.mask_comments(fragment))
            nodes = list(tree.iter_subtrees_topdown())
            if kind == "local":
                declared = [node for node in nodes if node.data == "var_decl_stmt" and str(node.children[1]) == name]
                collisions = [node for node in nodes if node.data in ("param", "table_iter_stmt") and
                              str(node.children[1]) == name]
                if len(declared) != 1 or collisions:
                    raise ValueError("Only uniquely declared local variables can be removed; parameters, iterators and shadowed names need manual changes.")
                node = declared[0]
                edits.append((fn["start"] + node.meta.start_pos, fn["start"] + node.meta.end_pos, ""))
            def matches(node):
                if kind == "field":
                    return node.data == "field_ref" and str(node.children[0]) == table and str(node.children[-1]) == name
                return node.data == "var_ref" and str(node.children[0]) == name
            write_ranges = []
            for node in nodes:
                target = None
                if node.data == "assign_stmt":
                    target = node.children[0]
                elif node.data == "expr_stmt" and isinstance(node.children[0], Tree) and node.children[0].data in ("pre_inc", "post_inc", "pre_dec", "post_dec"):
                    target = node.children[0].children[0]
                if isinstance(target, Tree) and matches(target):
                    start, end = fn["start"] + node.meta.start_pos, fn["start"] + node.meta.end_pos
                    if project.mask_comments(text[start:end]) != text[start:end]:
                        raise ValueError(f"{filename}: a write contains comments; remove it manually to preserve them.")
                    write_ranges.append((start, end))
            edits.extend((start, end, "") for start, end in write_ranges)
            for node in nodes:
                if not matches(node):
                    continue
                start, end = fn["start"] + node.meta.start_pos, fn["start"] + node.meta.end_pos
                if any(a <= start < b for a, b in write_ranges):
                    continue
                if any(a <= start < b and value == "" for a, b, value in edits):
                    continue
                if any(parent.data in ("pre_inc", "post_inc", "pre_dec", "post_dec") and
                       parent.meta.start_pos <= node.meta.start_pos < parent.meta.end_pos for parent in nodes):
                    raise ValueError(f"{filename}: embedded increment or decrement requires manual editing.")
                edits.append((start, end, "0"))
        if kind == "local" and filename == path.name:
            if not edits:
                raise ValueError("Local variable is unavailable.")
        # Refuse unmatched names: macro aliases, assembly and non-expression contexts need review.
        field_positions = {match.start() for match in usages._table_fields(masked, table, name)} if kind == "field" else set()
        other_field_positions = ({match.start() for other in known_tables - {table} - define_names
                                  for match in usages._table_fields(masked, other, name)}
                                 if kind == "field" else set())
        for hit in hits:
            if kind == "local" and (filename != path.name or not functions[function_index]["start"] <= hit.start() < functions[function_index]["end"]):
                continue
            if kind == "field" and hit.start() not in field_positions:
                if excluded and excluded[0] <= hit.start() < excluded[1]:
                    continue
                if hit.start() in other_field_positions:
                    continue
                prefix = masked[:hit.start()].rstrip()
                if prefix.endswith("."):
                    raise ValueError(f"{filename}: an ambiguous .{name} reference needs manual review.")
                continue
            if any(start <= hit.start() < end for start, end, _ in edits):
                continue
            raise ValueError(f"{filename}:{text.count(chr(10), 0, hit.start()) + 1}: cannot safely replace this reference; edit it manually.")
        if edits:
            result = text
            last = len(text) + 1
            for start, end, value in sorted(edits, reverse=True):
                if end > last:
                    if start < last:
                        raise ValueError(f"{filename}: overlapping references require manual editing.")
                    continue
                result = result[:start] + value + result[end:]
                last = start
            changes[filename] = result
    for candidate in root.rglob("*.gel"):
        if candidate.parent == root or candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
            continue
        text = usages._mask(project, project.read_source(candidate))
        if re.search(rf"\b{re.escape(name)}\b", text) and kind != "local":
            raise ValueError(f"{candidate.relative_to(root)} mentions this name; edit it manually first.")
    hashes = {name: project.text_hash(text) for name, text in originals.items()}
    token = hashlib.sha256(json.dumps({"hashes": hashes, "changes": changes}, sort_keys=True).encode()).hexdigest()
    diffs = {name: list(difflib.unified_diff(originals[name].splitlines(), text.splitlines(),
              fromfile=name, tofile=name, lineterm="")) for name, text in changes.items()}
    return {"changes": changes, "hashes": hashes, "token": token, "diffs": diffs}
