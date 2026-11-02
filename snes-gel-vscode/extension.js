const vscode = require("vscode");

// name -> { name, params: [{type, name, doc}], doc, file, line, isStatic }
let index = new Map();

const FN_RE = /^[ \t]*(static[ \t]+)?fn[ \t]+(\w+)[ \t]*\(([^)]*)\)/;
const PARAM_TAG = /^@param\s+(\w+)\s*[-:]?\s*(.*)$/;

function parseDocument(text, file) {
    const lines = text.split(/\r?\n/);
    const found = [];
    for (let i = 0; i < lines.length; i++) {
        const m = FN_RE.exec(lines[i]);
        if (!m) continue;
        // Contiguous // comment block directly above the function.
        const comment = [];
        for (let j = i - 1; j >= 0; j--) {
            const c = /^[ \t]*\/\/ ?(.*)$/.exec(lines[j]);
            if (!c) break;
            comment.unshift(c[1]);
        }
        const paramDocs = {};
        const doc = [];
        for (const c of comment) {
            const p = PARAM_TAG.exec(c.trim());
            if (p) paramDocs[p[1]] = p[2];
            else doc.push(c);
        }
        const params = m[3].trim() === "" ? [] : m[3].split(",").map((s) => {
            const parts = s.trim().split(/\s+/);
            const name = parts.pop();
            return { type: parts.join(" "), name, doc: paramDocs[name] || "" };
        });
        found.push({ name: m[2], params, doc: doc.join("\n").trim(), file, line: i, isStatic: !!m[1] });
    }
    return found;
}

async function rebuildIndex() {
    const next = new Map();
    const uris = await vscode.workspace.findFiles("**/*.gel", "**/{build,node_modules}/**");
    for (const uri of uris) {
        try {
            const doc = await vscode.workspace.openTextDocument(uri);
            for (const f of parseDocument(doc.getText(), uri)) next.set(f.name, f);
        } catch (e) { /* unreadable file: skip */ }
    }
    index = next;
}

function refreshFile(doc) {
    if (doc.languageId !== "gel") return;
    for (const [k, v] of index) if (v.file.toString() === doc.uri.toString()) index.delete(k);
    for (const f of parseDocument(doc.getText(), doc.uri)) index.set(f.name, f);
}

function signatureLabel(fn) {
    return `${fn.isStatic ? "static " : ""}fn ${fn.name}(${fn.params.map((p) => `${p.type} ${p.name}`.trim()).join(", ")})`;
}

