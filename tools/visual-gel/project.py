"""Read and safely patch supported statements in project GEL functions."""

import hashlib
import re
from pathlib import Path

from lark import Tree, UnexpectedInput

from gel_parser import load_parser
from functools import lru_cache


@lru_cache(maxsize=1)
def parser():
    return load_parser()


def mask_comments(source):
    pattern = r'"[^"\r\n]*"|\'[^\'\r\n]*\'|//[^\r\n]*|/\*[\s\S]*?\*/'
    return re.sub(pattern, lambda m: m.group() if m.group()[0] in "\"'" else
                  re.sub(r"[^\r\n]", " ", m.group()), source)


def file_path(project, name):
    if (not isinstance(name, str) or not name.endswith(".gel") or name in (".", "..")
            or "/" in name or "\\" in name or Path(name).name != name):
        raise ValueError("Invalid GEL filename.")
    path = (Path(project) / name).resolve()
    if path.parent != Path(project).resolve() or not path.is_file():
        raise ValueError("GEL file not found in the project folder.")
    return path


def read_source(path):
    return path.read_bytes().decode("utf-8")


def text_hash(source):
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def statement(source, node):
    def text(tree):
        return source[tree.meta.start_pos:tree.meta.end_pos]

    raw = text(node)
    if mask_comments(raw) != raw:
        return None
    children = node.children
    if node.data == "assign_stmt":
        op = str(children[1].children[0])
        result = {"op": {"=": "set", "+=": "add", "-=": "sub"}.get(op, "assign"),
                  "target": text(children[0]), "value": text(children[2])}
        if result["op"] == "assign":
            result["operator"] = op
        return result
    if node.data == "return_stmt":
        return {"op": "return", "value": text(children[0]) if children else ""}
    if node.data == "var_decl_stmt":
        return {"op": "declare", "type": str(children[0]), "name": str(children[1]),
                "value": text(children[2]) if len(children) > 2 else ""}
    if node.data == "expr_stmt" and isinstance(children[0], Tree):
        expr = children[0]
        if expr.data in ("pre_inc", "post_inc", "pre_dec", "post_dec"):
            return {"op": "increment" if expr.data.endswith("inc") else "decrement",
                    "target": text(expr.children[0]), "prefix": expr.data.startswith("pre_")}
        if expr.data == "table_free":
            return {"op": "free", "target": text(expr).rsplit(".free", 1)[0]}
        if expr.data == "table_create":
            return {"op": "create", "table": str(expr.children[0]), "var": ""}
        if expr.data == "func_call":
            args = text(expr.children[1]) if len(expr.children) > 1 else ""
            return {"op": "call", "fn": str(expr.children[0]), "args": args}
        return {"op": "expr", "value": text(expr)}
    return None


