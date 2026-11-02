#!/bin/sh
# Builds smconv (the SNESMOD soundbank converter) from the bundled source into tools/smconv/smconv.
# Needs Go 1.23+ (https://go.dev/dl/). Only required when a project has sounds in sfx/ or audio/.
set -e
cd "$(dirname "$0")/.."
command -v go >/dev/null 2>&1 || { echo "Go was not found. Install it from https://go.dev/dl/"; exit 1; }
mkdir -p tools/smconv
(cd tools/snesmod-main/smconv && go build -o ../../smconv/smconv .)
echo "Built tools/smconv/smconv"
