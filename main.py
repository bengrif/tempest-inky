"""Tempest Inky dashboard — layout 10.

Renders an 800x480 panel for the Pimoroni Inky Impression 7.3" (7 colour)
from a WeatherFlow Tempest station.

The panel is read from a sofa about 4.5 m away, in a dim room. Comfortable
reading needs a cap height of roughly distance / 200, which at 137 ppi is
~122 px — and Jost's caps run ~0.70em, so couch-readable type starts at
170 px. There is no arrangement of 800x480 that makes two text elements
couch-readable, so the panel carries exactly two things at that distance:
a number and a shape. The 172 px temperature and the 100 px condition
glyph. Everything else is a room-distance or walk-up element and is sized
as one.

Two columns split by a 3 px rule at x=362: NOW on the left at full height,
and METRICS / NEXT / LATER stacked on the right at 168 + 160 + 152.

The layout is a grid, not a set of centred boxes. Three rules generate most
of the numbers: one 24 px margin every zone insets by, fixed-width label
columns so values start on shared verticals, and nothing within 12 px of a
rule.

Colour is fill only, and only the 12 px forecast bars carry it, for
absolute temperature and nothing else. Condition is carried by glyphs and
has no ink of its own. All type is black.
"""


import argparse
import importlib.util
import json
import os
import socket
import sys
import time
from functools import lru_cache

import requests
from PIL import Image, ImageChops, ImageDraw, ImageFont

try:
    from inky.auto import auto
    INKY_AVAILABLE = True
except ImportError:
    INKY_AVAILABLE = False

user_home = os.path.expanduser("~")
API_BASE_URL = "https://swd.weatherflow.com/swd/rest"
HTTP_TIMEOUT = 20
DITHER_NONE = getattr(getattr(Image, "Dither", Image), "NONE", 0)

STATE_FILE = os.path.join(user_home, ".tempest-last.json")

# Thresholds for the headline band.
GUST_THRESHOLD_KPH = 40.0
BATTERY_LOW_VOLTS = 2.40
STATION_SILENT_SECONDS = 3600
LIGHTNING_NEAR_KM = 15
LIGHTNING_RECENT_SECONDS = 1800
PRECIP_LIKELY_PCT = 50

# Adaptive refresh (minutes). The systemd timer ticks every 5 minutes and
# main() decides whether this tick is due, because the service runs as the
# app user and cannot rewrite a unit file in /etc.
REFRESH_NIGHT_MIN = 60
REFRESH_NORMAL_MIN = 15
REFRESH_ACTIVE_MIN = 10
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 6


def get_base_path():
    try:
        return sys._MEIPASS
    except Exception:
        return os.path.dirname(os.path.abspath(__file__))


def get_app_path():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def load_secrets_file(path):
    """Import a secrets.py and hand back the module.

    The module rather than a tuple, because it carries optional settings
    such as UNITS alongside the credentials.
    """
    spec = importlib.util.spec_from_file_location("tempest_user_secrets", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"Could not load {path}")
    user_secrets = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(user_secrets)
    return user_secrets


@lru_cache(maxsize=1)
def get_secrets():
    for secret_path in [
        os.path.join(user_home, "secrets.py"),
        os.path.join(get_app_path(), "secrets.py"),
    ]:
        if not os.path.exists(secret_path):
            continue
        try:
            module = load_secrets_file(secret_path)
            print(f"Loaded configuration from {secret_path}")
            return module
        except Exception as e:
            print(f"Error loading {secret_path}: {e}")
    return None


def load_config():
    station_id = os.environ.get("TEMPEST_STATION_ID")
    token = os.environ.get("TEMPEST_TOKEN")
    if station_id and token:
        print("Loaded configuration from environment")
        return station_id, token
    if station_id or token:
        print("Incomplete environment configuration; falling back to secrets.py")

    module = get_secrets()
    if module is not None:
        found_id = getattr(module, "STATION_ID", None)
        found_token = getattr(module, "TOKEN", None)
        if found_id and found_token:
            return str(found_id), str(found_token)

    print("No secrets found. Using dummy data.")
    return "00000", "dummy"


STATION_ID, TOKEN = load_config()


UNIT_SYSTEMS = ("metric", "imperial")


def load_units():
    """Display units: "metric" (the default) or "imperial".

    Env wins over secrets.py. This only changes what is printed: the
    payload stays metric, and every threshold, temperature band and
    scenario stays in metric, so switching units can never move a
    threshold.
    """
    module = get_secrets()
    raw = (
        os.environ.get("TEMPEST_UNITS")
        or (getattr(module, "UNITS", None) if module is not None else None)
        or "metric"
    )
    units = str(raw).strip().lower()
    if units not in UNIT_SYSTEMS:
        print(f"Ignoring UNITS={raw!r}: use 'metric' or 'imperial'. Using metric.")
        return "metric"
    return units


UNITS = load_units()


BASE_DIR = get_base_path()
ASSETS_ROOT = os.path.join(BASE_DIR, "assets")

# One family: Jost, a geometric sans in the Futura tradition, which is what
# suits a mid-century room. 600 for every numeral and the headline, 400 for
# every label and sub-line, 300 for the `/` between high and low and
# nothing else. Plus Weather Icons for every glyph — meteorological rather
# than cartoon, and a font, so it hints to the pixel grid at any size
# instead of being resampled from a 712 px PNG.
FONT_SEMIBOLD = os.path.join(ASSETS_ROOT, "Jost-SemiBold.ttf")
FONT_REGULAR = os.path.join(ASSETS_ROOT, "Jost-Regular.ttf")
FONT_LIGHT = os.path.join(ASSETS_ROOT, "Jost-Light.ttf")
FONT_ICON = os.path.join(ASSETS_ROOT, "weathericons.ttf")

WIDTH = 800
HEIGHT = 480

# ── Design tokens ─────────────────────────────────────────────────────────────

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
INK_GREEN = (0, 255, 0)
INK_BLUE = (0, 0, 255)
INK_YELLOW = (255, 255, 0)
INK_ORANGE = (255, 128, 0)
INK_RED = (255, 0, 0)

# Pimoroni Inky Impression 7-colour palette. This is the entire palette.
INKY_PALETTE = [
    BLACK, WHITE, INK_GREEN, INK_BLUE, INK_RED, INK_YELLOW, INK_ORANGE,
]

# Temperature, divergent rather than sequential. A sequential cold-to-hot
# ramp renders the panel blue from November to March, because the palette
# has exactly one cold ink. Here white sits in the comfortable band and ink
# is spent only on departure from it, so a mild week is colourless and
# colour appearing across the room always means something changed.
TEMP_BANDS = [
    (0, INK_BLUE),       # below 0
    (10, INK_GREEN),     # 0-9
    (20, WHITE),         # 10-19, drawn with a 2 px keyline
    (25, INK_YELLOW),    # 20-24
    (30, INK_ORANGE),    # 25-29
    (None, INK_RED),     # 30 and above
]

DASH = "—"          # em dash, the "no data" mark
MIDDOT = "·"

# Verified against assets/weathericons.ttf — every one renders, no tofu.
WI = {
    "clear-day": "\uf00d",
    "clear-night": "\uf02e",
    "partly-cloudy-day": "\uf002",
    "partly-cloudy-night": "\uf086",
    "cloudy": "\uf013",
    "overcast": "\uf013",
    "rain": "\uf019",
    "rain-night": "\uf028",
    "day-rain": "\uf008",
    "snow": "\uf01b",
    "snow-night": "\uf02a",
    "day-snow": "\uf00a",
    "sleet": "\uf0b5",
    "thunderstorm": "\uf01e",
    "thunderstorm-night": "\uf02d",
    "fog": "\uf014",
    "fog-night": "\uf04a",
    "wind": "\uf050",
    "humidity": "\uf07a",
    "barometer": "\uf079",
    "sunrise": "\uf051",
    "sunset": "\uf052",
    "snowflake": "\uf076",
    "raindrop": "\uf078",
    "lightning": "\uf016",
    "hot": "\uf072",
    "na": "\uf07b",
}

# ── Geometry ──────────────────────────────────────────────────────────────────
# Two columns split by a 3 px rule. Geometry must stay byte-identical between
# refreshes and between states or the panel ghosts: zone heights, metric
# count and order, and forecast row count never move. Only fills and text
# content are allowed to change.

RULE_ZONE = 3       # column rule and zone dividers
RULE_INNER = 2      # dividers inside a zone, and the cloud-band keyline
MARGIN = 24         # the one margin: every zone in both columns insets by it
CLEARANCE = 12      # nothing sits closer than this to a rule

# The column rule occupies x=362..364; the right column starts at 365.
COLUMN_X = 362
RIGHT_X = COLUMN_X + RULE_ZONE

LEFT_X0 = MARGIN                    # 24
LEFT_X1 = COLUMN_X - MARGIN         # 338
RIGHT_X0 = RIGHT_X + MARGIN         # 389
RIGHT_X1 = WIDTH - MARGIN           # 776

# Right column zones. Load-bearing: an earlier revision of the spec summed
# to 476 and silently squeezed an element, so the sum is asserted rather
# than trusted.
METRICS_H = 168
NEXT_H = 160
LATER_H = 152
assert METRICS_H + NEXT_H + LATER_H == HEIGHT, "right column must sum to 480"

