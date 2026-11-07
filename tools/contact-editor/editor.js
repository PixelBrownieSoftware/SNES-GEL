(function () {
    "use strict";
    const rows = document.getElementById("rows");
    const status = document.getElementById("status");
    const preview = document.getElementById("preview");
    const handlerHost = document.getElementById("handlers");
    const serverBadge = document.getElementById("server-state");
    const keys = ["self", "other", "enter", "stay", "exit"];
    const phaseKeys = ["enter", "stay", "exit"];
    const TYPES = ["u8", "u16", "i8", "i16", "f16", "f32"];
    const CONTROL = new Set(["if", "else", "switch", "case", "default", "while", "break", "return", "for_each"]);
    let interactions = [];
    let handlers = {};
    let index = emptyIndex();

    function emptyIndex() {
        return { tables: new Map(), fields: new Map(), enums: new Map(), defines: new Set(), defineValues: {}, functions: new Set(), hardware: new Set(), bit: new Set(), globals: new Map(), items: [] };
    }

    function buildIndex(sym) {
        const idx = emptyIndex();
        const add = (cat, label, cls) => idx.items.push({ cat, label, cls });
        for (const [table, fields] of Object.entries(sym.tables || {})) {
            idx.tables.set(table, fields);
            add("Tables", table, "t-table");
            add("Slots", `${table}[self]`, "t-table");
            add("Slots", `${table}[other]`, "t-table");
            for (const [field, type] of Object.entries(fields)) {
                if (!idx.fields.has(field)) idx.fields.set(field, type);
                add("Fields", `${table}[self].${field}`, `t-${type}`);
                add("Fields", `${table}[other].${field}`, `t-${type}`);
            }
        }
        for (const [name, members] of Object.entries(sym.enums || {})) {
            for (const member of members) { idx.enums.set(member, name); add("Enums", member, "t-enum"); }
        }
        idx.defineValues = sym.defineValues || {};
        for (const name of sym.defines || []) { idx.defines.add(name); add("Defines", name, "t-define"); }
        for (const [name, type] of Object.entries(sym.globals || {})) { idx.globals.set(name, type); add("Globals", name, `t-${type}`); }
        for (const name of Object.keys(sym.functions || {})) { idx.functions.add(name); add("Functions", name, "t-fn"); }
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

    // Symbol search under a slot input; replaces the identifier being typed.
    function attachPicker(input, categories, apply) {
        closePicker();
        const picker = el("div", "picker");
        const tabs = el("div", "picker-tabs");
        const list = el("div", "picker-list");
        picker.append(tabs, list);
        const allowed = i => !categories || categories.includes(i.cat);
        const available = [...new Set(index.items.filter(allowed).map(i => i.cat))];
        let category = "All";
        const fragment = () => /[A-Za-z_]\w*$/.exec(input.value.slice(0, input.selectionStart ?? input.value.length));
        function refresh() {
            tabs.replaceChildren();
            for (const name of ["All", ...available]) {
                const tab = el("button", name === category ? "tab active" : "tab", name);
                tab.type = "button";
                tab.addEventListener("mousedown", event => { event.preventDefault(); category = name; refresh(); });
                tabs.append(tab);
            }
            list.replaceChildren();
            const query = (fragment() || [""])[0].toLowerCase();
            const matches = index.items.filter(i => allowed(i) && (category === "All" || i.cat === category) &&
                i.label.toLowerCase().includes(query)).slice(0, 60);
            if (!matches.length) list.append(el("div", "picker-empty", index.items.length ? "No matching symbols" : "Start the Python server to search project symbols"));
            for (const item of matches) {
                const row = el("div", "picker-item");
                row.append(el("span", item.cls, item.label), el("small", "", item.cat));
                row.addEventListener("mousedown", event => {
                    event.preventDefault();
                    const caret = input.selectionStart ?? input.value.length;
                    input.value = input.value.slice(0, caret).replace(/[A-Za-z_]\w*$/, "") + item.label + input.value.slice(caret);
                    apply();
                    refresh();
                });
                list.append(row);
            }
        }
        input.addEventListener("input", refresh);
        input.parentElement.append(picker);
        openPicker = picker;
        refresh();
    }

    // A slot shows coloured code; clicking turns it into an input with a symbol picker.
    function slot(get, set, categories, placeholder, onChange, validate, commitOnPick) {
        const wrap = el("span", "slot");
        const view = el("span", "slot-view");
        view.tabIndex = 0;
        function show() {
            const value = get();
            if (value) highlight(value, view); else view.textContent = placeholder;
            view.classList.toggle("empty", !value);
            wrap.replaceChildren(view);
        }
        function edit() {
            const input = el("input", "slot-input");
            input.value = get();
            input.setAttribute("aria-label", placeholder);
            wrap.replaceChildren(input);
            attachPicker(input, categories, () => { if (categories || commitOnPick) input.blur(); });
            input.addEventListener("blur", () => {
                closePicker();
                const value = input.value.trim();
                const problem = validate ? validate(value) : "";
                if (problem) status.textContent = `Rejected "${value}": ${problem}`;
                else { set(value); status.textContent = ""; }
                show();
                onChange(false);
            });
            input.addEventListener("keydown", event => {
                if (event.key === "Enter") input.blur();
                else if (event.key === "Escape") { input.value = get(); input.blur(); }
            });
            input.focus();
        }
        view.addEventListener("click", edit);
        view.addEventListener("keydown", event => { if (event.key === "Enter") edit(); });
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
        if (op === "call") return { op, fn: "", args: "" };
        if (op === "free") return { op, target: "" };
        if (op === "create") return { op, table: "", var: "" };
        return { op, target: "", value: newExpr() };
    }

    // Structured expression: values, operator choosers and real bracket groups.
    // `check` returns a problem message for the whole expression in its current state.
    function renderExpr(items, onChange, check, depth = 0) {
        const box = el("span", depth ? "expr group" : "expr");
        const rebuild = () => { selection = null; onChange(true); };
        const range = selectedRange(items);
        const inRange = i => range && i >= range[0] && i <= range[1];
        items.forEach((item, i) => {
            if (item.t === "op") {
                const select = el("select", "op-select");
                select.title = "Change operator";
                for (const op of ContactModel.OPERATORS) {
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
            pick.title = "Select values: click one, then click another (or shift-click) to select everything between, then press ( ) selected";
            pick.addEventListener("click", event => toggleSelect(items, i, event.shiftKey, () => onChange(true)));
            term.append(pick);
            if (item.t === "group") {
                term.classList.add("is-group");
                term.append(el("span", "bracket", "("), renderExpr(item.items, onChange, check, depth + 1), el("span", "bracket", ")"));
                const unwrap = el("button", "icon tiny", "⤺"); unwrap.type = "button"; unwrap.title = "Remove the brackets, keeping what is inside";
                unwrap.addEventListener("click", () => { items.splice(i, 1, ...item.items); rebuild(); });
                term.append(unwrap);
            } else {
                term.append(slot(() => item.text, v => {
                    item.text = v;
                    const parsed = ContactModel.parseExpr(v);
                    if (parsed.length > 1) {
                        if (items.length === 1) items.splice(0, 1, ...parsed); else items[i] = { t: "group", items: parsed };
                        rebuild();
                    }
                }, null, "[value]", () => onChange(false), v => {
                    const old = item.text; item.text = v;
                    const problem = check();
                    item.text = old;
                    return problem;
                }, true));
            }
            box.append(term);
        });
        const add = el("select", "op-select add-op");
        add.title = "Add an operator and another value";
        const prompt = el("option", "", "+ op"); prompt.value = ""; add.append(prompt);
        for (const op of ContactModel.OPERATORS) { const option = el("option", "", op); option.value = op; add.append(option); }
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
                ContactModel.wrapRange(items, range[0], range[1]);
                rebuild();
            });
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
        const bar = el("div", "add-bar");
        for (const [op, label] of [["set", "+ set"], ["add", "+ add"], ["sub", "+ subtract"], ["call", "+ call"], ["create", "+ create"], ["free", "+ free"], ["if", "+ if"]]) {
            const b = el("button", "ghost", label); b.type = "button";
            b.addEventListener("click", () => { list.push(newStatement(op)); onChange(true); });
            bar.append(b);
        }
        return bar;
    }

    let drag = null;
    function contains(stmt, list) {
        return stmt.op === "if" && [stmt.then, stmt.else].some(inner => inner === list || inner.some(child => contains(child, list)));
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
            if (!drag) return;
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

    function renderBlocks(list, container, onChange) {
        container.replaceChildren();
        list.forEach((stmt, i) => {
            container.append(dropZone(list, i, onChange));
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
            head.append(grip);
            const tools = el("span", "block-tools");
            const tool = (label, title, fn) => {
                const b = el("button", "icon", label); b.type = "button"; b.title = title;
                b.addEventListener("click", fn); tools.append(b);
            };
            tool("✕", "Remove", () => { list.splice(i, 1); onChange(true); });
            if (stmt.op === "set" || stmt.op === "add" || stmt.op === "sub") {
                head.append(el("span", "kw", { set: "set", add: "add to", sub: "subtract from" }[stmt.op]));
                head.append(slot(() => stmt.target, v => { stmt.target = v; }, ["Fields", "Globals"], "[variable]", () => onChange(false), v => stmtProblem({ ...stmt, target: v })));
                if (stmt.op === "set") head.append(el("span", "kw", "to"));
                head.append(renderExpr(stmt.value, onChange, () => stmtProblem(stmt)));
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
                head.append(slot(() => stmt.fn, v => { stmt.fn = v; }, ["Functions", "Hardware", "Bit"], "[function]", () => onChange(false)));
                head.append(el("span", "kw", "("));
                head.append(slot(() => stmt.args, v => { stmt.args = v; }, null, "[arguments]", () => onChange(false)));
                head.append(el("span", "kw", ")"));
            } else {
                head.append(el("span", "t-ctl", "if"));
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
            }
            container.append(block);
        });
        container.append(dropZone(list, list.length, onChange));
    }

    function usedHandlers() {
        const names = [];
        for (const row of interactions) for (const k of phaseKeys) {
            const name = row[k].trim();
            if (/^[A-Za-z_]\w*$/.test(name) && !names.includes(name)) names.push(name);
        }
        return names;
    }

    function renderHandlers() {
        handlerHost.replaceChildren();
        const names = usedHandlers();
        if (!names.length) handlerHost.append(el("p", "hint", "Name a handler in the table above to build its body visually."));
        const changed = rebuild => { if (rebuild) renderHandlers(); update(); };
        for (const name of names) {
            const card = el("section", "card");
            const title = el("h3", "code-title");
            highlight(`fn ${name}(u8 self, u8 other)`, title);
            const body = el("div", "block-body");
            const list = handlers[name] || (handlers[name] = []);
            renderBlocks(list, body, changed);
            card.append(title, body, addBar(list, changed));
            if (!list.length) card.append(el("p", "hint", `Empty: no function is generated, so define fn ${name} yourself.`));
            handlerHost.append(card);
        }
    }

    function data() {
        const used = new Set(usedHandlers());
        const scripts = {};
        for (const [name, body] of Object.entries(handlers)) if (used.has(name) && body.length) scripts[name] = body;
        return { version: 1, interactions, handlers: scripts };
    }

    function targetType(target) {
        const field = /^([A-Za-z_]\w*)\[[^\[\]]+\]\.([A-Za-z_]\w*)$/.exec(target);
        if (field) { const table = index.tables.get(field[1]); return table && table[field[2]] || ""; }
        return index.globals.get(target) || "";
    }

    function exprProblem(type, items) {
        if (items.length === 1 && items[0].t === "val") return ContactModel.checkValue(type, items[0].text, index.defineValues);
        const whole = ["u8", "u16", "i8", "i16"].includes(type);
        for (const item of items) {
            const problem = item.t === "group" ? exprProblem(type, item.items)
                : item.t === "val" && whole && /^-?\d+\.\d+$/.test(item.text.trim()) ? `${type} holds whole numbers only, not ${item.text.trim()}.` : "";
            if (problem) return problem;
        }
        return "";
    }

    function stmtProblem(stmt) {        if (!["set", "add", "sub"].includes(stmt.op) || !stmt.target || !stmt.value) return "";
        const type = targetType(stmt.target);
        if (!type) return "";
        const problem = ContactModel.checkValue(type, stmt.value, index.defineValues);
        return problem ? `${stmt.target} is ${type}; ${problem}` : "";
    }

    function firstTypeProblem(list) {
        for (const stmt of list) {
            const problem = stmtProblem(stmt) || (stmt.op === "if" && (firstTypeProblem(stmt.then) || firstTypeProblem(stmt.else)));
            if (problem) return problem;
        }
        return "";
    }

    function checkedConfig() {
        const config = data();
        for (const [name, body] of Object.entries(config.handlers)) {
            const problem = firstTypeProblem(body);
            if (problem) throw new Error(`Handler ${name}: ${problem}`);
        }
        return config;
    }

    function update() {
        try {
            highlight(ContactModel.generate(checkedConfig()), preview);
            status.textContent = "";
        } catch (error) {
            preview.textContent = "";
            status.textContent = error.message;
        }
    }

    function render() {
        rows.replaceChildren();
        interactions.forEach((row, i) => {
            const tr = document.createElement("tr");
            for (const key of keys) {
                const td = document.createElement("td");
                const input = document.createElement("input");
                input.value = row[key];
                input.setAttribute("aria-label", `${key} for interaction ${i + 1}`);
                input.addEventListener("input", () => { row[key] = input.value; update(); });
                input.addEventListener("change", renderHandlers);
                td.append(input); tr.append(td);
            }
            const td = document.createElement("td");
            const remove = document.createElement("button");
            remove.type = "button"; remove.textContent = "Remove";
            remove.setAttribute("aria-label", `Remove interaction ${i + 1}`);
            remove.addEventListener("click", () => { interactions.splice(i, 1); render(); });
            td.append(remove); tr.append(td); rows.append(tr);
        });
        renderHandlers();
        update();
    }

    function load(config) {
        interactions = ContactModel.validate(config);
        handlers = ContactModel.validateHandlers(config);
        render();
    }

    function cleanConfig() {
        const config = checkedConfig();
        return { version: 1, interactions: ContactModel.validate(config), handlers: ContactModel.validateHandlers(config) };
    }

    function download(name, content, type) {
        const url = URL.createObjectURL(new Blob([content], { type }));
        const link = document.createElement("a");
        link.href = url; link.download = name; link.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    const saveProject = document.getElementById("save-project");
    document.getElementById("add").addEventListener("click", () => {
        interactions.push({ self: "", other: "", enter: "", stay: "", exit: "" }); render();
    });
    document.getElementById("save").addEventListener("click", () => {
        try { download("contacts.json", JSON.stringify(cleanConfig(), null, 2) + "\n", "application/json"); }
        catch (error) { status.textContent = error.message; }
    });
    document.getElementById("export").addEventListener("click", () => {
        try { download("contact.gel", ContactModel.generate(checkedConfig()), "text/plain"); }
        catch (error) { status.textContent = error.message; }
    });
    saveProject.addEventListener("click", async () => {
        try {
            const config = cleanConfig();
            const response = await fetch("/api/contacts", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ config, gel: ContactModel.generate(config) }),
            });
            const result = await response.json();
            if (!response.ok) throw new Error(result.error || "Save failed.");
            status.textContent = `Saved ${result.saved.join(" and ")} to the project folder.`;
        } catch (error) { status.textContent = `Could not save: ${error.message}`; }
    });
    document.getElementById("load-button").addEventListener("click", () => document.getElementById("load").click());
    document.getElementById("load").addEventListener("change", async event => {
        const file = event.target.files[0];
        if (!file) return;
        try { load(JSON.parse(await file.text())); }
        catch (error) { status.textContent = `Could not load configuration: ${error.message}`; }
        event.target.value = "";
    });
    document.getElementById("refresh-symbols").addEventListener("click", () => loadSymbols().then(render));

    async function loadSymbols() {
        try {
            const response = await fetch("/api/symbols", { cache: "no-store" });
            if (!response.ok) throw new Error(String(response.status));
            index = buildIndex(await response.json());
            serverBadge.textContent = `Connected: ${index.items.length} project symbols`;
            serverBadge.className = "badge ok";
            saveProject.disabled = false;
            return true;
        } catch (error) {
            index = emptyIndex();
            serverBadge.textContent = "Offline: run start.bat for project symbols and saving";
            serverBadge.className = "badge";
            saveProject.disabled = true;
            return false;
        }
    }

    document.addEventListener("mousedown", event => {
        if (openPicker && !openPicker.parentElement.contains(event.target)) closePicker();
    }, true);

    (async function init() {
        if (await loadSymbols()) {
            try {
                const response = await fetch("/api/contacts", { cache: "no-store" });
                if (response.ok) load(await response.json());
            } catch (error) { status.textContent = `Could not load contacts.json: ${error.message}`; }
        }
        render();
    })();
})();
