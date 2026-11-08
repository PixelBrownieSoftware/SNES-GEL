(function (root) {
    "use strict";
    const sizes = { u8: 1, i8: 1, u16: 2, i16: 2, f16: 2 };

    function typeSize(type) {
        if (!Object.hasOwn(sizes, type)) throw new Error(`Unknown compiled memory type: ${type}`);
        return sizes[type];
    }

    function entries(memory) {
        if (!memory || !memory.available) return [];
        const result = memory.variables.map(item => ({
            id: `variable:${item.kind}:${item.name}`,
            name: item.name, category: item.kind, address: item.address,
            bytes: typeSize(item.type), detail: `${item.type} · ${item.kind === "mmio" ? "hardware register" : "RAM variable"}`
        }));
        for (const table of memory.tables) {
            if (table.mask !== null) result.push({
                id: `mask:${table.name}`, name: `${table.name} pool mask`, category: "mask",
                address: table.mask, bytes: 2, detail: "16-bit pool mask · RAM"
            });
            for (const field of table.fields) {
                const size = typeSize(field.type);
                result.push({
                    id: `field:${table.name}:${field.name}`, name: `${table.name}.${field.name}`,
                    category: "field", address: field.address, bytes: size * table.size,
                    detail: `${field.type} · ${table.size} slots × ${size} byte${size === 1 ? "" : "s"} · stride ${field.stride} bytes`
                });
            }
        }
        return result;
    }

    function totals(items) {
        return items.reduce((result, item) => {
            result[item.category === "mmio" ? "mmio" : "ram"] += item.bytes;
            return result;
        }, { ram: 0, mmio: 0 });
    }

    function filter(items, category, query) {
        const search = query.trim().toLowerCase();
        return items.filter(item => (category === "all" || item.category === category) &&
            `${item.name} ${item.detail}`.toLowerCase().includes(search));
    }

    function occupancy(memory) {
        const capacity = 0x2000;
        const map = new Uint8Array(capacity);
        let outside = false;
        function mark(address, width, count, stride, bit) {
            if (!count) return;
            if (address < 0 || address + (count - 1) * stride + width > capacity) outside = true;
            const first = Math.max(0, Math.ceil((-address - width + 1) / stride));
            const last = Math.min(count - 1, Math.floor((capacity - 1 - address) / stride));
            for (let slot = first; slot <= last; slot++) {
                for (let offset = 0; offset < width; offset++) {
                    const byte = address + slot * stride + offset;
                    if (byte >= 0 && byte < capacity) map[byte] |= bit;
                }
            }
        }
        for (const item of memory.variables) {
            if (item.kind === "ram") mark(item.address, typeSize(item.type), 1, 1, 1);
        }
        for (const table of memory.tables) {
            if (table.mask !== null) mark(table.mask, 2, 1, 1, 4);
            for (const field of table.fields)
                mark(field.address, typeSize(field.type), table.size, field.stride, 2);
        }
        const counts = { ram: 0, field: 0, mask: 0, overlap: 0, unreported: 0 };
        for (const byte of map) {
            if (byte === 0) counts.unreported++;
            else if (byte === 1) counts.ram++;
            else if (byte === 2) counts.field++;
            else if (byte === 4) counts.mask++;
            else counts.overlap++;
        }
        return { capacity, counts, outside };
    }

    const api = { entries, totals, filter, occupancy };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.VisualGelMemoryLayout = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
