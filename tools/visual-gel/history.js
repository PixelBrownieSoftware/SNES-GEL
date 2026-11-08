(function (root) {
    "use strict";
    function createHistory(initial, limit = 100) {
        let current = JSON.stringify(initial);
        const undo = [], redo = [];
        return {
            record(value) {
                const next = JSON.stringify(value);
                if (next === current) return false;
                undo.push(current);
                if (undo.length > limit) undo.shift();
                current = next;
                redo.length = 0;
                return true;
            },
            undo() {
                if (!undo.length) return null;
                redo.push(current);
                current = undo.pop();
                return JSON.parse(current);
            },
            redo() {
                if (!redo.length) return null;
                undo.push(current);
                current = redo.pop();
                return JSON.parse(current);
            },
            get canUndo() { return undo.length > 0; },
            get canRedo() { return redo.length > 0; }
        };
    }
    if (typeof module !== "undefined" && module.exports) module.exports = { createHistory };
    else root.createVisualGelHistory = createHistory;
})(globalThis);
