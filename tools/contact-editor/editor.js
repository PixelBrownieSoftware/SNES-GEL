(function () {
    "use strict";
    const rows = document.getElementById("rows");
    const status = document.getElementById("status");
    const preview = document.getElementById("preview");
    const handlerHost = document.getElementById("handlers");
    const serverBadge = document.getElementById("server-state");
    const keys = ["self", "other", "enter", "stay", "exit"];
    const phaseKeys = ["enter", "stay", "exit"];
    let interactions = [];
    let handlers = {};
    const visual = createVisualGelEditor({ status, locals: ["self", "other"], localTypes: { self: "u8", other: "u8" } });
    const { el, highlight, renderBlocks, addBar } = visual;
    let index = visual.emptyIndex();
    let objects = null;
    let history = null;
    let restoring = false;
    let validationVersion = 0;
    let validationTimer = null;
    const undoButton = document.getElementById("undo");
    const redoButton = document.getElementById("redo");

    function state() { return { interactions, handlers }; }
    function record() {
        if (!history || restoring) return;
        history.record(state());
        undoButton.disabled = !history.canUndo;
        redoButton.disabled = !history.canRedo;
    }
    function restore(snapshot) {
        if (!snapshot) return;
        interactions = snapshot.interactions;
        handlers = snapshot.handlers;
        visual.resetSelection();
        restoring = true;
        try { render(); } finally { restoring = false; }
        undoButton.disabled = !history.canUndo;
        redoButton.disabled = !history.canRedo;
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

    function checkedConfig() {
        const config = data();
        if (objects) ContactModel.validateObjects(config.interactions, objects);
        for (const [name, body] of Object.entries(config.handlers)) {
            const problem = visual.firstTypeProblem(body);
            if (problem) throw new Error(`Handler ${name}: ${problem}`);
        }
        return config;
    }

    function update() {
        record();
        const version = ++validationVersion;
        clearTimeout(validationTimer);
        const missing = new Set();
        if (objects) for (const row of interactions) {
            if (phaseKeys.some(key => row[key].trim())) {
                for (const value of [row.self, row.other]) {
                    if (value.startsWith("OBJ_") && !index.defines.has(value)) missing.add(value);
                }
            }
        }
        document.getElementById("object-warning").textContent = missing.size
            ? `Not found in built GEL symbols: ${[...missing].join(", ")}. Place these classes in a level and rebuild before compiling the contacts.`
            : "";
        try {
            const gel = ContactModel.generate(checkedConfig());
            highlight(gel, preview);
            status.textContent = "";
            if (!saveProject.disabled) {
                validationTimer = setTimeout(async () => {
                    try {
                        await validateSyntax(gel);
                    } catch (error) {
                        if (version === validationVersion) status.textContent = `Syntax check failed: ${error.message}`;
                    }
                }, 300);
            }
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
                const objectChoice = objects && (key === "self" || key === "other");
                const input = document.createElement(objectChoice ? "select" : "input");
                if (objectChoice) {
                    const choices = ContactModel.objectOptions(objects, key === "self");
                    input.append(new Option("Choose object…", ""));
                    if (row[key] && !choices.some(option => option.value === row[key])) {
                        input.append(new Option(`Unresolved: ${row[key]}`, row[key]));
                    }
                    for (const option of choices) input.append(new Option(`${option.label} (${option.value})`, option.value));
                }
                input.value = row[key];
                input.setAttribute("aria-label", `${key} for interaction ${i + 1}`);
                input.addEventListener("input", () => { row[key] = input.value; update(); });
                input.addEventListener("change", () => {
                    if (objectChoice) { row[key] = input.value; update(); }
                    else { renderHandlers(); record(); }
                });
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
        const newInteractions = ContactModel.validate(config);
        const newHandlers = ContactModel.validateHandlers(config);
        interactions = newInteractions;
        handlers = newHandlers;
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
    async function validateSyntax(gel) {
        const response = await fetch("/api/validate-gel", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ gel }),
        });
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    }

    async function validateForDownload(config) {
        if (!saveProject.disabled) await validateSyntax(ContactModel.generate(config));
        else status.textContent = "Offline: generated GEL was not checked against the compiler grammar.";
    }
    undoButton.addEventListener("click", () => restore(history.undo()));
    redoButton.addEventListener("click", () => restore(history.redo()));
    document.addEventListener("keydown", event => {
        const target = event.target;
        if (!history || !(event.ctrlKey || event.metaKey) || event.altKey || target.isContentEditable ||
            ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
        const key = event.key.toLowerCase();
        if (key === "z" || key === "y") {
            event.preventDefault();
            restore(key === "y" || event.shiftKey ? history.redo() : history.undo());
        }
    });
    document.getElementById("add").addEventListener("click", () => {
        interactions.push({ self: "", other: "", enter: "", stay: "", exit: "" }); render();
    });
    document.getElementById("save").addEventListener("click", async () => {
        try {
            const config = cleanConfig();
            await validateForDownload(config);
            download("contacts.json", JSON.stringify(config, null, 2) + "\n", "application/json");
        }
        catch (error) { status.textContent = error.message; }
    });
    document.getElementById("export").addEventListener("click", async () => {
        try {
            const config = checkedConfig();
            const gel = ContactModel.generate(config);
            await validateForDownload(config);
            download("contact.gel", gel, "text/plain");
        }
        catch (error) { status.textContent = error.message; }
    });
    saveProject.addEventListener("click", async () => {
        try {
            if (!await loadSymbols()) throw new Error("Could not refresh object definitions and symbols.");
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
    document.getElementById("refresh-symbols").addEventListener("click", async () => {
        await loadSymbols();
        render();
    });

    async function loadSymbols() {
        try {
            const [response, objectResponse] = await Promise.all([
                fetch("/api/symbols", { cache: "no-store" }),
                fetch("/api/objects", { cache: "no-store" })
            ]);
            if (!response.ok || !objectResponse.ok) throw new Error(`Symbols: HTTP ${response.status}; objects: HTTP ${objectResponse.status}`);
            const symbols = await response.json();
            const definitions = await objectResponse.json();
            ContactModel.objectOptions(definitions, false);
            index = visual.buildIndex(symbols);
            objects = definitions;
            visual.setIndex(index);
            serverBadge.textContent = `Connected: ${index.items.length} project symbols`;
            serverBadge.className = "badge ok";
            saveProject.disabled = false;
            return true;
        } catch (error) {
            index = visual.emptyIndex();
            visual.setIndex(index);
            objects = null;
            serverBadge.textContent = `Could not load project symbols or objects: ${error.message}`;
            serverBadge.className = "badge";
            saveProject.disabled = true;
            return false;
        }
    }

    (async function init() {
        if (await loadSymbols()) {
            try {
                const response = await fetch("/api/contacts", { cache: "no-store" });
                if (!response.ok) throw new Error(`HTTP ${response.status}`);
                load(await response.json());
            } catch (error) {
                render();
                status.textContent = `Could not load contacts.json: ${error.message}`;
                history = createVisualGelHistory(state());
                return;
            }
        }
        render();
        history = createVisualGelHistory(state());
    })();
})();
