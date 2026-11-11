(function (root) {
    "use strict";
    function create(code) {
        const head = /^(#define[ \t]+)([A-Za-z_]\w*)([ \t]+)(.*)$/.exec(code);
        if (!head) throw new Error("This define has no editable value; edit it in the source file.");
        const tail = head[4];
        let masked = tail.replace(/"[^"\r\n]*"|'[^'\r\n]*'|\/\/[^\r\n]*|\/\*[\s\S]*?\*\//g,
            part => /^["']/.test(part) ? part : part.replace(/[^\r\n]/g, " "));
        const last = masked.search(/\s*$/);
        const value = tail.slice(0, last);
        if (value !== masked.slice(0, last))
            throw new Error("This define has a comment inside its value; edit it in the source file.");
        return { prefix: head[1], name: head[2], spacing: head[3], value, suffix: tail.slice(last) };
    }
    function emit(model) {
        if (/[\r\n]/.test(model.name) || /[\r\n]/.test(model.value))
            throw new Error("A define must be a single line.");
        return model.prefix + model.name + model.spacing + model.value + model.suffix;
    }
    const api = { create, emit };
    if (typeof module !== "undefined" && module.exports) module.exports = api;
    root.VisualGelDefineEditor = api;
})(globalThis);
