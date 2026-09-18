"""Session D-Bus service for the ROBOBLOQ DX-Light controller.

The daemon is intentionally the sole owner of ``RobobloqControllers`` so its
per-controller protocol counters cannot be interleaved by multiple clients.
"""

from __future__ import annotations

import threading
import time

from gi.repository import Gio, GLib

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
    <method name="Off"/>
    <method name="StartHardwareEffect"><arg name="effectId" type="i" direction="in"/></method>
    <method name="StartRhythm"><arg name="effectId" type="i" direction="in"/></method>
    <method name="SetSpeed"><arg name="speed" type="i" direction="in"/></method>
    <method name="Stop"/>
    <method name="StartScreenSync">
      <arg name="monitor" type="i" direction="in"/>
      <arg name="fps" type="i" direction="in"/>
      <arg name="thickness" type="i" direction="in"/>
      <arg name="downscale" type="i" direction="in"/>
      <arg name="alpha" type="d" direction="in"/>
      <arg name="threshold" type="i" direction="in"/>
    </method>
    <method name="StopScreenSync"/>
  </interface>
</node>
"""


def _clamp(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value)))


class RobobloqDaemon:
    def __init__(self) -> None:
        self._controller: RobobloqControllers | None = None
        self._hid_lock = threading.RLock()
        self._sync_stop = threading.Event()
        self._sync_thread: threading.Thread | None = None

    def _get_controller(self) -> RobobloqControllers:
        if self._controller is None:
            self._controller = RobobloqControllers()
        return self._controller

    def _stop_sync(self) -> None:
        self._sync_stop.set()
        thread = self._sync_thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=2)
            if thread.is_alive():
                raise RuntimeError("Screen sync did not stop within two seconds.")
        self._sync_thread = None
        self._sync_stop.clear()

    def _stop_hardware_effect(self) -> None:
        # Send this unconditionally: effects persist in the controller after a restart.
        with self._hid_lock:
            if self._controller is not None:
                self._controller.stop_dxlight_effect()

    def set_color(self, r: int, g: int, b: int, brightness: int) -> None:
        self._stop_sync()
        self._stop_hardware_effect()
        scale = _clamp(brightness, 0, 100) / 100.0
        rgb = tuple(int(_clamp(channel, 0, 255) * scale) for channel in (r, g, b))
        with self._hid_lock:
            self._get_controller().set_color(*rgb)

    def off(self) -> None:
        self.set_color(0, 0, 0, 100)

    def start_hardware_effect(self, effect_id: int) -> None:
        self._stop_sync()
        with self._hid_lock:
            self._get_controller().set_dxlight_effect(int(effect_id))

    def start_rhythm(self, effect_id: int) -> None:
        self._stop_sync()
        with self._hid_lock:
            self._get_controller().set_dxlight_rhythm(int(effect_id))

    def set_speed(self, speed: int) -> None:
        with self._hid_lock:
            self._get_controller().set_dxlight_speed(_clamp(speed, 0, 100))

    def stop(self) -> None:
        self._stop_sync()
        self._stop_hardware_effect()

    def start_screen_sync(
        self, monitor: int, fps: int, thickness: int, downscale: int, alpha: float, threshold: int
    ) -> None:
        try:
            import mss
            import numpy
        except ImportError as error:
            raise RuntimeError("Screen sync requires the mss and numpy packages.") from error
        self._stop_sync()
        monitor = int(monitor)
        with mss.mss() as capture:
            if monitor < 1 or monitor >= len(capture.monitors):
                raise ValueError(f"Invalid monitor index {monitor}. Available: 1..{len(capture.monitors) - 1}")
        config = (monitor, _clamp(fps, 1, 120), _clamp(thickness, 1, 400),
                  _clamp(downscale, 1, 16), max(0.0, min(1.0, float(alpha))),
                  _clamp(threshold, 0, 255 * 3))
        # Discover HID before acknowledging the asynchronous operation.
        with self._hid_lock:
            self._get_controller().stop_dxlight_effect()
        self._sync_stop.clear()
        self._sync_thread = threading.Thread(
            target=self._screen_sync_loop, args=config, name="robobloq-screen-sync", daemon=True
        )
        self._sync_thread.start()

    def _screen_sync_loop(
        self, monitor: int, fps: int, thickness: int, downscale: int, alpha: float, threshold: int
    ) -> None:
        import mss
        import numpy as np

        smooth = np.zeros(3, dtype=np.float32)
        last_rgb = (-1, -1, -1)
        interval = 1.0 / fps
        try:
            with mss.mss() as capture:
                screen = capture.monitors[monitor]
                while not self._sync_stop.is_set():
                    started = time.monotonic()
                    if not SESSION_LOCK_PATH.exists():
                        frame = np.array(capture.grab(screen))[:, :, :3][:, :, ::-1]
                        image = frame[::downscale, ::downscale, :]
                        height, width, _ = image.shape
                        edge = max(1, min(thickness, width // 4, height // 4))
                        target = (0.25 * image[:, :edge].mean(axis=(0, 1))
                                  + 0.50 * image[:edge, :].mean(axis=(0, 1))
                                  + 0.25 * image[:, width - edge:].mean(axis=(0, 1)))
                        smooth = (1.0 - alpha) * smooth + alpha * target
                        rgb = tuple(np.clip(smooth, 0, 255).astype(int))
                        if sum(abs(a - b) for a, b in zip(rgb, last_rgb)) > threshold:
                            with self._hid_lock:
                                if not self._sync_stop.is_set():
                                    self._get_controller().set_color(*rgb)
                                    last_rgb = rgb
                    self._sync_stop.wait(max(0.0, interval - (time.monotonic() - started)))
        finally:
            # Do not clear the shared stop event: a replacement sync may already use it.
            pass

    def handle_method_call(self, _connection, _sender, _path, _interface, method, parameters, invocation) -> None:
        try:
            args = parameters.unpack()
            handlers = {
                "SetColor": lambda: self.set_color(*args),
                "Off": self.off,
                "StartHardwareEffect": lambda: self.start_hardware_effect(*args),
                "StartRhythm": lambda: self.start_rhythm(*args),
                "SetSpeed": lambda: self.set_speed(*args),
                "Stop": self.stop,
                "StartScreenSync": lambda: self.start_screen_sync(*args),
                "StopScreenSync": self._stop_sync,
            }
            if method not in handlers:
                raise ValueError(f"Unknown method: {method}")
            handlers[method]()
            invocation.return_value(GLib.Variant("()", ()))
        except (OSError, RuntimeError, ValueError) as error:
            invocation.return_dbus_error(f"{INTERFACE_NAME}.Error", str(error))
        except Exception:
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
    try:
        loop.run()
    finally:
        daemon.stop()
        Gio.bus_unown_name(owner_id)


if __name__ == "__main__":
    main()
