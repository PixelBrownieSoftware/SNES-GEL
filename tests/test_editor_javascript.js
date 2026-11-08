const test = require("node:test");
const assert = require("node:assert/strict");
const { analyze } = require("../snes-gel-vscode/semantic");
const contacts = require("../tools/contact-editor/model");
const visual = require("../tools/visual-gel/model");
const { createHistory } = require("../tools/visual-gel/history");
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

test("define compatibility distinguishes literal, fixed-point and unknown values", () => {
    const values = { GOOD: "0xFF", TOO_BIG: "256", NEGATIVE: "-1",
        DECIMAL: "1.0", SPEED: "4.4", RAW: "1", EXPR: "1 + 2",
        ALIAS: "GOOD", FIELD: "Entities[0].abil" };
    const check = (type, name) => visual.defineCompatibility(type, name, values);
    assert.equal(check("u8", "GOOD").status, "compatible");
    assert.equal(check("i8", "GOOD").status, "incompatible");
    assert.equal(check("u8", "TOO_BIG").status, "incompatible");
    assert.equal(check("u8", "NEGATIVE").status, "incompatible");
    assert.equal(check("i8", "NEGATIVE").status, "compatible");
    assert.equal(check("u8", "DECIMAL").status, "incompatible");
    assert.equal(check("f16", "SPEED").status, "compatible");
    assert.equal(check("f16", "RAW").status, "raw");
    for (const name of ["EXPR", "ALIAS", "FIELD", "MISSING"])
        assert.equal(check("u8", name).status, "unknown");
    assert.equal(check("", "GOOD").status, "unknown");
});

test("returning function calls preserve nested arguments and serialize as values", () => {
    assert.deepEqual(visual.parseCall("!health(entity, offset(1, 2))"),
        { prefix: "!", fn: "health", args: ["entity", "offset(1, 2)"] });
    assert.deepEqual(visual.parseCall("health()").args, []);
    assert.equal(visual.parseCall("health(1) + other(2)"), null);
    const body = visual.validateStatements([
        { op: "set", target: "x", value: visual.parseExpr("health(entity, offset(1, 2))") },
        { op: "if", cond: visual.parseExpr("health(entity, 1) > 0"), then: [], else: [] }
    ]);
    const lines = [];
    visual.emit(body, 0, lines);
    assert.match(lines.join("\n"), /x = health\(entity, offset\(1, 2\)\);/);
    assert.match(lines.join("\n"), /if \(health\(entity, 1\) > 0\)/);
});

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

test("object choices use contactable recipients and all other objects", () => {
    const objects = { objects: [
        { class: "PlayerStart", flags: { contactable: true } },
        { class: "Crate", flags: { contactable: false } }
    ] };
    assert.deepEqual(contacts.objectOptions(objects, true), [{ label: "PlayerStart", value: "OBJ_PLAYERSTART" }]);
    assert.equal(contacts.objectOptions(objects, false).length, 2);
    contacts.validateObjects([{ self: "OBJ_PLAYERSTART", other: "OBJ_CRATE" }], objects);
    assert.throws(() => contacts.validateObjects([{ self: "OBJ_CRATE", other: "OBJ_PLAYERSTART" }], objects), /contactable/);
    assert.throws(() => contacts.validateObjects([{ self: "PLAYER", other: "OBJ_CRATE" }], objects), /recipient/);
});

test("visual expression references survive validation and generation", () => {
    const ref = { t: "ref", name: "Entities[other].vx", category: "Fields" };
    const target = { t: "ref", name: "Entities[self].vx", category: "Fields" };
    const slot = { t: "ref", name: "Entities[other]", category: "Slots" };
    const table = { t: "ref", name: "Entities", category: "Tables" };
    const fn = { t: "ref", name: "action", category: "Functions" };
    const body = visual.validateStatements([
        { op: "set", target, value: [ref] }, { op: "free", target: slot },
        { op: "create", table, var: "" }, { op: "call", fn, args: "" }
    ]);
    assert.deepEqual(body[0].value[0], ref);
    assert.deepEqual(body[1].target, slot);
    const lines = [];
    visual.emit(body, 0, lines);
    assert.deepEqual(lines, ["Entities[self].vx = Entities[other].vx;", "Entities[other].free();",
        "Entities.create();", "action();"]);
    assert.throws(() => visual.validateStatements([{ op: "set", target: "x",
        value: [{ t: "ref", name: "x; evil", category: "Fields" }] }]), /reference/);
});

test("shared history restores snapshots and clears redo after a new edit", () => {
    const initial = { handlers: { hit: [] } };
    const history = createHistory(initial);
    initial.handlers.hit.push({ op: "set" });
    assert.equal(history.record(initial), true);
    assert.equal(history.record(initial), false);
    assert.deepEqual(history.undo(), { handlers: { hit: [] } });
    assert.equal(history.canRedo, true);
    assert.deepEqual(history.redo(), { handlers: { hit: [{ op: "set" }] } });
    history.undo();
    history.record({ handlers: {} });
    assert.equal(history.canRedo, false);
});

