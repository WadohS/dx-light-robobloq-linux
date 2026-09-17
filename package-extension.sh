#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXTENSION_DIR="$ROOT_DIR/gnome-extension"
OUTPUT_DIR="${1:-$ROOT_DIR/dist}"

mkdir -p "$OUTPUT_DIR"
gnome-extensions pack --force --out-dir "$OUTPUT_DIR" "$EXTENSION_DIR"
