"""Session D-Bus service for the ROBOBLOQ DX-Light controller.

The daemon is intentionally the sole owner of ``RobobloqControllers`` so its
per-controller protocol counters cannot be interleaved by multiple clients.
"""

from __future__ import annotations

import signal
import threading
import traceback

from gi.repository import Gio, GLib, GLibUnix

from .device import RobobloqControllers, SESSION_LOCK_PATH


BUS_NAME = "io.github.wadohs.RobobloqLed"
OBJECT_PATH = "/io/github/wadohs/RobobloqLed"
INTERFACE_NAME = BUS_NAME

INTROSPECTION_XML = f"""
<node>
  <interface name="{INTERFACE_NAME}">
    <method name="SetColor">
      <arg name="r" type="i" direction="in"/>
      <arg name="g" type="i" direction="in"/>
      <arg name="b" type="i" direction="in"/>
      <arg name="brightness" type="i" direction="in"/>
    </method>
    <method name="SetScreenColors">
      <arg name="colors" type="a(iii)" direction="in"/>
    </method>
    <method name="Off"/>
    <method name="StartHardwareEffect"><arg name="effectId" type="i" direction="in"/></method>
    <method name="StartRhythm"><arg name="effectId" type="i" direction="in"/></method>
    <method name="SetSpeed"><arg name="speed" type="i" direction="in"/></method>
    <method name="Stop"/>
  </interface>
</node>
"""


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


class RobobloqDaemon:
    def __init__(self) -> None:
        self._controller: RobobloqControllers | None = None
        self._hid_lock = threading.RLock()
        self._last_screen_colors: list[tuple[int, int, int]] | None = None

    def _get_controller(self) -> RobobloqControllers:
        if self._controller is None:
            self._controller = RobobloqControllers()
        return self._controller

    def _stop_hardware_effect(self) -> None:
        # Send this unconditionally: effects persist in the controller after a restart.
        with self._hid_lock:
            if self._controller is not None:
                self._controller.stop_dxlight_effect()

    def set_color(self, r: int, g: int, b: int, brightness: int) -> None:
        self._stop_hardware_effect()
        scale = _clamp(brightness, 0, 100) / 100.0
        rgb = tuple(int(_clamp(channel, 0, 255) * scale) for channel in (r, g, b))
        with self._hid_lock:
            self._get_controller().set_color(*rgb)

    def off(self) -> None:
        self.set_color(0, 0, 0, 100)

    def start_hardware_effect(self, effect_id: int) -> None:
        with self._hid_lock:
            self._get_controller().set_dxlight_effect(int(effect_id))

    def start_rhythm(self, effect_id: int) -> None:
        with self._hid_lock:
            self._get_controller().set_dxlight_rhythm(int(effect_id))

    def set_speed(self, speed: int) -> None:
        with self._hid_lock:
            self._get_controller().set_dxlight_speed(_clamp(speed, 0, 100))

    def stop(self) -> None:
        self._stop_hardware_effect()

    def set_screen_colors(self, colors: list[tuple[int, int, int]]) -> None:
        if SESSION_LOCK_PATH.exists():
            return
        normalized = [tuple(_clamp(channel, 0, 255) for channel in rgb) for rgb in colors]
        with self._hid_lock:
            controllers = self._get_controller().controllers
            if len(normalized) != len(controllers):
                raise ValueError(f"Need {len(controllers)} screen colors; received {len(normalized)}.")
            for controller, rgb in zip(controllers, normalized):
                if len(rgb) != 3:
                    raise ValueError("Each screen color must contain red, green, and blue.")
                controller.set_color(*rgb)
        if normalized != self._last_screen_colors:
            print(f"DX-Light Shell colors={normalized}", flush=True)
            self._last_screen_colors = normalized

    def handle_method_call(self, _connection, _sender, _path, _interface, method, parameters, invocation) -> None:
        try:
            args = parameters.unpack()
            handlers = {
                "SetColor": lambda: self.set_color(*args),
                "SetScreenColors": lambda: self.set_screen_colors(*args),
                "Off": self.off,
                "StartHardwareEffect": lambda: self.start_hardware_effect(*args),
                "StartRhythm": lambda: self.start_rhythm(*args),
                "SetSpeed": lambda: self.set_speed(*args),
                "Stop": self.stop,
            }
            if method not in handlers:
                raise ValueError(f"Unknown method: {method}")
            handlers[method]()
            invocation.return_value(GLib.Variant("()", ()))
        except (OSError, RuntimeError, ValueError) as error:
            invocation.return_dbus_error(f"{INTERFACE_NAME}.Error", str(error))
        except Exception:
            traceback.print_exc()
            invocation.return_dbus_error(f"{INTERFACE_NAME}.Error", "Unexpected daemon error")


def main() -> None:
    daemon = RobobloqDaemon()
    node_info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
    loop = GLib.MainLoop()

    def on_bus_acquired(connection, _name) -> None:
        connection.register_object(OBJECT_PATH, node_info.interfaces[0], daemon.handle_method_call, None, None)

    owner_id = Gio.bus_own_name(
        Gio.BusType.SESSION, BUS_NAME, Gio.BusNameOwnerFlags.NONE, on_bus_acquired, None, None
    )

    def shutdown() -> bool:
        try:
            daemon.off()
        except Exception:
            traceback.print_exc()
        loop.quit()
        return GLib.SOURCE_REMOVE

    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, shutdown)
    GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, shutdown)
    try:
        loop.run()
    finally:
        daemon.off()
        Gio.bus_unown_name(owner_id)


if __name__ == "__main__":
    main()
