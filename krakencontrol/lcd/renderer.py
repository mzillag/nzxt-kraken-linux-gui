"""Draws the 640x640 frames shown on the Kraken Elite LCD.

The layouts replicate NZXT CAM's built-in screens:

* Single Infographic: 270° gradient arc gauge opening at the bottom, marker dot
  at the current value, dimmed remainder, big number with a superscript unit
  and the reading label underneath, logo on top.
* Dual Infographic: the horizontal layout (two bars with label and value).
* Triple Infographic: arc gauge, primary reading centre-left, divider, two
  secondary readings stacked on the right.
* Clockface: analog (dial dots, hands) or digital (hours over minutes).
* Image / GIF, and Infographic + GIF (reading overlaid on a smaller GIF).

Frames are drawn at 2x and downsampled for anti-aliasing.  Static layers
(background, dimmed track, logo) are cached per screen specification.
"""

from __future__ import annotations

import math
import os
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageSequence

from ..metrics import METRICS, Metric, format_value, gauge_fraction

SIZE = 640
SS = 2  # supersampling factor
CANVAS = SIZE * SS
FONT_DIR = os.path.join(os.path.dirname(__file__), "..", "assets", "fonts")

DEFAULT_BACKGROUND = "#000000"
DEFAULT_TEXT = "#FFFFFF"
GRADIENT_DEFAULT = ("#2AB8FF", "#C81DFF")  # CAM "Visualization" swatch (blue -> magenta)
# CAM 4.76 defaults (renderer bundle): Single Infographic visualizationColors
# ["6B00DE", "D600BF"]; Dual Infographic ["6B00DE", "D600BF", "818181"] where the
# third entry is the unfilled part of the bars.
SINGLE_DEFAULT = ("#6B00DE", "#D600BF")
DUAL_DEFAULT = ("#6B00DE", "#D600BF", "#818181")
TRACK_ALPHA = 0.22


def hex_to_rgb(value: str, fallback=(255, 255, 255)) -> tuple[int, int, int]:
    if not isinstance(value, str):
        return fallback
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        return fallback
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return fallback


def mix(c1, c2, t: float) -> tuple[int, int, int]:
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(c1, c2))  # type: ignore[return-value]


def dim(color, alpha: float) -> tuple[int, int, int]:
    return tuple(int(round(c * alpha)) for c in color)  # type: ignore[return-value]


@lru_cache(maxsize=64)
def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    path = os.path.join(FONT_DIR, f"Montserrat-{weight}.ttf")
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        for fallback in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                         "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"):
            if os.path.exists(fallback):
                return ImageFont.truetype(fallback, size)
        return ImageFont.load_default()  # type: ignore[return-value]


def _text_size(draw: ImageDraw.ImageDraw, text: str, fnt) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=fnt)
    return right - left, bottom - top


def draw_text_centered(draw, cx, cy, text, fnt, fill, anchor="mm", stroke=0, stroke_fill=None):
    draw.text((cx, cy), text, font=fnt, fill=fill, anchor=anchor, stroke_width=stroke, stroke_fill=stroke_fill)


def draw_number_with_suffix(draw, cx, cy, number: str, suffix: str, size: int, color,
                            align: str = "center", stroke: int = 0, stroke_fill=None) -> int:
    """Draw a big number with a smaller superscript suffix.  Returns total width.

    `stroke` adds an outline (CAM draws readings over GIFs with a black outline)."""
    num_font = font("Bold", size)
    suf_font = font("Bold", int(size * (0.42 if suffix in ("°", "%") else 0.26)))
    bbox = draw.textbbox((0, 0), number, font=num_font, anchor="ls")
    num_w = bbox[2] - bbox[0]
    num_h = -bbox[1]
    gap = int(size * 0.03)
    if suffix in ("°", "%"):
        sbbox = draw.textbbox((0, 0), suffix, font=suf_font, anchor="ls")
        suf_w = sbbox[2] - sbbox[0]
    elif suffix:
        sbbox = draw.textbbox((0, 0), suffix, font=suf_font, anchor="ls")
        suf_w = sbbox[2] - sbbox[0] + gap
    else:
        suf_w = 0
    total = num_w + gap + suf_w if suffix else num_w
    if align == "center":
        x0 = cx - total / 2
    elif align == "left":
        x0 = cx
    else:
        x0 = cx - total
    baseline = cy + num_h / 2
    outline = {"stroke_width": stroke, "stroke_fill": stroke_fill}
    draw.text((x0, baseline), number, font=num_font, fill=color, anchor="ls", **outline)
    if suffix in ("°", "%"):
        draw.text((x0 + num_w + gap, baseline - num_h + int(size * 0.42)), suffix, font=suf_font,
                  fill=color, anchor="ls", **outline)
    elif suffix:
        draw.text((x0 + num_w + gap * 3, baseline), suffix, font=suf_font, fill=color, anchor="ls", **outline)
    return int(total)


