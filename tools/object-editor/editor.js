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
  function renderVariant(obj, variant) {
    const box = el("fieldset", undefined, "variant");
    box.append(el("legend", `Variant ${variant.name || variant.id}`));
    const id = input("ID (Tiled param, automatic)", variant.id, null, "number");
    id.children[0].readOnly = true;
    box.append(
      id,
      input("Name", variant.name, value => { variant.name = value; }),
      input("Metasprite reference ID (optional)", variant.metasprite_id, value => { variant.metasprite_id = value; }, "number"),
      input("Palette ID 0–7 (optional)", variant.palette_id, value => { variant.palette_id = value; }, "number")
    );
    box.append(el("p", "Metasprite and palette IDs are stored data; rendering is unchanged.", "hint"));
    box.append(el("h4", "Entity field overrides"));
    if (!obj.editable_fields.length) box.append(el("p", "Enable entity fields above to override them.", "hint"));
    for (const field of obj.editable_fields) {
      box.append(input(`${field} (${entityFields[field] || "unknown"})`, variant.fields[field],
        value => { variant.fields[field] = value; }, "number", entityFields[field]));
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
