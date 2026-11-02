# ==============================================================================
# metasprite_pack.py - Packs layered, animated metasprites (gel-metasprite-v2)
# ==============================================================================
# Input : <name>.metasprite.json written by the GEL Metasprite Editor plus the
#         layer PNGs it references (indexed colour, index 0 = transparent).
# Output: deduplicated 4bpp tiles (flip-aware), per-slot palettes, per-frame
#         entry tables and animation clip tables, plus GEL defines.
#
# Frame table  NAME_MS_FRAMES : db width_in_tiles, dw frame_ptr * frame_count
#   frame      : db entry_count, then per entry 3 bytes: pos, tile, attr
#                pos  = (grid_y << 4) | grid_x   (front-most entry first)
#                attr = (palette << 1) | 0x40 hflip | 0x80 vflip
# Clip table   NAME_MS_ANIMS  : dw clip_ptr * clip_count
#   clip       : db step_count, loop, then (frame, ticks) per step
import json
import os
import re
import sys
from PIL import Image

FORMAT = "gel-metasprite-v2"
MAX_GRID = 16
MAX_TILES = 256


def load_projects(folder):
    """Return [(path, data)] for every v2 metasprite project in the folder."""
    found = []
    for fn in sorted(os.listdir(folder)):
        if not fn.lower().endswith(".metasprite.json"):
            continue
        path = os.path.join(folder, fn)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            print(f"ERROR: cannot read '{fn}': {e}")
            sys.exit(1)
        if data.get("format") == FORMAT:
            found.append((path, data))
    return found


def consumed_sources(projects):
    out = set()
    for _, data in projects:
        for layer in data.get("layers", []):
            out.add(layer["source"].lower())
    return out


