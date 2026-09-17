import os
import glob
import fcntl
import json
import time
from dataclasses import dataclass
from pathlib import Path

# Your working BLUE capture (base packet). We modify counter+RGB+checksum.
BASE_HEX = (
    "5242100e86010000ff4142000000feb9"
    "00000000000000000000000000000000"
    "00000000000000000000000000000000"
    "00000000000000000000000000000000"
)

IOC_WRITE = 1
LAYOUT_PATH = Path.home() / ".config/robobloq-led/layout.json"
SESSION_LOCK_PATH = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "robobloq-led.locked"


def _default_session_behavior() -> dict:
    return {
        "lock": {"mode": "off", "effect": "dxlight-dynamix"},
        "unlock": {"mode": "sync", "effect": "dxlight-dynamix"},
    }

def _IOC(dir_, type_, nr, size):
    return (dir_ << 30) | (ord(type_) << 8) | (nr << 0) | (size << 16)

def HIDIOCSFEATURE(size):
    # Set FEATURE report via hidraw
    return _IOC(IOC_WRITE, "H", 0x06, size)

def _is_vendor_descriptor(hidraw_path: str) -> bool:
    # Vendor interface starts with: 06 00 ff
    sysname = os.path.basename(os.path.realpath(hidraw_path))
    desc_path = f"/sys/class/hidraw/{sysname}/device/report_descriptor"
    try:
        with open(desc_path, "rb") as f:
            return f.read(3) == bytes([0x06, 0x00, 0xFF])
    except FileNotFoundError:
        return False

def _default_layout() -> dict:
    devices = discover_vendor_devices()
    return {
        "version": 2,
        "session": _default_session_behavior(),
        "displays": [
            {
                "device": device,
                "screen": "left" if index == 0 else "right",
                "location": "back",
                "installation_direction": "left-to-right" if index == 0 else "right-to-left",
                "sync_area": "edge",
                "edge_count": 3,
                "zones": {"left": 17, "top": 29, "right": 17, "bottom": 0},
            }
            for index, device in enumerate(devices)
        ],
    }


def load_layout() -> dict:
    """Load the persisted controller layout, falling back to the current dual-screen setup."""
    try:
        with LAYOUT_PATH.open(encoding="utf-8") as layout_file:
            layout = json.load(layout_file)
    except (FileNotFoundError, json.JSONDecodeError):
        return _default_layout()

    if not isinstance(layout, dict):
        return _default_layout()
    if isinstance(layout.get("displays"), list):
        if not isinstance(layout.get("session"), dict):
            layout["session"] = _default_session_behavior()
        return layout
    if not isinstance(layout.get("bands"), list):
        return _default_layout()

    # Migrate the first version of the local preferences without losing its device order.
    led_count = max(1, min(254, int(layout.get("led_count", 63))))
    side_count = round(led_count * 17 / 63)
    top_count = led_count - 2 * side_count
    return {
        "version": 2,
        "session": _default_session_behavior(),
        "displays": [
            {
                "device": band["device"],
                "screen": band.get("screen", "left" if index == 0 else "right"),
                "location": "back",
                "installation_direction": "right-to-left" if band.get("reverse") else "left-to-right",
                "sync_area": "edge",
                "edge_count": 3,
                "zones": {"left": side_count, "top": top_count, "right": side_count, "bottom": 0},
            }
            for index, band in enumerate(layout["bands"])
            if isinstance(band, dict) and isinstance(band.get("device"), str)
        ],
    }
    return layout


def save_layout(layout: dict) -> None:
    LAYOUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = LAYOUT_PATH.with_suffix(".tmp")
    with temporary_path.open("w", encoding="utf-8") as layout_file:
        json.dump(layout, layout_file, indent=2)
        layout_file.write("\n")
    temporary_path.replace(LAYOUT_PATH)


