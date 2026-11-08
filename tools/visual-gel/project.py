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


def display_tree(source, parsed, body, parts):
    """Build a visual layout while leaving source patch ranges unchanged."""
    indexes = {(part["start"], part["end"], bool(part.get("insertion"))): i
               for i, part in enumerate(parts)}
    comment_pattern = re.compile(r'"[^"\r\n]*"|\'[^\'\r\n]*\'|//[^\r\n]*|/\*[\s\S]*?\*/')

    def raw(text):
        return [{"kind": "raw", "text": text}] if text.strip() else []

    def comments(start, end):
        return [{"kind": "raw", "text": match.group()}
                for match in comment_pattern.finditer(source, start, end)
                if match.group().startswith(("//", "/*"))]

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
            result += raw(source[cursor:node.meta.start_pos])
            result += visual_node(node)
            cursor = node.meta.end_pos
        return result + raw(source[cursor:end])

    def block_content(node):
        start, end = node.meta.start_pos + 1, node.meta.end_pos - 1
        return sequence(node.children, start, end) + insertion(end)

    def conditional_branch(node, label):
        condition, child_block = node.children[:2]
        a, b = condition.meta.start_pos, condition.meta.end_pos
        content = (comments(node.meta.start_pos, a) + item(a, b, label) +
                   comments(b, child_block.meta.start_pos))
        return {"kind": "branch", "items": content, "body": block_content(child_block)}

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
            content += raw(source[colon + 1:insert_pos]) if not content else []
        content += insertion(insert_pos)
        content += comments(insert_pos, end)
        return {"kind": "branch", "items": header, "body": content}

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
                              "body": block_content(child_block)}
                branch["items"] = first_comment + branch["items"]
                branches.append(branch)
                cursor = clause.meta.end_pos
            return [{"kind": "control", "style": "if", "branches": branches}]
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
            return [{"kind": "control", "style": "while" if kind == "while_stmt" else "for_each",
                     "branches": [{"kind": "branch", "items": header, "body": block_content(child_block)}]}]
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
            return [{"kind": "control", "style": "switch",
                     "branches": [{"kind": "branch", "items": header, "body": [
                         {"kind": "control", "style": "cases", "branches": branches}]}]}]
        index = indexes.get((start, end, False))
        return [{"kind": "part", "index": index}] if index is not None else raw(source[start:end])

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
                entry["display"] = display_tree(fragment, parsed, block, entry["statements"])
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
    for start, end, replacement in sorted(edits, reverse=True):
        fragment = fragment[:start] + replacement + fragment[end:]
    try:
        parser().parse(mask_comments(fragment))
    except UnexpectedInput as error:
        raise ValueError(f"GEL syntax error at line {error.line}, column {error.column}: {error.get_context(fragment).strip()}") from error
    return path, source[:function["start"]] + fragment + source[function["end"]:]
