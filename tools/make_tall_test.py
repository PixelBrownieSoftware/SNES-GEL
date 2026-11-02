"""Builds a taller copy of the game in a scratch folder to exercise map streaming.

usage: make_tall_test.py <out_project_dir> [rows]
The original level is moved to the bottom of a `rows`-tall map (default 100) and the new rows
above it are filled with a deterministic pattern, so each map row is distinguishable."""
import json
import os
import random
import shutil
import sys

SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "game")


def main():
    out = os.path.abspath(sys.argv[1])
    rows = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    if os.path.exists(out):
        shutil.rmtree(out)
    shutil.copytree(SRC, out, ignore=shutil.ignore_patterns("build"))
    path = os.path.join(out, "assets", "level.tmj")
    with open(path) as f:
        data = json.load(f)
    width, old_h = data["width"], data["height"]
    extra = rows - old_h
    rnd = random.Random(7)
    for layer in data["layers"]:
        if layer["type"] == "tilelayer":
            if layer["name"].lower() == "collision":
                top = [0] * (width * extra)
            else:
                first = data["tilesets"][0]["firstgid"]
                top = [first + 1 + (rnd.randrange(6) if rnd.random() < 0.3 else 0) if rnd.random() < 0.4 else 0
                       for _ in range(width * extra)]
            layer["data"] = top + layer["data"]
            layer["height"] = rows
        elif layer["type"] == "objectgroup":
            for obj in layer["objects"]:
                obj["y"] += extra * 8
    data["height"] = rows
    with open(path, "w") as f:
        json.dump(data, f)
    print(f"{out}: {width}x{rows}")


if __name__ == "__main__":
    main()
