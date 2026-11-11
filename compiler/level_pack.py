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
TILE_SIZE = 8
NEAR_SLOPE_CODE = 0x40
# A slope cell is 0x80 | index into the level's slope table. Profiles are authored as a signed
# (start, delta) pair in eighths of a pixel, so ramps can be straight lines at any gradient
# (e.g. 1 pixel up per 3 across), then expanded to per-column surface heights when emitted.
SLOPE_SUBPIXELS = 8
SLOPE_RANGE_PX = 16
SLOPE_BIAS_PX = 8
SLOPE_BIAS_EIGHTHS = SLOPE_BIAS_PX * SLOPE_SUBPIXELS
MAX_SLOPE_PROFILES = 128
# The level's slope table stores, per profile, the surface height (plus SLOPE_BIAS_PX so it is
# never negative) of each of the tile's 8 pixel columns, so the game needs one read per lookup.
SLOPE_TABLE_ENTRY_BYTES = TILE_SIZE

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

# Legacy levels use one 32 KiB bank. Large levels store 32x32 chunks in
# separate packed map and collision banks, with metadata in the first bank.
LEVEL_BANK_SIZE = 0x8000
MIN_LEVEL_HEIGHT = 32          # the VRAM tilemap is a 32-row ring buffer
MAX_LEVEL_WIDTH = 4064         # aligned to 32 tiles, within signed pixel coordinates
MAX_LEVEL_HEIGHT = 4095
CHUNK_SIZE = 32
MAP_CHUNKS_PER_BANK = 16
COLL_CHUNKS_PER_BANK = 32
_next_bank = [0x84]
_bank_limit = [0x100]


def reset(first_bank=0x84, bank_limit=0x100):
    OBJECT_TYPES.clear()
    _next_bank[0] = first_bank
    _bank_limit[0] = bank_limit


def fail_level(label, message):
    print(f"ERROR: '{label}': {message}.")
    sys.exit(1)


def legacy_max_height(width):
    return (LEVEL_BANK_SIZE - 1 - 255 * OBJECT_SIZE -
            SLOPE_TABLE_ENTRY_BYTES * MAX_SLOPE_PROFILES) // (width * 3)


def banked_level(width, height):
    return width > 64 or height > legacy_max_height(width)


