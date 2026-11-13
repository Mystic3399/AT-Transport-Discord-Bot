"""Render the Discord leaderboard and recognition artwork from live bot data."""
from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont, ImageOps

from tachograph import ART, ROOT, CLUBS, badge

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


def hall_of_fame(inductees):
    with Image.open(CAREER_ART / "hall-of-fame.webp") as source:
        canvas = source.convert("RGB")
    draw = ImageDraw.Draw(canvas)
    for tier, y, colour in (
        ("Immortal", 792, WHITE),
        ("Elite", 980, GOLD),
        ("Veteran", 1169, SILVER),
    ):
        names = inductees.get(tier, ())
        captions = [str(name).upper() for name in names[:3]]
        if len(names) > 3:
            captions.append(f"+{len(names) - 3:,} MORE")
        if not captions:
            captions = ["NO INDUCTEES YET"]
        size = 43 if len(captions) == 1 else 28
        for index, caption in enumerate(captions):
            line_y = y + (index - (len(captions) - 1) / 2) * 30
            label(draw, (884, line_y), caption, size, colour, width=381, anchor="mm")
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
