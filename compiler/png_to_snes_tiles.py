# ==============================================================================
# png_to_snes_tiles.py - Automated Folder-Based Asset Pipeline for GEL
# ==============================================================================
import sys
import os
import glob
import json
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import metasprite_pack
import level_pack   # level*.tmj (Tiled) and the legacy level*.png

ROM_ASSET_LIMIT = 0xFFC0  # interrupt vectors / header start here
ASSETS_DIR = "assets"
GEL_HEADER = "assets.gel"
ASM_HEADER = "assets.asm"

def rgb_to_bgr555(r, g, b):
    r_5 = (r >> 3) & 0x1F
    g_5 = (g >> 3) & 0x1F
    b_5 = (b >> 3) & 0x1F
    return (b_5 << 10) | (g_5 << 5) | r_5

def encode_4bpp_8x8(tile_pixels):
    tile_bytes = bytearray(32)
    for row in range(8):
        b0, b1, b2, b3 = 0, 0, 0, 0
        for col in range(8):
            color_idx = tile_pixels[row * 8 + col] & 0x0F
            bit_shift = 7 - col
            b0 |= ((color_idx >> 0) & 1) << bit_shift
            b1 |= ((color_idx >> 1) & 1) << bit_shift
            b2 |= ((color_idx >> 2) & 1) << bit_shift
            b3 |= ((color_idx >> 3) & 1) << bit_shift
        tile_bytes[row * 2]     = b0
        tile_bytes[row * 2 + 1] = b1
        tile_bytes[16 + row * 2]     = b2
        tile_bytes[16 + row * 2 + 1] = b3
    return tile_bytes

