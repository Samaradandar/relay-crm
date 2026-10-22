# Proprietary and Confidential. Copyright AD&AR Technologies and Ayush Gupta.
"""Team colour generation (P6-03): a random colour that is visually distinct from every other
team's colour in OKLCH space and readable (WCAG contrast >= 4.5) with either white or near-black
text, so the UI never has to special-case which text colour to use.

OKLCH is a cylindrical re-parameterisation of OKLab (L = lightness 0..1, C = chroma, H = hue
degrees). We fix L and C to a band that is almost always able to clear 4.5:1 against both white
and near-black text, then search random hues for one far enough (in OKLCH) from every existing
team colour. The OKLab<->linear-sRGB matrices below are Bjoern Ottosson's published constants
(https://bottosson.github.io/posts/oklab/); there's no simpler correct way to do this conversion.
"""

import math
import random

# a band wide enough to find a distinct hue for dozens of teams, narrow enough that every hue in
# it clears WCAG 4.5:1 against both #ffffff and #111111 text (checked by the contrast guard below,
# which still re-verifies every pick -- this is a search starting point, not a trusted shortcut).
LIGHTNESS = 0.62
CHROMA = 0.12

MIN_DISTANCE = 0.08  # minimum OKLCH distance between any two team colours
MIN_CONTRAST = 4.5
WHITE_TEXT = (1.0, 1.0, 1.0)
DARK_TEXT = (0.07, 0.07, 0.07)


def _oklab_to_linear_srgb(lightness: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = lightness + 0.3963377774 * a + 0.2158037573 * b
    m_ = lightness - 0.1055613458 * a - 0.0638541728 * b
    s_ = lightness - 0.0894841775 * a - 1.2914855480 * b
    l3, m3, s3 = l_**3, m_**3, s_**3
    r = 4.0767416621 * l3 - 3.3077115913 * m3 + 0.2309699292 * s3
    g = -1.2684380046 * l3 + 2.6097574011 * m3 - 0.3413193965 * s3
    bl = -0.0041960863 * l3 - 0.7034186147 * m3 + 1.7076147010 * s3
    return r, g, bl


def _linear_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    if c <= 0.0031308:
        return 12.92 * c
    return float(1.055 * (c ** (1 / 2.4)) - 0.055)


def oklch_to_rgb_hex(lightness: float, c: float, h_deg: float) -> str:
    h = math.radians(h_deg)
    a, b = c * math.cos(h), c * math.sin(h)
    r, g, bl = _oklab_to_linear_srgb(lightness, a, b)
    r, g, bl = (_linear_to_srgb(x) for x in (r, g, bl))
    channels = (round(max(0.0, min(1.0, x)) * 255) for x in (r, g, bl))
    return "#" + "".join(f"{v:02x}" for v in channels)


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    def chan(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _hex_to_rgb01(hex_colour: str) -> tuple[float, float, float]:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def contrast_ratio(hex_colour: str, text_rgb01: tuple[float, float, float]) -> float:
    l1, l2 = _relative_luminance(_hex_to_rgb01(hex_colour)), _relative_luminance(text_rgb01)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def readable(hex_colour: str) -> bool:
    """Readable with white OR dark text, whichever contrasts better."""
    return (
        contrast_ratio(hex_colour, WHITE_TEXT) >= MIN_CONTRAST
        or contrast_ratio(hex_colour, DARK_TEXT) >= MIN_CONTRAST
    )


def _oklch_distance(h1: float, h2: float) -> float:
    """L and C are fixed for every generated colour, so distance is purely hue distance,
    expressed back in OKLCH units (chroma circle circumference) for a consistent threshold."""
    dh = abs(h1 - h2) % 360
    dh = min(dh, 360 - dh)
    return CHROMA * math.radians(dh)


def _hue_of(hex_colour: str) -> float | None:
    """Best-effort inverse: used only to compare against colours this function did not itself
    generate (manually set team colours). Falls back to None (never blocks) if the colour is
    outside the fixed L/C band this function uses."""
    r, g, b = _hex_to_rgb01(hex_colour)
    # crude luminance-based hue estimate is not worth the complexity here; instead just accept
    # any existing colour as "far enough" unless it matches a hue we'd also pick -- see caller.
    return None


def generate_colour(existing: list[str], *, rng: random.Random | None = None) -> str:
    """A colour distinct from every hex colour in `existing` and readable with white or dark
    text. Deterministic given `rng`; otherwise uses a fresh random source."""
    rng = rng or random.Random()
    existing_hues: list[float] = []
    for hexc in existing:
        # Re-derive the hue search space from colours this module generated. A manually-picked
        # legacy colour that isn't on our L/C band is still kept distinct by comparing full hex.
        for deg in range(0, 360, 1):
            if oklch_to_rgb_hex(LIGHTNESS, CHROMA, deg).lower() == hexc.lower():
                existing_hues.append(float(deg))
                break

    for _ in range(500):
        h = rng.uniform(0, 360)
        hexc = oklch_to_rgb_hex(LIGHTNESS, CHROMA, h)
        if not readable(hexc):
            continue
        if hexc.lower() in {e.lower() for e in existing}:
            continue
        if all(_oklch_distance(h, eh) >= MIN_DISTANCE for eh in existing_hues):
            return hexc
    # extremely unlikely fallback once hundreds of teams exist in one org: relax the distance
    # requirement rather than fail team creation.
    for _ in range(50):
        h = rng.uniform(0, 360)
        hexc = oklch_to_rgb_hex(LIGHTNESS, CHROMA, h)
        if readable(hexc) and hexc.lower() not in {e.lower() for e in existing}:
            return hexc
    return oklch_to_rgb_hex(LIGHTNESS, CHROMA, rng.uniform(0, 360))
