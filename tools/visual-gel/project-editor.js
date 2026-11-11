(function () {
    "use strict";
    const file = document.getElementById("file");
    const functionSelect = document.getElementById("function");
    const openProjectButton = document.getElementById("open-project");
    const projectName = document.getElementById("project-name");
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
    let blocks = [], additions = [], commentEdits = {}, activeLocals = [], activeLocalTypes = {}, initial = "", history = null, restoring = false;
    const declarationFile = document.getElementById("declaration-file");
    const declarationList = document.getElementById("declaration-list");
    const declarationStatus = document.getElementById("declaration-status");
    const declarationDialog = document.getElementById("declaration-dialog");
    const declarationError = document.getElementById("declaration-error");
    const tableControls = document.getElementById("table-controls");
    const defineControls = document.getElementById("define-controls");
    const defineName = document.getElementById("define-name");
    const defineValue = document.getElementById("define-value");
    const globalControls = document.getElementById("global-controls");
    const globalType = document.getElementById("global-type");
    const globalName = document.getElementById("global-name");
    const globalValue = document.getElementById("global-value");
    const globalStorage = document.getElementById("global-storage");
    const globalAddress = document.getElementById("global-address");
    const globalMemory = document.getElementById("global-memory");
    const globalAddressStatus = document.getElementById("global-address-status");
    const globalMemoryRanges = document.getElementById("global-memory-ranges");
    let declarationData = null, declarationEdit = null, declarationLoad = 0, tableModel = null, defineModel = null, globalModel = null;

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
        if (!(response.headers.get("content-type") || "").includes("application/json"))
            throw new Error(`The editor server returned an unexpected response (HTTP ${response.status}). Stop and restart the GEL editor server, then reload this page.`);
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        return data;
    }

    function renderDeclarations() {
        declarationList.replaceChildren();
        const readonly = !declarationData || declarationData.name === "contact.gel";
        for (const kind of ["define", "global", "table"])
            document.getElementById(`add-${kind}`).disabled = readonly;
        if (!declarationData) return;
        if (readonly) declarationStatus.textContent = "Generated contact.gel is read-only.";
        if (!declarationData.declarations.length) {
            declarationList.append(el("p", "", "No editable declarations found in this file."));
            return;
        }
        declarationData.declarations.forEach((entry, index) => {
            const row = el("div", "declaration-row");
            let globalTypeName = "";
            if (entry.kind === "global") {
                try { globalTypeName = VisualGelGlobalEditor.create(entry.code).type; }
                catch (error) { declarationStatus.textContent = `Cannot colour ${entry.name}: ${error.message}`; }
            }
            const title = el("strong", entry.kind === "define" ? "t-define" : entry.kind === "table" ? "t-table" :
                globalTypeName ? `t-${globalTypeName}` : "",
                `${entry.kind}: ${entry.name}`);
            const code = el("code");
            visual.highlight(entry.code, code);
            if (globalTypeName) {
                for (const token of code.children)
                    if (token.textContent === entry.name)
                        token.className = `t-${globalTypeName}`;
            }
            row.append(title, code);
            const button = el("button", "", "Edit");
            button.type = "button";
            button.disabled = readonly;
            button.setAttribute("aria-label", `Edit ${entry.kind} ${entry.name}`);
            button.addEventListener("click", () => editDeclaration(entry.kind, index));
            row.append(button);
            if (entry.kind === "global" || entry.kind === "define")
                row.append(usageButton(entry.kind, entry.name), removalButton(entry.kind, entry.name, index));
            if (entry.kind === "table") {
                try {
                    for (const field of VisualGelTableEditor.create(entry.code).fields)
                        row.append(usageButton("field", field.name, entry.name));
                } catch (error) {
                    declarationStatus.textContent = `Cannot list fields of ${entry.name}: ${error.message}`;
                }
            }
            declarationList.append(row);
        });
    }

    async function loadDeclarations(name) {
        const load = ++declarationLoad;
        declarationData = null;
        renderDeclarations();
        if (!name) return;
        declarationStatus.textContent = "Loading declarations...";
        const data = await request(`/api/gel-declarations/${encodeURIComponent(name)}`, { cache: "no-store" });
        if (load !== declarationLoad) return;
        declarationData = data;
        declarationStatus.textContent = "";
        renderDeclarations();
    }

    function editDeclaration(kind, index = null) {
        if (!declarationData || declarationData.name === "contact.gel") return;
        declarationEdit = { kind, index };
        const templates = {
            define: "#define NAME 1",
            global: "u8 name = 0;",
            table: "struct_table Name[16] {\n    u8 field;\n}"
        };
        const code = index === null ? templates[kind] : declarationData.declarations[index].code;
        tableModel = null;
        defineModel = null;
        globalModel = null;
        if (kind === "define") {
            try { defineModel = VisualGelDefineEditor.create(code); }
            catch (error) {
                declarationStatus.textContent = error.message;
                return;
            }
            defineName.value = defineModel.name;
            defineValue.value = defineModel.value;
        }
        if (kind === "global") {
            try { globalModel = VisualGelGlobalEditor.create(code); }
            catch (error) {
                declarationStatus.textContent = error.message;
                return;
            }
            globalType.value = globalModel.type;
            globalName.value = globalModel.name;
            globalValue.value = globalModel.value;
            globalStorage.value = globalModel.storage;
            globalAddress.value = globalModel.address;
            colorGlobal();
        }
        if (kind === "table") {
            try { tableModel = VisualGelTableEditor.create(code); }
            catch (error) {
                declarationStatus.textContent = error.message;
                return;
            }
            renderTableEditor();
        }
        tableControls.hidden = kind !== "table";
        defineControls.hidden = kind !== "define";
        globalControls.hidden = kind !== "global";
        document.getElementById("global-storage-hint").hidden = kind !== "global";
        globalMemory.hidden = kind !== "global";
        if (kind === "global") renderGlobalMemory();
        declarationError.textContent = "";
        document.getElementById("declaration-dialog-title").textContent =
            `${index === null ? "Add" : "Edit"} ${kind === "table" ? "struct table" : kind}`;
        declarationDialog.showModal();
        (kind === "table" ? tableControls.querySelector("input") : kind === "define" ? defineValue : globalName).focus();
    }

    function colorGlobal() {
        globalType.className = globalName.className = `t-${globalType.value}`;
    }
    globalType.addEventListener("change", colorGlobal);

    function usageButton(kind, name, table = null) {
        const label = table ? `${table}.${name}` : name;
        const button = el("button", "ghost", "Find uses"); button.type = "button";
        button.setAttribute("aria-label", `Find uses of ${label}`);
        button.addEventListener("click", () => showUsages(kind, name, table));
        return button;
    }

    function removalButton(kind, name, index, table = null) {
        const label = table ? `${table}.${name}` : name;
        const button = el("button", "ghost", "Remove"); button.type = "button";
        button.disabled = kind === "local" ? documentData && documentData.name === "contact.gel"
            : declarationData && declarationData.name === "contact.gel";
        button.setAttribute("aria-label", `Remove ${label}`);
        button.addEventListener("click", () => removeVariable(kind, name, index, table));
        return button;
    }

    async function removeVariable(kind, name, index, table = null) {
        if (dirty()) {
            declarationStatus.textContent = symbolStatus.textContent = "Save or undo pending function edits before removing variables.";
            return;
        }
        const label = table ? `${table}.${name}` : name;
        const payload = kind === "local"
            ? { kind, symbol: name, name: documentData.name, hash: documentData.hash, function: selected }
            : { kind, symbol: name, table, index, name: declarationData.name, hash: declarationData.hash };
        const options = data => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
        let applied = false;
        try {
            const preview = await request("/api/gel-variable-removal", options(payload));
            const accepted = await confirmDeclarationPreview({ ...preview, type_changes: [], references: [],
                removal: label });
            if (!accepted) return;
            await request("/api/gel-variable-removal", options({ ...payload, token: preview.token, apply: true }));
            applied = true;
            if (declarationDialog.open) declarationDialog.close();
            if (declarationData) await loadDeclarations(declarationData.name);
            if (documentData && preview.files.includes(documentData.name)) {
                const oldIndex = selected;
                await openFile(documentData.name);
                if (oldIndex >= 0) { selectFunction(oldIndex); functionSelect.value = String(oldIndex); }
            }
            await refreshSymbols();
            declarationStatus.textContent = symbolStatus.textContent = `Removed ${label}. Build and review the game.`;
        } catch (error) {
            const message = applied ? `Removed ${label}, but refresh failed: ${error.message}`
                : `Could not remove ${label}: ${error.message}`;
            declarationStatus.textContent = symbolStatus.textContent = message;
            if (declarationDialog.open) declarationError.textContent = message;
        }
    }

    async function showUsages(kind, name, table = null) {
        if (dirty()) {
            const message = "Save or undo pending function edits before searching saved source.";
            status.textContent = message;
            symbolStatus.textContent = message;
            declarationStatus.textContent = message;
            return;
        }
        const dialog = el("dialog", "parameter-editor declaration-preview");
        const label = table ? `${table}.${name}` : name;
        dialog.setAttribute("aria-label", `Uses of ${label}`);
        dialog.append(el("h3", "", `Uses of ${label}`));
        const message = el("p", "", "Searching saved project source...");
        message.setAttribute("role", "status");
        const results = el("ol", "usage-results");
        const close = el("button", "ghost", "Close"); close.type = "button";
        close.addEventListener("click", () => dialog.close());
        dialog.append(message, results, close);
        dialog.addEventListener("close", () => dialog.remove());
        document.body.append(dialog);
        dialog.showModal();
        const query = { kind, symbol: name, table };
        if (kind === "local") Object.assign(query, { file: documentData.name, hash: documentData.hash, function: selected });
        try {
            const response = await request("/api/gel-usages", {
                method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(query)
            });
            if (!dialog.open) return;
            message.textContent = `${response.results.length} possible matches in saved ${response.scope} source. ${response.warning}`;
            for (const result of response.results) {
                const item = el("li");
                item.append(el("strong", "", `${result.file}:${result.line}:${result.column}${result.functionName ? ` · ${result.functionName}` : ""}`),
                    el("code", "", result.text));
                if (result.function !== null) {
                    const jump = el("button", "ghost", result.part === null ? "Open function" : "Jump to block");
                    jump.type = "button";
                    jump.addEventListener("click", async () => {
                        if (!mayDiscard()) return;
                        jump.disabled = true;
                        try {
                            await openFile(result.file, result.hash);
                            file.value = result.file;
                            selectFunction(result.function);
                            functionSelect.value = String(result.function);
                            selectTab(tabs[0]);
                            dialog.close();
                            const target = result.part === null ? body.querySelector(".function-signature") :
                                body.querySelector(`[data-source-part="${result.part}"]`);
                            if (target) {
                                target.classList.add("usage-target");
                                target.scrollIntoView({ block: "center", behavior: "smooth" });
                                setTimeout(() => target.classList.remove("usage-target"), 4000);
                            }
                            status.textContent = result.part === null
                                ? `${result.file}:${result.line} opened in function ${result.functionName}; this use is not in an editable block.`
                                : `${result.file}:${result.line} opened.`;
                        } catch (error) {
                            message.textContent = `Could not jump: ${error.message}`;
                            jump.disabled = false;
                        }
                    });
                    item.append(jump);
                }
                results.append(item);
            }
        } catch (error) {
            message.textContent = `Could not find uses: ${error.message}`;
        }
    }

    function renderGlobalMemory() {
        globalMemoryRanges.replaceChildren();
        if (!globalModel) return;
        if (globalStorage.value === "persist") {
            globalAddressStatus.textContent = "Persist addresses are assigned by the compiler; fixed addresses are not supported.";
            return;
        }
        if (globalStorage.value === "mmio") {
            globalAddressStatus.textContent = "MMIO addresses select hardware registers, not free WRAM. Consult the hardware register map.";
            return;
        }
        if (!memory || !memory.available) {
            globalAddressStatus.textContent = "No compiled layout available. Build the game, then refresh declarations and layout.";
            return;
        }
        let width, ranges;
        try {
            width = VisualGelMemoryLayout.typeSize(globalType.value);
            ranges = VisualGelMemoryLayout.addressRanges(memory);
        } catch (error) {
            globalAddressStatus.textContent = `Cannot display the memory layout: ${error.message}`;
            return;
        }
        const format = address => `$${address.toString(16).toUpperCase().padStart(4, "0")}`;
        const value = globalAddress.value.trim();
        if (!value) {
            globalAddressStatus.textContent = `No fixed address selected. The compiler allocates ${width} byte${width === 1 ? "" : "s"} automatically.`;
        } else if (!/^(?:0x[0-9a-f]+|[0-9]+)$/i.test(value)) {
            globalAddressStatus.textContent = "This address is a macro or invalid number; its occupancy cannot be checked here.";
        } else {
            const address = Number(value);
            if (!Number.isSafeInteger(address)) {
                globalAddressStatus.textContent = "Address is too large to check.";
            } else {
                const state = VisualGelMemoryLayout.addressStatus(ranges, address, width);
                globalAddressStatus.textContent = state === "outside"
                    ? "This address range lies outside the reported low WRAM window ($0000–$1FFF)."
                    : state === "reserved"
                        ? "This address overlaps a known scratch, sound-driver or stack reservation."
                    : state === "occupied"
                        ? "This address overlaps a compiler-reported allocation. The current variable, if already built, is included."
                        : "No compiler-reported allocation overlaps this address, but it is not guaranteed free.";
            }
        }
        function addRanges(category) {
            const list = ranges.filter(range => range.category === category &&
                (category !== "unreported" || range.end - range.start + 1 >= width));
            const group = el("details");
            const title = { unreported: "Unreported (not guaranteed free)", occupied: "Reported occupied", reserved: "Known reserved" }[category];
            group.append(el("summary", "", `${title} ranges: ${list.length}`));
            if (!list.length) group.append(el("p", "", category !== "unreported" ? "None reported." : `No unreported range fits ${width} bytes.`));
            else {
                const items = el("ul", "address-range-list");
                for (const range of list) {
                    const item = el("li", "", `${format(range.start)}–${format(range.end)} (${range.end - range.start + 1} bytes)`);
                    if (category === "unreported") {
                        const use = el("button", "ghost", "Use start"); use.type = "button";
                        use.setAttribute("aria-label", `Use unreported address ${format(range.start)}`);
                        use.addEventListener("click", () => {
                            globalAddress.value = `0x${range.start.toString(16).toUpperCase().padStart(4, "0")}`;
                            renderGlobalMemory();
                            globalAddress.focus();
                        });
                        item.append(use);
                    }
                    items.append(item);
                }
                group.append(items);
            }
            globalMemoryRanges.append(group);
        }
        addRanges("unreported");
        addRanges("occupied");
        addRanges("reserved");
    }
    globalType.addEventListener("change", renderGlobalMemory);
    globalStorage.addEventListener("change", renderGlobalMemory);
    globalAddress.addEventListener("input", renderGlobalMemory);

    function confirmDeclarationPreview(preview) {
        return new Promise(resolve => {
            const dialog = el("dialog", "parameter-editor declaration-preview");
            dialog.setAttribute("aria-label", "Confirm declaration changes");
            dialog.append(el("h3", "", "Review and confirm declaration changes"));
            if (preview.removal) dialog.append(el("p", "", `Remove ${preview.removal}? Reads will become 0; write statements will be removed, including their side effects. Review every change and build the game.`));
            const types = preview.type_changes.map(item => `${item.name}: ${item.before} → ${item.after}`);
            if (types.length) dialog.append(el("p", "", `Type changes: ${types.join(", ")}`));
            dialog.append(el("p", "", "Files to modify: " + preview.files.join(", ")));
            for (const [name, lines] of Object.entries(preview.diffs)) {
                const details = el("details");
                details.open = true;
                details.append(el("summary", "", name), el("pre", "", lines.join("\n")));
                dialog.append(details);
            }
            if (!preview.removal) dialog.append(el("h4", "", "Possible references to review"),
                el("p", "", "These are possible matches, not a complete semantic analysis. Type changes do not rewrite uses; check them and build the game. Generated, included and external files may need manual changes."));
            if (!preview.removal && preview.references.length) {
                const list = el("ul");
                for (const reference of preview.references)
                    list.append(el("li", "", `${reference.file}:${reference.line}: ${reference.text}`));
                dialog.append(list);
            } else if (!preview.removal) dialog.append(el("p", "", "No matching references found in project-root GEL files or contact configuration."));
            const confirm = el("button", "", "Confirm and save"); confirm.type = "button";
            const cancel = el("button", "ghost", "Cancel"); cancel.type = "button";
            confirm.addEventListener("click", () => dialog.close("confirm"));
            cancel.addEventListener("click", () => dialog.close("cancel"));
            dialog.append(confirm, cancel);
            dialog.addEventListener("close", () => {
                const accepted = dialog.returnValue === "confirm";
                dialog.remove();
                resolve(accepted);
            });
            document.body.append(dialog);
            dialog.showModal();
            cancel.focus();
        });
    }

    function renderTableEditor() {
        tableControls.replaceChildren();
        const header = el("div", "table-controls");
        function input(label, value, css, update) {
            const wrapper = el("label", "", label);
            const control = el("input", css);
            control.value = value;
            control.addEventListener("input", () => update(control.value));
            wrapper.append(control);
            header.append(wrapper);
        }
        input("Table name", tableModel.name, "t-table", value => { tableModel.name = value; });
        input("Slots", tableModel.size, "", value => { tableModel.size = value; });
        input("Address (optional)", tableModel.address, "", value => { tableModel.address = value; });
        const interleavedLabel = el("label", "", "Interleaved");
        const interleaved = el("input"); interleaved.type = "checkbox"; interleaved.checked = tableModel.interleaved;
        interleaved.addEventListener("change", () => { tableModel.interleaved = interleaved.checked; });
        interleavedLabel.append(interleaved); header.append(interleavedLabel);
        tableControls.append(header, el("h4", "", "Fields"));
        const fields = el("div", "table-fields");
        tableModel.fields.forEach((field, index) => {
            const row = el("div", "table-controls block");
            const typeLabel = el("label", "", "Type");
            const type = el("select");
            for (const value of ["u8", "i8", "u16", "i16", "f16", "f32"]) {
                const option = el("option", `t-${value}`, value); option.value = value; type.append(option);
            }
            type.value = field.type;
            const nameLabel = el("label", "", "Field name");
            const name = el("input"); name.value = field.name;
            const color = () => { type.className = name.className = `t-${field.type}`; };
            color();
            type.addEventListener("change", () => { field.type = type.value; color(); });
            name.addEventListener("input", () => { field.name = name.value; });
            typeLabel.append(type); nameLabel.append(name);
            const remove = el("button", "ghost", "Remove"); remove.type = "button";
            remove.setAttribute("aria-label", `Remove field ${field.name || index + 1}`);
            remove.addEventListener("click", () => {
                if (field.origin === null) { tableModel.fields.splice(index, 1); renderTableEditor(); return; }
                if (VisualGelTableEditor.emit(tableModel) !== tableModel.code) {
                    declarationError.textContent = "Save or undo table edits before removing an existing field.";
                    return;
                }
                removeVariable("field", field.name, declarationEdit.index, tableModel.originalName);
            });
            row.append(typeLabel, nameLabel, remove); fields.append(row);
        });
        const add = el("button", "ghost", "+ Field"); add.type = "button";
        add.addEventListener("click", () => {
            tableModel.fields.push({ type: "u8", name: "", origin: null });
            renderTableEditor();
        });
        tableControls.append(fields, add);
    }

    for (const kind of ["define", "global", "table"])
        document.getElementById(`add-${kind}`).addEventListener("click", () => editDeclaration(kind));
    document.getElementById("declaration-cancel").addEventListener("click", () => declarationDialog.close());
    declarationFile.addEventListener("change", async () => {
        try { await loadDeclarations(declarationFile.value); }
        catch (error) { declarationStatus.textContent = `Could not load declarations: ${error.message}`; }
    });
    document.getElementById("declaration-reload").addEventListener("click", async () => {
        try { await loadDeclarations(declarationFile.value); }
        catch (error) { declarationStatus.textContent = `Could not load declarations: ${error.message}`; }
    });
    document.getElementById("declaration-form").addEventListener("submit", async event => {
        event.preventDefault();
        if (dirty()) {
            declarationError.textContent = "Save or undo pending function edits before changing declarations.";
            return;
        }
        const form = event.currentTarget;
        form.inert = true;
        try {
            if (defineModel) { defineModel.name = defineName.value; defineModel.value = defineValue.value; }
            if (globalModel) {
                globalModel.type = globalType.value;
                globalModel.name = globalName.value;
                globalModel.value = globalValue.value;
                globalModel.storage = globalStorage.value;
                globalModel.address = globalAddress.value;
            }
            const code = tableModel ? VisualGelTableEditor.emit(tableModel)
                : defineModel ? VisualGelDefineEditor.emit(defineModel) : VisualGelGlobalEditor.emit(globalModel);
            const entry = declarationEdit.index === null ? null : declarationData.declarations[declarationEdit.index];
            const newName = tableModel ? tableModel.name : defineModel ? defineModel.name : globalModel.name;
            const fieldRenames = tableModel ? tableModel.fields.filter(field => field.origin !== null &&
                field.name !== tableModel.originalFields[field.origin].name).map(field => ({ origin: field.origin, name: field.name })) : [];
            const fieldOrigins = tableModel ? tableModel.fields.filter(field => field.origin !== null)
                .map(field => ({ origin: field.origin, name: field.name })) : [];
            const fieldTypeChanged = tableModel && tableModel.fields.some(field => field.origin !== null &&
                field.type !== tableModel.originalFields[field.origin].type);
            const globalTypeChanged = globalModel && globalModel.type !== globalModel.originalType;
            const payload = { ...declarationEdit, name: declarationData.name,
                hash: declarationData.hash, code, fieldRenames, fieldOrigins };
            const options = data => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });
            let changedFiles = [declarationData.name];
            if (entry && (entry.name !== newName || fieldRenames.length || fieldTypeChanged || globalTypeChanged)) {
                const preview = await request("/api/gel-declaration-rename", options(payload));
                if (!await confirmDeclarationPreview(preview)) return;
                await request("/api/gel-declaration-rename", options({ ...payload, apply: true, token: preview.token }));
                changedFiles = preview.files;
                await loadDeclarations(declarationData.name);
            } else {
                declarationData = await request("/api/gel-declarations", options(payload));
            }
            declarationDialog.close();
            declarationStatus.textContent = "Saved. Build the game to check references, types and layout.";
            renderDeclarations();
            if (documentData && changedFiles.includes(documentData.name)) {
                const index = selected;
                await openFile(documentData.name);
                if (index >= 0) {
                    selectFunction(index);
                    functionSelect.value = String(index);
                }
            }
            await refreshSymbols();
        } catch (error) {
            if (declarationDialog.open) declarationError.textContent = `Could not save: ${error.message}`;
            else declarationStatus.textContent = `Declaration saved, but refresh failed: ${error.message}`;
        } finally {
            form.inert = false;
        }
    });

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
                else if (row.globalType) {
                    const code = el("code");
                    code.append(el("span", `t-${row.globalType}`, row.globalType), document.createTextNode(" "),
                        el("span", symbols.defines.includes(row.name) ? "t-define" : `t-${row.globalType}`, row.name));
                    card.append(code);
                }
                else if (row.usageKind) {
                    const code = el("code"); visual.highlight(row.text, code); card.append(code);
                }
                else {
                    card.append(el("strong", "t-table", row.name), el("span", "symbol-detail", row.detail));
                    if (row.fields && row.fields.length) {
                        const table = el("table", "symbol-fields");
                        const header = el("tr");
                        for (const label of ["Field", "Type", "Compiled address", "Stride", "Uses"]) header.append(el("th", "", label));
                        const thead = el("thead"); thead.append(header); table.append(thead);
                        const tbody = el("tbody");
                        for (const field of row.fields) {
                            const tr = el("tr");
                            for (const cell of field) {
                                const td = el("td"); visual.highlight(cell, td); tr.append(td);
                            }
                            const actions = el("td");
                            actions.append(usageButton("field", field[0], row.name));
                            tr.append(actions);
                            tbody.append(tr);
                        }
                        table.append(tbody); card.append(table);
                    }
                }
                if (row.usageKind) {
                    card.append(usageButton(row.usageKind, row.name));
                    if (row.usageKind === "local") card.append(removalButton("local", row.name));
                }
                else if (row.globalType) card.append(usageButton("global", row.name));
                list.append(card);
            }
            group.append(list);
            symbolList.append(group);
        }
        section("Defines (text substitutions; not memory allocations)",
            symbols.defines.filter(matches).map(name => ({ name, usageKind: "define",
                text: `${name} = ${symbols.defineValues[name] || "(no value)"}` })));
        section("Declared globals",
            Object.entries(symbols.globals).filter(([name]) => matches(name))
                .map(([name, type]) => ({ name, globalType: type })));
        if (selected >= 0) section("Locals and parameters in selected function",
            activeLocals.filter(matches).map(name => ({ name, usageKind: "local", text: name })));
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
        symbols = null;
        memory = null;
        memoryEntries = [];
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
            visual.setIndex(visual.buildIndex(symbols));
            renderSymbols();
            renderMemory();
            if (declarationDialog.open && globalModel) renderGlobalMemory();
        } catch (error) {
            symbolStatus.textContent = `Could not load declarations and memory layout: ${error.message}`;
            if (declarationDialog.open && globalModel) renderGlobalMemory();
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

    async function loadProjectState(project) {
        declarationLoad++;
        declarationData = null;
        renderDeclarations();
        projectName.textContent = `${project.name} — ${project.path}`;
        projectName.title = project.path;
        file.replaceChildren();
        functionSelect.replaceChildren(new Option("Choose a function…", ""));
        documentData = null;
        selected = -1;
        blocks = []; additions = []; history = null;
        activeLocals = []; activeLocalTypes = {};
        visual.resetSelection();
        visual.setLocals(activeLocals, activeLocalTypes);
        visual.setIndex(visual.buildIndex({}));
        symbols = null; memory = null;
        body.replaceChildren(); added.replaceChildren(); closing.replaceChildren();
        addFunction.disabled = true;
        removeFunction.disabled = true;
        save.disabled = true;
        await refreshSymbols();
        if (!symbols) throw new Error("Project symbols could not be loaded; use Refresh declarations and layout.");
        const files = await request("/api/gel-files", { cache: "no-store" });
        files.files.forEach(name => file.add(new Option(name, name)));
        declarationFile.replaceChildren();
        files.files.forEach(name => declarationFile.add(new Option(name, name)));
        if (files.files.length) {
            file.value = files.files.includes("game.gel") ? "game.gel" : files.files[0];
            await openFile(file.value);
            declarationFile.value = file.value;
            await loadDeclarations(declarationFile.value);
        } else {
            declarationData = null;
            renderDeclarations();
            status.textContent = "No GEL files found in the project folder.";
        }
        if (project.warning) status.textContent = project.warning;
    }

    openProjectButton.addEventListener("click", async () => {
        if (!mayDiscard()) return;
        openProjectButton.disabled = true;
        try {
            const project = await request("/api/project/open", { method: "POST" });
            if (project.cancelled) return;
            await loadProjectState(project);
        } catch (error) {
            status.textContent = `Could not open project: ${error.message}`;
        } finally {
            openProjectButton.disabled = false;
        }
    });

    function snapshot() { return { blocks, additions, comments: commentEdits, locals: activeLocals, localTypes: activeLocalTypes }; }
    function dirty() { return history && JSON.stringify(snapshot()) !== initial; }
    function changed(rebuild) {
        if (!restoring) history.record(snapshot());
        undo.disabled = !history.canUndo;
        redo.disabled = !history.canRedo;
        save.disabled = documentData.name === "contact.gel" || !dirty();
        if (rebuild) render();
    }
    function renameLocal(oldName, newName) {
        for (const list of blocks) VisualGelModel.renameVariableReferences(list, oldName, newName);
        VisualGelModel.renameVariableReferences(additions, oldName, newName);
        const index = activeLocals.indexOf(oldName);
        if (index >= 0) activeLocals[index] = newName;
        if (Object.prototype.hasOwnProperty.call(activeLocalTypes, oldName)) {
            activeLocalTypes[newName] = activeLocalTypes[oldName];
            delete activeLocalTypes[oldName];
        }
        visual.setLocals(activeLocals, activeLocalTypes);
        visual.setIndex(visual.buildIndex(symbols));
        renderSymbols();
    }
    visual.setLocalRenameHandler(renameLocal);
    function restore(value) {
        if (!value) return;
        blocks = value.blocks;
        additions = value.additions;
        commentEdits = value.comments || {};
        activeLocals = value.locals || [];
        activeLocalTypes = value.localTypes || {};
        visual.setLocals(activeLocals, activeLocalTypes);
        visual.setIndex(visual.buildIndex(symbols));
        visual.resetSelection();
        renderSymbols();
        restoring = true;
        try { changed(true); } finally { restoring = false; }
    }
    function raw(text, container = body) {
        if (!text.trim()) return;
        const commentLine = /^\s*(\/\/.*|\/\*.*|\*.*|.*\*\/)$/;
        if (text.split(/\r?\n/).every(line => !line.trim() || commentLine.test(line))) {
            const note = el("div", "source-comment", text.split(/\r?\n/).map(line => line.trim()).filter(Boolean).join("\n"));
            note.setAttribute("aria-label", "Comment");
            container.append(note);
            return;
        }
        const pre = el("pre", "preserved-source", text);
        pre.setAttribute("aria-label", "Preserved GEL source");
        container.append(pre);
    }
    function commentBody(source) {
        return source.startsWith("//") ? source.slice(2).trim() : source.slice(2, -2).trim();
    }
    function renderComment(index, container, fn) {
        if (commentEdits[index] === null) return;
        const original = fn.comments[index].text;
        const line = original.startsWith("//");
        const value = index in commentEdits ? commentEdits[index] : commentBody(original);
        const row = el("div", "source-comment");
        row.append(el("span", "comment-mark", line ? "//" : "/*"));
        const field = el(line ? "input" : "textarea", "comment-input");
        field.value = value;
        field.setAttribute("aria-label", "Comment");
        if (!line) field.rows = Math.max(1, value.split("\n").length);
        field.addEventListener("change", () => {
            const next = line ? field.value.replace(/[\r\n]+/g, " ") : field.value;
            if (next.includes("*/") && !line) { status.textContent = "A comment cannot contain */."; field.value = value; return; }
            if (next === commentBody(original)) delete commentEdits[index]; else commentEdits[index] = next;
            changed(true);
        });
        const remove = el("button", "icon tiny", "✕"); remove.type = "button"; remove.title = "Delete this comment";
        remove.addEventListener("click", () => { commentEdits[index] = null; changed(true); });
        row.append(field, remove);
        container.append(row);
    }
    function movable() { return documentData && documentData.name !== "contact.gel" && !(documentData.functions[selected] || {}).error; }
    function renderLayout(items, container, fn, blockId = null) {
        const zones = blockId !== null && blockId !== undefined && movable();
        for (const item of items) {
            if (zones && item.unit !== undefined) container.append(visual.sourceZone(blockId, item.unit));
            if (item.kind === "comment") {
                renderComment(item.index, container, fn);
            } else if (item.kind === "raw") {
                if (zones && item.unit !== undefined) {
                    const wrap = el("div", "block existing-raw");
                    wrap.append(visual.sourceHandle(item.unit, wrap));
                    raw(item.text, wrap);
                    container.append(wrap);
                } else raw(item.text, container);
            } else if (item.kind === "label") {
                container.append(el("div", "t-ctl else-label", item.text));
            } else if (item.kind === "part") {
                const statement = fn.statements[item.index];
                const host = el("div", "editable-statement");
                host.dataset.sourcePart = String(item.index);
                renderBlocks(blocks[item.index], host, changed, statement.fixed, undefined, zones && item.unit !== undefined ? item.unit : null);
                container.append(host);
                if (statement.insertion) {
                    container.append(el("div", "insertion-label", statement.label), addBar(blocks[item.index], changed));
                }
            } else if (item.kind === "control") {
                const control = el("div", `block block-${item.style} existing-control`);
                if (zones && item.unit !== undefined) {
                    control.append(visual.sourceHandle(item.unit, control));
                    control.append(removeButton("Remove this block and everything inside it", () => removeCode({ units: [item.unit] }, "this block and everything inside it")));
                }
                for (const branch of item.branches) {
                    const section = el("div", "existing-branch");
                    if (zones && item.style === "cases" && branch.block !== undefined && branch.block !== null) {
                        section.append(removeButton("Remove this case and its code", () => removeCode({ case: branch.block }, "this case and its code")));
                    }
                    renderLayout(branch.items, section, fn, null);
                    const nested = el("div", "block-body");
                    renderLayout(branch.body, nested, fn, branch.block ?? null);
                    section.append(nested);
                    control.append(section);
                }
                container.append(control);
            }
        }
        if (zones) container.append(visual.sourceZone(blockId, null));
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
        renderLayout(fn.display, body, fn, fn.rootBlock ?? null);
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

    async function moveRequest(ids, extra) {
        if (dirty()) throw new Error("Save or undo edits before moving existing code.");
        return request("/api/gel-moves", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ name: documentData.name, hash: documentData.hash, function: selected, units: ids, ...extra })
        });
    }
    async function removeCode(what, label, confirmed = false) {
        if (dirty()) { status.textContent = "Save or undo edits before removing existing code."; return; }
        if (!confirmed && !window.confirm(`Remove ${label}?`)) return;
        try {
            const data = await request("/api/gel-moves", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: documentData.name, hash: documentData.hash, function: selected, delete: true, ...what })
            });
            const index = selected;
            documentData = data;
            selectFunction(index);
            functionSelect.value = String(index);
            status.textContent = "Removed.";
        } catch (error) { status.textContent = error.message; }
    }
    function removeButton(title, fn) {
        const button = el("button", "icon tiny remove-existing", "✕");
        button.type = "button"; button.title = title; button.setAttribute("aria-label", title);
        button.addEventListener("click", fn);
        return button;
    }
    const clipboardList = document.getElementById("clipboard-list");
    const clipboardClear = document.getElementById("clipboard-clear");
    let clipboard = [];
    let clipboardId = 0;
    try {
        clipboard = JSON.parse(sessionStorage.getItem("visual-gel-clipboard") || "[]")
            .filter(entry => entry && Array.isArray(entry.lines) && entry.lines.every(line => typeof line === "string"));
        clipboardId = clipboard.reduce((top, entry) => Math.max(top, entry.id || 0), 0);
    } catch (error) { clipboard = []; }
    function renderClipboard() {
        try { sessionStorage.setItem("visual-gel-clipboard", JSON.stringify(clipboard)); } catch (error) { /* storage unavailable */ }
        clipboardClear.disabled = !clipboard.length;
        clipboardList.replaceChildren();
        if (!clipboard.length) { clipboardList.append(el("p", "", "The clipboard is empty.")); return; }
        for (const entry of clipboard) {
            const row = el("div", "clipboard-entry");
            row.append(el("strong", "", `${entry.count} statement${entry.count === 1 ? "" : "s"}`));
            row.append(el("pre", "preserved-source", entry.lines.join("\n")));
            const paste = el("button", "", "Paste"); paste.type = "button";
            paste.title = "Paste before the single ticked statement, or at the end of the function";
            paste.addEventListener("click", () => pasteEntry(entry));
            const remove = el("button", "danger", "Remove"); remove.type = "button";
            remove.title = "Remove this entry from the clipboard";
            remove.addEventListener("click", () => {
                clipboard = clipboard.filter(item => item !== entry);
                renderClipboard();
            });
            row.append(paste, remove);
            clipboardList.append(row);
        }
    }
    clipboardClear.addEventListener("click", () => { clipboard = []; renderClipboard(); });
    async function copyToClipboard(ids, cut) {
        try {
            const data = await moveRequest(ids, { copy: true });
            clipboard.push({ id: ++clipboardId, count: data.count, lines: data.lines });
            renderClipboard();
            if (cut) await removeCode({ units: ids }, "", true);
            if (cut && status.textContent === "Removed.") status.textContent = "Cut to the clipboard.";
            else if (!cut) status.textContent = "Copied to the clipboard.";
        } catch (error) { status.textContent = error.message; }
    }
    async function pasteEntry(entry) {
        if (selected < 0 || !documentData || !movable()) { status.textContent = "Open an editable function to paste into."; return; }
        if (dirty()) { status.textContent = "Save or undo edits before pasting."; return; }
        const picked = visual.selectedUnits();
        if (picked.length > 1) { status.textContent = "Tick only one statement (paste goes before it), or none to paste at the end."; return; }
        const root = documentData.functions[selected].rootBlock;
        const target = picked.length ? { before: picked[0] } : { block: root, before: null };
        try {
            const data = await request("/api/gel-moves", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: documentData.name, hash: documentData.hash, function: selected, paste: true, lines: entry.lines, target })
            });
            const index = selected;
            documentData = data;
            selectFunction(index);
            functionSelect.value = String(index);
            status.textContent = "Pasted. Build the game to check symbols and types.";
        } catch (error) { status.textContent = error.message; }
    }
    renderClipboard();

    visual.setSourceMover({
        copy: copyToClipboard,
        remove: ids => removeCode({ units: ids }, `${ids.length} selected statement${ids.length === 1 ? "" : "s"}`),
        preview: ids => moveRequest(ids, { preview: true }),
        async apply(ids, target) {
            const data = await moveRequest(ids, { target });
            const index = selected;
            documentData = data;
            selectFunction(index);
            functionSelect.value = String(index);
            status.textContent = "Moved.";
        }
    });

    function selectFunction(index) {        selected = index;
        removeFunction.disabled = index < 0 || !documentData || documentData.name === "contact.gel";
        visual.resetSelection();
        if (index < 0) {
            blocks = []; additions = []; history = null;
            activeLocals = []; activeLocalTypes = {};
            visual.setLocals(activeLocals, activeLocalTypes);
            visual.setIndex(visual.buildIndex(symbols || {}));
            undo.disabled = redo.disabled = save.disabled = true;
            status.textContent = "Choose a function.";
            renderSymbols();
            render();
            return;
        }
        const fn = documentData.functions[index];
        activeLocals = [...fn.locals];
        activeLocalTypes = { ...fn.localTypes };
        visual.setLocals(activeLocals, activeLocalTypes);
        visual.setIndex(visual.buildIndex(symbols));
        blocks = fn.statements.map(item => {
            if (item.insertion) return [];
            const stmt = structuredClone(item.block);
            if (["set", "add", "sub", "assign", "expr", "header"].includes(stmt.op)) stmt.value = VisualGelModel.parseExpr(stmt.value);
            return [stmt];
        });
        additions = [];
        commentEdits = {};
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
    async function openFile(name, expectedHash = null) {
        const data = await request(`/api/gel-files/${encodeURIComponent(name)}`, { cache: "no-store" });
        if (expectedHash !== null && data.hash !== expectedHash)
            throw new Error("File changed since the search. Search again.");
        addFunction.disabled = true;
        removeFunction.disabled = true;
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
            const comment_changes = Object.entries(commentEdits).map(([index, text]) => ({ index: Number(index), text }));
            const data = await request("/api/gel-files", {
                method: "POST", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ name: documentData.name, hash: documentData.hash,
                    function: selected, changes, addition, comment_changes })
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
            const project = await request("/api/project", { cache: "no-store" });
            await loadProjectState(project);
        } catch (error) { status.textContent = `Could not open project: ${error.message}`; }
    })();
})();
