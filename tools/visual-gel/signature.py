"""Conservative project-wide refactoring of GEL function parameters."""

import hashlib
import json
import re
from pathlib import Path

from lark import Tree, UnexpectedInput

import gel_codegen


IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
RESERVED = {"u8", "i8", "u16", "i16", "f16", "f32", "if", "else", "switch",
            "case", "default", "break", "return", "while", "for_each", "fn", "enum",
            "struct", "struct_table", "asm", "static", "dp", "mmio", "persist",
            "extern", "interleaved", "create", "free"}
EFFECTS = {"func_call", "table_create", "table_free", "pre_inc", "post_inc", "pre_dec",
           "post_dec", "var_ref", "field_ref"}


def _function_tree(project, fragment):
    source = project.mask_comments(fragment)
    source = re.sub(r"(?m)^[ \t]*#[^\r\n]*", lambda m: " " * len(m.group()), source)
    return project.parser().parse(source).children[0]


def _params(tree):
    items = next((node for node in tree.children if isinstance(node, Tree) and node.data == "param_list"), None)
    return items.children if items else []


def _expression(project, expression, function_name):
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("Each new parameter needs an argument expression for existing calls.")
    if project.mask_comments(expression) != expression:
        raise ValueError("New argument expressions cannot contain comments.")
    try:
        tree = project.parser().parse(f"fn check() {{ check({expression}); }}")
    except UnexpectedInput as error:
        raise ValueError("A new argument is not a valid GEL expression.") from error
    block = next(tree.find_data("block"))
    if len(block.children) != 1 or block.children[0].data != "expr_stmt":
        raise ValueError("Each new argument must be a single GEL expression.")
    call = block.children[0].children[0]
    if not isinstance(call, Tree) or call.data != "func_call" or str(call.children[0]) != "check":
        raise ValueError("Each new argument must be a single GEL expression.")
    args = next((item for item in call.children if isinstance(item, Tree) and item.data == "arg_list"), None)
    if args is None or len(args.children) != 1:
        raise ValueError("Each new argument must be a single GEL expression.")
    if any(str(node.children[0]) == function_name for node in args.find_data("func_call")):
        raise ValueError("A new argument cannot call the function whose parameters are changing.")
    return expression.strip()


def _patch(source, edits):
    for start, end, value in sorted(edits, reverse=True):
        source = source[:start] + value + source[end:]
    return source


def _tokens_in_asm(tree, names):
    for asm in tree.find_data("inline_asm_stmt"):
        if any(re.search(rf"\b{re.escape(name)}\b", str(asm.children[0])) for name in names):
            return True
    return False


def _contact_calls(value, name):
    if isinstance(value, dict):
        if value.get("op") == "call":
            target = value.get("fn")
            if target == name or isinstance(target, dict) and target.get("name") == name:
                return True
        return any(_contact_calls(item, name) for item in value.values())
    if isinstance(value, list):
        return any(_contact_calls(item, name) for item in value)
    return isinstance(value, str) and bool(re.search(rf"\b{re.escape(name)}\s*\(", value))


