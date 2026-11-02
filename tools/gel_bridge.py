# ==============================================================================
# gel_bridge.py - Multi-Format (.gel & .png) Hot-Reload Bridge
# ==============================================================================
import os
import sys
import time
import subprocess
from datetime import datetime
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD_SCRIPT = os.path.join(ROOT, "compiler", "build.py")
PROJECT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "game"))
BUILD_DIR = os.path.join(PROJECT, "build")
SIGNAL_FILE = os.path.join(BUILD_DIR, "hotreload.signal")
WATCHED_EXTENSIONS = (".gel", ".png", ".metasprite.json", ".tmj", ".tsj", ".json")

def get_time_str():
    return datetime.now().strftime("[%H:%M:%S]")

class GelHotReloadHandler(FileSystemEventHandler):
    def __init__(self):
        self.is_building = False
        self.file_mtimes = {}

    def handle_path(self, path, is_directory):
        if is_directory or self.is_building:
            return

        filename = os.path.basename(path)

        # Filter by extensions and ignore anything generated under build/
        if not filename.endswith(WATCHED_EXTENSIONS) or filename.startswith("."):
            return
        if os.path.abspath(path).startswith(BUILD_DIR + os.sep):
            return

        filepath = os.path.abspath(path)
        
        try:
            current_mtime = os.path.getmtime(filepath)
        except OSError:
            return  # File locked during atomic write

        last_mtime = self.file_mtimes.get(filepath, 0)
        if current_mtime <= last_mtime:
            return

        self.file_mtimes[filepath] = current_mtime
        self.is_building = True

        print("\n" + "=" * 60)
        print(f"{get_time_str()} ⚡ Asset/Source edit detected in '{filename}'. Rebuilding...")
        print("=" * 60)

        try:
            res = subprocess.run([sys.executable, BUILD_SCRIPT, PROJECT], capture_output=False)

            if res.returncode == 0:
                with open(SIGNAL_FILE, "w") as f:
                    f.write(f"{time.time()}\n{filename}\n")
                    f.flush()
                    os.fsync(f.fileno())
                print(f"{get_time_str()} ✔ Build successful! Hot-reload signal sent to Mesen.")
            else:
                print(f"{get_time_str()} ❌ Build failed! ROM untouched.")
        finally:
            time.sleep(0.3)
            self.is_building = False

    def on_modified(self, event):
        self.handle_path(event.src_path, event.is_directory)

    def on_created(self, event):
        self.handle_path(event.src_path, event.is_directory)

    def on_moved(self, event):
        self.handle_path(event.dest_path, event.is_directory)


def start_bridge():
    print("============================================================")
    print("      SNES-GEL Live Hot-Reloading Bridge Active")
    print("============================================================")
    print(f"{get_time_str()} Watching '{PROJECT}' for .gel, .png, .tmj/.tsj (Tiled) and .json edits...")

    event_handler = GelHotReloadHandler()
    observer = Observer()
    observer.schedule(event_handler, path=PROJECT, recursive=True)
    observer.start()

    try:
        while True:
            time.sleep(0.3)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()

if __name__ == "__main__":
    start_bridge()