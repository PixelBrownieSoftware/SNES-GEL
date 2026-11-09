"use strict";

(() => {
  const rows = document.getElementById("objects");
  const status = document.getElementById("status");
  const save = document.getElementById("save");
  const flags = ["movable", "ghost", "gravity", "contactable"];
  const reservedFields = new Set(["type", "x", "y", "param", "capabilities"]);
  let entityFields = {};
  let configuration = {};
  const objects = [];
  const expanded = new WeakMap();
  const metasprites = { list: null, images: new Map() };

  function el(tag, text, cls) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (cls) node.className = cls;
    return node;
  }
  function button(text, action) {
    const node = el("button", text);
    node.type = "button";
    node.addEventListener("click", action);
    return node;
  }
  function input(label, value, onChange, type = "text", valueType = "") {
    const wrap = el("label", label);
    const node = el("input");
    node.type = type;
    node.value = value === null || value === undefined ? "" : value;
    if (valueType) {
      wrap.className = `t-${valueType}`;
      node.className = `t-${valueType}`;
    }
    if (onChange) node.addEventListener("input", () => onChange(node.value));
    wrap.append(node);
    return wrap;
  }
  function numeric(value, label, optional = false) {
    if (value === "" || value === null || value === undefined) {
      if (optional) return null;
      throw new Error(`${label} is required.`);
    }
    const number = Number(value);
    if (!Number.isFinite(number)) throw new Error(`${label} must be a number.`);
    return number;
  }
  function serialize() {
    return { ...configuration, version: 1, objects: objects.map(obj => ({
      ...obj,
      class: obj.class.trim(),
      flags: obj.flags,
      editable_fields: obj.editable_fields,
      variants: obj.variants.map(variant => ({
        ...variant,
        id: numeric(variant.id, "Variant ID"),
        name: variant.name.trim(),
        metasprite_id: numeric(variant.metasprite_id, "Metasprite ID", true),
        palette_id: numeric(variant.palette_id, "Palette ID", true),
        fields: Object.fromEntries(Object.entries(variant.fields).filter(([, value]) => value !== "")
          .map(([key, value]) => [key, numeric(value, key)])),
        values: Object.fromEntries(variant.values.map(item => [item.name, numeric(item.value, item.name)]))
      }))
    })) };
  }
  function loadImage(name) {
    if (!metasprites.images.has(name)) {
      metasprites.images.set(name, new Promise((resolve, reject) => {
        const image = new Image();
        image.onload = () => resolve(image);
        image.onerror = () => reject(new Error(`Could not load ${name}`));
        image.src = `/api/assets/${encodeURIComponent(name)}`;
      }));
    }
    return metasprites.images.get(name);
  }
  // Project order matches the packer (file name order), so the index is the generated <NAME>_MS_ID.
  function loadMetasprites() {
    if (!metasprites.list) {
      metasprites.list = (async () => {
        const catalog = await request("/api/catalog", { cache: "no-store" });
        const loaded = [];
        for (const name of [...catalog.projects].sort()) {
          const data = await request(`/api/projects/${encodeURIComponent(name)}`, { cache: "no-store" });
          if (data.format === "gel-metasprite-v2") loaded.push(data);
        }
        return loaded;
      })();
      metasprites.list.catch(() => { metasprites.list = null; });
    }
    return metasprites.list;
  }
  async function drawFirstFrame(canvas, note, rawId, variant) {
    const token = (canvas.token || 0) + 1;
    canvas.token = token;
    const context = canvas.getContext("2d");
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (rawId === "" || rawId === null || rawId === undefined) { note.textContent = "No metasprite selected."; return; }
    try {
      const projects = await loadMetasprites();
      const project = projects[Number(rawId)];
      if (canvas.token !== token) return;
      if (!project) { note.textContent = `No metasprite with ID ${rawId} (there are ${projects.length}).`; return; }
      const frame = (project.frames || [])[0];
      if (!frame) { note.textContent = `${project.name} has no frames.`; return; }
      const images = await Promise.all(project.layers.map(layer => loadImage(layer.source)));
      if (canvas.token !== token) return;
      const count = project.layers.length;
      const order = [];
      for (const li of frame.order || []) if (Number.isInteger(li) && li >= 0 && li < count && !order.includes(li)) order.push(li);
      for (let li = 0; li < count; li++) if (!order.includes(li)) order.push(li);
      const cells = [];
      for (const li of order) {
        for (const part of (frame.layers || [])[li] || []) {
          cells.push({ image: images[li], part, x: part.x, y: part.y });
        }
      }
      if (!cells.length) { note.textContent = `${project.name} frame 0 is empty.`; return; }
      // The hitbox's top-left is the project's origin (hitboxAnchor), shifted by this frame's sprite-anchor offset.
      const size = ["w", "h"].map(key => (variant?.fields?.[key] === "" || variant?.fields?.[key] == null) ? NaN : Number(variant.fields[key]));
      let hitbox = null;
      if (size.every(value => Number.isFinite(value) && value > 0)) {
        const origin = project.hitboxAnchor || project.anchor || { x: 0, y: 0 };
        const base = project.anchor || { x: 0, y: 0 };
        const own = frame.anchor || base;
        hitbox = { x: origin.x + own.x - base.x, y: origin.y + own.y - base.y, w: size[0], h: size[1] };
      }
      const minX = Math.min(...cells.map(c => c.x), hitbox ? hitbox.x : Infinity);
      const minY = Math.min(...cells.map(c => c.y), hitbox ? hitbox.y : Infinity);
      canvas.width = Math.max(...cells.map(c => c.x + 8), hitbox ? hitbox.x + hitbox.w : -Infinity) - minX;
      canvas.height = Math.max(...cells.map(c => c.y + 8), hitbox ? hitbox.y + hitbox.h : -Infinity) - minY;
      canvas.style.width = `${canvas.width * 3}px`;
      const paint = canvas.getContext("2d");
      paint.imageSmoothingEnabled = false;
      for (const { image, part, x, y } of cells) {
        paint.save();
        paint.translate(x - minX + (part.hflip ? 8 : 0), y - minY + (part.vflip ? 8 : 0));
        paint.scale(part.hflip ? -1 : 1, part.vflip ? -1 : 1);
        paint.drawImage(image, part.sheetX * 8, part.sheetY * 8, 8, 8, 0, 0, 8, 8);
        paint.restore();
      }
      if (hitbox) {
        paint.strokeStyle = "#ff5a5a";
        paint.fillStyle = "rgba(255, 90, 90, 0.2)";
        paint.lineWidth = 1;
        paint.fillRect(hitbox.x - minX, hitbox.y - minY, hitbox.w, hitbox.h);
        paint.strokeRect(hitbox.x - minX + 0.5, hitbox.y - minY + 0.5, hitbox.w - 1, hitbox.h - 1);
      }
      note.textContent = `${project.name} (first frame${hitbox ? `, hitbox ${hitbox.w}×${hitbox.h}` : ""})`;
    } catch (error) {
      if (canvas.token === token) note.textContent = `Preview unavailable: ${error.message}`;
    }
  }
  function renderVariant(obj, variant) {
    const box = el("fieldset", undefined, "variant");
    box.append(el("legend", `Variant ${variant.name || variant.id}`));
    const id = input("ID (Tiled param, automatic)", variant.id, null, "number");
    id.children[0].readOnly = true;
    const preview = el("div", undefined, "ms-preview");
    const canvas = el("canvas");
    canvas.width = canvas.height = 8;
    const note = el("span", undefined, "hint");
    preview.append(canvas, note);
    const redraw = () => drawFirstFrame(canvas, note, variant.metasprite_id, variant);
    box.append(
      id,
      input("Name", variant.name, value => { variant.name = value; }),
      input("Metasprite ID (optional)", variant.metasprite_id, value => { variant.metasprite_id = value; redraw(); }, "number"),
      preview,
      input("Palette ID 0–7 (optional)", variant.palette_id, value => { variant.palette_id = value; }, "number")
    );
    redraw();
    box.append(el("p", "The Metasprite ID is the project's position in the metasprite editor's list (the generated NAME_MS_ID). The preview shows its first frame with the hitbox (the w and h fields below) as a red box, placed at the sprite's origin; palette IDs are stored data.", "hint"));
    box.append(el("h4", "Entity field overrides"));
    if (!obj.editable_fields.length) box.append(el("p", "Enable entity fields above to override them.", "hint"));
    for (const field of obj.editable_fields) {
      box.append(input(`${field} (${entityFields[field] || "unknown"})`, variant.fields[field],
        value => { variant.fields[field] = value; if (field === "w" || field === "h") redraw(); }, "number", entityFields[field]));
    }
    box.append(el("h4", "Custom ROM values"));
    const custom = el("div", undefined, "custom-values");
    variant.values.forEach((value, index) => {
      const row = el("div", undefined, "value-row");
      row.append(input("Value name", value.name, text => { value.name = text; }),
        input("16-bit integer", value.value, text => { value.value = text; }, "number"),
        button("Remove value", () => { variant.values.splice(index, 1); render(); }));
      custom.append(row);
    });
    box.append(custom, button("+ Value", () => { variant.values.push({ name: "", value: "" }); render(); }),
      button("Remove variant", () => { obj.variants.splice(obj.variants.indexOf(variant), 1); render(); }));
    return box;
  }
  function render() {
    rows.replaceChildren();
    objects.forEach((obj, objectIndex) => {
      const row = document.createElement("tr");
      const nameCell = row.insertCell();
      const name = el("input");
      name.type = "text"; name.value = obj.class; name.setAttribute("aria-label", "Tiled class");
      name.addEventListener("input", () => { obj.class = name.value; });
      nameCell.append(name);
      for (const flag of flags) {
        const checkbox = el("input");
        checkbox.type = "checkbox"; checkbox.checked = obj.flags[flag];
        checkbox.setAttribute("aria-label", flag);
        checkbox.addEventListener("change", () => { obj.flags[flag] = checkbox.checked; });
        row.insertCell().append(checkbox);
      }
      row.insertCell().append(button("Remove", () => { objects.splice(objectIndex, 1); render(); }));
      rows.append(row);
      const detailRow = document.createElement("tr");
      const cell = detailRow.insertCell(); cell.colSpan = 6;
      const details = el("details");
      details.open = expanded.get(obj) || false;
      details.addEventListener("toggle", () => { expanded.set(obj, details.open); });
      details.append(el("summary", "Variants and editable fields"));
      details.append(el("p", "Choose which Entities fields variants can override. Empty values keep the game's defaults.", "hint"));
      const fieldList = el("div", undefined, "field-list");
      for (const field of obj.editable_fields) {
        const label = el("span", `${field} (${entityFields[field] || "unknown"})`,
          `field-tag${entityFields[field] ? ` t-${entityFields[field]}` : ""}`);
        label.append(button("Remove field", () => {
          if (obj.variants.some(v => v.fields[field] !== undefined && v.fields[field] !== "")) {
            status.textContent = `Clear ${field} in all variants before removing it.`;
            return;
          }
          obj.editable_fields = obj.editable_fields.filter(value => value !== field);
          render();
        }));
        fieldList.append(label);
      }
      const fieldSelect = el("select");
      fieldSelect.setAttribute("aria-label", "Entity field to enable");
      fieldSelect.append(el("option", "Choose entity field"));
      fieldSelect.options[0].value = "";
      for (const [field, type] of Object.entries(entityFields)) {
        if (reservedFields.has(field) || obj.editable_fields.includes(field) || !["u8", "i8", "u16", "i16", "f16"].includes(type)) continue;
        const option = el("option", `${field} (${type})`, `t-${type}`); option.value = field; fieldSelect.append(option);
      }
      details.append(fieldList, fieldSelect, button("+ Editable field", () => {
        if (!fieldSelect.value) { status.textContent = "Choose an entity field first."; return; }
        obj.editable_fields.push(fieldSelect.value); render();
      }));
      obj.variants.forEach(variant => details.append(renderVariant(obj, variant)));
      details.append(button("+ Variant", () => {
        const used = new Set(obj.variants.map(v => Number(v.id)));
        let id = 0; while (used.has(id) && id < 256) id++;
        if (id > 255) { status.textContent = "No variant IDs remain."; return; }
        obj.variants.push({ id, name: `Variant ${id}`, fields: {}, values: [], metasprite_id: null, palette_id: null });
        render();
      }));
      cell.append(details); rows.append(detailRow);
    });
  }
  async function request(url, options) {
    const response = await fetch(url, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  }
  document.getElementById("add").addEventListener("click", () => {
    objects.push({ class: "", flags: Object.fromEntries(flags.map(flag => [flag, false])),
      editable_fields: [], variants: [] }); render();
  });
  save.addEventListener("click", async () => {
    save.disabled = true;
    try {
      for (const obj of objects) for (const variant of obj.variants) {
        const names = variant.values.map(v => v.name);
        if (new Set(names).size !== names.length) throw new Error("Custom value names must be unique within a variant.");
      }
      await request("/api/objects", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(serialize()) });
      status.textContent = "Saved objects.json. Rebuild the game to use variant data.";
    } catch (error) {
      status.textContent = `Save failed: ${error.message}`;
    } finally { save.disabled = false; }
  });
  Promise.all([request("/api/objects", { cache: "no-store" }), request("/api/symbols", { cache: "no-store" })])
    .then(([data, symbols]) => {
      configuration = data;
      entityFields = symbols.tables.Entities || {};
      data.objects.forEach(obj => objects.push({
        ...obj, editable_fields: obj.editable_fields || [],
        variants: (obj.variants || []).map(variant => ({
          ...variant, fields: variant.fields || {},
          values: Object.entries(variant.values || {}).map(([name, value]) => ({ name, value }))
        }))
      }));
      render(); save.disabled = false; status.textContent = "Connected to the project.";
    }).catch(error => { status.textContent = `Could not load object definitions or entity fields: ${error.message}`; });
})();
