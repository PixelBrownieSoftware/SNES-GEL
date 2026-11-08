(function (root) {
    "use strict";
    const phases = [["enter", "entity_on_contact"], ["stay", "entity_on_stay"], ["exit", "entity_on_exit"]];
    const identifier = /^[A-Za-z_][A-Za-z0-9_]*$/;
    const reserved = new Set(["u8", "u16", "i8", "i16", "f16", "f32", "if", "else", "switch", "case", "default", "break", "return", "while", "for_each", "fn", "enum", "struct", "struct_table", "asm", "static", "dp", "mmio", "persist", "extern", "interleaved", "create", "free"]);

    function validate(data) {
        if (!data || data.version !== 1 || !Array.isArray(data.interactions)) throw new Error("Expected a version 1 contact configuration with an interactions array.");
        const pairs = new Set();
        return data.interactions.map((row, i) => {
            if (!row || typeof row !== "object" || Array.isArray(row)) throw new Error(`Interaction ${i + 1} must be an object.`);
            const clean = {};
            for (const key of ["self", "other", "enter", "stay", "exit"]) {
                if (typeof row[key] !== "string") throw new Error(`Interaction ${i + 1}: ${key} must be text.`);
                clean[key] = row[key].trim();
                if ((key === "self" || key === "other" || clean[key]) &&
                    (!identifier.test(clean[key]) || reserved.has(clean[key]))) throw new Error(`Interaction ${i + 1}: ${key} must be a GEL identifier.`);
            }
            const pair = `${clean.self}\0${clean.other}`;
            if (pairs.has(pair)) throw new Error(`Interaction ${i + 1}: duplicate directed pair.`);
            pairs.add(pair);
            return clean;
        });
    }

    const VisualGelModel = typeof module !== "undefined" && module.exports ? require("../visual-gel/model") : root.VisualGelModel;
    function objectOptions(data, recipient) {
        if (!data || !Array.isArray(data.objects)) throw new Error("Invalid object definitions.");
        return data.objects.filter(object => !recipient || object.flags.contactable)
            .map(object => ({ label: object.class, value: `OBJ_${object.class.toUpperCase()}` }));
    }

    function validateObjects(rows, objects) {
        const recipients = new Set(objectOptions(objects, true).map(object => object.value));
        const others = new Set(objectOptions(objects, false).map(object => object.value));
        rows.forEach((row, i) => {
            if (!recipients.has(row.self)) throw new Error(`Interaction ${i + 1}: choose a contactable recipient from the object editor.`);
            if (!others.has(row.other)) throw new Error(`Interaction ${i + 1}: choose an object for the other participant.`);
        });
    }
    function generate(data) {
        const rows = validate(data);
        const scripts = VisualGelModel.validateHandlers(data);
        const used = new Set(rows.flatMap(row => phases.map(([phase]) => row[phase]).filter(Boolean)));
        const handlerCode = Object.keys(scripts).filter(name => used.has(name)).map(name => {
            const lines = [`fn ${name}(u8 self, u8 other) {`];
            VisualGelModel.emit(scripts[name], 1, lines);
            lines.push("}");
            return lines.join("\n");
        });
        const dispatchers = phases.map(([phase, name]) => {
            const groups = new Map();
            for (const row of rows) {
                if (!row[phase]) continue;
                if (!groups.has(row.self)) groups.set(row.self, []);
                groups.get(row.self).push(row);
            }
            const lines = [`fn ${name}(u8 self, u8 other) {`];
            if (groups.size) {
                lines.push("    u8 self_type = Entities[self].type;", "    u8 other_type = Entities[other].type;", "", "    switch (self_type) {");
                for (const [self, group] of groups) {
                    lines.push(`        case ${self}:`, "            switch (other_type) {");
                    for (const row of group) lines.push(`                case ${row.other}:`, `                    ${row[phase]}(self, other);`, "                    break;");
                    lines.push("            }", "            break;");
                }
                lines.push("    }");
            }
            lines.push("}");
            return lines.join("\n");
        });
        return [...handlerCode, ...dispatchers].join("\n\n") + "\n";
    }
    const api = { ...VisualGelModel, validate, generate, objectOptions, validateObjects };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.ContactModel = api;
})(globalThis);