def display_tree(source, parsed, body, parts, found=None):
    """Build a visual layout while leaving source patch ranges unchanged."""
    found = found if found is not None else []
    indexes = {(part["start"], part["end"], bool(part.get("insertion"))): i
               for i, part in enumerate(parts)}
    comment_pattern = re.compile(r'"[^"\r\n]*"|\'[^\'\r\n]*\'|//[^\r\n]*|/\*[\s\S]*?\*/')

    def raw(text):
        return [{"kind": "raw", "text": text}] if text.strip() else []

    def comment_item(start, end):
        for number, known in enumerate(found):
            if known["start"] == start:
                return {"kind": "comment", "index": number}
        found.append({"start": start, "end": end, "text": source[start:end]})
        return {"kind": "comment", "index": len(found) - 1}

    def comments(start, end):
        return [comment_item(match.start(), match.end())
                for match in comment_pattern.finditer(source, start, end)
                if match.group().startswith(("//", "/*"))]

    def gap(start, end):
        result, cursor = [], start
        for match in comment_pattern.finditer(source, start, end):
            if not match.group().startswith(("//", "/*")):
                continue
            result += raw(source[cursor:match.start()])
            result.append(comment_item(match.start(), match.end()))
            cursor = match.end()
        return result + raw(source[cursor:end])

    def item(start, end, label):
        index = indexes.get((start, end, False))
        if index is None:
            return [{"kind": "label", "text": label}] + raw(source[start:end])
        return [{"kind": "part", "index": index}]

    def insertion(pos):
        index = indexes.get((pos, pos, True))
        return [{"kind": "part", "index": index}] if index is not None else []

    def sequence(nodes, start, end):
        result = []
        cursor = start
        for node in nodes:
            result += gap(cursor, node.meta.start_pos)
            result += visual_node(node)
            cursor = node.meta.end_pos
        return result + gap(cursor, end)

    def block_content(node):
        start, end = node.meta.start_pos + 1, node.meta.end_pos - 1
        return sequence(node.children, start, end) + insertion(end)

    def conditional_branch(node, label):
        condition, child_block = node.children[:2]
        a, b = condition.meta.start_pos, condition.meta.end_pos
        content = (comments(node.meta.start_pos, a) + item(a, b, label) +
                   comments(b, child_block.meta.start_pos))
        return {"kind": "branch", "items": content, "body": block_content(child_block),
                "block": child_block.meta.start_pos}

    def case_node(node):
        default = node.data == "default_clause"
        start, end = node.meta.start_pos, node.meta.end_pos
        if default:
            colon = parsed.find(":", start, end)
            header = comments(start, colon) + [{"kind": "label", "text": "default"}]
            children = node.children
        else:
            value = node.children[0]
            colon = parsed.find(":", value.end_pos, end)
            header = (comments(start, value.start_pos) +
                      item(value.start_pos, value.end_pos, "case value") +
                      comments(value.end_pos, colon))
            children = node.children[1:]
        ending = re.search(r"\bbreak\s*;\s*$", parsed[start:end])
        insert_pos = start + ending.start() if ending else end
        content = (comments(colon + 1, children[0].meta.start_pos) if children else [])
        if children:
            content += sequence(children, children[0].meta.start_pos, insert_pos)
        else:
            content += gap(colon + 1, insert_pos) if not content else []
        content += insertion(insert_pos)
        content += comments(insert_pos, end)
        return {"kind": "branch", "items": header, "body": content, "block": start}

    def visual_node(node):
        start, end = node.meta.start_pos, node.meta.end_pos
        kind = node.data
        if kind == "if_stmt":
            first = conditional_branch(node, "if condition")
            branches = [first]
            cursor = node.children[1].meta.end_pos
            for clause in node.children[2:]:
                first_comment = comments(cursor, clause.meta.start_pos)
                if clause.data == "else_if_clause":
                    branch = conditional_branch(clause, "else if condition")
                else:
                    child_block = clause.children[0]
                    branch = {"kind": "branch",
                              "items": comments(clause.meta.start_pos, child_block.meta.start_pos) +
                                       [{"kind": "label", "text": "else"}],
                              "body": block_content(child_block),
                              "block": child_block.meta.start_pos}
                branch["items"] = first_comment + branch["items"]
                branches.append(branch)
                cursor = clause.meta.end_pos
            return [{"kind": "control", "style": "if", "branches": branches, "unit": start}]
        if kind in ("while_stmt", "table_iter_stmt"):
            child_block = node.children[-1]
            if kind == "while_stmt":
                condition = node.children[0]
                a, b = condition.meta.start_pos, condition.meta.end_pos
                header = comments(start, a) + item(a, b, "while condition") + comments(b, child_block.meta.start_pos)
            else:
                table, iterator = node.children[:2]
                a = table.start_pos
                b = parsed.find(")", iterator.end_pos, child_block.meta.start_pos) + 1
                header = comments(start, a) + item(a, b, "for each") + comments(b, child_block.meta.start_pos)
            return [{"kind": "control", "style": "while" if kind == "while_stmt" else "for_each", "unit": start,
                     "branches": [{"kind": "branch", "items": header, "body": block_content(child_block),
                                   "block": child_block.meta.start_pos}]}]
        if kind == "switch_stmt":
            condition = node.children[0]
            a, b = condition.meta.start_pos, condition.meta.end_pos
            cases = node.children[1:]
            brace = parsed.find("{", b, end)
            header = comments(start, a) + item(a, b, "switch value") + comments(b, brace + 1)
            cursor = brace + 1
            branches = []
            for clause in cases:
                branch = case_node(clause)
                branch["items"] = comments(cursor, clause.meta.start_pos) + branch["items"]
                branches.append(branch)
                cursor = clause.meta.end_pos
            trailing = comments(cursor, end)
            if trailing:
                branches.append({"kind": "branch", "items": trailing, "body": []})
            return [{"kind": "control", "style": "switch", "unit": start,
                     "branches": [{"kind": "branch", "items": header, "body": [
                         {"kind": "control", "style": "cases", "branches": branches}]}]}]
        index = indexes.get((start, end, False))
        if index is not None:
            return [{"kind": "part", "index": index, "unit": start}]
        return [{**item, "unit": start} for item in raw(source[start:end])]

    return block_content(body)


