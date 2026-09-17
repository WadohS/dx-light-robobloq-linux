import argparse
import colorsys
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .device import RobobloqControllers, SESSION_LOCK_PATH


DEFAULT_WALLPAPER = Path.home() / ".local/share/dual-wallpaper/wallpaper-composite.jpg"
BLACK_FILL = (76, 76, 76)  # Neutral white at 30% prevents a dark hole behind black screens.


def dominant_color(image: Image.Image) -> tuple[int, int, int]:
    pixels = np.asarray(image.convert("RGB").resize((480, 270)), dtype=float) / 255
    red, green, blue = (pixels[:, :, channel] for channel in range(3))
    high = pixels.max(axis=2)
    low = pixels.min(axis=2)
    chroma = high - low
    hue = np.zeros_like(high)
    colored = chroma > 1e-6

    red_max = (high == red) & colored
    green_max = (high == green) & colored
    blue_max = (high == blue) & colored
    hue[red_max] = ((green[red_max] - blue[red_max]) / chroma[red_max]) % 6
    hue[green_max] = (blue[green_max] - red[green_max]) / chroma[green_max] + 2
    hue[blue_max] = (red[blue_max] - green[blue_max]) / chroma[blue_max] + 4
    hue /= 6

    saturation = chroma / (high + 1e-6)
    weights = saturation**1.5 * (0.3 + high)
    if weights.sum() < 1e-6:
        # Grayscale images have no hue; preserve their actual luminance instead
        # of selecting the first histogram bucket (red).
        return tuple(int(channel * 255) for channel in pixels.mean(axis=(0, 1)))
    bins = 24
    histogram = np.bincount((hue * bins).astype(int).ravel() % bins, weights=weights.ravel(), minlength=bins)
    dominant_hue = (histogram.argmax() + 0.5) / bins
    return tuple(int(channel * 255) for channel in colorsys.hsv_to_rgb(dominant_hue, 0.9, 1.0))


def wallpaper_colors(path: Path, brightness: int) -> list[tuple[int, int, int]]:
    with Image.open(path) as wallpaper:
        width, height = wallpaper.size
        split = width // 2
        screens = [
            wallpaper.crop((0, 0, split, height)),
            wallpaper.crop((split, 0, width, height)),
        ]

    scale = max(0, min(brightness, 100)) / 100
    colors = [dominant_color(screen) for screen in screens]
    return [BLACK_FILL if max(color) <= 5 else tuple(int(channel * scale) for channel in color) for color in colors]


def run(path: Path, brightness: int, interval: float):
    controllers = RobobloqControllers().controllers
    if not controllers:
        raise RuntimeError("A ROBOBLOQ controller is required for wallpaper sync.")

    last_mtime = None
    while True:
        try:
            if SESSION_LOCK_PATH.exists():
                time.sleep(interval)
                continue
            mtime = path.stat().st_mtime_ns
            if mtime != last_mtime:
                for controller, color in zip(controllers, wallpaper_colors(path, brightness)):
                    if SESSION_LOCK_PATH.exists():
                        break
                    controller.set_color(*color)
                else:
                    last_mtime = mtime
        except FileNotFoundError:
            pass
        time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Sync two ROBOBLOQ controllers with a dual-monitor wallpaper.")
    parser.add_argument("--wallpaper", type=Path, default=DEFAULT_WALLPAPER)
    parser.add_argument("--brightness", type=int, default=35)
    parser.add_argument("--interval", type=float, default=3)
    args = parser.parse_args()
    run(args.wallpaper, args.brightness, args.interval)


if __name__ == "__main__":
    main()
