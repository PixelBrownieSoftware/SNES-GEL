#!/usr/bin/env python3
"""Local server for the GEL contact editor.

Serves the editor, loads/saves the contact configuration in the game folder and
exposes a symbol index (tables, enums, defines, functions, globals) scanned from
the project's .gel files so the visual code editor can offer real names.
"""

import argparse
import json
import os
import re
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

EDITOR_DIR = Path(__file__).resolve().parent
ROOT = EDITOR_DIR.parents[1]
CONFIG_NAME = "contacts.json"
OUTPUT_NAME = "contact.gel"
SYMBOLS_NAME = "gel-symbols.json"
MAX_BODY_BYTES = 2 * 1024 * 1024
TYPES = ("u8", "u16", "i8", "i16", "f16", "f32")
BIT_FUNCTIONS = ["bit", "bit_set", "bit_clear", "bit_test"]
BUILTINS = ["abs", "clamp", "sign", "peek8l", "peek8"]
INPUT_VARIABLES = {"pad1_held": "u16", "pad1_pressed": "u16", "pad1_prev": "u16"}

IDENT = r"[A-Za-z_][A-Za-z0-9_]*"
MASK = re.compile(r'^[ \t]*#include[^\r\n]*|//[^\r\n]*|/\*.*?\*/|"[^"\r\n]*"|\'[^\'\r\n]*\'|\basm\s*\{[^}]*\}',
                  re.M | re.S)
TOKEN = re.compile(rf"{IDENT}|0x[0-9a-fA-F]+|[0-9]+(?:\.[0-9]+)?|\S")


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as out:
            temporary = Path(out.name)
            out.write(data)
        os.replace(temporary, path)
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def mask(text):
    return MASK.sub(lambda m: re.sub(r"[^\r\n]", " ", m.group(0)), text)


def scan_source(text, symbols, source):
    """Collect top-level symbols from possibly incomplete GEL source."""
    for match in re.finditer(rf"^[ \t]*#define[ \t]+({IDENT})[ \t]+([^\r\n]*)", text, re.M):
        value = match.group(2).split("//")[0].strip()
        symbols["defines"].add(match.group(1))
        symbols["define_values"][match.group(1)] = value
    for match in re.finditer(rf"^[ \t]*#define[ \t]+({IDENT})[ \t]*(?://.*)?$", text, re.M):
        symbols["defines"].add(match.group(1))
    tokens = [m.group(0) for m in TOKEN.finditer(mask(text))]
    depth = 0
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if depth == 0 and tok == "fn" and i + 1 < len(tokens):
            name = tokens[i + 1]
            j = i + 2
            params = []
            if j < len(tokens) and tokens[j] == "(":
                j += 1
                while j < len(tokens) and tokens[j] != ")":
                    if tokens[j] in TYPES and j + 1 < len(tokens):
                        params.append(f"{tokens[j]} {tokens[j + 1]}")
                    j += 1
            if name.startswith("std_"):
                symbols["hardware"].add(name)
            else:
                symbols["functions"][name] = {"params": params, "source": source}
            while j < len(tokens) and tokens[j] != "{":
                j += 1
            if j < len(tokens):
                depth += 1
            i = j + 1
            continue
        if depth == 0 and tok in ("struct_table", "struct") and i + 1 < len(tokens):
            name = tokens[i + 1]
            j = i + 2
            while j < len(tokens) and tokens[j] != "{":
                j += 1
            fields = {}
            j += 1
            while j < len(tokens) and tokens[j] != "}":
                if tokens[j] in TYPES and j + 1 < len(tokens):
                    fields[tokens[j + 1]] = tokens[j]
                j += 1
            symbols["tables"].setdefault(name, {}).update(fields)
            i = j + 1
            continue
        if depth == 0 and tok == "enum" and i + 1 < len(tokens):
            enum_name = tokens[i + 1]
            j = i + 2
            while j < len(tokens) and tokens[j] != "{":
                j += 1
            members = symbols["enums"].setdefault(enum_name, [])
            j += 1
            while j < len(tokens) and tokens[j] != "}":
                if tokens[j - 1] in ("{", ",") and re.fullmatch(IDENT, tokens[j]):
                    members.append(tokens[j])
                j += 1
            i = j + 1
            continue
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth = max(0, depth - 1)
        elif (depth == 0 and source != "stdlib" and tok in TYPES and i + 2 < len(tokens)
              and (i == 0 or tokens[i - 1] != "(")
              and tokens[i + 2] in (";", "=", "@") and re.fullmatch(IDENT, tokens[i + 1])):
            symbols["globals"][tokens[i + 1]] = tok
        i += 1