test("shared control-flow and statement blocks generate GEL for contact handlers", () => {
    const data = { version: 1, interactions: [
        { self: "A", other: "B", enter: "hit", stay: "", exit: "" }
    ], handlers: { hit: [
        { op: "declare", type: "u8", name: "count", value: "0" },
        { op: "while", cond: "count < 2", body: [
            { op: "assign", target: "count", operator: "*=", value: "2" },
            { op: "expr", value: "count++" }
        ] },
        { op: "for_each", table: "Entities", iterator: "item", body: [
            { op: "if", cond: "item == self", then: [{ op: "return", value: "" }], else: [] }
        ] },
        { op: "switch", cond: "count", cases: [
            { value: "1", body: [{ op: "return", value: "count" }] }
        ], default: [{ op: "return", value: "" }] }
    ] } };
    const code = contacts.generate(data);
    assert.match(code, /while \(count < 2\) \{/);
    assert.match(code, /count \*= 2;/);
    assert.match(code, /count\+\+;/);
    assert.match(code, /Entities\.for_each\(item\) \{/);
    assert.match(code, /if \(item == self\) \{[\s\S]*return;/);
    assert.match(code, /case 1:[\s\S]*return count;[\s\S]*break;[\s\S]*default:/);
    assert.throws(() => visual.validateStatements([{ op: "for_each", table: "Entities", iterator: "if", body: [] }]), /identifier/);
    assert.throws(() => visual.validateStatements([{ op: "switch", cond: "x", cases: [{ value: "1; x", body: [] }], default: null }]), /case/);
    assert.equal(visual.exprText(visual.parseExpr('"a+b"')), '"a+b"');
    assert.doesNotThrow(() => visual.validateStatements([{ op: "return", value: '"(//)"' }]));
});

test("increment and decrement blocks validate and preserve prefix or postfix", () => {
    const body = visual.validateStatements([
        { op: "increment", target: "x", prefix: false },
        { op: "decrement", target: "Entities[self].flags", prefix: true }
    ]);
    const lines = [];
    visual.emit(body, 0, lines);
    assert.deepEqual(lines, ["x++;", "--Entities[self].flags;"]);
    assert.throws(() => visual.validateStatements([{ op: "increment", target: "x;bad" }]), /target/);
});

test("shared bit_set validation uses the target width and resolves enum and define values", () => {
    const context = { document: { addEventListener() {} }, VisualGelModel: visual };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../tools/visual-gel/editor.js"), "utf8"), context);
    const editor = context.createVisualGelEditor({ status: {}, locals: ["small", "wide"],
        localTypes: { small: "u8", wide: "u16" } });
    editor.setIndex(editor.buildIndex({ globals: { counter: "u16" }, tables: { Entities: { flags: "u8" } },
        defines: ["TOO_BIG"], defineValues: { TOO_BIG: "8" },
        enums: { Bits: ["BIT_14"] }, enumValues: { BIT_14: 14 } }));
    const check = (target, bit) => editor.firstTypeProblem([{ op: "call", fn: "bit_set", args: `${target}, ${bit}` }]);
    assert.equal(check("small", "3"), "");
    assert.match(check("small", "8"), /0–7/);
    assert.match(check("Entities[small].flags", "TOO_BIG"), /0–7/);
    assert.equal(check("wide", "BIT_14"), "");
    assert.equal(check("counter", "14"), "");
    assert.match(check("counter", "16"), /0–15/);
    assert.match(check("counter", "-1"), /0–15/);
    for (const fn of ["bit_clear", "bit_test"]) {
        assert.equal(editor.firstTypeProblem([{ op: "call", fn, args: "small, 3" }]), "");
        assert.match(editor.firstTypeProblem([{ op: "call", fn, args: "small, 8" }]), /0–7/);
    }
    assert.match(editor.firstTypeProblem([{ op: "if", cond: visual.parseExpr("!bit_test(small, 9)"), then: [], else: [] }]), /0–7/);
    assert.match(editor.firstTypeProblem([{ op: "return", value: "peek8()" }]), /address/);
});

test("returning function index offers typed calls and detects missing arguments", () => {
    const context = { document: { addEventListener() {} }, VisualGelModel: visual };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../tools/visual-gel/editor.js"), "utf8"), context);
    const editor = context.createVisualGelEditor({ status: {} });
    const index = editor.buildIndex({ functions: {
        health: { params: ["u8 entity"], returnType: "i8" },
        legacy: { params: [], returnType: null }
    } });
    editor.setIndex(index);
    assert.ok(index.items.some(item => item.cat === "Returning functions" && item.label === "health" && item.cls === "t-i8"));
    assert.ok(!index.items.some(item => item.cat === "Returning functions" && item.label === "legacy"));
    const statement = value => [{ op: "if", cond: visual.parseExpr(value), then: [], else: [] }];
    assert.match(editor.firstTypeProblem(statement("health()")), /needs 1 argument/);
    assert.equal(editor.firstTypeProblem(statement("health(1)")), "");
});

test("statement chooser starts collapsed, filters choices and adds the selected statement", () => {
    class Element {
        constructor(tag) { this.tag = tag; this.children = []; this.events = {}; this.attributes = {}; }
        append(...nodes) { this.children.push(...nodes); }
        setAttribute(name, value) { this.attributes[name] = value; }
        addEventListener(name, callback) { this.events[name] = callback; }
        focus() { this.focused = true; }
        dispatch(name, event = {}) { this.events[name](event); }
    }
    const context = {
        document: { createElement: tag => new Element(tag), addEventListener() {} },
        VisualGelModel: visual
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../tools/visual-gel/editor.js"), "utf8"), context);
    const editor = context.createVisualGelEditor({ status: {} });
    const statements = [];
    let changes = 0;
    const chooser = editor.addBar(statements, rebuild => { assert.equal(rebuild, true); changes++; });
    const [toggle, panel] = chooser.children;
    const [search, choices, empty] = panel.children;
    assert.equal(chooser.tag, "details");
    assert.ok(!chooser.open);
    assert.equal(toggle.attributes["aria-label"], "Add statement");
    chooser.open = true;
    chooser.dispatch("toggle");
    assert.equal(search.focused, true);
    search.value = " INCREMENT ";
    search.dispatch("input");
    assert.deepEqual(choices.children.filter(button => !button.hidden).map(button => button.textContent), ["+ increment"]);
    assert.equal(empty.hidden, true);
    search.value = "not-a-statement";
    search.dispatch("input");
    assert.equal(empty.hidden, false);
    search.value = "for_each";
    search.dispatch("input");
    const match = choices.children.filter(button => !button.hidden);
    assert.equal(match.length, 1);
    match[0].dispatch("click");
    assert.equal(statements[0].op, "for_each");
    assert.equal(changes, 1);
    let prevented = false;
    chooser.dispatch("keydown", { key: "Escape", preventDefault() { prevented = true; } });
    assert.equal(chooser.open, false);
    assert.equal(toggle.focused, true);
    assert.equal(prevented, true);
});

test("intrinsics are recognized without losing surrounding expression syntax", () => {
    assert.deepEqual(visual.parseIntrinsic("!bit_test(Entities[e].flags, FLAG)"),
        { prefix: "!", fn: "bit_test", args: ["Entities[e].flags", "FLAG"] });
    assert.deepEqual(visual.parseIntrinsic("peek8l(LEVEL_BANK, ptr + fn(1, 2))"),
        { prefix: "", fn: "peek8l", args: ["LEVEL_BANK", "ptr + fn(1, 2)"] });
    assert.equal(visual.parseIntrinsic("bit_test(x)"), null);
    assert.equal(visual.parseIntrinsic("peek8(x) + other()"), null);
    const text = "!bit_test(Entities[e].flags, 3) || peek8(ptr + 1) == 1";
    const parts = visual.parseExpr(text);
    assert.equal(visual.parseIntrinsic(parts[0].text).fn, "bit_test");
    assert.equal(visual.parseIntrinsic(parts[2].text).fn, "peek8");
    assert.equal(visual.exprText(parts), text);
});

test("logical NOT wraps selected expressions, validates, emits, and reloads", () => {
    const expr = visual.parseExpr("a == b || c");
    visual.wrapRange(expr, 0, 2, true);
    assert.equal(visual.exprText(expr), "!(a == b) || c");
    assert.deepEqual(visual.parseExpr(visual.exprText(expr)), expr);
    const body = visual.validateStatements([{ op: "if", cond: expr, then: [], else: [] }]);
    const lines = [];
    visual.emit(body, 0, lines);
    assert.equal(lines[0], "if (!(a == b) || c) {");
    assert.equal(body[0].cond[0].not, true);
    const single = [{ t: "ref", name: "flag", category: "Globals" }];
    visual.wrapRange(single, 0, 0, true);
    assert.equal(visual.exprText(single), "!(flag)");
    delete single[0].not;
    assert.equal(visual.exprText(single), "(flag)");
    assert.equal(visual.exprText(visual.parseExpr("!(bit_test(flags, 3)) && !(a || b)")),
        "!(bit_test(flags, 3)) && !(a || b)");
    assert.equal(visual.exprText(visual.parseExpr("a != b")), "a != b");
    assert.throws(() => visual.validateStatements([{ op: "if", cond: [
        { t: "group", not: "yes", items: [{ t: "val", text: "x" }] }
    ], then: [], else: [] }]), /invalid negation/);
});
