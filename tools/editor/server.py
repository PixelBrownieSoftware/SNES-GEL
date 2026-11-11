#!/usr/bin/env python3
"""Local server for the GEL editors."""

import argparse
import importlib.util
import io
import json
import os
import subprocess
import sys
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
sys.path.insert(0, str(ROOT / "compiler"))
import object_definitions

PROJECT_FILE = "gel-project.json"


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
visual_declarations = load_editor("visual-gel", "declarations.py")
visual_usages = load_editor("visual-gel", "usages.py")
visual_removal = load_editor("visual-gel", "removal.py")
rom_overview = load_editor("rom-overview", "rom.py")
tile_levels = load_editor("tile-editor", "levels.py")


def project_cache_path():
    override = os.environ.get("GEL_EDITOR_CONFIG_DIR")
    if override:
        return Path(override) / "last-project.json"
    if os.name == "nt":
        config_dir = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        config_dir = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_dir / "SNES-GEL" / "last-project.json"


def read_project_marker(path):
    marker = Path(path).expanduser().resolve()
    if marker.name != PROJECT_FILE or not marker.is_file():
        raise ValueError(f"Select a {PROJECT_FILE} file.")
    project_dir = marker.parent
    if not (project_dir / "game.gel").is_file():
        raise ValueError("The project folder must contain game.gel.")
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{PROJECT_FILE} is not valid JSON: {error}") from error
    if (not isinstance(data, dict) or data.get("format") != "snes-gel-project" or
            data.get("version") != 1 or not isinstance(data.get("name"), str) or not data["name"].strip()):
        raise ValueError(f"{PROJECT_FILE} must contain format 'snes-gel-project', version 1, and a project name.")
    return {"name": data["name"].strip(), "path": str(project_dir), "marker": str(marker)}