def draw_gradient_arc(draw, center, radius, width, start_deg, sweep_deg, c1, c2, alpha=1.0,
                      total_sweep=None, steps_per_deg=1.0):
    """Arc from start_deg clockwise over sweep_deg, colour interpolated along total_sweep."""
    if sweep_deg <= 0:
        return
    total_sweep = total_sweep or sweep_deg
    cx, cy = center
    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    steps = max(1, int(sweep_deg * steps_per_deg))
    step = sweep_deg / steps
    for i in range(steps):
        a0 = start_deg + i * step
        t = (i * step) / total_sweep
        color = dim(mix(c1, c2, t), alpha)
        draw.arc(box, a0, a0 + step + 0.6, fill=color, width=width)
    # round caps
    for angle, t in ((start_deg, 0.0), (start_deg + sweep_deg, sweep_deg / total_sweep)):
        rad = math.radians(angle)
        px = cx + radius * math.cos(rad) - width / 2 + 0.5
        py = cy + radius * math.sin(rad) - width / 2 + 0.5
        color = dim(mix(c1, c2, t), alpha)
        draw.ellipse((px, py, px + width - 1, py + width - 1), fill=color)


def circle_mask(size: int) -> Image.Image:
    mask = Image.new("L", (size * SS, size * SS), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * SS - 1, size * SS - 1), fill=255)
    return mask.resize((size, size), Image.LANCZOS)


_CIRCLE_MASK = circle_mask(SIZE)


def apply_circle_mask(img: Image.Image, background=(0, 0, 0)) -> Image.Image:
    base = Image.new("RGB", img.size, background)
    base.paste(img, (0, 0), _CIRCLE_MASK)
    return base


# ---------------------------------------------------------------------------
# Media (images / GIFs)
# ---------------------------------------------------------------------------
@dataclass
class Frame:
    image: Image.Image   # RGB, already fitted
    duration: float      # seconds


