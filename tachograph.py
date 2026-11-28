"""A&T tachograph artwork renderer and persistent appearance settings."""
import json
import random
import re
from decimal import Decimal
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter, ImageChops, ImageEnhance

ROOT = Path(__file__).resolve().parent
ART = ROOT / "assets/tachograph"
SHIELDS = ART / "rank-badges/shield-edition/AT-Tachograph-Shield-Badges.zip"
FLAGS = ART / "country-flags/AT-Tachograph-World-Flags.zip"
MILES = (0,5000,10000,15000,20000,25000,30000,35000,40000,50000,75000,
         100000,150000,200000,300000,400000,500000,600000,700000,800000,900000,1000000)
RANK_NAMES = (
    "New Driver", "Road Runner", "Distance Driver", "Aurora Driver",
    "Wolf Pack Driver", "Viking Hauler", "Elite Hauler", "A&T Veteran",
    "A&T Road Legend", "Beyond Horizons", "Horizon Wolf", "A&T Centurion",
    "Viking Pathfinder", "Road Guardian", "Aurora Pathfinder", "Viking Roadmaster",
    "Horizon Legend", "Alpha Roadmaster", "Aurora Commander",
    "Guardian of the Horizon", "Beyond the Horizon", "A&T Immortal",
)
AMBER = "#ffca58"
WHITE, CYAN, GOLD = "#eef7ff", "#7cdcff", "#f1ce79"
CLUBS = {
    40000: ("mileage-club-40k", "MILEAGE CLUB"),
    100000: ("centurion", "CENTURION CLUB"),
    250000: ("veteran", "VETERAN CLUB"),
    500000: ("elite", "ELITE CLUB"),
    750000: ("horizon-elite", "HORIZON ELITE CLUB"),
    1000000: ("immortal", "IMMORTAL CLUB"),
}


def current_club(earned):
    return max((threshold for threshold in earned if threshold in CLUBS), default=None)


