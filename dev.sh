#!/bin/sh
# Linux/macOS launcher: ./dev.sh [project_folder]  (see dev.py)
cd "$(dirname "$0")" || exit 1
exec python3 dev.py "$@"
