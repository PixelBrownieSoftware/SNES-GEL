const test = require("node:test");
const assert = require("node:assert/strict");
const globalEditor = require("../tools/visual-gel/global-editor");

test("global controls round trip normal, persist, direct page and MMIO declarations", () => {
    for (const source of [
        "u8 lives = 3;", "persist u16 cam_x = 0;", "dp u16 pad;", "mmio u8 INIDISP @ 0x2100;",
        "u16 total = (a + 3) * 2;", "u16 value /* note */ = FLAG;"]) {
        assert.equal(globalEditor.emit(globalEditor.create(source)), source);
    }
});

test("edit type, name, storage, address and value without rewriting comments", () => {
    const model = globalEditor.create("dp  u8  score = 2 /* keep */;");
    model.type = "u16";
    model.name = "points";
    model.storage = "persist";
    model.value = "5";
    assert.equal(globalEditor.emit(model), "persist  u16  points = 5 /* keep */;");
});

test("optional address and initial value can be added and removed", () => {
    const model = globalEditor.create("u8 value;");
    model.storage = "mmio";
    model.address = "0x2100";
    model.value = "3";
    const code = globalEditor.emit(model);
    assert.match(code, /^mmio u8 value\s+@ 0x2100\s+= 3;$/);
    const reloaded = globalEditor.create(code);
    assert.equal(reloaded.address, "0x2100");
    assert.equal(reloaded.value, "3");
    reloaded.storage = "";
    reloaded.address = "";
    reloaded.value = "";
    const restored = globalEditor.create(globalEditor.emit(reloaded));
    assert.equal(restored.storage, "");
    assert.equal(restored.address, "");
    assert.equal(restored.value, "");
});

test("embedded initializer comments cannot be lost when editing", () => {
    const model = globalEditor.create("u16 score = first /* keep */ + 1;");
    assert.equal(globalEditor.emit(model), model.code);
    model.value = "2";
    assert.throws(() => globalEditor.emit(model), /contains comments/);
});

test("compiler storage requirements are surfaced before saving", () => {
    const model = globalEditor.create("u8 value;");
    model.storage = "persist";
    model.address = "0x200";
    assert.throws(() => globalEditor.emit(model), /cannot have a fixed address/);
    model.storage = "mmio";
    model.address = "";
    assert.throws(() => globalEditor.emit(model), /require a fixed address/);
});
