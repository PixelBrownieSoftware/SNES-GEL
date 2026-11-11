#!/usr/bin/env python3
"""Show the native file picker for a GEL project marker."""

import sys
import tkinter as tk
from tkinter import filedialog


def main():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askopenfilename(
            parent=root,
            title="Open GEL project",
            initialdir=sys.argv[1] if len(sys.argv) > 1 else None,
            filetypes=[("GEL project", "gel-project.json"), ("All files", "*.*")],
        )
        if selected:
            sys.stdout.write(selected)
    finally:
        root.destroy()


if __name__ == "__main__":
    main()
