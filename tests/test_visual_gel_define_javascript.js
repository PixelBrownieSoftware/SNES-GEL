const test = require("node:test");
const assert = require("node:assert/strict");
const define = require("../tools/visual-gel/define-editor");

test("edit only define name and value while preserving spacing and comments", () => {
    const source = "#define  SPEED\t  0.3  // explanation";
    const model = define.create(source);
    assert.equal(model.name, "SPEED");
    assert.equal(model.value, "0.3");
    assert.equal(define.emit(model), source);
    model.name = "RATE";
    model.value = "1.5";
    assert.equal(define.emit(model), "#define  RATE\t  1.5  // explanation");
});

test("define expressions and attached comments round trip", () => {
    for (const source of ["#define X 14//X", "#define X Entities[0].x",
        '#define X "hello//world" // comment', "#define X -3 /* comment */"]) {
        assert.equal(define.emit(define.create(source)), source);
    }
});

test("embedded comments and multiline values cannot be silently discarded", () => {
    assert.throws(() => define.create("#define X 1 /* note */ + 2"), /comment inside/);
    const model = define.create("#define X 1");
    model.value = "1\n#define Y 2";
    assert.throws(() => define.emit(model), /single line/);
});