def remember_project(marker):
    cache = project_cache_path()
    cache.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache.with_suffix(".tmp")
    temporary.write_text(json.dumps({"version": 1, "last_project": str(marker)}, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(cache)


def read_last_project():
    cache = project_cache_path()
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid editor project cache '{cache}': {error}") from error
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError(f"Invalid editor project cache '{cache}'.")
    marker = data.get("last_project")
    if marker is not None and not isinstance(marker, str):
        raise ValueError(f"Invalid last_project entry in editor project cache '{cache}'.")
    return marker


def choose_project_file(initial_dir):
    picker = TOOLS / "editor" / "project_picker.py"
    options = {"capture_output": True, "text": True, "check": False}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW
    result = subprocess.run([sys.executable, str(picker), str(initial_dir)], **options)
    if result.returncode:
        detail = result.stderr.strip() or "The project file picker failed."
        raise RuntimeError(detail)
    return result.stdout.strip() or None


def apply_project_changes(folder, result, token):
    if token != result["token"]:
        raise ValueError("Project changed since the preview. Preview the changes again.")
    current = {path.name: visual_project.text_hash(visual_project.read_source(path))
               for path in folder.glob("*.gel")}
    if current != result["hashes"]:
        raise ValueError("Project changed while applying changes. Preview them again.")
    originals = {name: visual_project.read_source(folder / name) for name in result["changes"]}
    written = []
    try:
        for name, source in result["changes"].items():
            contacts.atomic_write(folder / name, source.encode("utf-8"))
            written.append(name)
    except OSError as error:
        failures = []
        for name in reversed(written):
            try:
                contacts.atomic_write(folder / name, originals[name].encode("utf-8"))
            except OSError as rollback_error:
                failures.append(f"{name}: {rollback_error}")
        if failures:
            raise OSError(f"Write failed: {error}. Rollback also failed for: {'; '.join(failures)}") from error
        raise


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
        if path == "/api/project":
            self.send_json(200, self.server.project_info())
        elif path == "/api/tile-editor/maps":
            self.send_json(200, {"maps": tile_levels.maps(self.assets_dir)})
        elif path.startswith("/api/tile-editor/maps/"):
            try:
                name = unquote(path[len("/api/tile-editor/maps/"):])
                self.send_json(200, tile_levels.load(self.assets_dir, self.server.project_dir, name))
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path.startswith("/api/tile-editor/images/"):
            try:
                from PIL import Image
                name = tile_levels.filename(unquote(path[len("/api/tile-editor/images/"):]), ".png")
                with Image.open(self.assets_dir / name) as image:
                    output = io.BytesIO()
                    if image.mode == "P":
                        image.save(output, format="PNG", transparency=0)
                    else:
                        image.save(output, format="PNG")
                payload = output.getvalue()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/gel-files":
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
        elif path.startswith("/api/gel-declarations/"):
            try:
                name = unquote(path[len("/api/gel-declarations/"):])
                target = visual_project.file_path(self.server.project_dir, name)
                source = visual_project.read_source(target)
                self.send_json(200, {"name": name, "hash": visual_project.text_hash(source),
                                     "declarations": visual_declarations.declarations(visual_project, source)})
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/rom-overview":
            try:
                self.send_json(200, rom_overview.describe(self.server.project_dir))
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
            if (len(parts) < 3 or parts[1] not in ("editor", "object-editor", "contact-editor",             "metasprite-editor", "visual-gel", "rom-overview", "tile-editor")                    or any(part in (".", "..") or "\\" in part for part in parts)
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
        if path == "/api/tile-editor/maps":
            try:
                payload = json.loads(self.read_body(contacts.MAX_BODY_BYTES).decode("utf-8"))
                with self.server.gel_lock:
                    target, data = tile_levels.apply(self.assets_dir, self.server.project_dir, payload)
                    contacts.atomic_write(target, data)
                    result = tile_levels.load(self.assets_dir, self.server.project_dir, target.name)
                self.send_json(200, result)
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/project/open":
            try:
                marker = choose_project_file(self.server.project_dir)
                if marker is None:
                    self.send_json(200, {"cancelled": True})
                    return
                with self.server.gel_lock:
                    info = self.server.open_project(marker)
                self.send_json(200, info)
            except ValueError as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
            except RuntimeError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/gel-declaration-rename":
            try:
                data = json.loads(self.read_body(contacts.MAX_BODY_BYTES).decode("utf-8"))
                with self.server.gel_lock:
                    result = visual_declarations.prepare_impact(
                        visual_project, self.server.project_dir, data,
                        contacts.build_symbols(self.server.project_dir))
                    if data.get("apply"):
                        apply_project_changes(self.server.project_dir, result, data.get("token"))
                self.send_json(200, {"token": result["token"], "files": sorted(result["changes"]),
                                     "counts": result["counts"], "diffs": result["diffs"],
                                     "references": result["references"], "type_changes": result["type_changes"]})
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/gel-usages":
            try:
                data = json.loads(self.read_body(contacts.MAX_BODY_BYTES).decode("utf-8"))
                with self.server.gel_lock:
                    result = visual_usages.find(visual_project, self.server.project_dir, data)
                self.send_json(200, result)
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/gel-variable-removal":
            try:
                data = json.loads(self.read_body(contacts.MAX_BODY_BYTES).decode("utf-8"))
                with self.server.gel_lock:
                    result = visual_removal.prepare(visual_project, visual_declarations, visual_usages,
                                                    self.server.project_dir, data)
                    if data.get("apply"):
                        apply_project_changes(self.server.project_dir, result, data.get("token"))
                self.send_json(200, {"token": result["token"], "files": sorted(result["changes"]),
                                     "diffs": result["diffs"]})
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/gel-declarations":
            try:
                data = json.loads(self.read_body(contacts.MAX_BODY_BYTES).decode("utf-8"))
                with self.server.gel_lock:
                    target, source = visual_declarations.apply(
                        visual_project, self.server.project_dir, data,
                        contacts.build_symbols(self.server.project_dir))
                    contacts.atomic_write(target, source.encode("utf-8"))
                    result = {"name": target.name, "hash": visual_project.text_hash(source),
                              "declarations": visual_declarations.declarations(visual_project, source)}
                self.send_json(200, result)
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/gel-files":
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
        elif path == "/api/rom-config":
            try:
                data = json.loads(self.read_body(contacts.MAX_BODY_BYTES).decode("utf-8"))
                with self.server.gel_lock:
                    target, text = rom_overview.apply_settings(self.server.project_dir, data)
                    rom_overview.atomic_write(target, text)
                    result = rom_overview.describe(self.server.project_dir)
                self.send_json(200, result)
            except (ValueError, UnicodeDecodeError) as error:
                self.send_json(400, {"error": str(error)})
            except OSError as error:
                self.send_json(500, {"error": str(error)})
        elif path == "/api/gel-moves":
            try:
                data = json.loads(self.read_body(contacts.MAX_BODY_BYTES).decode("utf-8"))
                with self.server.gel_lock:
                    if data.get("preview") is True:
                        result = visual_project.preview_moves(self.server.project_dir, data)
                    elif data.get("copy") is True:
                        result = visual_project.copy_code(self.server.project_dir, data)
                    elif data.get("paste") is True:
                        target, source = visual_project.apply_paste(self.server.project_dir, data)
                        contacts.atomic_write(target, source.encode("utf-8"))
                        result = visual_project.read_document(self.server.project_dir, target.name)
                    elif data.get("delete") is True:
                        target, source = visual_project.apply_delete(self.server.project_dir, data)
                        contacts.atomic_write(target, source.encode("utf-8"))
                        result = visual_project.read_document(self.server.project_dir, target.name)
                    else:
                        target, source = visual_project.apply_moves(self.server.project_dir, data)
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
    def __init__(self, address, project_dir, assets_dir=None, startup_warning=None):
        self.project_dir = Path(project_dir).resolve()
        self.assets_dir = Path(assets_dir).resolve() if assets_dir else self.project_dir / "assets"
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.gel_lock = threading.Lock()
        self.startup_warning = startup_warning
        self._project_name = self.project_dir.name
        marker = self.project_dir / PROJECT_FILE
        if marker.is_file():
            self._project_name = read_project_marker(marker)["name"]
        super().__init__(address, EditorHandler)

    def project_info(self):
        return {"name": self._project_name, "path": str(self.project_dir),
                "has_marker": (self.project_dir / PROJECT_FILE).is_file(),
                "warning": self.startup_warning}

    def open_project(self, marker):
        info = read_project_marker(marker)
        assets_dir = Path(info["path"]) / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        remember_project(info["marker"])
        self.project_dir = Path(info["path"])
        self.assets_dir = assets_dir
        self._project_name = info["name"]
        self.startup_warning = None
        return self.project_info()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", nargs="?", help="game project folder")
    parser.add_argument("--assets-dir", help="override the project's assets folder")
    parser.add_argument("--legacy-assets", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open-browser", action="store_true", help="open the editor in a web browser")
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("The editor server must bind to loopback only.")
    startup_warning = None
    if args.legacy_assets:
        project_arg = Path(args.project_dir) if args.project_dir else ROOT / "game"
        assets_dir = args.assets_dir or (project_arg if project_arg != ROOT / "game" else None)
        project_dir = ROOT / "game"
    elif args.project_dir:
        project_dir, assets_dir = args.project_dir, args.assets_dir
    else:
        project_dir, assets_dir = ROOT / "game", args.assets_dir
        last_project = read_last_project()
        if last_project:
            try:
                info = read_project_marker(last_project)
            except (OSError, ValueError) as error:
                startup_warning = f"Could not reopen the last project: {error}"
            else:
                project_dir = info["path"]
    server = EditorServer((args.host, args.port), project_dir, assets_dir, startup_warning)
    browser_host = f"[{args.host}]" if ":" in args.host else args.host
    url = f"http://{browser_host}:{server.server_address[1]}/"
    print(f"GEL editors: {url}", flush=True)
    print(f"Project: {server.project_dir}\nAssets: {server.assets_dir}", flush=True)
    try:
        if args.open_browser:
            try:
                opened = webbrowser.open(url)
            except (webbrowser.Error, OSError) as error:
                print(f"Could not open a browser: {error}. Open {url} manually.", file=sys.stderr, flush=True)
            else:
                if not opened:
                    print(f"Could not open a browser. Open {url} manually.", file=sys.stderr, flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping editor server.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