METRICS_Y0, METRICS_Y1 = 0, METRICS_H                       # 0   - 168
NEXT_Y0, NEXT_Y1 = METRICS_Y1, METRICS_Y1 + NEXT_H          # 168 - 328
LATER_Y0, LATER_Y1 = NEXT_Y1, NEXT_Y1 + LATER_H             # 328 - 480

# ── Type scale ────────────────────────────────────────────────────────────────
# Nothing under 19 px anywhere on the panel, no numeric value under 24 px,
# no glyph under 21 px, and tracking never tighter than -0.03em.

SIZE_TEMP = 172
SIZE_HERO_GLYPH = 100
SIZE_HILO = 44          # feels-like, today's high and low
SIZE_SLASH = 26         # the Jost 300 `/` between high and low
SIZE_METRIC = 42
SIZE_NEXT_GLYPH = 32
SIZE_HEADLINE = 31
SIZE_HOUR_TEMP = 24
SIZE_DAY_GLYPH = 23
SIZE_DAY_HIGH = 23
SIZE_HOUR_GLYPH = 21
SIZE_LABEL = 20         # floor: labels, day names, the NEXT sub-line
SIZE_HOUR_TIME = 19     # floor

# Tracking, in pixels, from the design's em values. The 0.16em on the
# micro-labels is both a small-size legibility gain and the most
# recognisably mid-century detail on the panel.
TRACK_LABEL = 0.16 * SIZE_LABEL
TRACK_DAY = 0.1 * SIZE_LABEL
TRACK_HOUR = 0.1 * SIZE_HOUR_TIME
TRACK_TEMP = -0.03 * SIZE_TEMP
TRACK_HEADLINE = -0.01 * SIZE_HEADLINE
TRACK_VALUE = -0.02        # em, applied per size to the tier-2 numerals

# ── Left column: NOW (x 0-361, full height) ───────────────────────────────────
# padding 18 vertical / 24 horizontal, content vertically centred as one
# block. Every box height below is fixed, so the block sums to a fixed 444
# and the centring cannot drift when a value changes width.
#
# Three rows under the rule — FEELS, TODAY (the forecast) and SO FAR (what
# the station has measured since midnight) — paid for by tightening the
# space around the rule and between the rows, not by shrinking any type.

NOW_PAD_Y = 18
NOW_GLYPH_BOX = 108
NOW_TEMP_BOX = 136          # 172 px at line-height 0.78
NOW_TEMP_GAP = 10
NOW_RULE_ABOVE = 20
NOW_RULE_BELOW = 14
NOW_ROW_H = 46              # FEELS, TODAY and SO FAR rows
NOW_ROW_GAP = 8
NOW_ROWS = 3
NOW_LABEL_COL = 96          # fixed label column: values start at x=120
NOW_SLASH_MARGIN = 10

NOW_BLOCK_H = (
    NOW_GLYPH_BOX + NOW_TEMP_GAP + NOW_TEMP_BOX
    + NOW_RULE_ABOVE + RULE_INNER + NOW_RULE_BELOW
    + NOW_ROWS * NOW_ROW_H + (NOW_ROWS - 1) * NOW_ROW_GAP
)
assert NOW_BLOCK_H + 2 * NOW_PAD_Y <= HEIGHT, "left column block does not fit"

# ── Right zone A: METRICS (168 px) ────────────────────────────────────────────
# 2x2. The zone is 168 rather than smaller purely to buy the top row its
# clearance from the panel edge — at 144 the labels sat 2 px from it.

METRIC_PAD_L = 24
METRIC_PAD_R = 14
METRIC_LABEL_LINE = SIZE_LABEL
METRIC_LABEL_GAP = 6
METRIC_BLOCK_H = METRIC_LABEL_LINE + METRIC_LABEL_GAP + SIZE_METRIC   # 68

# ── Right zone B: NEXT (160 px) ───────────────────────────────────────────────

NEXT_PAD_TOP = 16
NEXT_PAD_BOTTOM = 12
NEXT_TITLE_H = 32
NEXT_GLYPH_COL = 54         # widest condition glyph is 45 px at 32; 9 px clear
NEXT_SUB_GAP = 6
NEXT_SUB_H = SIZE_LABEL
NEXT_STRIP_GAP = 10         # clearance above the strip's rule
NEXT_HOUR_TIME_H = 20
NEXT_HOUR_GAP = 6
NEXT_HOUR_ROW_H = 25
NEXT_HOUR_GLYPH_COL = 34
NEXT_HOURS = 4

# ── Right zone C: LATER (152 px) ──────────────────────────────────────────────

LATER_PAD_Y = 8
LATER_ROWS = 5
LATER_ROW_GAP = 4
LATER_DAY_COL = 60
LATER_GLYPH_COL = 34
LATER_HIGH_COL = 56
LATER_BAR_H = 12
LATER_BAR_PAD_R = 16

LATER_ROW_H = (
    (LATER_Y1 - LATER_Y0 - 2 * LATER_PAD_Y) - LATER_ROW_GAP * (LATER_ROWS - 1)
) / LATER_ROWS
assert LATER_ROW_H == 24, "forecast rows must be 24 px"

LATER_BAR_X0 = RIGHT_X0 + LATER_DAY_COL + LATER_GLYPH_COL           # 483
LATER_BAR_X1 = RIGHT_X1 - LATER_HIGH_COL - LATER_BAR_PAD_R          # 704
LATER_BAR_MIN = 0.34        # the shortest bar is a third of the track
LATER_BAR_SPAN = 0.66


# ── Small helpers ─────────────────────────────────────────────────────────────

