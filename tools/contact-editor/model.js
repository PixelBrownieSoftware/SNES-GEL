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

    const assignOps = { set: "=", add: "+=", sub: "-=" };
    const targetPattern = /^[A-Za-z_]\w*(\[[^\[\]]+\])?(\.[A-Za-z_]\w*)?$/;
    const exprPattern = /^[A-Za-z0-9_\s.\[\]()+\-*\/%&|^~<>=!,]*$/;
    const MAX_DEPTH = 8, MAX_STATEMENTS = 200;

    function checkExpr(text, label, required) {
        if (typeof text !== "string") throw new Error(`${label} must be text.`);
        const value = text.trim();
        if (required && !value) throw new Error(`${label} is empty.`);
        if (!exprPattern.test(value) || value.includes("//") || value.includes("/*")) throw new Error(`${label} contains characters GEL expressions cannot use.`);
        const stack = [];
        for (const ch of value) {
            if ("([".includes(ch)) stack.push(ch === "(" ? ")" : "]");
            else if (")]".includes(ch) && stack.pop() !== ch) throw new Error(`${label} has unbalanced brackets.`);
        }
        if (stack.length) throw new Error(`${label} has unbalanced brackets.`);
        return value;
    }

    const OPERATORS = ["+", "-", "*", "/", "&", "|", "^", "<<", ">>", "==", "!=", "<", ">", "<=", ">=", "&&", "||"];
    const OPS_LONGEST_FIRST = [...OPERATORS].sort((a, b) => b.length - a.length);
    const MAX_EXPR_DEPTH = 6;

    function matchClose(s, start) {
        let depth = 0;
        for (let i = start; i < s.length; i++) {
            if ("([".includes(s[i])) depth++;
            else if (")]".includes(s[i]) && --depth === 0) return i;
        }
        throw new Error("unbalanced");
    }

    function parseSequence(s) {
        const items = [];
        let cur = "", afterGroup = false, i = 0;
        const flush = () => { items.push({ t: "val", text: cur.trim() }); cur = ""; };
        while (i < s.length) {
            const ch = s[i];
            const inTerm = cur.trim() !== "";
            if (ch === "(" && !inTerm && !afterGroup) {
                const close = matchClose(s, i);
                items.push({ t: "group", items: parseSequence(s.slice(i + 1, close)) });
                afterGroup = true; cur = ""; i = close + 1;
                continue;
            }
            if (/\s/.test(ch)) { if (!afterGroup) cur += ch; i++; continue; }
            if (afterGroup && !OPS_LONGEST_FIRST.some(op => s.startsWith(op, i))) throw new Error("junk after group");
            if (ch === "(" || ch === "[") { const close = matchClose(s, i); cur += s.slice(i, close + 1); i = close + 1; continue; }
            const op = OPS_LONGEST_FIRST.find(o => s.startsWith(o, i));
            if (op && (inTerm || afterGroup)) {
                if (inTerm) flush();
                items.push({ t: "op", op });
                afterGroup = false; i += op.length;
                continue;
            }
            if (op && op !== "-") throw new Error("operator without a left side");
            cur += ch; i++;
        }
        if (cur.trim()) flush();
        else if (!afterGroup) throw new Error("dangling operator");
        if (items.length % 2 === 0) throw new Error("bad shape");
        return items;
    }

    // Turns typed text into structured terms, operators and bracket groups; unparseable text stays one term.
    function parseExpr(text) {
        const s = String(text).trim();
        try { return parseSequence(s); } catch (error) { return [{ t: "val", text: s }]; }
    }

    function exprText(items) {
        return items.map(item => item.t === "op" ? ` ${item.op} ` : item.t === "group" ? `(${exprText(item.items)})` : item.text).join("");
    }

    function cleanExpr(value, label, depth = 0) {
        if (typeof value === "string") value = parseExpr(value);
        if (!Array.isArray(value) || !value.length) throw new Error(`${label} is empty.`);
        if (depth > MAX_EXPR_DEPTH) throw new Error(`${label} has brackets nested too deeply.`);
        if (value.length % 2 === 0) throw new Error(`${label} ends with an operator.`);
        return value.map((item, i) => {
            if (!item || typeof item !== "object") throw new Error(`${label} has an invalid part.`);
            if (i % 2 === 1) {
                if (item.t !== "op" || !OPERATORS.includes(item.op)) throw new Error(`${label} needs an operator between values.`);
                return { t: "op", op: item.op };
            }
            if (item.t === "group") return { t: "group", items: cleanExpr(item.items, `${label} brackets`, depth + 1) };
            if (item.t === "val") return { t: "val", text: checkExpr(item.text, label, true) };
            throw new Error(`${label} needs a value here.`);
        });
    }

    function cleanStatements(list, label, depth, counter) {
        if (!Array.isArray(list)) throw new Error(`${label} must be a list of statements.`);
        if (depth > MAX_DEPTH) throw new Error(`${label} is nested too deeply.`);
        return list.map((stmt, i) => {
            const where = `${label} statement ${i + 1}`;
            if (++counter.n > MAX_STATEMENTS) throw new Error(`${label} has too many statements.`);
            if (!stmt || typeof stmt !== "object") throw new Error(`${where} must be an object.`);
            if (stmt.op in assignOps) {
                const target = typeof stmt.target === "string" ? stmt.target.trim() : "";
                if (!targetPattern.test(target)) throw new Error(`${where}: target must be a variable or Table[index].field.`);
                return { op: stmt.op, target, value: cleanExpr(stmt.value, `${where} value`) };
            }
            if (stmt.op === "call") {
                const fn = typeof stmt.fn === "string" ? stmt.fn.trim() : "";
                if (!identifier.test(fn) || reserved.has(fn)) throw new Error(`${where}: function must be a GEL identifier.`);
                return { op: "call", fn, args: checkExpr(stmt.args || "", `${where} arguments`, false) };
            }
            if (stmt.op === "free") {
                const target = checkExpr(stmt.target, `${where} target`, true);
                if (!/^[A-Za-z_]\w*\[[^\[\]]+\]$/.test(target)) throw new Error(`${where}: free needs a slot such as Table[self].`);
                return { op: "free", target };
            }
            if (stmt.op === "create") {
                const table = typeof stmt.table === "string" ? stmt.table.trim() : "";
                const name = typeof stmt.var === "string" ? stmt.var.trim() : "";
                if (!identifier.test(table) || reserved.has(table)) throw new Error(`${where}: create needs a struct table name.`);
                if (name && (!identifier.test(name) || reserved.has(name))) throw new Error(`${where}: variable name must be a GEL identifier.`);
                return { op: "create", table, var: name };
            }
            if (stmt.op === "if") {
                return {
                    op: "if", cond: cleanExpr(stmt.cond, `${where} condition`),
                    then: cleanStatements(stmt.then || [], `${where} body`, depth + 1, counter),
                    else: cleanStatements(stmt.else || [], `${where} else`, depth + 1, counter),
                };
            }
            throw new Error(`${where}: unknown statement type.`);
        });
    }

    function validateHandlers(data) {
        const handlers = data && data.handlers === undefined ? {} : data && data.handlers;
        if (!handlers || typeof handlers !== "object" || Array.isArray(handlers)) throw new Error("handlers must be an object of handler scripts.");
        const clean = {};
        for (const [name, body] of Object.entries(handlers)) {
            if (!identifier.test(name) || reserved.has(name)) throw new Error(`Handler script name "${name}" must be a GEL identifier.`);
            clean[name] = cleanStatements(body, `Handler ${name}`, 0, { n: 0 });
        }
        return clean;
    }

    function emit(list, indent, lines) {
        const pad = "    ".repeat(indent);
        for (const s of list) {
            if (s.op === "if") {
                lines.push(`${pad}if (${exprText(s.cond)}) {`);
                emit(s.then, indent + 1, lines);
                if (s.else.length) { lines.push(`${pad}} else {`); emit(s.else, indent + 1, lines); }
                lines.push(`${pad}}`);
            } else if (s.op === "free") lines.push(`${pad}${s.target}.free();`);
            else if (s.op === "create") lines.push(`${pad}${s.var ? `u8 ${s.var} = ` : ""}${s.table}.create();`);
            else if (s.op === "call") lines.push(`${pad}${s.fn}(${s.args});`);
            else lines.push(`${pad}${s.target} ${assignOps[s.op]} ${exprText(s.value)};`);
        }
    }

    function generate(data) {
        const rows = validate(data);
        const scripts = validateHandlers(data);
        const used = new Set(rows.flatMap(row => phases.map(([phase]) => row[phase]).filter(Boolean)));
        const handlerCode = Object.keys(scripts).filter(name => used.has(name)).map(name => {
            const lines = [`fn ${name}(u8 self, u8 other) {`];
            emit(scripts[name], 1, lines);
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
    const RANGES = { u8: [0, 255], u16: [0, 65535], i8: [-128, 127], i16: [-32768, 32767], f16: [-128, 127 + 255 / 256], f32: [-32768, 32767] };
    const INTEGER = new Set(["u8", "u16", "i8", "i16"]);
    const LITERAL = /^-?(0x[0-9a-fA-F]+|\d+(\.\d+)?)$/;

    function literalNumber(text) {
        const negative = text.startsWith("-");
        const body = negative ? text.slice(1) : text;
        const n = body.toLowerCase().startsWith("0x") ? parseInt(body, 16) : Number(body);
        return negative ? -n : n;
    }

    // Returns a message when `text` cannot be stored in a variable of `type`; "" when fine or unknown.
    function checkValue(type, text, defineValues) {
        const range = RANGES[type];
        let value = String(text).trim();
        let label = value;
        if (!range || !value) return "";
        if (/^[A-Za-z_]\w*$/.test(value) && defineValues && typeof defineValues[value] === "string") {
            label = `${value} (= ${defineValues[value]})`;
            value = defineValues[value].trim();
        }
        if (!LITERAL.test(value)) {
            return INTEGER.has(type) && /\d\.\d/.test(value) ? `${type} cannot hold fractional values like ${value}.` : "";
        }
        const n = literalNumber(value);
        if (INTEGER.has(type) && (!Number.isInteger(n) || /\./.test(value))) return `${type} holds whole numbers only, not ${label}.`;
        if (n < range[0] || n > range[1]) return `${label} is outside the ${type} range ${range[0]} to ${Number.isInteger(range[1]) ? range[1] : "127.99"}.`;
        return "";
    }

    // Groups items[lo..hi] (values and the operators between them) into one bracket group.
    function wrapRange(items, lo, hi) {
        if (lo % 2 || hi % 2 || lo > hi || hi >= items.length) throw new Error("Selection must start and end on a value.");
        const inner = items.splice(lo, hi - lo + 1);
        items.splice(lo, 0, { t: "group", items: inner });
        return items;
    }

    const api = { validate, validateHandlers, generate, checkValue, parseExpr, exprText, wrapRange, OPERATORS };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.ContactModel = api;
})(globalThis);
