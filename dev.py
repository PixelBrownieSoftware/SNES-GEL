#!/usr/bin/env python3
"""GEL dev launcher (Windows, Linux, macOS): check requirements, build, start Mesen, watch for changes.

usage: python dev.py [project_folder] [--no-emulator]     (default project: game)

Set GEL_MESEN to the Mesen executable if it is not in tools/mesen/ or on PATH.
"""
import importlib.util
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
WIN = sys.platform == "win32"


def find_mesen():
    env = os.environ.get("GEL_MESEN")
    if env:
        return env if os.path.isfile(env) else None
    names = ["Mesen.exe"] if WIN else ["Mesen", "mesen", "Mesen.AppImage"]
    for n in names:
        p = os.path.join(ROOT, "tools", "mesen", n)
        if os.path.isfile(p):
            return p
    for n in ("Mesen", "mesen"):
        p = shutil.which(n)
        if p:
            return p
    return None


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    launch = "--no-emulator" not in sys.argv
    project = args[0] if args else "game"
    project_dir = os.path.abspath(project)
    missing = []

    def need(path, what):
        if not os.path.exists(os.path.join(ROOT, path) if not os.path.isabs(path) else path):
            missing.append(f"{path}  -  {what}")

    print("=" * 60)
    print(f"  GEL dev environment  -  project: {project}")
    print("=" * 60)
    print("Checking requirements...")

    need(os.path.join(project_dir, "game.gel"), "your project's main source file")
    for f, what in (("compiler/build.py", "GEL build script"), ("compiler/snes_gel.lark", "GEL grammar"),
                    ("compiler/stdlib/snes.gel", "GEL standard library"),
                    ("tools/gel_bridge.py", "hot-reload file watcher"),
                    ("tools/mesen_gel_hook.lua", "Mesen hot-reload script")):
        need(f, what)
    for mod, pkg in (("lark", "lark"), ("watchdog", "watchdog"), ("PIL", "pillow")):
        if importlib.util.find_spec(mod) is None:
            missing.append(f"Python package '{pkg}'  -  fix: {sys.executable} -m pip install {pkg}")

    mesen = find_mesen() if launch else None
    if launch and not mesen:
        print("  [note] Mesen not found: get Mesen 2 from https://www.mesen.ca and put it in tools/mesen/ "
              "or set GEL_MESEN. Continuing without the emulator.")

    if missing:
        print()
        for m in missing:
            print(f"  [MISSING] {m}")
        print(f"\n{len(missing)} requirement(s) missing. Fix the above and run dev.py again.")
        return 1
    print("All requirements found.\n")

    print("Building initial ROM...")
    if subprocess.call([sys.executable, os.path.join(ROOT, "compiler", "build.py"), project_dir]) != 0:
        print("Initial build failed. Fix the errors above, then run dev.py again.")
        return 1

    if mesen:
        print("Launching Mesen with hot-reload hook...")
        build = os.path.join(project_dir, "build")
        subprocess.Popen([mesen, os.path.join(build, "game.sfc"), os.path.join(build, "gel_hook.lua")])

    print("Starting bridge (Ctrl+C to stop)...")
    try:
        return subprocess.call([sys.executable, os.path.join(ROOT, "tools", "gel_bridge.py"), project_dir])
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
