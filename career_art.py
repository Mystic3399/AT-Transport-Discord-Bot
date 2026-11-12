"""Render the Discord leaderboard and recognition artwork from live bot data."""
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageOps

from tachograph import ART, ROOT, CLUBS, badge

WIDTH = 1080
WHITE = "#eef7ff"
BLUE = "#53d7ff"
GOLD = "#ffd477"
SILVER = "#c6d8ea"
CAREER_ART = ROOT / "assets/career"
STANDINGS_TITLES = {
    "weekly": ("WEEKLY STANDINGS", WHITE),
    "monthly": ("MONTHLY STANDINGS", GOLD),
    "all-time": ("ALL-TIME STANDINGS", SILVER),
}


@lru_cache(maxsize=64)
def font(size):
    return ImageFont.truetype(str(ROOT / "assets/profile/fonts/LiberationSans-Bold.ttf"), size)


def label(draw, xy, value, size, colour=WHITE, *, width=None, anchor="lt"):
    value = " ".join(str(value).split())[:160]
    if width:
        while size > 20 and draw.textlength(value, font=font(size)) > width:
            size -= 2
        if draw.textlength(value, font=font(size)) > width:
            while value and draw.textlength(value + "…", font=font(size)) > width:
                value = value[:-1]
            value += "…"
    draw.text(xy, value, font=font(size), fill=colour, anchor=anchor)


def place(canvas, path, box, *, cover=False):
    with Image.open(path) as source:
        asset = source.convert("RGBA")
    left, top, width, height = box
    resize = ImageOps.fit if cover else ImageOps.contain
    asset = resize(asset, (width, height), Image.Resampling.LANCZOS)
    canvas.paste(asset, (left + (width - asset.width) // 2,
                         top + (height - asset.height) // 2), asset)


def background(height, accent, tint):
    canvas = Image.new("RGB", (WIDTH, height), "#050e1d")
    draw = ImageDraw.Draw(canvas)
    r, g, b = (int(tint[i:i+2], 16) for i in (1, 3, 5))
    for y in range(0, height, 2):
        strength = max(0, 1 - abs(y - height * .22) / (height * .65))
        draw.rectangle((0, y, WIDTH, y + 1),
                       fill=(5 + int(r * strength), 14 + int(g * strength), 29 + int(b * strength)))
    for x in range(-height, WIDTH, 82):
        draw.line((x, height, x + height, 0), fill="#213149")
    draw.rounded_rectangle((12, 12, WIDTH - 13, height - 13), radius=22,
                           outline=accent, width=2)
    return canvas, draw


def encode(canvas):
    result = BytesIO()
    canvas.save(result, "PNG")
    return result.getvalue()


def standings(period, entries):
    with Image.open(CAREER_ART / "leaderboard.webp") as source:
        canvas = source.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    title, accent = STANDINGS_TITLES[period]
    label(draw, (382, 186), title, 31, accent, width=425, anchor="mm")

    for (name, miles, former), x in zip(entries[:3], (558, 202, 924)):
        label(draw, (x, 682), name.upper() + (" (Former)" if former else ""),
              34, width=255, anchor="mm")
        label(draw, (x, 726), f"{miles:,} MI", 32, "#071525",
              width=255, anchor="mm")

    if not entries:
        label(draw, (560, 873), "No qualifying Real Miles yet", 30, anchor="mm")

    for position, (name, miles, former) in enumerate(entries[3:10], 4):
        y = 875 + (position - 4) * 55
        label(draw, (119, y), f"{position:02}", 34, anchor="mm")
        label(draw, (479, y), name + (" (Former)" if former else ""),
              35, width=510, anchor="mm")
        label(draw, (921, y), f"{miles:,}", 35, width=300, anchor="mm")
    return encode(canvas)


def hall_of_fame(counts):
    canvas, draw = background(415, GOLD, "#342910")
    place(canvas, ART / "company-logo.webp", (35, 28, 208, 130))
    place(canvas, ART / "insignias/hall-of-fame.webp", (855, 25, 170, 147))
    label(draw, (265, 45), "HALL OF FAME", 63, GOLD, width=570)
    label(draw, (268, 117), "PERMANENT CAREER RECORDS", 26)
    for x, (tier, threshold, colour) in zip((37, 379, 721), (
        ("Immortal", "1,000,000", BLUE), ("Elite", "500,000", GOLD),
        ("Veteran", "250,000", SILVER),
    )):
        draw.rounded_rectangle((x, 187, x + 322, 348), radius=12,
                               fill="#0b1728", outline=colour, width=2)
        label(draw, (x + 161, 207), tier.upper(), 33, colour, anchor="mt")
        label(draw, (x + 161, 251), f"{threshold} REAL MI", 25, anchor="mt")
        count = counts.get(tier, 0)
        label(draw, (x + 161, 291), f"{count} INDUCTED", 24, colour, anchor="mt")
    label(draw, (540, 368), "DRIVEN BEYOND HORIZONS", 25, GOLD, anchor="mt")
    return encode(canvas)


def achievement(name, title, miles, *, permanent=False):
    with Image.open(CAREER_ART / "achievement.webp") as source:
        canvas = source.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    if permanent:
        key = CLUBS.get(miles, ("achievement", ""))[0]
        with Image.open(ART / "insignias" / f"{key}.webp") as source:
            emblem = source.convert("RGBA")
    else:
        emblem = badge(miles, False)
    emblem = ImageOps.contain(emblem, (450, 447), Image.Resampling.LANCZOS)
    canvas.paste(emblem, (438 - emblem.width // 2, 624 - emblem.height), emblem)
    label(draw, (768, 748), name.upper(), 76, width=1140, anchor="mm")
    label(draw, (768, 815), title.upper(), 52, GOLD if permanent else BLUE,
          width=1220, anchor="mm")
    label(draw, (768, 881), f"{miles:,} REAL MILES", 68, width=1230, anchor="mm")
    return encode(canvas)