def discover_vendor_devices() -> list[str]:
    """Return all connected ROBOBLOQ vendor HID interfaces using stable USB links."""
    discovered = [dev for dev in sorted(glob.glob("/dev/hidraw*")) if _is_vendor_descriptor(dev)]
    links = sorted(glob.glob("/dev/input/by-path/*-hidraw"), key=lambda link: ("-usbv" in link, link))
    stable_links = {}
    for link in links:
        stable_links.setdefault(os.path.realpath(link), link)
    return [stable_links.get(os.path.realpath(dev), dev) for dev in discovered]


def find_vendor_devices() -> list[str]:
    """Find the configured ROBOBLOQ controller interfaces connected to the computer."""
    configured = [
        display.get("device")
        for display in load_layout()["displays"]
        if isinstance(display, dict) and isinstance(display.get("device"), str)
        and os.path.exists(display["device"]) and _is_vendor_descriptor(display["device"])
    ]
    if configured:
        return [dev for index, dev in enumerate(configured) if os.path.realpath(dev) not in {os.path.realpath(previous) for previous in configured[:index]}]

    devices = discover_vendor_devices()
    if not devices:
        raise RuntimeError("Vendor HID interface not found. Unplug/replug the LED and try again.")
    return devices


def find_vendor_device() -> str:
    """Return the first controller for compatibility with single-device callers."""
    return find_vendor_devices()[0]

@dataclass
class RobobloqController:
    dev: str
    counter: int = 0x0E  # start from known working value

    def _build_report(self, r: int, g: int, b: int) -> bytes:
        pkt = bytearray(bytes.fromhex(BASE_HEX))
        if len(pkt) != 64:
            raise ValueError("BASE_HEX must be 64 bytes")

        pkt[3] = self.counter & 0xFF
        pkt[6] = int(r) & 0xFF
        pkt[7] = int(g) & 0xFF
        pkt[8] = int(b) & 0xFF

        # checksum byte = sum(bytes[0..14]) & 0xFF
        pkt[15] = sum(pkt[0:15]) & 0xFF

        return bytes(pkt)

    def _send_feature(self, report64: bytes) -> None:
        buf = bytearray(report64)
        fd = os.open(self.dev, os.O_RDWR)
        try:
            fcntl.ioctl(fd, HIDIOCSFEATURE(64), buf, True)
        finally:
            os.close(fd)

    def _send_raw(self, report64: bytes) -> None:
        with open(self.dev, "wb", buffering=0) as f:
            f.write(report64)

    def set_color(self, r: int, g: int, b: int) -> None:
        report = self._build_report(r, g, b)
        # raw write works on your machine; feature is a safe fallback
        try:
            self._send_raw(report)
        except OSError:
            self._send_feature(report)
        self.counter = (self.counter + 1) & 0xFF

    def set_pixels(self, pixels: list[tuple[int, int, int]]) -> None:
        """Set individually addressable LEDs using the DX-Light section protocol."""
        if not pixels:
            return
        if len(pixels) > 254:
            raise ValueError("A controller supports at most 254 LEDs.")

        # A 64-byte HID report carries at most 11 five-byte LED ranges.
        ranges = []
        start = 1
        color = tuple(int(channel) & 0xFF for channel in pixels[0])
        for index, pixel in enumerate(pixels[1:], start=2):
            next_color = tuple(int(channel) & 0xFF for channel in pixel)
            if next_color != color:
                ranges.extend((start, *color, index - 1))
                start, color = index, next_color
        ranges.extend((start, *color, len(pixels)))

        for offset in range(0, len(ranges), 55):
            payload = ranges[offset : offset + 55]
            length = 6 + len(payload)
            report = bytearray(64)
            report[0:2] = b"RB"
            report[2] = length
            report[3] = self.counter & 0xFF
            report[4] = 0x86  # setSectionLED
            report[5 : 5 + len(payload)] = bytes(payload)
            report[length - 1] = sum(report[: length - 1]) & 0xFF
            self._send_raw(bytes(report))
            self.counter = (self.counter + 1) & 0xFF
            # The controller drops back-to-back section reports.
            if offset + 55 < len(ranges):
                time.sleep(0.02)

    def set_dxlight_effect(self, effect_id: int) -> None:
        """Start one of DX-Light's built-in dynamic effects (IDs 0 through 6)."""
        if not 0 <= effect_id <= 6:
            raise ValueError("DX-Light effect ID must be between 0 and 6.")

        report = bytearray(64)
        report[0:7] = (0x52, 0x42, 0x08, self.counter & 0xFF, 0x85, 0x02, effect_id)
        report[7] = sum(report[:7]) & 0xFF
        self._send_raw(bytes(report))
        self.counter = (self.counter + 1) & 0xFF

    def stop_dxlight_effect(self) -> None:
        """Stop the active DX-Light hardware effect."""
        report = bytearray(64)
        report[0:6] = (0x52, 0x42, 0x06, self.counter & 0xFF, 0x97, 0x00)
        report[6] = sum(report[:6]) & 0xFF
        self._send_raw(bytes(report))
        self.counter = (self.counter + 1) & 0xFF

    def set_dxlight_speed(self, speed: int) -> None:
        """Set DX-Light dynamic-effect speed, from 0 (slow) to 100 (fast)."""
        speed = max(0, min(100, int(speed)))
        report = bytearray(64)
        report[0:6] = (0x52, 0x42, 0x07, self.counter & 0xFF, 0x8A, 100 - speed)
        report[6] = sum(report[:6]) & 0xFF
        self._send_raw(bytes(report))
        self.counter = (self.counter + 1) & 0xFF

    def set_led_count(self, count: int) -> None:
        """Configure the number of LEDs used by DX-Light, from 1 through 254."""
        if not 1 <= count <= 254:
            raise ValueError("LED count must be between 1 and 254.")
        report = bytearray(64)
        report[0:6] = (0x52, 0x42, 0x07, self.counter & 0xFF, 0x95, count)
        report[6] = sum(report[:6]) & 0xFF
        self._send_raw(bytes(report))
        self.counter = (self.counter + 1) & 0xFF

    def set_dxlight_rhythm(self, rhythm_id: int) -> None:
        """Start one of DX-Light's controller-rhythm presets (IDs 0 through 6)."""
        if not 0 <= rhythm_id <= 6:
            raise ValueError("DX-Light rhythm ID must be between 0 and 6.")
        report = bytearray(64)
        report[0:7] = (0x52, 0x42, 0x08, self.counter & 0xFF, 0x85, 0x03, rhythm_id)
        report[7] = sum(report[:7]) & 0xFF
        self._send_raw(bytes(report))
        self.counter = (self.counter + 1) & 0xFF


