"""Find possible GEL variable references while preserving source locations."""

import bisect
import re
from pathlib import Path


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def _mask(project, source):
    masked = project.mask_comments(source)
    return re.sub(r'"[^"\r\n]*"|\'[^\'\r\n]*\'',
                  lambda match: " " * len(match.group()), masked)


def _table_fields(masked, table, field):
    tokens = list(re.finditer(r"[A-Za-z_][A-Za-z0-9_]*|[^\s]", masked))
    result = []
    for index, token in enumerate(tokens):
        if token.group() != table or index + 1 >= len(tokens) or tokens[index + 1].group() != "[":
            continue
        depth, cursor = 1, index + 2
        while cursor < len(tokens) and depth:
            depth += (tokens[cursor].group() == "[") - (tokens[cursor].group() == "]")
            cursor += 1
        if (depth == 0 and cursor + 1 < len(tokens) and tokens[cursor].group() == "."
                and tokens[cursor + 1].group() == field):
            result.append(tokens[cursor + 1])
    return result


def _is_member(masked, start):
    start -= 1
    while start >= 0 and masked[start].isspace():
        start -= 1
    return start >= 0 and masked[start] == "."


def find(project, folder, payload):
    if not isinstance(payload, dict) or payload.get("kind") not in ("global", "define", "field", "local"):
        raise ValueError("Invalid usage query.")
    kind, name = payload["kind"], payload.get("symbol")
    if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
        raise ValueError("Invalid variable name.")
    table = payload.get("table")
    if kind == "field" and (not isinstance(table, str) or not IDENTIFIER.fullmatch(table)):
        raise ValueError("Invalid table name.")
    root = Path(folder).resolve()
    scope = None
    if kind == "local":
        target = project.file_path(root, payload.get("file"))
        source = project.read_source(target)
        if project.text_hash(source) != payload.get("hash"):
            raise ValueError("File changed since it was opened. Reload before finding local usages.")
        functions = project.functions(source)
        index = payload.get("function")
        if type(index) is not int or index < 0 or index >= len(functions) or functions[index]["error"]:
            raise ValueError("Function is unavailable or cannot be parsed.")
        function = functions[index]
        if name not in function["locals"] and name not in function.get("localTypes", {}):
            raise ValueError("The variable is not declared in this function.")
        scope = (target, function["start"], function["end"])
    paths = []
    if scope:
        paths = [scope[0]]
    else:
        for path in sorted(root.rglob("*.gel")):
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):
                paths.append(path)
    results = []
    for path in paths:
        source = project.read_source(path)
        masked = _mask(project, source)
        if kind == "field":
            matches = _table_fields(masked, table, name)
        else:
            matches = list(re.finditer(rf"\b{re.escape(name)}\b", masked))
            if kind in ("global", "local"):
                matches = [match for match in matches if not _is_member(masked, match.start())]
        functions = project.functions(source) if matches and path.parent == root else []
        filename = str(path.relative_to(root)).replace("\\", "/")
        digest = project.text_hash(source)
        line_starts = [0] + [match.end() for match in re.finditer("\n", source)]
        for match in matches:
            pos = match.start()
            if scope and not scope[1] <= pos < scope[2]:
                continue
            line = bisect.bisect_right(line_starts, pos)
            start = line_starts[line - 1]
            end = source.find("\n", pos)
            if end < 0:
                end = len(source)
            item = {"file": filename, "line": line,
                    "column": pos - start + 1, "text": source[start:end].strip(),
                    "hash": digest, "function": None, "part": None}
            for index, fn in enumerate(functions):
                if fn["start"] <= pos < fn["end"]:
                    item["function"] = index
                    item["functionName"] = fn["name"]
                    offset = pos - fn["start"]
                    item["part"] = next((i for i, part in enumerate(fn["statements"])
                                         if not part.get("insertion") and part["start"] <= offset < part["end"]), None)
                    break
            results.append(item)
    return {"results": results, "scope": "function" if scope else "project",
            "warning": "Textual matches may include shadowed names or declarations; macro expansions, aliases, assembly and included files outside the project may need manual review."}