def functions(source):
    masked = mask_comments(source)
    # Keep offsets intact; directives cannot be parsed as GEL declarations.
    masked = re.sub(r"(?m)^[ \t]*#[^\r\n]*", lambda m: " " * len(m.group()), masked)
    scan = re.sub(r'"[^"\r\n]*"|\'[^\'\r\n]*\'',
                  lambda m: " " * len(m.group()), masked)
    tokens = list(re.finditer(r"[A-Za-z_][A-Za-z0-9_]*|[{}]", scan))
    result = []
    depth = 0
    i = 0
    while i < len(tokens):
        token = tokens[i].group()
        if depth == 0 and token == "fn" and i + 1 < len(tokens):
            name = tokens[i + 1].group()
            j = i + 2
            while j < len(tokens) and tokens[j].group() != "{":
                j += 1
            if j == len(tokens):
                break
            k, balance = j + 1, 1
            while k < len(tokens) and balance:
                if tokens[k].group() == "{":
                    balance += 1
                elif tokens[k].group() == "}":
                    balance -= 1
                k += 1
            if balance:
                break
            start, end = tokens[i].start(), tokens[k - 1].end()
            fragment = source[start:end]
            parsed = masked[start:end]
            entry = {"name": name, "start": start, "end": end, "source": fragment,
                     "statements": [], "locals": [], "error": None}
            try:
                tree = parser().parse(parsed)
                fn = tree.children[0]
                block = next(child for child in fn.children if isinstance(child, Tree) and child.data == "block")
                params = next((child for child in fn.children if isinstance(child, Tree) and child.data == "param_list"), None)
                return_type = next((child for child in fn.children if isinstance(child, Tree) and child.data == "return_type"), None)
                entry["returnType"] = str(return_type.children[0]) if return_type else None
                entry["params"] = [
                    {"type": str(param.children[0]), "name": str(param.children[1]),
                     "hint": fragment[param.children[2].meta.start_pos:param.children[2].meta.end_pos]
                     if len(param.children) > 2 else ""}
                    for param in (params.children if params else [])
                ]
                entry["locals"] = list(dict.fromkeys(re.findall(
                    r"\b(?:u8|u16|i8|i16|f16|f32)\s+([A-Za-z_]\w*)", parsed)))
                types = {}
                for item in fn.iter_subtrees_topdown():
                    if item.data in ("param", "var_decl_stmt"):
                        typ, name = str(item.children[0]), str(item.children[1])
                    elif item.data == "table_iter_stmt":
                        typ, name = "u16", str(item.children[1])
                        if name not in entry["locals"]:
                            entry["locals"].append(name)
                    else:
                        continue
                    if name in types:
                        types[name] = None
                    else:
                        types[name] = typ
                entry["localTypes"] = {name: typ for name, typ in types.items() if typ is not None}
                def add_part(start, end, value, fixed=False):
                    if mask_comments(fragment[start:end]) == fragment[start:end]:
                        entry["statements"].append({"start": start, "end": end,
                                                    "block": value, "fixed": fixed})

                def add_insertion(pos, label):
                    line_start = fragment.rfind("\n", 0, pos) + 1
                    line_indent = re.match(r"[ \t]*", fragment[line_start:pos]).group()
                    entry["statements"].append({"start": pos, "end": pos, "block": None,
                                                "insertion": True, "label": label,
                                                "indent": line_indent + "    ",
                                                "closing_indent": line_indent})

                def walk(node):
                    if not isinstance(node, Tree):
                        return
                    if node.data == "block":
                        for child in node.children:
                            walk(child)
                        if node is not block:
                            add_insertion(node.meta.end_pos - 1, "Add inside this block")
                        return
                    if node.data in ("assign_stmt", "expr_stmt", "return_stmt", "var_decl_stmt"):
                        value = statement(fragment, node)
                        if value is not None:
                            add_part(node.meta.start_pos, node.meta.end_pos, value)
                        return
                    if node.data in ("if_stmt", "else_if_clause", "while_stmt", "switch_stmt"):
                        condition = node.children[0]
                        label = {"if_stmt": "if condition", "else_if_clause": "else if condition",
                                 "while_stmt": "while condition", "switch_stmt": "switch value"}[node.data]
                        start, end = condition.meta.start_pos, condition.meta.end_pos
                        add_part(start, end, {"op": "header", "label": label,
                                             "value": fragment[start:end]}, True)
                        for child in node.children[1:]:
                            walk(child)
                        return
                    if node.data == "table_iter_stmt":
                        table, iterator, body = node.children
                        start = table.start_pos
                        end = parsed.find(")", iterator.end_pos, body.meta.start_pos) + 1
                        if end:
                            add_part(start, end, {"op": "foreachHeader", "table": str(table),
                                                  "iterator": str(iterator)}, True)
                        walk(body)
                        return
                    if node.data == "case_clause":
                        value = node.children[0]
                        add_part(value.start_pos, value.end_pos,
                                 {"op": "header", "label": "case value", "value": str(value)}, True)
                        for child in node.children[1:]:
                            walk(child)
                        ending = re.search(r"\bbreak\s*;\s*$",
                                           parsed[node.meta.start_pos:node.meta.end_pos])
                        if ending:
                            add_insertion(node.meta.start_pos + ending.start(), "Add to this case")
                        return
                    if node.data == "default_clause":
                        for child in node.children:
                            walk(child)
                        ending = re.search(r"\bbreak\s*;\s*$",
                                           parsed[node.meta.start_pos:node.meta.end_pos])
                        pos = node.meta.start_pos + ending.start() if ending else node.meta.end_pos
                        add_insertion(pos, "Add to default")
                        return
                    for child in node.children:
                        walk(child)
                walk(block)
                entry["insert"] = block.meta.end_pos - 1
                entry["signature"] = fragment[:block.meta.start_pos].strip()
                entry["rootBlock"] = block.meta.start_pos
                entry["display"] = display_tree(fragment, parsed, block, entry["statements"], found := [])
                entry["comments"] = found
                cursor = 0
                for part in entry["statements"]:
                    part["before"] = fragment[cursor:part["start"]]
                    cursor = part["end"]
                entry["tail"] = fragment[cursor:entry["insert"]]
                entry["closing"] = fragment[entry["insert"]:]
            except UnexpectedInput as error:
                entry["error"] = f"Cannot parse this function: line {error.line}, column {error.column}."
            result.append(entry)
            i = k
            continue
        if token == "{":
            depth += 1
        elif token == "}":
            depth = max(0, depth - 1)
        i += 1
    return result