def level_bank_count(width, height):
    if not banked_level(width, height):
        return 1
    chunks = (width // CHUNK_SIZE) * ((height + CHUNK_SIZE - 1) // CHUNK_SIZE)
    return 1 + ((chunks + MAP_CHUNKS_PER_BANK - 1) // MAP_CHUNKS_PER_BANK +
                (chunks + COLL_CHUNKS_PER_BANK - 1) // COLL_CHUNKS_PER_BANK)


def level_size_error(width, height):
    if (type(width) is not int or type(height) is not int or
            width < 32 or width > MAX_LEVEL_WIDTH or width % 32 or
            height < MIN_LEVEL_HEIGHT or height > MAX_LEVEL_HEIGHT):
        return (f"width must be a multiple of 32 from 32 to {MAX_LEVEL_WIDTH}, and height must be "
                f"from {MIN_LEVEL_HEIGHT} to {MAX_LEVEL_HEIGHT} tiles (got {width}x{height})")
    return None


def check_level_size(label, width, height):
    error = level_size_error(width, height)
    if error:
        fail_level(label, error)
    needed = level_bank_count(width, height)
    remaining = max(0, _bank_limit[0] - _next_bank[0])
    if needed > remaining:
        fail_level(label, f"no addressable ROM bank remains for this level "
                          f"(needs {needed} banks, {remaining} available)")


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
def tiled_tile_props(tileset, base_dir, label):
    """Returns (tile size, {local id: solid}, {local id: (left y, right y)})."""
    if "source" in tileset:
        path = os.path.join(base_dir, tileset["source"])
        with open(path) as f:
            tileset = json.load(f)
    solid = {}
    slope_starts = {}
    slope_ends = {}
    for tile in tileset.get("tiles", []):
        for prop in tile.get("properties", []):
            if prop.get("name") == "solid":
                solid[tile["id"]] = bool(prop.get("value"))
            elif prop.get("name") == "slope_start":
                slope_starts[tile["id"]] = prop.get("value")
            elif prop.get("name") == "slope_end":
                slope_ends[tile["id"]] = prop.get("value")
    slopes = {}
    for tile in slope_starts.keys() | slope_ends.keys():
        if tile not in slope_starts or tile not in slope_ends:
            fail_level(label, f"tile id {tile} needs both 'slope_start' and 'slope_end' properties")
        start, end = slope_starts[tile], slope_ends[tile]
        numbers = (int, float)
        if (type(start) not in numbers or type(end) not in numbers or
                not -SLOPE_RANGE_PX <= start <= SLOPE_RANGE_PX or
                not -SLOPE_RANGE_PX <= end <= SLOPE_RANGE_PX):
            fail_level(label, f"tile id {tile} has invalid slope endpoints ({start!r}, {end!r}); "
                              f"they must be numbers from -{SLOPE_RANGE_PX} to {SLOPE_RANGE_PX}")
        start8, end8 = round(start * SLOPE_SUBPIXELS), round(end * SLOPE_SUBPIXELS)
        if start8 == end8:
            fail_level(label, f"tile id {tile} slope endpoints must be different")
        slopes[tile] = (start8, end8)
    return (tileset.get("tilewidth"), tileset.get("tileheight")), solid, slopes


def _slope_surface(profile, pixel_x):
    """Surface Y (whole pixels, relative to the tile top) of a (start, delta) profile in eighths
    of a pixel at pixel column pixel_x. Mirrors slope_surface_at_x in game.gel."""
    start, delta = profile
    if delta < 0:
        eighths = SLOPE_BIAS_EIGHTHS + start - (((pixel_x + 1) * -delta) >> 3)
    else:
        eighths = SLOPE_BIAS_EIGHTHS + start + (((pixel_x + 1) * delta) >> 3)
    return (eighths >> 3) - SLOPE_BIAS_PX


def _merge_slope_profiles(old, new, label, x, y):
    old_surfaces = [_slope_surface(old, px) for px in range(8)]
    new_surfaces = [_slope_surface(new, px) for px in range(8)]
    if all(new_y <= old_y for new_y, old_y in zip(new_surfaces, old_surfaces)):
        return new
    if all(old_y <= new_y for new_y, old_y in zip(new_surfaces, old_surfaces)):
        return old
    fail_level(label, f"slope profiles cross at collision tile ({x},{y})")


def process_tiled_level(path, asset_name, out_dir, gel_lines, asm_lines, rom_offset):
    """Read a Tiled .tmj map: the first tile layer is the visible map (tile id = id within the
    tileset, so terrain.png tile N is Tiled tile N). Solid tiles use the boolean "solid" property;
    ramps use their "slope_start"/"slope_end" surface coordinates. Any tile painted on a layer
    named "collision" is solid. Object layers become the level's object table."""
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
    tile_size, solid_props, slope_props = tiled_tile_props(tilesets[0], base_dir, label)
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

    ids, flips, hflips = [], [], []
    for y in range(height):
        row, frow, hrow = [], [], []
        for x in range(width):
            gid = tile_layer["data"][y * width + x]
            flag = gid & 0xC0000000
            diagonal_flip = gid & 0x20000000
            gid &= 0x1FFFFFFF
            if gid == 0:
                tile = 0
            else:
                tile = gid - firstgid
                if not 0 <= tile < 1024:
                    fail_level(label, f"tile at ({x},{y}) is outside the first tileset")
            if tile in slope_props and (flag & 0x40000000 or diagonal_flip):
                fail_level(label, f"slope tile at ({x},{y}) cannot be vertically or diagonally flipped")
            row.append(tile)
            frow.append(((flag >> 31) & 1) << 14 | ((flag >> 30) & 1) << 15)
            hrow.append(bool(flag & 0x80000000))
        ids.append(row)
        flips.append(frow)
        hflips.append(hrow)

    slope_table = []
    if solid_props or slope_props:
        solid = []
        slope_cells = {}
        row_eighths = TILE_SIZE * SLOPE_SUBPIXELS
        for y in range(height):
            row = []
            for x in range(width):
                tile = ids[y][x]
                if tile in slope_props:
                    start, end = slope_props[tile]
                    if hflips[y][x]:
                        start, end = end, start
                    delta = end - start
                    if not -128 <= delta <= 127:
                        fail_level(label, f"slope tile at ({x},{y}) changes too steeply "
                                          f"({delta / SLOPE_SUBPIXELS} pixels over one tile)")
                    # The profile lives in the tile row where its surface starts.
                    row_offset = (start - 1) // row_eighths if start > 0 else start // row_eighths
                    target_y = y + row_offset
                    if not 0 <= target_y < height:
                        fail_level(label, f"slope tile at ({x},{y}) extends outside the collision map")
                    start -= row_offset * row_eighths
                    if not 0 <= start <= row_eighths:
                        fail_level(label, f"cannot normalize slope tile at ({x},{y})")
                    profile = (start, delta)
                    old = slope_cells.get((target_y, x))
                    if old is not None:
                        profile = _merge_slope_profiles(old, profile, label, x, y)
                    slope_cells[(target_y, x)] = profile
                    row.append(0)
                elif solid_props:
                    row.append(1 if solid_props.get(tile) else 0)
                else:
                    row.append(1 if 1 <= tile <= SOLID_TILE_MAX else 0)
            solid.append(row)
        for profile in sorted(set(slope_cells.values())):
            slope_table.append(profile)
        if len(slope_table) > MAX_SLOPE_PROFILES:
            fail_level(label, f"too many different slope profiles ({len(slope_table)}; "
                              f"the limit is {MAX_SLOPE_PROFILES})")
        for (y, x), profile in slope_cells.items():
            solid[y][x] = 0x80 | slope_table.index(profile)
    else:
        print(f"WARNING: '{label}': tileset has no 'solid' tile properties; "
              f"treating tile ids 1..{SOLID_TILE_MAX} as solid.")
        solid = [[1 if 1 <= ids[y][x] <= SOLID_TILE_MAX else 0 for x in range(width)] for y in range(height)]
    if coll_layer is not None:
        for y in range(height):
            for x in range(width):
                if coll_layer["data"][y * width + x] & 0x1FFFFFFF:
                    solid[y][x] = 1

    # Empty tiles next to a slope above/below get NEAR_SLOPE_CODE so the runtime can skip the
    # neighbour lookups for plain air.
    marks = [(y, x) for y in range(height) for x in range(width)
             if solid[y][x] == 0 and any(0 <= ny < height and solid[ny][x] & 0x80
                                         for ny in (y - 1, y + 1))]
    for y, x in marks:
        solid[y][x] = NEAR_SLOPE_CODE

    return emit_level(ids, solid, asset_name, label, out_dir, gel_lines, asm_lines, rom_offset, flips,
                      read_tiled_objects(data, label), slope_table)


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
               objects=(), slope_table=()):
    """Write the VRAM tilemap, collision map, slope table and object table into level banks.
    `solid` is a grid of collision codes, or None to derive it from the tile ids (the PNG
    workflow). `slope_table` is a list of (start, delta) eighth-pixel pairs indexed by slope codes.
    `flips` holds per-tile tilemap flip bits. rom_offset (bank 0) is returned unchanged."""
    prefix = asset_name.upper()
    height, width = len(ids), len(ids[0])
    bank = _next_bank[0]
    check_level_size(label, width, height)
    banked = banked_level(width, height)
    bank_count = level_bank_count(width, height)
    _next_bank[0] += bank_count

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
    slopes = bytearray()
    for profile in slope_table:
        slopes.extend(_slope_surface(profile, px) + SLOPE_BIAS_PX for px in range(TILE_SIZE))
    if not slopes:
        slopes.extend(bytes(SLOPE_TABLE_ENTRY_BYTES))

    map_file = os.path.join(out_dir, f"{asset_name}_map.bin")
    coll_file = os.path.join(out_dir, f"{asset_name}_coll.bin")
    slope_file = os.path.join(out_dir, f"{asset_name}_slopes.bin")
    with open(map_file, "wb") as f:
        f.write(tilemap)
    with open(coll_file, "wb") as f:
        f.write(collision)
    with open(slope_file, "wb") as f:
        f.write(slopes)

    gel_lines.append(f"#define {prefix}_WIDTH {width}")
    gel_lines.append(f"#define {prefix}_HEIGHT {height}")
    gel_lines.append(f"#define {prefix}_ROW_SHIFT {width.bit_length() - 1 if width in (32, 64) else 0}")
    gel_lines.append(f"#define {prefix}_WIDTH_PX {width * 8}")
    gel_lines.append(f"#define {prefix}_HEIGHT_PX {height * 8}")
    gel_lines.append(f"#define {prefix}_MAX_CAM_X {width * 8 - 256}")
    gel_lines.append(f"#define {prefix}_MAX_CAM_Y {height * 8 - 224}")
    gel_lines.append(f"#define {prefix}_SC_SIZE {1 if width >= 64 else 0}")
    gel_lines.append(f"#define {prefix}_BANKED {int(banked)}")
    gel_lines.append(f"#define {prefix}_PAGES {width // 32}")
    gel_lines.append(f"#define {prefix}_MAX_TOP {height - 32}")   # highest first row of the VRAM window
    gel_lines.append(f"#define {prefix}_PAGE_BYTES {0 if banked else height * 64}")
    gel_lines.append(f"#define {prefix}_BANK 0x{bank:02X}")

    offset = 0x8000
    if banked:
        chunks = (width // 32) * ((height + 31) // 32)
        map_banks = (chunks + MAP_CHUNKS_PER_BANK - 1) // MAP_CHUNKS_PER_BANK
        chunk_map = bytearray()
        chunk_coll = bytearray()
        for chunk_y in range((height + 31) // 32):
            for chunk_x in range(width // 32):
                for row in range(32):
                    y = chunk_y * 32 + row
                    for col in range(32):
                        x = chunk_x * 32 + col
                        if y < height:
                            tile = ids[y][x]
                            flip = flips[y][x] if flips else 0
                            chunk_map.extend(((tile & 0x3FF) | LEVEL_PALETTE_BITS | flip).to_bytes(2, "little"))
                            chunk_coll.append(solid[y][x])
                        else:
                            chunk_map.extend(b"\0\0")
                            chunk_coll.append(1)
        chunk_map_file = os.path.join(out_dir, f"{asset_name}_map_chunks.bin")
        chunk_coll_file = os.path.join(out_dir, f"{asset_name}_coll_chunks.bin")
        with open(chunk_map_file, "wb") as f:
            f.write(chunk_map)
        with open(chunk_coll_file, "wb") as f:
            f.write(chunk_coll)
        gel_lines.append(f"#define {prefix}_MAP_BANK 0x{bank + 1:02X}")
        gel_lines.append(f"#define {prefix}_COLL_BANK 0x{bank + 1 + map_banks:02X}")
        gel_lines.append(f"#define {prefix}_MAP 0x8000")
        gel_lines.append(f"#define {prefix}_COLL 0x8000")
        for base, path, data in ((bank + 1, chunk_map_file, chunk_map),
                                 (bank + 1 + map_banks, chunk_coll_file, chunk_coll)):
            for start in range(0, len(data), LEVEL_BANK_SIZE):
                end = min(start + LEVEL_BANK_SIZE, len(data))
                asm_lines.append(f"org ${base + start // LEVEL_BANK_SIZE:02X}8000")
                asm_lines.append(f'    incbin "{os.path.basename(path)}":${start:X}..${end:X}')
        parts = ((f"{prefix}_SLOPES", slope_file, slopes),)
    else:
        gel_lines.append(f"#define {prefix}_MAP_BANK 0x{bank:02X}")
        gel_lines.append(f"#define {prefix}_COLL_BANK 0x{bank:02X}")
        parts = ((f"{prefix}_MAP", map_file, tilemap), (f"{prefix}_COLL", coll_file, collision),
                 (f"{prefix}_SLOPES", slope_file, slopes))
    for sym, path, data in parts:
        gel_lines.append(f"#define {sym} 0x{offset:04X}")
        asm_lines.append(f"org ${bank:02X}{offset:04X}")
        asm_lines.append(f"{sym}:")
        asm_lines.append(f'    incbin "{os.path.basename(path)}"\n')
        offset += len(data)

    offset = emit_objects(objects, prefix, out_dir, gel_lines, asm_lines, bank, offset)

    detail = "metadata " if banked else ""
    print(f"OK level '{label}' -> {width}x{height} tiles in {bank_count} bank(s) from ${bank:02X}, "
          f"{len(tilemap)}b map + {len(collision)}b collision, {len(objects)} objects "
          f"({detail}{offset - 0x8000} of {LEVEL_BANK_SIZE} bytes)")
    return rom_offset
