# DX-Light ROBOBLOQ Linux

Control a ROBOBLOQ / QinHeng USB HID ambient LED strip (VID:PID **1a86:fe07**) on Linux (Ubuntu).

> This project was built by reverse-engineering the device protocol via USB capture and implementing native Linux HID control.
> It builds on the original Linux protocol work by Amel Varghese / RlNZLER.

---

## Features

- Set solid colors (RGB)
- Control multiple connected DX-Light controllers
- Built-in dynamic effects, controller rhythm presets, and effect speed
- Per-display LED-zone configuration (left, top, right, bottom)
- Local GNOME Shell panel control through a user-session D-Bus service
- GNOME Shell extension sources in `gnome-extension/`
- Wallpaper synchronization and session lock actions
- Works without the vendor Windows app
- Auto-detects the correct vendor HID interface (`06 00 ff` report descriptor)
- Udev rule support (run without `sudo`)

---

## Quick install

Requires GNOME Shell 50, Python 3 with `venv`, and a connected DX-Light controller.

```bash
git clone https://github.com/WadohS/dx-light-robobloq-linux.git
cd dx-light-robobloq-linux
./install.sh
```

The installer creates a user-local Python environment, installs the GNOME
extension, and enables the D-Bus daemon plus the lock-session monitor. Log out
and back in before using the panel extension.

Wallpaper synchronization is controlled from the GNOME panel extension. It
uses the configured desktop wallpaper and does not run an independent service.

---

## Supported device

This project targets devices that show up as:

- `lsusb` -> `ID 1a86:fe07 QinHeng Electronics USBHID`
- HID vendor interface report descriptor begins with: `06 00 ff`

---

## Setup

### 1) Udev rule (recommended)

Install the supplied rule so the active desktop session can access the
controller without `sudo`:

```bash
sudo install -m 0644 99-dx-light.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
# Unplug/replug the LED strip
```

Verify permissions:

```bash
ls -l /dev/hidraw*
```

You should see the matching device nodes with either an ACL for the active
session or `plugdev` group access.

---

## Development installation

```bash
cd robobloq-led-linux
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip

# install package in editable mode
pip install -e .
```

---

## CLI usage

Set a color:

```bash
robobloq-led --r 255 --g 0 --b 0     # red
robobloq-led --r 0 --g 255 --b 0     # green
robobloq-led --r 0 --g 0 --b 255     # blue
robobloq-led --r 0 --g 0 --b 0       # off
robobloq-led --r 255 --g 200 --b 120 # warm white
```

If auto-detect ever fails (after unplug/replug), you can pass the device path explicitly:

```bash
robobloq-led --dev /dev/hidraw1 --r 255 --g 255 --b 255
```

---

## How it works (high level)

- The LED strip exposes two HID interfaces:
  - a keyboard-like HID interface (ignore)
  - a vendor-defined HID interface (descriptor starts with `06 00 ff`)
- We send 64-byte reports to the vendor interface.
- The device expects a checksum byte. This implementation recomputes the checksum on every color set.

---

## Troubleshooting

### Permission denied: /dev/hidrawX
- Apply the udev rule above and unplug/replug the device.

### Colors work once, then stop / some colors ignored
- This is usually a checksum/counter issue. The current implementation updates checksum and increments the counter.

### Device path changes after replug
- Normal. The project auto-detects the correct vendor interface by reading the report descriptor.
- If needed, specify `--dev /dev/hidrawX`.

### Confirm device is detected
```bash
lsusb | grep -i 1a86
```

---

## Screen Sync (Ambilight-style)

Supports:
- Wallpaper-only synchronization on Wayland, without a screen-sharing session
- Real-time ambient color sync
- Adjustable FPS and edge thickness

On GNOME Wayland, the extension samples the configured wallpaper image and
sends one color per display to the local D-Bus daemon. Windows are excluded;
no Portal permission, PipeWire stream, or screen-sharing indicator is used.

Example:
```
python -m robobloq_led.screen_sync --monitor 2 --fps 40
```

## GNOME extension

The extension source is in `gnome-extension/`. Build an installable archive:

```bash
./package-extension.sh
```

It creates `dist/dx-light@robobloq-linux.shell-extension.zip`.

## Project status

The project includes a user-local installer, portable systemd units, and a
GNOME Shell extension package.

## Roadmap

- [ ] Single HID writer shared by all synchronization modes
- [ ] PipeWire audio capture for controller rhythm effects
- [ ] Translate the GNOME extension and web UI from the system language
- [ ] Package releases + GitHub Actions CI

---

## License

MIT
