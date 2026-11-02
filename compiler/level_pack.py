# ==============================================================================
# level_pack.py - Level converters for GEL (Tiled .tmj maps and the legacy level PNG)
# ==============================================================================
# Both paths end in emit_level(), which writes the BG tilemap, the collision map and the
# object table and appends the matching lines to assets.gel / assets.asm.
import json
import os
import re
import sys

# Ids 1..SOLID_TILE_MAX collide in the legacy PNG workflow (and in Tiled maps whose tileset has
# no "solid" properties). Level tiles use BG palette 1 (tilemap attribute bits 10-12).
SOLID_TILE_MAX = 7
LEVEL_PALETTE_BITS = 1 << 10

# Level images are read by RGB colour, so palette order/re-indexing in the editor is irrelevant.
# Any colour not listed (e.g. black) is an empty tile.
LEVEL_COLOR_TO_TILE = {
    (56, 140, 48): 1,    # grass
    (150, 100, 56): 2,   # dirt
    (104, 68, 36): 3,    # brick
    (255, 255, 255): 8,  # cloud
    (255, 220, 80): 9,   # flower
}

# Object types seen so far, in order of first appearance (0 means "none"). Each becomes
# `#define OBJ_<NAME> <id>` in assets.gel, shared by all levels.
OBJECT_TYPES = {}
OBJECT_SIZE = 6   # type, x lo, x hi, y lo, y hi, param

# Each level gets a whole LoROM bank ($8000-$FFFF, 32 KB) to itself, so its map, collision
# and objects can be any size that fits and bank 0 stays free for code and small assets.
# The first free bank comes from the build (after the sound banks).
LEVEL_BANK_SIZE = 0x8000
MIN_LEVEL_HEIGHT = 32          # the VRAM tilemap is a 32-row ring buffer
_next_bank = [0x84]


def reset(first_bank=0x84):
    OBJECT_TYPES.clear()
    _next_bank[0] = first_bank


def fail_level(label, message):
    print(f"ERROR: '{label}': {message}.")
    sys.exit(1)


def check_level_size(label, width, height):
    if width not in (32, 64) or height < MIN_LEVEL_HEIGHT:
        fail_level(label, f"must be 32 or 64 tiles wide and at least {MIN_LEVEL_HEIGHT} tiles high "
                          f"(got {width}x{height})")
    # map (2 bytes/tile) + collision (1 byte/tile) + a full object table must fit one bank
    max_h = (LEVEL_BANK_SIZE - 1 - 255 * OBJECT_SIZE) // (width * 3)
    if height > max_h:
        fail_level(label, f"too tall for one ROM bank: at {width} tiles wide the limit is {max_h} tiles "
                          f"(got {height})")


# ---------------- Legacy PNG levels ----------------
def process_png_level(img, asset_name, out_dir, gel_lines, asm_lines, rom_offset):
    """Turn a level image (1 pixel = 1 tile, colour = tile id) into a BG tilemap laid out for
    VRAM plus a 1-byte-per-tile collision map."""
    width, height = img.size
    check_level_size(f"{asset_name}.png", width, height)

    rgba = img.convert("RGBA")
    ids = []
    for y in range(height):
        row = []
        for x in range(width):
            r, g, b, a = rgba.getpixel((x, y))
            if a == 0:
                row.append(0)
            elif (r, g, b) in LEVEL_COLOR_TO_TILE:
                row.append(LEVEL_COLOR_TO_TILE[(r, g, b)])
            elif (r, g, b) == (0, 0, 0):
                row.append(0)
            else:
                fail_level(f"{asset_name}.png",
                           f"pixel ({x},{y}) has unknown colour ({r},{g},{b}); add it to LEVEL_COLOR_TO_TILE")
        ids.append(row)

    return emit_level(ids, None, asset_name, f"{asset_name}.png", out_dir, gel_lines, asm_lines, rom_offset)


# ---------------- Tiled maps ----------------
def tiled_tile_props(tileset, base_dir):
    """Returns (tile size, {local id: solid}) for a Tiled tileset (embedded or external .tsj)."""
    if "source" in tileset:
        path = os.path.join(base_dir, tileset["source"])
        with open(path) as f:
            tileset = json.load(f)
    solid = {}
    for tile in tileset.get("tiles", []):
        for prop in tile.get("properties", []):
            if prop.get("name") == "solid":
                solid[tile["id"]] = bool(prop.get("value"))
    return (tileset.get("tilewidth"), tileset.get("tileheight")), solid


