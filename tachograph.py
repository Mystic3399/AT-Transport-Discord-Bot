"""A&T tachograph artwork renderer and persistent appearance settings."""
import json
import random
from decimal import Decimal
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter, ImageChops

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
            layer = source.convert("RGBA")
            # Remove residual black matte without changing the shield colours.
            red, green, blue, alpha = layer.split()
            brightness = ImageChops.lighter(ImageChops.lighter(red,green),blue)
            layer.putalpha(ImageChops.multiply(alpha,brightness.point(lambda value: min(255,value*12))))
            return layer


def render(name, profile, prefs, side="front", role="DRIVER"):
    """Compose real data over independent artwork. Returns a Discord PNG."""
    if side not in {"front", "back"}:
        raise ValueError("Unknown card side")
    with Image.open(ART / f"backgrounds/{side}.webp") as source:
        canvas = source.convert("RGBA").resize((1117, 700), Image.Resampling.LANCZOS)
    if side == "back":
        # The approved artwork supplies the fixed header, frame and section labels.
        # Every sample driver field and badge state is covered below before output.
        with Image.open(ART / "design-reference.webp") as reference:
            canvas = reference.convert("RGBA").crop((0,706,1117,1406))
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
            md.ellipse((inset,inset,layer.width-inset,layer.height-inset),fill=210 if key=="wolf-seal" else 155)
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
                    image(flag.convert("RGBA"),(192,169,99,54))
        panel((37,235,321,553))
        truck = ART/"trucks"/f"{prefs['truck_key']}.webp"
        with Image.open(truck) as source:
            portrait=ImageOps.fit(source.convert("RGBA"),(278,310),Image.Resampling.LANCZOS)
        canvas.alpha_composite(portrait,(40,239))
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
            accounts["Steam"]={"name":profile["steam_id64"],"status":"Verified"}
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
        featured=[MILES[idx-1] if idx > 0 else None,current,next_rank]
        for i,miles in enumerate(featured):
            x=335+i*120
            if miles is not None:
                shield(miles,(x,455,111,166))
                plate(x+4,624,104,threshold_key(miles).upper())
        panel((714,463,907,539))
        detail("verified-driver-shield",(710,463,78,83))
        text(795,483,"LINKED",16,width=103)
        text(795,504,"A&T DRIVER",16,width=103)
        if 40000 in profile.get("permanent_achievements",()):
            detail("mileage-club-medallion",(705,547,94,76))
            metal(735,623,"40K",17,width=60)
            text(714,641,"MILEAGE CLUB",10,width=96)
        progress(841,557,232)
        # Compact numeric progress fills the remaining lower-right safe area.
    else:
        def clear(box):
            draw.rectangle(box,fill="#041c2c")

        # Replace all baked example values, including title, badge cells and totals.
        clear((798,28,986,69))
        metal(800,31,name,31,width=184)
        clear((47,141,275,364))
        shield(current,(68,144,176,218))
        clear((310,155,455,338))
        if next_rank is not None:
            shield(next_rank,(316,156,127,184))
        clear((285,340,494,362))
        metal(291,343,"MAXIMUM RANK" if next_rank is None else f"{remaining:,} MILES REMAINING",15,width=199)
        # Keep the original gold progress frame and populate its inset.
        draw.rectangle((293,365,483,375),fill="#163746")
        fraction=1 if next_rank is None else min(real/next_rank,1)
        for y in range(366,375):
            draw.line((294,y,294+int(188*fraction),y),fill=(54,204-(y-366)*7,251))
        clear((318,379,482,401))
        metal(321,381,f"{real:,} / {next_rank:,}" if next_rank is not None else f"{real:,} MI",15,width=171)
        for x,width,key in (
            (49,83,"jobs_completed"),(153,100,"average_job_distance"),
            (274,112,"longest_job_distance"),(403,81,"weekly_wins")
        ):
            clear((x,443,x+width,468))
            value=f"{int(profile.get(key) or 0):,}"
            if key in {"average_job_distance","longest_job_distance"}:
                value+=" MI"
            metal(x+7,445,value,20,width=width-9)
        ranks = (
            "NEW DRIVER","ROAD RUNNER","DISTANCE DRIVER","AURORA DRIVER",
            "WOLF PACK DRIVER","VIKING HAULER","ELITE HAULER","A&T VETERAN",
            "A&T ROAD LEGEND","BEYOND HORIZONS","HORIZON WOLF","A&T CENTURION",
            "VIKING PATHFINDER","ROAD GUARDIAN","AURORA PATHFINDER",
            "VIKING ROADMASTER","HORIZON LEGEND","ALPHA ROADMASTER",
            "AURORA COMMANDER","GUARDIAN OF THE HORIZON","BEYOND THE HORIZON",
            "A&T IMMORTAL"
        )
        for i,miles in enumerate(MILES):
            row,col=divmod(i,6)
            x=520+col*91 if row<3 else 544+col*113
            y=137+row*114
            width=91 if row<3 else 113
            clear((x,y,x+width-1,y+112))
            if miles==current:
                draw.rounded_rectangle((x,y,x+width-2,y+112),4,fill="#103755",outline="#49beff",width=2)
            if row<3:
                draw.line((x+width-1,y,x+width-1,y+112),fill="#124867")
                draw.line((x,y+112,x+width-1,y+112),fill="#124867")
            shield(miles,(x+4,y+1,width-8,79))
            words=ranks[i].split()
            midpoint=max(1,len(words)//2)
            lines=[" ".join(words[:midpoint])," ".join(words[midpoint:])]
            label_size=8
            for line_index,label in enumerate(lines):
                label_width=draw.textlength(label,font=font(label_size))
                text(x+max(1,(width-label_width)//2),y+80+line_index*9,label,label_size,width=width-4)
            value=threshold_key(miles).upper()
            value_width=draw.textlength(value,font=font(14))
            metal(x+(width-value_width)//2,y+98,value,14,width=width-8)
        for i,miles in enumerate((40000,100000,250000,500000,750000,1000000)):
            x=43+i*74
            clear((x,514,x+71,578))
            earned=miles in profile.get("permanent_achievements",())
            insignia={40000:"mileage-club-40k",100000:"centurion",250000:"veteran",
                      500000:"elite",750000:"horizon-elite",1000000:"immortal"}[miles]
            insignia_path = ART/"design-details/mileage-club-medallion.webp" if miles==40000 else ART/f"insignias/{insignia}.webp"
            with Image.open(insignia_path) as source:
                medal=source.convert("RGBA")
                if medal.getbbox():
                    medal=medal.crop(medal.getbbox())
            if not earned:
                alpha=medal.getchannel("A")
                medal=ImageOps.grayscale(medal).convert("RGBA")
                medal.putalpha(alpha)
            else:
                glow=Image.new("RGBA",canvas.size)
                ImageDraw.Draw(glow).ellipse((x+6,521,x+65,574),fill=(29,199,255,110))
                canvas.alpha_composite(glow.filter(ImageFilter.GaussianBlur(5)))
            image(medal,(x+2,515,67,63))
            if not earned:
                detail("small-lock",(x+26,537,18,22))
        tier=profile.get("hall_of_fame_tier")
        clear((161,606,451,634))
        text(164,613,"HALL OF FAME",13,width=116)
        text(301,613,str(tier).upper() if tier else "NOT YET INDUCTED",12,CYAN,width=147)
        clear((895,659,1084,680))
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
