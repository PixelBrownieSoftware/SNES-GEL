const test = require("node:test");
const assert = require("node:assert/strict");
const layout = require("../tools/visual-gel/memory-layout");

const memory = {
    available: true,
    variables: [
        { name: "score", type: "u16", kind: "ram", address: 0x10 },
        { name: "REG", type: "u8", kind: "mmio", address: 0x2100 }
    ],
    tables: [
        { name: "Entities", size: 10, mask: 0x20, fields: [
            { name: "x", type: "u16", address: 0x600, stride: 3 },
            { name: "flags", type: "u8", address: 0x602, stride: 3 }
        ] },
        { name: "Shadow", size: 128, mask: null, fields: [
            { name: "x", type: "u8", address: 0x200, stride: 4 }
        ] }
    ]
};

test("compiled entries calculate field payloads, masks, and MMIO separately", () => {
    const items = layout.entries(memory);
    assert.deepEqual(items.map(item => [item.name, item.bytes, item.address]), [
        ["score", 2, 0x10], ["REG", 1, 0x2100],
        ["Entities pool mask", 2, 0x20], ["Entities.x", 20, 0x600],
        ["Entities.flags", 10, 0x602], ["Shadow.x", 128, 0x200]
    ]);
    assert.match(items[3].detail, /10 slots × 2 bytes · stride 3 bytes/);
    assert.deepEqual(layout.totals(items), { ram: 162, mmio: 1 });
    assert.deepEqual(layout.totals(items.filter(item => item.name.startsWith("Entities"))),
        { ram: 32, mmio: 0 });
});

test("missing output is empty and unknown compiled types are reported", () => {
    assert.deepEqual(layout.entries(null), []);
    assert.deepEqual(layout.entries({ available: false }), []);
    assert.throws(() => layout.entries({
        available: true, tables: [], variables: [{ name: "bad", kind: "ram", type: "u32" }]
    }), /Unknown compiled memory type: u32/);
});

test("memory filtering combines category and case-insensitive name or type search", () => {
    const items = layout.entries(memory);
    assert.deepEqual(layout.filter(items, "field", "  ENTITIES  ").map(item => item.name),
        ["Entities.x", "Entities.flags"]);
    assert.deepEqual(layout.filter(items, "all", "u16").map(item => item.name), ["score", "Entities.x"]);
    assert.deepEqual(layout.filter(items, "mmio", "entities"), []);
    assert.equal(layout.filter(items, "all", "").length, items.length);
});

test("occupancy counts distinct low WRAM bytes by category including interleaved fields", () => {
    const result = layout.occupancy(memory);
    assert.equal(result.capacity, 8192);
    assert.deepEqual(result.counts, { ram: 2, field: 158, mask: 2, overlap: 0, unreported: 8030 });
    assert.equal(result.outside, false);
});

test("occupancy handles overlapping categories, aliases and ranges outside low WRAM", () => {
    const result = layout.occupancy({
        variables: [
            { kind: "ram", type: "u16", address: 0x1fff },
            { kind: "ram", type: "u16", address: 0x100 },
            { kind: "ram", type: "u16", address: 0x100 },
            { kind: "mmio", type: "u16", address: 0x200 }
        ],
        tables: [{ size: 2, mask: 0x101, fields: [
            { type: "u8", address: 0x100, stride: 2 },
            { type: "u8", address: 0x3000, stride: 1 }
        ] }]
    });
    assert.deepEqual(result.counts, { ram: 1, field: 0, mask: 0, overlap: 3, unreported: 8188 });
    assert.equal(result.outside, true);
});

test("address overview respects strided fields, reserved ranges, widths, and low WRAM boundaries", () => {
    const report = {
        available: true,
        variables: [{ name: "v", type: "u16", kind: "ram", address: 0x20 },
                    { name: "reg", type: "u8", kind: "mmio", address: 0x30 }],
        tables: [{ name: "T", size: 2, mask: 0x40,
            fields: [{ name: "x", type: "u8", address: 0x100, stride: 3 }] }],
        reserved: [{ name: "scratch", start: 0, end: 15 }, { name: "stack", start: 0x1f00, end: 0x1fff }]
    };
    const ranges = layout.addressRanges(report);
    assert.deepEqual(ranges.slice(0, 3), [
        { start: 0, end: 15, category: "reserved" },
        { start: 16, end: 31, category: "unreported" },
        { start: 32, end: 33, category: "occupied" }
    ]);
    assert.equal(layout.addressStatus(ranges, 0x1f, 2), "occupied");
    assert.equal(layout.addressStatus(ranges, 0x1f, 1), "unreported");
    assert.equal(layout.addressStatus(ranges, 0x101, 2), "unreported");
    assert.equal(layout.addressStatus(ranges, 0x102, 2), "occupied");
    assert.equal(layout.addressStatus(ranges, 0x30, 1), "unreported");
    assert.equal(layout.addressStatus(ranges, 0x1eff, 2), "reserved");
    assert.equal(layout.addressStatus(ranges, 0x1fff, 2), "outside");
    assert.equal(layout.typeSize("f16"), 2);
    assert.throws(() => layout.addressRanges({ available: false }), /No compiled/);
    assert.throws(() => layout.addressStatus(ranges, -1, 1), /Invalid/);
    assert.throws(() => layout.typeSize("f32"), /Unknown/);
});
