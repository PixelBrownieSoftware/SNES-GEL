(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const TS = 8;
  const SHEET_SCALE = 5;
  const FORMAT = "gel-metasprite-v2";
  const Pack = window.MetaspritePack;

  // ---- model ---------------------------------------------------------------
  // layers back to front; every frame holds one parts[] per layer (same order).
  const model = {
    name: "player",
    stage: { columns: 3, rows: 6 },
    collision: { width: 18, height: 46 },
    anchor: { x: 0, y: 0 },
    hitboxAnchor: { x: 0, y: 0 },
    layers: [],
    frames: [],
    clips: []
  };
  const ui = {
    layer: 0, frame: 0, clip: 0,
    selection: new Set(),
    tool: "select",
    scale: 5,
    tileSel: null, sheetDrag: null,
    drag: null, hover: null,
    activeAnchor: "sprite",
    showOnion: true, showCollision: true, showGrid: true, showAnchor: true
  };
  const undoStack = [];
  let dirHandle = null;
  let serverMode = false;
  let serverCatalog = null;
  let folderProjects = [];
  let activeServerProject = "";
  let toastTimer;
  let play = null;

  const stage = $("stage-canvas");
  const stageCtx = stage.getContext("2d");
  const sheet = $("sheet-canvas");
  const sheetCtx = sheet.getContext("2d");

  // ---- helpers -------------------------------------------------------------
  function toast(message) {
    const el = $("toast");
    el.textContent = message;
    el.classList.add("visible");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("visible"), 3200);
  }

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));
  const int = (v, fallback = 0) => (Number.isFinite(Number(v)) ? Math.round(Number(v)) : fallback);
  const ident = (name) => {
    const s = String(name).replace(/[^0-9A-Za-z]+/g, "_").replace(/^_+|_+$/g, "").toUpperCase();
    return !s || /^[0-9]/.test(s) ? `_${s}` : s;
  };
  const curLayer = () => model.layers[ui.layer] || null;
  const curFrame = () => model.frames[ui.frame] || null;
  const curParts = () => (curFrame() && curFrame().layers[ui.layer]) || [];

  function newFrame(name) {
    return { name, layers: model.layers.map(() => []) };
  }

  function uniqueFrameName() {
    let i = model.frames.length;
    while (model.frames.some((f) => f.name === `frame${i}`)) i++;
    return `frame${i}`;
  }

  function loadImageElement(src) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("image failed to decode"));
      img.src = src;
    });
  }

  async function decodeBlob(blob) {
    const url = URL.createObjectURL(blob);
    try {
      const decoded = await decodeSource(url);
      decoded.palette = await decodeIndexedPalette(await blob.arrayBuffer());
      return decoded;
    } finally {
      URL.revokeObjectURL(url);
    }
  }

  async function decodeIndexedPalette(buffer) {
    const bytes = new Uint8Array(buffer);
    const signature = [137, 80, 78, 71, 13, 10, 26, 10];
    if (bytes.length < 33 || !signature.every((value, i) => bytes[i] === value) || bytes[25] !== 3) return null;
    const view = new DataView(buffer);
    let palette = null;
    let transparency = new Uint8Array(0);
    for (let offset = 8; offset + 12 <= bytes.length;) {
      const length = view.getUint32(offset);
      const type = String.fromCharCode(...bytes.subarray(offset + 4, offset + 8));
      const start = offset + 8;
      if (start + length + 4 > bytes.length) return null;
      if (type === "PLTE") palette = bytes.slice(start, start + length);
      else if (type === "tRNS") transparency = bytes.slice(start, start + length);
      offset = start + length + 4;
      if (type === "IEND") break;
    }
    if (!palette || palette.length % 3 !== 0) return null;
    return Array.from({ length: 16 }, (_, i) => ({
      r: palette[i * 3] || 0,
      g: palette[i * 3 + 1] || 0,
      b: palette[i * 3 + 2] || 0,
      a: i === 0 ? 0 : (transparency[i] === undefined ? 255 : transparency[i])
    }));
  }

  async function decodeSource(src) {
    const img = await loadImageElement(src);
    const canvas = document.createElement("canvas");
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    ctx.drawImage(img, 0, 0);
    return { canvas, width: canvas.width, height: canvas.height, data: ctx.getImageData(0, 0, canvas.width, canvas.height).data };
  }

  function applyImage(layer, decoded) {
    layer.canvas = decoded.canvas;
    layer.width = decoded.width;
    layer.height = decoded.height;
    layer.data = decoded.data;
    layer.paletteColors = decoded.palette || null;
    layer.missing = false;
  }

  function makeLayer(source, extra = {}) {
    return {
      name: source.replace(/\.png$/i, ""), source, palette: 0, visible: true,
      cell: { w: model.stage.columns, h: model.stage.rows },
      canvas: null, width: 0, height: 0, data: null, missing: true, pending: false, ...extra
    };
  }

  function renderPalette() {
    const layer = curLayer();
    $("palette-title").textContent = layer
      ? `${layer.name} · OBJ P${layer.palette}`
      : "Palette";
    const container = $("palette-colors");
    container.textContent = "";
    if (!layer) {
      const hint = document.createElement("p");
      hint.className = "hint";
      hint.textContent = "Select a layer to view its indexed colors.";
      container.append(hint);
      return;
    }
    if (!layer.paletteColors) {
      const hint = document.createElement("p");
      hint.className = "hint";
      hint.textContent = "This image has no indexed PNG palette. The build requires indexed PNGs.";
      container.append(hint);
      return;
    }
    layer.paletteColors.forEach((color, index) => {
      const swatch = document.createElement("div");
      swatch.className = "palette-swatch";
      const sample = document.createElement("div");
      sample.className = `palette-swatch-color${index === 0 ? " transparent" : ""}`;
      if (index !== 0) sample.style.backgroundColor = `rgba(${color.r}, ${color.g}, ${color.b}, ${color.a / 255})`;
      sample.title = index === 0 ? "0 · transparent" : `${index} · #${[color.r, color.g, color.b].map((v) => v.toString(16).padStart(2, "0")).join("").toUpperCase()}`;
      const label = document.createElement("span");
      label.textContent = index.toString(16).toUpperCase();
      swatch.append(sample, label);
      container.append(swatch);
    });
  }

  async function serverRequest(path, options) {
    const response = await fetch(path, options);
    if (!response.ok) {
      let message = `${response.status} ${response.statusText}`;
      try { message = (await response.json()).error || message; } catch (_) { /* use HTTP status */ }
      throw new Error(message);
    }
    return response;
  }

  // ---- undo ----------------------------------------------------------------
  function snapshot() {
    return {
      layers: model.layers.map((l) => ({ ...l, cell: { ...l.cell } })),
      frames: JSON.parse(JSON.stringify(model.frames)),
      clips: JSON.parse(JSON.stringify(model.clips)),
      stage: { ...model.stage },
      collision: { ...model.collision },
      anchor: { ...model.anchor },
      hitboxAnchor: { ...model.hitboxAnchor },
      layer: ui.layer, frame: ui.frame, clip: ui.clip
    };
  }

  function pushUndo() {
    undoStack.push(snapshot());
    if (undoStack.length > 100) undoStack.shift();
    $("undo-button").disabled = false;
  }

  function undo() {
    const s = undoStack.pop();
    if (!s) return;
    Object.assign(model, {
      layers: s.layers, frames: s.frames, clips: s.clips, stage: s.stage,
      collision: s.collision, anchor: s.anchor, hitboxAnchor: s.hitboxAnchor
    });
    ui.layer = s.layer; ui.frame = s.frame; ui.clip = s.clip;
    ui.selection.clear();
    $("undo-button").disabled = undoStack.length === 0;
    syncInputs();
    renderAll();
  }

  // ---- drawing -------------------------------------------------------------
  function drawTile(ctx, layer, p, dx, dy, zoom, hflip, vflip) {
    const s = TS * zoom;
    ctx.save();
    ctx.translate(dx + (hflip ? s : 0), dy + (vflip ? s : 0));
    ctx.scale(hflip ? -1 : 1, vflip ? -1 : 1);
    ctx.drawImage(layer.canvas, p.sheetX * TS, p.sheetY * TS, TS, TS, 0, 0, s, s);
    ctx.restore();
  }

  function drawFrame(ctx, frameIndex, ox, oy, zoom, opts = {}) {
    const frame = model.frames[frameIndex];
    if (!frame) return;
    const { mirrored = false, alpha = 1, pin = false } = opts;
    const fa = frameAnchor(frameIndex);
    // Pinned: the frame's anchor point is drawn where the project anchor is.
    const sx = pin ? model.anchor.x - fa.x : 0, sy = pin ? model.anchor.y - fa.y : 0;
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.imageSmoothingEnabled = false;
    model.layers.forEach((layer, li) => {
      if (!layer.visible || !layer.canvas) return;
      for (const p of frame.layers[li] || []) {
        const x = (mirrored ? 2 * fa.x - p.x - TS : p.x) + sx;
        drawTile(ctx, layer, p, (ox + x) * zoom, (oy + p.y + sy) * zoom, zoom, !!p.hflip !== mirrored, !!p.vflip);
      }
    });
    ctx.restore();
  }

  // A frame may carry its own sprite anchor (the mirror axis); otherwise it uses the project one.
  const frameAnchor = (i) => model.frames[i]?.anchor || model.anchor;

  function sizeCanvas(canvas, w, h) {
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
  }

  function drawGrid(ctx, w, h, zoom) {
    ctx.strokeStyle = "#9cabb822";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x <= w; x += TS) { ctx.moveTo(x * zoom + .5, 0); ctx.lineTo(x * zoom + .5, h * zoom); }
    for (let y = 0; y <= h; y += TS) { ctx.moveTo(0, y * zoom + .5); ctx.lineTo(w * zoom, y * zoom + .5); }
    ctx.stroke();
  }

  function cross(ctx, x, y, size, color, width) {
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(x - size, y); ctx.lineTo(x + size, y);
    ctx.moveTo(x, y - size); ctx.lineTo(x, y + size);
    ctx.stroke();
  }

  function stageGeometry() {
    const w = model.stage.columns * TS;
    const h = model.stage.rows * TS;
    // The origin must sit on a tile boundary, or stamped tiles (which snap to 8px from it)
    // land half a cell off the grid when the stage has an odd number of columns or rows.
    return { w, h, ox: Math.floor(model.stage.columns / 2) * TS, oy: Math.floor(model.stage.rows / 2) * TS };
  }

  function renderStage() {
    const z = ui.scale;
    const { w, h, ox, oy } = stageGeometry();
    sizeCanvas(stage, w * z, h * z);
    const ctx = stageCtx;
    ctx.clearRect(0, 0, stage.width, stage.height);
    ctx.imageSmoothingEnabled = false;
    if (ui.showGrid) drawGrid(ctx, w, h, z);
    if (ui.showOnion && ui.frame > 0) drawFrame(ctx, ui.frame - 1, ox, oy, z, { alpha: 0.28 });
    drawFrame(ctx, ui.frame, ox, oy, z);

    if (ui.showCollision) {
      const x = (ox + model.hitboxAnchor.x) * z;
      const y = (oy + model.hitboxAnchor.y) * z;
      ctx.fillStyle = "#ff4d4d18";
      ctx.fillRect(x, y, model.collision.width * z, model.collision.height * z);
      ctx.strokeStyle = "#ff4d4d";
      ctx.lineWidth = 2;
      ctx.strokeRect(x + 1, y + 1, model.collision.width * z - 2, model.collision.height * z - 2);
    }

    const parts = curParts();
    for (const i of ui.selection) {
      const p = parts[i];
      if (!p) continue;
      ctx.strokeStyle = "#73e0c1";
      ctx.lineWidth = 2;
      ctx.strokeRect((ox + p.x) * z + 1, (oy + p.y) * z + 1, TS * z - 2, TS * z - 2);
    }

    if (ui.tool === "stamp" && ui.hover && ui.tileSel && curLayer()?.canvas) {
      ctx.save();
      ctx.globalAlpha = 0.6;
      for (const t of selectionTiles()) {
        const p = { sheetX: t.sx, sheetY: t.sy };
        drawTile(ctx, curLayer(), p, (ox + ui.hover.x + t.dx) * z, (oy + ui.hover.y + t.dy) * z, z, false, false);
      }
      ctx.restore();
    }

    if (ui.tool === "eraser" && ui.hover && !ui.drag) {
      ctx.fillStyle = "#ff4d4d33";
      ctx.fillRect((ox + ui.hover.x) * z, (oy + ui.hover.y) * z, TS * z, TS * z);
      ctx.strokeStyle = "#ff4d4d";
      ctx.lineWidth = 1;
      ctx.strokeRect((ox + ui.hover.x) * z + .5, (oy + ui.hover.y) * z + .5, TS * z - 1, TS * z - 1);
    }

    if (ui.drag?.kind === "marquee") {
      const { a, b } = ui.drag;
      const x = (ox + Math.min(a.x, b.x)) * z, y = (oy + Math.min(a.y, b.y)) * z;
      ctx.fillStyle = "#73e0c133";
      ctx.fillRect(x, y, Math.abs(b.x - a.x) * z, Math.abs(b.y - a.y) * z);
      ctx.strokeStyle = "#73e0c1";
      ctx.lineWidth = 1;
      ctx.strokeRect(x + .5, y + .5, Math.abs(b.x - a.x) * z, Math.abs(b.y - a.y) * z);
    }

    if (ui.showAnchor) {
      const fa = frameAnchor(ui.frame);
      cross(ctx, (ox + fa.x) * z, (oy + fa.y) * z, 7, ui.activeAnchor === "sprite" ? "#fff08a" : "#f5ca74", ui.activeAnchor === "sprite" ? 3 : 2);
      cross(ctx, (ox + model.hitboxAnchor.x) * z, (oy + model.hitboxAnchor.y) * z, 6, ui.activeAnchor === "hitbox" ? "#73e0c1" : "#ff7777", ui.activeAnchor === "hitbox" ? 3 : 2);
    }
    ctx.strokeStyle = "#d9e3ed";
    ctx.lineWidth = 2;
    ctx.strokeRect(1, 1, stage.width - 2, stage.height - 2);
    $("zoom-level").textContent = `${z}×`;
    $("empty-state").hidden = model.frames.some((f) => f.layers.some((l) => l.length));
  }

  function renderPreviews(analysis) {
    const { w, h, ox, oy } = stageGeometry();
    const z = clamp(Math.floor(140 / Math.max(w, h)), 1, 6);
    for (const [id, mirrored] of [["normal-canvas", false], ["mirror-canvas", true]]) {
      const c = $(id);
      sizeCanvas(c, w * z, h * z);
      c.style.width = "100%";
      c.style.height = "auto";
      const ctx = c.getContext("2d");
      ctx.clearRect(0, 0, c.width, c.height);
      drawFrame(ctx, ui.frame, ox, oy, z, { mirrored, pin: true });
      cross(ctx, (ox + model.anchor.x) * z, (oy + model.anchor.y) * z, 5, "#fff08a", 1);
    }
  }

  // Packed sheet view: only non-empty tiles, identical pixel arrangements shown once. Picking a
  // tile still stores its original sheet coordinates, so the project format is unchanged.
  const PACK_COLS = 8;
  let packedCache = { data: null, info: null };

  function packedTiles(layer) {
    if (packedCache.data === layer.data) return packedCache.info;
    const list = [], byKey = new Map(), byCoord = new Map();
    let nonEmpty = 0;
    for (let ty = 0; ty < Math.floor(layer.height / TS); ty++) {
      for (let tx = 0; tx < Math.floor(layer.width / TS); tx++) {
        const px = Pack.readTile(layer, tx, ty);
        if (!px) continue;
        nonEmpty++;
        const key = px.join(",");
        if (!byKey.has(key)) { byKey.set(key, list.length); list.push({ sx: tx, sy: ty }); }
        byCoord.set(`${tx},${ty}`, byKey.get(key));
      }
    }
    packedCache = { data: layer.data, info: { list, byCoord, nonEmpty } };
    return packedCache.info;
  }

  function renderPackedSheet(layer) {
    const info = packedTiles(layer), z = SHEET_SCALE, tp = TS * z;
    const rows = Math.max(1, Math.ceil(info.list.length / PACK_COLS));
    sizeCanvas(sheet, PACK_COLS * tp, rows * tp);
    sheetCtx.clearRect(0, 0, sheet.width, sheet.height);
    sheetCtx.imageSmoothingEnabled = false;
    info.list.forEach((t, i) => {
      sheetCtx.drawImage(layer.canvas, t.sx * TS, t.sy * TS, TS, TS, (i % PACK_COLS) * tp, Math.floor(i / PACK_COLS) * tp, tp, tp);
    });
    sheetCtx.strokeStyle = "#d9e3ed55";
    sheetCtx.lineWidth = 1;
    sheetCtx.beginPath();
    for (let x = 0; x <= PACK_COLS; x++) { sheetCtx.moveTo(x * tp + .5, 0); sheetCtx.lineTo(x * tp + .5, sheet.height); }
    for (let y = 0; y <= rows; y++) { sheetCtx.moveTo(0, y * tp + .5); sheetCtx.lineTo(sheet.width, y * tp + .5); }
    sheetCtx.stroke();
    const mark = (i, fill, stroke) => {
      const x = (i % PACK_COLS) * tp, y = Math.floor(i / PACK_COLS) * tp;
      sheetCtx.fillStyle = fill;
      sheetCtx.fillRect(x, y, tp, tp);
      if (stroke) { sheetCtx.strokeStyle = stroke; sheetCtx.lineWidth = 2; sheetCtx.strokeRect(x + 1, y + 1, tp - 2, tp - 2); }
    };
    for (const p of curParts()) {
      const i = info.byCoord.get(`${p.sheetX},${p.sheetY}`);
      if (i !== undefined) mark(i, "#f5ca7433");
    }
    if (ui.tileSel) {
      const i = info.byCoord.get(`${ui.tileSel.x},${ui.tileSel.y}`);
      if (i !== undefined) mark(i, "#73e0c133", "#73e0c1");
    }
    $("pack-toggle").textContent = `Packed: ${info.list.length}/${info.nonEmpty}`;
  }

  function renderSheet() {
    const layer = curLayer();
    if (!layer || !layer.canvas) {
      sheet.width = 1; sheet.height = 1;
      sheet.style.width = "0"; sheet.style.height = "0";
      return;
    }
    if (ui.packed) { renderPackedSheet(layer); return; }
    $("pack-toggle").textContent = "Packed view";
    const z = SHEET_SCALE;
    sizeCanvas(sheet, layer.width * z, layer.height * z);
    sheetCtx.clearRect(0, 0, sheet.width, sheet.height);
    sheetCtx.imageSmoothingEnabled = false;
    sheetCtx.drawImage(layer.canvas, 0, 0, sheet.width, sheet.height);
    const tp = TS * z;
    sheetCtx.strokeStyle = "#d9e3ed55";
    sheetCtx.lineWidth = 1;
    sheetCtx.beginPath();
    for (let x = 0; x <= sheet.width; x += tp) { sheetCtx.moveTo(x + .5, 0); sheetCtx.lineTo(x + .5, sheet.height); }
    for (let y = 0; y <= sheet.height; y += tp) { sheetCtx.moveTo(0, y + .5); sheetCtx.lineTo(sheet.width, y + .5); }
    sheetCtx.stroke();
    // Cell boundaries
    const { w: cw, h: ch } = layer.cell;
    sheetCtx.strokeStyle = "#f5ca7488";
    sheetCtx.beginPath();
    for (let x = 0; x <= layer.width / TS; x += cw) { sheetCtx.moveTo(x * tp + .5, 0); sheetCtx.lineTo(x * tp + .5, sheet.height); }
    for (let y = 0; y <= layer.height / TS; y += ch) { sheetCtx.moveTo(0, y * tp + .5); sheetCtx.lineTo(sheet.width, y * tp + .5); }
    sheetCtx.stroke();
    // Tiles already placed in this frame
    sheetCtx.fillStyle = "#f5ca7433";
    for (const p of curParts()) sheetCtx.fillRect(p.sheetX * tp, p.sheetY * tp, tp, tp);
    if (ui.tileSel) {
      const { x, y, w, h } = ui.tileSel;
      sheetCtx.fillStyle = "#73e0c133";
      sheetCtx.fillRect(x * tp, y * tp, w * tp, h * tp);
      sheetCtx.strokeStyle = "#73e0c1";
      sheetCtx.lineWidth = 2;
      sheetCtx.strokeRect(x * tp + 1, y * tp + 1, w * tp - 2, h * tp - 2);
    }
  }

  function renderLayers() {
    const list = $("layers-list");
    list.textContent = "";
    model.layers.forEach((layer, i) => {
      const row = document.createElement("div");
      row.className = `layer-row${i === ui.layer ? " selected" : ""}${layer.missing ? " missing" : ""}`;
      const eye = document.createElement("input");
      eye.type = "checkbox"; eye.checked = layer.visible; eye.title = "Show in the editor";
      eye.addEventListener("change", () => { layer.visible = eye.checked; renderAll(); });
      const name = document.createElement("input");
      name.className = "layer-name"; name.value = layer.name; name.title = layer.missing ? `Missing art: ${layer.source}` : layer.source;
      name.addEventListener("focus", () => selectLayer(i));
      name.addEventListener("change", () => { pushUndo(); layer.name = name.value.trim() || layer.name; });
      const pal = document.createElement("select");
      pal.title = "OBJ palette slot";
      for (let p = 0; p < 8; p++) pal.add(new Option(`P${p}`, p, false, p === layer.palette));
      pal.addEventListener("change", () => { pushUndo(); layer.palette = int(pal.value); renderAll(); });
      const mk = (label, title, fn) => {
        const b = document.createElement("button");
        b.type = "button"; b.className = "icon-button"; b.textContent = label; b.title = title;
        b.addEventListener("click", (e) => { e.stopPropagation(); fn(); });
        return b;
      };
      row.append(eye, name, pal,
        mk("▲", "Move back", () => moveLayer(i, -1)),
        mk("▼", "Move front", () => moveLayer(i, 1)),
        mk("⧉", "New layer using the same PNG (e.g. back arms)", () => duplicateLayer(i)),
        mk("×", "Remove layer", () => removeLayer(i)));
      row.addEventListener("click", () => selectLayer(i));
      list.append(row);
    });
    const layer = curLayer();
    $("cell-w").value = layer ? layer.cell.w : model.stage.columns;
    $("cell-h").value = layer ? layer.cell.h : model.stage.rows;
    $("source-hint").textContent = layer
      ? (layer.missing ? `Art for "${layer.source}" is missing. Add the PNG or use Reload art.`
        : `${layer.source} · ${layer.width}×${layer.height}px. Click a tile, drag an area, or double-click a cell.`)
      : "Add a layer PNG to start.";
  }

  function renderFrames() {
    const strip = $("frame-strip");
    strip.textContent = "";
    const { w, h, ox, oy } = stageGeometry();
    const z = clamp(Math.floor(96 / h), 1, 3);
    model.frames.forEach((frame, i) => {
      const card = document.createElement("button");
      card.type = "button";
      card.className = `frame-card${i === ui.frame ? " selected" : ""}`;
      const c = document.createElement("canvas");
      c.width = w * z; c.height = h * z;
      drawFrame(c.getContext("2d"), i, ox, oy, z);
      const label = document.createElement("span");
      label.textContent = `${i} ${frame.name}`;
      card.append(c, label);
      card.addEventListener("click", () => selectFrame(i));
      strip.append(card);
    });
    $("frame-name").value = curFrame() ? curFrame().name : "";
  }

  function renderClips() {
    const sel = $("clip-select");
    sel.textContent = "";
    model.clips.forEach((c, i) => sel.add(new Option(c.name, i, false, i === ui.clip)));
    const clip = model.clips[ui.clip];
    $("clip-name").value = clip ? clip.name : "";
    $("clip-name").disabled = $("clip-loop").disabled = $("clip-pingpong").disabled = $("clip-del").disabled = !clip;
    $("clip-pingpong").checked = !!clip?.pingpong;
    $("step-add").disabled = $("clip-fill").disabled = $("clip-play").disabled = !clip;
    $("clip-loop").checked = clip ? clip.loop : true;
    const steps = $("clip-steps");
    steps.textContent = "";
    if (!clip) return;
    clip.steps.forEach((step, i) => {
      const row = document.createElement("div");
      row.className = "step-row";
      const idx = document.createElement("span");
      idx.className = "part-id"; idx.textContent = String(i);
      const fsel = document.createElement("select");
      model.frames.forEach((f, fi) => fsel.add(new Option(`${fi} ${f.name}`, fi, false, fi === step.frame)));
      fsel.addEventListener("change", () => { pushUndo(); step.frame = int(fsel.value); selectFrame(step.frame); });
      const ticks = document.createElement("input");
      ticks.type = "number"; ticks.min = 1; ticks.max = 255; ticks.value = step.ticks; ticks.title = "Ticks (1/60 s)";
      ticks.addEventListener("change", () => { pushUndo(); step.ticks = clamp(int(ticks.value, 1), 1, 255); renderAll(); });
      const del = document.createElement("button");
      del.type = "button"; del.className = "icon-button"; del.textContent = "×";
      del.addEventListener("click", () => { pushUndo(); clip.steps.splice(i, 1); renderAll(); });
      row.append(idx, fsel, ticks, del);
      steps.append(row);
    });
  }

  function packLayers() {
    return model.layers.map((l) => ({ width: l.width, height: l.height, data: l.data, palette: l.palette }))
      .map((l, i) => (model.layers[i].data ? l : null));
  }

  function renderReport() {
    const a = Pack.analyze(packLayers(), model.frames, model.clips);
    const sprites = a.frames.map((f) => f.sprites);
    const raw = a.frames.map((f) => f.raw);
    const maxS = Math.max(0, ...sprites);
    const rows = [
      ["Sprites / frame", maxS ? `${Math.min(...sprites)}–${maxS} of 128` : "—"],
      ["Before merge", maxS ? `${Math.max(0, ...raw)} max` : "—"],
      ["Unique tiles", `${a.uniqueTiles} / 256`],
      ["Peak scanline", `${a.peakScanline} / 32`],
      ["Tile data", `${a.tileBytes} B`],
      ["Tables + palettes", `${a.tableBytes + a.paletteBytes} B`],
      ["Total ROM", `${a.totalBytes} B`]
    ];
    $("report").textContent = "";
    for (const [k, v] of rows) {
      const dt = document.createElement("dt"), dd = document.createElement("dd");
      dt.textContent = k; dd.textContent = v;
      $("report").append(dt, dd);
    }
    const warnings = [...a.warnings];
    const dup = (names, what) => {
      const seen = new Set();
      for (const n of names.map(ident)) {
        if (seen.has(n)) warnings.push(`Two ${what} share the name ${n}.`);
        seen.add(n);
      }
    };
    dup(model.frames.map((f) => f.name), "frames");
    dup(model.clips.map((c) => c.name), "clips");
    model.layers.forEach((l) => { if (l.missing) warnings.push(`Missing art: ${l.source}`); });
    const used = model.layers.map((_, li) => model.frames.some((f) => f.layers[li].length));
    model.layers.forEach((l, i) => { if (!used[i]) warnings.push(`Layer "${l.name}" is empty in every frame.`); });
    $("warnings").textContent = "";
    for (const w of warnings) {
      const li = document.createElement("li");
      li.textContent = w;
      $("warnings").append(li);
    }

    const box = a.box;
    const set = (id, v) => { $(id).textContent = v; };
    if (box) {
      const wpx = box.maxX - box.minX;
      set("offset-left", `${model.hitboxAnchor.x - box.minX}px`);
      set("offset-right", `${model.hitboxAnchor.x - 2 * frameAnchor(ui.frame).x + box.minX + wpx}px`);
      set("offset-top", `${model.hitboxAnchor.y - box.minY}px`);
      set("bounds-size", `${wpx} × ${box.maxY - box.minY}px`);
    } else {
      ["offset-left", "offset-right", "offset-top", "bounds-size"].forEach((id) => set(id, "—"));
    }
    const p = `${ident(model.name)}_MS`;
    $("usage").textContent =
      `std_load_palette(8, ${p}_PAL0, 16);\n` +
      `std_load_vram(0x0000, ${p}_TILES, ${p}_BYTE_LEN);\n` +
      `// each frame:\n` +
      `std_draw_frame(${p}_FRAMES, frame,\n` +
      `  x - ${p}_OFF_X_NORMAL, y - ${p}_OFF_Y,\n` +
      `  OAM_PRIO3, 0);\n` +
      `// clips: std_anim_frame / std_anim_ticks /\n` +
      `// std_anim_next(${p}_ANIMS, clip, step)`;
    $("sprite-title").textContent = `${model.name || "Untitled"} metasprite`;
    const fa = frameAnchor(ui.frame);
    $("anchor-position").textContent = `${fa.x}, ${fa.y}${model.frames[ui.frame]?.anchor ? " (frame)" : ""} · ${model.hitboxAnchor.x}, ${model.hitboxAnchor.y}`;
  }

  function renderSelectionInfo() {
    const n = ui.selection.size;
    const parts = curParts();
    let text = "Nothing selected";
    if (n === 1) {
      const p = parts[[...ui.selection][0]];
      if (p) text = `Tile ${p.sheetX},${p.sheetY} at ${p.x}, ${p.y}${p.hflip ? " · H" : ""}${p.vflip ? " · V" : ""}`;
    } else if (n > 1) {
      text = `${n} tiles selected`;
    }
    $("selection-info").textContent = text;
    for (const id of ["flip-h", "flip-v", "erase-selection"]) $(id).disabled = n === 0;
    $("fill-cell").disabled = !ui.tileSel || !curLayer()?.canvas;
    $("stamp-tool").disabled = !ui.tileSel || !curLayer()?.canvas;
    $("save-project").disabled = model.layers.length === 0;
  }

  function renderAll() {
    renderLayers();
    renderPalette();
    renderSheet();
    renderStage();
    renderPreviews();
    renderFrames();
    renderClips();
    renderReport();
    renderSelectionInfo();
  }

  function renderLight() {
    renderStage();
    renderSelectionInfo();
  }

  function syncInputs() {
    $("asset-name").value = model.name;
    $("stage-columns").value = model.stage.columns;
    $("stage-rows").value = model.stage.rows;
    $("collision-width").value = model.collision.width;
    $("collision-height").value = model.collision.height;
  }

  // ---- layers / frames / clips --------------------------------------------
  function selectLayer(i) {
    if (i === ui.layer) return;
    ui.layer = i;
    ui.selection.clear();
    ui.tileSel = null;
    renderAll();
  }

  function selectFrame(i) {
    ui.frame = clamp(i, 0, model.frames.length - 1);
    ui.selection.clear();
    renderAll();
  }

  function addLayerFromDecoded(source, decoded, blob) {
    const sharing = model.layers.filter((l) => l.source.toLowerCase() === source.toLowerCase());
    if (sharing.length) {
      for (const l of sharing) {
        applyImage(l, decoded);
        l.pending = l.pending || !!blob;
        if (blob) l.blob = blob;
      }
      return sharing[0];
    }
    const layer = makeLayer(source);
    applyImage(layer, decoded);
    layer.pending = !!blob;
    layer.blob = blob || null;
    model.layers.push(layer);
    model.frames.forEach((f) => f.layers.push([]));
    if (!model.frames.length) model.frames.push(newFrame("frame0"));
    return layer;
  }

  async function addLayerFiles(files) {
    if (!files.length) return;
    pushUndo();
    for (const file of files) {
      try {
        const layer = addLayerFromDecoded(file.name, await decodeBlob(file), file);
        ui.layer = model.layers.indexOf(layer);
      } catch (e) {
        toast(`Could not read ${file.name}: ${e.message}`);
      }
    }
    if (!model.frames.length) model.frames.push(newFrame("frame0"));
    ui.tileSel = null;
    renderAll();
  }

  // New layer that reuses the same PNG (e.g. back arms from the arms sheet) but has its own
  // tiles per frame, palette slot and draw order. It starts directly behind the original.
  function duplicateLayer(i) {
    const src = model.layers[i];
    if (!src) return;
    pushUndo();
    const copy = { ...src, cell: { ...src.cell }, name: `${src.name} 2`, pending: false, blob: null };
    model.layers.splice(i, 0, copy);
    model.frames.forEach((f) => f.layers.splice(i, 0, []));
    ui.layer = i;
    ui.selection.clear();
    ui.tileSel = null;
    renderAll();
    toast(`Added "${copy.name}" behind "${src.name}", sharing ${src.source}. Rename it and stamp tiles.`);
  }

  function removeLayer(i) {
    if (!confirm(`Remove layer "${model.layers[i].name}" from every frame?`)) return;
    pushUndo();
    model.layers.splice(i, 1);
    model.frames.forEach((f) => f.layers.splice(i, 1));
    ui.layer = clamp(ui.layer, 0, Math.max(0, model.layers.length - 1));
    ui.selection.clear();
    renderAll();
  }

  function moveLayer(i, dir) {
    const j = i + dir;
    if (j < 0 || j >= model.layers.length) return;
    pushUndo();
    [model.layers[i], model.layers[j]] = [model.layers[j], model.layers[i]];
    model.frames.forEach((f) => { [f.layers[i], f.layers[j]] = [f.layers[j], f.layers[i]]; });
    ui.layer = j;
    ui.selection.clear();
    renderAll();
  }

  function frameOp(kind) {
    pushUndo();
    const f = model.frames;
    if (kind === "add") {
      f.splice(ui.frame + 1, 0, newFrame(uniqueFrameName()));
      remapClips((i) => (i > ui.frame ? i + 1 : i));
      ui.frame++;
    } else if (kind === "dup") {
      const copy = JSON.parse(JSON.stringify(f[ui.frame]));
      copy.name = uniqueFrameName();
      f.splice(ui.frame + 1, 0, copy);
      remapClips((i) => (i > ui.frame ? i + 1 : i));
      ui.frame++;
    } else if (kind === "del") {
      if (f.length <= 1) { undoStack.pop(); toast("A sprite needs at least one frame."); return; }
      const gone = ui.frame;
      f.splice(gone, 1);
      model.clips.forEach((c) => { c.steps = c.steps.filter((s) => s.frame !== gone); });
      remapClips((i) => (i > gone ? i - 1 : i));
      ui.frame = clamp(ui.frame, 0, f.length - 1);
    } else {
      const j = ui.frame + (kind === "left" ? -1 : 1);
      if (j < 0 || j >= f.length) { undoStack.pop(); return; }
      [f[ui.frame], f[j]] = [f[j], f[ui.frame]];
      const a = ui.frame;
      remapClips((i) => (i === a ? j : i === j ? a : i));
      ui.frame = j;
    }
    ui.selection.clear();
    renderAll();
  }

  function remapClips(fn) {
    model.clips.forEach((c) => c.steps.forEach((s) => { s.frame = fn(s.frame); }));
  }

  function eraseSelection() {
    if (!ui.selection.size) return;
    pushUndo();
    const parts = curFrame().layers[ui.layer];
    curFrame().layers[ui.layer] = parts.filter((_, i) => !ui.selection.has(i));
    ui.selection.clear();
    renderAll();
  }

  function flipSelection(axis) {
    if (!ui.selection.size) return;
    pushUndo();
    const parts = curParts();
    const picked = [...ui.selection].map((i) => parts[i]);
    const key = axis === "h" ? "x" : "y";
    const lo = Math.min(...picked.map((p) => p[key]));
    const hi = Math.max(...picked.map((p) => p[key]));
    for (const p of picked) {
      p[key] = lo + hi - p[key];
      const flag = axis === "h" ? "hflip" : "vflip";
      if (p[flag]) delete p[flag]; else p[flag] = true;
    }
    renderAll();
  }

  function copyLayerToAll() {
    if (!curLayer()) return;
    pushUndo();
    const src = curParts();
    model.frames.forEach((f, i) => {
      if (i !== ui.frame) f.layers[ui.layer] = JSON.parse(JSON.stringify(src));
    });
    renderAll();
    toast(`Copied "${curLayer().name}" to ${model.frames.length - 1} other frame(s).`);
  }

  function addClip() {
    pushUndo();
    let n = model.clips.length;
    while (model.clips.some((c) => c.name === `anim${n}`)) n++;
    model.clips.push({ name: `anim${n}`, loop: true, steps: model.frames.map((_, i) => ({ frame: i, ticks: 8 })) });
    ui.clip = model.clips.length - 1;
    renderAll();
  }

  // ---- sheet tile selection -------------------------------------------------
  function selectionTiles() {
    const layer = curLayer();
    if (!layer?.data || !ui.tileSel) return [];
    const { x, y, w, h } = ui.tileSel;
    const info = packedTiles(layer);
    const out = [];
    for (let ty = y; ty < y + h; ty++) {
      for (let tx = x; tx < x + w; tx++) {
        if (!Pack.readTile(layer, tx, ty)) continue;
        // Identical tiles all point at the first copy on the sheet.
        const canon = info.list[info.byCoord.get(`${tx},${ty}`)] || { sx: tx, sy: ty };
        out.push({ sx: canon.sx, sy: canon.sy, dx: (tx - x) * TS, dy: (ty - y) * TS });
      }
    }
    return out;
  }

  function sheetTile(event) {
    const layer = curLayer();
    const rect = sheet.getBoundingClientRect();
    return {
      x: clamp(Math.floor((event.clientX - rect.left) / (TS * SHEET_SCALE)), 0, Math.floor(layer.width / TS) - 1),
      y: clamp(Math.floor((event.clientY - rect.top) / (TS * SHEET_SCALE)), 0, Math.floor(layer.height / TS) - 1)
    };
  }

  sheet.addEventListener("pointerdown", (e) => {
    const layer = curLayer();
    if (!layer?.canvas) return;
    if (ui.packed) {
      const rect = sheet.getBoundingClientRect(), tp = TS * SHEET_SCALE;
      const col = clamp(Math.floor((e.clientX - rect.left) / tp), 0, PACK_COLS - 1);
      const t = packedTiles(layer).list[Math.floor((e.clientY - rect.top) / tp) * PACK_COLS + col];
      if (t) ui.tileSel = { x: t.sx, y: t.sy, w: 1, h: 1 };
      renderSheet(); renderSelectionInfo();
      return;
    }
    sheet.setPointerCapture(e.pointerId);
    const t = sheetTile(e);
    ui.sheetDrag = t;
    ui.tileSel = { x: t.x, y: t.y, w: 1, h: 1 };
    renderSheet(); renderSelectionInfo();
  });
  sheet.addEventListener("pointermove", (e) => {
    if (!ui.sheetDrag) return;
    const t = sheetTile(e), s = ui.sheetDrag;
    ui.tileSel = { x: Math.min(s.x, t.x), y: Math.min(s.y, t.y), w: Math.abs(t.x - s.x) + 1, h: Math.abs(t.y - s.y) + 1 };
    renderSheet();
  });
  sheet.addEventListener("pointerup", () => { ui.sheetDrag = null; renderSelectionInfo(); });
  sheet.addEventListener("dblclick", (e) => {
    const layer = curLayer();
    if (!layer?.canvas || ui.packed) return;
    const t = sheetTile(e), { w, h } = layer.cell;
    const x = Math.floor(t.x / w) * w, y = Math.floor(t.y / h) * h;
    ui.tileSel = {
      x, y,
      w: Math.min(w, Math.floor(layer.width / TS) - x),
      h: Math.min(h, Math.floor(layer.height / TS) - y)
    };
    renderSheet(); renderSelectionInfo();
  });

  function fillFromSelection() {
    const tiles = selectionTiles();
    if (!tiles.length) { toast("The selected area has no visible pixels."); return; }
    pushUndo();
    const { w, h } = ui.tileSel;
    // Matches the stage origin so a whole cell lands centred on the canvas.
    const x0 = -Math.floor(w / 2) * TS, y0 = -Math.floor(h / 2) * TS;
    curFrame().layers[ui.layer] = tiles.map((t) => ({ sheetX: t.sx, sheetY: t.sy, x: x0 + t.dx, y: y0 + t.dy }));
    ui.selection = new Set(curParts().map((_, i) => i));
    renderAll();
  }

  // ---- stage interaction ---------------------------------------------------
  function stagePoint(e) {
    const rect = stage.getBoundingClientRect();
    const { ox, oy } = stageGeometry();
    return { x: (e.clientX - rect.left) / ui.scale - ox, y: (e.clientY - rect.top) / ui.scale - oy };
  }

  const snap = (v) => Math.floor(v / TS) * TS;

  function hitTest(pt) {
    const parts = curParts();
    for (let i = parts.length - 1; i >= 0; i--) {
      const p = parts[i];
      if (pt.x >= p.x && pt.x < p.x + TS && pt.y >= p.y && pt.y < p.y + TS) return i;
    }
    return -1;
  }

  function stampAt(pt) {
    const tiles = selectionTiles();
    if (!tiles.length) return;
    const x = snap(pt.x), y = snap(pt.y);
    const layerParts = curFrame().layers[ui.layer];
    const taken = new Set(tiles.map((t) => `${x + t.dx},${y + t.dy}`));
    curFrame().layers[ui.layer] = layerParts.filter((p) => !taken.has(`${p.x},${p.y}`));
    for (const t of tiles) curFrame().layers[ui.layer].push({ sheetX: t.sx, sheetY: t.sy, x: x + t.dx, y: y + t.dy });
  }

  stage.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !curFrame()) return;
    stage.setPointerCapture(e.pointerId);
    const pt = stagePoint(e);
    if (ui.tool === "anchor") {
      pushUndo();
      ui.drag = { kind: "anchor" };
      setAnchor(pt);
      return;
    }
    if (!curLayer()) return;
    if (ui.tool === "eraser") {
      pushUndo();
      ui.drag = { kind: "erase" };
      eraseAt(pt);
      return;
    }
    if (ui.tool === "stamp") {
      pushUndo();
      ui.drag = { kind: "stamp", last: null };
      stampStep(pt);
      return;
    }
    const hit = hitTest(pt);
    if (ui.tool === "move" && hit >= 0) {
      if (!ui.selection.has(hit)) ui.selection = new Set([hit]);
      ui.drag = {
        kind: "move", start: pt, undone: false,
        origins: [...ui.selection].map((i) => ({ i, x: curParts()[i].x, y: curParts()[i].y }))
      };
    } else {
      // Select tool (or Move tool pressed on empty space) draws a selection box.
      if (ui.tool === "move") setTool("select");
      if (!e.shiftKey) ui.selection.clear();
      if (hit >= 0) ui.selection.add(hit);
      ui.drag = { kind: "marquee", a: pt, b: pt, base: new Set(ui.selection) };
    }
    renderLight();
  });

  function stampStep(pt) {
    const key = `${snap(pt.x)},${snap(pt.y)}`;
    if (ui.drag.last === key) return;
    ui.drag.last = key;
    stampAt(pt);
    renderLight();
  }

  // The sprite anchor is edited per frame (created on first edit); the hitbox anchor is shared.
  function anchorTarget() {
    if (ui.activeAnchor !== "sprite") return model.hitboxAnchor;
    const f = curFrame();
    if (!f.anchor) f.anchor = { ...model.anchor };
    return f.anchor;
  }

  function setAnchor(pt) {
    const target = anchorTarget();
    target.x = Math.round(pt.x);
    target.y = Math.round(pt.y);
    renderStage();
    renderReport();
  }

  // Erases every tile on the current layer covered by the cell under the pointer.
  function eraseAt(pt) {
    const parts = curParts();
    const x = snap(pt.x), y = snap(pt.y);
    const kept = parts.filter((p) => !(p.x < x + TS && p.x + TS > x && p.y < y + TS && p.y + TS > y));
    if (kept.length === parts.length) return;
    curFrame().layers[ui.layer] = kept;
    ui.selection.clear();
    renderLight();
  }

  stage.addEventListener("pointermove", (e) => {
    const pt = stagePoint(e);
    ui.hover = { x: snap(pt.x), y: snap(pt.y) };
    const d = ui.drag;
    if (!d) {
      if (ui.tool === "stamp" || ui.tool === "eraser") renderStage();
      return;
    }
    if (d.kind === "erase") eraseAt(pt);
    else if (d.kind === "anchor") setAnchor(pt);
    else if (d.kind === "stamp") stampStep(pt);
    else if (d.kind === "marquee") {
      d.b = pt;
      const x0 = Math.min(d.a.x, pt.x), x1 = Math.max(d.a.x, pt.x);
      const y0 = Math.min(d.a.y, pt.y), y1 = Math.max(d.a.y, pt.y);
      ui.selection = new Set(d.base);
      curParts().forEach((p, i) => {
        if (p.x < x1 && p.x + TS > x0 && p.y < y1 && p.y + TS > y0) ui.selection.add(i);
      });
      renderLight();
    } else if (d.kind === "move") {
      const dx = snap(pt.x - d.start.x + TS / 2), dy = snap(pt.y - d.start.y + TS / 2);
      if (!d.undone && (dx || dy)) {
        // Push the pre-move state once; positions are restored from origins first.
        const parts = curParts();
        d.origins.forEach((o) => { parts[o.i].x = o.x; parts[o.i].y = o.y; });
        pushUndo();
        d.undone = true;
      }
      const parts = curParts();
      d.origins.forEach((o) => { parts[o.i].x = o.x + dx; parts[o.i].y = o.y + dy; });
      renderLight();
    }
  });

  const endDrag = () => {
    if (!ui.drag) return;
    const wasMarquee = ui.drag.kind === "marquee";
    ui.drag = null;
    if (wasMarquee && ui.selection.size) setTool("move");
    renderAll();
  };
  stage.addEventListener("pointerup", endDrag);
  stage.addEventListener("pointercancel", endDrag);
  stage.addEventListener("pointerleave", () => { ui.hover = null; if ((ui.tool === "stamp" || ui.tool === "eraser") && !ui.drag) renderStage(); });
  stage.addEventListener("wheel", (e) => {
    if (!e.ctrlKey) return;
    e.preventDefault();
    setZoom(ui.scale + (e.deltaY < 0 ? 1 : -1));
  }, { passive: false });

  function setZoom(z) {
    ui.scale = clamp(z, 1, 16);
    renderStage();
  }

  function setTool(tool) {
    ui.tool = tool;
    for (const [id, t] of [["select-tool", "select"], ["move-tool", "move"], ["stamp-tool", "stamp"], ["eraser-tool", "eraser"], ["anchor-tool", "anchor"]]) {
      $(id).classList.toggle("active", t === tool);
      $(id).setAttribute("aria-pressed", String(t === tool));
    }
    stage.style.cursor = tool === "move" ? "move" : "crosshair";
    renderStage();
  }

  // ---- playback --------------------------------------------------------------
  function drawAnim(frameIndex) {
    const c = $("anim-canvas");
    const { w, h, ox, oy } = stageGeometry();
    const z = clamp(Math.floor(200 / h), 1, 6);
    sizeCanvas(c, w * z, h * z);
    c.style.width = "100%";
    c.style.height = "auto";
    const ctx = c.getContext("2d");
    ctx.clearRect(0, 0, c.width, c.height);
    drawFrame(ctx, frameIndex, ox, oy, z, { mirrored: $("play-mirror").checked, pin: true });
  }

  function togglePlay() {
    if (play) { stopPlay(); return; }
    const clip = model.clips[ui.clip];
    if (!clip || !clip.steps.length) return;
    play = { clip, step: 0, ticks: 0, last: performance.now() };
    $("clip-play").textContent = "■ Stop";
    requestAnimationFrame(playLoop);
  }

  function stopPlay() {
    play = null;
    $("clip-play").textContent = "▶ Play";
    $("anim-step").textContent = "—";
  }

  // Ping-pong plays 0..n-1 then back down to 1, so the end frames are not shown twice.
  function pingpongSteps(clip) {
    const s = clip.steps;
    if (!clip.pingpong || s.length < 3) return s;
    return s.concat(s.slice(1, -1).reverse());
  }

  function playLoop(now) {
    if (!play) return;
    const { clip } = play;
    play.ticks += (now - play.last) * 0.06;
    play.last = now;
    let guard = 0;
    const steps = pingpongSteps(clip);
    if (play.step >= steps.length) play.step = 0;
    while (steps.length && play.ticks >= steps[play.step].ticks && guard++ < 64) {
      play.ticks -= steps[play.step].ticks;
      if (play.step + 1 < steps.length) play.step++;
      else if (clip.loop) play.step = 0;
      else { play.ticks = 0; break; }
    }
    const step = steps[play.step];
    if (!step) { stopPlay(); return; }
    drawAnim(step.frame);
    $("anim-step").textContent = `step ${play.step} · frame ${step.frame}`;
    $("anim-label").textContent = clip.name;
    requestAnimationFrame(playLoop);
  }

  // ---- persistence -----------------------------------------------------------
  function projectData(embedSheets) {
    const data = {
      format: FORMAT,
      name: model.name,
      tileSize: TS,
      stage: { ...model.stage },
      collision: { ...model.collision },
      anchor: { ...model.anchor },
      hitboxAnchor: { ...model.hitboxAnchor },
      layers: model.layers.map((l) => ({
        name: l.name, source: l.source, palette: l.palette, visible: l.visible, cell: { ...l.cell }
      })),
      frames: model.frames.map((f) => ({
        name: f.name,
        ...(f.anchor ? { anchor: { x: f.anchor.x, y: f.anchor.y } } : {}),
        layers: f.layers.map((parts) => parts.map((p) => {
          const o = { sheetX: p.sheetX, sheetY: p.sheetY, x: p.x, y: p.y };
          if (p.hflip) o.hflip = true;
          if (p.vflip) o.vflip = true;
          return o;
        }))
      })),
      clips: model.clips.map((c) => ({ name: c.name, loop: c.loop, pingpong: !!c.pingpong, steps: c.steps.map((s) => ({ frame: s.frame, ticks: s.ticks })) }))
    };
    if (embedSheets) {
      data.sheets = {};
      model.layers.forEach((l) => { if (l.canvas) data.sheets[l.source] = l.canvas.toDataURL("image/png"); });
    }
    return data;
  }

  function download(filename, text) {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    a.download = filename;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  async function ensurePermission(handle, mode) {
    let p = await handle.queryPermission({ mode });
    if (p !== "granted") p = await handle.requestPermission({ mode });
    if (p !== "granted") throw new Error("Folder access was not granted.");
  }

  async function saveProject() {
    const filename = `${(model.name || "player").toLowerCase().replace(/[^a-z0-9_]/g, "_")}.metasprite.json`;
    const analysis = Pack.analyze(packLayers(), model.frames, model.clips);
    if (!analysis.frames.some((frame) => frame.sprites > 0)) {
      toast("Place at least one non-transparent tile before saving a metasprite project.");
      return;
    }
    if (serverMode) {
      try {
        if (serverCatalog.projects.includes(filename) && filename !== activeServerProject) {
          toast(`A project named ${filename} already exists. Choose another asset name.`);
          return;
        }
        for (const layer of model.layers) {
          if (layer.pending && layer.blob) {
            await serverRequest(`/api/assets/${encodeURIComponent(layer.source)}`, {
              method: "POST",
              headers: { "Content-Type": "image/png" },
              body: layer.blob
            });
            layer.pending = false;
          }
        }
        const response = await serverRequest(`/api/projects/${encodeURIComponent(filename)}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(projectData(false))
        });
        serverCatalog = await response.json();
        activeServerProject = filename;
        renderServerCatalog(filename);
        toast(`Saved ${filename}. The bridge will rebuild the ROM.`);
      } catch (e) {
        toast(`Could not save: ${e.message}`);
      }
      return;
    }
    if (!dirHandle) {
      download(filename, JSON.stringify(projectData(true), null, 1));
      toast(`Downloaded ${filename}. Put it and the layer PNGs in your assets folder.`);
      return;
    }
    try {
      await ensurePermission(dirHandle, "readwrite");
      const write = async (name, content) => {
        const w = await (await dirHandle.getFileHandle(name, { create: true })).createWritable();
        await w.write(content);
        await w.close();
      };
      for (const l of model.layers) {
        if (l.pending && l.blob) { await write(l.source, l.blob); l.pending = false; }
      }
      await write(filename, JSON.stringify(projectData(false), null, 1));
      toast(`Saved ${filename}. The bridge will rebuild the ROM.`);
    } catch (e) {
      toast(`Could not save: ${e.message}`);
    }
  }

  async function readFromFolder(name) {
    return (await dirHandle.getFileHandle(name)).getFile();
  }

  async function loadData(data, { sheets, fromFolder } = {}) {
    stopPlay();
    let converted = data;
    if (data.format === "gel-metasprite-v1") {
      const source = data.sheet?.filename || "sheet.png";
      converted = {
        ...data,
        layers: [{ name: source.replace(/\.png$/i, ""), source, palette: 0, visible: true }],
        frames: [{ name: "frame0", layers: [data.parts.map((p) => ({ sheetX: p.sheetX, sheetY: p.sheetY, x: p.x, y: p.y }))] }],
        clips: [],
        sheets: { [source]: data.sheet?.dataUrl }
      };
    } else if (data.format !== FORMAT) {
      throw new Error("Unrecognized metasprite project format.");
    }

    model.name = converted.name || "player";
    model.stage = { columns: clamp(int(converted.stage?.columns, 3), 1, 64), rows: clamp(int(converted.stage?.rows, 6), 1, 64) };
    model.collision = { width: clamp(int(converted.collision?.width, 18), 1, 128), height: clamp(int(converted.collision?.height, 46), 1, 128) };
    model.anchor = { x: int(converted.anchor?.x), y: int(converted.anchor?.y) };
    model.hitboxAnchor = { x: int(converted.hitboxAnchor?.x, model.anchor.x), y: int(converted.hitboxAnchor?.y, model.anchor.y) };
    model.layers = [];
    const embedded = converted.sheets || sheets || {};
    const missing = [];
    for (const l of converted.layers) {
      const layer = makeLayer(l.source, {
        name: l.name || l.source, palette: clamp(int(l.palette), 0, 7), visible: l.visible !== false,
        cell: { w: clamp(int(l.cell?.w, model.stage.columns), 1, 16), h: clamp(int(l.cell?.h, model.stage.rows), 1, 16) }
      });
      try {
        if (fromFolder && dirHandle) applyImage(layer, await decodeBlob(await readFromFolder(l.source)));
        else if (serverMode) applyImage(layer, await decodeBlob(await (await serverRequest(`/api/assets/${encodeURIComponent(l.source)}`)).blob()));
        else if (embedded[l.source]) applyImage(layer, await decodeBlob(await (await fetch(embedded[l.source])).blob()));
      } catch (e) {
        if (embedded[l.source]) {
          try { applyImage(layer, await decodeSource(embedded[l.source])); } catch (_) { /* fall through */ }
        }
      }
      if (layer.missing) missing.push(l.source);
      model.layers.push(layer);
    }
    model.frames = converted.frames.map((f, i) => ({
      name: f.name || `frame${i}`,
      ...(f.anchor ? { anchor: { x: int(f.anchor.x), y: int(f.anchor.y) } } : {}),
      layers: model.layers.map((_, li) => (f.layers[li] || []).map((p) => {
        const o = { sheetX: int(p.sheetX), sheetY: int(p.sheetY), x: int(p.x), y: int(p.y) };
        if (p.hflip) o.hflip = true;
        if (p.vflip) o.vflip = true;
        return o;
      }))
    }));
    if (!model.frames.length) model.frames.push(newFrame("frame0"));
    model.clips = (converted.clips || []).map((c) => ({
      name: c.name, loop: c.loop !== false, pingpong: !!c.pingpong,
      steps: c.steps.map((s) => ({ frame: clamp(int(s.frame), 0, model.frames.length - 1), ticks: clamp(int(s.ticks, 8), 1, 255) }))
    }));
    ui.layer = 0; ui.frame = 0; ui.clip = 0;
    ui.selection.clear(); ui.tileSel = null;
    ui.scale = clamp(Math.floor(420 / (model.stage.rows * TS)), 2, 12);
    undoStack.length = 0;
    $("undo-button").disabled = true;
    syncInputs();
    renderAll();
    if (missing.length) toast(`Missing art: ${missing.join(", ")}. Add the PNGs or choose the assets folder.`);
    else toast(`Loaded ${model.name}: ${model.layers.length} layers, ${model.frames.length} frames.`);
  }

  async function chooseFolder() {
    if (typeof window.showDirectoryPicker !== "function") {
      toast("This browser cannot open folders. Use Load project and Add layer PNG instead.");
      return;
    }
    try {
      const handle = await window.showDirectoryPicker({ mode: "readwrite" });
      dirHandle = handle;
      storeHandle(handle).catch(() => {});
      $("project-folder-name").textContent = `Folder: ${handle.name}`;
      const projects = [];
      for await (const [name, entry] of handle.entries()) {
        if (entry.kind === "file" && name.toLowerCase().endsWith(".metasprite.json")) projects.push(name);
      }
      folderProjects = projects;
      if (!projects.length) { toast(`No *.metasprite.json in ${handle.name}. Add layer PNGs and save to create one.`); return; }
      let pick = projects[0];
      if (projects.length > 1) {
        pick = prompt(`Open which project?\n${projects.join("\n")}`, projects[0]);
        if (!pick) return;
      }
      await loadData(JSON.parse(await (await readFromFolder(pick)).text()), { fromFolder: true });
    } catch (e) {
      if (e.name !== "AbortError") toast(`Could not open folder: ${e.message}`);
    }
  }

  async function reloadArt() {
    if (!dirHandle && !serverMode) { toast("Open the assets folder first."); return; }
    try {
      if (dirHandle) await ensurePermission(dirHandle, "read");
      let count = 0;
      for (const l of model.layers) {
        try {
          const blob = serverMode
            ? await (await serverRequest(`/api/assets/${encodeURIComponent(l.source)}`)).blob()
            : await readFromFolder(l.source);
          applyImage(l, await decodeBlob(blob));
          count++;
        } catch (_) { l.missing = true; }
      }
      renderAll();
      toast(`Reloaded ${count}/${model.layers.length} layer images.`);
    } catch (e) {
      toast(`Could not reload: ${e.message}`);
    }
  }

  function renderServerCatalog(selected = activeServerProject) {
    const projectSelect = $("server-project");
    const assetSelect = $("server-asset");
    projectSelect.textContent = "";
    projectSelect.add(new Option(serverCatalog.projects.length ? "Choose project…" : "No projects yet", ""));
    for (const name of serverCatalog.projects) projectSelect.add(new Option(name, name));
    if (selected && serverCatalog.projects.includes(selected)) projectSelect.value = selected;
    else projectSelect.value = "";

    assetSelect.textContent = "";
    if (!serverCatalog.images.length) assetSelect.add(new Option("No PNG images found", ""));
    for (const name of serverCatalog.images) assetSelect.add(new Option(name, name));
    $("server-project-label").hidden = false;
    $("server-asset-picker").hidden = false;
    $("choose-project-folder").hidden = true;
    $("project-folder-name").textContent = `Assets: ${serverCatalog.images.length} images`;
  }

  async function loadServerProject(filename) {
    if (!filename) return;
    try {
      const data = await (await serverRequest(`/api/projects/${encodeURIComponent(filename)}`)).json();
      activeServerProject = filename;
      await loadData(data);
    } catch (e) {
      toast(`Could not load ${filename}: ${e.message}`);
    }
  }

  async function addServerAsset() {
    const name = $("server-asset").value;
    if (!name) return;
    try {
      const blob = await (await serverRequest(`/api/assets/${encodeURIComponent(name)}`)).blob();
      pushUndo();
      const layer = addLayerFromDecoded(name, await decodeBlob(blob));
      ui.layer = model.layers.indexOf(layer);
      ui.tileSel = null;
      renderAll();
    } catch (e) {
      toast(`Could not add ${name}: ${e.message}`);
    }
  }

  function newProject() {
    $("new-project-name").value = "new_metasprite";
    const source = $("new-project-source");
    source.textContent = "";
    source.add(new Option("Blank project", "blank"));
    if (model.layers.length) source.add(new Option(`Current project: ${model.name}`, "current"));
    const projects = serverMode ? serverCatalog.projects : folderProjects;
    for (const project of projects) source.add(new Option(project, `project:${project}`));
    $("new-project-dialog").showModal();
    $("new-project-name").focus();
    $("new-project-name").select();
  }

  async function createProject(rawName, source) {
    const name = rawName.trim();
    const filename = `${name.toLowerCase().replace(/[^a-z0-9_]/g, "_")}.metasprite.json`;
    if (!name || /^_+\.metasprite\.json$/.test(filename)) {
      toast("Enter a name containing at least one letter or number.");
      return;
    }
    if (serverMode && serverCatalog.projects.some((project) => project.toLowerCase() === filename)) {
      toast(`A project named ${filename} already exists.`);
      return;
    }
    if (dirHandle) {
      try {
        await dirHandle.getFileHandle(filename);
        toast(`A project named ${filename} already exists.`);
        return;
      } catch (e) {
        if (e.name !== "NotFoundError") {
          toast(`Could not check the project folder: ${e.message}`);
          return;
        }
      }
    }
    let duplicate = null;
    let fromFolder = false;
    if (source === "current") duplicate = projectData(true);
    else if (source.startsWith("project:")) {
      const filename = source.slice("project:".length);
      try {
        if (serverMode) duplicate = await (await serverRequest(`/api/projects/${encodeURIComponent(filename)}`)).json();
        else if (dirHandle) {
          duplicate = JSON.parse(await (await readFromFolder(filename)).text());
          fromFolder = true;
        } else {
          toast("Open the assets folder to duplicate a saved project.");
          return;
        }
      } catch (e) {
        toast(`Could not read the project to duplicate: ${e.message}`);
        return;
      }
    }
    stopPlay();
    if (duplicate) {
      try {
        await loadData(duplicate, { fromFolder });
      } catch (e) {
        toast(`Could not duplicate project: ${e.message}`);
        return;
      }
    } else {
      model.name = name;
      model.stage = { columns: 3, rows: 6 };
      model.collision = { width: 18, height: 46 };
      model.anchor = { x: 0, y: 0 };
      model.hitboxAnchor = { x: 0, y: 0 };
      model.layers = [];
      model.frames = [newFrame("frame0")];
      model.clips = [{ name: "idle", loop: true, pingpong: false, steps: [{ frame: 0, ticks: 8 }] }];
    }
    model.name = name;
    activeServerProject = "";
    if (serverMode) $("server-project").value = "";
    $("new-project-dialog").close();
    ui.layer = 0; ui.frame = 0; ui.clip = 0;
    ui.selection.clear(); ui.tileSel = null;
    undoStack.length = 0;
    $("undo-button").disabled = true;
    syncInputs();
    renderAll();
    toast(duplicate
      ? `Started ${name} as a copy. Its layers use the same source images; save to create a separate project.`
      : `Started ${name}. Add an image, place a tile, then save to create the project file.`);
  }

  async function startServerMode() {
    if (location.protocol === "file:" || !["127.0.0.1", "localhost", "[::1]", "::1"].includes(location.hostname)) return;
    try {
      serverCatalog = await (await serverRequest("/api/catalog")).json();
      serverMode = true;
      renderServerCatalog();
      const firstProject = serverCatalog.projects[0];
      if (firstProject) {
        $("server-project").value = firstProject;
        await loadServerProject(firstProject);
      }
    } catch (e) {
      toast(`Could not connect to the metasprite server: ${e.message}`);
    }
  }

  function openHandleDb() {
    return new Promise((resolve, reject) => {
      const r = indexedDB.open("gel-metasprite-editor", 1);
      r.onupgradeneeded = () => r.result.createObjectStore("handles");
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  }

  async function storeHandle(handle) {
    const db = await openHandleDb();
    await new Promise((resolve, reject) => {
      const t = db.transaction("handles", "readwrite");
      t.objectStore("handles").put(handle, "project-directory");
      t.oncomplete = resolve; t.onerror = () => reject(t.error);
    });
    db.close();
  }

  async function restoreHandle() {
    try {
      const db = await openHandleDb();
      dirHandle = await new Promise((resolve, reject) => {
        const r = db.transaction("handles", "readonly").objectStore("handles").get("project-directory");
        r.onsuccess = () => resolve(r.result || null);
        r.onerror = () => reject(r.error);
      });
      db.close();
      if (dirHandle) $("project-folder-name").textContent = `Folder: ${dirHandle.name}`;
    } catch (_) { /* optional convenience */ }
  }

  // ---- wiring ------------------------------------------------------------------
  $("choose-project-folder").addEventListener("click", chooseFolder);
  $("reload-art").addEventListener("click", reloadArt);
  $("load-project").addEventListener("click", () => $("project-file").click());
  $("project-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    try {
      await loadData(JSON.parse(await file.text()), { fromFolder: !!dirHandle });
    } catch (err) {
      toast(`Could not load project: ${err.message}`);
    }
  });
  $("save-project").addEventListener("click", saveProject);
  $("new-project").addEventListener("click", newProject);
  $("new-project-cancel").addEventListener("click", () => $("new-project-dialog").close());
  $("new-project-form").addEventListener("submit", (e) => {
    e.preventDefault();
    createProject($("new-project-name").value, $("new-project-source").value);
  });
  $("add-layer").addEventListener("click", () => $("layer-files").click());
  $("layer-files").addEventListener("change", (e) => { addLayerFiles([...e.target.files]); e.target.value = ""; });
  $("server-project").addEventListener("change", () => loadServerProject($("server-project").value));
  $("server-add-asset").addEventListener("click", addServerAsset);
  $("asset-name").addEventListener("change", () => { pushUndo(); model.name = $("asset-name").value.trim() || "player"; renderAll(); });
  $("select-tool").addEventListener("click", () => setTool("select"));
  $("move-tool").addEventListener("click", () => setTool("move"));
  $("stamp-tool").addEventListener("click", () => setTool("stamp"));
  $("eraser-tool").addEventListener("click", () => setTool("eraser"));
  $("pack-toggle").addEventListener("click", () => {
    ui.packed = !ui.packed;
    $("pack-toggle").classList.toggle("active", ui.packed);
    renderSheet();
  });
  $("frame-anchor-reset").addEventListener("click", () => {
    if (!curFrame().anchor) return;
    pushUndo();
    delete curFrame().anchor;
    renderAll();
  });
  $("anchor-tool").addEventListener("click", () => {
    if (ui.tool === "anchor") ui.activeAnchor = ui.activeAnchor === "sprite" ? "hitbox" : "sprite";
    setTool("anchor");
    toast(`Click the canvas to place the ${ui.activeAnchor} anchor (click Anchors again to switch).`);
  });
  $("undo-button").addEventListener("click", undo);
  $("flip-h").addEventListener("click", () => flipSelection("h"));
  $("flip-v").addEventListener("click", () => flipSelection("v"));
  $("erase-selection").addEventListener("click", eraseSelection);
  $("copy-layer-all").addEventListener("click", copyLayerToAll);
  $("fill-cell").addEventListener("click", fillFromSelection);
  $("zoom-in").addEventListener("click", () => setZoom(ui.scale + 1));
  $("zoom-out").addEventListener("click", () => setZoom(ui.scale - 1));
  for (const [id, key] of [["show-onion", "showOnion"], ["show-collision", "showCollision"], ["show-grid", "showGrid"], ["show-anchor", "showAnchor"]]) {
    $(id).addEventListener("change", (e) => { ui[key] = e.target.checked; renderStage(); });
  }
  const sizeInput = (ids, apply) => ids.forEach((id) => $(id).addEventListener("change", () => { pushUndo(); apply(); syncInputs(); renderAll(); }));
  sizeInput(["stage-columns", "stage-rows"], () => {
    model.stage = { columns: clamp(int($("stage-columns").value, 1), 1, 64), rows: clamp(int($("stage-rows").value, 1), 1, 64) };
  });
  sizeInput(["collision-width", "collision-height"], () => {
    model.collision = { width: clamp(int($("collision-width").value, 1), 1, 128), height: clamp(int($("collision-height").value, 1), 1, 128) };
  });
  for (const id of ["cell-w", "cell-h"]) {
    $(id).addEventListener("change", () => {
      const l = curLayer();
      if (!l) return;
      l.cell = { w: clamp(int($("cell-w").value, 1), 1, 16), h: clamp(int($("cell-h").value, 1), 1, 16) };
      renderSheet();
    });
  }
  $("frame-add").addEventListener("click", () => frameOp("add"));
  $("frame-dup").addEventListener("click", () => frameOp("dup"));
  $("frame-del").addEventListener("click", () => frameOp("del"));
  $("frame-left").addEventListener("click", () => frameOp("left"));
  $("frame-right").addEventListener("click", () => frameOp("right"));
  $("frame-name").addEventListener("change", () => {
    if (!curFrame()) return;
    pushUndo();
    curFrame().name = $("frame-name").value.trim() || curFrame().name;
    renderAll();
  });
  $("clip-add").addEventListener("click", addClip);
  $("clip-select").addEventListener("change", () => { stopPlay(); ui.clip = int($("clip-select").value); renderClips(); });
  $("clip-name").addEventListener("change", () => { pushUndo(); model.clips[ui.clip].name = $("clip-name").value.trim() || model.clips[ui.clip].name; renderAll(); });
  $("clip-loop").addEventListener("change", () => { pushUndo(); model.clips[ui.clip].loop = $("clip-loop").checked; });
  $("clip-pingpong").addEventListener("change", () => { pushUndo(); model.clips[ui.clip].pingpong = $("clip-pingpong").checked; });
  $("clip-del").addEventListener("click", () => {
    pushUndo();
    stopPlay();
    model.clips.splice(ui.clip, 1);
    ui.clip = clamp(ui.clip, 0, Math.max(0, model.clips.length - 1));
    renderAll();
  });
  $("step-add").addEventListener("click", () => {
    pushUndo();
    model.clips[ui.clip].steps.push({ frame: ui.frame, ticks: 8 });
    renderAll();
  });
  $("clip-fill").addEventListener("click", () => {
    pushUndo();
    model.clips[ui.clip].steps = model.frames.map((_, i) => ({ frame: i, ticks: 8 }));
    renderAll();
  });
  $("clip-play").addEventListener("click", togglePlay);

  window.addEventListener("keydown", (e) => {
    if (e.target.matches("input, select, textarea")) return;
    const key = e.key.toLowerCase();
    if ((e.ctrlKey || e.metaKey) && key === "z") { e.preventDefault(); undo(); return; }
    if (key === "delete" || key === "backspace") { eraseSelection(); return; }
    if (key === "h") flipSelection("h");
    else if (key === "v") flipSelection("v");
    else if (key === "e") setTool("eraser");
    else if (key === "escape") { ui.selection.clear(); setTool("select"); renderSelectionInfo(); }
    else if (key === "," || key === "<") selectFrame(ui.frame - 1);
    else if (key === "." || key === ">") selectFrame(ui.frame + 1);
    else if (key.startsWith("arrow")) {
      const d = { arrowleft: [-1, 0], arrowright: [1, 0], arrowup: [0, -1], arrowdown: [0, 1] }[key];
      if (!d) return;
      e.preventDefault();
      if (ui.tool === "anchor") {
        pushUndo();
        const t = anchorTarget();
        t.x += d[0]; t.y += d[1];
        renderAll();
      } else if (ui.selection.size) {
        pushUndo();
        const step = e.shiftKey ? 1 : TS;
        for (const i of ui.selection) { curParts()[i].x += d[0] * step; curParts()[i].y += d[1] * step; }
        renderAll();
      }
    }
  });

  window.metaspriteEditor = { model, ui, loadData, renderAll };
  syncInputs();
  setTool("select");
  model.frames.push(newFrame("frame0"));
  renderAll();
  restoreHandle();
  startServerMode();
})();
