"""Read and update the supported GEL subset of Tiled JSON maps."""

import hashlib
import json
import math
import re
from pathlib import Path

import level_pack
import object_definitions


def filename(name, suffix):
    if (not isinstance(name, str) or Path(name).name != name or
            not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or not name.lower().endswith(suffix)):
        raise ValueError(f"Invalid {suffix} filename.")
    return name


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path.name}: invalid JSON: {error}") from error


def hash_bytes(data):
    return hashlib.sha256(data).hexdigest()


def maps(assets):
    return sorted(p.name for p in Path(assets).glob("level*.tmj") if p.is_file())


def load(assets, project, name):
    assets = Path(assets)
    path = assets / filename(name, ".tmj")
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name}: invalid UTF-8 JSON: {error}") from error
    if not isinstance(data, dict):
        raise ValueError("The map must be a Tiled JSON object.")
    width, height = data.get("width"), data.get("height")
    if (type(width) is not int or type(height) is not int or data.get("infinite") or
            data.get("orientation") != "orthogonal" or
            (data.get("tilewidth"), data.get("tileheight")) != (8, 8)):
        raise ValueError("Only finite orthogonal maps with 8x8 tiles are supported.")
    error = level_pack.level_size_error(width, height)
    if error:
        raise ValueError(error)
    tilesets = data.get("tilesets")
    if not isinstance(tilesets, list) or not tilesets or not isinstance(tilesets[0], dict):
        raise ValueError("The map needs a first tileset.")
    reference = tilesets[0]
    firstgid = reference.get("firstgid")
    if type(firstgid) is not int or firstgid <= 0:
        raise ValueError("The first tileset needs a valid firstgid.")
    if "source" in reference:
        tileset = read_json(assets / filename(reference["source"], ".tsj"))
    else:
        tileset = reference
    if not isinstance(tileset, dict) or (tileset.get("tilewidth"), tileset.get("tileheight")) != (8, 8):
        raise ValueError("The first tileset must use 8x8 tiles.")
    image = filename(tileset.get("image"), ".png")
    if not (assets / image).is_file():
        raise ValueError(f"Tileset image '{image}' not found.")
    count, columns = tileset.get("tilecount"), tileset.get("columns")
    if type(count) is not int or not 1 <= count <= 1024 or type(columns) is not int or columns < 1:
        raise ValueError("The first tileset needs a tilecount (1-1024) and columns.")
    layers = data.get("layers")
    if not isinstance(layers, list):
        raise ValueError("The map needs a layers array.")
    primary = None
    collision = None
    warnings = []
    for layer in layers:
        if not isinstance(layer, dict):
            raise ValueError("Invalid map layer.")
        if layer.get("type") == "tilelayer":
            is_collision = str(layer.get("name", "")).lower() == "collision"
            if not is_collision and primary is not None:
                warnings.append(f"Layer '{layer.get('name')}' is not used by the GEL level packer.")
                continue
            cells = layer.get("data")
            if (not isinstance(cells, list) or len(cells) != width * height or
                    any(type(cell) is not int or not 0 <= cell <= 0xFFFFFFFF for cell in cells)):
                raise ValueError(f"Layer '{layer.get('name')}' must contain uncompressed tile data.")
            if is_collision:
                if collision is not None:
                    warnings.append("Only the last collision layer is used by the GEL level packer.")
                collision = layer
            else:
                primary = layer
    if primary is None:
        raise ValueError("The map needs a visible tile layer.")
    editable = [layer["id"] for layer in (primary, collision) if layer is not None and "id" in layer]
    if len(editable) != 1 + (collision is not None):
        raise ValueError("Editable layers need numeric IDs.")
    if any(type(layer_id) is not int for layer_id in editable) or len(set(editable)) != len(editable):
        raise ValueError("Editable layers need unique numeric IDs.")
    if len(tilesets) > 1:
        warnings.append("GEL uses only the first tileset.")
    if any((cell & 0x20000000) for cell in primary["data"]):
        warnings.append("Diagonally flipped tiles are not rendered as diagonal flips by GEL.")
    if any((cell & 0x1FFFFFFF) and not firstgid <= (cell & 0x1FFFFFFF) < firstgid + count
           for cell in primary["data"]):
        warnings.append("Some tile IDs are outside the first tileset and cannot be previewed.")
    sprites = []
    for sprite_path in sorted(assets.glob("*.metasprite.json"), key=lambda p: p.name):
        sprite = read_json(sprite_path)
        if isinstance(sprite, dict) and sprite.get("format") == "gel-metasprite-v2":
            sprites.append({"id": len(sprites), "file": sprite_path.name, "data": sprite})
    definitions = object_definitions.load(Path(project) / "objects.json")
    return {"name": name, "project_path": str(Path(project).resolve()), "hash": hash_bytes(raw), "map": data, "tileset": tileset,
            "firstgid": firstgid, "image": image, "editable_layers": editable,
            "max_height": level_pack.MAX_LEVEL_HEIGHT, "warnings": warnings, "objects": definitions["objects"], "sprites": sprites}


