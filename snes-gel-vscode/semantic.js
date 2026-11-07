// Lightweight GEL symbol analysis for semantic highlighting, including incomplete files.
const TYPES = new Set(["u8", "u16", "i8", "i16", "f16", "f32"]);
const TOKEN = /[A-Za-z_][A-Za-z0-9_]*|0x[0-9a-fA-F]+|[0-9]+(?:\.[0-9]+)?|[^\s]/g;

function lex(text) {
    // Preserve offsets and newlines while masking text that must not contain GEL symbols.
    const masked = text.replace(/^[ \t]*#include[^\r\n]*|\/\/[^\r\n]*|"(?:[^"\r\n])*"|'[^'\r\n]*'|\basm\s*\{[^}]*\}/gm,
        s => s.replace(/[^\r\n]/g, " "));
    return Array.from(masked.matchAll(TOKEN), m => ({ value: m[0], offset: m.index }));
}

function collect(text) {
    const tokens = lex(text);
    const globals = new Map(), tables = new Map(), enums = new Set(), defines = new Set();
    for (const match of text.matchAll(/^[ \t]*#define[ \t]+([A-Za-z_]\w*)/gm)) {
        defines.add(match[1]);
    }
    let depth = 0;
    for (let i = 0; i < tokens.length; i++) {
        const v = tokens[i].value;
        if (depth === 0 && v === "fn") {
            while (i < tokens.length && tokens[i].value !== "{") i++;
            if (i < tokens.length) depth++;
            continue;
        }
        if (depth === 0 && (v === "struct_table" || v === "struct") && tokens[i + 1]) {
            const name = tokens[i + 1].value;
            let j = i + 2;
            while (j < tokens.length && tokens[j].value !== "{") j++;
            const fields = new Map();
            for (j++; j < tokens.length && tokens[j].value !== "}"; j++) {
                if (TYPES.has(tokens[j].value) && tokens[j + 1]) fields.set(tokens[j + 1].value, tokens[j].value);
            }
            tables.set(name, fields);
            i = j;
            continue;
        }
        if (depth === 0 && v === "enum" && tokens[i + 1]) {
            let j = i + 2;
            while (j < tokens.length && tokens[j].value !== "{") j++;
            for (j++; j < tokens.length && tokens[j].value !== "}"; j++) {
                if ((tokens[j - 1].value === "{" || tokens[j - 1].value === ",") &&
                    /^[A-Za-z_]\w*$/.test(tokens[j].value)) enums.add(tokens[j].value);
            }
            i = j;
            continue;
        }
        if (v === "{") depth++;
        else if (v === "}") depth = Math.max(0, depth - 1);
        else if (depth === 0 && TYPES.has(v) && tokens[i + 1] &&
                 (i === 0 || tokens[i - 1].value !== "(") &&
                 [";", "=", "@"].includes(tokens[i + 2] && tokens[i + 2].value)) {
            globals.set(tokens[i + 1].value, v);
        }
    }
    return { tokens, globals, tables, enums, defines };
}

function mergeUnique(target, name, value) {
    if (!target.has(name)) target.set(name, value);
    else if (target.get(name) !== value) target.set(name, null);
}

function analyze(text, sources) {
    const current = collect(text);
    const globals = new Map(), fields = new Map(), enums = new Set(), defines = new Set();
    for (const source of [current, ...sources.map(collect)]) {
        for (const [name, type] of source.globals) mergeUnique(globals, name, type);
        for (const [table, members] of source.tables) {
            for (const [name, type] of members) mergeUnique(fields, `${table}.${name}`, type);
        }
        for (const name of source.enums) enums.add(name);
        for (const name of source.defines) defines.add(name);
    }

    const t = current.tokens, result = [], scopes = [new Map()];
    const functionParams = new Map();
    for (let i = 0; i < t.length; i++) {
        if (t[i].value !== "fn" || !t[i + 2] || t[i + 2].value !== "(") continue;
        const params = new Map();
        let j = i + 3;
        let depth = 1;
        for (; j < t.length && depth; j++) {
            if (t[j].value === "(") depth++;
            if (t[j].value === ")") depth--;
            if (depth === 1 && TYPES.has(t[j].value) && t[j + 1]) params.set(t[j + 1].value, t[j].value);
        }
        if (t[j] && t[j].value === "{") functionParams.set(j, params);
    }
    function local(name) {
        for (let j = scopes.length - 1; j >= 0; j--) if (scopes[j].has(name)) return scopes[j].get(name);
        return globals.get(name);
    }
    for (let i = 0; i < t.length; i++) {
        const v = t[i].value, prev = t[i - 1] && t[i - 1].value;
        if (v === "{") {
            scopes.push(functionParams.get(i) || (scopes.length > 1 ? scopes[scopes.length - 1] : new Map()));
            continue;
        }
        if (v === "}") { if (scopes.length > 1) scopes.pop(); continue; }
        if (!/^[A-Za-z_]\w*$/.test(v) || TYPES.has(v)) continue;
        let type;
        if (prev === "(" && t[i - 2] && t[i - 2].value === "for_each") {
            type = "u16";
            if (scopes.length > 1) scopes[scopes.length - 1].set(v, type);
        }
        else if (prev === ".") {
            // Find the table at the start of the indexed expression, accounting for nested indices.
            if (t[i - 2] && t[i - 2].value === "]") {
                let depth = 1, j = i - 3;
                for (; j >= 0 && depth; j--) {
                    if (t[j].value === "]") depth++;
                    if (t[j].value === "[") depth--;
                }
                if (t[j]) type = fields.get(`${t[j].value}.${v}`);
            }
        } else if (TYPES.has(prev)) {
            type = prev;
            if (scopes.length > 1) scopes[scopes.length - 1].set(v, type);
        } else if (defines.has(v)) type = "define";
        else if ((t[i + 1] && t[i + 1].value === "(") || prev === "fn") continue;
        else if (local(v)) type = local(v);
        else if (enums.has(v)) type = "enum";
        if (type) result.push({ offset: t[i].offset, length: v.length, type: `gel${type[0].toUpperCase()}${type.slice(1)}` });
    }
    return result;
}

module.exports = { collect, analyze };
