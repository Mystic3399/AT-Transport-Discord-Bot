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
BRONZE = "#e8a782"
THEMES = {
    "weekly": ("WEEKLY STANDINGS", BLUE, "#082949"),
    "monthly": ("MONTHLY STANDINGS", GOLD, "#35280f"),
    "all-time": ("ALL-TIME STANDINGS", SILVER, "#202b46"),
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
    title, accent, tint = THEMES[period]
    canvas, draw = background(1220, accent, tint)
    place(canvas, ART / "company-logo.webp", (30, 28, 200, 124))
    label(draw, (252, 44), "DRIVER LEADERBOARD", 52, width=790)
    label(draw, (254, 109), title + "  /  REAL MILES", 28, accent)
    draw.line((34, 163, 1046, 163), fill=accent, width=3)

    for index, (x, top, platform, colour, fallback) in enumerate((
        (379, 180, 505, GOLD, "scania-s-r-next-gen"),
        (38, 215, 534, SILVER, "volvo-fh6"),
        (720, 242, 552, BRONZE, "man-euro-6"),
    )):
        if index >= len(entries):
            continue
        name, miles, truck, former = entries[index]
        truck_path = ART / "trucks" / f"{truck}.webp"
        if not truck or not truck_path.is_file():
            truck_path = ART / "trucks" / f"{fallback}.webp"
        draw.rounded_rectangle((x, top, x + 322, 578), radius=12,
                               fill="#09192d", outline=colour, width=3)
        place(canvas, truck_path, (x + 7, top + 7, 308, platform - top - 7), cover=True)
        draw.rectangle((x, platform, x + 322, 598), fill="#142439", outline=colour, width=3)
        draw.rectangle((x, platform, x + 322, platform + 7), fill=colour)
        label(draw, (x + 161, (platform + 598) // 2 + 3), str(index + 1), 37, colour, anchor="mm")
        label(draw, (x + 161, 612), name + (" (Former)" if former else ""),
              31, width=310, anchor="mt")
        label(draw, (x + 161, 652), f"{miles:,} MI", 33, colour, anchor="mt")

    if not entries:
        label(draw, (WIDTH // 2, 390), "No qualifying Real Miles yet", 42, anchor="mt")

    draw.rounded_rectangle((34, 716, 1046, 1138), radius=12,
                           fill="#081528", outline=accent, width=2)
    label(draw, (57, 734), "POS", 26, accent)
    label(draw, (174, 734), "DRIVER", 26, accent)
    label(draw, (1020, 734), "REAL MILES", 26, accent, anchor="rt")
    for index in range(4, 11):
        y = 775 + (index - 4) * 50
        draw.rectangle((39, y, 1041, y + 48), fill="#102743" if index % 2 == 0 else "#0c1d33")
        label(draw, (59, y + 10), f"{index:02}", 28, accent)
        if index <= len(entries):
            name, miles, _, former = entries[index - 1]
            label(draw, (174, y + 9), name + (" (Former)" if former else ""), 30, width=600)
            label(draw, (1020, y + 9), f"{miles:,}", 30, anchor="rt")
    label(draw, (540, 1162), "DRIVEN BEYOND HORIZONS", 27, accent, anchor="mt")
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
    accent = GOLD if permanent else BLUE
    canvas, draw = background(490, accent, "#3b2a13" if permanent else "#062f57")
    place(canvas, ART / "company-logo.webp", (39, 28, 195, 120))
    label(draw, (258, 49), "PERMANENT ACHIEVEMENT" if permanent else "RANK MILESTONE",
          38, accent, width=775)
    draw.line((42, 150, 1038, 150), fill=accent, width=2)
    if permanent:
        key = CLUBS.get(miles, ("achievement", ""))[0]
        place(canvas, ART / "insignias" / f"{key}.webp", (40, 174, 242, 246))
    else:
        asset = ImageOps.contain(badge(miles, False), (242, 246), Image.Resampling.LANCZOS)
        canvas.paste(asset, (40 + (242 - asset.width) // 2, 174 + (246 - asset.height) // 2), asset)
    label(draw, (310, 180), name, 37, width=710)
    label(draw, (310, 241), title, 50, accent, width=710)
    label(draw, (310, 320), f"{miles:,} REAL MILES", 47, width=710)
    label(draw, (540, 443), "DRIVEN BEYOND HORIZONS", 25, accent, anchor="mt")
    return encode(canvas)
