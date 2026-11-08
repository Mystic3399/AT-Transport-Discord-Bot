"""Render the front and back of the A&T artwork concept using sample data."""
import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ART = Path(__file__).resolve().parent
ASSETS = ART.parent
FONT = ASSETS / "profile/fonts/LiberationSans-Bold.ttf"
BADGES = sorted(
    (ASSETS / "badges").glob("*.png"),
    key=lambda path: int(path.stem.rsplit("-", 1)[1]),
)
WHITE = "#edf8ff"
CYAN = "#8adcff"
GOLD = "#f4d587"


def render(side, truck):
    with Image.open(ART / f"backgrounds/{side}.webp") as background:
        canvas = background.convert("RGBA")
    draw = ImageDraw.Draw(canvas)

    def text(x, y, label, size=20, colour=WHITE):
        draw.text((x, y), label, font=ImageFont.truetype(str(FONT), size), fill=colour)

    def panel(box):
        draw.rounded_rectangle(box, radius=12, fill="#081b30", outline="#4289ac", width=2)

    def art(path, box, locked=False, cover=False):
        x, y, w, h = box
        with Image.open(path) as source:
            layer = source.convert("RGBA")
        layer = (ImageOps.fit if cover else ImageOps.contain)(
            layer, (w, h), Image.Resampling.LANCZOS
        )
        if locked:
            alpha = layer.getchannel("A")
            layer = ImageOps.grayscale(layer).convert("RGBA")
            layer.putalpha(alpha.point(lambda value: value * 3 // 4))
        canvas.alpha_composite(layer, (x + (w - layer.width)//2, y + (h - layer.height)//2))

    def badge(index, box):
        art(BADGES[index], box, locked=index > 10)

    def insignia(name, box, locked=False):
        art(ART / "insignias" / (name + ".webp"), box, locked)

    def progress(x, y, width):
        text(x, y, "96,735 / 100,000 REAL MILES", 17, CYAN)
        draw.rounded_rectangle((x, y+30, x+width, y+42), 6, fill="#17384d")
        draw.rounded_rectangle((x, y+30, x+round(width*.96735), y+42), 6, fill="#38bbff")
        text(x, y+53, "3,265 MILES REMAINING", 15, GOLD)

    art(ART / "company-logo.webp", (30, 20, 249, 110))
    title = "DRIVER TACHOGRAPH CARD" if side == "front" else "DRIVER CAREER RECORD"
    text(309, 44, title, 39)
    text(312, 93, "VIRTUAL COMPANY ID  /  A&T TRANSPORT LTD", 17, CYAN)

    if side == "front":
        draw.rounded_rectangle((38, 145, 121, 197), 8, fill="#cfad65", outline="#fff0c4", width=2)
        for x in (63, 94):
            draw.line((x, 146, x, 196), fill="#6b542f", width=2)
        draw.line((39, 171, 120, 171), fill="#6b542f", width=2)
        panel((32, 212, 319, 442))
        art(truck, (40, 220, 271, 214), cover=True)
        text(39, 453, "ASSIGNED TRUCK", 14, CYAN)
        text(39, 473, truck.stem.replace("-", " ").upper(), 15)
        text(343, 149, "DRIVER NAME", 16, CYAN)
        text(343, 174, "MYSTIC", 51)
        text(346, 237, "OWNER / DRIVER", 22)
        text(346, 275, "UNITED KINGDOM", 19, CYAN)
        panel((649, 142, 1059, 315))
        text(669, 152, "LINKED ACCOUNTS", 17, CYAN)
        for i, (service, name) in enumerate((
            ("Steam", "Mystic"), ("Discord", "Mystic"), ("TrucksBook", "Mystic3399"),
            ("Trucky", "Mystic3399"), ("Drivers Hub", "Mystic"),
        )):
            text(670, 178 + i*25, service, 16)
            text(849, 178 + i*25, name, 16)
        panel((335, 324, 1059, 391))
        for x, label, value in ((349,"REAL MILES","96,735"), (587,"TRUCKY RACE","12,450"), (829,"REAL JOBS","110")):
            text(x, 331, label, 14, CYAN)
            text(x, 351, value, 27)
        for i, idx in enumerate((9,10,11)):
            x = 341 + i*110
            badge(idx, (x, 405, 101, 167))
            text(x+25, 579, ("50K","75K","100K")[i], 19, GOLD if i == 1 else WHITE)
        for i, (icon, label) in enumerate((("verified","VERIFIED A&T DRIVER"),("mileage-club-40k","40K MILEAGE CLUB"))):
            y = 405 + i*53
            insignia(icon, (686,y,47,47))
            text(747,y+12,label,17)
        progress(689, 526, 345)
    else:
        panel((30, 137, 510, 630))
        panel((523, 137, 1068, 630))
        text(49, 153, "MYSTIC  /  RANK PROGRESSION", 20, CYAN)
        text(50, 187, "CURRENT RANK", 15)
        text(231, 187, "NEXT RANK", 15)
        badge(10, (51, 206, 129, 171))
        badge(11, (229, 206, 129, 171))
        progress(50, 384, 405)
        for i, (label, value) in enumerate((("REAL JOBS","110"),("AVG JOB","735 MI"),("LONGEST","3,541 MI"),("WEEKLY WINS","0"))):
            x = 50+i*110
            text(x, 466, value, 20)
            text(x, 491, label, 13, CYAN)
        text(49, 517, "PERMANENT ACHIEVEMENTS", 15, CYAN)
        for i, label in enumerate(("40K","100K","250K","500K","750K","1M")):
            x = 49+i*70
            insignia("achievement", (x, 541, 38, 38), i > 0)
            text(x, 580, label, 13, GOLD if i == 0 else WHITE)
        insignia("hall-of-fame", (48, 600, 26, 26), True)
        text(84, 605, "HALL OF FAME  /  NOT YET INDUCTED", 14)
        text(545, 152, "PATH TO IMMORTAL", 22, CYAN)
        for i, path in enumerate(BADGES):
            x, y = 537+(i%6)*87, 189+(i//6)*106
            if i == 10:
                draw.rounded_rectangle((x,y,x+81,y+100), 8, fill="#153a61", outline=CYAN, width=2)
            badge(i, (x+7,y+2,67,80))
            miles = int(path.stem.rsplit("-",1)[1])
            label = "1M" if miles == 1_000_000 else f"{miles//1000}K" if miles else "0"
            text(x+22,y+83,label,14,GOLD if i == 10 else WHITE)

    text(292, 650, "D R I V E N   B E Y O N D   H O R I Z O N S", 18)
    text(900, 655, "SAMPLE DATA", 12, CYAN)
    return canvas.convert("RGB")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("tachograph-previews"))
    parser.add_argument("--truck", choices=[p.stem for p in sorted((ART/"trucks").glob("*.webp"))],
                        default="scania-s-r-next-gen")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    truck = ART / "trucks" / (args.truck + ".webp")
    for side in ("front", "back"):
        render(side, truck).save(args.output / f"tachograph-{side}.png")


if __name__ == "__main__":
    main()
