"""Content-hash cache for the slow, self-contained build stages (sound, assets).

A stage is skipped only when ALL of these hold:
  * its recorded input key (hash of every input file's name and bytes, the converter scripts,
    tool versions and settings) equals the current key,
  * every output file it produced still exists and has the same hash as when it was stored.
The cache entry is dropped before a stage runs and written only after it succeeds, so an
interrupted or failed stage can never be mistaken for a good one. Any doubt means a rebuild.
"""
import hashlib
import json
import os

CACHE_VERSION = 1      # bump when the cache format or key rules change
CACHE_FILE = "cache.json"


def file_hash(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_key(base_dir, files, extra=""):
    """Hash of (relative path, content) for every file, plus a string of settings."""
    h = hashlib.sha256()
    h.update(f"gel-cache-{CACHE_VERSION}\0{extra}\0".encode())
    for path in sorted({os.path.abspath(p) for p in files}):
        try:
            rel = os.path.relpath(path, base_dir).replace("\\", "/")
        except ValueError:
            rel = path
        h.update(rel.encode() + b"\0")
        try:
            h.update(file_hash(path).encode())
        except OSError:
            h.update(b"<missing>")
        h.update(b"\0")
    return h.hexdigest()


class StageCache:
    def __init__(self, build_dir, enabled=True):
        self.build_dir = build_dir
        self.enabled = enabled
        self.path = os.path.join(build_dir, CACHE_FILE)
        self.data = self._load()

    def _load(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
            if data.get("version") == CACHE_VERSION and isinstance(data.get("stages"), dict):
                return data
        except (OSError, ValueError, AttributeError):
            pass
        return {"version": CACHE_VERSION, "stages": {}}

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.data, f, indent=1, sort_keys=True)
        os.replace(tmp, self.path)

    def lookup(self, stage, key):
        """The stored entry (with its 'extra' dict) if the stage can be skipped, else None."""
        if not self.enabled:
            return None
        entry = self.data["stages"].get(stage)
        if not entry or entry.get("key") != key:
            return None
        for name, digest in entry.get("outputs", {}).items():
            path = os.path.join(self.build_dir, name)
            try:
                if file_hash(path) != digest:
                    return None
            except OSError:
                return None
        return entry

    def invalidate(self, stage):
        """Forget a stage before it runs; returns the old output names (for stale cleanup)."""
        old = self.data["stages"].pop(stage, {})
        self._save()
        return list(old.get("outputs", {}))

    def store(self, stage, key, output_names, extra=None):
        outputs = {}
        for name in sorted(set(output_names)):
            path = os.path.join(self.build_dir, name)
            if os.path.isfile(path):
                outputs[name] = file_hash(path)
        self.data["stages"][stage] = {"key": key, "outputs": outputs, "extra": extra or {}}
        self._save()
