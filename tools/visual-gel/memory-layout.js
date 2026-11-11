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

    function occupancyMap(memory) {
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
        return { map, outside };
    }

    function occupancy(memory) {
        const { map, outside } = occupancyMap(memory);
        const capacity = map.length;
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

    function addressRanges(memory) {
        if (!memory || !memory.available) throw new Error("No compiled memory layout available.");
        const { map } = occupancyMap(memory);
        for (const range of memory.reserved || []) {
            if (!Number.isInteger(range.start) || !Number.isInteger(range.end) ||
                range.start < 0 || range.end < range.start || range.end >= map.length)
                throw new Error(`Invalid reserved memory range: ${range.name}`);
            for (let address = range.start; address <= range.end; address++) map[address] |= 8;
        }
        const ranges = [];
        const category = byte => byte & 8 ? "reserved" : byte ? "occupied" : "unreported";
        let start = 0;
        for (let address = 1; address <= map.length; address++) {
            if (address === map.length || category(map[address]) !== category(map[start])) {
                ranges.push({ start, end: address - 1, category: category(map[start]) });
                start = address;
            }
        }
        return ranges;
    }

    function addressStatus(ranges, address, width) {
        if (!Number.isSafeInteger(address) || address < 0 ||
            !Number.isSafeInteger(width) || width < 1) throw new Error("Invalid address or width.");
        if (address + width > 0x2000) return "outside";
        const overlaps = range => address <= range.end && address + width - 1 >= range.start;
        if (ranges.some(range => range.category === "reserved" && overlaps(range))) return "reserved";
        if (ranges.some(range => range.category === "occupied" && overlaps(range))) return "occupied";
        return "unreported";
    }

    const api = { entries, totals, filter, occupancy, addressRanges, addressStatus, typeSize };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.VisualGelMemoryLayout = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