def prepare(project, folder, payload):
    if not isinstance(payload, dict) or type(payload.get("function")) is not int:
        raise ValueError("Expected a file, function index, original hash and parameters.")
    target = project.file_path(folder, payload.get("name"))
    if target.name == "contact.gel":
        raise ValueError("contact.gel is generated; edit it in the contact editor.")
    paths = sorted(Path(folder).glob("*.gel"))
    if any(path.is_symlink() or path.resolve().parent != Path(folder).resolve() for path in paths):
        raise ValueError("Project GEL files must not be symbolic links.")
    original = {path.name: project.read_source(path) for path in paths}
    sources = dict(original)
    if project.text_hash(sources[target.name]) != payload.get("hash"):
        raise ValueError("File changed since it was opened. Reload it before editing parameters.")
    entries = project.functions(sources[target.name])
    index = payload["function"]
    if index < 0 or index >= len(entries) or entries[index]["error"]:
        raise ValueError("Function is unavailable or cannot be parsed.")
    entry = entries[index]
    name = entry["name"]
    tree = _function_tree(project, entry["source"])
    old_return = entry["returnType"]
    new_return = payload.get("returnType", old_return)
    if new_return is not None and new_return not in gel_codegen.SIZES:
        raise ValueError("Return type must be a supported GEL type or untyped.")
    return_changed = new_return != old_return
    old = _params(tree)
    old_names = [str(p.children[1]) for p in old]
    new = payload.get("params")
    if not isinstance(new, list):
        raise ValueError("Expected a list of parameters.")
    used, names = set(), set()
    parsed = []
    for param in new:
        if not isinstance(param, dict):
            raise ValueError("Invalid parameter.")
        typ, pname, origin = param.get("type"), param.get("name"), param.get("origin")
        if typ not in gel_codegen.SIZES or not isinstance(pname, str) or not IDENTIFIER.fullmatch(pname) or pname in RESERVED:
            raise ValueError("Parameters need a supported type and a valid GEL identifier.")
        if pname in names:
            raise ValueError(f"Duplicate parameter name: {pname}.")
        names.add(pname)
        if origin is not None and (type(origin) is not int or origin < 0 or origin >= len(old) or origin in used):
            raise ValueError("Invalid or duplicate original parameter.")
        if origin is not None:
            used.add(origin)
        parsed.append({"type": typ, "name": pname, "origin": origin, "argument": param.get("argument", "")})
    if any(len(param.children) > 2 for param in old):
        raise ValueError("Register-hinted parameters cannot be edited safely.")
    removed = set(range(len(old))) - used
    kept = [param["origin"] for param in parsed if param["origin"] is not None]
    if kept != sorted(kept):
        raise ValueError("Reordering existing parameters is not supported.")
    renames = {old_names[p["origin"]]: p["name"] for p in parsed
               if p["origin"] is not None and old_names[p["origin"]] != p["name"]}
    body = next(node for node in tree.children if isinstance(node, Tree) and node.data == "block")
    local_names = {str(node.children[1]) for node in body.iter_subtrees()
                   if node.data in ("var_decl_stmt", "table_iter_stmt")}
    if names & local_names:
        raise ValueError("A parameter name conflicts with a local variable or loop iterator.")
    refs = [node.children[0] for node in body.iter_subtrees() if node.data == "var_ref"]
    if any(str(ref) in names - set(old_names) for ref in refs):
        raise ValueError("A new parameter name would shadow an existing variable reference in the function.")
    if any(str(ref) in {old_names[i] for i in removed} for ref in refs):
        raise ValueError("A removed parameter is still used inside the function. Remove those uses first.")
    if _tokens_in_asm(tree, set(old_names) | names | {name}):
        raise ValueError("Inline assembly refers to this function or its parameters; update it manually.")
    edits = [(ref.start_pos, ref.end_pos, renames[str(ref)]) for ref in refs if str(ref) in renames]
    param_nodes = _params(tree)
    params_edited = (len(parsed) != len(old) or
                     any(p["origin"] != i or p["type"] != str(old[i].children[0])
                         or p["name"] != str(old[i].children[1])
                         for i, p in enumerate(parsed) if i < len(old)))
    if param_nodes:
        start, end = param_nodes[0].meta.start_pos, param_nodes[-1].meta.end_pos
        if params_edited and project.mask_comments(entry["source"][start:end]) != entry["source"][start:end]:
            raise ValueError("The parameter list contains comments; edit it manually.")
    else:
        match = re.search(r"\bfn\s+" + re.escape(name) + r"\s*\(", entry["source"])
        if not match:
            raise ValueError("Could not locate function parameters.")
        start = end = match.end()
    if params_edited:
        edits.append((start, end, ", ".join(f"{p['type']} {p['name']}" for p in parsed)))
    result_node = next((node for node in tree.children if isinstance(node, Tree) and node.data == "return_type"), None)
    if return_changed:
        if result_node:
            result_source = entry["source"][result_node.meta.start_pos:result_node.meta.end_pos]
            if project.mask_comments(result_source) != result_source:
                raise ValueError("The return type contains comments; edit it manually.")
            edits.append((result_node.meta.start_pos, result_node.meta.end_pos,
                          f"-> {new_return}" if new_return else ""))
        else:
            close = project.mask_comments(entry["source"]).find(")", end)
            if close < 0:
                raise ValueError("Could not locate function return type.")
            edits.append((close + 1, close + 1, f" -> {new_return}"))
    fragment = _patch(entry["source"], edits)
    sources[target.name] = sources[target.name][:entry["start"]] + fragment + sources[target.name][entry["end"]:]
    changed_functions = {}
    if fragment != entry["source"]:
        changed_functions[target.name] = [name]
    signature_changed = (len(parsed) != len(old) or
                         any(p["origin"] != i or p["type"] != str(old[i].children[0])
                             for i, p in enumerate(parsed) if i < len(old)))
    arguments_changed = len(parsed) != len(old) or any(p["origin"] != i for i, p in enumerate(parsed))
    for filename, source in original.items():
        directives = re.findall(r"(?m)^[ \t]*#define\b[^\r\n]*", project.mask_comments(source))
        risky = {name} if signature_changed else set()
        risky.update(renames)
        if any(re.search(rf"\b{re.escape(symbol)}\b", directive)
               for directive in directives for symbol in risky):
            raise ValueError(f"{filename} contains a macro referencing a changed name; update it manually.")
    if sum(fn["name"] == name for source in original.values() for fn in project.functions(source)) != 1:
        raise ValueError(f"Function {name} has multiple project definitions; it cannot be refactored safely.")
    if signature_changed:
        contacts = Path(folder) / "contacts.json"
        if contacts.is_file():
            data = json.loads(contacts.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("interactions"), list) or any(
                    not isinstance(interaction, dict) for interaction in data["interactions"]):
                raise ValueError("Invalid contact configuration; cannot safely check handler references.")
            for interaction in data.get("interactions", []):
                if any(interaction.get(phase) == name for phase in ("enter", "stay", "exit")):
                    raise ValueError("The contact editor uses this function as a handler; change its configuration first.")
            if _contact_calls(data.get("handlers", {}), name):
                raise ValueError("The contact editor calls this function; change its configuration first.")
        for param in parsed:
            if param["origin"] is None:
                param["argument"] = _expression(project, param["argument"], name)
    call_counts = {}
    call_functions = {}
    if signature_changed or return_changed:
        for filename, source in list(sources.items()):
            pieces = project.functions(source)
            masked = project.mask_comments(source)
            masked = re.sub(r'"[^"\r\n]*"|\'[^\'\r\n]*\'', lambda m: " " * len(m.group()), masked)
            outside = list(masked)
            for fn in pieces:
                outside[fn["start"]:fn["end"]] = " " * (fn["end"] - fn["start"])
            if re.search(rf"\b{re.escape(name)}\s*\(", "".join(outside)):
                raise ValueError(f"{filename} contains a call outside a parseable function; update it manually.")
            updates = []
            count = 0
            callers = []
            for fn in pieces:
                if fn["error"]:
                    raise ValueError(f"Cannot safely scan {filename}: function {fn['name']} cannot be parsed.")
                fragment = fn["source"]
                fn_tree = _function_tree(project, fragment)
                if _tokens_in_asm(fn_tree, {name}):
                    raise ValueError(f"{filename} contains an assembly reference to {name}; update it manually.")
                calls = [node for node in fn_tree.iter_subtrees_topdown()
                         if node.data == "func_call" and str(node.children[0]) == name]
                if not calls:
                    continue
                if filename == "contact.gel" and signature_changed:
                    raise ValueError("Generated contact.gel calls this function. Update the contact configuration manually first.")
                call_data = {}
                for call in calls:
                    args_node = next((node for node in call.children if isinstance(node, Tree) and node.data == "arg_list"), None)
                    args = args_node.children if args_node else []
                    if len(args) != len(old):
                        raise ValueError(f"{filename}: {name} has a call with {len(args)} arguments, expected {len(old)}.")
                    if not arguments_changed:
                        continue
                    for i in removed:
                        if any(node.data in EFFECTS for node in args[i].iter_subtrees()):
                            raise ValueError(f"{filename}: removing argument {i + 1} would discard possible side effects.")
                    name_token = call.children[0]
                    open_pos = fragment.find("(", name_token.end_pos, call.meta.end_pos)
                    start, end = open_pos + 1, call.meta.end_pos - 1
                    if project.mask_comments(fragment[start:end]) != fragment[start:end]:
                        raise ValueError(f"{filename}: a call to {name} contains comments in its arguments; update it manually.")
                    call_data[id(call)] = (start, end, args)
                def render_range(start, end):
                    inner = [call for call in calls if start <= call.meta.start_pos and call.meta.end_pos <= end]
                    direct = [call for call in inner if not any(other is not call and
                              other.meta.start_pos <= call.meta.start_pos and call.meta.end_pos <= other.meta.end_pos
                              for other in inner)]
                    replacements = []
                    for call in direct:
                        a, b, args = call_data[id(call)]
                        values = [render_range(arg.meta.start_pos, arg.meta.end_pos) for arg in args]
                        replacement = ", ".join(values[p["origin"]] if p["origin"] is not None else p["argument"] for p in parsed)
                        replacements.append((a - start, b - start, replacement))
                    return _patch(fragment[start:end], replacements)
                updated = render_range(0, len(fragment)) if arguments_changed else fragment
                updates.append((fn["start"], fn["end"], updated))
                count += len(calls)
                callers.append({"name": fn["name"], "count": len(calls)})
                if updated != fragment:
                    changed = changed_functions.setdefault(filename, [])
                    if fn["name"] not in changed:
                        changed.append(fn["name"])
            sources[filename] = _patch(source, updates)
            if count:
                call_counts[filename] = count
                call_functions[filename] = callers
    for filename, source in sources.items():
        for fn in project.functions(source):
            if fn["error"]:
                raise ValueError(f"Resulting function {fn['name']} in {filename} has invalid GEL syntax.")
    changes = {name: text for name, text in sources.items() if text != original[name]}
    hashes = {name: project.text_hash(text) for name, text in original.items()}
    digest = hashlib.sha256(json.dumps({"hashes": hashes, "changes": changes}, sort_keys=True).encode()).hexdigest()
    return {"changes": changes, "hashes": hashes, "token": digest, "calls": call_counts,
            "call_functions": call_functions, "changed_functions": changed_functions,
            "type_changed": any(p["origin"] is not None and p["type"] != str(old[p["origin"]].children[0]) for p in parsed),
            "return_changed": return_changed}
