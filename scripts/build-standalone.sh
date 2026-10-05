#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_DIR="${1:-$PROJECT_ROOT/dist/nuitka}"

command -v python3 >/dev/null 2>&1 || {
    printf 'DebArk standalone build: python3 is required.\n' >&2
    exit 1
}
python3 -m nuitka --version >/dev/null 2>&1 || {
    printf 'DebArk standalone build: install Nuitka in the build environment first.\n' >&2
    exit 1
}

mkdir -p "$OUTPUT_DIR"
PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" python3 -m nuitka \
    --mode=standalone \
    --output-dir="$OUTPUT_DIR" \
    --output-filename=debark-cli \
    --include-package=debark \
    --include-data-dir="$PROJECT_ROOT/src/debark/data=debark/data" \
    "$PROJECT_ROOT/scripts/standalone_entry.py"

printf '\nStandalone output: %s/standalone_entry.dist\n' "$OUTPUT_DIR"
printf 'Executable: %s/standalone_entry.dist/debark-cli\n' "$OUTPUT_DIR"
printf 'This is a directory distribution. It is not a onefile binary.\n'
