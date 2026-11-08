"""A&T tachograph artwork renderer and persistent appearance settings."""
import json
import random
from decimal import Decimal
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent
ART = ROOT / "assets/tachograph"
SHIELDS = ART / "rank-badges/shield-edition/AT-Tachograph-Shield-Badges.zip"
FLAGS = ART / "country-flags/AT-Tachograph-World-Flags.zip"
MILES = (0,5000,10000,15000,20000,25000,30000,35000,40000,50000,75000,
         100000,150000,200000,300000,400000,500000,600000,700000,800000,900000,1000000)
WHITE, CYAN, GOLD = "#eef7ff", "#7cdcff", "#f1ce79"
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
    path = f"{'locked' if locked else 'unlocked'}/{threshold_key(miles)}.webp"
    with ZipFile(SHIELDS) as archive:
        with Image.open(BytesIO(archive.read(path))) as source:
            return source.convert("RGBA")


def render(name, profile, prefs, side="front", role="DRIVER"):
    """Compose real data over independent artwork. Returns a Discord PNG."""
    if side not in {"front", "back"}:
        raise ValueError("Unknown card side")
    with Image.open(ART / f"backgrounds/{side}.webp") as source:
        canvas = source.convert("RGBA").resize((1117, 700), Image.Resampling.LANCZOS)
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
        draw.text((x,y),value,font=font(size),fill=colour)

    def panel(box):
        draw.rounded_rectangle(box,10,fill="#061b2e",outline="#3483aa",width=2)

    def image(layer,box):
        x,y,w,h=box
        layer=ImageOps.contain(layer,(w,h),Image.Resampling.LANCZOS)
        canvas.alpha_composite(layer,(x+(w-layer.width)//2,y+(h-layer.height)//2))

    def asset(path,box):
        with Image.open(path) as source:
            image(source.convert("RGBA"),box)

    def detail(key,box):
        asset(ART/f"design-details/{key}.webp",box)

    def shield(miles,box):
        image(badge(miles,real < miles),box)

    def progress(x,y,w):
        label = "MAXIMUM RANK ACHIEVED" if next_rank is None else f"{remaining:,} MILES TO NEXT RANK"
        text(x,y,f"{real:,} REAL MILES",20,width=w)
        draw.rounded_rectangle((x,y+30,x+w,y+44),6,fill="#21394a",outline="#91c9de")
        fraction = 1 if next_rank is None else (real-current)/(next_rank-current)
        if fraction > 0:
            draw.rounded_rectangle((x+2,y+32,x+2+int((w-4)*min(fraction,1)),y+42),4,fill="#34bdf3")
        text(x,y+52,label,15,GOLD,width=w)

    real = max(0,int(profile.get("real_miles") or 0))
    idx = max(i for i,m in enumerate(MILES) if real >= m)
    current = MILES[idx]
    next_rank = MILES[idx+1] if idx+1<len(MILES) else None
    remaining = max(0,(next_rank or real)-real)
    asset(ART/"company-logo.webp",(30,20,254,110))
    text(319,45,"DRIVER TACHOGRAPH CARD" if side=="front" else "DRIVER CAREER RECORD",34,width=730)
    text(321,94,"VIRTUAL COMPANY ID  •  A&T TRANSPORT LTD",15,CYAN,width=700)
    detail("tachograph-symbol",(1002,35,70,45))
    if side=="front":
        detail("smart-chip",(59,160,119,75))
        code=prefs.get("country_code")
        country=countries().get(code)
        if country:
            with ZipFile(FLAGS) as archive:
                with Image.open(BytesIO(archive.read(country["flag"]))) as flag:
                    image(flag.convert("RGBA"),(192,169,99,54))
        panel((37,235,321,553))
        truck = ART/"trucks"/f"{prefs['truck_key']}.webp"
        with Image.open(truck) as source:
            portrait=ImageOps.fit(source.convert("RGBA"),(278,310),Image.Resampling.LANCZOS)
        canvas.alpha_composite(portrait,(40,239))
        text(345,183,"DRIVER NAME",15,CYAN)
        text(345,209,name,36,width=260)
        text(345,267,"ROLE",15,CYAN)
        text(345,290,role,21,width=260)
        text(345,329,"COUNTRY",15,CYAN)
        text(345,351,country["name"].upper() if country else "NOT SET",20,width=260)
        panel((612,170,922,378))
        text(629,179,"LINKED ACCOUNTS",16,CYAN)
        accounts={item["provider"]:item for item in profile.get("linked_accounts",[])}
        accounts["Discord"]={"name":name,"status":"Linked"}
        if profile.get("steam_id64"):
            accounts["Steam"]={"name":profile["steam_id64"],"status":"Verified"}
        for i,(service,icon) in enumerate((("Steam","steam"),("Discord","discord"),("TrucksBook","trucksbook"),("Trucky","trucky"),("Drivers Hub","drivers-hub"))):
            y=205+i*33
            detail(icon+"-icon",(630,y,27,27))
            item=accounts.get(service,{})
            text(670,y+3,service,14,width=100)
            text(774,y+3,item.get("name") or "Not linked",14,width=110)
            if item.get("status","").lower() in {"verified","linked"}:
                detail("verified-account-tick",(891,y+2,20,20))
        detail("wolf-seal",(935,275,150,219))
        panel((347,386,925,449))
        race=profile.get("trucky_race_miles")
        values=(("REAL MILES",f"{real:,}"),("TRUCKY RACE",f"{Decimal(str(race)):,.0f}" if race is not None else "Unavailable"),("REAL JOBS",str(profile.get("jobs_completed",0))))
        for i,(label,value) in enumerate(values):
            x=360+i*187
            text(x,394,label,13,CYAN)
            text(x,414,value,24,width=170)
        featured=[MILES[idx-1] if idx > 0 else None,current,next_rank]
        for i,miles in enumerate(featured):
            x=335+i*120
            if miles is not None:
                shield(miles,(x,455,111,166))
                text(x+32,625,threshold_key(miles).upper(),16)
        detail("verified-driver-shield",(710,463,68,80))
        text(790,481,"LINKED A&T DRIVER",15,width=185)
        if 40000 in profile.get("permanent_achievements",()):
            detail("mileage-club-medallion",(711,547,80,76))
            text(795,551,"40K MILEAGE CLUB",13,width=254)
        progress(836,576,250)
        # Compact numeric progress fills the remaining lower-right safe area.
    else:
        text(330,116,name,19,CYAN,width=720)
        panel((36,145,506,637))
        panel((517,145,1080,637))
        text(50,155,"CURRENT RANK",17,CYAN)
        text(310,155,"NEXT RANK",17,CYAN)
        shield(current,(67,185,176,218))
        if next_rank is not None:
            shield(next_rank,(316,196,127,184))
        progress(70,410,395)
        for i,(label,key) in enumerate((("REAL JOBS","jobs_completed"),("AVERAGE MI","average_job_distance"),("LONGEST MI","longest_job_distance"),("WEEKLY WINS","weekly_wins"))):
            x=51+i*110
            text(x,500,f"{int(profile.get(key) or 0):,}",20,width=100)
            text(x,528,label,12,CYAN)
        text(50,552,"PERMANENT ACHIEVEMENTS",15,CYAN)
        for i,miles in enumerate((40000,100000,250000,500000,750000,1000000)):
            x=50+i*70
            with Image.open(ART/"insignias/achievement.webp") as source:
                medal=source.convert("RGBA")
            if miles not in profile.get("permanent_achievements",()):
                alpha=medal.getchannel("A")
                medal=ImageOps.grayscale(medal).convert("RGBA")
                medal.putalpha(alpha)
            image(medal,(x,575,37,37))
            text(x,615,threshold_key(miles).upper(),12,width=60)
        text(538,154,"PATH TO IMMORTAL",22,CYAN)
        for i,miles in enumerate(MILES):
            row,col=divmod(i,6)
            x=528+col*90
            y=194+row*104
            if miles==current:
                draw.rounded_rectangle((x,y,x+84,y+99),7,outline=CYAN,width=2)
            shield(miles,(x+5,y+3,74,77))
            text(x+23,y+80,threshold_key(miles).upper(),13,width=63)
        tier=profile.get("hall_of_fame_tier")
        detail("hall-of-fame-trophy",(535,613,24,24))
        text(566,616,"HALL OF FAME • "+(str(tier).upper() if tier else "NOT YET INDUCTED"),13,width=490)
    detail("footer-front" if side=="front" else "footer-back",(273,650,566,37))
    output=BytesIO()
    canvas.convert("RGB").save(output,"PNG",optimize=True)
    output.seek(0)
    return output
