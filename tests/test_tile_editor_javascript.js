const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { createHistory } = require("../tools/visual-gel/history");

async function setup(initialTiles = {}, width = 32, height = 32) {
    const strokes = [];
    const context = {
        scale() {}, translate() {}, save() {}, restore() {}, drawImage() {}, fillRect() {}, fillText() {},
        strokeRect(...args) { strokes.push({ color: this.strokeStyle, args }); }
    };
    class Element {
        constructor(id) {
            this.id = id; this.value = ""; this.style = {}; this.listeners = {};
            this.validity = { valid: true }; this.clientWidth = 300; this.clientHeight = 300;
        }
        addEventListener(name, callback) { this.listeners[name] = callback; }
        replaceChildren(...options) { this.options = options; this.value = options[0]?.value || ""; }
        getContext() { return context; }
        getBoundingClientRect() { return { left: 0, top: 0, width: this.width, height: this.height }; }
        setPointerCapture() {}
        dispatch(name, event = {}) { return this.listeners[name]({ button: 0, pointerId: 1, ...event }); }
    }
    const ids = ["map", "palette", "save", "undo", "redo", "maps", "reload", "layers",
        "objects", "object-x", "object-y", "object-param", "object-fields", "object-info",
        "tile-info", "limits", "warnings", "status"];
    const nodes = Object.fromEntries(ids.map(id => [id, new Element(id)]));
    const radio = Object.fromEntries(["paint", "erase", "select", "object"].map(value =>
        [value, { value, checked: value === "paint" }]));
    const document = {
        getElementById: id => nodes[id],
        querySelector(selector) {
            if (selector === ".map-scroll") return new Element("scroll");
            if (selector.includes(":checked")) return Object.values(radio).find(item => item.checked);
            const value = selector.match(/value="([^"]+)"/)[1];
            return { set checked(checked) {
                if (checked) Object.values(radio).forEach(item => { item.checked = item.value === value; });
            } };
        },
        querySelectorAll: () => Object.values(radio),
        addEventListener() {}
    };
    const data = {
        name: "level.tmj", project_path: "project", hash: "hash",
        map: { width, height, layers: [
            { id: 1, name: "tiles", type: "tilelayer", data: Array(width * height).fill(0) },
            { id: 2, type: "objectgroup", objects: [
                { id: 1, class: "Test", x: 32, y: 24, properties: [{ name: "param", value: 0 }] }
            ] }
        ] },
        tileset: { columns: 4, tilecount: 16 }, firstgid: 1, image: "terrain.png",
        editable_layers: [1], max_height: 314, warnings: [], sprites: [],
        objects: [{ class: "Test", variants: [{ id: 0, name: "Test", fields: { w: 22, h: 45 } }] }]
    };
    for (const [index, gid] of Object.entries(initialTiles)) data.map.layers[0].data[Number(index)] = gid;
    let payload;
    const fetch = async (url, options = {}) => {
        if (options.method === "POST") { payload = JSON.parse(options.body); return { ok: true, json: async () => ({ hash: "saved-hash" }) }; }
        return { ok: true, json: async () => url.endsWith("/maps") ? { maps: ["level.tmj"] } : structuredClone(data) };
    };
    class Image {
        set src(value) { queueMicrotask(() => this.onload()); }
    }
    class Option { constructor(text, value) { this.text = text; this.value = value; } }
    const window = { addEventListener() {}, confirm: () => true };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../tools/tile-editor/editor.js"), "utf8"),
        { document, window, fetch, Image, Option, createVisualGelHistory: createHistory });
    await new Promise(resolve => setImmediate(resolve));
    const selectTool = value => Object.values(radio).forEach(item => { item.checked = item.value === value; });
    const drag = (id, x1, y1, x2, y2) => {
        const el = nodes[id];
        el.dispatch("pointerdown", { clientX: x1 * 2, clientY: y1 * 2 });
        el.dispatch("pointermove", { clientX: x2 * 2, clientY: y2 * 2 });
        el.dispatch("pointerup");
    };
    return { nodes, strokes, drag, selectTool, saved: () => payload };
}

