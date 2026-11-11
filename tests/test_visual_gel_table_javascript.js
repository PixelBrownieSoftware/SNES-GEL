const test = require("node:test");
const assert = require("node:assert/strict");
const table = require("../tools/visual-gel/table-editor");

test("table controls patch fields and header without losing comments or CRLF", () => {
    const source = "struct_table Entities[10] @ 0x300 interleaved {\r\n  u8 x; // keep\r\n  u16 y;\r\n}";
    const model = table.create(source);
    model.name = "Actors";
    model.size = "LIMIT";
    model.fields[0].name = "horizontal";
    model.fields[1].type = "i16";
    assert.equal(table.emit(model), source.replace("Entities[10]", "Actors[LIMIT]")
        .replace("u8 x", "u8 horizontal").replace("u16 y", "i16 y"));
});

test("adding and removing fields preserves comments and allows empty header options", () => {
    const model = table.create("struct_table T[2] {\n  u8 x; // comment\n}");
    model.fields.splice(0, 1);
    model.fields.push({ type: "u16", name: "value", origin: null });
    model.address = "0x200";
    model.interleaved = true;
    const code = table.emit(model);
    assert.match(code, /@ 0x200/);
    assert.match(code, /interleaved/);
    assert.match(code, /\/\/ comment/);
    assert.match(code, /u16 value;/);
    assert.doesNotMatch(code, /u8 x;/);
    assert.equal(table.create(code).fields[0].name, "value");
});

test("unsupported table syntax is reported instead of discarded", () => {
    assert.throws(() => table.create("struct_table T[2] { unknown field; }"), /Unsupported/);
});
