#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
EXTENSION_UUID="dx-light@robobloq-linux"

command -v gnome-extensions >/dev/null || { printf 'GNOME Shell extensions are required.\n' >&2; exit 1; }

"$ROOT_DIR/package-extension.sh"
gnome-extensions install --force "$ROOT_DIR/dist/$EXTENSION_UUID.shell-extension.zip"
gnome-extensions enable "$EXTENSION_UUID" || true

printf 'DX-Light installed. The GNOME extension controls HID devices directly, without a local API service.\n'
printf 'Reconnect to GNOME before opening the panel extension.\n'
printf 'Optional HID permissions: sudo install -m 0644 99-dx-light.rules /etc/udev/rules.d/\n'
