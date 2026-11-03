#!/usr/bin/env python3
"""Local file server for the GEL metasprite editor."""

import argparse
import json
import mimetypes
import os
import re
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


EDITOR_DIR = Path(__file__).resolve().parent
MANIFEST_NAME = "metasprite-project.json"
PROJECT_FORMAT = "gel-metasprite-project-v1"
MAX_PROJECT_BYTES = 8 * 1024 * 1024
MAX_IMAGE_BYTES = 32 * 1024 * 1024


def safe_filename(name, suffix):
    return (
        isinstance(name, str)
        and name not in ("", ".", "..")
        and Path(name).name == name
        and re.fullmatch(r"[A-Za-z0-9_.-]+", name) is not None
        and name.lower().endswith(suffix)
    )


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


def convert_bmp(path):
    from PIL import Image

    output = path.with_suffix(path.suffix + ".png")
    if output.is_file() and output.stat().st_mtime_ns >= path.stat().st_mtime_ns:
        return output
    with Image.open(path) as source:
        if source.mode == "P" and len(set(source.tobytes())) <= 16:
            converted = source.copy()
            converted.save(output, format="PNG", transparency=0)
            return output

        rgba = source.convert("RGBA")
        quantized = rgba.convert("RGB").quantize(colors=15)
        alpha = rgba.getchannel("A").tobytes()
        indices = bytes(
            0 if alpha[i] < 128 else pixel + 1
            for i, pixel in enumerate(quantized.tobytes())
        )
        converted = Image.frombytes("P", source.size, indices)
        palette = [0, 0, 0] + (quantized.getpalette() or [])[:45]
        palette.extend([0] * (768 - len(palette)))
        converted.putpalette(palette)
        converted.save(output, format="PNG", transparency=0)
    return output


def project_catalog(assets_dir):
    manifest_path = assets_dir / MANIFEST_NAME
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            not isinstance(manifest, dict)
            or manifest.get("format") != PROJECT_FORMAT
            or not isinstance(manifest.get("projects"), list)
        ):
            raise ValueError(f"{MANIFEST_NAME} has an unsupported format.")
        projects = [
            name for name in manifest["projects"]
            if safe_filename(name, ".metasprite.json") and (assets_dir / name).is_file()
        ]
    else:
        projects = []

    projects.extend(
        path.name for path in assets_dir.glob("*.metasprite.json")
        if path.is_file() and path.name not in projects
    )
    projects = sorted(set(projects), key=str.casefold)
    manifest = {"format": PROJECT_FORMAT, "projects": projects}
    encoded = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    if not manifest_path.exists() or manifest_path.read_bytes() != encoded:
        atomic_write(manifest_path, encoded)

    for path in sorted(assets_dir.glob("*.bmp")):
        if path.is_file():
            convert_bmp(path)
    images = sorted(
        path.name for path in assets_dir.glob("*.png")
        if path.is_file()
    )
    images.extend(
        sorted(
            path.name for path in assets_dir.glob("*.bmp.png")
            if path.is_file() and path.name not in images
        )
    )
    return {"manifest": manifest, "projects": projects, "images": images}


class EditorHandler(SimpleHTTPRequestHandler):
    server_version = "GELMetaspriteEditor/1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(EDITOR_DIR), **kwargs)

    @property
    def assets_dir(self):
        return self.server.assets_dir

    def send_json(self, status, value):
        payload = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def api_name(self, prefix, suffix):
        raw_name = unquote(self.path[len(prefix):])
        if not safe_filename(raw_name, suffix):
            raise ValueError("Invalid filename.")
        return raw_name

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/catalog":
                self.send_json(200, project_catalog(self.assets_dir))
                return
            if path.startswith("/api/projects/"):
                name = self.api_name("/api/projects/", ".metasprite.json")
                self.send_file(self.assets_dir / name, "application/json; charset=utf-8")
                return
            if path.startswith("/api/assets/"):
                name = self.api_name("/api/assets/", ".png")
                self.send_file(self.assets_dir / name, "image/png")
                return
            if path.startswith("/api/"):
                self.send_error(404)
                return
            super().do_GET()
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error)})
        except OSError as error:
            self.send_json(404, {"error": str(error)})

    def send_file(self, path, content_type):
        if not path.is_file():
            self.send_json(404, {"error": f"File not found: {path.name}"})
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type or mimetypes.guess_type(path.name)[0])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path.startswith("/api/projects/"):
                name = self.api_name("/api/projects/", ".metasprite.json")
                payload = self.read_body(MAX_PROJECT_BYTES)
                project = json.loads(payload.decode("utf-8"))
                if not isinstance(project, dict) or project.get("format") != "gel-metasprite-v2":
                    raise ValueError("Expected a gel-metasprite-v2 project.")
                atomic_write(self.assets_dir / name, (json.dumps(project, indent=1) + "\n").encode("utf-8"))
                catalog = project_catalog(self.assets_dir)
                self.send_json(200, catalog)
                return
            if path.startswith("/api/assets/"):
                name = self.api_name("/api/assets/", ".png")
                payload = self.read_body(MAX_IMAGE_BYTES)
                if not payload.startswith(b"\x89PNG\r\n\x1a\n"):
                    raise ValueError("Layer assets must be PNG files.")
                atomic_write(self.assets_dir / name, payload)
                self.send_json(201, {"name": name})
                return
            self.send_error(404)
        except (ValueError, json.JSONDecodeError) as error:
            self.send_json(400, {"error": str(error)})
        except OSError as error:
            self.send_json(500, {"error": str(error)})

    def read_body(self, limit):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Invalid Content-Length.") from error
        if length <= 0 or length > limit:
            raise ValueError("Request body is empty or too large.")
        return self.rfile.read(length)


class EditorServer(ThreadingHTTPServer):
    def __init__(self, address, assets_dir):
        self.assets_dir = Path(assets_dir).resolve()
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        super().__init__(address, EditorHandler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "assets_dir", nargs="?",
        default=EDITOR_DIR.parents[1] / "game" / "assets",
        help="directory containing metasprite projects and source images",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        parser.error("The editor server must bind to loopback only.")
    server = EditorServer((args.host, args.port), args.assets_dir)
    print(f"Metasprite editor: http://{args.host}:{args.port}/", flush=True)
    print(f"Assets folder: {server.assets_dir}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping metasprite editor.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