def apply(assets, project, payload):
    if not isinstance(payload, dict):
        raise ValueError("Expected map changes.")
    if payload.get("project_path") != str(Path(project).resolve()):
        raise ValueError("The active project changed; reload the editor before saving.")
    loaded = load(assets, project, payload.get("name"))
    if payload.get("hash") != loaded["hash"]:
        raise ValueError("The map changed on disk; reload before saving.")
    data = loaded["map"]
    tiles = payload.get("tiles", [])
    objects = payload.get("objects", [])
    if not isinstance(tiles, list) or not isinstance(objects, list):
        raise ValueError("Expected tile and object change lists.")
    layers = {layer["id"]: layer for layer in data["layers"] if layer.get("id") in loaded["editable_layers"]}
    firstgid = loaded["firstgid"]
    count = loaded["tileset"]["tilecount"]
    for change in tiles:
        if not isinstance(change, dict):
            raise ValueError("Invalid tile change.")
        layer_id, index, gid = change.get("layer"), change.get("index"), change.get("gid")
        if (type(layer_id) is not int or layer_id not in layers or type(index) is not int or
                not 0 <= index < data["width"] * data["height"] or type(gid) is not int or
                not (gid == 0 or firstgid <= (gid & 0x1FFFFFFF) < firstgid + count)):
            raise ValueError("Invalid tile layer, position, or tileset ID.")
        layers[layer_id]["data"][index] = gid
    by_id = {}
    for layer in data["layers"]:
        if layer.get("type") == "objectgroup":
            for obj in layer.get("objects", []):
                if obj.get("id") in by_id:
                    raise ValueError("Duplicate object ID in map.")
                by_id[obj.get("id")] = obj
    for change in objects:
        if not isinstance(change, dict):
            raise ValueError("Invalid object change.")
        obj_id, x, y, param = (change.get(key) for key in ("id", "x", "y", "param"))
        if (type(obj_id) is not int or obj_id not in by_id or
                type(x) not in (int, float) or type(y) not in (int, float) or
                not math.isfinite(x) or not math.isfinite(y) or not 0 <= x <= 65535 or
                not 0 <= y <= 65535 or type(param) is not int or not 0 <= param <= 255):
            raise ValueError("Object position must be 0-65535 and param must be 0-255.")
        obj = by_id[obj_id]
        if obj.get("gid") and y - obj.get("height", 0) < 0:
            raise ValueError(f"Object {obj_id} has a negative game-space Y position.")
        obj["x"], obj["y"] = x, y
        props = obj.setdefault("properties", [])
        if not isinstance(props, list) or any(not isinstance(prop, dict) for prop in props):
            raise ValueError(f"Object {obj_id} has invalid properties.")
        existing = [prop for prop in props if prop.get("name") == "param"]
        if len(existing) > 1:
            raise ValueError(f"Object {obj_id} has duplicate param properties.")
        if existing:
            existing[0]["value"] = param
            existing[0]["type"] = "int"
        else:
            props.append({"name": "param", "type": "int", "value": param})
    return Path(assets) / payload["name"], (json.dumps(data, indent=1, ensure_ascii=False) + "\n").encode("utf-8")