SCHEMA = """
CREATE TABLE IF NOT EXISTS driver_card_preferences (
    discord_user_id BIGINT PRIMARY KEY,
    truck_key TEXT NOT NULL,
    country_code TEXT,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


@lru_cache(maxsize=1)
def countries():
    with ZipFile(FLAGS) as archive:
        return json.loads(archive.read("countries.json"))


TRUCK_LABELS = {
    "scania-s-r-next-gen":"Scania S/R Next Gen",
    "mercedes-actros-mp6":"Mercedes-Benz Actros MP6",
    "man-euro-6":"MAN Euro 6",
    "scania-t-cab":"Scania T-Cab",
    "scania-next-gen-torpedo":"Scania Next Gen Torpedo",
    "scania-s-longline":"Scania S Longline",
    "scania-s-wrecker":"Scania S Wrecker",
    "daf-xf":"DAF XF",
    "daf-xg-plus":"DAF XG+",
    "daf-wrecker":"DAF Wrecker",
    "volvo-fh6":"Volvo FH6",
    "mercedes-actros-mp5":"Mercedes-Benz Actros MP5",
    "renault-t":"Renault T",
    "kenworth-w900-highway-killer":"Kenworth W900 — Jon Ruda Highway Killer",
    "volvo-vnl-2025":"Volvo VNL 2025",
    "western-star-57x":"Western Star 57X",
    "scania-t-cab-longline":"Scania T-Cab Longline",
    "freightliner-cascadia":"Freightliner Cascadia",
    "scania-streamline":"Scania R Streamline",
    "scania-164l":"Scania 164L V8 Topline",
    "mercedes-actros-mp6-longline":"Mercedes-Benz Actros MP6 Longline",
    "iveco-s-way-longline":"Iveco S-Way Longline",
    "volvo-fh16-heavy-haul":"Volvo FH16 8×4 Heavy Haul",
    "mercedes-actros-wrecker":"Mercedes-Benz Actros Wrecker",
    "kenworth-t680":"Kenworth T680 Next Generation",
    "peterbilt-389":"Peterbilt 389",
}


def available_trucks():
    return {key:TRUCK_LABELS.get(key,key.replace("-"," ").title()) for key in truck_keys()}


def resolve_truck(value):
    import re
    normalise=lambda text: re.sub(r"[^a-z0-9]","",text.casefold())
    query=normalise(value)
    for key,label in available_trucks().items():
        if query in {normalise(key),normalise(label)}:
            return key
    return ""


def truck_keys():
    return sorted(path.stem for path in (ART / "trucks").glob("*.webp"))


async def appearance(connection, user_id):
    # Serialise allocation so concurrent registration/profile requests never reroll.
    await connection.execute("SELECT pg_advisory_xact_lock(746520031)")
    row = await connection.fetchrow(
        "SELECT * FROM driver_card_preferences WHERE discord_user_id=$1", user_id
    )
    if row:
        return dict(row)
    keys = truck_keys()
    if not keys:
        raise RuntimeError("No tachograph truck assets installed")
    counts = {key: 0 for key in keys}
    for item in await connection.fetch(
        "SELECT truck_key, COUNT(*) AS total FROM driver_card_preferences GROUP BY truck_key"
    ):
        if item["truck_key"] in counts:
            counts[item["truck_key"]] = item["total"]
    minimum = min(counts.values())
    key = random.SystemRandom().choice([key for key in keys if counts[key] == minimum])
    return dict(await connection.fetchrow(
        """INSERT INTO driver_card_preferences(discord_user_id,truck_key)
           VALUES($1,$2) RETURNING *""", user_id, key
    ))


def threshold_key(miles):
    return "1m" if miles == 1000000 else f"{miles//1000}k" if miles else "0"


@lru_cache(maxsize=100)
def badge(miles, locked):
    path = f"unlocked/{threshold_key(miles)}.webp"
    with ZipFile(SHIELDS) as archive:
        with Image.open(BytesIO(archive.read(path))) as source:
            layer = source.convert("RGBA")
            # Remove residual black matte without changing the shield colours.
            red, green, blue, alpha = layer.split()
            brightness = ImageChops.lighter(ImageChops.lighter(red,green),blue)
            layer.putalpha(ImageChops.multiply(alpha,brightness.point(lambda value: min(255,value*12))))
            if locked:
                # Derive the locked state from the complete unlocked silhouette.
                # The separate lock overlay must never replace its shield frame.
                alpha=layer.getchannel("A")
                layer=ImageOps.grayscale(layer).point(lambda value:min(255,int(value*1.08)+8)).convert("RGBA")
                layer.putalpha(alpha)
                with Image.open(ART/"design-details/small-lock.webp") as source:
                    lock=ImageOps.contain(source.convert("RGBA"),(30,36),Image.Resampling.LANCZOS)
                layer.alpha_composite(lock,(layer.width-lock.width-2,layer.height-lock.height-2))
            return layer



def progression(value):
    """Return a track's rank from its own credited total, preserving unavailable."""
    if value is None:
        return None
    miles = Decimal(str(value))
    if not miles.is_finite() or miles < 0:
        raise ValueError("Mileage must be finite and nonnegative")
    index = max(i for i, threshold in enumerate(MILES) if miles >= threshold)
    target = MILES[index + 1] if index + 1 < len(MILES) else None
    return dict(miles=miles, current=MILES[index], name=RANK_NAMES[index],
                target=target, next_name=RANK_NAMES[index + 1] if target else None,
                remaining=max(Decimal(0), Decimal(target or miles) - miles))