class RobobloqControllers:
    """Keep one protocol counter per connected controller while applying one color to all."""

    def __init__(self, devices: list[str] | None = None):
        self.controllers = [RobobloqController(dev) for dev in (devices or find_vendor_devices())]

    def set_color(self, r: int, g: int, b: int) -> None:
        for controller in self.controllers:
            controller.set_color(r, g, b)

    def set_dxlight_effect(self, effect_id: int) -> None:
        for controller in self.controllers:
            controller.set_dxlight_effect(effect_id)

    def stop_dxlight_effect(self) -> None:
        for controller in self.controllers:
            controller.stop_dxlight_effect()

    def set_dxlight_speed(self, speed: int) -> None:
        for controller in self.controllers:
            controller.set_dxlight_speed(speed)

    def set_led_count(self, count: int) -> None:
        for controller in self.controllers:
            controller.set_led_count(count)

    def set_dxlight_rhythm(self, rhythm_id: int) -> None:
        for controller in self.controllers:
            controller.set_dxlight_rhythm(rhythm_id)

def set_color(r: int, g: int, b: int, dev: str | None = None) -> None:
    """Convenience function: set a solid color."""
    if dev is not None:
        RobobloqController(dev=dev).set_color(r, g, b)
        return
    RobobloqControllers().set_color(r, g, b)
