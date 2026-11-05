// Pure packing analysis shared by the editor and node tests. It mirrors the build-time
// packer (compiler/metasprite_pack.py): same merging, empty-tile removal and flip dedupe.
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.MetaspritePack = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const SIZE = 8;

  // layer = { width, height, data: RGBA bytes }. Returns 64 packed RGBA ints, or null if blank.
  function readTile(layer, tx, ty) {
    if (!layer || tx < 0 || ty < 0 || (tx + 1) * SIZE > layer.width || (ty + 1) * SIZE > layer.height) return null;
    const px = new Array(64);
    let any = false;
    for (let r = 0; r < SIZE; r++) {
      for (let c = 0; c < SIZE; c++) {
        const i = ((ty * SIZE + r) * layer.width + tx * SIZE + c) * 4;
        const a = layer.data[i + 3];
        // 0 means transparent; opaque colours are keyed by RGB (+1 keeps them nonzero).
        const v = a === 0 ? 0 : ((layer.data[i] << 16) | (layer.data[i + 1] << 8) | layer.data[i + 2]) + 1;
        px[r * SIZE + c] = v;
        if (v) any = true;
      }
    }
    return any ? px : null;
  }

  const flipH = (px) => px.map((_, i) => px[(i >> 3) * 8 + 7 - (i & 7)]);
  const flipV = (px) => px.map((_, i) => px[(7 - (i >> 3)) * 8 + (i & 7)]);

  function canonicalKey(px) {
    const h = flipH(px);
    const keys = [px.join(","), h.join(","), flipV(px).join(","), flipV(h).join(",")];
    return keys.reduce((a, b) => (b < a ? b : a));
  }

  // Back-to-front layer indices for a frame: its own `order` (invalid/duplicate entries
  // dropped) followed by any layers it does not mention, which sit in front.
  function layerOrder(frame, count) {
    const seen = new Set();
    const out = [];
    for (const v of frame.order || []) {
      const i = Number(v);
      if (Number.isInteger(i) && i >= 0 && i < count && !seen.has(i)) { seen.add(i); out.push(i); }
    }
    for (let i = 0; i < count; i++) if (!seen.has(i)) out.push(i);
    return out;
  }

  // Per-frame layer anchor: a pixel offset applied to every tile of that layer in the frame.
  function layerOffset(frame, li) {
    const a = frame.layerAnchors && frame.layerAnchors[li];
    return { x: Math.round(Number(a && a.x) || 0), y: Math.round(Number(a && a.y) || 0) };
  }

  // Compose the cells of one frame (layers in the frame's draw order) into the entries the ROM stores.
  function composeFrame(frame, layers, tileCache) {
    const stacks = new Map();
    let raw = 0;
    for (const li of layerOrder(frame, frame.layers.length)) {
      const parts = frame.layers[li];
      const layer = layers[li];
      if (!layer) continue;
      const off = layerOffset(frame, li);
      for (const q of parts) {
        const p = { ...q, x: q.x + off.x, y: q.y + off.y };
        const cacheKey = `${li}:${p.sheetX}:${p.sheetY}`;
        let base = tileCache.get(cacheKey);
        if (base === undefined) {
          base = readTile(layer, p.sheetX, p.sheetY);
          tileCache.set(cacheKey, base);
        }
        if (!base) continue;
        let px = base;
        if (p.hflip) px = flipH(px);
        if (p.vflip) px = flipV(px);
        const key = `${p.x},${p.y}`;
        if (!stacks.has(key)) stacks.set(key, { x: p.x, y: p.y, list: [] });
        stacks.get(key).list.push({ pal: layer.palette | 0, px });
        raw++;
      }
    }
    const entries = [];
    for (const s of stacks.values()) {
      const merged = [];
      for (const item of s.list) {
        const top = merged[merged.length - 1];
        if (top && top.pal === item.pal) {
          top.px = item.px.map((v, i) => v || top.px[i]);
        } else {
          merged.push({ pal: item.pal, px: item.px.slice() });
        }
      }
      merged.forEach((m, order) => entries.push({ x: s.x, y: s.y, order, pal: m.pal, px: m.px }));
    }
    return { entries, raw };
  }

  // layers: [{width,height,data,palette}], frames: [{layers:[parts[]]}], clips: [{steps}]
  function analyze(layers, frames, clips) {
    const tileCache = new Map();
    const composed = frames.map((f) => composeFrame(f, layers, tileCache));
    const all = composed.flatMap((c) => c.entries);
    const result = {
      frames: composed.map((c) => ({ sprites: c.entries.length, raw: c.raw })),
      uniqueTiles: 0, tileBytes: 0, tableBytes: 0, paletteBytes: 0, totalBytes: 0,
      peakScanline: 0, peakFrame: 0, widthT: 0, heightT: 0, box: null, warnings: []
    };
    if (!all.length) return result;

    const minX = Math.min(...all.map((e) => e.x));
    const minY = Math.min(...all.map((e) => e.y));
    const maxX = Math.max(...all.map((e) => e.x)) + SIZE;
    const maxY = Math.max(...all.map((e) => e.y)) + SIZE;
    result.box = { minX, minY, maxX, maxY };
    result.widthT = (maxX - minX) / SIZE;
    result.heightT = (maxY - minY) / SIZE;
    if (result.widthT > 16 || result.heightT > 16) result.warnings.push("Sprite box is larger than 16x16 tiles.");

    const unique = new Set();
    const slots = new Set();
    composed.forEach((c, fi) => {
      const lines = new Map();
      for (const e of c.entries) {
        unique.add(canonicalKey(e.px));
        slots.add(e.pal);
        const gy = (e.y - minY) / SIZE;
        for (let r = 0; r < SIZE; r++) lines.set(gy * SIZE + r, (lines.get(gy * SIZE + r) || 0) + 1);
      }
      const peak = Math.max(0, ...lines.values());
      if (peak > result.peakScanline) { result.peakScanline = peak; result.peakFrame = fi; }
      if (c.entries.length > 128) result.warnings.push(`Frame ${fi} needs more than 128 sprites.`);
    });
    result.uniqueTiles = unique.size;
    if (unique.size > 256) result.warnings.push("More than 256 unique tiles.");
    if (result.peakScanline > 32) result.warnings.push("More than 32 sprites on one scanline.");
    result.tileBytes = unique.size * 32;
    result.paletteBytes = slots.size * 32;
    let table = 1 + 2 * composed.length;
    for (const c of composed) table += 1 + 3 * c.entries.length;
    if (clips && clips.length) {
      table += 2 * clips.length;
      for (const clip of clips) table += 2 + 2 * clip.steps.length;
    }
    result.tableBytes = table;
    result.totalBytes = result.tileBytes + result.paletteBytes + table;
    return result;
  }

  return { analyze, composeFrame, readTile, canonicalKey, flipH, flipV, layerOrder, layerOffset };
});
