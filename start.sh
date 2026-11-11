#!/bin/sh
# Linux/macOS editor launcher: sh start.sh [project_folder]
cd "$(dirname "$0")" || exit 1
exec python3 tools/editor/server.py --open-browser "$@"