test("very wide maps scale down while palette selection and painting stay accurate", async () => {
    const { nodes, saved } = await setup({}, 4064, 32);
    assert.equal(nodes.map.width, 4064 * 4);
    assert.equal(nodes.palette.width, 4 * 8 * 2);
    nodes.palette.dispatch("pointerdown", { clientX: 18, clientY: 2 });
    nodes.palette.dispatch("pointerup");
    nodes.map.dispatch("pointerdown", { clientX: 4063 * 4 + 1, clientY: 1 });
    nodes.map.dispatch("pointerup");
    await nodes.save.dispatch("click");
    assert.deepEqual(saved().tiles.map(({ index, gid }) => [index, gid]), [[4063, 2]]);
});

test("rectangular palette and map stamps are undoable", async () => {
    const { nodes, drag, selectTool, saved } = await setup();
    drag("palette", 1, 1, 9, 9);
    await new Promise(resolve => setImmediate(resolve));
    assert.match(nodes["tile-info"].textContent, /2 × 2/);
    drag("map", 1, 1, 1, 1);
    assert.equal(nodes.save.disabled, false);
    nodes.undo.dispatch("click");
    assert.equal(nodes.save.disabled, true);
    nodes.redo.dispatch("click");
    selectTool("select");
    drag("map", 1, 1, 9, 9);
    drag("map", 33, 1, 33, 1);
    await nodes.save.dispatch("click");
    assert.deepEqual(saved().tiles.map(({ index, gid }) => [index, gid]),
        [[0, 1], [1, 2], [4, 1], [5, 2], [32, 5], [33, 6], [36, 5], [37, 6]]);
});

test("map stamps preserve flipped tiles and clear destination cells copied as empty", async () => {
    const flipped = 0x80000002;
    const { nodes, drag, selectTool, saved } = await setup({ 0: flipped, 5: 3 });
    selectTool("select");
    drag("map", 1, 1, 9, 1);
    drag("map", 33, 1, 33, 1);
    await nodes.save.dispatch("click");
    assert.deepEqual(saved().tiles.map(({ index, gid }) => [index, gid]), [[4, flipped], [5, 0]]);
});

test("cancelled strokes restore the map and do not enter history", async () => {
    const { nodes } = await setup();
    nodes.map.dispatch("pointerdown", { clientX: 2, clientY: 2 });
    nodes.map.dispatch("pointercancel");
    assert.equal(nodes.save.disabled, true);
    assert.equal(nodes.undo.disabled, true);
});

test("hitbox drag and numeric object edits have separate undo and redo steps", async () => {
    const { nodes, strokes, drag, selectTool, saved } = await setup();
    await new Promise(resolve => setImmediate(resolve));
    assert.ok(strokes.some(stroke => stroke.color === "#ff5a5a" &&
        JSON.stringify(stroke.args) === JSON.stringify([32.5, 24.5, 21, 44])));
    selectTool("object");
    drag("map", 50, 60, 60, 67);
    assert.equal(nodes["object-x"].value, 42);
    assert.equal(nodes["object-y"].value, 31);
    nodes["object-param"].value = "1";
    nodes["object-param"].dispatch("change");
    nodes.undo.dispatch("click");
    assert.equal(nodes["object-param"].value, 0);
    nodes.undo.dispatch("click");
    assert.equal(nodes["object-x"].value, 32);
    assert.equal(nodes["object-y"].value, 24);
    assert.equal(nodes.save.disabled, true);
    nodes.redo.dispatch("click");
    nodes.redo.dispatch("click");
    await nodes.save.dispatch("click");
    assert.deepEqual(saved().objects, [{ id: 1, x: 42, y: 31, param: 1 }]);
    assert.equal(nodes.save.disabled, true);
    nodes.undo.dispatch("click");
    assert.equal(nodes.save.disabled, false);
    await nodes.save.dispatch("click");
    assert.equal(saved().hash, "saved-hash");
    assert.deepEqual(saved().objects, [{ id: 1, x: 42, y: 31, param: 0 }]);
});
