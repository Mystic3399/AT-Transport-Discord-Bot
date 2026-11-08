"""A&T tachograph artwork renderer and persistent appearance settings."""
import json
import random
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


def render(name, profile, prefs, side="front", role="DRIVER"):
    """Compose real data over independent artwork. Returns a Discord PNG."""
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

    def plate(x,y,width,label):
        draw.polygon(((x+6,y),(x+width-6,y),(x+width,y+6),(x+width-4,y+22),(x+4,y+22),(x,y+6)),
                     fill="#07182b",outline="#339ac2")
        metal(x+width//2-18,y+1,label,18,width=width-12)

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

    def shield(miles,box):
        layer=badge(miles,real < miles)
        if miles == current:
            x,y,w,h=box
            fitted=ImageOps.contain(layer,(w,h),Image.Resampling.LANCZOS)
            glow=Image.new("RGBA",canvas.size)
            mask=Image.new("L",canvas.size)
            mask.paste(fitted.getchannel("A"),(x+(w-fitted.width)//2,y+(h-fitted.height)//2))
            mask=mask.filter(ImageFilter.MaxFilter(9)).filter(ImageFilter.GaussianBlur(5))
            glow.paste((36,205,255,255),(0,0,canvas.width,canvas.height))
            glow.putalpha(mask.point(lambda value: int(value*0.8)))
            canvas.alpha_composite(glow)
        image(layer,box)

    def progress(x,y,w,compact=False):
        label = "MAXIMUM RANK ACHIEVED" if next_rank is None else f"{remaining:,} MILES TO NEXT RANK"
        if compact:
            metal(x,y,label,14,width=w)
            draw.polygon(((x+4,y+24),(x+w-4,y+24),(x+w,y+32),(x+w-4,y+40),(x+4,y+40),(x,y+32)),
                         fill="#102c43",outline="#dfc99b")
            fraction=1 if next_rank is None else min(real/next_rank,1)
            draw.rectangle((x+4,y+27,x+4+int((w-8)*fraction),y+36),fill="#48d0ff")
            target=f"{real:,} / {next_rank:,}" if next_rank is not None else f"{real:,} REAL MILES"
            metal(x+10,y+44,target,16,width=w-10)
            return
        panel((x-10,y-9,x+w+10,y+91))
        target=f"{real:,} / {next_rank:,}" if next_rank is not None else f"{real:,} REAL MILES"
        metal(x,y,target,23,width=w)
        shape=((x+5,y+31),(x+w-5,y+31),(x+w,y+41),(x+w-5,y+52),(x+5,y+52),(x,y+41))
        draw.polygon(shape,fill="#152c3d",outline="#e2c891")
        # Match the approved absolute progress display.
        fraction=1 if next_rank is None else min(real/next_rank,1)
        end=x+5+int((w-10)*fraction)
        if end>x+5:
            for line in range(34,50):
                fade=(line-34)/15
                draw.line((x+5,y+line,end,y+line),fill=(int(43+74*(1-fade)),int(147+91*(1-fade)),248))
        metal(x,y+62,label,16,width=w)

    real = max(0,int(profile.get("real_miles") or 0))
    idx = max(i for i,m in enumerate(MILES) if real >= m)
    current = MILES[idx]
    next_rank = MILES[idx+1] if idx+1<len(MILES) else None
    remaining = max(0,(next_rank or real)-real)
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
        # Balance the portrait between the chip and the lower content edge.
        panel((37,268,321,640))
        truck = ART/"trucks"/f"{prefs['truck_key']}.webp"
        with Image.open(truck) as source:
            original=source.convert("RGBA")
            portrait=ImageOps.fit(original,(278,364),Image.Resampling.LANCZOS).filter(ImageFilter.GaussianBlur(16))
            portrait=ImageEnhance.Brightness(portrait).enhance(0.45)
            fitted=ImageOps.contain(original,(278,364),Image.Resampling.LANCZOS)
            portrait.alpha_composite(fitted,((278-fitted.width)//2,(364-fitted.height)//2))
        canvas.alpha_composite(portrait,(40,272))
        text(345,183,"DRIVER NAME",15,CYAN)
        metal(345,204,name,36,width=239)
        draw.line((345,249,584,249),fill=CYAN,width=1)
        text(345,267,"ROLE",15,CYAN)
        metal(345,287,role,24,width=239)
        draw.line((345,317,574,317),fill=CYAN,width=1)
        text(345,329,"COUNTRY",15,CYAN)
        metal(345,347,country["name"] if country else "NOT SET",23,width=254)
        panel((612,170,922,378))
        metal(629,176,"LINKED ACCOUNTS",16,width=260)
        draw.line((616,197,919,197),fill="#296181")
        accounts={item["provider"]:item for item in profile.get("linked_accounts",[])}
        accounts["Discord"]={"name":name,"status":"Linked"}
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
        detail("wolf-seal",(935,275,150,219))
        draw.line((345,385,924,385),fill="#4696b4",width=1)
        draw.line((345,449,924,449),fill="#4696b4",width=1)
        race=profile.get("trucky_race_miles")
        values=(("REAL MILES",f"{real:,}"),("TRUCKY RACE",f"{Decimal(str(race)):,.0f}" if race is not None else "Unavailable"),("REAL JOBS",str(profile.get("jobs_completed",0))))
        for i,(label,value) in enumerate(values):
            x=360+i*192
            detail(("road-icon","trophy-icon","jobs-icon")[i],(x,398,39,38))
            text(x+58,395,label,13,CYAN,width=130)
            metal(x+58,414,value,25,width=126)
            draw.line((x-15,396,x-15,438),fill="#4789a7",width=1)
        text(461,456,"CURRENT RANK",11,CYAN,width=110)
        featured=[MILES[idx-1] if idx > 0 else None,current,next_rank]
        for i,miles in enumerate(featured):
            x=335+i*120
            if miles is not None:
                shield(miles,(x,471,111,147))
                plate(x+4,624,104,threshold_key(miles).upper())
        panel((708,463,1083,654))
        detail("verified-driver-shield",(724,473,53,53))
        text(790,480,"LINKED A&T DRIVER",17,width=273)
        draw.line((721,535,1069,535),fill="#286582",width=1)
        club = current_club(profile.get("permanent_achievements",()))
        if club is not None:
            insignia,label = CLUBS[club]
            path = ART/"design-details/mileage-club-medallion.webp" if club==40000 else ART/f"insignias/{insignia}.webp"
            glow=Image.new("RGBA",canvas.size)
            ImageDraw.Draw(glow).ellipse((723,545,813,622),fill=(24,194,255,95))
            canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(6)))
            with Image.open(path) as source:
                medal=source.convert("RGBA")
                bounds=medal.getchannel("A").point(lambda value:255 if value>55 else 0).getbbox()
                if bounds:
                    medal=medal.crop(bounds)
            image(ImageEnhance.Brightness(medal).enhance(1.2),(720,541,99,79))
            centred_metal(716,619,threshold_key(club).upper(),17,108)
            label_size=10
            while label_size>8 and draw.textlength(label,font=font(label_size))>108:
                label_size-=1
            label_width=draw.textlength(label,font=font(label_size))
            text(770-label_width/2,639,label,label_size,width=108)
        progress(841,557,232)
        # Compact numeric progress fills the remaining lower-right safe area.
    else:
        asset(ART/"company-logo.webp",(30,16,250,100))
        metal(330,40,"DRIVER CAREER RECORD",33,width=644)
        text(333,86,name,21,CYAN,width=640)
        detail("tachograph-symbol",(1002,35,70,45))
        panel((37,128,507,399))
        heading(45,134,454,"CURRENT RANK")
        text(335,137,"NEXT RANK",15,CYAN,width=150)
        shield(current,(65,165,195,213))
        if next_rank is not None:
            shield(next_rank,(319,170,143,169))
        else:
            centred_metal(307,237,"MAXIMUM RANK",16,184)
        progress(299,333,186,compact=True)
        panel((37,405,507,493))
        heading(45,409,454,"CAREER RECORD")
        for i,(key,label,icon) in enumerate((
            ("jobs_completed","REAL JOBS","jobs-icon"),
            ("average_job_distance","AVERAGE JOB","road-icon"),
            ("longest_job_distance","LONGEST JOB","career-pin"),
            ("weekly_wins","WEEKLY WINS","trophy-icon")
        )):
            x=47+i*113
            detail(icon,(x+43,437,26,24))
            value=f"{int(profile.get(key) or 0):,}"
            if key in {"average_job_distance","longest_job_distance"}:
                value+=" MI"
            centred_metal(x,457,value,19,108)
            label_width=draw.textlength(label,font=font(10))
            text(x+(108-label_width)/2,479,label,10,CYAN,width=108)
        panel((37,500,507,604))
        heading(45,504,454,"PERMANENT ACHIEVEMENTS")
        for i,miles in enumerate((40000,100000,250000,500000,750000,1000000)):
            x=45+i*75
            earned=miles in profile.get("permanent_achievements",())
            insignia=CLUBS[miles][0]
            path=ART/"design-details/mileage-club-medallion.webp" if miles==40000 else ART/f"insignias/{insignia}.webp"
            with Image.open(path) as source:
                medal=source.convert("RGBA")
                bounds=medal.getchannel("A").point(lambda value:255 if value>40 else 0).getbbox()
                if bounds:
                    medal=medal.crop(bounds)
            if not earned:
                alpha=medal.getchannel("A")
                medal=ImageOps.grayscale(medal).convert("RGBA")
                medal.putalpha(alpha)
            else:
                glow=Image.new("RGBA",(74,76))
                ImageDraw.Draw(glow).ellipse((7,0,67,59),fill=(29,199,255,105))
                canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(4)),(x,527))
            image(medal,(x+5,527,64,60))
            if not earned:
                detail("small-lock",(x+28,547,18,22))
            centred_metal(x,587,threshold_key(miles).upper(),15,74)
        panel((94,611,507,649))
        detail("hall-of-fame-trophy",(104,615,36,29))
        text(150,622,"HALL OF FAME",13,width=115)
        tier=profile.get("hall_of_fame_tier")
        text(283,622,str(tier).upper() if tier else "NOT YET INDUCTED",12,CYAN,width=211)
        detail("reverse-compass-seal",(20,605,68,65))
        panel((519,128,1080,649))
        heading(527,134,545,"PATH TO IMMORTAL")
        for i,miles in enumerate(MILES):
            row,col=divmod(i,6)
            # All ranks share one cell size; centre the final four in the same grid.
            x=529+col*90+(90 if row==3 else 0)
            y=169+row*116
            if miles==current:
                draw.rounded_rectangle((x,y,x+86,y+112),5,fill="#103755",outline="#49beff",width=2)
            shield(miles,(x+3,y+3,80,87))
            centred_metal(x+1,y+93,threshold_key(miles).upper(),17,84)
        detail("footer-back",(273,660,566,30))
    if side == "front":
        detail("footer-front",(273,660,566,30))
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
        (("lead it support", "it support"), "IT SUPPORT / DRIVER"),
        (("recruitment manager",), "RECRUITMENT MANAGER / DRIVER"),
        (("event manager",), "EVENT MANAGER / DRIVER"),
        (("server manager",), "SERVER MANAGER / DRIVER"),
        (("management", "manager"), "MANAGEMENT / DRIVER"),
        (("dispatcher",), "DISPATCHER / DRIVER"),
        (("moderator", "admin"), "STAFF / DRIVER"),
    )
    names=[]
    for role in member.roles:
        name=unicodedata.normalize("NFKC",role.name).casefold()
        name=" ".join(re.sub(r"[^a-z0-9]+"," ",name).split())
        if any(word in name for word in ("leadership staff","ranks badges","partners community","interests access","bots integrations")):
            continue
        names.append(" "+name+" ")
    for aliases,label in priorities:
        if any(" "+alias+" " in name for alias in aliases for name in names):
            return label
    return "DRIVER"
