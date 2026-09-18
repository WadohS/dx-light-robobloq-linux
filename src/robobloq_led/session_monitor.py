import subprocess

from .device import SESSION_LOCK_PATH, load_layout


BUS_NAME = "io.github.wadohs.RobobloqLed"
OBJECT_PATH = "/io/github/wadohs/RobobloqLed"
WALLPAPER_SYNC_SERVICE = "robobloq-wallpaper-sync.service"
HARDWARE_EFFECTS = {
    "dxlight-dynamix": 0,
    "dxlight-serpentin": 1,
    "dxlight-feu": 2,
    "dxlight-4": 3,
    "dxlight-5": 4,
    "dxlight-6": 5,
    "dxlight-7": 6,
}


def daemon_call(method: str, *arguments: int) -> None:
    subprocess.run(
        [
            "gdbus", "call", "--session", "--dest", BUS_NAME,
            "--object-path", OBJECT_PATH, "--method", f"{BUS_NAME}.{method}",
            *(str(argument) for argument in arguments),
        ],
        check=True,
    )


def set_wallpaper_sync(enabled: bool) -> None:
    subprocess.run(["systemctl", "--user", "start" if enabled else "stop", WALLPAPER_SYNC_SERVICE], check=False)


def set_session_locked(locked: bool) -> None:
    if locked:
        SESSION_LOCK_PATH.touch()
    else:
        SESSION_LOCK_PATH.unlink(missing_ok=True)


def apply_action(action: dict) -> None:
    mode = action.get("mode", "off")
    if mode == "off":
        set_wallpaper_sync(False)
        daemon_call("Off")
    elif mode == "sync":
        daemon_call("Stop")
        set_wallpaper_sync(True)
    elif mode == "effect":
        set_wallpaper_sync(False)
        effect = action.get("effect", "dxlight-dynamix")
        if effect in HARDWARE_EFFECTS:
            daemon_call("StartHardwareEffect", HARDWARE_EFFECTS[effect])
        elif isinstance(effect, str) and effect.startswith("dxlight-rhythm-"):
            try:
                rhythm_id = int(effect.removeprefix("dxlight-rhythm-"))
            except ValueError:
                rhythm_id = -1
            if 0 <= rhythm_id <= 6:
                daemon_call("StartRhythm", rhythm_id)


def is_locked() -> bool:
    output = subprocess.check_output(
        ["gdbus", "call", "--session", "--dest", "org.gnome.ScreenSaver", "--object-path", "/org/gnome/ScreenSaver", "--method", "org.gnome.ScreenSaver.GetActive"],
        text=True,
    )
    return "true" in output


def main() -> None:
    locked = is_locked()
    set_session_locked(locked)
    if locked:
        apply_action(load_layout()["session"]["lock"])
    monitor = subprocess.Popen(
        ["gdbus", "monitor", "--session", "--dest", "org.gnome.ScreenSaver", "--object-path", "/org/gnome/ScreenSaver"],
        stdout=subprocess.PIPE,
        text=True,
    )
    assert monitor.stdout is not None
    for line in monitor.stdout:
        if "ActiveChanged (true," in line:
            set_session_locked(True)
            apply_action(load_layout()["session"]["lock"])
        elif "ActiveChanged (false," in line:
            set_session_locked(False)
            apply_action(load_layout()["session"]["unlock"])
    raise RuntimeError("GNOME lock monitor exited unexpectedly")


if __name__ == "__main__":
    main()
