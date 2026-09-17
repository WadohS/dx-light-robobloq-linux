import mss
import time
import asyncio
import os
import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from .device import RobobloqControllers, SESSION_LOCK_PATH, discover_vendor_devices, load_layout, save_layout

app = FastAPI(title="Robobloq LED Controller")

# Single controller instance so counter increments properly
_controller: RobobloqControllers | None = None
_current = {"r": 255, "g": 200, "b": 120}  # assume warm-white start
_fade_task: asyncio.Task | None = None
_lock = asyncio.Lock()
_effect_task: asyncio.Task | None = None
_effect_stop = asyncio.Event()
_dxlight_effect: int | None = None
_sync_tasks: list[asyncio.Task] = []
_sync_stop = asyncio.Event()

def get_controller() -> RobobloqControllers:
    global _controller
    if _controller is None:
        _controller = RobobloqControllers()
    return _controller

class Color(BaseModel):
    r: int
    g: int
    b: int
    brightness: int | None = 100  # 0..100

class FadeRequest(BaseModel):
    r: int
    g: int
    b: int
    brightness: int | None = 100  # 0..100
    duration_ms: int = 800        # fade time
    steps: int = 40               # number of steps

class EffectRequest(BaseModel):
    effect: str              # "pulse" or "rainbow"
    speed: int = 50          # 1..100
    r: int | None = None     # pulse uses selected color (after brightness)
    g: int | None = None
    b: int | None = None
    brightness: int | None = 100

class SpeedRequest(BaseModel):
    speed: int

class LedCountRequest(BaseModel):
    count: int


class ZoneLayout(BaseModel):
    left: int
    top: int
    right: int
    bottom: int = 0


class DisplayLayout(BaseModel):
    device: str
    screen: str
    location: str
    installation_direction: str
    sync_area: str
    edge_count: int
    zones: ZoneLayout


class SessionAction(BaseModel):
    mode: str
    effect: str = "dxlight-dynamix"


class SessionBehavior(BaseModel):
    lock: SessionAction = Field(default_factory=lambda: SessionAction(mode="off"))
    unlock: SessionAction = Field(default_factory=lambda: SessionAction(mode="sync"))


class LayoutRequest(BaseModel):
    version: int = 2
    session: SessionBehavior = Field(default_factory=SessionBehavior)
    displays: list[DisplayLayout]

class SyncStartRequest(BaseModel):
    monitor: int = 2
    fps: int = 60
    thickness: int = 80
    downscale: int = 4          # sample every Nth pixel (1=no downscale)
    alpha: float = 0.35         # smoothing
    change_threshold: int = 6   # don't spam USB for tiny changes


def clamp(v: int, lo: int, hi: int) -> int:
    return lo if v < lo else hi if v > hi else v

def apply_brightness(r: int, g: int, b: int, brightness: int) -> tuple[int,int,int]:
    brightness = clamp(brightness, 0, 100)
    scale = brightness / 100.0
    return (int(r * scale), int(g * scale), int(b * scale))

def cancel_fade():
    global _fade_task
    if _fade_task and not _fade_task.done():
        _fade_task.cancel()
    _fade_task = None

def cancel_effect():
    global _effect_task
    if _effect_task and not _effect_task.done():
        _effect_stop.set()
        _effect_task.cancel()
    _effect_task = None
    _effect_stop.clear()

def stop_dxlight_effect():
    global _dxlight_effect
    # The controller keeps running an effect across API restarts, so always
    # send its stop command instead of relying on the in-memory state.
    get_controller().stop_dxlight_effect()
    _dxlight_effect = None

def cancel_sync():
    global _sync_tasks
    if any(not task.done() for task in _sync_tasks):
        _sync_stop.set()
        for task in _sync_tasks:
            task.cancel()
    _sync_tasks = []
    _sync_stop.clear()

def wheel(pos: int) -> tuple[int, int, int]:
    # Classic rainbow wheel (0..255)
    pos = pos % 256
    if pos < 85:
        return (pos * 3, 255 - pos * 3, 0)
    if pos < 170:
        pos -= 85
        return (255 - pos * 3, 0, pos * 3)
    pos -= 170
    return (0, pos * 3, 255 - pos * 3)

async def effect_pulse(base: dict, speed: int):
    """
    Pulse between OFF and base color.
    speed: 1..100 (higher = faster)
    """
    ctl = get_controller()
    speed = clamp(speed, 1, 100)
    # period in seconds (fastest ~0.4s, slowest ~3.0s)
    period = 3.0 - (speed - 1) * (2.6 / 99.0)
    steps = 50
    dt = period / steps

    async with _lock:
        while not _effect_stop.is_set():
            # up
            for i in range(steps + 1):
                if _effect_stop.is_set(): return
                t = i / steps
                r = round(base["r"] * t)
                g = round(base["g"] * t)
                b = round(base["b"] * t)
                ctl.set_color(r, g, b)
                _current["r"], _current["g"], _current["b"] = r, g, b
                await asyncio.sleep(dt)
            # down
            for i in range(steps, -1, -1):
                if _effect_stop.is_set(): return
                t = i / steps
                r = round(base["r"] * t)
                g = round(base["g"] * t)
                b = round(base["b"] * t)
                ctl.set_color(r, g, b)
                _current["r"], _current["g"], _current["b"] = r, g, b
                await asyncio.sleep(dt)

async def effect_rainbow(speed: int):
    """
    Rainbow cycle.
    speed: 1..100 (higher = faster)
    """
    ctl = get_controller()
    speed = clamp(speed, 1, 100)
    # delay per step (fastest ~0.01s, slowest ~0.10s)
    delay = 0.10 - (speed - 1) * (0.09 / 99.0)

    async with _lock:
        j = 0
        while not _effect_stop.is_set():
            r, g, b = wheel(j)
            ctl.set_color(r, g, b)
            _current["r"], _current["g"], _current["b"] = r, g, b
            j = (j + 1) % 256
            await asyncio.sleep(delay)

