const test = require("node:test");
const assert = require("node:assert/strict");
const { analyze } = require("../snes-gel-vscode/semantic");
const contacts = require("../tools/contact-editor/model");

function colored(text, sources = []) {
    return analyze(text, sources).map(token => ({
        word: text.slice(token.offset, token.offset + token.length),
        type: token.type
    }));
}

test("semantic colors follow declarations across files without coloring comments or strings", () => {
    const source = `#define SPEED 2
enum Kind { FRIEND, Enemy }
struct_table Entities[10] { f16 vx; u8 flags; }
u16 global_value;`;
    const text = `fn update(u16 e, u8 small) {
    i8 direction = 0;
    Entities[e].vx = SPEED;
    Entities[Entities[e].flags].vx = direction;
    if (small == FRIEND) { u8 inner = 0; }
    inner = Enemy;
    global_value = e;
    // SPEED FRIEND direction
    print("SPEED FRIEND");
    Entities.for_each(item) { Entities[item].flags = small; }
}`;
    const tokens = colored(text, [source]);
    const types = word => tokens.filter(token => token.word === word).map(token => token.type);
    assert.deepEqual(types("vx"), ["gelF16", "gelF16"]);
    assert.deepEqual(types("flags"), ["gelU8", "gelU8"]);
    assert.deepEqual(types("SPEED"), ["gelDefine"]);
    assert.deepEqual(types("FRIEND"), ["gelEnum"]);
    assert.deepEqual(types("Enemy"), ["gelEnum"]);
    assert.deepEqual(types("direction"), ["gelI8", "gelI8"]);
    assert.deepEqual(types("inner"), ["gelU8", "gelU8"]);
    assert.deepEqual(types("global_value"), ["gelU16"]);
    assert.deepEqual(types("item"), ["gelU16", "gelU16"]);
    assert.ok(types("e").every(type => type === "gelU16"));
});

test("semantic colors respect local shadowing and leave ambiguous globals alone", () => {
    const text = "fn one(u8 value) { value = shared; } fn two() { shared = 1; }";
    const tokens = colored(text, ["u16 value; u8 shared;", "u16 shared;"]);
    assert.deepEqual(tokens.filter(t => t.word === "value").map(t => t.type), ["gelU8", "gelU8"]);
    assert.equal(tokens.filter(t => t.word === "shared").length, 0);
});

test("contact generation separates directions and phases", () => {
    const data = { version: 1, interactions: [
        { self: "OBJ_BULLET", other: "OBJ_PLAYER", enter: "hit", stay: "", exit: "leave" },
        { self: "OBJ_PLAYER", other: "OBJ_BULLET", enter: "", stay: "touch", exit: "" }
    ] };
    const gel = contacts.generate(data);
    assert.match(gel, /fn entity_on_contact\(u8 self, u8 other\)/);
    assert.match(gel, /case OBJ_BULLET:\n\s+switch \(other_type\) {\n\s+case OBJ_PLAYER:\n\s+hit\(self, other\)/);
    assert.match(gel, /fn entity_on_stay[\s\S]*case OBJ_PLAYER:[\s\S]*case OBJ_BULLET:[\s\S]*touch\(self, other\)/);
    assert.match(gel, /fn entity_on_exit[\s\S]*leave\(self, other\)/);
    assert.equal(contacts.validate(JSON.parse(JSON.stringify(data))).length, 2);
});

test("contact validation rejects malformed, duplicate and unsafe identifiers", () => {
    const row = { self: "A", other: "B", enter: "hit", stay: "", exit: "" };
    assert.throws(() => contacts.validate({ version: 1, interactions: [row, row] }), /duplicate/);
    assert.throws(() => contacts.generate({ version: 1, interactions: [{ ...row, enter: "hit(); evil" }] }), /identifier/);
    assert.throws(() => contacts.generate({ version: 1, interactions: [{ ...row, self: "if" }] }), /identifier/);
    assert.throws(() => contacts.validate({ version: 2, interactions: [] }), /version 1/);
    assert.match(contacts.generate({ version: 1, interactions: [] }), /fn entity_on_exit/);
});
