const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function setup(data, fields) {
    class Element {
        constructor(tag) {
            this.tag = tag;
            this.children = [];
            this.listeners = {};
        }
        append(...nodes) { this.children.push(...nodes); }
        replaceChildren(...nodes) { this.children = nodes; }
        insertCell() { const cell = new Element("td"); this.append(cell); return cell; }
        setAttribute() {}
        addEventListener(event, callback) { this.listeners[event] = callback; }
        getContext() { return { clearRect() {} }; }
        get options() { return this.children; }
    }
    const nodes = Object.fromEntries(["objects", "status", "save", "add"].map(id => [id, new Element(id)]));
    let saved;
    const fetch = async (url, options) => {
        if (options.method === "POST") {
            saved = JSON.parse(options.body);
            return { ok: true, json: async () => ({}) };
        }
        return { ok: true, json: async () => url === "/api/objects"
            ? data : { tables: { Entities: fields } } };
    };
    const document = { getElementById: id => nodes[id], createElement: tag => new Element(tag) };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "..", "tools", "object-editor", "editor.js"), "utf8"),
        { document, fetch });
    await new Promise(resolve => setImmediate(resolve));
    return { nodes, saved: () => saved };
}

function descendants(node) {
    return [node, ...node.children.flatMap(descendants)];
}

test("saving preserves unrecognized project, object and variant properties", async () => {
    const data = {
        version: 1, metadata: { project: true }, objects: [{
            class: "Gem", flags: { movable: false, ghost: false, gravity: false, contactable: true },
            open: "stored metadata", extra: 7, editable_fields: ["w"], variants: [{
                id: 2, name: "Large", fields: { w: 20 }, values: { heal_amount: 0 },
                metadata: { note: "keep" }
            }]
        }]
    };
    const { nodes, saved: getSaved } = await setup(data, { w: "u8" });
    await nodes.save.listeners.click();
    const saved = getSaved();
    assert.deepEqual(saved.metadata, data.metadata);
    assert.equal(saved.objects[0].open, "stored metadata");
    assert.equal(saved.objects[0].extra, 7);
    assert.deepEqual(saved.objects[0].variants[0].metadata, { note: "keep" });
    assert.deepEqual(saved.objects[0].variants[0].values, { heal_amount: 0 });
    assert.match(nodes.status.textContent, /Saved/);
});

test("variant IDs are automatic, stable per object, and reuse available gaps", async () => {
    const flags = { movable: false, ghost: false, gravity: false, contactable: true };
    const variant = id => ({ id, name: `Variant ${id}`, fields: {}, values: {} });
    const data = { version: 1, objects: [
        { class: "Gem", flags, variants: [variant(0), variant(2)] },
        { class: "Spring", flags, variants: [] }
    ] };
    const { nodes, saved } = await setup(data, {});
    const buttons = text => descendants(nodes.objects).filter(node => node.tag === "button" && node.textContent === text);
    buttons("+ Variant")[0].listeners.click();
    buttons("+ Variant")[1].listeners.click();
    let ids = descendants(nodes.objects).filter(node => node.tag === "label" && node.textContent === "ID (Tiled param, automatic)");
    assert.deepEqual(ids.map(label => label.children[0].value), [0, 2, 1, 0]);
    assert.ok(ids.every(label => label.children[0].readOnly && !label.children[0].listeners.input));
    buttons("Remove variant")[0].listeners.click();
    buttons("+ Variant")[0].listeners.click();
    ids = descendants(nodes.objects).filter(node => node.tag === "label" && node.textContent === "ID (Tiled param, automatic)");
    assert.deepEqual(ids.map(label => label.children[0].value), [2, 1, 0, 0]);
    await nodes.save.listeners.click();
    assert.deepEqual(saved().objects.map(obj => obj.variants.map(item => item.id)), [[2, 1, 0], [0]]);
});

test("variant ID exhaustion is reported without adding a variant", async () => {
    const variants = Array.from({ length: 256 }, (_, id) => ({ id, name: `Variant ${id}`, fields: {}, values: {} }));
    const data = { version: 1, objects: [{ class: "Gem",
        flags: { movable: false, ghost: false, gravity: false, contactable: true }, variants }] };
    const { nodes, saved } = await setup(data, {});
    descendants(nodes.objects).find(node => node.textContent === "+ Variant").listeners.click();
    assert.match(nodes.status.textContent, /No variant IDs remain/);
    await nodes.save.listeners.click();
    assert.equal(saved().objects[0].variants.length, 256);
});

test("entity field controls use the respective GEL type colours", async () => {
    const fields = { a: "u8", b: "u16", c: "i8", d: "i16", e: "f16", unused: "i8" };
    const data = { version: 1, objects: [{ class: "Gem",
        flags: { movable: false, ghost: false, gravity: false, contactable: true },
        editable_fields: ["a", "b", "c", "d", "e"],
        variants: [{ id: 5, name: "Test", fields: { a: 10 }, values: {} }] }] };
    const { nodes, saved } = await setup(data, fields);
    const elements = descendants(nodes.objects);
    for (const [field, type] of Object.entries(fields).filter(([field]) => field !== "unused")) {
        const label = elements.find(node => node.tag === "label" && node.textContent === `${field} (${type})`);
        assert.equal(label.className, `t-${type}`);
        assert.equal(label.children[0].className, `t-${type}`);
        assert.ok(elements.some(node => node.tag === "span" && node.textContent === `${field} (${type})`
            && node.className === `field-tag t-${type}`));
    }
    assert.equal(elements.find(node => node.tag === "option" && node.value === "unused").className, "t-i8");
    await nodes.save.listeners.click();
    assert.deepEqual(saved().objects[0].variants[0].fields, { a: 10 });
});