def process_tiled_level(path, asset_name, out_dir, gel_lines, asm_lines, rom_offset):
    """Read a Tiled .tmj map: the first tile layer is the visible map (tile id = id within the
    tileset, so terrain.png tile N is Tiled tile N). Solid tiles come from a boolean tile
    property "solid" in the tileset, and any tile painted on a layer named "collision" is solid
    too. Object layers become the level's object table."""
    base_dir = os.path.dirname(path)
    label = os.path.basename(path)
    with open(path) as f:
        data = json.load(f)
    if data.get("infinite"):
        fail_level(label, "infinite maps are not supported (Map properties > uncheck Infinite)")
    if (data["tilewidth"], data["tileheight"]) != (8, 8):
        fail_level(label, f"tiles must be 8x8 (got {data['tilewidth']}x{data['tileheight']})")
    width, height = data["width"], data["height"]
    check_level_size(label, width, height)

    tilesets = data.get("tilesets", [])
    if not tilesets:
        fail_level(label, "the map has no tileset")
    firstgid = tilesets[0]["firstgid"]
    tile_size, solid_props = tiled_tile_props(tilesets[0], base_dir)
    if tile_size != (8, 8):
        fail_level(label, f"tileset tiles must be 8x8 (got {tile_size[0]}x{tile_size[1]})")

    tile_layer = None
    coll_layer = None
    for layer in data["layers"]:
        if layer["type"] != "tilelayer":
            continue
        if layer["name"].lower() == "collision":
            coll_layer = layer
        elif tile_layer is None:
            tile_layer = layer
    if tile_layer is None:
        fail_level(label, "the map has no tile layer")

    ids, flips = [], []
    for y in range(height):
        row, frow = [], []
        for x in range(width):
            gid = tile_layer["data"][y * width + x]
            flag = gid & 0xC0000000
            gid &= 0x1FFFFFFF
            if gid == 0:
                tile = 0
            else:
                tile = gid - firstgid
                if not 0 <= tile < 1024:
                    fail_level(label, f"tile at ({x},{y}) is outside the first tileset")
            row.append(tile)
            frow.append(((flag >> 31) & 1) << 14 | ((flag >> 30) & 1) << 15)
        ids.append(row)
        flips.append(frow)

    if solid_props:
        solid = [[1 if solid_props.get(ids[y][x]) else 0 for x in range(width)] for y in range(height)]
    else:
        print(f"WARNING: '{label}': tileset has no 'solid' tile properties; "
              f"treating tile ids 1..{SOLID_TILE_MAX} as solid.")
        solid = [[1 if 1 <= ids[y][x] <= SOLID_TILE_MAX else 0 for x in range(width)] for y in range(height)]
    if coll_layer is not None:
        for y in range(height):
            for x in range(width):
                if coll_layer["data"][y * width + x] & 0x1FFFFFFF:
                    solid[y][x] = 1

    return emit_level(ids, solid, asset_name, label, out_dir, gel_lines, asm_lines, rom_offset, flips,
                      read_tiled_objects(data, label))


def read_tiled_objects(data, label):
    """Objects from every object layer: [(type name, x, y, param)]. The Tiled "Class"
    (or "Type") picks the type, falling back to the object's name. x/y is the top-left of
    the object, in pixels. An optional integer custom property "param" is stored as a byte."""
    found = []
    for layer in data["layers"]:
        if layer["type"] != "objectgroup":
            continue
        for obj in layer.get("objects", []):
            name = (obj.get("class") or obj.get("type") or obj.get("name") or "").strip()
            name = re.sub(r"\W+", "_", name).strip("_").upper()
            if not name:
                fail_level(label, f"object {obj['id']} has no Class (or name)")
            x, y = round(obj["x"]), round(obj["y"])
            if obj.get("gid"):
                y -= round(obj.get("height", 0))   # tile objects are anchored at the bottom-left
            param = 0
            for prop in obj.get("properties", []):
                if prop.get("name") == "param":
                    param = int(prop.get("value", 0))
            if not (0 <= x < 65536 and 0 <= y < 65536 and 0 <= param < 256):
                fail_level(label, f"object {obj['id']} ({name}) is out of range (x={x}, y={y}, param={param})")
            found.append((name, x, y, param))
    if len(found) > 255:
        fail_level(label, f"too many objects ({len(found)}, max 255)")
    return found