def _num(value):
    """Return value only if it is a real number.

    The old code used `obs.get("x") or 0`, which treats a legitimate 0 as
    missing. A genuine 0 C feels-like silently became the air temperature.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    return None


@lru_cache(maxsize=64)
def get_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError as e:
        print(f"Font load error for {path}: {e}")
        return ImageFont.load_default()


def semibold(size):
    """Jost 600 — every numeral, and the headline."""
    return get_font(FONT_SEMIBOLD, size)


def regular(size):
    """Jost 400 — every label and sub-line."""
    return get_font(FONT_REGULAR, size)


def light(size):
    """Jost 300 — the `/` between high and low, and nothing else."""
    return get_font(FONT_LIGHT, size)


def icon(size):
    return get_font(FONT_ICON, size)


def fonts_loaded():
    """True when the real TTFs are in place rather than PIL's fallback.

    The width assertions below are meaningless against the default bitmap
    font, and a missing font should not stop the panel drawing.
    """
    return all(
        isinstance(get_font(path, 20), ImageFont.FreeTypeFont)
        for path in (FONT_SEMIBOLD, FONT_REGULAR, FONT_LIGHT, FONT_ICON)
    )


def _rounded(value, decimals):
    """Round for display, without printing a negative zero.

    -0.4 C formats to "-0" at zero decimals, which reads as a distinct
    temperature rather than as zero.
    """
    out = f"{value:.{decimals}f}"
    return out[1:] if out.startswith("-") and float(out) == 0 else out


def fmt_num(value, decimals=0, suffix=""):
    if value is None:
        return DASH
    return f"{_rounded(value, decimals)}{suffix}"


# ── Display units ─────────────────────────────────────────────────────────────
# Every value in the payload is metric. These convert at the moment of
# printing and nowhere else, which is what keeps thresholds and bands honest.

KM_PER_MILE = 1.609344
MM_PER_INCH = 25.4

UNIT_TEXT = {
    "metric": {
        "speed": "km/h", "rain": "mm", "distance": "km",
        "wind_label": "WIND KM/H", "rain_label": "RAIN MM",
    },
    "imperial": {
        "speed": "mph", "rain": "in", "distance": "mi",
        # "RAIN IN" reads as a preposition at a glance; the cell has room.
        "wind_label": "WIND MPH", "rain_label": "RAIN INCHES",
    },
}


def imperial():
    return UNITS == "imperial"


def unit_text(key):
    return UNIT_TEXT[UNITS][key]


def fmt_temp(value, decimals=0):
    """A Celsius reading in the display unit, with a bare degree sign."""
    if value is None:
        return DASH
    if imperial():
        value = value * 9 / 5 + 32
    return f"{_rounded(value, decimals)}°"


def fmt_speed(kph, decimals=0):
    if kph is not None and imperial():
        kph = kph / KM_PER_MILE
    return fmt_num(kph, decimals)


def fmt_rain(mm):
    """Millimetres to one decimal; inches need two to show a drizzle at all."""
    if mm is not None and imperial():
        return fmt_num(mm / MM_PER_INCH, 2)
    return fmt_num(mm, 1)


def fmt_distance(km):
    if km is not None and imperial():
        km = km / KM_PER_MILE
    return fmt_num(km, 0)


def hhmm(epoch):
    if not epoch:
        return DASH
    return time.strftime("%H:%M", time.localtime(epoch))


def sentence_case(value):
    """First letter up, the rest down.

    The headline is sentence case, not caps: all-caps is reserved for the
    tracked micro-labels, it stops the panel shouting twice, and it is what
    makes a long condition fit — `Thunderstorms possible` in caps would
    not.
    """
    text = " ".join((value or "").split())
    return text[:1].upper() + text[1:].lower() if text else ""


# ── Text: tracking and tabular figures ────────────────────────────────────────

DIGITS = "0123456789"


@lru_cache(maxsize=128)
def digit_advance(font):
    """The advance every digit is drawn on, so figures are tabular.

    Jost's digits differ by a third — `1` is 45 units where `0` is 60 — so
    a value shuffles sideways as it changes, which is visible jitter and
    needless extra e-ink repainting. This is what
    `font-variant-numeric: tabular-nums` does in the design file. Doing it
    here rather than through the OpenType `tnum` feature keeps it working on
    a Pillow built without libraqm, which is the common case on a Pi.
    """
    return max(font.getlength(c) for c in DIGITS)


def text_width(content, font, track=0.0):
    """Width of a string as draw_text will lay it out.

    Kerning is ignored, which makes this very slightly wide for lettered
    strings — the fit tests below are the only callers and erring wide is
    the right direction for them.
    """
    if not content:
        return 0.0
    advance = digit_advance(font)
    total = sum(advance if c in DIGITS else font.getlength(c) for c in content)
    return total + track * (len(content) - 1)


def hard_text(draw, xy, content, font, fill):
    """Draw one run of text with no anti-aliased pixels.

    PIL anti-aliases text, and with DITHER_NONE every grey edge pixel lands
    on whichever of the seven inks is nearest in RGB. Mid-grey is nearest to
    orange — so black type on white picks up a coloured fringe that is very
    visible at 172 px. Type here is only ever black, so the coverage mask is
    thresholded and the fill painted through it. The result is a hard edge,
    which is the correct rendering for a panel with seven flat inks and no
    dithering.
    """
    box = draw.textbbox(xy, content, font=font, anchor="ls")
    left, top = int(box[0]) - 1, int(box[1]) - 1
    size = (int(box[2]) - left + 2, int(box[3]) - top + 2)
    if size[0] <= 0 or size[1] <= 0:
        return
    mask = Image.new("L", size, 0)
    ImageDraw.Draw(mask).text(
        (xy[0] - left, xy[1] - top), content, font=font, fill=255, anchor="ls",
    )
    draw.bitmap((left, top), mask.point(lambda v: 255 if v >= 128 else 0), fill=fill)


def draw_text(draw, x, baseline, content, font, track=0.0, fill=BLACK, align="left"):
    """Draw one line on a baseline, with letter-spacing and tabular figures.

    PIL has no tracking and no `tnum`, so both are applied by hand. Digits
    are placed one at a time, centred in the tabular advance; runs of
    anything else are drawn whole when there is no tracking, so the face's
    kerning survives in the headline and the sub-line.

    `stroke_width` is never passed. The old faces used a stroke to fake a
    bold weight, which grows glyphs outward and closes the counters of 8, 6
    and 0; Jost 600 is an actual weight and needs none.
    """
    if not content:
        return
    if align == "right":
        x -= text_width(content, font, track)
    advance = digit_advance(font)
    run = ""

    def flush(x):
        if run:
            hard_text(draw, (x, baseline), run, font, fill)
            x += font.getlength(run)
        return x

    for char in content:
        if char in DIGITS:
            x = flush(x)
            run = ""
            natural = font.getlength(char)
            hard_text(draw, (x + (advance - natural) / 2, baseline), char, font, fill)
            x += advance + track
        elif track:
            x = flush(x)
            run = ""
            hard_text(draw, (x, baseline), char, font, fill)
            x += font.getlength(char) + track
        else:
            run += char
    flush(x)


def baseline_for(draw, ref, font, centre_y):
    """Baseline that centres `ref`'s ink on centre_y.

    PIL's "m" anchor centres between ascender and descender, which sits
    digits visibly low because they have no descender — the design's
    line-heights are doing the same job in CSS. `ref` is deliberately a
    reference string rather than the content: passing "0" keeps a numeric
    row on one baseline whatever the value is, which is what stops the
    panel repainting ink that did not need to move.
    """
    if not ref:
        return centre_y
    box = draw.textbbox((0, 0), ref, font=font, anchor="ls")
    return centre_y - (box[1] + box[3]) / 2


def draw_row(draw, x, centre_y, content, font, track=0.0, fill=BLACK,
             align="left", ref="0"):
    """Draw a line centred vertically on centre_y."""
    if not content:
        return
    draw_text(
        draw, x, baseline_for(draw, ref, font, centre_y),
        content, font, track, fill=fill, align=align,
    )


@lru_cache(maxsize=128)
def ink_right(font, char):
    """How far right of its origin a character's ink actually ends.

    Not font.getbbox: for these faces Pillow reports the advance box there,
    which for Jost's `°` at 172 px is 3 px wider than the ink. Right-aligning
    by it leaves the hero short of the edge that the glyph above it reaches.
    """
    pad = int(font.size)
    canvas = Image.new("L", (pad * 3, pad * 3), 0)
    ImageDraw.Draw(canvas).text((pad, pad * 2), char, font=font, fill=255, anchor="ls")
    # Thresholded exactly as hard_text paints it, so a faint anti-aliased
    # edge that never reaches the panel does not count as ink.
    box = canvas.point(lambda v: 255 if v >= 128 else 0).getbbox()
    return (box[2] - pad) if box else font.getlength(char)


def draw_glyph(draw, x, centre_y, glyph, size, fill=BLACK, align="left"):
    """Draw an icon glyph at x, centred vertically on its own ink.

    Weather Icons advances vary by more than a factor of two, so a glyph is
    centred on itself rather than on a shared reference. Right alignment
    puts the glyph's ink, not its advance, against x: the side bearings
    differ from glyph to glyph, and aligning advances would leave each
    condition a different distance from the edge.
    """
    if not glyph:
        return
    font = icon(size)
    if align == "right":
        x -= ink_right(font, glyph)
    draw_text(draw, x, baseline_for(draw, glyph, font, centre_y), glyph, font, fill=fill)


def temp_band_ink(value):
    """Absolute temperature to its ink. Divergent, white in the middle."""
    if value is None:
        return WHITE
    for ceiling, ink in TEMP_BANDS:
        if ceiling is None or value < ceiling:
            return ink
    return INK_RED


def is_night(weather):
    now = weather.get("obs_time") or time.time()
    sunrise, sunset = weather.get("sunrise"), weather.get("sunset")
    if not sunrise or not sunset:
        return False
    return now < sunrise or now > sunset


def condition_glyph(icon_name, night=False):
    name = (icon_name or "").lower()
    if "thunder" in name or "storm" in name:
        return WI["thunderstorm-night"] if night else WI["thunderstorm"]
    if "sleet" in name or "wintry" in name or "hail" in name:
        return WI["sleet"]
    if "snow" in name:
        if "partly" in name or "possibly" in name:
            return WI["snow-night"] if night else WI["day-snow"]
        return WI["snow"]
    if "rain" in name or "drizzle" in name:
        if "partly" in name or "possibly" in name:
            return WI["rain-night"] if night else WI["day-rain"]
        return WI["rain"]
    if "fog" in name or "haze" in name or "mist" in name:
        return WI["fog-night"] if night else WI["fog"]
    if "partly" in name:
        return WI["partly-cloudy-night"] if night else WI["partly-cloudy-day"]
    if "cloud" in name or "overcast" in name:
        return WI["cloudy"]
    if "clear" in name:
        return WI["clear-night"] if night else WI["clear-day"]
    return WI["na"]


def get_wind_direction(degrees):
    if degrees is None:
        return DASH
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    return dirs[round(degrees / (360.0 / len(dirs))) % len(dirs)]


# ── Persistent state ──────────────────────────────────────────────────────────

def load_json(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e:
        print(f"Could not write {path}: {e}")


def load_state():
    return load_json(STATE_FILE)


def save_state(state):
    save_json(STATE_FILE, state)


# What state["displayed"] holds while the panel shows the error screen
# rather than a temperature. Never equal to a formatted temperature.
DISPLAYED_ERROR = "error"


def current_boot_id():
    """The kernel's ID for this boot, or None off Linux.

    The refresh schedule is stored as wall-clock time and survives a reboot,
    so without this the first tick after boot reads the previous boot's
    next_due and skips. That leaves the panel blank, or showing whatever it
    held before, for up to an hour after power-on. Worse, a Pi has no
    RTC: until NTP syncs, the clock sits at the shutdown time, which is
    exactly when an old next_due looks most in the future.
    """
    try:
        with open("/proc/sys/kernel/random/boot_id") as f:
            return f.read().strip() or None
    except OSError:
        return None


# ── Reliability helpers ───────────────────────────────────────────────────────

def wait_for_network(timeout=120):
    """Block until swd.weatherflow.com is reachable or timeout expires."""
    print(f"Waiting for network (up to {timeout}s)...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("swd.weatherflow.com", 443), timeout=5):
                print("Network ready.")
                return True
        except OSError:
            time.sleep(5)
    print("Network not available — attempting fetch anyway.")
    return False


# ── API fetch ─────────────────────────────────────────────────────────────────

UNIT_PARAMS = {
    "units_temp": "c",
    "units_wind": "kph",
    "units_pressure": "mb",
    "units_precip": "mm",
    "units_distance": "km",
}


def forecast_unit_params():
    """The forecast request's units: Fahrenheit when the panel shows it.

    The forecast rounds its temperatures to whole degrees in whichever unit
    is asked for, so a whole Celsius high converted to Fahrenheit can be a
    degree away from the high Tempest itself forecasts in Fahrenheit. Asking
    for Fahrenheit and converting it to Celsius on the way in keeps the
    payload metric and still prints Tempest's own figure, because fmt_temp
    converts it straight back.
    """
    if imperial():
        return {**UNIT_PARAMS, "units_temp": "f"}
    return UNIT_PARAMS


def forecast_temp(value):
    """A forecast temperature, in Celsius whatever unit it was requested in."""
    value = _num(value)
    if value is None or not imperial():
        return value
    return (value - 32) * 5 / 9


def forecast_temp_delta(value):
    """A forecast temperature difference, which converts without the offset."""
    value = _num(value)
    if value is None or not imperial():
        return value
    return value * 5 / 9


def fetch_weather(retries=3):
    """Fetch from the Tempest API with exponential-backoff retry.

    The payload is always metric, whatever UNITS says, so every threshold
    shares one unit system, and the fmt_* helpers convert to imperial at
    the moment of printing. The one exception on the wire is
    the forecast, which is fetched in Fahrenheit for an imperial panel and
    converted to Celsius here; see forecast_unit_params.
    """
    last_err = None
    session = requests.Session()
    for attempt in range(retries):
        if attempt > 0:
            delay = 10 * (2 ** (attempt - 1))
            print(f"Retry {attempt}/{retries-1} in {delay}s...")
            time.sleep(delay)
        try:
            obs_response = session.get(
                f"{API_BASE_URL}/observations/station/{STATION_ID}",
                params={"token": TOKEN, **UNIT_PARAMS},
                timeout=HTTP_TIMEOUT,
            )
            obs_response.raise_for_status()
            r_obs = obs_response.json()
            if "obs" not in r_obs or not r_obs["obs"]:
                raise ValueError("No observations in API response")
            obs = r_obs["obs"][0]

            forecast_response = session.get(
                f"{API_BASE_URL}/better_forecast",
                params={
                    "station_id": STATION_ID, "token": TOKEN,
                    **forecast_unit_params(),
                },
                timeout=HTTP_TIMEOUT,
            )
            forecast_response.raise_for_status()
            r_for = forecast_response.json()
            if "forecast" not in r_for:
                raise ValueError("No forecast in API response")
            current = r_for.get("current_conditions", {})
            daily = r_for["forecast"].get("daily", [])
            hourly = r_for["forecast"].get("hourly", [])
            if not daily:
                raise ValueError("Empty daily forecast")

            # Five days: that is what the LATER zone draws.
            forecast_daily = [
                {
                    "day": time.strftime(
                        "%a", time.localtime(_num(day.get("day_start_local")) or 0)
                    ).upper(),
                    "high": forecast_temp(day.get("air_temp_high")),
                    "low": forecast_temp(day.get("air_temp_low")),
                    "icon": day.get("icon", "cloudy"),
                    "conditions": day.get("conditions", ""),
                    "precip_prob": _num(day.get("precip_probability")),
                }
                for day in daily[:5]
            ]

            forecast_hourly = [
                {
                    "time": _num(hour.get("time")),
                    "prob": _num(hour.get("precip_probability")),
                    "type": hour.get("precip_type"),
                    "temp": forecast_temp(hour.get("air_temperature")),
                    "icon": hour.get("icon"),
                }
                for hour in hourly[:24]
            ]

            return {
                "temp": _num(obs.get("air_temperature")),
                "feels_like": forecast_temp(current.get("feels_like")) if _num(
                    current.get("feels_like")
                ) is not None else _num(obs.get("feels_like")),
                "condition": current.get("conditions") or daily[0].get("conditions") or "",
                "icon_name": current.get("icon") or daily[0].get("icon") or "clear-day",
                "obs_time": _num(obs.get("timestamp")),
                "today_high": forecast_temp(daily[0].get("air_temp_high")),
                "today_low": forecast_temp(daily[0].get("air_temp_low")),
                "today_conditions": daily[0].get("conditions", ""),
                "dew_point": _num(obs.get("dew_point")),
                "wind_avg": _num(obs.get("wind_avg")),
                "wind_gust": _num(obs.get("wind_gust")),
                "wind_dir": _num(obs.get("wind_direction")),
                "pressure": _num(obs.get("sea_level_pressure")),
                "pressure_trend": (current.get("pressure_trend") or "steady").lower(),
                "rain_today": _num(obs.get("precip_accum_local_day")),
                "rain_yesterday": _num(obs.get("precip_accum_local_yesterday")),
                "rain_minutes_today": _num(obs.get("precip_minutes_local_day")),
                "humidity": _num(obs.get("relative_humidity")),
                "wet_bulb": forecast_temp(current.get("wet_bulb_temperature")),
                "delta_t": forecast_temp_delta(current.get("delta_t")),
                "battery": _num(obs.get("battery")),
                "solar_radiation": _num(obs.get("solar_radiation")),
                "brightness": _num(obs.get("brightness")),
                "lightning_count": _num(obs.get("lightning_strike_count")),
                "lightning_distance": _num(obs.get("lightning_strike_last_distance")),
                "lightning_epoch": _num(obs.get("lightning_strike_last_epoch")),
                "sunrise": _num(daily[0].get("sunrise")),
                "sunset": _num(daily[0].get("sunset")),
                "daily": forecast_daily,
                "hourly": forecast_hourly,
                "fetched_at": time.time(),
            }
        except Exception as e:
            last_err = e
            print(f"Fetch attempt {attempt+1}/{retries} failed: {e}")

    print(f"All {retries} fetch attempts failed. Last error: {last_err}")
    return None


# ── Today's measured range ────────────────────────────────────────────────────
# The station endpoint carries only the latest reading, and the forecast's
# high and low are predictions. What the station has actually measured
# since midnight comes from the device's minute-by-minute history, read
# incrementally: each poll fetches only the minutes since the last one, and
# the running high and low live in the state file.

TEMPEST_DEVICE_TYPE = "ST"
HUB_DEVICE_TYPE = "HB"
OBS_ST_EPOCH = 0            # obs_st row layout, from the Tempest API docs
OBS_ST_AIR_TEMP = 7         # always Celsius; the device endpoint has no units
# Re-read a little before the last minute seen, in case a reading reached
# the server late. High and low are unaffected by seeing a reading twice.
HISTORY_OVERLAP_SECONDS = 600


class WrongDeviceType(ValueError):
    """The cached device ID did not return Tempest (obs_st) readings."""


def local_midnight(epoch):
    t = time.localtime(epoch)
    return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))


def find_tempest_device(session):
    """The Tempest sensor's device ID, from the station's device list.

    The device type code is not in the published API spec. Tempest sensors
    report "ST" and hubs "HB", so "ST" is preferred and otherwise any
    active device that is not the hub is tried; fetch_device_temps then
    checks that what comes back really is Tempest data.
    """
    response = session.get(
        f"{API_BASE_URL}/stations/{STATION_ID}",
        params={"token": TOKEN},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    body = response.json()
    # The spec calls this list "locations"; live responses use "stations".
    stations = body.get("stations") or body.get("locations") or []
    devices = [
        device
        for station in stations
        for device in station.get("devices") or []
        # Only a device with a serial number still sends observations.
        if device.get("device_id") and device.get("serial_number")
    ]
    for device in devices:
        if device.get("device_type") == TEMPEST_DEVICE_TYPE:
            return device["device_id"]
    for device in devices:
        if device.get("device_type") != HUB_DEVICE_TYPE:
            return device["device_id"]
    raise ValueError("no Tempest device found on this station")


def fetch_device_temps(session, device_id, start, end):
    """(epoch, °C) for every minute the device reported between start and end."""
    response = session.get(
        f"{API_BASE_URL}/observations/device/{device_id}",
        params={"token": TOKEN, "time_start": int(start), "time_end": int(end)},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("type") not in (None, "obs_st"):
        raise WrongDeviceType(f"device {device_id} returned {body.get('type')}")
    readings = []
    for row in body.get("obs") or []:
        if not isinstance(row, list) or len(row) <= OBS_ST_AIR_TEMP:
            continue
        epoch, temp = _num(row[OBS_ST_EPOCH]), _num(row[OBS_ST_AIR_TEMP])
        if epoch is not None and temp is not None:
            readings.append((epoch, temp))
    return readings


def update_measured_range(weather, state):
    """Fold the minutes since the last poll into today's high and low.

    A failure here never touches the weather: the range keeps what it had,
    and because `through` only advances on readings actually received, the
    next successful poll backfills whatever was missed — including a Pi
    that was switched off for part of the day.
    """
    now = time.time()
    midnight = local_midnight(now)
    day = time.strftime("%Y-%m-%d", time.localtime(now))

    measured = state.get("measured")
    if (
        not isinstance(measured, dict)
        or measured.get("day") != day
        or measured.get("station") != STATION_ID
    ):
        measured = {
            "day": day, "station": STATION_ID,
            "high": None, "low": None, "through": midnight,
        }

    def include(temp):
        if measured["high"] is None or temp > measured["high"]:
            measured["high"] = temp
        if measured["low"] is None or temp < measured["low"]:
            measured["low"] = temp

    session = requests.Session()
    device_id = state.get("device_id") if state.get("device_station") == STATION_ID else None
    try:
        if device_id is None:
            device_id = find_tempest_device(session)
            state["device_id"], state["device_station"] = device_id, STATION_ID
        start = max(midnight, measured["through"] - HISTORY_OVERLAP_SECONDS)
        for epoch, temp in fetch_device_temps(session, device_id, start, now):
            if epoch >= midnight:
                include(temp)
                measured["through"] = max(measured["through"], epoch)
    except WrongDeviceType as e:
        print(f"{e} — will look the device up again next time.")
        state.pop("device_id", None)
    except Exception as e:
        print(f"Could not read today's history: {e} — keeping the range so far.")

    # The latest reading counts too, so the hero can never sit outside the
    # range printed beneath it. It does not move `through`: only history
    # does, so a gap in the history is still backfilled later.
    obs_time = weather.get("obs_time")
    if weather.get("temp") is not None and obs_time and obs_time >= midnight:
        include(weather["temp"])

    state["measured"] = measured
    weather["measured_high"] = measured["high"]
    weather["measured_low"] = measured["low"]


def fetch_all():
    """The weather, or None when it could not be fetched.

    Shared by the panel and the desktop preview so the two render the same
    thing. There is no fallback to an older reading: on the panel a failed
    fetch simply leaves the last paint in place.
    """
    weather = fetch_weather()
    if weather is None:
        return None
    state = load_state()
    update_measured_range(weather, state)
    save_state(state)
    return weather


# ── The NEXT zone's content ───────────────────────────────────────────────────

def select_concern(weather):
    """Highest-priority *active* concern, held for as long as it is active.

    Deliberately not a round-robin: at a 15-minute cadence a five-item
    rotation shows any given item for 15 minutes in every 75, so you can
    walk up wanting the wind and have to wait. Nothing here rotates.

    Returns a headline in sentence case and a list of sub-line clauses in
    priority order — draw_next drops them from the end until the line fits,
    so the clause that matters most is always the one that survives.
    """
    now = weather.get("obs_time") or time.time()
    exact = weather.get("temp")
    # The hero rounds to whole degrees, so the exact reading lives here.
    exact_clause = fmt_temp(exact, 1) if exact is not None else None

    # 1. Station health. No ambient state: it appears here or not at all.
    obs_time = weather.get("obs_time")
    # Measured against when the data was fetched, not the wall clock, so a
    # --scenario render, whose observation time is pinned to 15:30, stays
    # that scenario at any hour of the day.
    reference = weather.get("fetched_at") or time.time()
    if obs_time and (reference - obs_time) > STATION_SILENT_SECONDS:
        silent_for = int((reference - obs_time) / 60)
        return {
            "glyph": WI["na"],
            "headline": "Station silent",
            "clauses": [f"last {hhmm(obs_time)}", f"{silent_for} min ago"],
        }
    battery = weather.get("battery")
    if battery is not None and battery < BATTERY_LOW_VOLTS:
        return {
            "glyph": WI["na"],
            "headline": "Battery low",
            "clauses": [f"{battery:.2f} V", exact_clause],
        }

    # 2. Lightning inside 15 km in the last 30 minutes.
    strike_epoch = weather.get("lightning_epoch")
    strike_km = weather.get("lightning_distance")
    if (
        strike_epoch
        and (now - strike_epoch) <= LIGHTNING_RECENT_SECONDS
        and strike_km is not None
        and strike_km <= LIGHTNING_NEAR_KM
    ):
        count = weather.get("lightning_count")
        distance = f"{fmt_distance(strike_km)} {unit_text('distance')}"
        clauses = [f"nearest strike {distance}", hhmm(strike_epoch)]
        if count:
            clauses.append(f"{int(count)} strikes")
        return {
            "glyph": WI["lightning"],
            "headline": f"Lightning {distance}",
            "clauses": clauses,
        }

    # 3. Precipitation starting or stopping within three hours.
    transition = precip_transition(weather)
    if transition:
        return transition

    # 4. Gust above the threshold.
    gust = weather.get("wind_gust")
    if gust is not None and gust >= GUST_THRESHOLD_KPH:
        direction = get_wind_direction(weather.get("wind_dir"))
        return {
            "glyph": WI["wind"],
            "headline": f"Gusts {fmt_speed(gust)} {unit_text('speed')}",
            "clauses": [
                f"{direction} · avg {fmt_speed(weather.get('wind_avg'))}",
                exact_clause,
            ],
        }

    # 5. Frost crossing — air temperature or dew point through 0 C.
    dew = weather.get("dew_point")
    low = weather.get("today_low")
    if any(v is not None and v <= 0 for v in (exact, dew, low)):
        return {
            "glyph": WI["snowflake"],
            "headline": "Frost",
            "clauses": [f"low {fmt_temp(low)}", f"dew {fmt_temp(dew)}", exact_clause],
        }

    # 6. Nothing active: say what the sky is doing now.
    # This used to read "All clear today" over the day's conditions, which
    # the API words as sky ("Partly Cloudy", "Rain Possible"), so the panel
    # said all clear and partly cloudy in one breath. It also promised the
    # whole day while only the next few hours are checked above. The
    # current condition is what you would see out of the window, and when
    # anything above becomes active it takes this slot, so the absence of
    # a concern is still legible.
    trend = weather.get("pressure_trend")
    condition = weather.get("condition") or weather.get("today_conditions")
    return {
        "glyph": condition_glyph(weather.get("icon_name"), is_night(weather)),
        "headline": sentence_case(condition) if condition else "Nothing to watch",
        "clauses": [
            exact_clause,
            f"dew {fmt_temp(dew)}" if dew is not None else None,
            f"pressure {trend}" if trend in ("rising", "falling") else None,
        ],
    }


def precip_transition(weather):
    """Precipitation starting or stopping inside the next three hours.

    Times are printed absolutely. The panel only repaints when the
    temperature changes, so it can be hours old, and "in 3h 25m" would be
    wrong for nearly all of that time; `18:00` never is.
    """
    hours = [h for h in weather.get("hourly", [])[:4] if h.get("prob") is not None]
    if len(hours) < 2:
        return None

    wet = [h["prob"] >= PRECIP_LIKELY_PCT for h in hours]
    # The type of the wet hours, not the first hour: a dry hour still
    # carries a precip_type, and taking it named snow "Rain".
    kind = None
    for hour, is_wet in sorted(zip(hours, wet), key=lambda pair: not pair[1]):
        if hour.get("type"):
            kind = str(hour["type"]).lower()
            break
    stormy = any("thunder" in str(h.get("icon") or "").lower() for h in hours)

    if stormy:
        word, glyph = "Storm", WI["thunderstorm"]
    elif kind == "snow":
        word, glyph = "Snow", WI["snow"]
    elif kind == "sleet":
        word, glyph = "Sleet", WI["sleet"]
    else:
        word, glyph = "Rain", WI["rain"]

    exact = weather.get("temp")

    def clauses(hour):
        rain = weather.get("rain_today")
        return [
            f"{int(hour['prob'])}% chance",
            fmt_temp(exact, 1) if exact is not None else None,
            f"{fmt_rain(rain)} {unit_text('rain')} so far" if rain is not None else None,
        ]

    for i in range(1, len(wet)):
        if wet[i] and not wet[i - 1]:
            hour = hours[i]
            return {
                "glyph": glyph,
                "headline": f"{word} from {hhmm(hour['time'])}",
                "clauses": clauses(hour),
            }
        if wet[i - 1] and not wet[i]:
            hour = hours[i]
            return {
                "glyph": glyph,
                "headline": f"{word} until {hhmm(hour['time'])}",
                "clauses": clauses(hours[i - 1]),
            }

    if wet and wet[0]:
        return {
            "glyph": glyph,
            "headline": f"{word} now",
            "clauses": clauses(hours[0]),
        }
    return None


# ── Headline copy ─────────────────────────────────────────────────────────────

WORD_ABBREVIATIONS = {
    "precipitation": "precip",
    "temperature": "temp",
    "kilometre": "km",
}


def shorten_headline(draw, headline, font, max_width, track=0.0):
    """Make the headline fit on one line without shrinking the type.

    The type size comes from a legibility budget, so when a string is too
    long the copy gives way and never the size. In order: the text as
    given, then known abbreviations, then the last two words, then the
    last word, then a truncation.
    """
    short = sentence_case(
        " ".join(WORD_ABBREVIATIONS.get(w.lower(), w) for w in headline.split())
    )

    candidates = [sentence_case(headline), short]
    words = short.split()
    if len(words) > 2:
        candidates.append(sentence_case(" ".join(words[-2:])))
    if len(words) > 1:
        candidates.append(sentence_case(words[-1]))

    for candidate in candidates:
        if candidate and text_width(candidate, font, track) <= max_width:
            return candidate

    trimmed = candidates[-1]
    while trimmed and text_width(trimmed + DASH, font, track) > max_width:
        trimmed = trimmed[:-1]
    return (trimmed.rstrip() + DASH) if trimmed else DASH


def fit_clauses(clauses, font, max_width, track=0.0):
    """Drop trailing clauses until the sub-line fits its zone."""
    parts = [str(c).strip() for c in clauses if c]
    while parts:
        candidate = f" {MIDDOT} ".join(parts)
        if text_width(candidate, font, track) <= max_width:
            return candidate
        parts.pop()
    return ""


def next_hours(weather, count=NEXT_HOURS):
    """The next `count` whole hours, skipping the one already in progress."""
    now = weather.get("obs_time") or time.time()
    upcoming = [
        h for h in weather.get("hourly", [])
        if h.get("time") and h["time"] > now
    ]
    return upcoming[:count]


# ── Left column: NOW (x 0-361, full height) ───────────────────────────────────

def hero_temp_size(temp_str):
    """172 px, unless the reading is too wide for the column at 172.

    Only three-digit Fahrenheit gets here: `104°` is ~377 px against a
    314 px column, and dropping the degree sign still leaves `104` at ~332.
    The copy has nothing left to give, so the size does, by the least that
    fits — and only on the hottest days of the year.
    """
    budget = LEFT_X1 - LEFT_X0
    size = SIZE_TEMP
    while size > SIZE_HILO and text_width(
        temp_str, semibold(size), TRACK_TEMP * size / SIZE_TEMP
    ) > budget:
        size -= 2
    return size


def draw_now(draw, weather):
    """Condition glyph, temperature and today's range, black on white."""
    draw.rectangle([COLUMN_X, 0, COLUMN_X + RULE_ZONE - 1, HEIGHT - 1], fill=BLACK)

    # The block is a fixed height whatever the values are, so nothing below
    # it moves when a digit is added.
    y = NOW_PAD_Y + (HEIGHT - 2 * NOW_PAD_Y - NOW_BLOCK_H) / 2

    glyph = condition_glyph(weather.get("icon_name"), is_night(weather))
    draw_glyph(
        draw, LEFT_X1, y + NOW_GLYPH_BOX / 2, glyph, SIZE_HERO_GLYPH, align="right",
    )
    y += NOW_GLYPH_BOX + NOW_TEMP_GAP

    # Whole degrees: 22°, not 21.8°. Dropping the decimal is ~35 % narrower,
    # which is what funds 172 px. The exact value is on the NEXT sub-line,
    # read at walk-up — nobody decides anything on 0.8 C from a sofa.
    temp = weather.get("temp")
    if temp is None:
        # An em dash at 172 px is a solid slab that reads as a redaction
        # rather than as a missing reading, so the no-data mark drops to the
        # tier-2 size on the same baseline.
        temp_str, temp_font, track = DASH, semibold(SIZE_HILO), 0.0
    else:
        temp_str = fmt_temp(temp)
        size = hero_temp_size(temp_str)
        temp_font, track = semibold(size), TRACK_TEMP * size / SIZE_TEMP
    # Right-justified to the column's inner edge, so the units digit and the
    # degree sign hold still as the reading changes length and the number
    # sits against the rule, next to the right column it is read with. The
    # last character's ink, not its advance, meets the edge, so the number
    # lines up exactly with the glyph above it.
    last = temp_str[-1]
    trailing = temp_font.getlength(last) - ink_right(temp_font, last)
    draw_row(
        draw, LEFT_X1 + trailing, y + NOW_TEMP_BOX / 2, temp_str, temp_font, track,
        align="right", ref="0",
    )
    y += NOW_TEMP_BOX + NOW_RULE_ABOVE

    draw.rectangle(
        [LEFT_X0, round(y), LEFT_X1 - 1, round(y) + RULE_INNER - 1], fill=BLACK,
    )
    y += RULE_INNER + NOW_RULE_BELOW

    # Three rows on fixed label columns, each label sharing its value's real
    # baseline — the values start on the same vertical at x=120. TODAY is
    # Tempest's forecast for the day; SO FAR is what the station has
    # actually measured since local midnight.
    rows = [
        ("FEELS", [weather.get("feels_like")]),
        ("TODAY", [weather.get("today_high"), weather.get("today_low")]),
        ("SO FAR", [weather.get("measured_high"), weather.get("measured_low")]),
    ]
    assert len(rows) == NOW_ROWS
    for label, values in rows:
        draw_now_row(draw, y, label, values)
        y += NOW_ROW_H + NOW_ROW_GAP


