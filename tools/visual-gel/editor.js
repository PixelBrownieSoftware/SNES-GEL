(function (root) {
    "use strict";
    function createVisualGelEditor({ status, locals = [], localTypes = {} }) {
    const TYPES = ["u8", "u16", "i8", "i16", "f16", "f32"];
    const CONTROL = new Set(["if", "else", "switch", "case", "default", "while", "break", "return", "for_each"]);
    let index = emptyIndex();
    function emptyIndex() {
        return { tables: new Map(), fields: new Map(), enums: new Map(), enumValues: {}, defines: new Set(), defineValues: {}, functions: new Set(), valueFunctions: new Map(), hardware: new Set(), bit: new Set(), globals: new Map(), items: [] };
    }

    function buildIndex(sym) {
        const idx = emptyIndex();
        const add = (cat, label, cls) => idx.items.push({ cat, label, cls });
        for (const [table, fields] of Object.entries(sym.tables || {})) {
            idx.tables.set(table, fields);
            add("Tables", table, "t-table");
            for (const local of locals) add("Slots", `${table}[${local}]`, "t-table");
            for (const [field, type] of Object.entries(fields)) {
                if (!idx.fields.has(field)) idx.fields.set(field, type);
                for (const local of locals) add("Fields", `${table}[${local}].${field}`, `t-${type}`);
            }
        }
        for (const [name, members] of Object.entries(sym.enums || {})) {
            for (const member of members) { idx.enums.set(member, name); add("Enums", member, "t-enum"); }
        }
        idx.defineValues = sym.defineValues || {};
        idx.enumValues = sym.enumValues || {};
        for (const name of sym.defines || []) { idx.defines.add(name); add("Defines", name, "t-define"); }
        for (const [name, type] of Object.entries(sym.globals || {})) { idx.globals.set(name, type); add("Globals", name, `t-${type}`); }
        for (const local of locals) add("Locals", local, localTypes[local] ? `t-${localTypes[local]}` : "");
        for (const [name, info] of Object.entries(sym.functions || {})) {
            idx.functions.add(name); add("Functions", name, "t-fn");
            if (info.returnType) {
                idx.valueFunctions.set(name, info);
                add("Returning functions", name, `t-${info.returnType}`);
            }
        }
        for (const name of sym.hardware || []) { idx.hardware.add(name); add("Hardware", name, "t-hw"); }
        for (const name of sym.bit || []) { idx.bit.add(name); add("Bit", name, "t-bit"); }
        return idx;
    }

    function classify(word, previous) {
        if (previous === "." && index.fields.has(word)) return `t-${index.fields.get(word)}`;
        if (TYPES.includes(word)) return `t-${word}`;
        if (CONTROL.has(word)) return "t-ctl";
        if (word === "fn") return "t-fn";
        if (index.tables.has(word)) return "t-table";
        if (index.defines.has(word)) return "t-define";
        if (index.enums.has(word)) return "t-enum";
        if (index.bit.has(word)) return "t-bit";
        if (index.hardware.has(word) || /^std_/.test(word)) return "t-hw";
        if (index.functions.has(word)) return "t-fn";
        if (locals.includes(word)) return localTypes[word] ? `t-${localTypes[word]}` : "";
        if (index.globals.has(word)) return `t-${index.globals.get(word)}`;
        return "";
    }

    function highlight(text, into) {
        const fragment = document.createDocumentFragment();
        let previous = "";
        for (const match of text.matchAll(/[A-Za-z_]\w*|0x[0-9a-fA-F]+|\d+(?:\.\d+)?|\s+|[\s\S]/g)) {
            const token = match[0];
            const span = document.createElement("span");
            span.textContent = token;
            if (/^[A-Za-z_]/.test(token)) span.className = classify(token, previous);
            else if (/^[0-9]/.test(token)) span.className = "t-num";
            fragment.append(span);
            if (!/^\s+$/.test(token)) previous = token;
        }
        into.replaceChildren(fragment);
    }

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    let openPicker = null;
    function closePicker() { if (openPicker) { openPicker.remove(); openPicker = null; } }

    function attachPicker(wrap, categories, apply, manualValue, cancel, defineType) {
        closePicker();
        const picker = el("div", "picker");
        const search = el("input", "picker-search");
        search.type = "search";
        search.placeholder = "Search symbols";
        search.setAttribute("aria-label", "Search symbols");
        const tabs = el("div", "picker-tabs");
        const list = el("div", "picker-list");
        picker.append(search);
        const allowed = i => !categories || categories.includes(i.cat);
        const available = [...new Set(index.items.filter(allowed).map(i => i.cat))];
        let category = "All";
        function refresh() {
            tabs.replaceChildren();
            for (const name of ["All", ...available]) {
                const tab = el("button", name === category ? "tab active" : "tab", name);
                tab.type = "button";
                tab.addEventListener("click", () => { category = name; refresh(); });
                tabs.append(tab);
            }
            list.replaceChildren();
            const query = search.value.trim().toLowerCase();
            const matches = index.items.filter(i => allowed(i) && (category === "All" || i.cat === category) &&
                i.label.toLowerCase().includes(query)).slice(0, 60);
            if (!matches.length) list.append(el("div", "picker-empty", index.items.length ? "No matching symbols" : "Start the Python server to search project symbols"));
            for (const item of matches) {
                const row = el("button", "picker-item");
                row.type = "button";
                let description = item.cat;
                if (item.cat === "Defines") {
                    const type = defineType ? defineType() : "";
                    const info = VisualGelModel.defineCompatibility(type, item.label, index.defineValues);
                    if (info.value) description += ` = ${info.value}`;
                    if (type) {
                        description += info.status === "compatible" ? ` · fits ${type}` :
                            info.status === "incompatible" ? ` · ${info.problem}` :
                            info.status === "raw" ? " · raw fixed-point bits; not scaled" : " · type unknown; build to check";
                        row.disabled = info.status === "incompatible";
                    }
                }
                row.append(el("span", item.cls, item.label), el("small", "", description));
                row.addEventListener("click", () => apply(item.label, item));
                list.append(row);
            }
        }
        search.addEventListener("input", refresh);
        search.addEventListener("keydown", event => { if (event.key === "Escape") cancel(); });
        if (manualValue !== null) {
            const manual = el("div", "picker-manual");
            const input = el("input", "picker-value");
            input.value = manualValue;
            input.setAttribute("aria-label", "Manual value");
            input.placeholder = "Enter a literal or expression";
            const use = el("button", "tab", "Use value");
            use.type = "button";
            use.addEventListener("click", () => apply(input.value.trim(), null));
            input.addEventListener("keydown", event => {
                if (event.key === "Enter") { event.preventDefault(); apply(input.value.trim(), null); }
                if (event.key === "Escape") cancel();
            });
            manual.append(input, use);
            picker.append(manual);
        }
        picker.append(tabs, list);
        wrap.append(picker);
        openPicker = picker;
        refresh();
        search.focus();
    }

    function slot(get, set, categories, placeholder, onChange, validate, allowManual, defineType) {
        const wrap = el("span", "slot");
        const view = el("button", "slot-view");
        view.type = "button";
        view.setAttribute("aria-label", placeholder);
        function show() {
            const value = VisualGelModel.referenceText(get());
            if (value) highlight(value, view); else view.textContent = placeholder;
            view.classList.toggle("empty", !value);
            wrap.replaceChildren(view);
        }
        function edit() {
            if (openPicker && openPicker.parentElement === wrap) { closePicker(); return; }
            const restricted = categories && !categories.includes("None") && !allowManual;
            const manual = VisualGelModel.referenceText(get());
            attachPicker(wrap, categories, (value, item) => {
                if (restricted && !item) {
                    item = index.items.find(candidate => categories.includes(candidate.cat) && candidate.label === value);
                    if (!item) { status.textContent = "Choose a matching project symbol for this reference."; return; }
                }
                if (allowManual && !item) {
                    item = index.items.find(candidate => categories.includes(candidate.cat) && candidate.label === value);
                }
                const problem = validate ? validate(value) : "";
                if (problem) { status.textContent = `Rejected "${value}": ${problem}`; return; }
                closePicker();
                const selected = item && categories && !allowManual
                    ? { t: "ref", name: value, category: item.cat } : value;
                set(selected, item);
                show();
                onChange(false);
            }, manual, () => { closePicker(); view.focus(); }, defineType);
        }
        view.addEventListener("click", edit);
        show();
        return wrap;
    }

    // Range selection inside one expression level: anchor and end are item indices.
    let selection = null;
    function selectedRange(items) {
        if (!selection || selection.items !== items) return null;
        return [Math.min(selection.a, selection.b), Math.max(selection.a, selection.b)];
    }
    function toggleSelect(items, i, extend, redraw) {
        const same = selection && selection.items === items;
        if (same && (extend || selection.a === selection.b) && selection.a !== i) selection.b = i;
        else if (same && selection.a === selection.b && selection.a === i) selection = null;
        else selection = { items, a: i, b: i };
        redraw();
    }

    function newExpr() { return [{ t: "val", text: "" }]; }

    function newStatement(op) {
        if (op === "if") return { op, cond: newExpr(), then: [], else: [] };
        if (op === "while") return { op, cond: newExpr(), body: [] };
        if (op === "for_each") return { op, table: "", iterator: "", body: [] };
        if (op === "switch") return { op, cond: newExpr(), cases: [], default: null };
        if (op === "return") return { op, value: "" };
        if (op === "increment" || op === "decrement") return { op, target: "", prefix: false };
        if (op === "declare") return { op, type: "u8", name: "", value: "" };
        if (op === "expr") return { op, value: newExpr() };
        if (op === "assign") return { op, operator: "*=", target: "", value: newExpr() };
        if (op === "call") return { op, fn: "", args: "" };
        if (op === "free") return { op, target: "" };
        if (op === "create") return { op, table: "", var: "" };
        return { op, target: "", value: newExpr() };
    }

    // Structured expression: values, operator choosers and real bracket groups.
    // `check` returns a problem message for the whole expression in its current state.
    function renderExpr(items, onChange, check, depth = 0, allowNegation = true, defineType = null) {
        const box = el("span", depth ? "expr group" : "expr");
        const rebuild = () => { selection = null; onChange(true); };
        const range = selectedRange(items);
        const inRange = i => range && i >= range[0] && i <= range[1];
        items.forEach((item, i) => {
            if (item.t === "op") {
                const select = el("select", "op-select");
                select.title = "Change operator";
                for (const op of VisualGelModel.OPERATORS) {
                    const option = el("option", "", op); option.value = op; option.selected = op === item.op;
                    select.append(option);
                }
                select.addEventListener("change", () => {
                    const old = item.op; item.op = select.value;
                    const problem = check();
                    if (problem) { item.op = old; select.value = old; status.textContent = problem; } else onChange(false);
                });
                const drop = el("button", "icon tiny", "✕"); drop.type = "button"; drop.title = "Remove this operator and the value after it";
                drop.addEventListener("click", () => { items.splice(i, 2); rebuild(); });
                box.append(select, drop);
                if (inRange(i)) select.classList.add("selected");
                return;
            }
            const term = el("span", inRange(i) ? "term selected" : "term");
            const pick = el("button", inRange(i) ? "pick on" : "pick", inRange(i) ? "☑" : "☐"); pick.type = "button";
            pick.title = "Select values: click one, then click another (or shift-click) to select everything between";
            pick.addEventListener("click", event => toggleSelect(items, i, event.shiftKey, () => onChange(true)));
            term.append(pick);
            if (item.t === "group") {
                term.classList.add("is-group");
                if (item.not) {
                    const not = el("button", "negation-toggle", "!"); not.type = "button";
                    not.title = "Remove logical NOT";
                    not.setAttribute("aria-label", "Remove logical NOT");
                    not.addEventListener("click", () => { delete item.not; rebuild(); });
                    term.append(not);
                }
                term.append(el("span", "bracket", "("), renderExpr(item.items, onChange, check, depth + 1, allowNegation,
                    items.length === 1 && !item.not ? defineType : null), el("span", "bracket", ")"));
                const unwrap = el("button", "icon tiny", "⤺"); unwrap.type = "button"; unwrap.title = "Remove the brackets, keeping what is inside";
                if (item.not) {
                    unwrap.disabled = true;
                    unwrap.title = "Remove logical NOT before removing the brackets";
                }
                unwrap.addEventListener("click", () => { items.splice(i, 1, ...item.items); rebuild(); });
                term.append(unwrap);
            } else if (item.t === "val" && VisualGelModel.parseIntrinsic(item.text)) {
                const intrinsic = VisualGelModel.parseIntrinsic(item.text);
                term.append(renderIntrinsic(intrinsic.fn, intrinsic.args, args => {
                    item.text = `${intrinsic.prefix}${intrinsic.fn}(${args.join(", ")})`;
                }, onChange, intrinsic.prefix));
                const remove = el("button", "icon tiny", "✕"); remove.type = "button";
                remove.title = "Replace function with a value";
                remove.addEventListener("click", () => { item.text = ""; rebuild(); });
                term.append(remove);
            } else if (item.t === "val" && VisualGelModel.parseCall(item.text) &&
                       index.valueFunctions.has(VisualGelModel.parseCall(item.text).fn)) {
                const call = VisualGelModel.parseCall(item.text);
                const info = index.valueFunctions.get(call.fn);
                while (call.args.length < info.params.length) call.args.push("");
                const host = el("span", "intrinsic");
                if (call.prefix) host.append(el("span", "kw", call.prefix));
                host.append(el("span", `t-${info.returnType}`, call.fn), el("span", "kw", "("));
                call.args.forEach((arg, n) => {
                    if (n) host.append(el("span", "kw", ","));
                    host.append(slot(() => call.args[n], value => {
                        call.args[n] = value;
                        item.text = `${call.prefix}${call.fn}(${call.args.join(", ")})`;
                    }, ["Fields", "Globals", "Locals", "Defines", "Enums"], `[${info.params[n] || `argument ${n + 1}`}]`,
                    () => onChange(true), null, true));
                });
                host.append(el("span", "kw", ")"));
                term.append(host);
                const remove = el("button", "icon tiny", "✕"); remove.type = "button";
                remove.title = "Replace function with a value";
                remove.addEventListener("click", () => { item.text = ""; rebuild(); });
                term.append(remove);
            } else {
                term.append(slot(() => item.t === "ref" ? item.name : item.text, (v, picked) => {
                    if (picked) {
                        if (picked.cat === "Returning functions") {
                            const info = index.valueFunctions.get(v);
                            items[i] = { t: "val", text: `${v}(${info.params.map(() => "").join(", ")})` };
                            rebuild();
                            return;
                        }
                        if (["bit_set", "bit_clear", "bit_test", "peek8", "peek8l"].includes(v) &&
                            ["Bit", "Hardware"].includes(picked.cat)) {
                            items[i] = { t: "val", text: `${v}(${v === "peek8" ? "" : ", "})` };
                            rebuild();
                            return;
                        }
                        item.t = "ref";
                        item.name = v;
                        item.category = picked.cat;
                        delete item.text;
                        return;
                    }
                    item.t = "val";
                    item.text = v;
                    delete item.name;
                    delete item.category;
                    const parsed = VisualGelModel.parseExpr(v);
                    if (parsed.length > 1 || parsed[0].t === "group" || VisualGelModel.parseIntrinsic(v)) {
                        if (items.length === 1) items.splice(0, 1, ...parsed); else items[i] = { t: "group", items: parsed };
                        rebuild();
                    }
                }, ["Fields", "Globals", "Locals", "Defines", "Enums", "Bit", "Hardware",
                    ...(allowNegation ? ["Returning functions"] : [])], "[value]", () => onChange(false), v => {
                    const old = { ...item }; item.t = "val"; item.text = v; delete item.name; delete item.category;
                    const problem = check();
                    for (const key of Object.keys(item)) delete item[key];
                    Object.assign(item, old);
                    return problem;
                }, true, items.length === 1 ? defineType : null));
            }
            box.append(term);
        });
        const add = el("select", "op-select add-op");
        add.title = "Add an operator and another value";
        const prompt = el("option", "", "+ op"); prompt.value = ""; add.append(prompt);
        for (const op of VisualGelModel.OPERATORS) { const option = el("option", "", op); option.value = op; add.append(option); }
        add.addEventListener("change", () => {
            if (!add.value) return;
            items.push({ t: "op", op: add.value }, { t: "val", text: "" });
            rebuild();
        });
        box.append(add);
        if (range) {
            const count = (range[1] - range[0]) / 2 + 1;
            const wrapSel = el("button", "wrap-selected", `( ) selected (${count})`); wrapSel.type = "button";
            wrapSel.title = "Put the selected values and the operators between them in brackets";
            wrapSel.addEventListener("click", () => {
                VisualGelModel.wrapRange(items, range[0], range[1]);
                rebuild();
            });
            if (allowNegation) {
                const negate = el("button", "wrap-selected", `! selected (${count})`); negate.type = "button";
                negate.title = "Apply logical NOT to the selected expression";
                negate.addEventListener("click", () => {
                    VisualGelModel.wrapRange(items, range[0], range[1], true);
                    rebuild();
                });
                box.append(negate);
            }
            const clear = el("button", "icon tiny", "clear"); clear.type = "button";
            clear.addEventListener("click", () => { selection = null; onChange(true); });
            box.append(wrapSel, clear);
        }
        if (depth) {
            const wrapAll = el("button", "icon tiny", "( )"); wrapAll.type = "button"; wrapAll.title = "Put everything in these brackets inside another pair";
            wrapAll.addEventListener("click", () => { const inner = items.splice(0, items.length); items.push({ t: "group", items: inner }); rebuild(); });
            box.append(wrapAll);
        } else if (items.length > 1) {
            const wrapAll = el("button", "icon tiny", "( … )"); wrapAll.type = "button"; wrapAll.title = "Put the whole expression in brackets";
            wrapAll.addEventListener("click", () => { const inner = items.splice(0, items.length); items.push({ t: "group", items: inner }); rebuild(); });
            box.append(wrapAll);
        }
        return box;
    }

    function addBar(list, onChange) {
        const bar = el("details", "add-bar");
        const toggle = el("summary", "ghost add-toggle", "+");
        toggle.setAttribute("aria-label", "Add statement");
        const panel = el("div", "add-panel");
        const search = el("input", "add-search");
        search.type = "search";
        search.placeholder = "Search statements";
        search.setAttribute("aria-label", "Search statements");
        const choices = el("div", "add-choices");
        const empty = el("p", "picker-empty", "No matching statements.");
        empty.hidden = true;
        const buttons = [];
        for (const [op, label] of [["set", "+ set"], ["add", "+ add"], ["sub", "+ subtract"], ["increment", "+ increment"], ["decrement", "+ decrement"], ["assign", "+ assignment"], ["declare", "+ variable"], ["expr", "+ expression"], ["call", "+ call"], ["create", "+ create"], ["free", "+ free"], ["if", "+ if"], ["while", "+ while"], ["for_each", "+ for each"], ["switch", "+ switch"], ["return", "+ return"]]) {
            const b = el("button", "ghost", label); b.type = "button";
            b.addEventListener("click", () => { list.push(newStatement(op)); onChange(true); });
            choices.append(b);
            buttons.push({ button: b, text: `${op} ${label}`.toLowerCase() });
        }
        search.addEventListener("input", () => {
            const query = search.value.trim().toLowerCase();
            let count = 0;
            for (const item of buttons) {
                item.button.hidden = !item.text.includes(query);
                if (!item.button.hidden) count++;
            }
            empty.hidden = count > 0;
        });
        bar.addEventListener("toggle", () => { if (bar.open) search.focus(); });
        bar.addEventListener("keydown", event => {
            if (event.key === "Escape" && bar.open) {
                event.preventDefault();
                bar.open = false;
                toggle.focus();
            }
        });
        panel.append(search, choices, empty);
        bar.append(toggle, panel);
        return bar;
    }

    let drag = null;
    function contains(stmt, list) {
        const lists = stmt.op === "if" ? [stmt.then, stmt.else] :
            stmt.op === "while" || stmt.op === "for_each" ? [stmt.body] :
            stmt.op === "switch" ? [...stmt.cases.map(item => item.body), ...(stmt.default === null ? [] : [stmt.default])] : [];
        return lists.some(inner => inner === list || inner.some(child => contains(child, list)));
    }
    function dropZone(list, at, onChange) {
        const zone = el("div", "dropzone");
        zone.addEventListener("dragover", event => {
            if (!drag || list === drag.stmt.then || list === drag.stmt.else || contains(drag.stmt, list)) return;
            event.preventDefault();
            zone.classList.add("over");
        });
        zone.addEventListener("dragleave", () => zone.classList.remove("over"));
        zone.addEventListener("drop", event => {
            event.preventDefault();
            if (!drag || contains(drag.stmt, list)) return;
            const { list: from, stmt } = drag;
            const old = from.indexOf(stmt);
            if (old < 0) return;
            from.splice(old, 1);
            list.splice(from === list && old < at ? at - 1 : at, 0, stmt);
            drag = null;
            onChange(true);
        });
        return zone;
    }

    function renderBlocks(list, container, onChange, fixed = false) {
        container.replaceChildren();
        list.forEach((stmt, i) => {
            if (!fixed) container.append(dropZone(list, i, onChange));
            const block = el("div", `block block-${stmt.op}`);
            const head = el("div", "block-head");
            const grip = el("span", "grip", "⠿");
            grip.draggable = true;
            grip.title = "Drag to move";
            grip.addEventListener("dragstart", event => {
                drag = { list, stmt };
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData("text/plain", stmt.op);
                event.dataTransfer.setDragImage(block, 10, 10);
                document.body.classList.add("dragging");
                setTimeout(() => block.classList.add("lifted"), 0);
            });
            grip.addEventListener("dragend", () => {
                drag = null;
                document.body.classList.remove("dragging");
                block.classList.remove("lifted");
            });
            if (!fixed) head.append(grip);
            const tools = el("span", "block-tools");
            const tool = (label, title, fn) => {
                const b = el("button", "icon", label); b.type = "button"; b.title = title;
                b.addEventListener("click", fn); tools.append(b);
            };
            if (!fixed) tool("✕", "Remove", () => { list.splice(i, 1); onChange(true); });
            if (["set", "add", "sub", "assign"].includes(stmt.op)) {
                head.append(el("span", "kw", { set: "set", add: "add to", sub: "subtract from", assign: "assign" }[stmt.op]));
                head.append(slot(() => stmt.target, v => { stmt.target = v; }, ["Fields", "Globals", "Locals"], "[variable]", () => onChange(false), v => stmtProblem({ ...stmt, target: v }), true));
                if (stmt.op === "assign") {
                    const select = el("select", "op-select");
                    for (const op of ["=", "+=", "-=", "*=", "/=", "&=", "|=", "^=", "<<=", ">>="]) {
                        const option = el("option", "", op); option.value = op; option.selected = stmt.operator === op; select.append(option);
                    }
                    select.addEventListener("change", () => { stmt.operator = select.value; onChange(false); });
                    head.append(select);
                }
                if (stmt.op === "set") head.append(el("span", "kw", "to"));
                head.append(renderExpr(stmt.value, onChange, () => stmtProblem(stmt), 0, true, () => targetType(stmt.target)));
            } else if (stmt.op === "increment" || stmt.op === "decrement") {
                head.append(el("span", "kw", stmt.op),
                    slot(() => stmt.target, v => { stmt.target = v; }, ["Fields", "Globals", "Locals"],
                        "[variable]", () => onChange(false), null, true));
                const position = el("select", "op-select");
                position.setAttribute("aria-label", "Increment position");
                for (const [value, label] of [["false", "postfix"], ["true", "prefix"]]) {
                    const option = el("option", "", label); option.value = value; option.selected = !!stmt.prefix === (value === "true"); position.append(option);
                }
                position.addEventListener("change", () => { stmt.prefix = position.value === "true"; onChange(false); });
                head.append(position);
            } else if (stmt.op === "free") {
                head.append(el("span", "kw", "free"));
                head.append(slot(() => stmt.target, v => { stmt.target = v; }, ["Slots"], "[Table[slot]]", () => onChange(false)));
            } else if (stmt.op === "create") {
                head.append(el("span", "kw", "create in"));
                head.append(slot(() => stmt.table, v => { stmt.table = v; }, ["Tables"], "[table]", () => onChange(false)));
                head.append(el("span", "kw", "store slot in"));
                head.append(slot(() => stmt.var, v => { stmt.var = v; }, ["None"], "[new variable, optional]", () => onChange(false)));
            } else if (stmt.op === "call") {
                head.append(el("span", "kw", "call"));
                head.append(slot(() => stmt.fn, v => { stmt.fn = v; }, ["Functions", "Hardware", "Bit"], "[function]", () => onChange(true)));
                const fn = VisualGelModel.referenceText(stmt.fn);
                const intrinsic = VisualGelModel.parseIntrinsic(`${fn}(${stmt.args || (["bit_set", "bit_clear", "bit_test", "peek8l"].includes(fn) ? ", " : "")})`);
                if (intrinsic) {
                    head.append(renderIntrinsic(fn, intrinsic.args, args => { stmt.args = args.join(", "); }, onChange, "", false));
                } else {
                    head.append(el("span", "kw", "("));
                    head.append(slot(() => stmt.args, v => { stmt.args = v; }, null, "[arguments]", () => onChange(true)));
                    head.append(el("span", "kw", ")"));
                }
            } else if (stmt.op === "return" || stmt.op === "declare") {
                head.append(el("span", "t-ctl", stmt.op === "return" ? "return" : "variable"));
                if (stmt.op === "declare") {
                    const select = el("select", "op-select");
                    for (const type of TYPES) { const option = el("option", `t-${type}`, type); option.value = type; option.selected = stmt.type === type; select.append(option); }
                    select.classList.add(`t-${stmt.type}`);
                    select.addEventListener("change", () => { select.classList.remove(`t-${stmt.type}`); stmt.type = select.value; select.classList.add(`t-${stmt.type}`); onChange(false); });
                    head.append(select, slot(() => stmt.name, v => { stmt.name = v; }, ["None"], "[name]", () => onChange(false)));
                }
                if (stmt.value && VisualGelModel.parseExpr(stmt.value).some(item =>
                    item.t === "val" && (VisualGelModel.parseIntrinsic(item.text) ||
                        (VisualGelModel.parseCall(item.text) && index.valueFunctions.has(VisualGelModel.parseCall(item.text).fn))))) {
                    const expression = VisualGelModel.parseExpr(stmt.value);
                    head.append(renderExpr(expression, rebuild => { stmt.value = VisualGelModel.exprText(expression); onChange(rebuild); }, () => ""));
                } else {
                    head.append(slot(() => stmt.value, (v, picked) => {
                        stmt.value = picked && picked.cat === "Returning functions"
                            ? `${v}(${index.valueFunctions.get(v).params.map(() => "").join(", ")})`
                            : picked && ["bit_test", "bit_set", "bit_clear", "peek8", "peek8l"].includes(v)
                            ? `${v}(${v === "peek8" ? "" : ", "})` : v;
                    }, ["Fields", "Globals", "Locals", "Defines", "Enums", "Bit", "Hardware", "Returning functions"], "[value, optional]", () => onChange(true),
                    stmt.op === "declare" ? v => VisualGelModel.checkValue(stmt.type, v, index.defineValues) : null,
                    true, stmt.op === "declare" ? () => stmt.type : null));
                }
            } else if (stmt.op === "for_each" || stmt.op === "foreachHeader") {
                head.append(el("span", "t-ctl", "for each"), slot(() => stmt.table, v => { stmt.table = v; }, ["Tables"], "[table]", () => onChange(false)),
                    el("span", "kw", "as"), slot(() => stmt.iterator, v => { stmt.iterator = v; }, ["None"], "[iterator]", () => onChange(false)));
            } else if (stmt.op === "expr" || stmt.op === "header") {
                head.append(el("span", "t-ctl", stmt.op === "header" ? stmt.label : "expression"));
                head.append(renderExpr(stmt.value, onChange, () => "", 0, stmt.op !== "header" || stmt.label !== "case value"));
            } else if (["if", "while", "switch"].includes(stmt.op)) {
                head.append(el("span", "t-ctl", stmt.op));
                head.append(renderExpr(stmt.cond, onChange, () => ""));
            }
            head.append(tools);
            block.append(head);
            if (stmt.op === "if") {
                const body = el("div", "block-body");
                renderBlocks(stmt.then, body, onChange);
                block.append(body, addBar(stmt.then, onChange));
                if (stmt.else.length) {
                    const other = el("div", "block-body");
                    renderBlocks(stmt.else, other, onChange);
                    block.append(el("div", "t-ctl else-label", "else"), other, addBar(stmt.else, onChange));
                } else {
                    const add = el("button", "ghost", "+ else"); add.type = "button";
                    add.addEventListener("click", () => { stmt.else.push(newStatement("set")); onChange(true); });
                    block.append(add);
                }
            } else if (stmt.op === "while" || stmt.op === "for_each") {
                const nested = el("div", "block-body");
                renderBlocks(stmt.body, nested, onChange);
                block.append(nested, addBar(stmt.body, onChange));
            } else if (stmt.op === "switch") {
                stmt.cases.forEach((item, n) => {
                    const row = el("div", "block-head");
                    row.append(el("span", "t-ctl", "case"), slot(() => item.value, v => { item.value = v; }, ["None"], "[case value]", () => onChange(false)));
                    const remove = el("button", "icon", "✕"); remove.type = "button"; remove.title = "Remove case";
                    remove.addEventListener("click", () => { stmt.cases.splice(n, 1); onChange(true); });
                    row.append(remove);
                    const nested = el("div", "block-body"); renderBlocks(item.body, nested, onChange);
                    block.append(row, nested, addBar(item.body, onChange));
                });
                const addCase = el("button", "ghost", "+ case"); addCase.type = "button";
                addCase.addEventListener("click", () => { stmt.cases.push({ value: "", body: [] }); onChange(true); });
                block.append(addCase);
                if (stmt.default !== null) {
                    const row = el("div", "block-head", "default");
                    const remove = el("button", "icon", "✕"); remove.type = "button"; remove.title = "Remove default";
                    remove.addEventListener("click", () => { stmt.default = null; onChange(true); });
                    row.append(remove);
                    const nested = el("div", "block-body"); renderBlocks(stmt.default, nested, onChange);
                    block.append(row, nested, addBar(stmt.default, onChange));
                } else {
                    const addDefault = el("button", "ghost", "+ default"); addDefault.type = "button";
                    addDefault.addEventListener("click", () => { stmt.default = []; onChange(true); });
                    block.append(addDefault);
                }
            }
            container.append(block);
        });
        if (!fixed) container.append(dropZone(list, list.length, onChange));
    }

    function targetType(target) {
        target = VisualGelModel.referenceText(target);
        const field = /^([A-Za-z_]\w*)\[[^\[\]]+\]\.([A-Za-z_]\w*)$/.exec(target);
        if (field) { const table = index.tables.get(field[1]); return table && table[field[2]] || ""; }
        if (Object.prototype.hasOwnProperty.call(localTypes, target)) return localTypes[target];
        if (locals.includes(target)) return "";
        if (index.defineValues[target] && index.defineValues[target] !== target) {
            const alias = index.defineValues[target];
            const fieldAlias = /^([A-Za-z_]\w*)\[[^\[\]]+\]\.([A-Za-z_]\w*)$/.exec(alias);
            if (fieldAlias) { const table = index.tables.get(fieldAlias[1]); return table && table[fieldAlias[2]] || ""; }
        }
        return index.globals.get(target) || "";
    }

    function bitInfo(target, bit) {
        const type = targetType(target);
        const width = ["u8", "i8"].includes(type) ? 8 : type ? 16 : null;
        const literal = /^(?:-?\d+|0x[0-9a-f]+)$/i;
        const resolved = literal.test(bit) ? bit : index.defineValues[bit] === undefined ? String(index.enumValues[bit]) : index.defineValues[bit];
        const number = typeof resolved === "string" && literal.test(resolved.trim()) ? Number(resolved.trim()) : null;
        return { width, number };
    }

    function renderIntrinsic(fn, args, setArgs, onChange, prefix = "", showName = true) {
        const host = el("span", "intrinsic");
        if (prefix) host.append(el("span", "kw", prefix));
        if (showName) host.append(el("span", fn.startsWith("bit_") ? "t-bit" : "t-hw", fn));
        host.append(el("span", "kw", "("));
        const labels = fn.startsWith("bit_") ? ["bit target", "bit index"] :
            fn === "peek8l" ? ["constant bank", "16-bit address"] : ["bank-0 address"];
        labels.forEach((label, i) => {
            if (i) host.append(el("span", "kw", ","));
            const categories = fn.startsWith("bit_") && i === 0 ? ["Fields", "Globals", "Locals"] :
                ["Fields", "Globals", "Locals", "Defines", "Enums"];
            host.append(slot(() => args[i], value => { args[i] = value; setArgs(args); }, categories,
                `[${label}]`, () => onChange(true), null, true));
        });
        host.append(el("span", "kw", ")"));
        if (fn.startsWith("bit_")) host.append(bitChooser(fn, args, setArgs, onChange));
        else host.append(el("span", "bit-hint", fn === "peek8" ? "Reads one byte from bank 0" : "Reads one byte from a constant bank"));
        return host;
    }

    function bitChooser(fn, args, setArgs, onChange) {
        const [target, bit] = args;
        const { width, number } = bitInfo(target, bit);
        const host = el("div", "bit-chooser");
        const count = width || 16;
        const message = !width ? "Target width unknown; showing 0–15. Build to verify." :
            `Compiler bit range: 0–${width - 1} (${targetType(target)})`;
        host.append(el("span", "bit-hint", message));
        const grid = el("div", "bit-grid");
        const action = { bit_set: "Set", bit_clear: "Clear", bit_test: "Test" }[fn];
        grid.setAttribute("aria-label", `Choose bit to ${action.toLowerCase()}`);
        for (let n = count - 1; n >= 0; n--) {
            const selected = n === number;
            const button = el("button", selected ? "bit-choice selected" : "bit-choice", `${n}`);
            button.type = "button";
            button.setAttribute("aria-label", `${action} bit ${n}, mask 0x${(2 ** n).toString(16).toUpperCase()}`);
            button.setAttribute("aria-pressed", String(selected));
            button.addEventListener("click", () => { args[1] = String(n); setArgs(args); onChange(true); });
            grid.append(button);
        }
        host.append(grid);
        if (number !== null && number >= 0 && number < count)
            host.append(el("span", "bit-hint", `Bit ${number}: mask 0x${(2 ** number).toString(16).toUpperCase().padStart(count / 4, "0")} (${action.toLowerCase()})`));
        else if (bit) host.append(el("span", "bit-hint", number === null
            ? "Symbolic expression: bit position will be checked by the compiler."
            : `Bit ${number} is outside the ${count}-bit range.`));
        return host;
    }

    function exprProblem(type, items) {
        if (typeof items === "string") return VisualGelModel.checkValue(type, items, index.defineValues);
        if (items.length === 1 && (items[0].t === "val" || items[0].t === "ref")) return VisualGelModel.checkValue(type, items[0].t === "ref" ? items[0].name : items[0].text, index.defineValues);
        const whole = ["u8", "u16", "i8", "i16"].includes(type);
        for (const item of items) {
            const problem = item.t === "group" ? (item.not ? "" : exprProblem(type, item.items))
                : item.t === "val" && whole && /^-?\d+\.\d+$/.test(item.text.trim()) ? `${type} holds whole numbers only, not ${item.text.trim()}.` : "";
            if (problem) return problem;
        }
        return "";
    }

    function stmtProblem(stmt) {        if (!["set", "add", "sub", "assign"].includes(stmt.op) || !stmt.target || !stmt.value) return "";
        const type = targetType(stmt.target);
        if (!type) return "";
        const problem = exprProblem(type, stmt.value);
        return problem ? `${VisualGelModel.referenceText(stmt.target)} is ${type}; ${problem}` : "";
    }

    function intrinsicProblem(fn, args) {
        if (args.some(arg => !arg.trim())) return `${fn} needs ${fn === "peek8" ? "an address" : "both arguments"}.`;
        if (!fn.startsWith("bit_")) return "";
        const { width, number } = bitInfo(...args);
        if (number !== null && (number < 0 || number >= (width || 16)))
            return `${fn} bit index ${number} is outside 0–${(width || 16) - 1}.`;
        return "";
    }

    function expressionIntrinsicProblem(value) {
        const items = typeof value === "string" ? VisualGelModel.parseExpr(value) : value;
        if (!Array.isArray(items)) return "";
        for (const item of items) {
            if (item.t === "group") {
                const problem = expressionIntrinsicProblem(item.items);
                if (problem) return problem;
            } else if (item.t === "val") {
                const intrinsic = VisualGelModel.parseIntrinsic(item.text);
                if (intrinsic) {
                    const problem = intrinsicProblem(intrinsic.fn, intrinsic.args);
                    if (problem) return problem;
                }
                const call = VisualGelModel.parseCall(item.text);
                if (call && index.valueFunctions.has(call.fn)) {
                    const count = index.valueFunctions.get(call.fn).params.length;
                    if (call.args.length !== count || call.args.some(arg => !arg.trim()))
                        return `${call.fn} needs ${count} argument(s).`;
                }
            }
        }
        return "";
    }

    function firstTypeProblem(list) {
        for (const stmt of list) {
            let bitProblem = "";
            if (stmt.op === "call" && ["bit_set", "bit_clear", "bit_test", "peek8", "peek8l"].includes(VisualGelModel.referenceText(stmt.fn))) {
                const parsed = VisualGelModel.parseIntrinsic(`${VisualGelModel.referenceText(stmt.fn)}(${stmt.args})`);
                if (parsed) bitProblem = intrinsicProblem(parsed.fn, parsed.args);
                else bitProblem = `${VisualGelModel.referenceText(stmt.fn)} has the wrong number of arguments.`;
            }
            const problem = bitProblem || expressionIntrinsicProblem(stmt.cond) || expressionIntrinsicProblem(stmt.value) ||
                stmtProblem(stmt) || (stmt.op === "if" && (firstTypeProblem(stmt.then) || firstTypeProblem(stmt.else))) ||
                (["while", "for_each"].includes(stmt.op) && firstTypeProblem(stmt.body)) ||
                (stmt.op === "switch" && (stmt.cases.map(item => firstTypeProblem(item.body)).find(Boolean) || (stmt.default && firstTypeProblem(stmt.default))));
            if (problem) return problem;
        }
        return "";
    }

    document.addEventListener("mousedown", event => {
        if (openPicker && !openPicker.parentElement.contains(event.target)) closePicker();
    }, true);

    return { buildIndex, emptyIndex, setIndex(value) { index = value; }, setLocals(value, types = {}) { locals = value; localTypes = types; }, highlight, el, renderBlocks, addBar, firstTypeProblem, resetSelection() { selection = null; closePicker(); } };
    }
    root.createVisualGelEditor = createVisualGelEditor;
})(globalThis);