@lru_cache(maxsize=44)
def race_badge(miles, locked=False):
    """Reuse the full approved shield with an amber frame and explicit Race ribbon."""
    layer = Image.new("RGBA", (180, 200))
    fitted = ImageOps.contain(badge(miles, locked), (162, 178), Image.Resampling.LANCZOS)
    layer.alpha_composite(fitted, ((180 - fitted.width) // 2, 4))
    with Image.open(ART / "design-details/race-shield-overlay.webp") as source:
        layer.alpha_composite(source.convert("RGBA"))
    return layer

@lru_cache(maxsize=12)
def club_insignia(threshold, earned=True):
    key = CLUBS[threshold][0]
    path = (ART/"design-details/mileage-club-medallion.webp"
            if threshold == 40000 else ART/f"insignias/{key}.webp")
    with Image.open(path) as source:
        medal = source.convert("RGBA")
    bounds = medal.getchannel("A").point(lambda value: 255 if value > 40 else 0).getbbox()
    if bounds:
        medal = medal.crop(bounds)
    if not earned:
        alpha = medal.getchannel("A")
        medal = ImageOps.grayscale(medal).convert("RGBA")
        medal.putalpha(alpha)
    return medal


def driver_identity(display_name):
    """Extract an explicit bracketed driver number without inventing an ID."""
    original = str(display_name).strip()
    match = re.match(r"^\[(\d{1,8})\]\s*(.+)$", original)
    if not match:
        return None, original
    number, name = match.groups()
    title, separator, remainder = name.partition("|")
    known_titles = {"ceo", "owner", "co-owner", "co owner", "co-founder",
                    "co founder", "director", "manager", "driver", "staff",
                    "events manager", "recruitment manager", "lead it server support"}
    titles = [part.strip().casefold() for part in title.split("/")]
    if separator and remainder.strip() and all(part in known_titles for part in titles):
        name = remainder.strip()
    return number, name.strip()


def render(name, profile, prefs, side="front", role="DRIVER"):
    """Compose real data over independent artwork. Returns a Discord PNG."""
    discord_display_name = name
    driver_number, name = driver_identity(name)
    if side not in {"front", "back"}:
        raise ValueError("Unknown card side")
    with Image.open(ART / f"backgrounds/{side}.webp") as source:
        canvas = source.convert("RGBA").resize((1117, 700), Image.Resampling.LANCZOS)
    if side == "back":
        # Compose every field independently; never paint over sample-driver artwork.
        with Image.open(ART / "backgrounds/front.webp") as source:
            canvas = source.convert("RGBA").resize((1117,700),Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(canvas)

    def font(size):
        return ImageFont.truetype(str(ROOT/"assets/profile/fonts/LiberationSans-Bold.ttf"), size)

    def text(x,y,value,size=18,colour=WHITE,width=None):
        value = " ".join(str(value if value is not None else "Unavailable").split())
        while width and size > 11 and draw.textlength(value,font=font(size)) > width:
            size -= 1
        if width and draw.textlength(value,font=font(size)) > width:
            while value and draw.textlength(value+"…",font=font(size)) > width:
                value = value[:-1]
            value += "…"
        draw.text((x+1,y+2),value,font=font(size),fill="#000914")
        draw.text((x,y),value,font=font(size),fill=colour)

    def metal(x,y,value,size=24,width=None):
        value = " ".join(str(value).upper().split())
        while width and size > 11 and draw.textlength(value,font=font(size)) > width:
            size -= 1
        if width and draw.textlength(value,font=font(size)) > width:
            while value and draw.textlength(value+"…",font=font(size)) > width:
                value=value[:-1]
            value+="…"
        mask=Image.new("L",canvas.size)
        md=ImageDraw.Draw(mask)
        md.text((x,y),value,font=font(size),fill=255)
        draw.text((x+1,y+2),value,font=font(size),fill="#000813",stroke_width=2,stroke_fill="#000813")
        bounds=mask.getbbox()
        if bounds:
            gradient=Image.new("RGBA",canvas.size)
            gd=ImageDraw.Draw(gradient)
            for line in range(bounds[1],bounds[3]):
                t=(line-bounds[1])/max(1,bounds[3]-bounds[1]-1)
                # Brushed silver with a narrow dark reflection and cyan rim.
                shade=int(253-65*t) if t<0.48 else int(160+85*(t-0.48)/0.52)
                gd.line((bounds[0],line,bounds[2],line),fill=(shade,min(255,shade+12),min(255,shade+22),255))
            gradient.putalpha(mask)
            canvas.alpha_composite(gradient)

    def centred_metal(x,y,value,size,width):
        value=str(value).upper()
        while size>11 and draw.textlength(value,font=font(size))>width:
            size-=1
        left=x+max(0,(width-draw.textlength(value,font=font(size)))/2)
        metal(left,y,value,size,width=width)

    def panel(box):
        overlay=Image.new("RGBA",canvas.size)
        od=ImageDraw.Draw(overlay)
        od.rounded_rectangle(box,10,fill=(3,15,29,205),outline=(59,155,204,230),width=2)
        x1,y1,x2,y2=box
        od.rounded_rectangle((x1+3,y1+3,x2-3,y2-3),8,outline=(104,195,223,65),width=1)
        canvas.alpha_composite(overlay)

    def heading(x,y,width,label):
        draw.polygon(((x,y+21),(x,y+8),(x+10,y),(x+width-10,y),(x+width,y+8),(x+width,y+21)),
                     fill="#081e33",outline="#409fc7")
        text(x+12,y+3,label,15,CYAN,width=width-24)

    def image(layer,box):
        x,y,w,h=box
        layer=ImageOps.contain(layer,(w,h),Image.Resampling.LANCZOS)
        canvas.alpha_composite(layer,(x+(w-layer.width)//2,y+(h-layer.height)//2))

    def asset(path,box):
        with Image.open(path) as source:
            image(source.convert("RGBA"),box)

    def detail(key,box):
        if key in {"uk-map-watermark", "wolf-seal"}:
            x,y,w,h=box
            with Image.open(ART/f"design-details/{key}.webp") as source:
                layer=ImageOps.contain(source.convert("RGBA"),(w,h),Image.Resampling.LANCZOS)
            x+=(w-layer.width)//2
            y+=(h-layer.height)//2
            mask=Image.new("L",layer.size)
            md=ImageDraw.Draw(mask)
            inset=max(5,min(layer.size)//12)
            md.ellipse((inset,inset,layer.width-inset,layer.height-inset),fill=155 if key=="wolf-seal" else 115)
            mask=mask.filter(ImageFilter.GaussianBlur(inset))
            mask=ImageChops.multiply(mask,layer.getchannel("A"))
            region=canvas.crop((x,y,x+layer.width,y+layer.height))
            blended=ImageChops.screen(region.convert("RGB"),layer.convert("RGB")).convert("RGBA")
            canvas.paste(Image.composite(blended,region,mask),(x,y))
            return
        asset(ART/f"design-details/{key}.webp",box)

    def track_row(box, track, *, race=False, compact=False):
        x, y, right, bottom = box
        accent = AMBER if race else CYAN
        panel(box)
        draw.rounded_rectangle(box, 10, outline=accent, width=2)
        badge_width = 82 if compact else 96
        label_x = x + badge_width + 13
        content_right = right - (85 if compact else 18)
        track_label = "RACE" if race else "REAL"
        if track is None:
            text(label_x, y + 8, track_label + " PROGRESSION", 14, accent)
            metal(label_x, y + 28, "UNAVAILABLE", 22, width=content_right-label_x)
            text(label_x, y + 55, "No credited Race total available", 12, accent,
                 width=content_right-label_x)
            return
        maker = race_badge if race else badge
        image(maker(track["current"], False), (x+7, y+5, badge_width, bottom-y-10))
        text(label_x, y+(5 if compact else 7), track_label + (" MILEAGE" if compact else " PROGRESSION"),
             10 if compact else 12, accent, width=content_right-label_x)
        metal(label_x, y+(19 if compact else 23), track["name"], 15 if compact else 20,
              width=content_right-label_x-(0 if compact else 230))
        amount = f'{int(track["miles"]):,}'
        target = f'{amount} / {track["target"]:,}' if track["target"] else amount + " MI"
        if compact:
            text(label_x, y+36, target, 13, WHITE, width=content_right-label_x)
        else:
            text(content_right-225, y+24, target, 18, WHITE, width=225)
        bar_y = y+49 if compact else y+48
        width = content_right-label_x
        draw.rounded_rectangle((label_x,bar_y,content_right,bar_y+13),5,
                               fill="#132b3d",outline=accent,width=1)
        fraction = min(track["miles"]/track["target"],Decimal(1)) if track["target"] else Decimal(1)
        fill_width = int((width-4)*fraction)
        if fill_width:
            draw.rounded_rectangle((label_x+2,bar_y+2,label_x+2+fill_width,bar_y+11),
                                   3,fill=accent)
        caption = "MAXIMUM RANK ACHIEVED"
        if track["target"]:
            remaining = int(track["remaining"].to_integral_value(rounding="ROUND_CEILING"))
            caption = f'NEXT · {track["next_name"].upper()} · {remaining:,} MI'
        text(label_x,bar_y+(16 if compact else 18),caption,9 if compact else 10,accent,width=width)
        if compact and track["target"]:
            image(maker(track["target"], True), (right-79,y+10,71,bottom-y-20))

    real = max(0,int(profile.get("real_miles") or 0))
    real_track = progression(real)
    race_track = progression(profile.get("trucky_race_miles"))
    if side == "front":
        asset(ART/"company-logo.webp",(30,20,254,110))
        metal(382 if side=="front" else 335,45,
              "DRIVER TACHOGRAPH CARD" if side=="front" else "DRIVER CAREER RECORD",
              38,width=608 if side=="front" else 650)
        if side=="front":
            text(435,94,"V I R T U A L   C O M P A N Y   I D",15,WHITE,width=540)
        detail("tachograph-symbol",(1002,35,70,45))
    if side=="front":
        detail("uk-map-watermark",(922,84,157,177))
        detail("smart-chip",(59,160,119,75))
        code=prefs.get("country_code")
        country=countries().get(code)
        if country:
            with ZipFile(FLAGS) as archive:
                with Image.open(BytesIO(archive.read(country["flag"]))) as flag:
                    image(flag.convert("RGBA"),(205,171,99,54))
        panel((37,245,321,640))
        with Image.open(ART/"trucks"/f"{prefs['truck_key']}.webp") as source:
            # The portrait masters include headroom and the complete vehicle.
            # Contain preserves every wheel and mirror across long and wide models.
            portrait = source.convert("RGBA")
            if prefs["truck_key"] in {
                "scania-s-r-next-gen", "daf-xf", "daf-xg-plus", "man-euro-6",
                "mercedes-actros-mp5", "mercedes-actros-mp6", "renault-t", "volvo-fh6",
            }:
                portrait = portrait.crop((0, round(portrait.height * .08),
                                          portrait.width, round(portrait.height * .88)))
                portrait = ImageOps.fit(portrait,(278,389),Image.Resampling.LANCZOS,
                                       centering=(.65,.5))
            image(portrait,(40,248,278,389))
        if driver_number:
            text(345,172,"DRIVER NO.",11,CYAN)
            metal(424,168,driver_number,20,width=160)
            text(345,197,"DRIVER NAME",12,CYAN)
            metal(345,213,name,30,width=239)
        else:
            text(345,183,"DRIVER NAME",15,CYAN)
            metal(345,204,name,36,width=239)
        draw.line((345,249,584,249),fill=CYAN,width=1)
        text(345,254,"ROLE",15,CYAN)
        metal(345,274,role,24,width=239)
        draw.line((345,305,574,305),fill=CYAN,width=1)
        text(345,314,"COUNTRY",15,CYAN)
        metal(345,334,country["name"] if country else "NOT SET",23,width=254)
        if profile.get("member_since"):
            text(345,366,"DISCORD JOINED · " + profile["member_since"],11,CYAN,width=254)
        panel((612,170,922,378))
        metal(629,176,"LINKED ACCOUNTS",16,width=260)
        draw.line((616,197,919,197),fill="#296181")
        accounts={item["provider"]:item for item in profile.get("linked_accounts",[])}
        accounts["Discord"]={"name":discord_display_name,"status":"Linked"}
        if profile.get("steam_id64"):
            accounts["Steam"]={"name":profile.get("steam_display_name") or "Name unavailable","status":"Verified"}
        for i,(service,icon) in enumerate((("Steam","steam"),("Discord","discord"),("TrucksBook","trucksbook"),("Trucky","trucky"),("Drivers Hub","drivers-hub"))):
            y=205+i*33
            if i:
                draw.line((619,y-5,914,y-5),fill="#24475d")
            detail(icon+"-icon",(630,y,27,27))
            item=accounts.get(service,{})
            text(670,y+3,service,14,width=100)
            text(774,y+3,item.get("name") or "Not linked",14,width=110)
            if item.get("status","").lower() in {"verified","linked"}:
                detail("verified-account-tick",(891,y+2,20,20))
        draw.line((345,385,924,385),fill="#4696b4",width=1)
        draw.line((345,449,924,449),fill="#4696b4",width=1)
        race=profile.get("trucky_race_miles")
        values=(("REAL MILES",f"{real:,}"),("TRUCKY RACE",f"{int(Decimal(str(race))):,}" if race is not None else "Unavailable"),("REAL JOBS",str(profile.get("jobs_completed",0))))
        for i,(label,value) in enumerate(values):
            x=360+i*192
            detail(("road-icon","trophy-icon","jobs-icon")[i],(x,398,39,38))
            text(x+58,395,label,13,CYAN,width=130)
            metal(x+58,414,value,25,width=126)
            draw.line((x-15,396,x-15,438),fill="#4789a7",width=1)
        panel((329,451,922,473))
        latest = profile.get("latest_award")
        text(342,455,"LATEST AWARD · " + latest if latest else "LATEST AWARD · NONE RECORDED",
             11,CYAN,width=566)
        track_row((329,480,922,560),real_track)
        track_row((329,568,1083,650),race_track,race=True)
        panel((934,169,1083,251))
        detail("verified-driver-shield",(942,184,39,48))
        text(987,188,"LINKED A&T",12,CYAN,width=87)
        metal(987,209,"DRIVER",16,width=87)
        panel((934,258,1083,560))
        centred_metal(940,266,"CLUB HONOURS",14,137)
        for is_race, y in ((False,292),(True,426)):
            accent = AMBER if is_race else CYAN
            earned = profile.get("race_permanent_achievements" if is_race else "permanent_achievements",())
            club = current_club(earned)
            threshold = club or 40000
            prefix = "RACE" if is_race else "REAL"
            caption = prefix + (" · " if club else " · NEXT ") + threshold_key(threshold).upper()
            text(946,y,caption,12,accent,width=126)
            image(club_insignia(threshold,club is not None),(976,y+20,66,67))
            if club is None:
                detail("small-lock",(1001,y+42,18,23))
            if club is not None:
                text(944,y+94,CLUBS[club][1],10,accent,width=130)
            else:
                track = race_track if is_race else real_track
                caption = "TOTAL UNAVAILABLE"
                if track is not None:
                    remaining = max(0,threshold-int(track["miles"]))
                    caption = f"{remaining:,} MI TO CLUB"
                text(944,y+94,caption,10,accent,width=130)
        draw.line((945,415,1072,415),fill="#376681")
    else:
        asset(ART/"company-logo.webp",(30,16,250,100))
        metal(330,40,"DRIVER CAREER RECORD",33,width=644)
        text(333,86,(f"DRIVER {driver_number} · " if driver_number else "")+name,21,CYAN,width=640)
        detail("tachograph-symbol",(1002,35,70,45))
        panel((37,128,507,330))
        heading(45,134,454,"CURRENT RANKS")
        text(395,137,"NEXT RANK",13,CYAN,width=100)
        track_row((44,159,499,240),real_track,compact=True)
        track_row((44,246,499,327),race_track,race=True,compact=True)
        panel((37,337,507,411))
        heading(45,341,454,"CAREER RECORD")
        for i,(key,label,icon) in enumerate((
            ("jobs_completed","REAL JOBS","jobs-icon"),
            ("average_job_distance","AVERAGE JOB","road-icon"),
            ("longest_job_distance","LONGEST JOB","career-pin"),
            ("weekly_wins","WEEKLY WINS","trophy-icon"),
        )):
            x=47+i*113
            detail(icon,(x+45,366,21,18))
            value=f"{int(profile.get(key) or 0):,}"
            if key in {"average_job_distance","longest_job_distance"}:
                value+=" MI"
            centred_metal(x,381,value,16,108)
            label_width=draw.textlength(label,font=font(9))
            text(x+(108-label_width)/2,398,label,9,CYAN,width=108)

        panel((37,418,507,480))
        heading(45,422,454,"MONTHLY REAL MILES")
        text(346,426,profile.get("monthly_label","CURRENT MONTH"),10,CYAN,width=145)
        monthly = profile.get("monthly_real_miles")
        target = int(profile.get("monthly_target") or 10000)
        if monthly is None:
            text(58,449,"MONTHLY TOTAL UNAVAILABLE",13,CYAN,width=430)
        else:
            monthly = max(0,int(monthly))
            metal(58,447,f"{monthly:,} / {target:,} MI",18,width=260)
            remaining = max(0,target-monthly)
            text(336,451,f"{remaining:,} MI TO TARGET" if remaining else "TARGET ACHIEVED",
                 11,CYAN,width=154)
            draw.rounded_rectangle((58,470,490,477),3,fill="#102c43",outline=CYAN)
            fill = int(428*min(monthly/target,1))
            if fill:
                draw.rounded_rectangle((60,472,60+fill,475),1,fill=CYAN)

        panel((37,487,507,610))
        heading(45,491,454,"PERMANENT ACHIEVEMENTS")
        for i,miles in enumerate(CLUBS):
            x=45+i*75
            earned_real = miles in profile.get("permanent_achievements",())
            earned_race = miles in profile.get("race_permanent_achievements",())
            image(club_insignia(miles,earned_real or earned_race),(x+12,517,50,46))
            if not (earned_real or earned_race):
                detail("small-lock",(x+28,533,17,22))
            centred_metal(x,565,threshold_key(miles).upper(),13,74)
            for y, label, earned, accent in (
                (582,"REAL",earned_real,CYAN),(596,"RACE",earned_race,AMBER),
            ):
                draw.rounded_rectangle((x+7,y,x+67,y+11),4,
                    fill=accent if earned else "#152c3d",outline=accent if earned else "#597080")
                draw.text((x+16,y+1),label,font=font(8),
                          fill="#061728" if earned else "#b3c3ce")
                draw.ellipse((x+56,y+4,x+60,y+8),
                             fill="#061728" if earned else "#597080")
        panel((94,611,507,649))
        detail("hall-of-fame-trophy",(104,615,36,29))
        text(150,622,"HALL OF FAME",13,width=115)
        tier=profile.get("hall_of_fame_tier")
        text(283,622,str(tier).upper() if tier else "NOT YET INDUCTED",12,CYAN,width=211)
        detail("reverse-compass-seal",(20,605,68,65))
        panel((519,128,1080,649))
        heading(527,134,545,"PATH TO IMMORTAL")
        text(880,138,"● REAL",11,CYAN,width=78)
        text(967,138,"● RACE",11,AMBER,width=85)
        for i,miles in enumerate(MILES):
            row,col=divmod(i,6)
            # All ranks share one cell size; centre the final four in the same grid.
            x=529+col*90+(90 if row==3 else 0)
            y=169+row*116
            real_current = miles == real_track["current"]
            race_current = race_track is not None and miles == race_track["current"]
            if real_current or race_current:
                draw.rounded_rectangle((x,y,x+86,y+112),5,fill="#103755",
                                       outline=CYAN if real_current else AMBER,width=2)
            earned_real = real >= miles
            earned_race = race_track is not None and race_track["miles"] >= miles
            if earned_race and (race_current or not earned_real):
                image(race_badge(miles),(x+3,y+3,80,87))
            else:
                image(badge(miles,not earned_real),(x+3,y+3,80,87))
            centred_metal(x+1,y+91,threshold_key(miles).upper(),15,84)
            if real_current:
                draw.rounded_rectangle((x+9,y+108,x+38,y+111),1,fill=CYAN)
            if race_current:
                draw.rounded_rectangle((x+48,y+108,x+77,y+111),1,fill=AMBER)
        detail("footer-back",(273,660,566,30))
    if side == "front":
        detail("footer-front",(273,660,566,30))
    if profile.get("card_updated_at"):
        text(858,678,"UPDATED " + profile["card_updated_at"],9,CYAN,width=224)
    output=BytesIO()
    canvas.convert("RGB").save(output,"PNG",optimize=True)
    output.seek(0)
    return output


def resolve_country(value):
    query = " ".join(value.strip().casefold().split())
    aliases = {"uk":"GB", "united states":"US", "usa":"US", "holland":"NL", "eire":"IE", "éire":"IE"}
    if query == "clear":
        return "CLEAR"
    if query in aliases:
        return aliases[query]
    for key, entry in countries().items():
        if query in {key.casefold(), entry["name"].casefold()}:
            return key
    return ""


def company_role(member, guild_id=None):
    """Select an actual company job title instead of a decorative divider."""
    import re
    import unicodedata
    if getattr(getattr(member,"guild",None),"owner_id",None) == member.id:
        return "OWNER / DRIVER"
    priorities = (
        (("co owner", "chief operator", "chief operating"), "CO-OWNER / DRIVER"),
        (("owner", "chief executive", "founder"), "OWNER / DRIVER"),
        (("lead it server support",), "LEAD IT SERVER SUPPORT / DRIVER"),
        (("lead it support", "it support lead", "head of it"), "LEAD IT SUPPORT / DRIVER"),
        (("it support",), "IT SUPPORT / DRIVER"),
        (("recruitment manager", "recruitment management"), "RECRUITMENT MANAGER / DRIVER"),
        (("event manager", "events manager"), "EVENTS MANAGER / DRIVER"),
        (("server manager",), "SERVER MANAGER / DRIVER"),
        (("night shift operations manager", "night shift manager"), "NIGHT SHIFT MANAGER / DRIVER"),
        (("operations manager",), "OPERATIONS MANAGER / DRIVER"),
        (("management", "manager"), "MANAGEMENT / DRIVER"),
        (("dispatcher",), "DISPATCHER / DRIVER"),
        (("moderator", "admin"), "STAFF / DRIVER"),
    )
    names=[]
    for role in member.roles:
        name=unicodedata.normalize("NFKC",role.name).casefold()
        # Discord role names often use small-cap letters that NFKC does not fold.
        name=name.translate(str.maketrans("ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘǫʀꜱᴛᴜᴠᴡхʏᴢ", "abcdefghijklmnopqrstuvwxyz"))
        name=" ".join(re.sub(r"[^a-z0-9]+"," ",name).split())
        if any(word in name for word in ("leadership staff","ranks badges","partners community","interests access","bots integrations")):
            continue
        names.append(" "+name+" ")
    for aliases,label in priorities:
        if any(" "+alias+" " in name for alias in aliases for name in names):
            return label
    return "DRIVER"
