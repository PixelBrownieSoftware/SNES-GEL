(function (root) {
    "use strict";
    function mask(text) {
        return text.replace(/"[^"\r\n]*"|'[^'\r\n]*'|\/\/[^\r\n]*|\/\*[\s\S]*?\*\//g,
            match => /^["']/.test(match) ? match : match.replace(/[^\r\n]/g, " "));
    }
    function parse(code) {
        const text = mask(code);
        const header = /^(struct_table\s+)([A-Za-z_]\w*)(\s*\[\s*)([A-Za-z_]\w*|0x[0-9a-fA-F]+|\d+)(\s*\])(?:\s*@\s*([A-Za-z_]\w*|0x[0-9a-fA-F]+|\d+))?(\s+interleaved)?\s*\{/y;
        const match = header.exec(text);
        if (!match) throw new Error("This table header cannot be edited visually; edit it in the source file.");
        const close = text.lastIndexOf("}");
        if (close < match[0].length || !/^\s*;?\s*$/.test(text.slice(close + 1)))
            throw new Error("This table cannot be edited visually; edit it in the source file.");
        const nameStart = match[1].length;
        const sizeStart = nameStart + match[2].length + match[3].length;
        const fields = [];
        const fieldPattern = /\b(u8|i8|u16|i16|f16|f32)\s+([A-Za-z_]\w*)\s*;/g;
        fieldPattern.lastIndex = match[0].length;
        let field;
        let cursor = match[0].length;
        while ((field = fieldPattern.exec(text)) && field.index < close) {
            if (text.slice(cursor, field.index).trim()) throw new Error("Unsupported table field syntax.");
            const nameOffset = field[0].indexOf(field[2], field[1].length);
            fields.push({ type: field[1], name: field[2], origin: fields.length,
                start: field.index, end: fieldPattern.lastIndex,
                typeEnd: field.index + field[1].length, nameStart: field.index + nameOffset,
                nameEnd: field.index + nameOffset + field[2].length });
            cursor = fieldPattern.lastIndex;
        }
        if (text.slice(cursor, close).trim()) throw new Error("Unsupported table field syntax.");
        const addressMatch = /@\s*([A-Za-z_]\w*|0x[0-9a-fA-F]+|\d+)/.exec(text.slice(sizeStart + match[4].length, match[0].length));
        const addressStart = addressMatch ? sizeStart + match[4].length + addressMatch.index : -1;
        const interleavedStart = match[7] ? text.indexOf("interleaved", sizeStart + match[4].length) : -1;
        return { code, name: match[2], size: match[4], address: match[6] || "",
            interleaved: !!match[7], fields, nameStart, sizeStart, addressStart,
            addressEnd: addressMatch ? addressStart + addressMatch[0].length : -1,
            interleavedStart, open: match[0].length - 1, close };
    }
    function emit(model) {
        const edits = [
            [model.nameStart, model.nameStart + model.originalName.length, model.name],
            [model.sizeStart, model.sizeStart + model.originalSize.length, model.size]
        ];
        if (model.addressStart >= 0) edits.push([model.addressStart, model.addressEnd, model.address ? `@ ${model.address}` : ""]);
        if (model.interleavedStart >= 0 && !model.interleaved)
            edits.push([model.interleavedStart, model.interleavedStart + "interleaved".length, ""]);
        const insertedAddress = model.addressStart < 0 && model.address ? `@ ${model.address} ` : "";
        if (insertedAddress && model.interleavedStart >= 0)
            edits.push([model.interleavedStart, model.interleavedStart, insertedAddress]);
        else {
            const insertedInterleaved = model.interleavedStart < 0 && model.interleaved ? "interleaved " : "";
            if (insertedAddress || insertedInterleaved)
                edits.push([model.open, model.open, insertedAddress + insertedInterleaved]);
        }
        const kept = new Map(model.fields.filter(f => f.origin !== null).map(f => [f.origin, f]));
        for (const original of model.originalFields) {
            const field = kept.get(original.origin);
            if (!field) edits.push([original.start, original.end, ""]);
            else {
                edits.push([original.start, original.typeEnd, field.type]);
                edits.push([original.nameStart, original.nameEnd, field.name]);
            }
        }
        const added = model.fields.filter(f => f.origin === null);
        if (added.length) {
            const newline = model.code.includes("\r\n") ? "\r\n" : "\n";
            const lineStart = model.code.lastIndexOf("\n", model.close - 1) + 1;
            const indent = /^[ \t]*$/.test(model.code.slice(lineStart, model.close)) ? model.code.slice(lineStart, model.close) : "";
            edits.push([model.close, model.close, `${newline}${added.map(f => `${indent}    ${f.type} ${f.name};`).join(newline)}${newline}${indent}`]);
        }
        let code = model.code;
        for (const [start, end, value] of edits.sort((a, b) => b[0] - a[0] || b[1] - a[1]))
            code = code.slice(0, start) + value + code.slice(end);
        return code;
    }
    function create(code) {
        const model = parse(code);
        model.originalName = model.name;
        model.originalSize = model.size;
        model.originalFields = model.fields.map(field => ({ ...field }));
        return model;
    }
    const api = { create, emit };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    root.VisualGelTableEditor = api;
})(globalThis);