// Finds calls `name(args)` in text, returning arg start offsets. Skips comments and strings.
function findCalls(text) {
    const calls = [];
    const re = /\b([A-Za-z_]\w*)[ \t]*\(/g;
    let m;
    while ((m = re.exec(text))) {
        const fn = index.get(m[1]);
        if (!fn) continue;
        const lineStart = text.lastIndexOf("\n", m.index) + 1;
        const before = text.slice(lineStart, m.index);
        if (before.includes("//")) continue;
        if (/\bfn[ \t]+$/.test(before)) continue; // the definition itself
        const open = m.index + m[0].length;
        const args = [];
        let depth = 0, start = open, inStr = false, closed = -1;
        for (let i = open; i < text.length; i++) {
            const ch = text[i];
            if (inStr) { if (ch === '"') inStr = false; continue; }
            if (ch === '"') inStr = true;
            else if (ch === "(" || ch === "[") depth++;
            else if ((ch === ")" || ch === "]") && depth > 0) depth--;
            else if (ch === ")") { args.push([start, i]); closed = i; break; }
            else if (ch === "," && depth === 0) { args.push([start, i]); start = i + 1; }
            else if (ch === ";" || ch === "{") break;
        }
        calls.push({ fn, nameStart: m.index, open, closed, args });
    }
    return calls;
}

function activate(context) {
    rebuildIndex();
    const watcher = vscode.workspace.createFileSystemWatcher("**/*.gel");
    watcher.onDidCreate(rebuildIndex);
    watcher.onDidDelete(rebuildIndex);
    context.subscriptions.push(
        watcher,
        vscode.workspace.onDidChangeTextDocument((e) => refreshFile(e.document)),
        vscode.workspace.onDidOpenTextDocument(refreshFile)
    );

    const sel = { language: "gel" };

    context.subscriptions.push(vscode.languages.registerHoverProvider(sel, {
        provideHover(doc, pos) {
            const range = doc.getWordRangeAtPosition(pos);
            if (!range) return;
            const word = doc.getText(range);
            const call = findCalls(doc.getText()).find((c) => {
                const o = doc.offsetAt(pos);
                return c.closed >= 0 && o > c.open && o <= c.closed;
            });
            const fn = index.get(word);
            if (fn) {
                const md = new vscode.MarkdownString();
                md.appendCodeblock(signatureLabel(fn), "gel");
                if (fn.doc) md.appendMarkdown(fn.doc.replace(/\n/g, "  \n") + "\n\n");
                for (const p of fn.params) {
                    if (p.doc) md.appendMarkdown(`- \`${p.name}\` — ${p.doc}\n`);
                }
                return new vscode.Hover(md, range);
            }
            // Hovering an argument: explain which parameter it feeds.
            if (call) {
                const o = doc.offsetAt(pos);
                const k = call.args.findIndex(([s, e]) => o >= s && o <= e);
                const p = call.fn.params[k];
                if (p) {
                    const md = new vscode.MarkdownString();
                    md.appendMarkdown(`**${call.fn.name}** parameter \`${p.type} ${p.name}\``);
                    if (p.doc) md.appendMarkdown(`\n\n${p.doc}`);
                    return new vscode.Hover(md);
                }
            }
        }
    }));

    context.subscriptions.push(vscode.languages.registerSignatureHelpProvider(sel, {
        provideSignatureHelp(doc, pos) {
            const text = doc.getText();
            const o = doc.offsetAt(pos);
            const call = findCalls(text)
                .filter((c) => o >= c.open && (c.closed < 0 || o <= c.closed))
                .pop();
            if (!call) return;
            const fn = call.fn;
            const info = new vscode.SignatureInformation(signatureLabel(fn), new vscode.MarkdownString(fn.doc));
            info.parameters = fn.params.map((p) => new vscode.ParameterInformation(
                `${p.type} ${p.name}`.trim(), new vscode.MarkdownString(p.doc)));
            const help = new vscode.SignatureHelp();
            help.signatures = [info];
            help.activeSignature = 0;
            const k = call.args.findIndex(([s, e]) => o >= s && o <= e);
            help.activeParameter = k < 0 ? Math.max(0, call.args.length - 1) : k;
            return help;
        }
    }, "(", ","));

    if (vscode.languages.registerInlayHintsProvider) {
        context.subscriptions.push(vscode.languages.registerInlayHintsProvider(sel, {
            provideInlayHints(doc, range) {
                if (!vscode.workspace.getConfiguration("gel").get("parameterNameHints", true)) return [];
                const hints = [];
                for (const call of findCalls(doc.getText())) {
                    call.args.forEach(([s, e], k) => {
                        const p = call.fn.params[k];
                        if (!p) return;
                        const raw = doc.getText().slice(s, e);
                        const arg = raw.trim();
                        if (arg === "" || arg === p.name) return;
                        const at = doc.positionAt(s + (raw.length - raw.trimStart().length));
                        if (!range.contains(at)) return;
                        const hint = new vscode.InlayHint(at, `${p.name}:`, vscode.InlayHintKind.Parameter);
                        hint.paddingRight = true;
                        if (p.doc) hint.tooltip = new vscode.MarkdownString(`\`${p.type} ${p.name}\` — ${p.doc}`);
                        hints.push(hint);
                    });
                }
                return hints;
            }
        }));
    }
}

function deactivate() {}

module.exports = { activate, deactivate };
