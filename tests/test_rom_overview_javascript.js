const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.join(__dirname, "..", "tools", "rom-overview");
const source = fs.readFileSync(path.join(root, "editor.js"), "utf8");

test("ROM size options show exact KB and MB", () => {
    const select = { children: [], append(option) { this.children.push(option); } };
    const context = {
        document: { createElement() { return {}; } },
        window: { addEventListener() {} },
    };
    vm.runInNewContext(source, context);
    context.fillSelect(select, [32, 256, 1024, 4096], 256, true);
    assert.deepEqual(select.children.map(option => option.textContent), [
        "32 KB (0.03125 MB)", "256 KB (0.25 MB)", "1024 KB (1 MB)", "4096 KB (4 MB)",
    ]);
    assert.equal(select.value, "256");
});

test("unsupported ROM mappings are disabled in the editor", () => {
    const html = fs.readFileSync(path.join(root, "index.html"), "utf8");
    assert.match(html, /<option value="hirom">HiROM<\/option>/);
    for (const mode of ["exlorom", "exhirom"]) {
        assert.match(html, new RegExp(`<option value="${mode}" disabled>`));
    }
});
