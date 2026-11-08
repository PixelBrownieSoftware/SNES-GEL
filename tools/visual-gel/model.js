(function (root) {
    "use strict";
    const identifier = /^[A-Za-z_][A-Za-z0-9_]*$/;
    const reserved = new Set(["u8", "u16", "i8", "i16", "f16", "f32", "if", "else", "switch", "case", "default", "break", "return", "while", "for_each", "fn", "enum", "struct", "struct_table", "asm", "static", "dp", "mmio", "persist", "extern", "interleaved", "create", "free"]);
    const assignOps = { set: "=", add: "+=", sub: "-=" };
    const assignmentOperators = ["=", "+=", "-=", "*=", "/=", "&=", "|=", "^=", "<<=", ">>="];
    const targetPattern = /^[A-Za-z_]\w*(\[[^\[\]]+\])?(\.[A-Za-z_]\w*)?$/;
    const exprPattern = /^[A-Za-z0-9_\s.\[\]()+\-*\/%&|^~<>=!,]*$/;
    const MAX_DEPTH = 8, MAX_STATEMENTS = 200;

    function cleanReference(value, categories, pattern, label) {
        if (typeof value === "string") return value.trim();
        if (!value || value.t !== "ref" || !categories.includes(value.category) ||
            typeof value.name !== "string" || !pattern.test(value.name))
            throw new Error(`${label} has an invalid symbol reference.`);
        return { t: "ref", name: value.name, category: value.category };
    }

    function referenceText(value) {
        return typeof value === "string" ? value : value.name;
    }

    function checkExpr(text, label, required) {
        if (typeof text !== "string") throw new Error(`${label} must be text.`);
        const value = text.trim();
        if (required && !value) throw new Error(`${label} is empty.`);
        const syntax = value.replace(/"[^"\r\n]*"|'[^'\r\n]*'/g, match => " ".repeat(match.length));
        if (!exprPattern.test(syntax) || syntax.includes("//") || syntax.includes("/*")) throw new Error(`${label} contains characters GEL expressions cannot use.`);
        const stack = [];
        for (const ch of syntax) {
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
            if (ch === "!" && !inTerm && !afterGroup) {
                const match = /^!\s*\(/.exec(s.slice(i));
                if (match) {
                    const start = i + match[0].length - 1;
                    const close = matchClose(s, start);
                    items.push({ t: "group", not: true, items: parseSequence(s.slice(start + 1, close)) });
                    afterGroup = true; cur = ""; i = close + 1;
                    continue;
                }
            }
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
        if (/['"]/.test(s)) return [{ t: "val", text: s }];
        try { return parseSequence(s); } catch (error) { return [{ t: "val", text: s }]; }
    }

    const intrinsicArity = { bit_set: 2, bit_clear: 2, bit_test: 2, peek8: 1, peek8l: 2 };
    function splitArguments(text) {
        const result = [];
        let stack = [], start = 0, quote = "";
        for (let i = 0; i < text.length; i++) {
            const ch = text[i];
            if (quote) { if (ch === quote) quote = ""; continue; }
            if (ch === "'" || ch === '"') { quote = ch; continue; }
            if (ch === "(" || ch === "[") stack.push(ch === "(" ? ")" : "]");
            else if (ch === ")" || ch === "]") {
                if (stack.pop() !== ch) return null;
            } else if (ch === "," && !stack.length) {
                result.push(text.slice(start, i).trim()); start = i + 1;
            }
        }
        if (quote || stack.length) return null;
        result.push(text.slice(start).trim());
        return result;
    }

    function parseIntrinsic(text) {
        const match = /^(!\s*)?(bit_set|bit_clear|bit_test|peek8l|peek8)\s*\(([\s\S]*)\)$/.exec(text.trim());
        if (!match) return null;
        const args = splitArguments(match[3]);
        if (!args || args.length !== intrinsicArity[match[2]]) return null;
        return { prefix: match[1] || "", fn: match[2], args };
    }

    function parseCall(text) {
        const match = /^(!\s*)?([A-Za-z_]\w*)\s*\(([\s\S]*)\)$/.exec(text.trim());
        if (!match) return null;
        const args = match[3].trim() ? splitArguments(match[3]) : [];
        return args ? { prefix: match[1] || "", fn: match[2], args } : null;
    }

    function exprText(items) {
        return items.map(item => item.t === "op" ? ` ${item.op} ` : item.t === "group" ? `${item.not ? "!" : ""}(${exprText(item.items)})` : item.t === "ref" ? item.name : item.text).join("");
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
            if (item.t === "group") {
                if (item.not !== undefined && typeof item.not !== "boolean") throw new Error(`${label} has an invalid negation.`);
                const group = { t: "group", items: cleanExpr(item.items, `${label} brackets`, depth + 1) };
                if (item.not) group.not = true;
                return group;
            }
            if (item.t === "ref") {
                return cleanReference(item, ["Fields", "Globals", "Locals", "Defines", "Enums"], targetPattern, label);
            }
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
                const target = cleanReference(stmt.target, ["Fields", "Globals", "Locals"], targetPattern, where);
                if (!targetPattern.test(referenceText(target))) throw new Error(`${where}: target must be a variable or Table[index].field.`);
                return { op: stmt.op, target, value: cleanExpr(stmt.value, `${where} value`) };
            }
            if (stmt.op === "assign") {
                const target = cleanReference(stmt.target, ["Fields", "Globals", "Locals"], targetPattern, where);
                if (!targetPattern.test(referenceText(target)) || !assignmentOperators.includes(stmt.operator))
                    throw new Error(`${where}: invalid assignment.`);
                return { op: "assign", target, operator: stmt.operator, value: cleanExpr(stmt.value, `${where} value`) };
            }
            if (stmt.op === "increment" || stmt.op === "decrement") {
                const target = cleanReference(stmt.target, ["Fields", "Globals", "Locals"], targetPattern, where);
                if (!targetPattern.test(referenceText(target))) throw new Error(`${where}: target must be a variable or table field.`);
                if (stmt.prefix !== undefined && typeof stmt.prefix !== "boolean") throw new Error(`${where}: invalid increment position.`);
                return { op: stmt.op, target, prefix: stmt.prefix === true };
            }
            if (stmt.op === "return" || stmt.op === "declare") {
                const value = checkExpr(stmt.value || "", `${where} value`, false);
                if (stmt.op === "return") return { op: "return", value };
                if (!["u8", "u16", "i8", "i16", "f16", "f32"].includes(stmt.type) ||
                    !identifier.test(stmt.name) || reserved.has(stmt.name))
                    throw new Error(`${where}: declaration needs a type and variable name.`);
                return { op: "declare", type: stmt.type, name: stmt.name, value };
            }
            if (stmt.op === "expr" || stmt.op === "header") {
                return { op: stmt.op, label: stmt.label, value: cleanExpr(stmt.value, `${where} expression`) };
            }
            if (stmt.op === "foreachHeader" || stmt.op === "for_each") {
                const table = cleanReference(stmt.table, ["Tables"], identifier, where);
                if (!identifier.test(referenceText(table)) || reserved.has(referenceText(table)) ||
                    !identifier.test(stmt.iterator) || reserved.has(stmt.iterator))
                    throw new Error(`${where}: for_each needs a table and iterator identifier.`);
                const result = { op: stmt.op, table, iterator: stmt.iterator };
                if (stmt.op === "for_each") result.body = cleanStatements(stmt.body || [], `${where} body`, depth + 1, counter);
                return result;
            }
            if (stmt.op === "while") {
                return { op: "while", cond: cleanExpr(stmt.cond, `${where} condition`),
                    body: cleanStatements(stmt.body || [], `${where} body`, depth + 1, counter) };
            }
            if (stmt.op === "switch") {
                if (!Array.isArray(stmt.cases)) throw new Error(`${where}: cases must be a list.`);
                const cases = stmt.cases.map((item, n) => {
                    if (!item || typeof item.value !== "string" ||
                        !/^(?:0x[0-9a-fA-F]+|[0-9]+|[A-Za-z_]\w*)$/.test(item.value.trim()) ||
                        reserved.has(item.value.trim())) throw new Error(`${where}: case ${n + 1} needs a number or identifier.`);
                    return { value: item.value.trim(), body: cleanStatements(item.body || [], `${where} case ${n + 1}`, depth + 1, counter) };
                });
                return { op: "switch", cond: cleanExpr(stmt.cond, `${where} expression`), cases,
                    default: stmt.default === null ? null : cleanStatements(stmt.default || [], `${where} default`, depth + 1, counter) };
            }
            if (stmt.op === "call") {
                const fn = cleanReference(stmt.fn, ["Functions", "Hardware", "Bit"], identifier, where);
                if (!identifier.test(referenceText(fn)) || reserved.has(referenceText(fn))) throw new Error(`${where}: function must be a GEL identifier.`);
                return { op: "call", fn, args: checkExpr(stmt.args || "", `${where} arguments`, false) };
            }
            if (stmt.op === "free") {
                const slotPattern = /^[A-Za-z_]\w*\[[^\[\]]+\]$/;
                const target = cleanReference(stmt.target, ["Slots"], slotPattern, where);
                const targetName = checkExpr(referenceText(target), `${where} target`, true);
                if (!slotPattern.test(targetName)) throw new Error(`${where}: free needs a slot such as Table[self].`);
                return { op: "free", target };
            }
            if (stmt.op === "create") {
                const table = cleanReference(stmt.table, ["Tables"], identifier, where);
                const name = typeof stmt.var === "string" ? stmt.var.trim() : "";
                if (!identifier.test(referenceText(table)) || reserved.has(referenceText(table))) throw new Error(`${where}: create needs a struct table name.`);
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
            clean[name] = validateStatements(body, `Handler ${name}`);
        }

        return clean;
    }

    function validateStatements(body, label = "Script") {
        return cleanStatements(body, label, 0, { n: 0 });
    }

    function emit(list, indent, lines) {
        const pad = "    ".repeat(indent);
        for (const s of list) {
            if (s.op === "if") {
                lines.push(`${pad}if (${exprText(s.cond)}) {`);
                emit(s.then, indent + 1, lines);
                if (s.else.length) { lines.push(`${pad}} else {`); emit(s.else, indent + 1, lines); }
                lines.push(`${pad}}`);
            } else if (s.op === "while" || s.op === "for_each") {
                lines.push(s.op === "while" ? `${pad}while (${exprText(s.cond)}) {` : `${pad}${referenceText(s.table)}.for_each(${s.iterator}) {`);
                emit(s.body, indent + 1, lines);
                lines.push(`${pad}}`);
            } else if (s.op === "switch") {
                lines.push(`${pad}switch (${exprText(s.cond)}) {`);
                for (const item of s.cases) {
                    lines.push(`${pad}    case ${item.value}:`);
                    emit(item.body, indent + 2, lines);
                    lines.push(`${pad}        break;`);
                }
                if (s.default !== null) {
                    lines.push(`${pad}    default:`);
                    emit(s.default, indent + 2, lines);
                }
                lines.push(`${pad}}`);
            } else if (s.op === "return") lines.push(`${pad}return${s.value ? ` ${s.value}` : ""};`);
            else if (s.op === "declare") lines.push(`${pad}${s.type} ${s.name}${s.value ? ` = ${s.value}` : ""};`);
            else if (s.op === "expr") lines.push(`${pad}${exprText(s.value)};`);
            else if (s.op === "header") lines.push(exprText(s.value));
            else if (s.op === "foreachHeader") lines.push(`${referenceText(s.table)}.for_each(${s.iterator})`);
            else if (s.op === "assign") lines.push(`${pad}${referenceText(s.target)} ${s.operator} ${exprText(s.value)};`);
            else if (s.op === "increment" || s.op === "decrement") {
                const operator = s.op === "increment" ? "++" : "--";
                lines.push(`${pad}${s.prefix ? operator : ""}${referenceText(s.target)}${s.prefix ? "" : operator};`);
            }
            else if (s.op === "free") lines.push(`${pad}${referenceText(s.target)}.free();`);
            else if (s.op === "create") lines.push(`${pad}${s.var ? `u8 ${s.var} = ` : ""}${referenceText(s.table)}.create();`);
            else if (s.op === "call") lines.push(`${pad}${referenceText(s.fn)}(${s.args});`);
            else lines.push(`${pad}${referenceText(s.target)} ${assignOps[s.op]} ${exprText(s.value)};`);
        }
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

    function defineCompatibility(type, name, defineValues) {
        const value = defineValues && defineValues[name];
        if (typeof value !== "string" || !value.trim())
            return { value: "", status: "unknown", problem: "" };
        const text = value.trim();
        if (!type || !RANGES[type] || !LITERAL.test(text))
            return { value: text, status: "unknown", problem: "" };
        const problem = checkValue(type, name, defineValues);
        const status = problem ? "incompatible" :
            type === "f16" && !text.includes(".") ? "raw" : "compatible";
        return { value: text, status, problem };
    }

    // Groups items[lo..hi] (values and the operators between them) into one bracket group.
    function wrapRange(items, lo, hi, negate = false) {
        if (lo % 2 || hi % 2 || lo > hi || hi >= items.length) throw new Error("Selection must start and end on a value.");
        const inner = items.splice(lo, hi - lo + 1);
        const group = { t: "group", items: inner };
        if (negate) group.not = true;
        items.splice(lo, 0, group);
        return items;
    }

    const api = { validateStatements, validateHandlers, emit, referenceText, checkValue, defineCompatibility, parseExpr, exprText, wrapRange, OPERATORS, parseIntrinsic, parseCall, splitArguments };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    else root.VisualGelModel = api;
})(globalThis);