def process_asset_folder(folder_path, out_dir, first_level_bank=0x84, asset_start=0xC000):
    png_files = glob.glob(os.path.join(folder_path, "*.png"))
    if not png_files:
        print(f"WARNING: No PNG files found in '{folder_path}/'.")
        return

    gel_lines = ["// Auto-generated asset header\n"]
    level_pack.reset(first_level_bank)
    asm_lines = ["; Auto-generated asset binary inclusions\n"]

    # Assets live high in bank $00 (DMA source bank is 0) so code can grow from $8000.
    current_rom_bank_offset = asset_start

    # Layer PNGs referenced by a metasprite project are packed through it (deduplicated),
    # so they are not also emitted as standalone assets.
    projects = metasprite_pack.load_projects(folder_path)
    consumed = metasprite_pack.consumed_sources(projects)
    infos, next_pal = [], 0
    for path, data in projects:
        info = {}
        current_rom_bank_offset = metasprite_pack.pack_project(
            path, data, folder_path, out_dir, gel_lines, asm_lines, current_rom_bank_offset,
            pal_base=next_pal, info=info)
        next_pal += len(info['slots'])
        infos.append(info)
    metasprite_pack.write_loader(infos, out_dir)

    # A Tiled map replaces the level PNG of the same name.
    tiled_levels = {}
    for tmj in sorted(glob.glob(os.path.join(folder_path, "level*.tmj"))):
        name = os.path.splitext(os.path.basename(tmj))[0]
        tiled_levels[name.lower()] = tmj
        current_rom_bank_offset = level_pack.process_tiled_level(
            tmj, name, out_dir, gel_lines, asm_lines, current_rom_bank_offset)

    for png_path in png_files:
        if os.path.basename(png_path).lower() in consumed:
            continue
        asset_name = os.path.splitext(os.path.basename(png_path))[0]
        if asset_name.lower().startswith("level"):
            if asset_name.lower() in tiled_levels:
                continue
            current_rom_bank_offset = level_pack.process_png_level(
                Image.open(png_path), asset_name, out_dir, gel_lines, asm_lines, current_rom_bank_offset)
            continue
        img = Image.open(png_path).convert("P")
        width, height = img.size

        # 1. BGR555 Palette Extraction
        raw_palette = img.getpalette()[:48]
        is_font = asset_name.lower().startswith("font")
        if is_font:
            # Font sheets use index 1 as background and index 0 as the glyph outline;
            # swap them so the background becomes transparent (index 0).
            raw_palette[0:3], raw_palette[3:6] = raw_palette[3:6], raw_palette[0:3]
        bgr555_bytes = bytearray()
        bgr555_bytes.extend((0).to_bytes(2, "little"))
        for i in range(1, 16):
            if i * 3 < len(raw_palette):
                r, g, b = raw_palette[i*3 : i*3+3]
                color_val = rgb_to_bgr555(r, g, b)
            else:
                color_val = 0
            bgr555_bytes.extend(color_val.to_bytes(2, "little"))

        pal_file = os.path.join(out_dir, f"{asset_name}.pal")
        with open(pal_file, "wb") as f:
            f.write(bgr555_bytes)

        # 2. 4bpp Tile Slicing (bounding box of non-empty tiles, row-major, so
        #    the tile grid stays regular for metasprites)
        tiles_x, tiles_y = width // 8, height // 8

        def tile_pixels(tx, ty):
            return [img.getpixel((tx * 8 + px, ty * 8 + py)) for py in range(8) for px in range(8)]

        used = [(tx, ty) for ty in range(tiles_y) for tx in range(tiles_x)
                if any(p != 0 for p in tile_pixels(tx, ty))]
        all_tile_bytes = bytearray()
        tiles_wide = tiles_high = tile_count = 0
        if is_font:
            # Grid sheet: 8x8 glyph cells on a 10px pitch with a 1px margin. Tile 0 is
            # left blank (so a zeroed tilemap is empty); glyph cell N becomes tile N+1.
            swap = {0: 1, 1: 0}
            cols, rows = width // 10, height // 10

            def cell(cx, cy):
                return [swap.get(p, p) for p in
                        (img.getpixel((cx * 10 + 1 + px, cy * 10 + 1 + py))
                         for py in range(8) for px in range(8))]

            cells = [cell(i % cols, i // cols) for i in range(cols * rows)]
            while cells and not any(cells[-1]):
                cells.pop()
            all_tile_bytes.extend(bytes(32))
            for c in cells:
                all_tile_bytes.extend(encode_4bpp_8x8(c))
            tile_count = len(cells) + 1
            tiles_wide = cols
            tiles_high = (len(cells) + cols - 1) // cols
        elif asset_name.lower().startswith("terrain"):
            # Tileset: keep every grid cell (even empty ones) so tile id == grid position.
            for ty in range(tiles_y):
                for tx in range(tiles_x):
                    all_tile_bytes.extend(encode_4bpp_8x8(tile_pixels(tx, ty)))
                    tile_count += 1
            tiles_wide, tiles_high = tiles_x, tiles_y
        elif used:
            min_x, max_x = min(t[0] for t in used), max(t[0] for t in used)
            min_y, max_y = min(t[1] for t in used), max(t[1] for t in used)
            tiles_wide, tiles_high = max_x - min_x + 1, max_y - min_y + 1
            for ty in range(min_y, max_y + 1):
                for tx in range(min_x, max_x + 1):
                    all_tile_bytes.extend(encode_4bpp_8x8(tile_pixels(tx, ty)))
                    tile_count += 1

        bin_file = os.path.join(out_dir, f"{asset_name}.bin")
        with open(bin_file, "wb") as f:
            f.write(all_tile_bytes)

        prefix = asset_name.upper()
        
        # 3. Append constants to assets.gel
        gel_lines.append(f"#define {prefix}_TILE_COUNT {tile_count}")
        gel_lines.append(f"#define {prefix}_TILES_WIDE {tiles_wide}")
        gel_lines.append(f"#define {prefix}_TILES_HIGH {tiles_high}")
        gel_lines.append(f"#define {prefix}_BYTE_LEN   {len(all_tile_bytes)}")
        gel_lines.append(f"extern {prefix}_PALETTE;")
        gel_lines.append(f"extern {prefix}_TILES;")

        # 4. Append incbin directives to assets.asm (using POSIX slashes for cross-platform Asar)
        pal_label = f"{prefix}_PALETTE"
        tiles_label = f"{prefix}_TILES"
        pal_file_posix = os.path.basename(pal_file)
        bin_file_posix = os.path.basename(bin_file)

        asm_lines.append(f"org ${current_rom_bank_offset:06X}")
        asm_lines.append(f"{pal_label}:")
        asm_lines.append(f'    incbin "{pal_file_posix}"\n')
        current_rom_bank_offset += len(bgr555_bytes)

        asm_lines.append(f"{tiles_label}:")
        asm_lines.append(f'    incbin "{bin_file_posix}"\n')
        current_rom_bank_offset += len(all_tile_bytes)

        print(f"OK Processed '{png_path}' -> {asset_name}.pal ({len(bgr555_bytes)}b), {asset_name}.bin ({len(all_tile_bytes)}b)")

    if current_rom_bank_offset > ROM_ASSET_LIMIT:
        print(f"ERROR: assets end at ${current_rom_bank_offset:04X}, past the ${ROM_ASSET_LIMIT:04X} "
              f"limit ({current_rom_bank_offset - ROM_ASSET_LIMIT} bytes too many).")
        sys.exit(1)
    print(f"Assets use {current_rom_bank_offset - asset_start} bytes "
          f"({ROM_ASSET_LIMIT - current_rom_bank_offset} free).")

    with open(os.path.join(out_dir, "assets.layout.json"), "w") as f:
        json.dump({"start": asset_start, "end": current_rom_bank_offset, "limit": ROM_ASSET_LIMIT}, f)

    with open(os.path.join(out_dir, GEL_HEADER), "w") as f:
        f.write("\n".join(gel_lines) + "\n")

    with open(os.path.join(out_dir, ASM_HEADER), "w") as f:
        f.write("\n".join(asm_lines) + "\n")

    print(f"\nOK Successfully generated '{GEL_HEADER}' and '{ASM_HEADER}'.")

if __name__ == "__main__":
    # usage: png_to_snes_tiles.py <assets_folder> <output_folder> [first_level_bank] [asset_start]
    folder = sys.argv[1] if len(sys.argv) > 1 else ASSETS_DIR
    out = sys.argv[2] if len(sys.argv) > 2 else folder
    first_bank = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x84
    start = int(sys.argv[4], 0) if len(sys.argv) > 4 else 0xC000
    if not os.path.exists(folder):
        os.makedirs(folder)
        print(f"Created '{folder}' directory. Put your PNG files there!")
    else:
        os.makedirs(out, exist_ok=True)
        process_asset_folder(folder, out, first_bank, start)