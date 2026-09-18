#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATA_DIR="$HOME/.local/share/dx-light-robobloq-linux"
VENV_DIR="$DATA_DIR/venv"
SYSTEMD_DIR="$HOME/.config/systemd/user"
EXTENSION_UUID="dx-light@robobloq-linux"

command -v python3 >/dev/null || { printf 'python3 is required.\n' >&2; exit 1; }
command -v gnome-extensions >/dev/null || { printf 'GNOME Shell extensions are required.\n' >&2; exit 1; }
command -v curl >/dev/null || { printf 'curl is required.\n' >&2; exit 1; }

python3 -m venv --system-site-packages "$VENV_DIR"
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install "$ROOT_DIR"

mkdir -p "$SYSTEMD_DIR"
install -m 0644 "$ROOT_DIR/systemd/robobloq-led.service" "$SYSTEMD_DIR/robobloq-led.service"
install -m 0644 "$ROOT_DIR/systemd/robobloq-session-monitor.service" "$SYSTEMD_DIR/robobloq-session-monitor.service"
install -m 0644 "$ROOT_DIR/systemd/robobloq-wallpaper-sync.service" "$SYSTEMD_DIR/robobloq-wallpaper-sync.service"

"$ROOT_DIR/package-extension.sh"
gnome-extensions install --force "$ROOT_DIR/dist/$EXTENSION_UUID.shell-extension.zip"
gnome-extensions enable "$EXTENSION_UUID" || true

systemctl --user daemon-reload
systemctl --user enable --now robobloq-led.service robobloq-session-monitor.service

printf 'DX-Light installed. The local D-Bus daemon owns HID and screen synchronization; no HTTP port is opened.\n'
printf 'Reconnect to GNOME before opening the panel extension.\n'
printf 'Optional HID permissions: sudo install -m 0644 99-dx-light.rules /etc/udev/rules.d/\n'