def bgr555_palette(img):
    raw = img.getpalette()[:48]
    out = bytearray(2)
    for i in range(1, 16):
        if i * 3 + 2 < len(raw):
            r, g, b = raw[i * 3:i * 3 + 3]
            v = ((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3)
        else:
            v = 0
        out.extend(v.to_bytes(2, "little"))
    return bytes(out)


def encode_4bpp(px):
    out = bytearray(32)
    for row in range(8):
        b = [0, 0, 0, 0]
        for col in range(8):
            c = px[row * 8 + col] & 0x0F
            for plane in range(4):
                b[plane] |= ((c >> plane) & 1) << (7 - col)
        out[row * 2], out[row * 2 + 1] = b[0], b[1]
        out[16 + row * 2], out[17 + row * 2] = b[2], b[3]
    return bytes(out)


def flip_h(px):
    return tuple(px[r * 8 + 7 - c] for r in range(8) for c in range(8))


def flip_v(px):
    return tuple(px[(7 - r) * 8 + c] for r in range(8) for c in range(8))


def canonical(px):
    """Smallest of the four flip variants plus the OAM flip bits that restore px."""
    variants = ((px, 0), (flip_h(px), 0x40), (flip_v(px), 0x80), (flip_v(flip_h(px)), 0xC0))
    return min(variants, key=lambda v: v[0])


def ident(name):
    s = re.sub(r"[^0-9A-Za-z]+", "_", name).strip("_").upper()
    return s if s and not s[0].isdigit() else "_" + s


def pack_project(path, data, assets_dir, out_dir, gel_lines, asm_lines, rom_offset):
    name = data["name"]
    prefix = ident(name) + "_MS"
    label = os.path.basename(path)

    def fail(msg):
        print(f"ERROR: {label}: {msg}")
        sys.exit(1)

    layers = data.get("layers", [])
    frames = data.get("frames", [])
    if not layers or not frames:
        fail("needs at least one layer and one frame")

    images, pal_bytes = [], {}
    for layer in layers:
        src = os.path.join(assets_dir, layer["source"])
        if not os.path.exists(src):
            fail(f"layer '{layer['name']}' source '{layer['source']}' not found in assets/")
        img = Image.open(src)
        if img.mode != "P":
            fail(f"'{layer['source']}' must be an indexed (palette) PNG, got {img.mode}")
        images.append(img)
        slot = int(layer.get("palette", 0))
        if not 0 <= slot <= 7:
            fail(f"layer '{layer['name']}' palette slot must be 0-7")
        pal = bgr555_palette(img)
        if slot in pal_bytes and pal_bytes[slot] != pal:
            print(f"WARNING: {label}: palette slot {slot} is used by layers with different "
                  f"palettes; the first one wins")
        pal_bytes.setdefault(slot, pal)

    def cell_pixels(li, tx, ty, hf, vf):
        img = images[li]
        w, h = img.size
        if (tx + 1) * 8 > w or (ty + 1) * 8 > h or tx < 0 or ty < 0:
            fail(f"layer '{layers[li]['name']}' has a part outside its image ({tx},{ty})")
        px = tuple(img.getpixel((tx * 8 + c, ty * 8 + r)) for r in range(8) for c in range(8))
        if hf:
            px = flip_h(px)
        if vf:
            px = flip_v(px)
        return px

    # Build per-frame cell lists (back to front) at stage coordinates.
    raw_frames = []
    for fr in frames:
        cells = []
        for li, parts in enumerate(fr["layers"]):
            slot = int(layers[li].get("palette", 0))
            for p in parts:
                px = cell_pixels(li, p["sheetX"], p["sheetY"], p.get("hflip"), p.get("vflip"))
                if any(px):
                    cells.append((p["x"], p["y"], slot, px))
        raw_frames.append(cells)

    all_cells = [c for f in raw_frames for c in f]
    if not all_cells:
        fail("no visible parts")
    min_x = min(c[0] for c in all_cells)
    min_y = min(c[1] for c in all_cells)
    max_x = max(c[0] for c in all_cells) + 8
    max_y = max(c[1] for c in all_cells) + 8
    width_t, height_t = (max_x - min_x) // 8, (max_y - min_y) // 8
    if width_t > MAX_GRID or height_t > MAX_GRID:
        fail(f"sprite box is {width_t}x{height_t} tiles; the limit is {MAX_GRID}x{MAX_GRID}")

    tile_index = {}
    tile_data = []
    out_frames = []
    raw_entries = 0
    for cells in raw_frames:
        by_pos = {}
        for x, y, slot, px in cells:
            by_pos.setdefault((x, y), []).append((slot, px))
        entries = []  # back to front
        for (x, y), stack in by_pos.items():
            merged = []
            for slot, px in stack:
                raw_entries += 1
                if merged and merged[-1][0] == slot:
                    top = merged[-1][1]
                    merged[-1] = (slot, tuple(p if p else t for p, t in zip(px, top)))
                else:
                    merged.append((slot, px))
            # A merged entry must stay in the same relative order; fold back-to-front.
            for order, (slot, px) in enumerate(merged):
                entries.append((x, y, order, slot, px))
        entries.sort(key=lambda e: (e[2],))
        out = []
        for x, y, order, slot, px in entries:
            canon, bits = canonical(px)
            if canon not in tile_index:
                tile_index[canon] = len(tile_data)
                tile_data.append(canon)
            gx, gy = (x - min_x) // 8, (y - min_y) // 8
            out.append(((gy << 4) | gx, tile_index[canon], (slot << 1) | bits))
        out.reverse()  # front-most first so it wins the low OAM slots
        out_frames.append(out)
        if len(out) > 128:
            fail("a frame needs more than 128 sprites")

    if len(tile_data) > MAX_TILES:
        fail(f"needs {len(tile_data)} unique tiles; the limit is {MAX_TILES}")

    tiles_bin = b"".join(encode_4bpp(t) for t in tile_data)
    tiles_file = f"{prefix.lower()}_tiles.bin"
    with open(os.path.join(out_dir, tiles_file), "wb") as f:
        f.write(tiles_bin)

    # Peak sprites on any one scanline (a sprite covers 8 rows).
    peak_line = 0
    for ent in out_frames:
        lines = {}
        for pos, _, _ in ent:
            gy = pos >> 4
            for r in range(8):
                lines[gy * 8 + r] = lines.get(gy * 8 + r, 0) + 1
        peak_line = max(peak_line, max(lines.values(), default=0))

    def emit(line):
        asm_lines.append(line)

    start = rom_offset
    emit(f"org ${rom_offset:06X}")
    for slot in sorted(pal_bytes):
        pal_file = f"{prefix.lower()}_pal{slot}.pal"
        with open(os.path.join(out_dir, pal_file), "wb") as f:
            f.write(pal_bytes[slot])
        emit(f"{prefix}_PAL{slot}:")
        emit(f'    incbin "{pal_file}"')
        rom_offset += 32
    emit(f"{prefix}_TILES:")
    emit(f'    incbin "{tiles_file}"')
    rom_offset += len(tiles_bin)

    emit(f"{prefix}_FRAMES:")
    emit(f"    db {width_t}")
    emit("    dw " + ", ".join(f"{prefix}_F{i}" for i in range(len(out_frames))))
    rom_offset += 1 + 2 * len(out_frames)
    for i, ent in enumerate(out_frames):
        emit(f"{prefix}_F{i}:")
        emit(f"    db {len(ent)}")
        for pos, tile, attr in ent:
            emit(f"    db ${pos:02X}, ${tile:02X}, ${attr:02X}")
        rom_offset += 1 + 3 * len(ent)

    clips = data.get("clips", [])
    frame_names = [fr.get("name", f"f{i}") for i, fr in enumerate(frames)]
    if clips:
        emit(f"{prefix}_ANIMS:")
        emit("    dw " + ", ".join(f"{prefix}_A{i}" for i in range(len(clips))))
        rom_offset += 2 * len(clips)
        for i, clip in enumerate(clips):
            steps = clip.get("steps", [])
            if not steps:
                fail(f"clip '{clip['name']}' has no steps")
            if clip.get("pingpong") and len(steps) >= 3:
                steps = steps + steps[-2:0:-1]
            emit(f"{prefix}_A{i}:")
            emit(f"    db {len(steps)}, {1 if clip.get('loop', True) else 0}")
            for s in steps:
                fi, ticks = int(s["frame"]), max(1, min(255, int(s["ticks"])))
                if not 0 <= fi < len(frames):
                    fail(f"clip '{clip['name']}' uses missing frame {fi}")
                emit(f"    db {fi}, {ticks}")
            rom_offset += 2 + 2 * len(steps)
    emit("")

    ax, ay = data["anchor"]["x"], data["anchor"]["y"]
    hx, hy = data["hitboxAnchor"]["x"], data["hitboxAnchor"]["y"]
    off_x = hx - min_x
    off_x_mirror = hx - 2 * ax + min_x + width_t * 8
    off_y = hy - min_y
    # Per-frame offsets: a frame's own sprite anchor pins that point of the art to the
    # project anchor, so each frame shifts by (frame anchor - project anchor).
    normal_offs, mirror_offs, y_offs = [], [], []
    for fr in frames:
        fa = fr.get("anchor") or data["anchor"]
        dx, dy = int(fa["x"]) - ax, int(fa["y"]) - ay
        normal_offs.append((off_x + dx) & 0xFFFF)
        mirror_offs.append((hx - 2 * int(fa["x"]) + min_x + width_t * 8 + dx) & 0xFFFF)
        y_offs.append((off_y + dy) & 0xFFFF)
    d = gel_lines.append
    for tag, vals in (("NORMAL_X", normal_offs), ("MIRROR_X", mirror_offs), ("Y", y_offs)):
        emit(f"{prefix}_{tag}:")
        emit("    dw " + ", ".join(str(v) for v in vals))
        rom_offset += 2 * len(vals)
        d(f"extern {prefix}_{tag};")
    d(f"#define {prefix}_FRAME_COUNT {len(out_frames)}")
    d(f"#define {prefix}_TILE_COUNT {len(tile_data)}")
    d(f"#define {prefix}_BYTE_LEN {len(tiles_bin)}")
    d(f"#define {prefix}_WIDTH {width_t * 8}")
    d(f"#define {prefix}_HEIGHT {height_t * 8}")
    d(f"#define {prefix}_OFF_X_NORMAL {off_x}")
    d(f"#define {prefix}_OFF_X_MIRROR {off_x_mirror}")
    d(f"#define {prefix}_OFF_Y {off_y}")
    for slot in sorted(pal_bytes):
        d(f"extern {prefix}_PAL{slot};")
    d(f"extern {prefix}_TILES;")
    d(f"extern {prefix}_FRAMES;")
    for i, n in enumerate(frame_names):
        d(f"#define {prefix}_FRAME_{ident(n)} {i}")
    if clips:
        d(f"extern {prefix}_ANIMS;")
        d(f"#define {prefix}_ANIM_COUNT {len(clips)}")
        for i, c in enumerate(clips):
            d(f"#define {prefix}_ANIM_{ident(c['name'])} {i}")

    sprites = [len(e) for e in out_frames]
    print(f"OK metasprite '{name}': {len(layers)} layers, {len(frames)} frames, "
          f"{len(tile_data)} unique tiles ({len(tiles_bin)}b), sprites/frame "
          f"{min(sprites)}-{max(sprites)} (from {raw_entries // len(frames)} raw), "
          f"peak {peak_line}/32 per scanline, {rom_offset - start}b ROM total")
    return rom_offset