def build_symbols(project_dir):
    symbols = {
        "tables": {}, "enums": {}, "defines": set(), "define_values": {}, "functions": {},
        "globals": dict(INPUT_VARIABLES), "hardware": set(BUILTINS),
    }
    stdlib = ROOT / "compiler" / "stdlib"
    sources = [(p, "stdlib") for p in sorted(stdlib.glob("*.gel"))] if stdlib.is_dir() else []
    sources += [(p, "game") for p in sorted(project_dir.glob("*.gel")) if p.name != OUTPUT_NAME]
    # Generated by the build: OBJ_* object type ids, asset sizes and addresses.
    sources += [(p, "build") for p in sorted((project_dir / "build").glob("*.gel"))]
    for path, source in sources:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        scan_source(text, symbols, source)
    for name in list(symbols["functions"]):
        if symbols["functions"][name]["source"] == "stdlib":
            symbols["hardware"].add(name)
            del symbols["functions"][name]
    return {
        "tables": symbols["tables"],
        "enums": symbols["enums"],
        "defines": sorted(symbols["defines"]),
        "defineValues": symbols["define_values"],
        "functions": symbols["functions"],
        "globals": symbols["globals"],
        "hardware": sorted(symbols["hardware"]),
        "bit": BIT_FUNCTIONS,
    }


class EditorHandler(SimpleHTTPRequestHandler):
    server_version = "GELContactEditor/1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(EDITOR_DIR), **kwargs)

    def log_message(self, fmt, *args):
        pass

    def send_json(self, status, value):
        payload = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/symbols":
                symbols = build_symbols(self.server.project_dir)
                cache = self.server.project_dir / "build" / SYMBOLS_NAME
                try:
                    atomic_write(cache, (json.dumps(symbols, indent=1) + "\n").encode("utf-8"))
                except OSError:
                    pass
                self.send_json(200, symbols)
                return
            if path == "/api/contacts":
                config = self.server.project_dir / CONFIG_NAME
                if config.is_file():
                    self.send_json(200, json.loads(config.read_text(encoding="utf-8")))
                else:
                    self.send_json(200, {"version": 1, "interactions": []})
                return
            if path.startswith("/api/"):
                self.send_error(404)
                return
            super().do_GET()
        except (ValueError, OSError) as error:
            self.send_json(500, {"error": str(error)})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path != "/api/contacts":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise ValueError("Invalid Content-Length.") from error
            if length <= 0 or length > MAX_BODY_BYTES:
                raise ValueError("Request body is empty or too large.")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            config = payload.get("config") if isinstance(payload, dict) else None
            code = payload.get("gel") if isinstance(payload, dict) else None
            if (not isinstance(config, dict) or config.get("version") != 1
                    or not isinstance(config.get("interactions"), list) or not isinstance(code, str)):
                raise ValueError("Expected a version 1 contact configuration and generated GEL.")
            atomic_write(self.server.project_dir / CONFIG_NAME,
                         (json.dumps(config, indent=2) + "\n").encode("utf-8"))
            atomic_write(self.server.project_dir / OUTPUT_NAME, code.encode("utf-8"))
            self.send_json(200, {"saved": [CONFIG_NAME, OUTPUT_NAME]})
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error)})
        except OSError as error:
            self.send_json(500, {"error": str(error)})


class EditorServer(ThreadingHTTPServer):
    def __init__(self, address, project_dir):
        self.project_dir = Path(project_dir).resolve()
        self.project_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(address, EditorHandler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", nargs="?", default=ROOT / "game",
                        help="game folder containing .gel files; contacts.json and contact.gel are saved here")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("The editor server must bind to loopback only.")
    server = EditorServer((args.host, args.port), args.project_dir)
    print(f"Contact editor: http://{args.host}:{args.port}/", flush=True)
    print(f"Project folder: {server.project_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping contact editor.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
