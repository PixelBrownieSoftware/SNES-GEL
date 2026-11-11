(function (root) {
    "use strict";
    function mask(text) {
        return text.replace(/"[^"\r\n]*"|'[^'\r\n]*'|\/\/[^\r\n]*|\/\*[\s\S]*?\*\//g,
            part => /^["']/.test(part) ? part : part.replace(/[^\r\n]/g, " "));
    }
    function create(code) {
        const text = mask(code);
        const head = /^(?:(dp|mmio|persist)\s+)?(u8|u16|i8|i16|f16|f32)\s+([A-Za-z_]\w*)/.exec(text);
        if (!head) throw new Error("This global declaration cannot be edited visually; edit it in the source file.");
        const semicolon = text.lastIndexOf(";");
        if (semicolon < head[0].length || text.slice(semicolon + 1).trim())
            throw new Error("This global declaration cannot be edited visually; edit it in the source file.");
        const tail = text.slice(head[0].length, semicolon);
        const parts = /^\s*(?:@\s*(0x[0-9a-fA-F]+|[0-9]+|[A-Za-z_]\w*))?\s*(?:=\s*([\s\S]*?))?\s*$/.exec(tail);
        if (!parts) throw new Error("This global declaration has unsupported syntax; edit it in the source file.");
        const storageStart = head[1] ? head.index : -1;
        const typeStart = head[0].indexOf(head[2], head[1] ? head[1].length : 0);
        const nameStart = head[0].lastIndexOf(head[3]);
        const at = parts[1] === undefined ? -1 : text.indexOf("@", head[0].length);
        const equals = parts[2] === undefined ? -1 : text.indexOf("=", head[0].length);
        const addressStart = at < 0 ? -1 : text.indexOf(parts[1], at + 1);
        const valueStart = equals < 0 ? -1 : equals + 1 + (text.slice(equals + 1, semicolon).match(/^\s*/) || [""])[0].length;
        const valueEnd = equals < 0 ? -1 : semicolon - (text.slice(valueStart, semicolon).match(/\s*$/) || [""])[0].length;
        return { code, storage: head[1] || "", type: head[2], name: head[3],
            address: parts[1] || "", value: equals < 0 ? "" : code.slice(valueStart, valueEnd),
            storageStart, typeStart, nameStart, at, equals, addressStart, valueStart, valueEnd, semicolon,
            originalStorage: head[1] || "", originalType: head[2], originalName: head[3],
            originalAddress: parts[1] || "", originalValue: equals < 0 ? "" : code.slice(valueStart, valueEnd) };
    }
    function emit(model) {
        if (model.storage === "persist" && model.address.trim())
            throw new Error("Persist variables cannot have a fixed address.");
        if (model.storage === "mmio" && !model.address.trim())
            throw new Error("MMIO variables require a fixed address.");
        const edits = [
            [model.typeStart, model.typeStart + model.originalType.length, model.type],
            [model.nameStart, model.nameStart + model.originalName.length, model.name]
        ];
        if (model.storageStart >= 0) {
            const end = model.storageStart + model.originalStorage.length;
            const whitespace = model.code.slice(end, model.typeStart);
            edits.push([model.storageStart, !model.storage && /^\s*$/.test(whitespace) ? model.typeStart : end, model.storage]);
        }
        else if (model.storage) edits.push([model.typeStart, model.typeStart, model.storage + " "]);
        if (model.at >= 0) {
            if (model.address) edits.push([model.addressStart, model.addressStart + model.originalAddress.length, model.address]);
            else edits.push([model.at, model.addressStart + model.originalAddress.length, ""]);
        }
        if (model.equals >= 0) {
            if (model.value) edits.push([model.valueStart, model.valueEnd, model.value]);
            else {
                if (mask(model.code.slice(model.equals, model.semicolon)) !== model.code.slice(model.equals, model.semicolon))
                    throw new Error("The initializer contains comments; remove it in the source file to preserve them.");
                edits.push([model.equals, model.valueEnd, ""]);
            }
        }
        if (model.value !== model.originalValue && model.originalValue &&
                mask(model.originalValue) !== model.originalValue)
            throw new Error("The initializer contains comments; edit it in the source file to preserve them.");
        if (model.at < 0 && model.address && model.equals >= 0)
            edits.push([model.equals, model.equals, `@ ${model.address} `]);
        if (model.equals < 0) {
            const insertion = (model.at < 0 && model.address ? `@ ${model.address} ` : "") +
                (model.value ? `= ${model.value}` : "");
            if (insertion) edits.push([model.semicolon, model.semicolon, ` ${insertion}`]);
        }
        let code = model.code;
        for (const [start, end, value] of edits.sort((a, b) => b[0] - a[0] || b[1] - a[1]))
            code = code.slice(0, start) + value + code.slice(end);
        return code;
    }
    const api = { create, emit };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    root.VisualGelGlobalEditor = api;
})(globalThis);