def draw_now_row(draw, y, label, values):
    """A label, then one value or a high / low pair, on a shared baseline."""
    value_font = semibold(SIZE_HILO)
    track_value = TRACK_VALUE * SIZE_HILO
    slash_font = light(SIZE_SLASH)

    baseline = baseline_for(draw, "0", value_font, y + NOW_ROW_H / 2)
    draw_text(draw, LEFT_X0, baseline, label, regular(SIZE_LABEL), TRACK_LABEL)
    x = LEFT_X0 + NOW_LABEL_COL
    for i, value in enumerate(values):
        if i:
            x += NOW_SLASH_MARGIN
            draw_text(draw, x, baseline, "/", slash_font)
            x += slash_font.getlength("/") + NOW_SLASH_MARGIN
        text = fmt_temp(value)
        draw_text(draw, x, baseline, text, value_font, track_value)
        x += text_width(text, value_font, track_value)


# ── Right zone A: METRICS (y 0-168) ───────────────────────────────────────────

def metric_values(weather):
    """The four cells and their order are fixed forever.

    That is what guarantees nothing is ever missing: the number you want is
    always in the position you last found it. A cell with no data renders
    an em dash — it does not vanish and it is not reordered.

    Units live in the label, never in the value. A cell is only ~217 px
    wide and a value with its unit beside `13h 56m` collides; that was
    measured, not preferred.

    UPDATED is when the figures on the panel were fetched. The panel only
    repaints when the displayed temperature changes, so this is also the
    last time it changed: an old time with a correct temperature means the
    temperature has held, not that the panel has stopped. The NEXT zone
    separately flags a station that has stopped reporting.
    """
    sunrise, sunset = weather.get("sunrise"), weather.get("sunset")
    if sunrise and sunset and sunset > sunrise:
        total = int(sunset - sunrise)
        daylight = f"{total // 3600}h{(total % 3600) // 60:02d}"
    else:
        daylight = DASH

    return [
        {"label": unit_text("wind_label"), "value": fmt_speed(weather.get("wind_avg"))},
        {"label": "UPDATED", "value": hhmm(weather.get("fetched_at"))},
        {"label": unit_text("rain_label"), "value": fmt_rain(weather.get("rain_today"))},
        {"label": "DAYLIGHT", "value": daylight},
    ]


