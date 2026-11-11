(function () {
    "use strict";
    const $ = id => document.getElementById(id);
    const mapCanvas = $("map"), paletteCanvas = $("palette");
    const mapContext = mapCanvas.getContext("2d"), paletteContext = paletteCanvas.getContext("2d");
    const tileSize = 8, paletteScale = 2, flagH = 0x80000000, flagV = 0x40000000;
    let scale = 2;
    let level = null, selectedObject = null, dirty = false, gesture = null, history = null, baseline = null, saving = false;
    let stamp = { width: 1, height: 1, gids: [] }, paletteSelection = null, mapSelection = null;
    let tileChanges = new Map(), objectChanges = new Map(), images = new Map(), loadedImages = new Map();

    async function request(url, options) {
        const response = await fetch(url, options);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        return data;
    }

    async function image(name) {
        if (!images.has(name)) {
            const promise = new Promise((resolve, reject) => {
                const img = new Image();
                img.onload = () => { loadedImages.set(name, img); resolve(img); };
                img.onerror = () => reject(new Error(`Could not load image "${name}".`));
                img.src = `/api/tile-editor/images/${encodeURIComponent(name)}`;
            });
            images.set(name, promise);
        }
        return images.get(name);
    }

    function allObjects() {
        return level.map.layers.filter(layer => layer.type === "objectgroup")
            .flatMap(layer => layer.objects || []);
    }
    function objectParam(object) {
        const prop = (object.properties || []).find(item => item.name === "param");
        return prop ? Number(prop.value) : 0;
    }
    function objectClass(object) { return object.class || object.type || object.name || ""; }
    function variant(object) {
        const definition = level.objects.find(item => item.class.toLowerCase() === objectClass(object).toLowerCase());
        return definition && (definition.variants || []).find(item => item.id === objectParam(object));
    }
    function spriteFor(object) {
        const entry = variant(object);
        return entry && entry.metasprite_id != null
            ? level.sprites.find(item => item.id === entry.metasprite_id) : null;
    }
    function origin(object) {
        return { x: object.x, y: object.y - (object.gid ? (object.height || 0) : 0) };
    }
    function snapshot() {
        return {
            layers: level.map.layers.filter(layer => level.editable_layers.includes(layer.id))
                .map(layer => ({ id: layer.id, data: layer.data.slice() })),
            objects: allObjects().map(object => ({ id: object.id, x: object.x, y: object.y, param: objectParam(object) }))
        };
    }
    function setObject(object, state) {
        object.x = state.x; object.y = state.y;
        const props = object.properties || (object.properties = []);
        let prop = props.find(item => item.name === "param");
        if (!prop && state.param !== 0) { prop = { name: "param", type: "int", value: state.param }; props.push(prop); }
        if (prop) prop.value = state.param;
    }
    function syncChanges() {
        tileChanges.clear(); objectChanges.clear();
        const current = snapshot();
        for (const layer of current.layers) {
            const original = baseline.layers.find(item => item.id === layer.id);
            layer.data.forEach((gid, index) => {
                if (gid !== original.data[index]) tileChanges.set(`${layer.id}:${index}`, { layer: layer.id, index, gid });
            });
        }
        for (const object of current.objects) {
            const original = baseline.objects.find(item => item.id === object.id);
            if (object.x !== original.x || object.y !== original.y || object.param !== original.param)
                objectChanges.set(object.id, object);
        }
        dirty = tileChanges.size > 0 || objectChanges.size > 0;
        $("save").disabled = !dirty || saving;
        $("undo").disabled = !history.canUndo || saving;
        $("redo").disabled = !history.canRedo || saving;
        for (const id of ["maps", "reload", "layers", "objects", "object-x", "object-y", "object-param"])
            $(id).disabled = saving;
        document.querySelectorAll('input[name="tool"]').forEach(input => { input.disabled = saving; });
    }
    function record() { history.record(snapshot()); syncChanges(); }
    function restore(state) {
        if (!state) return;
        for (const layer of state.layers) level.map.layers.find(item => item.id === layer.id).data = layer.data;
        for (const object of state.objects) setObject(allObjects().find(item => item.id === object.id), object);
        syncChanges();
        selectObject(selectedObject);
    }
    function canDiscard() { return !dirty || window.confirm("Discard unsaved map edits?"); }
    function tool() { return document.querySelector('input[name="tool"]:checked').value; }
    function canvasPoint(event, canvas, factor) {
        const rect = canvas.getBoundingClientRect();
        return { x: (event.clientX - rect.left) * canvas.width / rect.width / factor,
                 y: (event.clientY - rect.top) * canvas.height / rect.height / factor };
    }
    function canvasSetup(canvas, context) {
        context.imageSmoothingEnabled = false;
        canvas.style.width = `${canvas.width}px`;
        canvas.style.height = `${canvas.height}px`;
    }
    function cell(context, img, tile, x, y, h, v) {
        const ts = level.tileset;
        const sx = (ts.margin || 0) + (tile % ts.columns) * (8 + (ts.spacing || 0));
        const sy = (ts.margin || 0) + Math.floor(tile / ts.columns) * (8 + (ts.spacing || 0));
        context.save();
        context.translate(x + (h ? 8 : 0), y + (v ? 8 : 0));
        context.scale(h ? -1 : 1, v ? -1 : 1);
        context.drawImage(img, sx, sy, 8, 8, 0, 0, 8, 8);
        context.restore();
    }
    async function drawPalette() {
        const ts = level.tileset, img = await image(level.image);
        paletteCanvas.width = ts.columns * 8 * paletteScale;
        paletteCanvas.height = Math.ceil(ts.tilecount / ts.columns) * 8 * paletteScale;
        canvasSetup(paletteCanvas, paletteContext);
        paletteContext.scale(paletteScale, paletteScale);
        for (let i = 0; i < ts.tilecount; i++) cell(paletteContext, img, i, i % ts.columns * 8, Math.floor(i / ts.columns) * 8, false, false);
        paletteContext.strokeStyle = "#ffcf49";
        paletteContext.lineWidth = 1;
        if (paletteSelection) {
            paletteContext.strokeRect(paletteSelection.x * 8 + .5, paletteSelection.y * 8 + .5,
                paletteSelection.width * 8 - 1, paletteSelection.height * 8 - 1);
        }
        if (paletteSelection && stamp.width === 1 && stamp.height === 1) {
            const selectedTile = stamp.gids[0] - level.firstgid;
            const meta = (ts.tiles || []).find(item => item.id === selectedTile);
            const properties = meta && meta.properties ? meta.properties.map(p => `${p.name}: ${p.value}`).join(", ") : "no properties";
            $("tile-info").textContent = `Tile ${selectedTile} (GID ${stamp.gids[0]}): ${properties}`;
        } else $("tile-info").textContent = `Stamp: ${stamp.width} × ${stamp.height} tiles${paletteSelection ? "" : " from map"}.`;
    }
    function hitbox(object) {
        const point = origin(object), fields = variant(object)?.fields || {};
        const w = Number(fields.w), h = Number(fields.h);
        if (Number.isFinite(w) && w > 0 && Number.isFinite(h) && h > 0)
            return { x: point.x, y: point.y, w, h };
        const width = Number(object.width), height = Number(object.height);
        if (width > 0 && height > 0) return { x: point.x, y: point.y, w: width, h: height };
        return { x: point.x - 4, y: point.y - 4, w: 8, h: 8 };
    }
    function drawSprite(object) {
        const sprite = spriteFor(object);
        if (!sprite) return;
        const data = sprite.data;
        const frameIndex = data.clips && data.clips[0] && data.clips[0].steps && data.clips[0].steps[0]
            ? data.clips[0].steps[0].frame : 0;
        const frame = data.frames && data.frames[frameIndex];
        if (!frame || !Array.isArray(frame.layers) || !Array.isArray(data.layers)) {
            throw new Error(`Metasprite ${sprite.id} has no previewable frame.`);
        }
        const point = origin(object), hit = data.hitboxAnchor || { x: 0, y: 0 };
        const anchor = data.anchor || { x: 0, y: 0 }, frameAnchor = frame.anchor || anchor;
        const dx = frameAnchor.x - anchor.x, dy = frameAnchor.y - anchor.y;
        const order = [...new Set((frame.order || []).filter(i => Number.isInteger(i) && i >= 0 && i < frame.layers.length))];
        for (let i = 0; i < frame.layers.length; i++) if (!order.includes(i)) order.push(i);
        for (const i of order) {
            if (!data.layers[i]) throw new Error(`Metasprite ${sprite.id} has an invalid layer.`);
            const img = loadedImages.get(data.layers[i].source);
            for (const part of frame.layers[i]) {
                mapContext.save();
                const x = point.x + part.x - hit.x - dx, y = point.y + part.y - hit.y - dy;
                mapContext.translate(x + (part.hflip ? 8 : 0), y + (part.vflip ? 8 : 0));
                mapContext.scale(part.hflip ? -1 : 1, part.vflip ? -1 : 1);
                mapContext.drawImage(img, part.sheetX * 8, part.sheetY * 8, 8, 8, 0, 0, 8, 8);
                mapContext.restore();
            }
        }
    }
    let renderVersion = 0;
    async function drawMap() {
        const version = ++renderVersion;
        const img = await image(level.image);
        const spriteImages = level.sprites.flatMap(sprite => (sprite.data.layers || []).map(layer => image(layer.source)));
        await Promise.all(spriteImages);
        if (version !== renderVersion) return;
        const { width, height, layers } = level.map;
        mapCanvas.width = width * 8 * scale; mapCanvas.height = height * 8 * scale;
        canvasSetup(mapCanvas, mapContext);
        mapContext.scale(scale, scale);
        mapContext.fillStyle = "#273245"; mapContext.fillRect(0, 0, width * 8, height * 8);
        for (const layer of layers) {
            if (!level.editable_layers.includes(layer.id) || layer.visible === false) continue;
            const collision = String(layer.name).toLowerCase() === "collision";
            if (collision) mapContext.globalAlpha = .5;
            for (let i = 0; i < layer.data.length; i++) {
                const gid = layer.data[i] >>> 0, id = (gid & 0x1fffffff) - level.firstgid;
                if (!gid || id < 0 || id >= level.tileset.tilecount) continue;
                cell(mapContext, img, id, i % width * 8, Math.floor(i / width) * 8, Boolean(gid & flagH), Boolean(gid & flagV));
            }
            mapContext.globalAlpha = 1;
        }
        for (const object of allObjects()) {
            if (version !== renderVersion) return;
            drawSprite(object);
            const point = origin(object), entry = variant(object);
            const box = hitbox(object);
            mapContext.fillStyle = object.id === selectedObject ? "rgba(255, 223, 99, .25)" : "rgba(255, 90, 90, .2)";
            mapContext.strokeStyle = object.id === selectedObject ? "#ffdf63" : "#ff5a5a";
            mapContext.lineWidth = 1;
            mapContext.fillRect(box.x, box.y, box.w, box.h);
            mapContext.strokeRect(box.x + .5, box.y + .5, box.w - 1, box.h - 1);
            mapContext.fillStyle = object.id === selectedObject ? "#ffdf63" : "#fff";
            mapContext.fillRect(point.x - 2, point.y - 2, 4, 4);
            mapContext.font = "7px sans-serif";
            mapContext.fillText(`${objectClass(object)}${entry && entry.metasprite_id != null ? ` #${entry.metasprite_id}` : ""}`, point.x + 3, point.y - 3);
        }
        if (mapSelection) {
            const rect = rectangle(mapSelection.start, mapSelection.end);
            mapContext.strokeStyle = "#ffdf63";
            mapContext.lineWidth = 1;
            mapContext.strokeRect(rect.x * 8 + .5, rect.y * 8 + .5, rect.width * 8 - 1, rect.height * 8 - 1);
        }
    }
    function redraw() { drawMap().catch(error => { $("status").textContent = error.message; }); }
    function selectObject(id, center = false) {
        selectedObject = id;
        const object = allObjects().find(item => item.id === id);
        $("objects").value = id == null ? "" : String(id);
        $("object-fields").hidden = !object;
        if (!object) { $("object-info").textContent = ""; redraw(); return; }
        $("object-x").value = object.x; $("object-y").value = object.y; $("object-param").value = objectParam(object);
        const entry = variant(object), sprite = spriteFor(object);
        $("object-info").textContent = entry
            ? `Variant ${entry.id}: ${entry.name}; metasprite ${entry.metasprite_id == null ? "none" : entry.metasprite_id}${sprite ? ` (${sprite.data.name})` : entry.metasprite_id == null ? "" : " (not found)"}`
            : `No object variant found for ${objectClass(object)}, param ${objectParam(object)}.`;
        if (center) {
            const point = origin(object), scroll = document.querySelector(".map-scroll");
            scroll.scrollLeft = Math.max(0, point.x * scale - scroll.clientWidth / 2);
            scroll.scrollTop = Math.max(0, point.y * scale - scroll.clientHeight / 2);
        }
        redraw();
    }
    function updateObject() {
        const object = allObjects().find(item => item.id === selectedObject);
        if (!object) return;
        const x = Number($("object-x").value), y = Number($("object-y").value), param = Number($("object-param").value);
        if (["object-x", "object-y", "object-param"].some(id => !$(id).validity.valid || $(id).value === "") ||
            !Number.isInteger(param) || param < 0 || param > 255) {
            $("status").textContent = "Object coordinates must be 0–65535 and param must be an integer from 0 to 255.";
            return;
        }
        setObject(object, { x, y, param });
        record(); selectObject(object.id);
    }
    async function openMap(name) {
        const result = await request(`/api/tile-editor/maps/${encodeURIComponent(name)}`, { cache: "no-store" });
        level = result; images = new Map(); loadedImages = new Map(); selectedObject = null;
        scale = 2;
        while (level.map.width * 8 * scale > 16384 || level.map.height * 8 * scale > 16384 ||
               level.map.width * level.map.height * 64 * scale * scale > 16000000) scale /= 2;
        stamp = { width: 1, height: 1, gids: [level.firstgid] };
        paletteSelection = { x: 0, y: 0, width: 1, height: 1 }; mapSelection = null; gesture = null;
        tileChanges = new Map(); objectChanges = new Map(); dirty = false;
        baseline = snapshot(); history = createVisualGelHistory(baseline); syncChanges();
        $("layers").replaceChildren(...level.map.layers.filter(layer => level.editable_layers.includes(layer.id))
            .map(layer => new Option(layer.name, String(layer.id))));
        $("objects").replaceChildren(new Option("Select object", ""), ...allObjects().map(obj =>
            new Option(`${objectClass(obj)} (object ${obj.id})`, String(obj.id))));
        $("limits").textContent = `${level.map.width} × ${level.map.height} tiles (8×8 pixels). GEL: widths 32–4064 in multiples of 32; heights 32–${level.max_height}. Available ROM also limits map size.`;
        $("warnings").textContent = (level.warnings || []).join(" ");
        $("object-fields").hidden = true; $("object-info").textContent = "";
        await drawPalette(); await drawMap();
        $("status").textContent = "Map loaded.";
    }
    function rectangle(a, b) {
        return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y),
            width: Math.abs(a.x - b.x) + 1, height: Math.abs(a.y - b.y) + 1 };
    }
    function gridPoint(event, canvas, width, height) {
        const point = canvasPoint(event, canvas, canvas === paletteCanvas ? paletteScale : scale);
        return { x: Math.max(0, Math.min(width - 1, Math.floor(point.x / 8))),
            y: Math.max(0, Math.min(height - 1, Math.floor(point.y / 8))) };
    }
    function activeLayer() { return level.map.layers.find(item => item.id === Number($("layers").value)); }
    function paintAt(x, y, erase) {
        const layer = level.map.layers.find(item => item.id === Number($("layers").value));
        const width = erase ? 1 : stamp.width, height = erase ? 1 : stamp.height;
        for (let dy = 0; dy < height; dy++) for (let dx = 0; dx < width; dx++) {
            if (x + dx >= level.map.width || y + dy >= level.map.height) continue;
            const gid = erase ? 0 : stamp.gids[dy * width + dx];
            if (gid == null) continue;
            layer.data[(y + dy) * level.map.width + x + dx] = gid;
        }
    }
    function paintLine(from, to, erase) {
        let { x, y } = from;
        const dx = Math.abs(to.x - x), dy = Math.abs(to.y - y);
        const sx = x < to.x ? 1 : -1, sy = y < to.y ? 1 : -1;
        let error = dx - dy;
        while (true) {
            paintAt(x, y, erase);
            if (x === to.x && y === to.y) break;
            const twice = 2 * error;
            if (twice > -dy) { error -= dy; x += sx; }
            if (twice < dx) { error += dx; y += sy; }
        }
        redraw();
    }
    function selectPalette(end) {
        const rect = rectangle(gesture.start, end), ts = level.tileset;
        paletteSelection = rect; mapSelection = null;
        stamp = { width: rect.width, height: rect.height, gids: [] };
        for (let y = 0; y < rect.height; y++) for (let x = 0; x < rect.width; x++) {
            const index = (rect.y + y) * ts.columns + rect.x + x;
            stamp.gids.push(index < ts.tilecount ? level.firstgid + index : null);
        }
        drawPalette().catch(error => { $("status").textContent = error.message; });
        redraw();
    }
    paletteCanvas.addEventListener("pointerdown", event => {
        if (!level || saving || gesture || event.button !== 0) return;
        const point = gridPoint(event, paletteCanvas, level.tileset.columns, Math.ceil(level.tileset.tilecount / level.tileset.columns));
        if (point.y * level.tileset.columns + point.x >= level.tileset.tilecount) return;
        gesture = { kind: "palette", pointerId: event.pointerId, start: point };
        document.querySelector('input[name="tool"][value="paint"]').checked = true;
        paletteCanvas.setPointerCapture(event.pointerId);
        selectPalette(point);
    });
    paletteCanvas.addEventListener("pointermove", event => {
        if (!gesture || gesture.kind !== "palette" || gesture.pointerId !== event.pointerId) return;
        selectPalette(gridPoint(event, paletteCanvas, level.tileset.columns, Math.ceil(level.tileset.tilecount / level.tileset.columns)));
    });
    paletteCanvas.addEventListener("pointerup", event => {
        if (gesture && gesture.kind === "palette" && gesture.pointerId === event.pointerId) gesture = null;
    });
    paletteCanvas.addEventListener("pointercancel", () => { if (gesture && gesture.kind === "palette") gesture = null; });
    mapCanvas.addEventListener("pointerdown", event => {
        if (!level || saving || gesture || event.button !== 0) return;
        const p = canvasPoint(event, mapCanvas, scale);
        const point = gridPoint(event, mapCanvas, level.map.width, level.map.height);
        if (tool() === "object") {
            const object = [...allObjects()].reverse().find(obj => {
                const box = hitbox(obj);
                return p.x >= box.x && p.x < box.x + box.w && p.y >= box.y && p.y < box.y + box.h;
            });
            selectObject(object ? object.id : null);
            if (!object) return;
            gesture = { kind: "object", pointerId: event.pointerId, start: p,
                x: object.x, y: object.y, object, before: snapshot() };
        } else if (tool() === "select") {
            gesture = { kind: "select", pointerId: event.pointerId, start: point };
            mapSelection = { start: point, end: point };
            redraw();
        } else {
            gesture = { kind: "paint", pointerId: event.pointerId, last: point,
                erase: tool() === "erase", before: snapshot() };
            mapSelection = null;
            paintLine(point, point, gesture.erase);
        }
        mapCanvas.setPointerCapture(event.pointerId);
    });
    mapCanvas.addEventListener("pointermove", event => {
        if (!gesture || gesture.pointerId !== event.pointerId) return;
        if (gesture.kind === "object") {
            const p = canvasPoint(event, mapCanvas, scale);
            gesture.object.x = Math.max(0, Math.min(65535, Math.round(gesture.x + p.x - gesture.start.x)));
            const minY = gesture.object.gid ? (gesture.object.height || 0) : 0;
            gesture.object.y = Math.max(minY, Math.min(65535, Math.round(gesture.y + p.y - gesture.start.y)));
            selectObject(gesture.object.id);
        } else if (gesture.kind === "select") {
            mapSelection.end = gridPoint(event, mapCanvas, level.map.width, level.map.height);
            redraw();
        } else if (gesture.kind === "paint") {
            const point = gridPoint(event, mapCanvas, level.map.width, level.map.height);
            paintLine(gesture.last, point, gesture.erase); gesture.last = point;
        }
    });
    mapCanvas.addEventListener("pointerup", event => {
        if (!gesture || gesture.pointerId !== event.pointerId) return;
        if (gesture.kind === "select") {
            const rect = rectangle(mapSelection.start, mapSelection.end), gids = [], layer = activeLayer();
            for (let y = 0; y < rect.height; y++) for (let x = 0; x < rect.width; x++)
                gids.push(layer.data[(rect.y + y) * level.map.width + rect.x + x] >>> 0);
            if (gids.some(gid => gid !== 0 && ((gid & 0x1fffffff) < level.firstgid ||
                (gid & 0x1fffffff) >= level.firstgid + level.tileset.tilecount))) {
                $("status").textContent = "Selection contains tiles outside the first tileset and cannot be stamped.";
                mapSelection = null; redraw();
            } else {
                stamp = { width: rect.width, height: rect.height, gids };
                paletteSelection = null;
                drawPalette().catch(error => { $("status").textContent = error.message; });
                document.querySelector('input[name="tool"][value="paint"]').checked = true;
                $("status").textContent = `Selected ${rect.width} × ${rect.height} tiles. Paint to stamp them.`;
            }
        } else if (gesture.kind === "paint" || gesture.kind === "object") record();
        gesture = null;
    });
    mapCanvas.addEventListener("pointercancel", () => {
        if (!gesture) return;
        if (gesture.before) restore(gesture.before);
        if (gesture.kind === "select") { mapSelection = null; redraw(); }
        gesture = null;
    });
    $("objects").addEventListener("change", () => selectObject($("objects").value === "" ? null : Number($("objects").value), true));
    for (const id of ["object-x", "object-y", "object-param"]) $(id).addEventListener("change", updateObject);
    $("maps").addEventListener("change", async () => {
        if (!canDiscard()) { $("maps").value = level.name; return; }
        try { await openMap($("maps").value); } catch (error) { $("status").textContent = error.message; }
    });
    $("reload").addEventListener("click", async () => {
        if (!canDiscard() || !$("maps").value) return;
        try { await openMap($("maps").value); } catch (error) { $("status").textContent = error.message; }
    });
    $("save").addEventListener("click", async () => {
        if (saving || gesture || !dirty) return;
        saving = true; syncChanges();
        try {
            const result = await request("/api/tile-editor/maps", { method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: level.name, project_path: level.project_path, hash: level.hash, tiles: [...tileChanges.values()], objects: [...objectChanges.values()] }) });
            if (typeof result.hash !== "string") throw new Error("Save response did not contain the map hash; reload before saving again.");
            level.hash = result.hash;
            baseline = snapshot(); syncChanges();
            $("status").textContent = "Map saved.";
        } catch (error) { $("status").textContent = `Could not save: ${error.message}`; }
        finally { saving = false; if (level && history) syncChanges(); }
    });
    $("undo").addEventListener("click", () => { if (!saving && !gesture) restore(history.undo()); });
    $("redo").addEventListener("click", () => { if (!saving && !gesture) restore(history.redo()); });
    document.addEventListener("keydown", event => {
        const target = event.target;
        if (!history || saving || gesture || !(event.ctrlKey || event.metaKey) || event.altKey ||
            target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
        const key = event.key.toLowerCase();
        if (key !== "z" && key !== "y") return;
        event.preventDefault();
        restore(key === "y" || event.shiftKey ? history.redo() : history.undo());
    });
    window.addEventListener("beforeunload", event => {
        if (dirty || (gesture && gesture.before && JSON.stringify(snapshot()) !== JSON.stringify(gesture.before))) {
            event.preventDefault(); event.returnValue = "";
        }
    });
    (async () => {
        try {
            const data = await request("/api/tile-editor/maps");
            $("maps").replaceChildren(...data.maps.map(name => new Option(name, name)));
            if (data.maps.length) await openMap(data.maps[0]);
            else $("status").textContent = "No level*.tmj maps found in the project's assets folder.";
        } catch (error) { $("status").textContent = `Could not open map: ${error.message}`; }
    })();
})();