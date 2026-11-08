#!/usr/bin/env python3
"""Local server for the GEL editors."""

import argparse
import importlib.util
import json
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(ROOT / "compiler"))
import object_definitions


def load_editor(name, filename="server.py"):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), TOOLS / name / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contacts = load_editor("contact-editor")
metasprites = load_editor("metasprite-editor")
visual_project = load_editor("visual-gel", "project.py")
visual_memory = load_editor("visual-gel", "memory.py")
visual_signature = load_editor("visual-gel", "signature.py")


class EditorHandler(SimpleHTTPRequestHandler):
    server_version = "GELEditor/1"
    api_name = metasprites.EditorHandler.api_name
    send_file = metasprites.EditorHandler.send_file
    read_body = metasprites.EditorHandler.read_body
    send_json = contacts.EditorHandler.send_json

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(TOOLS), **kwargs)

    @property
    def assets_dir(self):
        return self.server.assets_dir

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/gel-files":
            try:
                files = sorted(p.name for p in self.server.project_dir.glob("*.gel") if p.is_file())
                self.send_json(200, {"files": files})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path.startswith("/api/gel-files/"):
            try:
                name = unquote(path[len("/api/gel-files/"):])
                self.send_json(200, visual_project.read_document(self.server.project_dir, name))
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/gel-memory":
            try:
                self.send_json(200, visual_memory.memory_layout(self.server.project_dir))
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/objects":
            try:
                self.send_json(200, object_definitions.load(self.server.project_dir / "objects.json"))
            except (OSError, ValueError) as error:
                self.send_json(500, {"error": str(error)})
        elif path in ("/api/contacts", "/api/symbols"):
            contacts.EditorHandler.do_GET(self)
        elif path.startswith("/api/"):
            metasprites.EditorHandler.do_GET(self)
        else:
            if path == "/":
                self.path = "/editor/index.html"
                path = self.path
            decoded = unquote(path)
            parts = decoded.split("/")
            if (len(parts) < 3 or parts[1] not in ("editor", "object-editor", "contact-editor", "metasprite-editor", "visual-gel")
                    or any(part in (".", "..") or "\\" in part for part in parts)
                    or (parts[-1] and Path(parts[-1]).suffix not in (".html", ".js", ".css"))):
                self.send_error(404)
                return
            super().do_GET()

    def do_POST(self):
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
                    or parsed.netloc != self.headers.get("Host")):
                self.send_error(403, "Invalid request origin")
                return
        path = urlparse(self.path).path
        if path == "/api/gel-files":
            try:
                data = json.loads(self.read_body(contacts.MAX_BODY_BYTES).decode("utf-8"))
                with self.server.gel_lock:
                    target, source = visual_project.apply_changes(self.server.project_dir, data)
                    contacts.atomic_write(target, source.encode("utf-8"))
                    result = visual_project.read_document(self.server.project_dir, target.name)
                self.send_json(200, result)
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/gel-functions":
            try:
                data = json.loads(self.read_body(contacts.MAX_BODY_BYTES).decode("utf-8"))
                with self.server.gel_lock:
                    target, source = visual_project.change_function(
                        self.server.project_dir, data, contacts.build_symbols(self.server.project_dir))
                    if data.get("action") == "remove":
                        entries = visual_project.functions(visual_project.read_source(target))
                        name = entries[data["function"]]["name"]
                        config_path = self.server.project_dir / "contacts.json"
                        if config_path.is_file():
                            config = json.loads(config_path.read_text(encoding="utf-8"))
                            if not isinstance(config, dict) or not isinstance(config.get("interactions"), list) or any(
                                    not isinstance(item, dict) for item in config["interactions"]):
                                raise ValueError("Invalid contact configuration; cannot safely check handler references.")
                            if (any(item.get(phase) == name for item in config["interactions"]
                                    for phase in ("enter", "stay", "exit")) or
                                    visual_signature._contact_calls(config.get("handlers", {}), name)):
                                raise ValueError(f"Cannot remove '{name}': the contact editor references it.")
                    contacts.atomic_write(target, source.encode("utf-8"))
                    result = visual_project.read_document(self.server.project_dir, target.name)
                self.send_json(200, result)
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/gel-signature":
            try:
                data = json.loads(self.read_body(contacts.MAX_BODY_BYTES).decode("utf-8"))
                with self.server.gel_lock:
                    result = visual_signature.prepare(visual_project, self.server.project_dir, data)
                    if data.get("apply"):
                        if data.get("token") != result["token"]:
                            raise ValueError("Project changed since the preview. Preview the changes again.")
                        current = {path.name: visual_project.text_hash(visual_project.read_source(path))
                                   for path in self.server.project_dir.glob("*.gel")}
                        if current != result["hashes"]:
                            raise ValueError("Project changed while applying changes. Preview them again.")
                        originals = {name: visual_project.read_source(self.server.project_dir / name)
                                     for name in result["changes"]}
                        written = []
                        try:
                            for name, source in result["changes"].items():
                                contacts.atomic_write(self.server.project_dir / name, source.encode("utf-8"))
                                written.append(name)
                        except OSError as error:
                            failures = []
                            for name in reversed(written):
                                try:
                                    contacts.atomic_write(self.server.project_dir / name, originals[name].encode("utf-8"))
                                except OSError as rollback_error:
                                    failures.append(f"{name}: {rollback_error}")
                            if failures:
                                raise OSError(f"Write failed: {error}. Rollback also failed for: {'; '.join(failures)}") from error
                            raise
                        document = visual_project.read_document(self.server.project_dir, data["name"])
                    else:
                        document = None
                self.send_json(200, {"token": result["token"], "files": sorted(result["changes"]),
                                     "calls": result["calls"], "type_changed": result["type_changed"],
                                     "call_functions": result["call_functions"],
                                     "changed_functions": result["changed_functions"],
                                     "return_changed": result["return_changed"],
                                     "document": document})
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/validate-gel":
            try:
                data = json.loads(self.read_body(contacts.MAX_BODY_BYTES).decode("utf-8"))
                if not isinstance(data, dict) or not isinstance(data.get("gel"), str):
                    raise ValueError("Expected GEL source text.")
                contacts.check_gel_syntax(data["gel"])
                self.send_json(200, {"valid": True})
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/objects":
            try:
                data = json.loads(self.read_body(contacts.MAX_BODY_BYTES).decode("utf-8"))
                object_definitions.validate(data)
                if any(obj.get("editable_fields") for obj in data["objects"]):
                    fields = contacts.build_symbols(self.server.project_dir)["tables"].get("Entities", {})
                    object_definitions.validate_fields(data, fields)
                contacts.atomic_write(self.server.project_dir / "objects.json",
                                      (json.dumps(data, indent=2) + "\n").encode("utf-8"))
                self.send_json(200, {"saved": "objects.json"})
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/contacts":
            with self.server.gel_lock:
                contacts.EditorHandler.do_POST(self)
        else:
            metasprites.EditorHandler.do_POST(self)


class EditorServer(ThreadingHTTPServer):
    def __init__(self, address, project_dir, assets_dir=None):
        self.project_dir = Path(project_dir).resolve()
        self.assets_dir = Path(assets_dir).resolve() if assets_dir else self.project_dir / "assets"
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.gel_lock = threading.Lock()
        super().__init__(address, EditorHandler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", nargs="?", default=ROOT / "game", help="game project folder")
    parser.add_argument("--assets-dir", help="override the project's assets folder")
    parser.add_argument("--legacy-assets", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("The editor server must bind to loopback only.")
    if args.legacy_assets:
        assets_dir = args.assets_dir or (Path(args.project_dir) if args.project_dir != ROOT / "game" else None)
        project_dir = ROOT / "game"
    else:
        project_dir, assets_dir = args.project_dir, args.assets_dir
    server = EditorServer((args.host, args.port), project_dir, assets_dir)
    print(f"GEL editors: http://{args.host}:{args.port}/", flush=True)
    print(f"Project: {server.project_dir}\nAssets: {server.assets_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping editor server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
