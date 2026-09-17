import json
import subprocess
import urllib.request

from .device import SESSION_LOCK_PATH, load_layout


API_URL = "http://127.0.0.1:8000"
WALLPAPER_SYNC_SERVICE = "robobloq-wallpaper-sync.service"


def post(path: str, payload: dict | None = None) -> None:
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(f"{API_URL}{path}", data=body, method="POST")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=3):
        pass


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
        post("/api/off")
    elif mode == "sync":
        post("/api/effect/stop")
        set_wallpaper_sync(True)
    elif mode == "effect":
        set_wallpaper_sync(False)
        post("/api/effect/start", {"effect": action.get("effect", "dxlight-dynamix")})


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