def draw_metrics(draw, weather):
    """2x2, left-aligned, with 2 px dividers between the cells.

    The content is 68 px tall in an ~82 px cell, and that clearance is the
    whole reason the zone is 168 px rather than smaller: the top row sits
    against the panel edge. At 42 px rather than 46 the values gained 8 px
    of air and read larger for it.
    """
    zone_bottom = METRICS_Y1 - RULE_ZONE
    draw.rectangle([RIGHT_X, zone_bottom, WIDTH - 1, METRICS_Y1 - 1], fill=BLACK)

    divider_x = round((RIGHT_X + WIDTH) / 2 - RULE_INNER / 2)
    divider_y = round((METRICS_Y0 + zone_bottom) / 2 - RULE_INNER / 2)
    draw.rectangle(
        [divider_x, METRICS_Y0, divider_x + RULE_INNER - 1, zone_bottom - 1], fill=BLACK,
    )
    draw.rectangle(
        [RIGHT_X, divider_y, WIDTH - 1, divider_y + RULE_INNER - 1], fill=BLACK,
    )

    columns = [RIGHT_X, divider_x + RULE_INNER]
    rows = [(METRICS_Y0, divider_y), (divider_y + RULE_INNER, zone_bottom)]

    label_font = regular(SIZE_LABEL)
    value_font = semibold(SIZE_METRIC)
    track_value = TRACK_VALUE * SIZE_METRIC

    for i, metric in enumerate(metric_values(weather)):
        x = columns[i % 2] + METRIC_PAD_L
        row_top, row_bottom = rows[i // 2]
        top = row_top + (row_bottom - row_top - METRIC_BLOCK_H) / 2
        draw_row(
            draw, x, top + METRIC_LABEL_LINE / 2, metric["label"],
            label_font, TRACK_LABEL, ref="H",
        )
        draw_row(
            draw, x, top + METRIC_LABEL_LINE + METRIC_LABEL_GAP + SIZE_METRIC / 2,
            metric["value"], value_font, track_value,
        )


# ── Right zone B: NEXT (y 168-328) ────────────────────────────────────────────

def draw_next(draw, weather):
    """What is happening, the figures that qualify it, and the next 4 hours.

    White ground: the only colour on the panel is the forecast bars, so
    colour appearing always means temperature.
    """
    zone_bottom = NEXT_Y1 - RULE_ZONE
    draw.rectangle([RIGHT_X, zone_bottom, WIDTH - 1, NEXT_Y1 - 1], fill=BLACK)

    concern = select_concern(weather)
    content_bottom = zone_bottom - NEXT_PAD_BOTTOM
    y = NEXT_Y0 + NEXT_PAD_TOP

    # Title row: glyph in a fixed 54 px column, then the headline.
    draw_glyph(
        draw, RIGHT_X0, y + NEXT_TITLE_H / 2, concern["glyph"], SIZE_NEXT_GLYPH,
    )
    headline_font = semibold(SIZE_HEADLINE)
    headline_x = RIGHT_X0 + NEXT_GLYPH_COL
    headline = shorten_headline(
        draw, concern["headline"], headline_font,
        RIGHT_X1 - headline_x, TRACK_HEADLINE,
    )
    draw_row(
        draw, headline_x, y + NEXT_TITLE_H / 2, headline, headline_font,
        TRACK_HEADLINE, ref="H",
    )
    y += NEXT_TITLE_H + NEXT_SUB_GAP

    clauses = list(concern["clauses"])
    sub_font = regular(SIZE_LABEL)
    sub_line = fit_clauses(clauses, sub_font, RIGHT_X1 - RIGHT_X0)
    draw_row(draw, RIGHT_X0, y + NEXT_SUB_H / 2, sub_line, sub_font, ref="H")

    # Hour strip, pushed to the bottom of the zone under a 2 px rule.
    cell_h = NEXT_HOUR_TIME_H + NEXT_HOUR_GAP + NEXT_HOUR_ROW_H
    cells_top = content_bottom - cell_h
    rule_y = cells_top - NEXT_STRIP_GAP - RULE_INNER
    draw.rectangle(
        [RIGHT_X0, rule_y, RIGHT_X1 - 1, rule_y + RULE_INNER - 1], fill=BLACK,
    )

    cell_w = (RIGHT_X1 - RIGHT_X0) / NEXT_HOURS
    time_font = regular(SIZE_HOUR_TIME)
    temp_font = semibold(SIZE_HOUR_TEMP)
    hours = next_hours(weather)
    for i in range(NEXT_HOURS):
        hour = hours[i] if i < len(hours) else {}
        x = RIGHT_X0 + i * cell_w
        draw_row(
            draw, x, cells_top + NEXT_HOUR_TIME_H / 2,
            hhmm(hour.get("time")), time_font, TRACK_HOUR,
        )
        row_centre = cells_top + NEXT_HOUR_TIME_H + NEXT_HOUR_GAP + NEXT_HOUR_ROW_H / 2
        if hour:
            draw_glyph(
                draw, x, row_centre,
                condition_glyph(hour.get("icon"), is_night(weather)),
                SIZE_HOUR_GLYPH,
            )
        draw_row(
            draw, x + NEXT_HOUR_GLYPH_COL, row_centre,
            fmt_temp(hour.get("temp")), temp_font,
        )


# ── Right zone C: LATER (y 328-480) ───────────────────────────────────────────

def draw_later(draw, weather):
    """Five days: name, condition glyph, temperature bar, high.

    Length and colour answer different questions and stay independent.
    Length is relative to this week's own min and max, so it shows the
    week's shape; fill is absolute against the temperature bands, so it
    shows the week's level. A flat hot week keeps its shape, and a mild
    week spends no ink at all.

    There is no key. Warm-is-warm and blue-is-freezing is a convention
    people already hold, and the high is printed beside every bar, so the
    panel teaches its own scale within a couple of days.
    """
    days = (weather.get("daily") or [])[:LATER_ROWS]
    highs = [d.get("high") for d in days if d.get("high") is not None]
    lowest, highest = (min(highs), max(highs)) if highs else (0.0, 1.0)
    span = (highest - lowest) or 1.0
    track = LATER_BAR_X1 - LATER_BAR_X0

    day_font = regular(SIZE_LABEL)
    high_font = semibold(SIZE_DAY_HIGH)

    # Always five rows, so the geometry cannot move between refreshes even
    # if the forecast comes back short.
    for i in range(LATER_ROWS):
        day = days[i] if i < len(days) else {}
        top = LATER_Y0 + LATER_PAD_Y + i * (LATER_ROW_H + LATER_ROW_GAP)
        centre = top + LATER_ROW_H / 2

        draw_row(draw, RIGHT_X0, centre, day.get("day") or DASH, day_font,
                 TRACK_DAY, ref="H")
        high = day.get("high")
        draw_row(draw, RIGHT_X1, centre, fmt_temp(high), high_font, align="right")
        if high is None:
            continue

        draw_glyph(
            draw, RIGHT_X0 + LATER_DAY_COL, centre,
            condition_glyph(day.get("icon")), SIZE_DAY_GLYPH,
        )

        width = round(
            (LATER_BAR_MIN + (high - lowest) / span * LATER_BAR_SPAN) * track
        )
        bar_top = round(centre - LATER_BAR_H / 2)
        box = [
            LATER_BAR_X0, bar_top,
            LATER_BAR_X0 + width - 1, bar_top + LATER_BAR_H - 1,
        ]
        ink = temp_band_ink(high)
        if ink == WHITE:
            draw.rectangle(box, fill=WHITE, outline=BLACK, width=RULE_INNER)
        else:
            draw.rectangle(box, fill=ink)


# ── Dashboard ─────────────────────────────────────────────────────────────────

def draw_error_screen(draw, message):
    draw.text(
        (WIDTH // 2, HEIGHT // 2), message,
        fill=BLACK, font=semibold(34), anchor="mm", align="center",
    )


def create_dashboard(weather, theme_name="inky"):
    """Render layout 10.

    theme_name is accepted for backwards compatibility with desktop.py;
    the panel and the desktop window render identically, so the desktop
    app is a true preview of what the Inky shows.
    """
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)

    if not weather:
        draw_error_screen(draw, "DATA FETCH ERROR\nCheck Console Logs")
        return img

    try:
        draw_now(draw, weather)
        draw_metrics(draw, weather)
        draw_next(draw, weather)
        draw_later(draw, weather)
    except Exception as e:
        print(f"Error drawing dashboard: {e}")
        img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
        draw = ImageDraw.Draw(img)
        draw_error_screen(draw, "RENDER ERROR\nCheck Console Logs")

    return img


def check_widths():
    """Assert the two strings most likely to overflow actually fit.

    The hero temperature's widest realistic value and the longest
    condition the API words. Both are inside a budget rather than measured
    at draw time, and a budget that is never checked is a budget that has
    already been broken once.
    """
    if not fonts_loaded():
        print("Fonts missing — skipping the width checks.")
        return
    hero = text_width("-19°", semibold(SIZE_TEMP), TRACK_TEMP)
    assert hero <= LEFT_X1 - LEFT_X0, f"hero temperature overflows: {hero:.0f} px"
    # Three-digit Fahrenheit is fitted at draw time rather than budgeted;
    # make sure the fit does not have to go silly small to manage it.
    assert hero_temp_size("118°") >= 140, "three-digit hero shrinks too far"

    # Three-digit Fahrenheit in the narrow columns: the forecast high and
    # the hour strip's temperature.
    later = text_width("118°", semibold(SIZE_DAY_HIGH))
    assert later <= LATER_HIGH_COL, f"forecast high overflows: {later:.0f} px"
    hour_w = text_width("118°", semibold(SIZE_HOUR_TEMP))
    hour_budget = (RIGHT_X1 - RIGHT_X0) / NEXT_HOURS - NEXT_HOUR_GLYPH_COL
    assert hour_w <= hour_budget, f"hour temperature overflows: {hour_w:.0f} px"

    for label in ("FEELS", "TODAY", "SO FAR"):
        width = text_width(label, regular(SIZE_LABEL), TRACK_LABEL)
        assert width <= NOW_LABEL_COL - 8, f"NOW label {label} crowds its value"

    for units in UNIT_SYSTEMS:
        for key in ("wind_label", "rain_label"):
            label = text_width(UNIT_TEXT[units][key], regular(SIZE_LABEL), TRACK_LABEL)
            cell = (WIDTH - RIGHT_X) / 2 - METRIC_PAD_L - METRIC_PAD_R
            assert label <= cell, f"{units} {key} overflows: {label:.0f} px"

    # The longest condition the Tempest API words, which a quiet panel
    # carries as its headline.
    budget = RIGHT_X1 - RIGHT_X0 - NEXT_GLYPH_COL
    condition = text_width(
        "Thunderstorms possible", semibold(SIZE_HEADLINE), TRACK_HEADLINE,
    )
    assert condition <= budget, f"condition overflows: {condition:.0f} px of {budget}"

    # Every glyph the NEXT title row can draw keeps clear of the headline.
    glyph_font = icon(SIZE_NEXT_GLYPH)
    for name, glyph in WI.items():
        left, _, right, _ = glyph_font.getbbox(glyph)
        assert right - left <= NEXT_GLYPH_COL - 8, (
            f"NEXT glyph {name} crowds the headline: {right - left} px"
        )


check_widths()


@lru_cache(maxsize=1)
def get_inky_palette_image():
    palette_img = Image.new("P", (1, 1))
    palette = []
    for color in INKY_PALETTE:
        palette.extend(color)
    palette.extend([255, 255, 255] * (256 - len(INKY_PALETTE)))
    palette_img.putpalette(palette)
    return palette_img


NEUTRAL_TOLERANCE = 40


def flatten_text_edges(img):
    """Resolve anti-aliased greys to black or white before quantising.

    PIL anti-aliases text, and with DITHER_NONE those grey edge pixels land
    on whichever of the seven inks is nearest in RGB — which for mid-grey is
    orange, so black type on white picks up a coloured fringe that is very
    visible at 182 px. Type on this panel is only ever black or white, so
    any near-neutral pixel is a text edge and is resolved to one or the
    other. Coloured fills are left alone: they are not neutral.
    """
    red, green, blue = img.split()
    brightest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
    neutral = ImageChops.difference(brightest, darkest).point(
        lambda v: 255 if v < NEUTRAL_TOLERANCE else 0
    )
    hard = img.convert("L").point(lambda v: 255 if v >= 128 else 0).convert("RGB")
    return Image.composite(hard, img, neutral)


def quantize_for_inky(img):
    return flatten_text_edges(img.convert("RGB")).quantize(
        palette=get_inky_palette_image(),
        dither=DITHER_NONE,
    ).convert("RGB")


# ── Refresh cadence ───────────────────────────────────────────────────────────

def refresh_interval_minutes(weather):
    """Fewer lifetime repaints, better resolution when something is happening."""
    hour = time.localtime().tm_hour
    if hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR:
        return REFRESH_NIGHT_MIN
    if weather:
        upcoming = [
            h.get("prob") for h in weather.get("hourly", [])[:1]
            if h.get("prob") is not None
        ]
        if upcoming and upcoming[0] > 40:
            return REFRESH_ACTIVE_MIN
    return REFRESH_NORMAL_MIN


# ── Entry point ───────────────────────────────────────────────────────────────

# ── Canned scenarios ──────────────────────────────────────────────────────────
# The five states from the design file, so every one can be checked without
# waiting for the weather. These never touch the network.

SCENARIO_ICONS = {
    "clear": "clear-day",
    "partly": "partly-cloudy-day",
    "cloud": "cloudy",
    "rain": "rain",
    "snow": "snow",
    "storm": "thunderstorm",
}

SCENARIOS = {
    "quiet": {
        "temp": 21.8, "feels": 24.4, "high": 30.0, "low": 18.0, "dew": 18.0,
        "icon": "partly", "conditions": "Partly Cloudy",
        "wind": 0.0, "rain": 0.0, "pressure": 1021.0, "daylight": "13h56",
        "so_far": (24.6, 17.2),
        "hours": [("partly", 24.0, 5), ("clear", 24.0, 0),
                  ("partly", 22.0, 0), ("cloud", 21.0, 10)],
        "days": [("clear", 30.0), ("clear", 31.0), ("clear", 28.0),
                 ("cloud", 26.0), ("rain", 24.0)],
    },
    "storm": {
        "temp": 25.4, "feels": 27.1, "high": 26.0, "low": 17.0, "dew": 19.0,
        "icon": "storm", "conditions": "thunderstorms",
        "wind": 34.0, "rain": 4.2, "pressure": 998.0, "daylight": "15h12",
        "so_far": (27.9, 16.8),
        "hours": [("cloud", 25.0, 30), ("rain", 24.0, 70),
                  ("rain", 22.0, 80), ("storm", 21.0, 90)],
        "days": [("storm", 24.0), ("rain", 26.0), ("rain", 22.0),
                 ("cloud", 19.0), ("clear", 23.0)],
    },
    "snow": {
        "temp": -9.2, "feels": -14.3, "high": -8.0, "low": -17.0, "dew": -13.0,
        "icon": "snow", "conditions": "snow all day",
        "wind": 41.0, "rain": 0.0, "pressure": 994.0, "daylight": "8h36",
        "so_far": (-7.8, -16.2),
        "hours": [("cloud", -9.0, 20), ("snow", -10.0, 80),
                  ("snow", -12.0, 90), ("snow", -13.0, 90)],
        "days": [("snow", -8.0), ("snow", -11.0), ("cloud", -11.0),
                 ("clear", -13.0), ("cloud", -10.0)],
    },
    "rain": {
        "temp": 11.3, "feels": 9.4, "high": 13.0, "low": 8.0, "dew": 10.0,
        "icon": "rain", "conditions": "rain easing this evening",
        "wind": 22.0, "rain": 12.6, "pressure": 1002.0, "daylight": "11h08",
        "so_far": (12.4, 8.9),
        "hours": [("rain", 11.0, 90), ("rain", 11.0, 90),
                  ("rain", 10.0, 80), ("cloud", 10.0, 20)],
        "days": [("rain", 12.0), ("rain", 11.0), ("cloud", 13.0),
                 ("rain", 10.0), ("cloud", 14.0)],
    },
    "heat": {
        "temp": 33.4, "feels": 36.2, "high": 34.0, "low": 21.0, "dew": 20.0,
        "icon": "clear", "conditions": "clear and very hot",
        "wind": 8.0, "rain": 0.0, "pressure": 1018.0, "daylight": "15h44",
        "so_far": (33.9, 21.6),
        "hours": [("clear", 33.0, 0), ("clear", 32.0, 0),
                  ("clear", 30.0, 0), ("partly", 28.0, 0)],
        "days": [("clear", 33.0), ("clear", 34.0), ("clear", 35.0),
                 ("clear", 32.0), ("clear", 29.0)],
    },
}


def scenario_weather(name):
    """A payload shaped exactly like fetch_weather's, from canned data."""
    spec = SCENARIOS[name]
    clock = time.localtime()
    midday = time.mktime((clock.tm_year, clock.tm_mon, clock.tm_mday,
                          12, 0, 0, 0, 0, -1))
    now = midday + 3.5 * 3600        # 15:30, so the strip starts at 16:00

    hours, minutes = spec["daylight"].split("h")
    daylight = int(hours) * 3600 + int(minutes) * 60

    return {
        "temp": spec["temp"],
        "feels_like": spec["feels"],
        "condition": spec["conditions"],
        "icon_name": SCENARIO_ICONS[spec["icon"]],
        "obs_time": now,
        "today_high": spec["high"],
        "today_low": spec["low"],
        "measured_high": spec["so_far"][0],
        "measured_low": spec["so_far"][1],
        "today_conditions": spec["conditions"],
        "dew_point": spec["dew"],
        "wind_avg": spec["wind"],
        "wind_gust": spec["wind"] * 1.3,
        "wind_dir": 225,
        "pressure": spec["pressure"],
        "pressure_trend": "falling" if spec["pressure"] < 1000 else "steady",
        "rain_today": spec["rain"],
        "humidity": 70,
        "battery": 2.72,
        "lightning_count": None,
        "lightning_distance": None,
        "lightning_epoch": None,
        "sunrise": midday - daylight / 2,
        "sunset": midday + daylight / 2,
        "daily": [
            {
                "day": time.strftime("%a", time.localtime(now + i * 86400)).upper(),
                "high": high,
                "low": high - 8,
                "icon": SCENARIO_ICONS[cat],
                "conditions": spec["conditions"],
                "precip_prob": None,
            }
            for i, (cat, high) in enumerate(spec["days"])
        ],
        "hourly": [
            {
                "time": midday + (4 + i) * 3600,
                "prob": prob,
                "type": "snow" if cat == "snow" else "rain",
                "temp": temp,
                "icon": SCENARIO_ICONS[cat],
            }
            for i, (cat, temp, prob) in enumerate(spec["hours"])
        ],
        "fetched_at": now,
    }


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Render the Tempest Inky dashboard.")
    parser.add_argument(
        "--force", action="store_true",
        help="Render now, ignoring the adaptive refresh schedule.",
    )
    parser.add_argument(
        "--output", default="dashboard-preview.png",
        help="Where to save the render when no Inky panel is attached. "
             "PNG, not JPEG: the panel renders 7 flat inks, and JPEG "
             "ringing turns those into thousands of colours.",
    )
    parser.add_argument(
        "--preview", metavar="PATH",
        help="Write the rendered PNG to PATH instead of pushing it to the "
             "panel, so the layout can be checked with no display attached. "
             "Ignores the refresh schedule and leaves it untouched.",
    )
    parser.add_argument(
        "--scenario", choices=sorted(SCENARIOS),
        help="Render from canned data matching the design file's scenarios "
             "instead of fetching, so every state can be checked without "
             "waiting for the weather.",
    )
    args = parser.parse_args()

    # A preview or a scenario is a rendering job, not a refresh: it neither
    # consults the schedule nor moves it on.
    render_only = bool(args.preview or args.scenario)

    boot_id = current_boot_id()

    if args.scenario:
        weather = scenario_weather(args.scenario)
    else:
        if not render_only:
            state = load_state()
            next_due = state.get("next_due")
            # A schedule written in a previous boot does not apply to this one.
            new_boot = boot_id is not None and state.get("boot_id") != boot_id
            if new_boot:
                print("No successful refresh yet this boot — refreshing now.")
            elif not args.force and next_due and time.time() < next_due:
                print(f"Not due until {hhmm(next_due)} — skipping "
                      f"(use --force to override).")
                return
            if INKY_AVAILABLE:
                wait_for_network(timeout=120)

        print("Fetching weather...")
        weather = fetch_all()

    if not render_only:
        state = load_state()
        state.pop("payload", None)      # the old stale-render cache; unused
        shown = state.get("displayed")
        if weather is None:
            # A failed fetch never touches the panel: whatever it shows is
            # left alone, and the schedule stays put so the next tick
            # retries. The error screen is only for a panel that has never
            # shown anything, and is painted once, not every tick.
            if shown is not None:
                print("Fetch failed — leaving the panel as it is.")
                save_state(state)
                return
            displayed = DISPLAYED_ERROR
        else:
            displayed = fmt_temp(weather.get("temp"))
            if not args.force and displayed == shown:
                print(f"Temperature still {displayed} — not repainting "
                      f"(use --force to override).")
                state["next_due"] = time.time() + refresh_interval_minutes(weather) * 60
                state["boot_id"] = boot_id
                save_state(state)
                return

    img = quantize_for_inky(create_dashboard(weather, theme_name="inky"))

    if args.preview:
        img.save(args.preview)
        print(f"Saved {args.preview}")
        return

    if INKY_AVAILABLE:
        try:
            panel = auto()
            panel.set_image(img)
            panel.show()
            print("Display updated.")
        except Exception as e:
            print(f"Display error: {e}")
            raise   # let systemd record the failure
    else:
        img.save(args.output)
        print(f"Saved {args.output}")

    # The schedule and the displayed temperature only move on once the panel
    # has actually been painted. Advancing them before the push meant one
    # failed write cost a whole 10-60 minute interval before the next
    # attempt, or worse, that the panel was believed to show a temperature
    # it never received. An error screen leaves the schedule alone so the
    # next tick retries.
    if not render_only:
        state["displayed"] = displayed
        if weather is not None:
            state["next_due"] = time.time() + refresh_interval_minutes(weather) * 60
            state["boot_id"] = boot_id
        save_state(state)


if __name__ == "__main__":
    main()