async def effect_directional_rainbow(speed: int):
    """Move a rainbow through every LED, following each strip's physical direction."""
    controllers = get_controller().controllers
    displays = load_layout()["displays"]
    speed = clamp(speed, 1, 100)
    delay = 0.20 - (speed - 1) * (0.16 / 99.0)
    phase = 0

    async with _lock:
        while not _effect_stop.is_set():
            for controller, display in zip(controllers, displays):
                zones = display["zones"]
                led_count = sum(zones.values())
                zone_size = min(6, led_count)
                zone_count = (led_count + zone_size - 1) // zone_size
                direction = -1 if display["installation_direction"] == "right-to-left" else 1
                pixels = [wheel(phase + direction * (led // zone_size) * 256 // zone_count) for led in range(led_count)]
                await asyncio.to_thread(controller.set_pixels, pixels)
            phase = (phase + 6) % 256
            await asyncio.sleep(delay)

async def effect_rainbow_chase(speed: int):
    controllers = get_controller().controllers
    displays = load_layout()["displays"]
    speed = clamp(speed, 1, 100)
    delay = 0.20 - (speed - 1) * (0.16 / 99.0)
    position = 0

    async with _lock:
        while not _effect_stop.is_set():
            for controller, display in zip(controllers, displays):
                zones = display["zones"]
                led_count = sum(zones.values())
                zone_size = min(6, led_count)
                zone_count = (led_count + zone_size - 1) // zone_size
                active = position if display["installation_direction"] == "left-to-right" else zone_count - 1 - position
                pixels = [(0, 0, 0)] * led_count
                color = wheel(position * 256 // zone_count)
                first = active * zone_size
                pixels[first : first + zone_size] = [color] * min(zone_size, led_count - first)
                await asyncio.to_thread(controller.set_pixels, pixels)
            position = (position + 1) % max((sum(display["zones"].values()) + 5) // 6 for display in displays)
            await asyncio.sleep(delay)

async def effect_aurora_wave(speed: int):
    controllers = get_controller().controllers
    displays = load_layout()["displays"]
    speed = clamp(speed, 1, 100)
    delay = 0.20 - (speed - 1) * (0.16 / 99.0)
    phase = 0

    async with _lock:
        while not _effect_stop.is_set():
            for controller, display in zip(controllers, displays):
                zones = display["zones"]
                led_count = sum(zones.values())
                zone_size = min(6, led_count)
                zone_count = (led_count + zone_size - 1) // zone_size
                direction = -1 if display["installation_direction"] == "right-to-left" else 1
                pixels = []
                for led in range(led_count):
                    level = (phase + direction * (led // zone_size) * 256 // zone_count) % 256
                    pixels.append((0, level // 2, 40 + level * 3 // 4))
                await asyncio.to_thread(controller.set_pixels, pixels)
            phase = (phase + 8) % 256
            await asyncio.sleep(delay)

async def fade_to(target: dict, duration_ms: int, steps: int):
    ctl = get_controller()
    duration_ms = clamp(duration_ms, 0, 60_000)
    steps = clamp(steps, 1, 300)

    async with _lock:
        start = dict(_current)

        for i in range(1, steps + 1):
            t = i / steps
            r = round(start["r"] + (target["r"] - start["r"]) * t)
            g = round(start["g"] + (target["g"] - start["g"]) * t)
            b = round(start["b"] + (target["b"] - start["b"]) * t)

            ctl.set_color(r, g, b)
            _current["r"], _current["g"], _current["b"] = r, g, b

            await asyncio.sleep(duration_ms / steps / 1000.0)

async def screen_sync_loop(cfg: SyncStartRequest, ctl):

    fps = clamp(cfg.fps, 1, 120)
    thickness = clamp(cfg.thickness, 1, 400)
    down = clamp(cfg.downscale, 1, 16)
    alpha = float(cfg.alpha)
    if alpha < 0.0: alpha = 0.0
    if alpha > 1.0: alpha = 1.0
    change_thr = clamp(cfg.change_threshold, 0, 255*3)

    dt = 1.0 / fps

    # State for smoothing + change threshold
    smooth = np.array([0.0, 0.0, 0.0], dtype=np.float32)
    last_rgb = (-1, -1, -1)

    def avg_edge_color(img: np.ndarray, thickness_px: int):
        h, w, _ = img.shape
        t = max(1, min(thickness_px, w // 4, h // 4))

        left = img[:, :t, :]
        top = img[:t, :, :]
        right = img[:, w - t :, :]

        l = left.mean(axis=(0, 1))
        tcol = top.mean(axis=(0, 1))
        r = right.mean(axis=(0, 1))
        return l, tcol, r

    def combine_edges(l, tcol, r):
        # Weighted towards top (movie-friendly)
        return 0.25*l + 0.50*tcol + 0.25*r

    # IMPORTANT: mss is best created once per task
    with mss.mss() as sct:
        if cfg.monitor < 1 or cfg.monitor >= len(sct.monitors):
            raise ValueError(f"Invalid monitor index {cfg.monitor}. Available: 1..{len(sct.monitors)-1}")

        mon = sct.monitors[cfg.monitor]

        while not _sync_stop.is_set():
            start = time.time()
            if SESSION_LOCK_PATH.exists():
                await asyncio.sleep(dt)
                continue

            frame = np.array(sct.grab(mon))[:, :, :3][:, :, ::-1]  # RGB
            img = frame[::down, ::down, :]  # downscale for speed

            l, tcol, r = avg_edge_color(img, thickness_px=thickness)
            target = combine_edges(l, tcol, r)

            # smoothing
            smooth = (1.0 - alpha) * smooth + alpha * target.astype(np.float32)
            rgb = tuple(np.clip(smooth, 0, 255).astype(int))

            # reduce USB spam
            if not SESSION_LOCK_PATH.exists() and sum(abs(a - b) for a, b in zip(rgb, last_rgb)) > change_thr:
                ctl.set_color(*rgb)
                _current["r"], _current["g"], _current["b"] = rgb
                last_rgb = rgb

            elapsed = time.time() - start
            sleep_for = dt - elapsed
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            else:
                # If capture takes longer than one frame, yield to the other monitor task.
                await asyncio.sleep(0)


HTML_PAGE = r"""
<!doctype html>
<html lang="__LANG__">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>DX-Light Configuration</title>
  <style>
    :root{
      --bg:#0b0f17;
      --card:rgba(255,255,255,.06);
      --card2:rgba(255,255,255,.10);
      --border:rgba(255,255,255,.12);
      --text:rgba(255,255,255,.92);
      --muted:rgba(255,255,255,.60);
      --shadow:0 18px 60px rgba(0,0,0,.45);
      --r:18px;
      --accent:#6ae4ff;
      --bad:#ff5b6e;
    }
    @media (prefers-color-scheme: light) {
      :root{
        --bg:#f6f7fb;
        --card:rgba(0,0,0,.04);
        --card2:rgba(0,0,0,.06);
        --border:rgba(0,0,0,.10);
        --text:rgba(0,0,0,.88);
        --muted:rgba(0,0,0,.60);
        --shadow:0 18px 60px rgba(0,0,0,.12);
        --accent:#0ea5e9;
        --bad:#e11d48;
      }
    }
    *{box-sizing:border-box}
    body{
      margin:0;
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Arial;
      color:var(--text);
      background:
        radial-gradient(1200px 600px at 20% 0%, rgba(106,228,255,0.20), transparent 60%),
        radial-gradient(900px 500px at 80% 10%, rgba(255,204,102,0.16), transparent 62%),
        radial-gradient(900px 700px at 55% 100%, rgba(72,227,155,0.14), transparent 60%),
        var(--bg);
    }
    .wrap{max-width:760px;margin:26px auto;padding:0 16px 34px}
    .top{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-bottom:14px}
    .brand{display:flex;gap:12px;align-items:center}
    .logo{width:42px;height:42px;border-radius:14px;background:linear-gradient(135deg, rgba(106,228,255,.9), rgba(255,204,102,.8));box-shadow:0 12px 30px rgba(106,228,255,.25)}
    h1{margin:0;font-size:18px}
    .sub{margin:2px 0 0;font-size:13px;color:var(--muted)}
    .pill{font-size:12px;color:var(--muted);border:1px solid var(--border);background:var(--card);padding:8px 10px;border-radius:999px;backdrop-filter:blur(10px)}
    .card{border:1px solid var(--border);background:var(--card);border-radius:var(--r);padding:16px;box-shadow:var(--shadow);backdrop-filter:blur(12px)}
    .tabs{display:flex;gap:8px;margin-bottom:12px}
    .tab{padding:10px 12px;border-radius:12px;border:1px solid var(--border);background:var(--card2);cursor:pointer;user-select:none}
    .tab.active{border-color:rgba(106,228,255,.35);background:linear-gradient(135deg, rgba(106,228,255,.18), rgba(106,228,255,.06))}
    .row{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
    .btn{border:1px solid var(--border);background:var(--card2);color:var(--text);padding:10px 12px;border-radius:12px;cursor:pointer}
    .btn.primary{border-color:rgba(106,228,255,.35);background:linear-gradient(135deg, rgba(106,228,255,.18), rgba(106,228,255,.06))}
    .btn.danger{border-color:rgba(255,91,110,.35);background:linear-gradient(135deg, rgba(255,91,110,.16), rgba(255,91,110,.06))}
    .picker{display:flex;align-items:center;gap:10px;padding:10px;border-radius:14px;border:1px solid var(--border);background:var(--card2)}
    input[type=color]{width:54px;height:44px;border:none;background:none;padding:0;cursor:pointer}
    .label{min-width:92px;font-size:13px;color:var(--muted)}
    .value{min-width:70px;font-size:13px}
    input[type=range]{width:280px;accent-color:var(--accent)}
    select,input[type=number]{color:var(--text);background:var(--card2);border:1px solid var(--border);border-radius:8px;padding:7px}
    .config-grid{display:grid;grid-template-columns:repeat(2,minmax(180px,1fr));gap:10px;margin-top:10px}
    .config-field{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:9px;border:1px solid var(--border);border-radius:10px;background:var(--card2);font-size:13px}
    @media(max-width:560px){.config-grid{grid-template-columns:1fr}}
    .presets{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}
    .preset{display:flex;align-items:center;gap:8px;padding:9px 10px;border-radius:999px;border:1px solid var(--border);background:var(--card2);color:var(--text);cursor:pointer;font-size:13px}
    .dot{width:12px;height:12px;border-radius:50%;border:1px solid rgba(255,255,255,.22);flex:none}
    .toast{margin-top:12px;padding:10px 12px;border-radius:14px;border:1px solid var(--border);background:rgba(0,0,0,.18);color:var(--muted);min-height:44px;display:flex;align-items:center;justify-content:space-between;gap:10px;backdrop-filter:blur(10px)}
    @media (prefers-color-scheme: light){ .toast{background:rgba(255,255,255,.55)} }
    code{background:rgba(255,255,255,.10);padding:2px 6px;border-radius:8px}
    @media (prefers-color-scheme: light){ code{background:rgba(0,0,0,.08)} }
    .hint{margin-top:10px;font-size:12px;color:rgba(255,255,255,.45);line-height:1.35}
    @media (prefers-color-scheme: light){ .hint{color:rgba(0,0,0,.45)} }
    .hidden{display:none}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <div class="brand">
        <div class="logo"></div>
        <div>
          <h1>DX-Light Configuration</h1>
          <div class="sub">Contrôleur local Linux (1a86:fe07)</div>
        </div>
      </div>
      <div class="pill" id="conn">Ready</div>
    </div>

    <div class="card">
      <div class="tabs">
        <div class="tab active" id="tab-colors" onclick="showTab('colors')">Colors</div>
        <div class="tab" id="tab-effects" onclick="showTab('effects')">Effects</div>
        <div class="tab" id="tab-sync" onclick="showTab('sync')">Sync</div>
        <div class="tab" id="tab-config" onclick="showTab('config')">Configuration</div>
      </div>

      <!-- Colors tab -->
      <div id="panel-colors">
        <div class="row">
          <div class="picker">
            <input id="picker" type="color" value="#ffc878" oninput="syncLabels()" />
            <div>
              <div class="label">Selected</div>
              <div class="value" id="hexLabel">#FFC878</div>
            </div>
          </div>
          <button class="btn primary" onclick="applyInstant()">Apply</button>
          <button class="btn" onclick="applyFade()">Fade</button>
          <button class="btn danger" onclick="turnOff()">Off</button>
          <button class="btn" onclick="stopAll()">Stop</button>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Brightness</div>
          <input id="bright" type="range" min="0" max="100" value="100" oninput="syncLabels()">
          <div class="value" id="brightVal">100%</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Fade time</div>
          <input id="duration" type="range" min="0" max="3000" value="800" oninput="syncLabels()">
          <div class="value" id="durVal">800ms</div>
        </div>

        <div style="height:12px"></div>

        <div class="presets">
          <div class="preset" onclick="preset('#ffc878','Warm')"><span class="dot" style="background:#ffc878"></span>Warm</div>
          <div class="preset" onclick="preset('#d8ecff','Cool')"><span class="dot" style="background:#d8ecff"></span>Cool</div>
          <div class="preset" onclick="preset('#ffffff','White')"><span class="dot" style="background:#ffffff"></span>White</div>
          <div class="preset" onclick="preset('#ff3b30','Red')"><span class="dot" style="background:#ff3b30"></span>Red</div>
          <div class="preset" onclick="preset('#34c759','Green')"><span class="dot" style="background:#34c759"></span>Green</div>
          <div class="preset" onclick="preset('#0a84ff','Blue')"><span class="dot" style="background:#0a84ff"></span>Blue</div>
          <div class="preset" onclick="preset('#b400ff','Purple')"><span class="dot" style="background:#b400ff"></span>Purple</div>
        </div>
      </div>

      <!-- Effects tab -->
      <div id="panel-effects" class="hidden">
        <div class="row">
          <div class="label">Effect</div>
          <button class="preset" data-effect="pulse" onclick="selectEffect('pulse')"><span class="dot" style="background:#ffc878"></span>Pulse</button>
          <button class="preset" data-effect="rainbow" onclick="selectEffect('rainbow')"><span class="dot" style="background:#0a84ff"></span>Rainbow</button>
        </div>

        <div style="height:12px"></div>

        <div class="label">Effets DX-Light</div>
        <div class="presets">
          <button class="preset" data-effect="dxlight-dynamix" onclick="selectEffect('dxlight-dynamix')">Dynamix</button>
          <button class="preset" data-effect="dxlight-serpentin" onclick="selectEffect('dxlight-serpentin')">Serpentin</button>
          <button class="preset" data-effect="dxlight-feu" onclick="selectEffect('dxlight-feu')">Feu</button>
          <button class="preset" data-effect="dxlight-4" onclick="selectEffect('dxlight-4')">Météore</button>
          <button class="preset" data-effect="dxlight-5" onclick="selectEffect('dxlight-5')">Scintillement</button>
          <button class="preset" data-effect="dxlight-6" onclick="selectEffect('dxlight-6')">Dégradé</button>
          <button class="preset" data-effect="dxlight-7" onclick="selectEffect('dxlight-7')">Défilement</button>
        </div>

        <div style="height:12px"></div>

        <div class="label">Rythme contrôleur</div>
        <div class="presets">
          <button class="preset" data-effect="dxlight-rhythm-0" onclick="selectEffect('dxlight-rhythm-0')">Onde</button>
          <button class="preset" data-effect="dxlight-rhythm-1" onclick="selectEffect('dxlight-rhythm-1')">Pulsation</button>
          <button class="preset" data-effect="dxlight-rhythm-2" onclick="selectEffect('dxlight-rhythm-2')">Spectre</button>
          <button class="preset" data-effect="dxlight-rhythm-3" onclick="selectEffect('dxlight-rhythm-3')">Flash</button>
          <button class="preset" data-effect="dxlight-rhythm-4" onclick="selectEffect('dxlight-rhythm-4')">Dégradé</button>
          <button class="preset" data-effect="dxlight-rhythm-5" onclick="selectEffect('dxlight-rhythm-5')">Chenillard</button>
          <button class="preset" data-effect="dxlight-rhythm-6" onclick="selectEffect('dxlight-rhythm-6')">Arc-en-ciel</button>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Vitesse DX-Light</div>
          <input id="speed" type="range" min="1" max="100" value="50" oninput="syncLabels()">
          <div class="value" id="speedVal">50</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <button class="btn primary" onclick="startEffect()">Start</button>
          <button class="btn" onclick="stopAll()">Stop</button>
          <button class="btn danger" onclick="turnOff()">Off</button>
          <div class="hint">Pulse uses the selected color (from Colors tab) + brightness.</div>
        </div>
      </div>

      <!-- Sync tab -->
      <div id="panel-sync" class="hidden">
        <div class="row">
          <div class="label">Monitor</div>
          <input id="syncMonitor" type="number" min="1" value="2" style="width:90px">
          <div class="label">FPS</div>
          <input id="syncFps" type="range" min="5" max="90" value="60" oninput="syncLabels()">
          <div class="value" id="syncFpsVal">60</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Thickness</div>
          <input id="syncThickness" type="range" min="10" max="250" value="80" oninput="syncLabels()">
          <div class="value" id="syncThicknessVal">80px</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Downscale</div>
          <input id="syncDown" type="range" min="1" max="10" value="4" oninput="syncLabels()">
          <div class="value" id="syncDownVal">4x</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Smoothing</div>
          <input id="syncAlpha" type="range" min="0" max="100" value="35" oninput="syncLabels()">
          <div class="value" id="syncAlphaVal">0.35</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <div class="label">Change threshold</div>
          <input id="syncThr" type="range" min="0" max="60" value="6" oninput="syncLabels()">
          <div class="value" id="syncThrVal">6</div>
        </div>

        <div style="height:12px"></div>

        <div class="row">
          <button class="btn primary" onclick="startSync()">Start Sync</button>
          <button class="btn" onclick="stopSync()">Stop Sync</button>
          <button class="btn" onclick="stopAll()">Stop All</button>
          <button class="btn danger" onclick="turnOff()">Off</button>
        </div>

        <div class="hint">
          Use <code>Monitor</code> = the MSS index that matches your external display (you found it was 2).<br>
          Downscale 4x is a good default. Lower downscale = better quality but more CPU.
        </div>
      </div>

      <div id="panel-config" class="hidden">
        <div class="hint">La même configuration est utilisée par les préférences GNOME. Un bandeau est découpé en trois ou quatre zones autour de son écran.</div>
        <div id="configRoot" class="hint">Chargement de la configuration DX-Light…</div>
      </div>

      <div class="toast">
        <div><strong>Status:</strong> <span id="status">Ready.</span></div>
        <div><span id="mini">Brightness 100% • Fade 800ms • Speed 50</span></div>
      </div>

      <div class="hint">
        Phone access: run with <code>--host 0.0.0.0</code> and open <code>http://&lt;PC_IP&gt;:8000</code>.<br>
        Tip: Use <code>Stop</code> if an effect is running and you want manual control.
      </div>
    </div>
  </div>
  

<script>
const LOCALE = "__LANG__";
const TEXT = {
  "Ready": ["Ready", "Prêt"], "Working…": ["Working…", "En cours…"], "Error": ["Error", "Erreur"],
  "Colors": ["Colors", "Couleurs"], "Effects": ["Effects", "Effets"], "Sync": ["Sync", "Synchronisation"],
  "Selected": ["Selected", "Sélection"], "Apply": ["Apply", "Appliquer"], "Fade": ["Fade", "Fondu"], "Off": ["Off", "Éteindre"], "Stop": ["Stop", "Arrêter"],
  "Brightness": ["Brightness", "Luminosité"], "Fade time": ["Fade time", "Durée du fondu"], "Effect": ["Effect", "Effet"], "Pulse": ["Pulse", "Pulsation"], "Rainbow": ["Rainbow", "Arc-en-ciel"],
  "Vitesse DX-Light": ["DX-Light speed", "Vitesse DX-Light"], "Start": ["Start", "Démarrer"], "Monitor": ["Monitor", "Écran"], "Thickness": ["Thickness", "Épaisseur"], "Downscale": ["Downscale", "Réduction"], "Smoothing": ["Smoothing", "Lissage"], "Change threshold": ["Change threshold", "Seuil de changement"],
  "Start Sync": ["Start sync", "Démarrer la synchronisation"], "Stop Sync": ["Stop sync", "Arrêter la synchronisation"], "Stop All": ["Stop all", "Tout arrêter"], "Status:": ["Status:", "État :"],
  "Configuration": ["Configuration", "Configuration"], "Contrôleur local Linux (1a86:fe07)": ["Local Linux controller (1a86:fe07)", "Contrôleur local Linux (1a86:fe07)"],
  "Warm": ["Warm", "Chaud"], "Cool": ["Cool", "Froid"], "White": ["White", "Blanc"], "Red": ["Red", "Rouge"], "Green": ["Green", "Vert"], "Blue": ["Blue", "Bleu"], "Purple": ["Purple", "Violet"],
  "Rythme contrôleur": ["Controller rhythm", "Rythme contrôleur"], "Météore": ["Meteor", "Météore"], "Scintillement": ["Twinkle", "Scintillement"], "Dégradé": ["Gradient", "Dégradé"], "Défilement": ["Scrolling", "Défilement"], "Feu": ["Fire", "Feu"], "Onde": ["Wave", "Onde"], "Pulsation": ["Pulse", "Pulsation"], "Spectre": ["Spectrum", "Spectre"], "Chenillard": ["Chaser", "Chenillard"], "Arc-en-ciel": ["Rainbow", "Arc-en-ciel"],
  "Chargement de la configuration DX-Light…": ["Loading DX-Light configuration…", "Chargement de la configuration DX-Light…"], "Session GNOME": ["GNOME session", "Session GNOME"], "Au verrouillage": ["On lock", "Au verrouillage"], "Au déverrouillage": ["On unlock", "Au déverrouillage"], "Contrôleur": ["Controller", "Contrôleur"], "Écran gauche": ["Left display", "Écran gauche"], "Écran droit": ["Right display", "Écran droit"],
  "Éteindre": ["Turn off", "Éteindre"], "Sync fond": ["Wallpaper sync", "Sync fond"], "Effet DX-Light": ["DX-Light effect", "Effet DX-Light"], "Appliquer": ["Apply", "Appliquer"]
};
function t(text) { return TEXT[text]?.[LOCALE === "fr" ? 1 : 0] || text; }
function localize(root = document) {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const value = node.nodeValue.trim();
    if (value && TEXT[value]) node.nodeValue = node.nodeValue.replace(value, t(value));
  }
}
let selectedEffect = "pulse";

function setConn(text){ document.getElementById("conn").textContent = t(text); }
function setStatus(text){ document.getElementById("status").textContent = t(text); }

function showTab(name){
  document.getElementById("panel-colors").classList.toggle("hidden", name !== "colors");
  document.getElementById("panel-effects").classList.toggle("hidden", name !== "effects");
  document.getElementById("panel-sync").classList.toggle("hidden", name !== "sync");
  document.getElementById("panel-config").classList.toggle("hidden", name !== "config");

  document.getElementById("tab-colors").classList.toggle("active", name === "colors");
  document.getElementById("tab-effects").classList.toggle("active", name === "effects");
  document.getElementById("tab-sync").classList.toggle("active", name === "sync");
  document.getElementById("tab-config").classList.toggle("active", name === "config");
  if (name === "config") loadConfiguration();
}

async function configRequest(method, url, body = null) {
  const options = {method, headers: {"Content-Type": "application/json"}};
  if (body !== null) options.body = JSON.stringify(body);
  const response = await fetch(url, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Erreur de configuration");
  return data;
}

function configOption(value, label, selected) {
  return `<option value="${value}" ${value === selected ? "selected" : ""}>${label}</option>`;
}

function sessionEffectOptions(selected) {
  const effects = [
    ["dxlight-dynamix", "Dynamix"], ["dxlight-serpentin", "Serpentin"], ["dxlight-feu", "Feu"],
    ["dxlight-4", "Météore"], ["dxlight-5", "Scintillement"], ["dxlight-6", "Dégradé"], ["dxlight-7", "Défilement"],
    ...Array.from({length: 7}, (_value, index) => [`dxlight-rhythm-${index}`, `Rythme ${index + 1}`]),
  ];
  return effects.map(([value, label]) => configOption(value, label, selected)).join("");
}

async function loadConfiguration() {
  const root = document.getElementById("configRoot");
  root.textContent = "Chargement de la configuration DX-Light…";
  try {
    const [deviceResponse, layoutResponse] = await Promise.all([
      configRequest("GET", "/api/devices"), configRequest("GET", "/api/layout")
    ]);
    const devices = deviceResponse.devices;
    const session = layoutResponse.layout.session;
    const sessionAction = (phase, action, title) => `<label class="config-field">${title}<select id="session-${phase}-mode" onchange="updateSessionEffect('${phase}')">${configOption("off", "Éteindre", action.mode)}${configOption("sync", "Sync fond", action.mode)}${configOption("effect", "Effet DX-Light", action.mode)}</select></label><label class="config-field">${title} : effet<select id="session-${phase}-effect">${sessionEffectOptions(action.effect)}</select></label>`;
    root.innerHTML = layoutResponse.layout.displays.map((display, index) => {
      const deviceOptions = devices.map(device => configOption(device, device.replace("/dev/input/by-path/", "USB "), display.device)).join("");
      const zone = (name, title) => `<label class="config-field">${title}<input id="zone-${index}-${name}" type="number" min="0" max="254" value="${display.zones[name]}"></label>`;
      return `<div class="card" style="margin-top:12px"><strong>Écran ${display.screen === "right" ? "droit" : "gauche"}</strong>
        <div class="config-grid">
          <label class="config-field">Contrôleur<select id="device-${index}">${deviceOptions}</select></label>
          <label class="config-field">Écran<select id="screen-${index}">${configOption("left", "Gauche", display.screen)}${configOption("right", "Droit", display.screen)}</select></label>
          <label class="config-field">Pose<select id="location-${index}">${configOption("back", "Derrière l’écran", display.location)}${configOption("top", "Au-dessus", display.location)}${configOption("bottom", "Sous l’écran", display.location)}${configOption("left", "À gauche", display.location)}${configOption("right", "À droite", display.location)}</select></label>
          <label class="config-field">Arrivée LEDs<select id="direction-${index}">${configOption("left-to-right", "Gauche vers droite", display.installation_direction)}${configOption("right-to-left", "Droite vers gauche", display.installation_direction)}</select></label>
          <label class="config-field">Prélèvement<select id="sync-area-${index}">${configOption("edge", "Bord de l’écran", display.sync_area)}${configOption("center", "Centre de l’écran", display.sync_area)}</select></label>
          <label class="config-field">Zones<select id="edge-count-${index}" onchange="updateBottomZone(${index})">${configOption("3", "3 côtés", String(display.edge_count))}${configOption("4", "4 côtés", String(display.edge_count))}</select></label>
          ${zone("left", "LEDs gauche")}${zone("top", "LEDs haut")}${zone("right", "LEDs droite")}${zone("bottom", "LEDs bas")}
        </div></div>`;
    }).join("") + `<div class="card" style="margin-top:12px"><strong>Session GNOME</strong><div class="config-grid">${sessionAction("lock", session.lock, "Au verrouillage")}${sessionAction("unlock", session.unlock, "Au déverrouillage")}</div></div><div class="row" style="margin-top:12px"><button class="btn primary" onclick="saveConfiguration(${layoutResponse.layout.displays.length})">Appliquer</button></div>`;
    layoutResponse.layout.displays.forEach((_display, index) => updateBottomZone(index));
    localize(root);
    updateSessionEffect("lock");
    updateSessionEffect("unlock");
  } catch (error) {
    root.textContent = error.message;
  }
}

function updateBottomZone(index) {
  const bottom = document.getElementById(`zone-${index}-bottom`);
  const enabled = document.getElementById(`edge-count-${index}`).value === "4";
  bottom.disabled = !enabled;
  if (!enabled) bottom.value = 0;
}

function updateSessionEffect(phase) {
  document.getElementById(`session-${phase}-effect`).disabled = document.getElementById(`session-${phase}-mode`).value !== "effect";
}

async function saveConfiguration(count) {
  try {
    const displays = Array.from({length: count}, (_value, index) => ({
      device: document.getElementById(`device-${index}`).value,
      screen: document.getElementById(`screen-${index}`).value,
      location: document.getElementById(`location-${index}`).value,
      installation_direction: document.getElementById(`direction-${index}`).value,
      sync_area: document.getElementById(`sync-area-${index}`).value,
      edge_count: parseInt(document.getElementById(`edge-count-${index}`).value, 10),
      zones: Object.fromEntries(["left", "top", "right", "bottom"].map(name => [name, parseInt(document.getElementById(`zone-${index}-${name}`).value, 10)])),
    }));
    const sessionAction = phase => ({
      mode: document.getElementById(`session-${phase}-mode`).value,
      effect: document.getElementById(`session-${phase}-effect`).value,
    });
    await configRequest("PUT", "/api/layout", {version: 2, displays, session: {lock: sessionAction("lock"), unlock: sessionAction("unlock")}});
    setStatus("Configuration DX-Light appliquée.");
  } catch (error) {
    setStatus(`Erreur : ${error.message}`);
  }
}

function hexToRgb(hex) {
  const v = hex.replace('#','');
  return { r: parseInt(v.substring(0,2), 16), g: parseInt(v.substring(2,4), 16), b: parseInt(v.substring(4,6), 16) };
}

async function postJson(url, body) {
  setConn("Working…");
  const res = await fetch(url, { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(body) });
  const data = await res.json().catch(() => ({}));
  setConn(res.ok ? "Ready" : "Error");
  return {ok: res.ok, data};
}

function syncLabels(){
  const hex = document.getElementById("picker").value.toUpperCase();
  const b = parseInt(document.getElementById("bright").value, 10);
  const d = parseInt(document.getElementById("duration").value, 10);
  const s = parseInt(document.getElementById("speed").value, 10);

  document.getElementById("hexLabel").textContent = hex;
  document.getElementById("brightVal").textContent = `${b}%`;
  document.getElementById("durVal").textContent = `${d}ms`;
  document.getElementById("speedVal").textContent = `${s}`;
  document.getElementById("mini").textContent = `Brightness ${b}% • Fade ${d}ms • Speed ${s}`;
  
  const sfps = parseInt(document.getElementById("syncFps").value, 10);
  const sth = parseInt(document.getElementById("syncThickness").value, 10);
  const sdown = parseInt(document.getElementById("syncDown").value, 10);
  const salpha = parseInt(document.getElementById("syncAlpha").value, 10);
  const sthr = parseInt(document.getElementById("syncThr").value, 10);

  document.getElementById("syncFpsVal").textContent = `${sfps}`;
  document.getElementById("syncThicknessVal").textContent = `${sth}px`;
  document.getElementById("syncDownVal").textContent = `${sdown}x`;
  document.getElementById("syncAlphaVal").textContent = `${(salpha/100).toFixed(2)}`;
  document.getElementById("syncThrVal").textContent = `${sthr}`;
}

async function applyInstant() {
  const hex = document.getElementById("picker").value;
  const b = parseInt(document.getElementById("bright").value, 10);
  const rgb = hexToRgb(hex);

  const {ok, data} = await postJson("/api/color", {...rgb, brightness: b});
  setStatus(ok ? `Applied ${hex.toUpperCase()} @ ${b}%` : `Error: ${data.detail || "unknown"}`);
}

async function applyFade() {
  const hex = document.getElementById("picker").value;
  const b = parseInt(document.getElementById("bright").value, 10);
  const d = parseInt(document.getElementById("duration").value, 10);
  const rgb = hexToRgb(hex);

  if (d === 0) return applyInstant();

  const {ok, data} = await postJson("/api/fade", {...rgb, brightness: b, duration_ms: d, steps: 40});
  setStatus(ok ? `Fading to ${hex.toUpperCase()} @ ${b}% (${d}ms)` : `Error: ${data.detail || "unknown"}`);
}

async function turnOff() {
  const {ok, data} = await postJson("/api/off", {});
  setStatus(ok ? "Turned off." : `Error: ${data.detail || "unknown"}`);
}

async function stopAll(){
  // stops fade + stops effects
  await postJson("/api/stop", {});
  await postJson("/api/effect/stop", {});
  setStatus("Stopped.");
}

async function startSync(){
  const monitor = parseInt(document.getElementById("syncMonitor").value, 10);
  const fps = parseInt(document.getElementById("syncFps").value, 10);
  const thickness = parseInt(document.getElementById("syncThickness").value, 10);
  const downscale = parseInt(document.getElementById("syncDown").value, 10);
  const alpha = parseInt(document.getElementById("syncAlpha").value, 10) / 100.0;
  const change_threshold = parseInt(document.getElementById("syncThr").value, 10);

  const payload = { monitor, fps, thickness, downscale, alpha, change_threshold };

  const {ok, data} = await postJson("/api/sync/start", payload);
  setStatus(ok ? `Sync running (Monitor #${monitor} • ${fps} FPS)` : `Error: ${data.detail || "unknown"}`);
}

async function stopSync(){
  const {ok, data} = await postJson("/api/sync/stop", {});
  setStatus(ok ? "Sync stopped." : `Error: ${data.detail || "unknown"}`);
}

function preset(hex, name){
  document.getElementById("picker").value = hex;
  syncLabels();
  const d = parseInt(document.getElementById("duration").value, 10);
  setStatus(`Preset: ${name}`);
  return (d === 0) ? applyInstant() : applyFade();
}

function selectEffect(name){
  selectedEffect = name;
  document.querySelectorAll("[data-effect]").forEach(button => {
    button.style.borderColor = button.dataset.effect === name ? "rgba(106,228,255,.7)" : "";
  });
  setStatus(`Effect selected: ${name}`);
}

async function startEffect(){
  const s = parseInt(document.getElementById("speed").value, 10);
  const b = parseInt(document.getElementById("bright").value, 10);
  const hex = document.getElementById("picker").value;
  const rgb = hexToRgb(hex);

  let payload = { effect: selectedEffect, speed: s };

  // Pulse uses chosen color + brightness
  if (selectedEffect === "pulse"){
    payload = { ...payload, ...rgb, brightness: b };
  }

  if (selectedEffect.startsWith("dxlight-") && !selectedEffect.startsWith("dxlight-rhythm-"))
    await postJson("/api/dxlight/speed", {speed: s});

  const {ok, data} = await postJson("/api/effect/start", payload);
  setStatus(ok ? `Effect running: ${selectedEffect} (speed ${s})` : `Error: ${data.detail || "unknown"}`);
}

localize();
syncLabels();
selectEffect("pulse");
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index():
    locale = next((value for value in (os.environ.get("LANGUAGE"), os.environ.get("LC_ALL"), os.environ.get("LC_MESSAGES"), os.environ.get("LANG")) if value), "en")
    return HTML_PAGE.replace("__LANG__", "fr" if locale.startswith("fr") else "en")

@app.post("/api/color")
def set_color_api(c: Color):
    ctl = get_controller()
    cancel_fade()
    cancel_sync()
    cancel_effect()
    stop_dxlight_effect()

    brightness = clamp(100 if c.brightness is None else c.brightness, 0, 100)
    r, g, b = apply_brightness(clamp(c.r,0,255), clamp(c.g,0,255), clamp(c.b,0,255), brightness)

    ctl.set_color(r, g, b)
    _current["r"], _current["g"], _current["b"] = r, g, b
    return JSONResponse({"ok": True, "r": r, "g": g, "b": b, "brightness": brightness})

@app.post("/api/fade")
async def fade_api(req: FadeRequest):
    cancel_fade()
    cancel_sync()
    cancel_effect()
    stop_dxlight_effect()

    brightness = clamp(100 if req.brightness is None else req.brightness, 0, 100)
    r, g, b = apply_brightness(clamp(req.r,0,255), clamp(req.g,0,255), clamp(req.b,0,255), brightness)
    target = {"r": r, "g": g, "b": b}

    global _fade_task
    _fade_task = asyncio.create_task(fade_to(target, req.duration_ms, req.steps))
    return JSONResponse({"ok": True, "target": target, "brightness": brightness, "duration_ms": req.duration_ms})

@app.post("/api/effect/start")
async def effect_start(req: EffectRequest):
    cancel_fade()
    cancel_sync()
    cancel_effect()
    stop_dxlight_effect()

    eff = (req.effect or "").lower().strip()
    speed = clamp(req.speed, 1, 100)

    global _effect_task, _dxlight_effect
    dxlight_effects = {
        "dxlight-dynamix": 0,
        "dxlight-serpentin": 1,
        "dxlight-feu": 2,
        "dxlight-4": 3,
        "dxlight-5": 4,
        "dxlight-6": 5,
        "dxlight-7": 6,
    }
    if eff in dxlight_effects:
        effect_id = dxlight_effects[eff]
        get_controller().set_dxlight_effect(effect_id)
        _dxlight_effect = effect_id
        return JSONResponse({"ok": True, "effect": eff, "effect_id": effect_id, "mode": "dxlight"})

    if eff.startswith("dxlight-rhythm-"):
        try:
            rhythm_id = int(eff.removeprefix("dxlight-rhythm-"))
        except ValueError:
            rhythm_id = -1
        if not 0 <= rhythm_id <= 6:
            raise HTTPException(status_code=400, detail="DX-Light rhythm ID must be between 0 and 6.")
        get_controller().set_dxlight_rhythm(rhythm_id)
        _dxlight_effect = rhythm_id
        return JSONResponse({"ok": True, "effect": eff, "rhythm_id": rhythm_id, "mode": "dxlight-rhythm"})

    if eff == "pulse":
        # Use chosen color (with brightness)
        if req.r is None or req.g is None or req.b is None:
            raise HTTPException(status_code=400, detail="pulse requires r,g,b")
        brightness = clamp(100 if req.brightness is None else req.brightness, 0, 100)
        r, g, b = apply_brightness(clamp(req.r,0,255), clamp(req.g,0,255), clamp(req.b,0,255), brightness)
        base = {"r": r, "g": g, "b": b}
        _effect_task = asyncio.create_task(effect_pulse(base, speed))
        return JSONResponse({"ok": True, "effect": "pulse", "base": base, "speed": speed})

    elif eff == "rainbow":
        _effect_task = asyncio.create_task(effect_rainbow(speed))
        return JSONResponse({"ok": True, "effect": "rainbow", "speed": speed})
    elif eff == "directional-rainbow":
        _effect_task = asyncio.create_task(effect_directional_rainbow(speed))
        return JSONResponse({"ok": True, "effect": "directional-rainbow", "speed": speed})
    elif eff == "rainbow-chase":
        _effect_task = asyncio.create_task(effect_rainbow_chase(speed))
        return JSONResponse({"ok": True, "effect": "rainbow-chase", "speed": speed})
    elif eff == "aurora-wave":
        _effect_task = asyncio.create_task(effect_aurora_wave(speed))
        return JSONResponse({"ok": True, "effect": "aurora-wave", "speed": speed})
    elif eff == "breathing":
        base = apply_brightness(req.r if req.r is not None else 255, req.g if req.g is not None else 200, req.b if req.b is not None else 120, req.brightness if req.brightness is not None else 100)
        _effect_task = asyncio.create_task(effect_pulse(dict(zip(("r", "g", "b"), base)), speed))
        return JSONResponse({"ok": True, "effect": "breathing", "speed": speed})

    raise HTTPException(status_code=400, detail="Unknown effect. Use 'pulse' or 'rainbow'.")

@app.post("/api/sync/start")
async def sync_start(req: SyncStartRequest):
    # Stop other modes
    cancel_fade()
    cancel_effect()
    stop_dxlight_effect()
    cancel_sync()

    global _sync_tasks
    try:
        _sync_tasks = []
        for index, controller in enumerate(get_controller().controllers):
            config = req.model_copy(update={"monitor": req.monitor + index})
            _sync_tasks.append(asyncio.create_task(screen_sync_loop(config, controller)))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    return JSONResponse({
        "ok": True,
        "mode": "sync",
        "config": req.model_dump(),
        "controllers": len(_sync_tasks),
    })

@app.post("/api/sync/stop")
def sync_stop():
    cancel_sync()
    return JSONResponse({"ok": True, "mode": "manual"})

@app.get("/api/status")
def status():
    mode = "manual"
    if any(not task.done() for task in _sync_tasks):
        mode = "sync"
    elif _effect_task and not _effect_task.done():
        mode = "effect"
    elif _dxlight_effect is not None:
        mode = "dxlight-effect"
    elif _fade_task and not _fade_task.done():
        mode = "fade"
    return JSONResponse({"ok": True, "mode": mode, "current": _current})

@app.post("/api/effect/stop")
def effect_stop():
    cancel_sync()
    cancel_effect()
    stop_dxlight_effect()
    return JSONResponse({"ok": True})

@app.post("/api/dxlight/speed")
def dxlight_speed(req: SpeedRequest):
    speed = clamp(req.speed, 0, 100)
    get_controller().set_dxlight_speed(speed)
    return JSONResponse({"ok": True, "speed": speed})

@app.post("/api/dxlight/led-count")
def dxlight_led_count(req: LedCountRequest):
    count = clamp(req.count, 1, 254)
    get_controller().set_led_count(count)
    return JSONResponse({"ok": True, "count": count})


@app.get("/api/devices")
def devices():
    return JSONResponse({"ok": True, "devices": discover_vendor_devices()})


@app.get("/api/layout")
def layout():
    return JSONResponse({"ok": True, "layout": load_layout()})


@app.put("/api/layout")
def update_layout(req: LayoutRequest):
    if not req.displays:
        raise HTTPException(status_code=400, detail="At least one connected display is required.")
    valid_effects = {
        "dxlight-dynamix", "dxlight-serpentin", "dxlight-feu", "dxlight-4", "dxlight-5", "dxlight-6", "dxlight-7",
        *(f"dxlight-rhythm-{index}" for index in range(7)),
    }
    for action in (req.session.lock, req.session.unlock):
        if action.mode not in {"off", "sync", "effect"}:
            raise HTTPException(status_code=400, detail="Invalid session action.")
        if action.mode == "effect" and action.effect not in valid_effects:
            raise HTTPException(status_code=400, detail="Invalid session effect.")

    available = set(discover_vendor_devices())
    devices = [display.device for display in req.displays]
    if any(device not in available for device in devices):
        raise HTTPException(status_code=400, detail="A selected controller is no longer connected.")
    if len(set(devices)) != len(devices):
        raise HTTPException(status_code=400, detail="Each display must use a different controller.")
    if any(display.screen not in {"left", "right"} for display in req.displays):
        raise HTTPException(status_code=400, detail="Screen must be left or right.")
    if any(display.location not in {"back", "top", "bottom", "left", "right"} for display in req.displays):
        raise HTTPException(status_code=400, detail="Invalid installation location.")
    if any(display.installation_direction not in {"left-to-right", "right-to-left"} for display in req.displays):
        raise HTTPException(status_code=400, detail="Invalid installation direction.")
    if any(display.sync_area not in {"edge", "center"} for display in req.displays):
        raise HTTPException(status_code=400, detail="Invalid synchronization area.")
    for display in req.displays:
        zones = display.zones
        if display.edge_count not in {3, 4}:
            raise HTTPException(status_code=400, detail="A strip must use three or four sides.")
        if display.edge_count == 3 and zones.bottom != 0:
            raise HTTPException(status_code=400, detail="The bottom zone must be zero with three sides.")
        if any(count < 0 for count in (zones.left, zones.top, zones.right, zones.bottom)):
            raise HTTPException(status_code=400, detail="Zone LED counts cannot be negative.")
        if not 1 <= sum((zones.left, zones.top, zones.right, zones.bottom)) <= 254:
            raise HTTPException(status_code=400, detail="Each strip must contain between 1 and 254 LEDs.")

    layout = req.model_dump()
    save_layout(layout)
    global _controller
    _controller = None
    for controller, display in zip(get_controller().controllers, req.displays):
        zones = display.zones
        controller.set_led_count(sum((zones.left, zones.top, zones.right, zones.bottom)))
    return JSONResponse({"ok": True, "layout": layout})

@app.post("/api/off")
def off_api():
    ctl = get_controller()
    cancel_fade()
    cancel_sync()
    cancel_effect()
    stop_dxlight_effect()
    ctl.set_color(0, 0, 0)
    _current["r"], _current["g"], _current["b"] = 0, 0, 0
    return JSONResponse({"ok": True})

@app.post("/api/stop")
def stop_api():
    cancel_fade()
    cancel_sync()
    cancel_effect()
    stop_dxlight_effect()
    return JSONResponse({"ok": True})
