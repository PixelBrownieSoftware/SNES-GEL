(function () {
    "use strict";
    const file = document.getElementById("file");
    const functionSelect = document.getElementById("function");
    const addFunction = document.getElementById("add-function");
    const removeFunction = document.getElementById("remove-function");
    const body = document.getElementById("function-body");
    const added = document.getElementById("new-statements");
    const closing = document.getElementById("closing");
    const status = document.getElementById("status");
    const save = document.getElementById("save");
    const undo = document.getElementById("undo");
    const redo = document.getElementById("redo");
    const symbolList = document.getElementById("symbol-list");
    const symbolFilter = document.getElementById("symbol-filter");
    const symbolStatus = document.getElementById("symbols-status");
    const memoryList = document.getElementById("memory-list");
    const memorySummary = document.getElementById("memory-summary");
    const memoryCategory = document.getElementById("memory-category");
    const memoryOccupancy = document.getElementById("memory-occupancy");
    const selectedMemory = new Set();
    let memoryEntries = [];
    const visual = createVisualGelEditor({ status });
    const { el, renderBlocks, addBar } = visual;
    let documentData = null, symbols = null, memory = null, selected = -1;
    let blocks = [], additions = [], initial = "", history = null, restoring = false;

    const tabs = [document.getElementById("code-tab"), document.getElementById("declarations-tab")];
    function selectTab(tab) {
        for (const button of tabs) {
            const active = button === tab;
            button.setAttribute("aria-selected", String(active));
            button.tabIndex = active ? 0 : -1;
            document.getElementById(button.getAttribute("aria-controls")).hidden = !active;
        }
    }
    for (const tab of tabs) {
        tab.addEventListener("click", () => selectTab(tab));
        tab.addEventListener("keydown", event => {
            if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
            event.preventDefault();
            const next = event.key === "Home" ? tabs[0] : event.key === "End" ? tabs[tabs.length - 1] : tabs[1 - tabs.indexOf(tab)];
            selectTab(next);
            next.focus();
        });
    }

    async function request(url, options) {
        const response = await fetch(url, options);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        return data;
    }

    function renderMemory() {
        memoryList.replaceChildren();
        memoryOccupancy.replaceChildren();
        if (!memory || !memory.available) {
            memorySummary.textContent = "No compiled memory layout available.";
            memoryOccupancy.append(el("p", "", "Build the game to see reported occupancy."));
            return;
        }
        const occupancy = VisualGelMemoryLayout.occupancy(memory);
        const labels = [
            ["ram", "RAM variables"], ["field", "Struct table fields"], ["mask", "Pool masks"],
            ["overlap", "Overlapping categories"], ["unreported", "Not reported"]
        ];
        const occupied = occupancy.capacity - occupancy.counts.unreported;
        memoryOccupancy.append(el("p", "", `${occupied} of ${occupancy.capacity} bytes reported occupied (${(occupied / occupancy.capacity * 100).toFixed(1)}%).`));
        const bar = el("div", "occupancy-bar");
        bar.setAttribute("aria-hidden", "true");
        const legend = el("div", "occupancy-legend");
        for (const [category, label] of labels) {
            const bytes = occupancy.counts[category];
            const percent = bytes / occupancy.capacity * 100;
            const segment = el("span", `occupancy-segment occupancy-${category}`);
            segment.style.width = `${percent}%`;
            bar.append(segment);
            const item = el("span", "occupancy-legend-item");
            const swatch = el("span", `occupancy-swatch occupancy-${category}`);
            swatch.setAttribute("aria-hidden", "true");
            item.append(swatch, el("span", "", `${label}: ${bytes} bytes (${percent.toFixed(1)}%)`));
            legend.append(item);
        }
        memoryOccupancy.append(bar, legend);
        if (occupancy.outside) memoryOccupancy.append(el("p", "", "Some reported RAM addresses lie outside this low WRAM window and are not included."));
        const visible = VisualGelMemoryLayout.filter(memoryEntries, memoryCategory.value, symbolFilter.value);
        const selected = () => memoryEntries.filter(item => selectedMemory.has(item.id));
        function updateSummary() {
            const visibleTotals = VisualGelMemoryLayout.totals(visible);
            const chosen = selected();
            const chosenTotals = VisualGelMemoryLayout.totals(chosen);
            memorySummary.textContent = `Visible: ${visible.length} items, ${visibleTotals.ram} RAM bytes and ${visibleTotals.mmio} MMIO bytes. ` +
                `Selected: ${chosen.length} items, ${chosenTotals.ram} RAM bytes and ${chosenTotals.mmio} MMIO bytes (including hidden selections). ` +
                "Totals sum reported items and may count overlapping addresses more than once.";
        }
        updateSummary();
        if (!visible.length) {
            memoryList.append(el("p", "", "No matching compiled memory items."));
            return;
        }
        const largest = Math.max(...visible.map(item => item.bytes), 1);
        const list = el("div", "memory-list");
        for (const item of visible) {
            const label = el("label", `memory-item memory-item-${item.category}`);
            const checkbox = el("input");
            checkbox.type = "checkbox";
            checkbox.checked = selectedMemory.has(item.id);
            checkbox.addEventListener("change", () => {
                if (checkbox.checked) selectedMemory.add(item.id);
                else selectedMemory.delete(item.id);
                updateSummary();
            });
            const content = el("span", "memory-item-content");
            const title = el("span", "memory-item-title");
            title.append(el("strong", "", item.name), el("span", "", `${item.bytes} bytes`));
            const address = `$${item.address.toString(16).toUpperCase().padStart(4, "0")}`;
            const detail = el("span", "memory-item-detail", `${item.detail} · first address ${address}`);
            const bar = el("span", "memory-bar");
            bar.setAttribute("aria-hidden", "true");
            const fill = el("span", "memory-bar-fill");
            fill.style.width = `${item.bytes / largest * 100}%`;
            bar.append(fill);
            content.append(title, detail, bar);
            label.append(checkbox, content);
            list.append(label);
        }
        memoryList.append(list);
    }

    function renderSymbols() {
        symbolList.replaceChildren();
        if (!symbols) return;
        const query = symbolFilter.value.trim().toLowerCase();
        const matches = text => text.toLowerCase().includes(query);
        const address = value => `$${value.toString(16).toUpperCase().padStart(4, "0")}`;
        function section(title, rows) {
            if (!rows.length) return;
            const group = el("section", "symbol-section");
            const heading = el("div", "symbol-heading");
            heading.append(el("h3", "", title), el("span", "symbol-count", String(rows.length)));
            group.append(heading);
            const list = el("div", "symbol-cards");
            for (const row of rows) {
                const card = el("div", "symbol-card");
                if (typeof row === "string") {
                    const code = el("code"); visual.highlight(row, code); card.append(code);
                }
                else {
                    card.append(el("strong", "", row.name), el("span", "symbol-detail", row.detail));
                    if (row.fields && row.fields.length) {
                        const table = el("table", "symbol-fields");
                        const header = el("tr");
                        for (const label of ["Field", "Type", "Compiled address", "Stride"]) header.append(el("th", "", label));
                        const thead = el("thead"); thead.append(header); table.append(thead);
                        const tbody = el("tbody");
                        for (const field of row.fields) {
                            const tr = el("tr");
                            for (const cell of field) {
                                const td = el("td"); visual.highlight(cell, td); tr.append(td);
                            }
                            tbody.append(tr);
                        }
                        table.append(tbody); card.append(table);
                    }
                }
                list.append(card);
            }
            group.append(list);
            symbolList.append(group);
        }
        section("Defines (text substitutions; not memory allocations)",
            symbols.defines.filter(matches).map(name => `${name} = ${symbols.defineValues[name] || "(no value)"}`));
        section("Declared globals",
            Object.entries(symbols.globals).filter(([name]) => matches(name))
                .map(([name, type]) => `${type} ${name}`));
        if (selected >= 0) section("Locals and parameters in selected function",
            documentData.functions[selected].locals.filter(matches));
        section("Enums", Object.entries(symbols.enums).flatMap(([name, values]) =>
            matches(name) ? [`${name}: ${values.join(", ")}`] :
                values.filter(matches).map(value => `${name}: ${value}`)));
        section("Structs and tables", Object.entries(symbols.tables).flatMap(([name, fields]) => {
            const layout = memory && memory.tables.find(table => table.name === name);
            const fieldEntries = Object.entries(fields).filter(([field]) => matches(name) || matches(field));
            if (!fieldEntries.length && !matches(name)) return [];
            const detail = layout ? `${layout.size} slots${layout.mask === null ? "" : ` · pool mask ${address(layout.mask)}`}` : "No compiled table layout";
            return [{ name, detail, fields: fieldEntries.map(([field, type]) => {
                const found = layout && layout.fields.find(entry => entry.name === field);
                return [field, type, found ? `RAM ${address(found.address)} (${found.type})` : "Not in compiler output", found ? `${found.stride} bytes/slot` : "—"];
            }) }];
        }));
        section("Functions", Object.entries(symbols.functions).filter(([name]) => matches(name))
            .map(([name, value]) => `${name}(${value.params.join(", ")})`));
        if (memory) {
            section("Compiled RAM and MMIO", memory.variables.filter(item => matches(item.name))
                .map(item => `${item.kind.toUpperCase()} ${item.type} ${item.name}: ${address(item.address)}`));
            section("Compiler-only tables", memory.tables.filter(table => !symbols.tables[table.name] && matches(table.name))
                .map(table => ({ name: table.name, detail: `${table.size} slots · compiler output only`,
                    fields: table.fields.map(field => [field.name, field.type, `RAM ${address(field.address)}`, `${field.stride} bytes/slot`]) })));
        }
        if (!symbolList.childElementCount) symbolList.append(el("p", "", "No matching declarations."));
    }

    async function refreshSymbols() {
        symbolStatus.textContent = "Loading declarations and memory layout…";
        try {
            symbols = await request("/api/symbols", { cache: "no-store" });
            try {
                memory = await request("/api/gel-memory", { cache: "no-store" });
                memoryEntries = VisualGelMemoryLayout.entries(memory);
                const ids = new Set(memoryEntries.map(item => item.id));
                for (const id of selectedMemory) if (!ids.has(id)) selectedMemory.delete(id);
                symbolStatus.textContent = memory.available
                    ? "RAM and MMIO addresses are from compiler output and may be stale."
                    : "No compiler output found. Build the game to see RAM and MMIO addresses.";
            } catch (error) {
                memory = null;
                memoryEntries = [];
                symbolStatus.textContent = `Could not load memory layout: ${error.message}`;
            }
            if (selected >= 0) visual.setIndex(visual.buildIndex(symbols));
            renderSymbols();
            renderMemory();
        } catch (error) {
            symbolStatus.textContent = `Could not load declarations and memory layout: ${error.message}`;
        }
    }
    symbolFilter.addEventListener("input", () => { renderSymbols(); renderMemory(); });
    memoryCategory.addEventListener("change", renderMemory);
    document.getElementById("memory-select-visible").addEventListener("click", () => {
        for (const item of VisualGelMemoryLayout.filter(memoryEntries, memoryCategory.value, symbolFilter.value))
            selectedMemory.add(item.id);
        renderMemory();
    });
    document.getElementById("memory-clear").addEventListener("click", () => {
        selectedMemory.clear();
        renderMemory();
    });
    document.getElementById("refresh-symbols").addEventListener("click", refreshSymbols);

    function snapshot() { return { blocks, additions }; }
    function dirty() { return history && JSON.stringify(snapshot()) !== initial; }
    function changed(rebuild) {
        if (!restoring) history.record(snapshot());
        undo.disabled = !history.canUndo;
        redo.disabled = !history.canRedo;
        save.disabled = documentData.name === "contact.gel" || !dirty();
        if (rebuild) render();
    }
    function restore(value) {
        if (!value) return;
        blocks = value.blocks;
        additions = value.additions;
        visual.resetSelection();
        restoring = true;
        try { changed(true); } finally { restoring = false; }
    }
    function raw(text, container = body) {
        if (!text.trim()) return;
        const pre = el("pre", "preserved-source", text);
        pre.setAttribute("aria-label", "Preserved GEL source");
        container.append(pre);
    }
    function renderLayout(items, container, fn) {
        for (const item of items) {
            if (item.kind === "raw") {
                raw(item.text, container);
            } else if (item.kind === "label") {
                container.append(el("div", "t-ctl else-label", item.text));
            } else if (item.kind === "part") {
                const statement = fn.statements[item.index];
                const host = el("div", "editable-statement");
                renderBlocks(blocks[item.index], host, changed, statement.fixed);
                container.append(host);
                if (statement.insertion) {
                    container.append(el("div", "insertion-label", statement.label), addBar(blocks[item.index], changed));
                }
            } else if (item.kind === "control") {
                const control = el("div", `block block-${item.style} existing-control`);
                for (const branch of item.branches) {
                    const section = el("div", "existing-branch");
                    renderLayout(branch.items, section, fn);
                    const nested = el("div", "block-body");
                    renderLayout(branch.body, nested, fn);
                    section.append(nested);
                    control.append(section);
                }
                container.append(control);
            }
        }
    }
    function render() {
        body.replaceChildren();
        added.replaceChildren();
        closing.replaceChildren();
        if (selected < 0) return;
        const fn = documentData.functions[selected];
        if (fn.error) {
            raw(fn.source);
            status.textContent = fn.error;
            return;
        }
        const signature = el("h2", "function-signature");
        visual.highlight(fn.signature, signature);
        const editParams = el("button", "ghost", "Edit parameters"); editParams.type = "button";
        editParams.disabled = documentData.name === "contact.gel";
        editParams.addEventListener("click", () => {
            if (dirty()) {
                status.textContent = "Save or undo function edits before changing parameters.";
                return;
            }
            showParameterEditor(fn, editParams);
        });
        body.append(signature, editParams);
        renderLayout(fn.display, body, fn);
        const heading = el("h2", "", "Add statements at the end of this function");
        const container = el("div", "block-body");
        renderBlocks(additions, container, changed);
        added.append(heading, container, addBar(additions, changed));
    }

    function showParameterEditor(fn, button) {
        button.disabled = true;
        const host = el("dialog", "parameter-editor");
        host.setAttribute("aria-label", "Edit parameters");
        const heading = el("h3", "", "Edit parameters");
        const hint = el("p", "hint", "New parameters require an expression to insert at every existing call. Remove all uses of a parameter before removing it.");
        const rows = el("div", "parameter-rows");
        const editor = el("div", "parameter-edit");
        const previewHost = el("div", "parameter-preview");
        previewHost.hidden = true;
        const message = el("p", "parameter-message");
        message.setAttribute("role", "status");
        message.setAttribute("aria-live", "polite");
        const params = fn.params.map((param, origin) => ({ ...param, origin, argument: "" }));
        const resultLabel = el("label", "", "Return type");
        const resultType = el("select");
        for (const value of ["", "u8", "i8", "u16", "i16", "f16"]) {
            const option = el("option", value ? `t-${value}` : "", value || "Untyped (legacy u16)"); option.value = value; resultType.append(option);
        }
        resultType.value = fn.returnType || "";
        const colorResultType = () => { resultType.className = resultType.value ? `t-${resultType.value}` : ""; };
        colorResultType();
        resultType.addEventListener("change", colorResultType);
        resultLabel.append(resultType);
        function renderRows() {
            rows.replaceChildren();
            params.forEach((param, i) => {
                const row = el("div", "parameter-row");
                const typeLabel = el("label", "", "Type");
                const type = el("select");
                for (const value of ["u8", "i8", "u16", "i16", "f16"]) {
                    const option = el("option", "", value); option.value = value; type.append(option);
                }
                type.value = param.type;
                typeLabel.append(type);
                const nameLabel = el("label", "", "Name");
                const name = el("input"); name.value = param.name;
                function updateTypeColors() {
                    type.className = `t-${param.type}`;
                    name.className = `t-${param.type}`;
                }
                updateTypeColors();
                type.addEventListener("change", () => { param.type = type.value; updateTypeColors(); });
                name.addEventListener("input", () => { param.name = name.value; });
                nameLabel.append(name);
                row.append(typeLabel, nameLabel);
                if (param.origin === null) {
                    const argumentLabel = el("label", "", "Argument for existing calls");
                    const argument = el("input"); argument.value = param.argument;
                    argument.placeholder = "e.g. 0";
                    argument.addEventListener("input", () => { param.argument = argument.value; });
                    argumentLabel.append(argument);
                    row.append(argumentLabel);
                }
                const remove = el("button", "ghost", "Remove"); remove.type = "button";
                remove.setAttribute("aria-label", `Remove parameter ${param.name || i + 1}`);
                remove.addEventListener("click", () => { params.splice(i, 1); renderRows(); });
                row.append(remove);
                rows.append(row);
            });
        }
        renderRows();
        const add = el("button", "ghost", "+ Parameter"); add.type = "button";
        add.addEventListener("click", () => { params.push({ type: "u8", name: "", origin: null, argument: "" }); renderRows(); });
        const apply = el("button", "", "Preview changes"); apply.type = "button";
        const cancel = el("button", "ghost", "Cancel"); cancel.type = "button";
        cancel.addEventListener("click", () => host.close());
        host.addEventListener("close", () => { host.remove(); button.disabled = false; });
        host.addEventListener("cancel", event => {
            if (host.inert) event.preventDefault();
        });
        const options = data => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
        apply.addEventListener("click", async () => {
            if (dirty()) {
                message.textContent = "Save or undo function edits before changing parameters.";
                return;
            }
            apply.disabled = true;
            host.inert = true;
            message.textContent = "";
            try {
                const payload = { name: documentData.name, hash: documentData.hash, function: selected,
                    returnType: resultType.value || null,
                    params: params.map(({ type, name, origin, argument }) => ({ type, name, origin, argument })) };
                const preview = await request("/api/gel-signature", options(payload));
                previewHost.replaceChildren();
                previewHost.append(el("h3", "", "Preview parameter changes"), el("h4", "", "Files to modify"));
                if (preview.files.length) {
                    const list = el("ul", "changed-files");
                    for (const file of preview.files) {
                        const item = el("li", "changed-file");
                        const details = el("details");
                        details.append(el("summary", "", file));
                        const functions = el("ul", "file-functions");
                        for (const name of preview.changed_functions[file])
                            functions.append(el("li", "", `${name}()`));
                        details.append(functions);
                        item.append(details);
                        list.append(item);
                    }
                    previewHost.append(list);
                } else previewHost.append(el("p", "", "No files will be modified."));
                previewHost.append(el("h4", "", "Calls affected"));
                const calls = Object.entries(preview.calls);
                if (calls.length) {
                    const list = el("ul", "affected-calls");
                    for (const [file, count] of calls) {
                        const item = el("li");
                        const details = el("details");
                        details.append(el("summary", "", `${file}: ${count} ${count === 1 ? "call" : "calls"}`));
                        const functions = el("ul", "file-functions");
                        for (const caller of preview.call_functions[file])
                            functions.append(el("li", "", `${caller.name}(): ${caller.count} ${caller.count === 1 ? "call" : "calls"}`));
                        details.append(functions);
                        item.append(details);
                        list.append(item);
                    }
                    previewHost.append(list);
                } else previewHost.append(el("p", "", "No calls affected."));
                if (preview.type_changed)
                    previewHost.append(el("p", "notice", "Parameter types changed: existing argument expressions are unchanged. Build the game to check types."));
                if (preview.return_changed)
                    previewHost.append(el("p", "notice", "Return type changed: callers are unchanged and may behave differently. Build the game to check them."));
                previewHost.append(el("p", "hint", "Only top-level project GEL files are scanned; check external and generated code separately."));
                const confirm = el("button", "", "Apply changes"); confirm.type = "button";
                const back = el("button", "ghost", "Back to parameters"); back.type = "button";
                back.addEventListener("click", () => {
                    previewHost.hidden = true;
                    editor.hidden = false;
                    message.textContent = "";
                    apply.focus();
                });
                confirm.addEventListener("click", async () => {
                    host.inert = true;
                    message.textContent = "";
                    try {
                        const result = await request("/api/gel-signature", options({ ...payload, apply: true, token: preview.token }));
                        documentData = result.document;
                        host.close();
                        await refreshSymbols();
                        selectFunction(selected);
                        functionSelect.value = String(selected);
                        status.textContent = "Parameters saved. Build the game to check symbols and types.";
                    } catch (error) {
                        message.textContent = `Could not change parameters: ${error.message}`;
                    } finally {
                        host.inert = false;
                    }
                });
                previewHost.append(confirm, back);
                editor.hidden = true;
                previewHost.hidden = false;
            } catch (error) {
                message.textContent = `Could not change parameters: ${error.message}`;
            } finally {
                host.inert = false;
                apply.disabled = false;
                if (!previewHost.hidden) previewHost.querySelector("button.ghost").focus();
            }
        });
        editor.append(heading, hint, resultLabel, rows, add, apply, cancel);
        host.append(editor, previewHost, message);
        button.after(host);
        host.showModal();
    }

    function selectFunction(index) {
        selected = index;
        removeFunction.disabled = index < 0 || !documentData || documentData.name === "contact.gel";
        visual.resetSelection();
        if (index < 0) {
            blocks = []; additions = []; history = null;
            undo.disabled = redo.disabled = save.disabled = true;
            status.textContent = "Choose a function.";
            renderSymbols();
            render();
            return;
        }
        const fn = documentData.functions[index];
        visual.setLocals(fn.locals, fn.localTypes);
        visual.setIndex(visual.buildIndex(symbols));
        blocks = fn.statements.map(item => {
            if (item.insertion) return [];
            const stmt = structuredClone(item.block);
            if (["set", "add", "sub", "assign", "expr", "header"].includes(stmt.op)) stmt.value = VisualGelModel.parseExpr(stmt.value);
            return [stmt];
        });
        additions = [];
        history = createVisualGelHistory(snapshot());
        initial = JSON.stringify(snapshot());
        undo.disabled = redo.disabled = save.disabled = true;
        status.textContent = documentData.name === "contact.gel"
            ? "Generated file: use the contact editor to save changes." : "";
        renderSymbols();
        render();
    }

    function mayDiscard() {
        return !dirty() || window.confirm("Discard unsaved visual GEL edits?");
    }
    async function openFile(name) {
        addFunction.disabled = true;
        removeFunction.disabled = true;
        const data = await request(`/api/gel-files/${encodeURIComponent(name)}`, { cache: "no-store" });
        documentData = data;
        addFunction.disabled = data.name === "contact.gel";
        removeFunction.disabled = true;
        functionSelect.replaceChildren(new Option("Choose a function…", ""));
        data.functions.forEach((fn, i) => functionSelect.add(new Option(fn.name, String(i))));
        selected = -1;
        blocks = []; additions = []; history = null;
        renderSymbols();
        undo.disabled = redo.disabled = save.disabled = true;
        body.replaceChildren(); added.replaceChildren(); closing.replaceChildren();
        status.textContent = data.functions.length ? "Choose a function." : "No functions found in this file.";
    }
    function code(list) {
        const clean = VisualGelModel.validateStatements(list);
        const problem = visual.firstTypeProblem(clean);
        if (problem) throw new Error(problem);
        const lines = [];
        VisualGelModel.emit(clean, 0, lines);
        return lines.join("\n");
    }

    async function changeFunction(payload) {
        const panel = document.getElementById("code-panel");
        panel.inert = true;
        try {
            const data = await request("/api/gel-functions", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ ...payload, name: documentData.name, hash: documentData.hash })
            });
            documentData = data;
            functionSelect.replaceChildren(new Option("Choose a function…", ""));
            data.functions.forEach((fn, i) => functionSelect.add(new Option(fn.name, String(i))));
            selected = -1;
            blocks = []; additions = []; history = null;
            undo.disabled = redo.disabled = save.disabled = true;
            removeFunction.disabled = true;
            await refreshSymbols();
            if (payload.action === "add") {
                const index = data.functions.findIndex(fn => fn.name === payload.function_name);
                selectFunction(index);
                functionSelect.value = String(index);
                status.textContent = "Function created. Edit its parameters and body, then build the game.";
            } else {
                render();
                status.textContent = "Function removed. Build the game to check external references.";
            }
        } catch (error) {
            status.textContent = `Could not ${payload.action} function: ${error.message}`;
        } finally {
            panel.inert = false;
        }
    }

    addFunction.addEventListener("click", () => {
        if (dirty()) {
            status.textContent = "Save or undo function edits before adding a function.";
            return;
        }
        const name = window.prompt("Name of the new function:");
        if (name === null) return;
        changeFunction({ action: "add", function_name: name.trim() });
    });
    removeFunction.addEventListener("click", () => {
        if (dirty()) {
            status.textContent = "Save or undo function edits before removing a function.";
            return;
        }
        const fn = documentData.functions[selected];
        if (!window.confirm(`Are you sure you want to permanently remove ${fn.name}? Its entire body will be deleted.\n\nProject GEL references and contact configuration are checked; external and generated code may also refer to it.`)) return;
        changeFunction({ action: "remove", function: selected });
    });

    file.addEventListener("change", async () => {
        if (!mayDiscard()) { file.value = documentData.name; return; }
        try { await openFile(file.value); }
        catch (error) {
            if (documentData) file.value = documentData.name;
            addFunction.disabled = !documentData || documentData.name === "contact.gel";
            removeFunction.disabled = !documentData || documentData.name === "contact.gel" || selected < 0;
            status.textContent = error.message;
        }
    });
    functionSelect.addEventListener("change", () => {
        if (!mayDiscard()) { functionSelect.value = String(selected); return; }
        selectFunction(functionSelect.value === "" ? -1 : Number(functionSelect.value));
    });
    document.getElementById("reload").addEventListener("click", async () => {
        if (!mayDiscard()) return;
        try { await openFile(file.value); } catch (error) {
            addFunction.disabled = !documentData || documentData.name === "contact.gel";
            removeFunction.disabled = !documentData || documentData.name === "contact.gel" || selected < 0;
            status.textContent = error.message;
        }
    });
    undo.addEventListener("click", () => restore(history.undo()));
    redo.addEventListener("click", () => restore(history.redo()));
    document.addEventListener("keydown", event => {
        if (!history || !(event.ctrlKey || event.metaKey) || event.altKey || event.target.isContentEditable ||
            ["INPUT", "TEXTAREA", "SELECT"].includes(event.target.tagName)) return;
        const key = event.key.toLowerCase();
        if (key === "z" || key === "y") {
            event.preventDefault();
            restore(key === "y" || event.shiftKey ? history.redo() : history.undo());
        }
    });
    save.addEventListener("click", async () => {
        try {
            const original = JSON.parse(initial);
            const fn = documentData.functions[selected];
            const newline = fn.source.includes("\r\n") ? "\r\n" : "\n";
            const changes = blocks.flatMap((list, i) => {
                if (JSON.stringify(list) === JSON.stringify(original.blocks[i])) return [];
                const part = fn.statements[i];
                let result = code(list);
                if (part.insertion && result) {
                    result = newline + result.split("\n").map(line => part.indent + line).join(newline) +
                        newline + part.closing_indent;
                }
                return [{ index: i, code: result }];
            });
            const extra = code(additions);
            const addition = extra ? newline + extra.split("\n").map(line => "    " + line).join(newline) + newline : "";
            const data = await request("/api/gel-files", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: documentData.name, hash: documentData.hash,
                    function: selected, changes, addition })
            });
            documentData = data;
            selectFunction(selected);
            functionSelect.value = String(selected);
            status.textContent = "Saved. Build the game to check symbols and types.";
        } catch (error) { status.textContent = `Could not save: ${error.message}`; }
    });
    window.addEventListener("beforeunload", event => {
        if (dirty()) { event.preventDefault(); event.returnValue = ""; }
    });
    (async () => {
        try {
            const files = await request("/api/gel-files", { cache: "no-store" });
            await refreshSymbols();
            if (!symbols) throw new Error("Project symbols could not be loaded; use Refresh declarations and layout.");
            files.files.forEach(name => file.add(new Option(name, name)));
            if (files.files.length) {
                file.value = files.files.includes("game.gel") ? "game.gel" : files.files[0];
                await openFile(file.value);
            } else status.textContent = "No GEL files found in the project folder.";
        } catch (error) { status.textContent = `Could not open project: ${error.message}`; }
    })();
})();