# ---------------- Output ----------------
def emit_objects(objects, prefix, out_dir, gel_lines, asm_lines, bank, offset):
    """Object table in the level's bank: a count byte followed by OBJECT_SIZE bytes per object."""
    table = bytearray([len(objects)])
    for name, x, y, param in objects:
        if name not in OBJECT_TYPES:
            OBJECT_TYPES[name] = len(OBJECT_TYPES) + 1
            gel_lines.append(f"#define OBJ_{name} {OBJECT_TYPES[name]}")
        table += bytes([OBJECT_TYPES[name]]) + x.to_bytes(2, "little") + y.to_bytes(2, "little") + bytes([param])
    path = os.path.join(out_dir, f"{prefix.lower()}_objects.bin")
    with open(path, "wb") as f:
        f.write(table)
    gel_lines.append(f"#define {prefix}_OBJECT_COUNT {len(objects)}")
    gel_lines.append(f"#define {prefix}_OBJECTS 0x{offset:04X}")
    asm_lines.append(f"org ${bank:02X}{offset:04X}")
    asm_lines.append(f"{prefix}_OBJECTS:")
    asm_lines.append(f'    incbin "{os.path.basename(path)}"\n')
    return offset + len(table)


def emit_level(ids, solid, asset_name, label, out_dir, gel_lines, asm_lines, rom_offset, flips=None,
               objects=()):
    """Write the VRAM tilemap, collision map and object table into the level's own bank. `solid`
    is a grid of 0/1, or None to derive it from the tile ids (the PNG workflow). `flips` holds
    per-tile tilemap flip bits. rom_offset (bank 0) is returned unchanged."""
    prefix = asset_name.upper()
    height, width = len(ids), len(ids[0])
    bank = _next_bank[0]
    _next_bank[0] += 1

    # BG tilemap pages are 32 tiles wide; a 64-wide map is two pages stored one after the other,
    # each `height` rows of 64 bytes, so one row of a page is a single 64-byte DMA.
    tilemap = bytearray()
    for page in range(width // 32):
        for y in range(height):
            for x in range(32):
                tile = ids[y][page * 32 + x]
                flip = flips[y][page * 32 + x] if flips else 0
                tilemap.extend(((tile & 0x3FF) | LEVEL_PALETTE_BITS | flip).to_bytes(2, "little"))

    if solid is None:
        solid = [[1 if 1 <= ids[y][x] <= SOLID_TILE_MAX else 0 for x in range(width)] for y in range(height)]
    collision = bytearray(solid[y][x] for y in range(height) for x in range(width))

    map_file = os.path.join(out_dir, f"{asset_name}_map.bin")
    coll_file = os.path.join(out_dir, f"{asset_name}_coll.bin")
    with open(map_file, "wb") as f:
        f.write(tilemap)
    with open(coll_file, "wb") as f:
        f.write(collision)

    gel_lines.append(f"#define {prefix}_WIDTH {width}")
    gel_lines.append(f"#define {prefix}_HEIGHT {height}")
    gel_lines.append(f"#define {prefix}_ROW_SHIFT {width.bit_length() - 1}")
    gel_lines.append(f"#define {prefix}_WIDTH_PX {width * 8}")
    gel_lines.append(f"#define {prefix}_HEIGHT_PX {height * 8}")
    gel_lines.append(f"#define {prefix}_MAX_CAM_X {width * 8 - 256}")
    gel_lines.append(f"#define {prefix}_MAX_CAM_Y {height * 8 - 224}")
    gel_lines.append(f"#define {prefix}_SC_SIZE {1 if width == 64 else 0}")
    gel_lines.append(f"#define {prefix}_PAGES {width // 32}")
    gel_lines.append(f"#define {prefix}_MAX_TOP {height - 32}")   # highest first row of the VRAM window
    gel_lines.append(f"#define {prefix}_PAGE_BYTES {height * 64}")
    gel_lines.append(f"#define {prefix}_BANK 0x{bank:02X}")

    offset = 0x8000
    for sym, path, data in ((f"{prefix}_MAP", map_file, tilemap), (f"{prefix}_COLL", coll_file, collision)):
        gel_lines.append(f"#define {sym} 0x{offset:04X}")
        asm_lines.append(f"org ${bank:02X}{offset:04X}")
        asm_lines.append(f"{sym}:")
        asm_lines.append(f'    incbin "{os.path.basename(path)}"\n')
        offset += len(data)

    offset = emit_objects(objects, prefix, out_dir, gel_lines, asm_lines, bank, offset)

    print(f"OK level '{label}' -> {width}x{height} tiles in bank ${bank:02X}, "
          f"{len(tilemap)}b map + {len(collision)}b collision, {len(objects)} objects "
          f"({offset - 0x8000} of {LEVEL_BANK_SIZE} bytes)")
    return rom_offset