def read_document(project, name):
    source = read_source(file_path(project, name))
    return {"name": name, "hash": text_hash(source), "functions": functions(source)}


def change_function(project, payload, symbols):
    if not isinstance(payload, dict):
        raise ValueError("Expected a file, original hash and function operation.")
    path = file_path(project, payload.get("name"))
    if path.name == "contact.gel":
        raise ValueError("contact.gel is generated by the contact editor; edit it there.")
    source = read_source(path)
    if text_hash(source) != payload.get("hash"):
        raise ValueError("File changed since it was opened. Reload it before changing functions.")
    action = payload.get("action")
    if action == "add":
        name = payload.get("function_name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError("Function name must be a GEL identifier.")
        if name in {"u8", "i8", "u16", "i16", "f16", "f32", "if", "else", "switch",
                    "case", "default", "break", "return", "while", "for_each", "fn",
                    "enum", "struct", "struct_table", "asm", "static", "dp", "mmio",
                    "persist", "extern", "interleaved", "create", "free"}:
            raise ValueError("Function name is a reserved GEL keyword.")
        if any(name in symbols[key] for key in ("functions", "globals", "tables", "enums", "defines", "hardware", "bit")) or any(
                name in values for values in symbols["enums"].values()):
            raise ValueError(f"Symbol '{name}' already exists.")
        for candidate in Path(project).glob("*.gel"):
            if candidate.is_symlink() or candidate.resolve().parent != Path(project).resolve():
                raise ValueError("Project GEL files must not be symbolic links.")
            if any(fn["name"] == name for fn in functions(read_source(candidate))):
                raise ValueError(f"Function '{name}' already exists in {candidate.name}.")
        newline = "\r\n" if "\r\n" in source else "\n"
        separator = "" if not source else newline if source.endswith(("\n", "\r")) else newline * 2
        updated = source + separator + f"fn {name}() {{{newline}}}{newline}"
        return path, updated
    if action != "remove" or type(payload.get("function")) is not int:
        raise ValueError("Invalid function operation.")
    entries = functions(source)
    index = payload["function"]
    if index < 0 or index >= len(entries) or entries[index]["error"]:
        raise ValueError("Function is unavailable or cannot be parsed.")
    fn = entries[index]
    start = fn["start"]
    prefix = source[:start]
    static = re.search(r"\bstatic\s+$", mask_comments(prefix))
    if static:
        if mask_comments(prefix[static.start():]) != prefix[static.start():]:
            raise ValueError("Comments between static and fn cannot be removed safely; edit the file manually.")
        start = static.start()
    remaining = source[:start] + source[fn["end"]:]
    name = fn["name"]
    references = []
    for candidate in sorted(Path(project).glob("*.gel")):
        if candidate.is_symlink() or candidate.resolve().parent != Path(project).resolve():
            raise ValueError("Project GEL files must not be symbolic links.")
        text = remaining if candidate == path else read_source(candidate)
        masked = mask_comments(text)
        masked = re.sub(r'"[^"\r\n]*"|\'[^\'\r\n]*\'', lambda m: " " * len(m.group()), masked)
        for match in re.finditer(rf"\b{re.escape(name)}\b", masked):
            references.append(f"{candidate.name}:{text.count(chr(10), 0, match.start()) + 1}")
    if references:
        raise ValueError(f"Cannot remove '{name}': possible references remain at {', '.join(references[:20])}."
                         " Remove them first." + (" (Additional references omitted.)" if len(references) > 20 else ""))
    return path, remaining


def apply_changes(project, payload):
    if not isinstance(payload, dict) or not isinstance(payload.get("function"), int):
        raise ValueError("Expected a file, function index, original hash and edits.")
    path = file_path(project, payload.get("name"))
    if path.name == "contact.gel":
        raise ValueError("contact.gel is generated by the contact editor; edit it there.")
    source = read_source(path)
    if text_hash(source) != payload.get("hash"):
        raise ValueError("File changed since it was opened. Reload it before saving.")
    entries = functions(source)
    number = payload["function"]
    if number < 0 or number >= len(entries) or entries[number]["error"]:
        raise ValueError("Function is unavailable or cannot be parsed.")
    function = entries[number]
    changes = payload.get("changes")
    addition = payload.get("addition", "")
    if not isinstance(changes, list) or not isinstance(addition, str):
        raise ValueError("Invalid edits.")
    edits, seen = [], set()
    for change in changes:
        if not isinstance(change, dict) or type(change.get("index")) is not int or not isinstance(change.get("code"), str):
            raise ValueError("Invalid statement edit.")
        index = change["index"]
        if index in seen or index < 0 or index >= len(function["statements"]):
            raise ValueError("Invalid or duplicate statement index.")
        seen.add(index)
        part = function["statements"][index]
        edits.append((part["start"], part["end"], change["code"]))
    if addition:
        edits.append((function["insert"], function["insert"], addition))
    fragment = function["source"]
    comment_changes = payload.get("comment_changes", [])
    if not isinstance(comment_changes, list):
        raise ValueError("Invalid comment edits.")
    seen_comments = set()
    for change in comment_changes:
        if (not isinstance(change, dict) or type(change.get("index")) is not int
                or not (change.get("text") is None or isinstance(change.get("text"), str))):
            raise ValueError("Invalid comment edit.")
        index = change["index"]
        if index in seen_comments or index < 0 or index >= len(function["comments"]):
            raise ValueError("Invalid or duplicate comment index.")
        seen_comments.add(index)
        comment = function["comments"][index]
        start, end, original = comment["start"], comment["end"], comment["text"]
        text = change["text"]
        if text is None:
            line_start = fragment.rfind("\n", 0, start) + 1
            line_end = fragment.find("\n", end)
            line_end = len(fragment) if line_end < 0 else line_end
            if not fragment[line_start:start].strip() and not fragment[end:line_end].strip():
                start, end = line_start, min(line_end + 1, len(fragment))
            else:
                while start > 0 and fragment[start - 1] in " \t":
                    start -= 1
            edits.append((start, end, ""))
            continue
        if original.startswith("//"):
            if "\n" in text or "\r" in text:
                raise ValueError("A // comment must stay on one line.")
            replacement = "// " + text.strip() if text.strip() else "//"
        else:
            if "*/" in text:
                raise ValueError("A comment cannot contain */.")
            replacement = "/* " + text.strip() + " */"
        edits.append((start, end, replacement))
    ordered = sorted(edits)
    for before, after in zip(ordered, ordered[1:]):
        if after[0] < before[1]:
            raise ValueError("Edits overlap; reload and try again.")
    for start, end, replacement in sorted(edits, reverse=True):
        fragment = fragment[:start] + replacement + fragment[end:]
    try:
        parser().parse(mask_comments(fragment))
    except UnexpectedInput as error:
        raise ValueError(f"GEL syntax error at line {error.line}, column {error.column}: {error.get_context(fragment).strip()}") from error
    return path, source[:function["start"]] + fragment + source[function["end"]:]


MOVABLE = {"var_decl_stmt", "return_stmt", "assign_stmt", "if_stmt", "switch_stmt", "while_stmt",
           "table_iter_stmt", "inline_asm_stmt", "expr_stmt"}


def parse_function(fragment):
    masked = mask_comments(fragment)
    masked = re.sub(r"(?m)^[ \t]*#[^\r\n]*", lambda m: " " * len(m.group()), masked)
    tree = parser().parse(masked)
    fn = tree.children[0]
    block = next(child for child in fn.children if isinstance(child, Tree) and child.data == "block")
    return masked, block


def scope_model(parsed, block):
    """Statement tree (blocks of statements) used to check variable scope for moves."""
    clean = re.sub(r'"[^"\r\n]*"|\'[^\'\r\n]*\'', lambda m: " " * len(m.group()), parsed)
    units, blocks = {}, {}

    def words(start, end, holes):
        text = list(clean[start:end])
        for a, b in holes:
            for i in range(max(a, start) - start, max(min(b, end) - start, 0)):
                text[i] = " "
        return {m.group() for m in re.finditer(r"(?<![.\w])[A-Za-z_]\w*", "".join(text))}

    def make_block(bid, nodes, end, extra_indent, scope=()):
        item = {"id": bid, "scope": list(scope), "items": [], "end": end, "extra": extra_indent}
        blocks[bid] = item
        for node in nodes:
            if isinstance(node, Tree) and node.data in MOVABLE:
                item["items"].append(make_unit(node))
        return item

    def brace_block(node, scope=()):
        return make_block(node.meta.start_pos, node.children, node.meta.end_pos - 1, True, scope)

    def make_unit(node):
        start, end = node.meta.start_pos, node.meta.end_pos
        unit = {"id": start, "start": start, "end": end, "blocks": [], "declares": []}
        units[start] = unit
        holes = []
        if node.data == "if_stmt":
            for child in node.children:
                if isinstance(child, Tree):
                    inner = child if child.data == "block" else next(
                        (c for c in child.children if isinstance(c, Tree) and c.data == "block"), None)
                    if inner is not None:
                        unit["blocks"].append(brace_block(inner)["id"])
                        holes.append((inner.meta.start_pos, inner.meta.end_pos))
        elif node.data in ("while_stmt", "table_iter_stmt"):
            inner = node.children[-1]
            scope = (str(node.children[1]),) if node.data == "table_iter_stmt" else ()
            unit["blocks"].append(brace_block(inner, scope)["id"])
            holes.append((inner.meta.start_pos, inner.meta.end_pos))
        elif node.data == "switch_stmt":
            for clause in node.children[1:]:
                if isinstance(clause, Tree):
                    ending = re.search(r"\bbreak\s*;\s*$", parsed[clause.meta.start_pos:clause.meta.end_pos])
                    stop = clause.meta.start_pos + ending.start() if ending else clause.meta.end_pos
                    unit["blocks"].append(make_block(clause.meta.start_pos, clause.children, stop, False)["id"])
                    blocks[clause.meta.start_pos]["clause"] = (clause.meta.start_pos, clause.meta.end_pos)
                    holes.append((clause.meta.start_pos, clause.meta.end_pos))
        elif node.data == "var_decl_stmt":
            unit["declares"] = [str(node.children[1])]
        unit["uses"] = sorted(words(start, end, holes) - set(unit["declares"]))
        return start

    root = brace_block(block)
    names = set()
    for unit in units.values():
        names.update(unit["declares"])
    for item in blocks.values():
        names.update(item["scope"])
    return {"root": root["id"], "units": units, "blocks": blocks, "names": names}


def scope_problems(model, blocks, bid, scope, out):
    scope = set(scope) | set(blocks[bid]["scope"])
    for uid in blocks[bid]["items"]:
        unit = model["units"][uid]
        for name in unit["uses"]:
            if name in model["names"] and name not in scope:
                out[(uid, name, "use")] = f'"{name}" would be used before it is declared or outside its scope there.'
        for child in unit["blocks"]:
            scope_problems(model, blocks, child, scope, out)
        for name in unit["declares"]:
            if name in scope:
                out[(uid, name, "dup")] = f'"{name}" would be declared twice in the same scope there.'
            scope.add(name)


def nested_ids(model, uid, seen=None):
    seen = seen if seen is not None else set()
    for bid in model["units"][uid]["blocks"]:
        for child in model["blocks"][bid]["items"]:
            seen.add(child)
            nested_ids(model, child, seen)
    return seen


def nested_blocks(model, uid):
    found = set()
    for bid in model["units"][uid]["blocks"]:
        found.add(bid)
        for child in model["blocks"][bid]["items"]:
            found |= nested_blocks(model, child)
    return found


def normalize_selection(model, ids):
    if not isinstance(ids, list) or not ids or any(type(i) is not int or i not in model["units"] for i in ids):
        raise ValueError("Select existing statements to move.")
    chosen = set(ids)
    inner = set()
    for uid in chosen:
        inner |= nested_ids(model, uid)
    return sorted(chosen - inner, key=lambda uid: model["units"][uid]["start"])


def move_verdict(model, ids, target, before_problems):
    if target["block"] not in model["blocks"]:
        return False, "Unknown place."
    if target["before"] in ids:
        return False, "The selection is already there."
    forbidden = set()
    for uid in ids:
        forbidden |= nested_blocks(model, uid)
    if target["block"] in forbidden:
        return False, "A block cannot be moved inside itself."
    blocks = {key: {**value, "items": [i for i in value["items"] if i not in ids]}
              for key, value in model["blocks"].items()}
    items = blocks[target["block"]]["items"]
    if target["before"] is not None and target["before"] not in items:
        return False, "Unknown place."
    at = items.index(target["before"]) if target["before"] is not None else len(items)
    items[at:at] = ids
    after = {}
    scope_problems(model, blocks, model["root"], set(), after)
    for key, message in after.items():
        if key not in before_problems:
            return False, message
    return True, ""


def load_edit(project, payload):
    if not isinstance(payload, dict) or type(payload.get("function")) is not int:
        raise ValueError("Expected a file, function and statements.")
    path = file_path(project, payload.get("name"))
    if path.name == "contact.gel":
        raise ValueError("contact.gel is generated by the contact editor; edit it there.")
    source = read_source(path)
    if text_hash(source) != payload.get("hash"):
        raise ValueError("File changed since it was opened. Reload it before editing code.")
    entries = functions(source)
    number = payload["function"]
    if number < 0 or number >= len(entries) or entries[number]["error"]:
        raise ValueError("Function is unavailable or cannot be parsed.")
    entry = entries[number]
    parsed, block = parse_function(entry["source"])
    model = scope_model(parsed, block)
    before = {}
    scope_problems(model, model["blocks"], model["root"], set(), before)
    return path, source, entry, model, before


def load_move(project, payload, writing):
    path, source, entry, model, before = load_edit(project, payload)
    ids = normalize_selection(model, payload.get("units"))
    return path, source, entry, model, ids, before


def apply_delete(project, payload):
    """Remove existing statements, control blocks or switch cases, unless that breaks variable scope."""
    path, source, entry, model, before = load_edit(project, payload)
    fragment = entry["source"]
    units = {key: {**value, "blocks": list(value["blocks"])} for key, value in model["units"].items()}
    blocks = {key: {**value, "items": list(value["items"])} for key, value in model["blocks"].items()}
    spans = []
    if "case" in payload:
        bid = payload["case"]
        clause = blocks.get(bid, {}).get("clause") if type(bid) is int else None
        if not clause:
            raise ValueError("Select an existing switch case to remove.")
        for unit in units.values():
            if bid in unit["blocks"]:
                unit["blocks"].remove(bid)
        spans.append(clause)
    else:
        ids = normalize_selection(model, payload.get("units"))
        for block in blocks.values():
            block["items"] = [i for i in block["items"] if i not in ids]
        spans.extend((model["units"][uid]["start"], model["units"][uid]["end"]) for uid in ids)
    after = {}
    scope_problems({**model, "units": units}, blocks, model["root"], set(), after)
    for key, message in after.items():
        if key not in before:
            raise ValueError(f"Cannot remove this: {message}")
    edits = []
    for start, end in spans:
        span = line_span(fragment, start, end)
        edits.append((span[0], span[1], "") if span else (start, end, ""))
    for start, end, replacement in sorted(edits, reverse=True):
        fragment = fragment[:start] + replacement + fragment[end:]
    try:
        parser().parse(mask_comments(fragment))
    except UnexpectedInput as error:
        raise ValueError(f"The removal would create a syntax error at line {error.line}, column {error.column}.") from error
    return path, source[:entry["start"]] + fragment + source[entry["end"]:]


def preview_moves(project, payload):
    _, _, _, model, ids, before = load_move(project, payload, False)
    targets = []
    for bid, item in model["blocks"].items():
        for before_id in [*item["items"], None]:
            ok, reason = move_verdict(model, ids, {"block": bid, "before": before_id}, before)
            targets.append({"block": bid, "before": before_id, "ok": ok, "reason": reason})
    return {"units": ids, "targets": targets}


def line_span(fragment, start, end):
    """Whole-line extent of a statement (with a trailing // comment) or None if it shares its line."""
    line_start = fragment.rfind("\n", 0, start) + 1
    line_end = fragment.find("\n", end)
    line_end = len(fragment) if line_end < 0 else line_end
    leading = fragment[line_start:start]
    if leading.strip() or not re.fullmatch(r"[ \t]*(//[^\r\n]*)?[ \t\r]*", fragment[end:line_end]):
        return None
    return line_start, min(line_end + 1, len(fragment)), leading, fragment[line_start:line_end].rstrip("\r")


def apply_moves(project, payload):
    path, source, entry, model, ids, before = load_move(project, payload, True)
    target = payload.get("target")
    if (not isinstance(target, dict) or type(target.get("block")) is not int or
            not (target.get("before") is None or type(target.get("before")) is int)):
        raise ValueError("Invalid drop target.")
    ok, reason = move_verdict(model, ids, target, before)
    if not ok:
        raise ValueError(reason)
    fragment = entry["source"]
    newline = "\r\n" if "\r\n" in fragment else "\n"
    edits, chunks = [], []
    for uid in ids:
        lines, edit = unit_lines(fragment, model["units"][uid])
        edits.append(edit)
        chunks.append(lines)
    edits.append(insertion_edit(fragment, model, target, chunks, newline))
    for start, end, replacement in sorted(edits, key=lambda item: (item[0], item[1]), reverse=True):
        fragment = fragment[:start] + replacement + fragment[end:]
    try:
        parser().parse(mask_comments(fragment))
    except UnexpectedInput as error:
        raise ValueError(f"The move would create a syntax error at line {error.line}, column {error.column}.") from error
    return path, source[:entry["start"]] + fragment + source[entry["end"]:]


def unit_lines(fragment, unit):
    """Dedented source lines of a statement and the edit that removes it."""
    span = line_span(fragment, unit["start"], unit["end"])
    if span:
        first, last, indent, text = span
        lines = [line[len(indent):] if line.startswith(indent) else line.lstrip()
                 for line in text.split("\n")]
        return [line.rstrip("\r") for line in lines], (first, last, "")
    return [" ".join(fragment[unit["start"]:unit["end"]].split())], (unit["start"], unit["end"], "")


def insertion_edit(fragment, model, target, chunks, newline):
    destination = model["blocks"][target["block"]]
    if target["before"] is not None:
        anchor = model["units"][target["before"]]["start"]
    else:
        anchor = destination["end"]
    line_start = fragment.rfind("\n", 0, anchor) + 1
    if not fragment[line_start:anchor].strip():
        indent = fragment[line_start:anchor] + ("    " if target["before"] is None and destination["extra"] else "")
        text = "".join(indent + line + newline if line.strip() else newline
                       for lines in chunks for line in lines)
        return (line_start, line_start, text)
    text = " ".join(line for lines in chunks for line in lines) + " "
    if anchor > 0 and not fragment[anchor - 1].isspace():
        text = " " + text
    return (anchor, anchor, text)


def copy_code(project, payload):
    """Source text of existing statements, for the clipboard. Nothing is changed."""
    _, _, entry, model, ids, _ = load_move(project, payload, False)
    fragment = entry["source"]
    chunks = [unit_lines(fragment, model["units"][uid])[0] for uid in ids]
    return {"count": len(ids), "lines": [line for lines in chunks for line in lines]}


def apply_paste(project, payload):
    """Insert clipboard statements before a statement, or at the end of a block."""
    path, source, entry, model, before = load_edit(project, payload)
    lines = payload.get("lines")
    if (not isinstance(lines, list) or not lines or len(lines) > 2000 or
            any(not isinstance(line, str) or "\n" in line or "\r" in line for line in lines)):
        raise ValueError("The clipboard entry is empty or invalid.")
    target = payload.get("target")
    if not isinstance(target, dict) or not (target.get("before") is None or type(target.get("before")) is int):
        raise ValueError("Invalid paste target.")
    if target.get("before") is not None:
        if target["before"] not in model["units"]:
            raise ValueError("Unknown place.")
        owner = [bid for bid, item in model["blocks"].items() if target["before"] in item["items"]]
        if not owner:
            raise ValueError("Unknown place.")
        target = {"block": owner[0], "before": target["before"]}
    elif type(target.get("block")) is int and target["block"] in model["blocks"]:
        target = {"block": target["block"], "before": None}
    else:
        raise ValueError("Unknown place.")
    fragment = entry["source"]
    newline = "\r\n" if "\r\n" in fragment else "\n"
    start, end, text = insertion_edit(fragment, model, target, [lines], newline)
    fragment = fragment[:start] + text + fragment[end:]
    try:
        parser().parse(mask_comments(fragment))
        parsed, block = parse_function(fragment)
    except UnexpectedInput as error:
        raise ValueError(f"The pasted code is not valid GEL here (line {error.line}, column {error.column}).") from error
    after = {}
    scope_model_after = scope_model(parsed, block)
    scope_problems(scope_model_after, scope_model_after["blocks"], scope_model_after["root"], set(), after)
    known = {}
    for key, message in before.items():
        known[message] = known.get(message, 0) + 1
    for message in after.values():
        if known.get(message, 0) <= 0:
            raise ValueError(f"Cannot paste here: {message}")
        known[message] -= 1
    return path, source[:entry["start"]] + fragment + source[entry["end"]:]