class Media:
    """A decoded image or animation, fitted to the requested box."""

    def __init__(self, path: str, box: int = SIZE, circular: bool = True) -> None:
        self.path = path
        self.frames: list[Frame] = []
        self.total = 0.0
        self.animated = False
        self.mtime = os.path.getmtime(path) if os.path.exists(path) else 0
        self._load(box, circular)

    def _load(self, box: int, circular: bool) -> None:
        img = Image.open(self.path)
        self.animated = getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1
        for frame in ImageSequence.Iterator(img):
            duration = frame.info.get("duration", 100) / 1000.0
            if duration <= 0.005:
                duration = 0.1
            fitted = ImageOps.fit(frame.convert("RGB"), (box, box), Image.LANCZOS)
            if circular and box == SIZE:
                fitted = apply_circle_mask(fitted)
            self.frames.append(Frame(fitted, duration))
            if not self.animated:
                break
        self.total = sum(f.duration for f in self.frames)

    def frame_at(self, t: float) -> Frame:
        if not self.animated or self.total <= 0:
            return self.frames[0]
        t = t % self.total
        for frame in self.frames:
            if t < frame.duration:
                return frame
            t -= frame.duration
        return self.frames[-1]


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------
class Renderer:
    ARC_START = 135.0   # degrees, PIL convention (0 = 3 o'clock, clockwise)
    ARC_SWEEP = 270.0

    def __init__(self) -> None:
        self._static_cache: dict[str, Image.Image] = {}
        self._media_cache: dict[tuple, Media] = {}
        self._arc_cache: dict[str, Image.Image] = {}

    def _bright_arc(self, key: str, radius, width, c1, c2) -> Image.Image:
        """Full 270° gradient arc on a transparent layer (cached)."""
        layer = self._arc_cache.get(key)
        if layer is None:
            layer = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
            draw_gradient_arc(ImageDraw.Draw(layer), (CANVAS / 2, CANVAS / 2), radius, width,
                              self.ARC_START, self.ARC_SWEEP, c1, c2)
            if len(self._arc_cache) > 8:
                self._arc_cache.clear()
            self._arc_cache[key] = layer
        return layer

    def _draw_gauge(self, img: Image.Image, key: str, radius, width, c1, c2, frac: float) -> None:
        """Reveal the cached bright arc up to `frac` and draw the marker dot."""
        sweep = self.ARC_SWEEP * frac
        if sweep > 0.5:
            layer = self._bright_arc(key, radius, width, c1, c2)
            mask = Image.new("L", (CANVAS, CANVAS), 0)
            md = ImageDraw.Draw(mask)
            big = CANVAS  # pie slice larger than the arc so it fully covers the stroke
            md.pieslice((CANVAS / 2 - big, CANVAS / 2 - big, CANVAS / 2 + big, CANVAS / 2 + big),
                        self.ARC_START, self.ARC_START + sweep, fill=255)
            # keep the rounded start cap, which the pie slice would clip
            a = math.radians(self.ARC_START)
            sx = CANVAS / 2 + radius * math.cos(a)
            sy = CANVAS / 2 + radius * math.sin(a)
            md.ellipse((sx - width, sy - width, sx + width, sy + width), fill=255)
            combined = Image.new("L", (CANVAS, CANVAS), 0)
            combined.paste(layer.getchannel("A"), (0, 0), mask)
            img.paste(layer, (0, 0), combined)
        d = ImageDraw.Draw(img)
        ang = math.radians(self.ARC_START + sweep)
        dot_r = width * 0.95
        mx = CANVAS / 2 + radius * math.cos(ang)
        my = CANVAS / 2 + radius * math.sin(ang)
        d.ellipse((mx - dot_r, my - dot_r, mx + dot_r, my + dot_r), fill=mix(c1, c2, frac))

    # ----------------------------------------------------------------- helpers
    def media(self, path: str, box: int = SIZE, circular: bool = True) -> Optional[Media]:
        if not path or not os.path.exists(path):
            return None
        key = (path, box, circular)
        cached = self._media_cache.get(key)
        mtime = os.path.getmtime(path)
        if cached is None or cached.mtime != mtime:
            try:
                cached = Media(path, box, circular)
            except (OSError, ValueError):
                return None
            self._media_cache = {key: cached}  # keep only the active media in memory
        return cached

    def _values(self, snapshot, unit: str):
        def get(metric_id: str):
            metric = METRICS.get(metric_id, METRICS["liquid_temp"])
            value = snapshot.value_for(metric.id) if snapshot else None
            return metric, value
        return get

    # ----------------------------------------------------------------- entry point
    def render(self, spec: dict, snapshot, unit: str = "C", now: Optional[float] = None,
               rotation: int = 0) -> Image.Image:
        mode = spec.get("mode", "single")
        now = time.time() if now is None else now
        if mode == "single":
            img = self._render_single(spec, snapshot, unit)
        elif mode == "dual":
            img = self._render_dual(spec, snapshot, unit)
        elif mode == "triple":
            img = self._render_triple(spec, snapshot, unit)
        elif mode == "clock":
            img = self._render_clock(spec, now)
        elif mode == "image":
            img = self._render_image(spec, now)
        elif mode in ("single_gif", "dual_gif", "triple_gif"):
            img = self._render_infographic_gif(spec, snapshot, unit, now)
        elif mode == "blank":
            img = Image.new("RGB", (SIZE, SIZE), hex_to_rgb(spec.get("background", "#000000")))
        else:
            img = self._render_single(spec, snapshot, unit)
        if rotation % 360:
            img = img.rotate(-rotation, resample=Image.BICUBIC, fillcolor=(0, 0, 0))
        return img

    # ----------------------------------------------------------------- static layer
    def _static(self, key: str, builder) -> Image.Image:
        img = self._static_cache.get(key)
        if img is None:
            img = builder()
            if len(self._static_cache) > 8:
                self._static_cache.clear()
            self._static_cache[key] = img
        return img.copy()

    def _draw_logo(self, draw, color, cy=None, size=None, stroke=0, stroke_fill=None):
        cy = CANVAS * 0.235 if cy is None else cy
        fnt = font("Bold", size or int(CANVAS * 0.052))
        letters = "NZXT"
        spacing = fnt.size * 0.28
        widths = [draw.textlength(ch, font=fnt) for ch in letters]
        total = sum(widths) + spacing * (len(letters) - 1)
        x = CANVAS / 2 - total / 2
        for ch, w in zip(letters, widths):
            draw.text((x, cy), ch, font=fnt, fill=color, anchor="lm", stroke_width=stroke, stroke_fill=stroke_fill)
            x += w + spacing

    # ----------------------------------------------------------------- single
    def _render_single(self, spec, snapshot, unit) -> Image.Image:
        bg = hex_to_rgb(spec.get("background", DEFAULT_BACKGROUND), (0, 0, 0))
        c1 = hex_to_rgb(spec.get("color1", SINGLE_DEFAULT[0]))
        c2 = hex_to_rgb(spec.get("color2", SINGLE_DEFAULT[1]))
        text_color = hex_to_rgb(spec.get("text_color", DEFAULT_TEXT))
        logo = spec.get("logo", True)
        logo_color = hex_to_rgb(spec.get("logo_color", DEFAULT_TEXT))
        metric, value = self._values(snapshot, unit)(spec.get("reading", "liquid_temp"))
        radius = CANVAS * 0.40
        width = int(CANVAS * 0.034)

        def build():
            img = Image.new("RGB", (CANVAS, CANVAS), bg)
            d = ImageDraw.Draw(img)
            draw_gradient_arc(d, (CANVAS / 2, CANVAS / 2), radius, width, self.ARC_START,
                              self.ARC_SWEEP, c1, c2, alpha=TRACK_ALPHA)
            if logo:
                self._draw_logo(d, logo_color)
            return img

        key = f"single:{bg}:{c1}:{c2}:{logo}:{logo_color}"
        img = self._static(key, build)
        frac = gauge_fraction(metric, value)
        self._draw_gauge(img, f"arc:{c1}:{c2}", radius, width, c1, c2, frac)
        d = ImageDraw.Draw(img)
        number, suffix = format_value(metric, value, unit)
        draw_number_with_suffix(d, CANVAS / 2, CANVAS * 0.50, number, suffix, int(CANVAS * 0.30), text_color)
        draw_text_centered(d, CANVAS / 2, CANVAS * 0.70, metric.short, font("SemiBold", int(CANVAS * 0.075)), text_color)
        return img.resize((SIZE, SIZE), Image.LANCZOS)

    # ----------------------------------------------------------------- dual (horizontal)
    def _render_dual(self, spec, snapshot, unit) -> Image.Image:
        bg = hex_to_rgb(spec.get("background", DEFAULT_BACKGROUND), (0, 0, 0))
        readings = list(spec.get("readings", ["cpu_temp", "gpu_temp"]))[:2] or ["cpu_temp", "gpu_temp"]
        if len(readings) == 1:
            readings.append("gpu_temp")
        number_colors = [hex_to_rgb(c) for c in spec.get("number_colors", [DEFAULT_TEXT, DEFAULT_TEXT])]
        text_colors = [hex_to_rgb(c) for c in spec.get("text_colors", [DEFAULT_TEXT, DEFAULT_TEXT])]
        viz_colors = [hex_to_rgb(c) for c in spec.get("viz_colors", list(DUAL_DEFAULT))]
        track = viz_colors[2] if len(viz_colors) > 2 else hex_to_rgb(DUAL_DEFAULT[2])
        logo = spec.get("logo", True)
        logo_color = hex_to_rgb(spec.get("logo_color", DEFAULT_TEXT))
        get = self._values(snapshot, unit)

        bar_w = CANVAS * 0.40
        bar_h = int(CANVAS * 0.040)
        bar_x0 = CANVAS * 0.26
        rows = (CANVAS * 0.455, CANVAS * 0.545)

        def build():
            img = Image.new("RGB", (CANVAS, CANVAS), bg)
            d = ImageDraw.Draw(img)
            if logo:
                self._draw_logo(d, logo_color)
            for cy in rows:
                d.rounded_rectangle((bar_x0, cy - bar_h / 2, bar_x0 + bar_w, cy + bar_h / 2),
                                    radius=bar_h / 2, fill=track)
            return img

        key = f"dual:{bg}:{logo}:{logo_color}:{track}"
        img = self._static(key, build)
        d = ImageDraw.Draw(img)
        label_font = font("Bold", int(CANVAS * 0.05))
        for i, reading in enumerate(readings):
            metric, value = get(reading)
            cy = rows[i]
            frac = gauge_fraction(metric, value)
            fill_w = max(bar_h, bar_w * frac)
            color = viz_colors[i] if i < len(viz_colors) else viz_colors[-1]
            if frac > 0:
                d.rounded_rectangle((bar_x0, cy - bar_h / 2, bar_x0 + fill_w, cy + bar_h / 2),
                                    radius=bar_h / 2, fill=color)
            tcol = text_colors[i] if i < len(text_colors) else text_colors[-1]
            ncol = number_colors[i] if i < len(number_colors) else number_colors[-1]
            label_y = cy - bar_h * 1.6 if i == 0 else cy + bar_h * 1.6
            d.text((bar_x0, label_y), metric.short.upper(), font=label_font, fill=tcol, anchor="lm")
            number, suffix = format_value(metric, value, unit)
            draw_number_with_suffix(d, bar_x0 + bar_w + CANVAS * 0.035, cy + (-CANVAS * 0.02 if i == 0 else CANVAS * 0.02),
                                    number, suffix, int(CANVAS * 0.13), ncol, align="left")
        return img.resize((SIZE, SIZE), Image.LANCZOS)

    # ----------------------------------------------------------------- triple
    def _render_triple(self, spec, snapshot, unit) -> Image.Image:
        bg = hex_to_rgb(spec.get("background", DEFAULT_BACKGROUND), (0, 0, 0))
        viz = spec.get("viz", list(GRADIENT_DEFAULT))
        c1, c2 = hex_to_rgb(viz[0]), hex_to_rgb(viz[1] if len(viz) > 1 else viz[0])
        logo = spec.get("logo", True)
        logo_color = hex_to_rgb(spec.get("logo_color", DEFAULT_TEXT))
        number_color = hex_to_rgb(spec.get("number_color", DEFAULT_TEXT))
        text_color = hex_to_rgb(spec.get("text_color", DEFAULT_TEXT))
        sec_ids = list(spec.get("secondary", ["cpu_temp", "liquid_temp"]))[:2]
        while len(sec_ids) < 2:
            sec_ids.append("liquid_temp")
        sec_num = [hex_to_rgb(c) for c in spec.get("secondary_number_colors", [DEFAULT_TEXT, DEFAULT_TEXT])]
        sec_txt = [hex_to_rgb(c) for c in spec.get("secondary_text_colors", [DEFAULT_TEXT, DEFAULT_TEXT])]
        get = self._values(snapshot, unit)
        metric, value = get(spec.get("primary", "gpu_temp"))
        radius = CANVAS * 0.40
        width = int(CANVAS * 0.034)

        def build():
            img = Image.new("RGB", (CANVAS, CANVAS), bg)
            d = ImageDraw.Draw(img)
            draw_gradient_arc(d, (CANVAS / 2, CANVAS / 2), radius, width, self.ARC_START,
                              self.ARC_SWEEP, c1, c2, alpha=TRACK_ALPHA)
            if logo:
                self._draw_logo(d, logo_color)
            # divider
            x = CANVAS * 0.585
            d.line((x, CANVAS * 0.40, x, CANVAS * 0.62), fill=text_color, width=int(CANVAS * 0.004))
            return img

        key = f"triple:{bg}:{c1}:{c2}:{logo}:{logo_color}:{text_color}"
        img = self._static(key, build)
        frac = gauge_fraction(metric, value)
        self._draw_gauge(img, f"arc:{c1}:{c2}", radius, width, c1, c2, frac)
        d = ImageDraw.Draw(img)
        number, suffix = format_value(metric, value, unit)
        draw_number_with_suffix(d, CANVAS * 0.555, CANVAS * 0.49, number, suffix, int(CANVAS * 0.23), number_color, align="right")
        draw_text_centered(d, CANVAS * 0.40, CANVAS * 0.635, metric.short, font("SemiBold", int(CANVAS * 0.06)), text_color)
        for i, sid in enumerate(sec_ids):
            smetric, svalue = get(sid)
            snum, ssuf = format_value(smetric, svalue, unit)
            cy = CANVAS * (0.43 if i == 0 else 0.56)
            draw_number_with_suffix(d, CANVAS * 0.615, cy, snum, ssuf, int(CANVAS * 0.10),
                                    sec_num[i] if i < len(sec_num) else sec_num[-1], align="left")
            d.text((CANVAS * 0.615, cy + CANVAS * 0.065), smetric.short, font=font("SemiBold", int(CANVAS * 0.036)),
                   fill=sec_txt[i] if i < len(sec_txt) else sec_txt[-1], anchor="lm")
        return img.resize((SIZE, SIZE), Image.LANCZOS)

    # ----------------------------------------------------------------- clock
    def _render_clock(self, spec, now: float) -> Image.Image:
        bg = hex_to_rgb(spec.get("background", DEFAULT_BACKGROUND), (0, 0, 0))
        dial = hex_to_rgb(spec.get("dial_color", DEFAULT_TEXT))
        seconds = hex_to_rgb(spec.get("seconds_color", "#8000FF"))
        face = spec.get("face", "analog")
        lt = time.localtime(now)
        img = Image.new("RGB", (CANVAS, CANVAS), bg)
        d = ImageDraw.Draw(img)
        cx = cy = CANVAS / 2
        if face == "digital":
            hour = lt.tm_hour if spec.get("hour24", False) else (lt.tm_hour % 12 or 12)
            big = font("Bold", int(CANVAS * 0.30))
            d.text((cx, cy - CANVAS * 0.02), f"{hour:02d}", font=big, fill=dial, anchor="ms")
            d.text((cx, cy + CANVAS * 0.02), f"{lt.tm_min:02d}", font=big, fill=seconds, anchor="mt")
            if not spec.get("hour24", False):
                d.text((cx + CANVAS * 0.30, cy), "PM" if lt.tm_hour >= 12 else "AM",
                       font=font("SemiBold", int(CANVAS * 0.05)), fill=dial, anchor="mm")
        else:
            r = CANVAS * 0.30
            dot = CANVAS * 0.012
            for i in range(12):
                a = math.radians(i * 30 - 90)
                px, py = cx + r * math.cos(a), cy + r * math.sin(a)
                d.ellipse((px - dot, py - dot, px + dot, py + dot), fill=dial)
            sec_f = lt.tm_sec + (now % 1)
            min_f = lt.tm_min + sec_f / 60
            hour_f = (lt.tm_hour % 12) + min_f / 60
            for length, angle, width, color in (
                (r * 0.55, hour_f * 30, int(CANVAS * 0.016), dial),
                (r * 0.85, min_f * 6, int(CANVAS * 0.010), dial),
                (r * 0.95, sec_f * 6, int(CANVAS * 0.005), seconds),
            ):
                a = math.radians(angle - 90)
                d.line((cx, cy, cx + length * math.cos(a), cy + length * math.sin(a)), fill=color, width=width)
            cdot = CANVAS * 0.014
            d.ellipse((cx - cdot, cy - cdot, cx + cdot, cy + cdot), fill=seconds)
        return img.resize((SIZE, SIZE), Image.LANCZOS)

    # ----------------------------------------------------------------- image / gif
    def _render_image(self, spec, now: float) -> Image.Image:
        media = self.media(spec.get("path", ""))
        if media is None:
            img = Image.new("RGB", (SIZE, SIZE), (0, 0, 0))
            d = ImageDraw.Draw(img)
            draw_text_centered(d, SIZE / 2, SIZE / 2, "No image", font("SemiBold", 40), (120, 120, 120))
            return img
        return media.frame_at(now - spec.get("_start", 0)).image

    def _render_infographic_gif(self, spec, snapshot, unit, now: float) -> Image.Image:
        """"Infographic + GIF" modes: the media fills the screen and the readings are
        drawn over it.  An outline colour may be given as `outline` (CAM has such a
        field); by default none is drawn."""
        bg = hex_to_rgb(spec.get("background", DEFAULT_BACKGROUND), (0, 0, 0))
        img = Image.new("RGB", (SIZE, SIZE), bg)
        media = self.media(spec.get("path", ""), box=SIZE, circular=True)
        if media is not None:
            img.paste(media.frame_at(now - spec.get("_start", 0)).image, (0, 0))
        # Overlay at 2x on a transparent layer for anti-aliased text
        overlay = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        d = ImageDraw.Draw(overlay)
        number_color = hex_to_rgb(spec.get("number_color", DEFAULT_TEXT))
        text_color = hex_to_rgb(spec.get("text_color", DEFAULT_TEXT))
        # Optional per-reading colours (used by the dual mode, like CAM's primary/secondary readings)
        number_colors = [hex_to_rgb(c) for c in spec.get("number_colors", [])] or [number_color]
        text_colors = [hex_to_rgb(c) for c in spec.get("text_colors", [])] or [text_color]
        outline_spec = spec.get("outline")  # e.g. "#000000"; unset or empty = no outline
        outline = hex_to_rgb(outline_spec, (0, 0, 0)) if outline_spec else None
        logo = spec.get("logo", True)
        logo_color = hex_to_rgb(spec.get("logo_color", DEFAULT_TEXT))
        get = self._values(snapshot, unit)
        mode = spec.get("mode", "single_gif")
        if mode == "single_gif":
            readings = [spec.get("reading", "liquid_temp")]
        elif mode == "dual_gif":
            readings = list(spec.get("readings", ["cpu_temp", "gpu_temp"]))[:2]
        else:
            readings = [spec.get("primary", "gpu_temp")] + list(spec.get("secondary", ["cpu_temp", "liquid_temp"]))[:2]

        def stroke_for(size):
            return max(2, int(size * 0.07)) if outline else 0

        def number(cx, cy, size, text, suffix, color):
            draw_number_with_suffix(d, cx, cy, text, suffix, size, color, stroke=stroke_for(size), stroke_fill=outline)

        def label(cx, cy, size, text, color):
            fnt = font("Bold", size)
            draw_text_centered(d, cx, cy, text, fnt, color, stroke=stroke_for(size), stroke_fill=outline)

        if logo:
            self._draw_logo(d, logo_color, stroke=stroke_for(int(CANVAS * 0.052)), stroke_fill=outline)
        if len(readings) == 1:
            metric, value = get(readings[0])
            num, suffix = format_value(metric, value, unit)
            number(CANVAS / 2, CANVAS * 0.50, int(CANVAS * 0.26), num, suffix, number_colors[0])
            label(CANVAS / 2, CANVAS * 0.665, int(CANVAS * 0.065), metric.short, text_colors[0])
        elif len(readings) == 2:
            for i, rid in enumerate(readings):
                metric, value = get(rid)
                num, suffix = format_value(metric, value, unit)
                cx = CANVAS * (0.31 if i == 0 else 0.69)
                ncol = number_colors[i] if i < len(number_colors) else number_colors[-1]
                tcol = text_colors[i] if i < len(text_colors) else text_colors[-1]
                number(cx, CANVAS * 0.50, int(CANVAS * 0.19), num, suffix, ncol)
                label(cx, CANVAS * 0.635, int(CANVAS * 0.06), metric.short, tcol)
        else:
            metric, value = get(readings[0])
            num, suffix = format_value(metric, value, unit)
            number(CANVAS / 2, CANVAS * 0.45, int(CANVAS * 0.21), num, suffix, number_colors[0])
            label(CANVAS / 2, CANVAS * 0.575, int(CANVAS * 0.06), metric.short, text_colors[0])
            for i, rid in enumerate(readings[1:3]):
                metric, value = get(rid)
                num, suffix = format_value(metric, value, unit)
                cx = CANVAS * (0.36 if i == 0 else 0.64)
                number(cx, CANVAS * 0.70, int(CANVAS * 0.10), num, suffix, number_colors[-1])
                label(cx, CANVAS * 0.77, int(CANVAS * 0.045), metric.short, text_colors[-1])
        overlay = overlay.resize((SIZE, SIZE), Image.LANCZOS)
        img.paste(overlay, (0, 0), overlay)
        return img

    def frame_duration(self, spec: dict) -> Optional[float]:
        """Natural frame duration of the current media, if any (for GIF pacing)."""
        if spec.get("mode") in ("image", "single_gif", "dual_gif", "triple_gif"):
            media = self.media(spec.get("path", ""), box=SIZE, circular=True)
            if media and media.animated:
                return min(f.duration for f in media.frames)
            return None
        return None
