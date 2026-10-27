"""Read-only V6 tachograph identity card renderer.

No database reads, account verification, mileage reconciliation or Discord command
side effects happen here. The caller must provide authoritative, already reconciled
values and independently verified account statuses.
"""
from dataclasses import dataclass, field
from io import BytesIO
from typing import Mapping
from PIL import Image, ImageDraw, ImageFont

SERVICES = ("Discord", "Steam", "TrucksBook", "Trucky", "Drivers Hub")
STATUSES = {"verified", "linked", "pending", "unlinked", "unconfirmed"}
INK = (235, 248, 255)
MUTED = (158, 191, 209)
CYAN = (87, 219, 239)
GOLD = (243, 196, 105)

@dataclass(frozen=True)
class Account:
    username: str = ""
    status: str = "unconfirmed"

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError("Unknown verification status")

@dataclass(frozen=True)
class DriverCard:
    display_name: str
    accounts: Mapping[str, Account] = field(default_factory=dict)
    real_miles: int | None = None
    race_miles: int | None = None
    rank: str = "Rank unavailable"

    def __post_init__(self):
        for number in (self.real_miles, self.race_miles):
            if number is not None and (not isinstance(number, int) or number < 0):
                raise ValueError("Mileage must be a nonnegative integer or None")

def _font(size, bold=False):
    paths = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    try:
        return ImageFont.truetype(paths[0 if bold else 1], size)
    except OSError:
        return ImageFont.load_default()

def _fitted(draw, value, max_width, size, bold=False):
    value = str(value or "—").replace("\n", " ").replace("\r", " ")
    while size > 13 and draw.textbbox((0, 0), value, font=_font(size, bold))[2] > max_width:
        size -= 1
    if draw.textbbox((0, 0), value, font=_font(size, bold))[2] > max_width:
        while value and draw.textbbox((0, 0), value + "…", font=_font(size, bold))[2] > max_width:
            value = value[:-1]
        value += "…"
    return value, _font(size, bold)

def render_tacho_card(data: DriverCard) -> BytesIO:
    """Render a 1280x800 PNG. No account is marked verified by inference."""
    im = Image.new("RGB", (1280, 800), (5, 18, 32))
    d = ImageDraw.Draw(im)
    for y in range(800):
        d.line((0, y, 1280, y), fill=(5, 18 + y // 125, 32 + y // 90))
    d.rounded_rectangle((20, 20, 1260, 780), radius=28, outline=(42, 143, 175), width=3)
    d.rounded_rectangle((42, 42, 1238, 156), radius=18, fill=(10, 43, 64))
    d.text((70, 59), "A&T TRANSPORT LTD", font=_font(38, True), fill=INK)
    d.text((72, 112), "DRIVEN BEYOND HORIZONS  /  DRIVER IDENTITY PASSPORT", font=_font(17, True), fill=CYAN)
    d.text((72, 191), "REGISTERED DRIVER", font=_font(18, True), fill=CYAN)
    name, font = _fitted(d, data.display_name, 510, 48, True)
    d.text((72, 224), name, font=font, fill=INK)
    d.text((72, 309), "UNIFIED VERIFIED REAL MILES", font=_font(17, True), fill=MUTED)
    d.text((72, 344), f"{data.real_miles:,} mi" if data.real_miles is not None else "Awaiting verified data", font=_font(32, True), fill=INK)
    d.text((72, 413), "TRUCKY RACE MILES", font=_font(17, True), fill=MUTED)
    d.text((72, 448), f"{data.race_miles:,} mi" if data.race_miles is not None else "Awaiting verified data", font=_font(28, True), fill=GOLD)
    d.text((72, 538), "CAREER RANK", font=_font(17, True), fill=CYAN)
    rank, rank_font = _fitted(d, data.rank, 485, 28, True)
    d.text((72, 573), rank, font=rank_font, fill=INK)
    d.rounded_rectangle((620, 179, 1213, 680), radius=20, fill=(9, 37, 55), outline=(42, 112, 143), width=2)
    d.text((651, 201), "LINKED ACCOUNTS", font=_font(25, True), fill=CYAN)
    colors = {"verified": (94, 220, 155), "linked": (95, 183, 235), "pending": GOLD, "unlinked": MUTED, "unconfirmed": MUTED}
    for i, service in enumerate(SERVICES):
        y = 261 + i * 78
        account = data.accounts.get(service, Account())
        d.text((651, y), service.upper(), font=_font(15, True), fill=MUTED)
        label, f = _fitted(d, account.username or "Not provided", 330, 23, True)
        d.text((651, y + 22), label, font=f, fill=INK)
        status = account.status.upper()
        d.text((1060, y + 23), status, font=_font(13, True), fill=colors[account.status])
    d.line((70, 708, 1209, 708), fill=(42, 143, 175), width=2)
    d.text((72, 727), "ACCOUNT STATUS IS SOURCE-VERIFIED, NOT INFERRED FROM USERNAME MATCHES", font=_font(14, True), fill=MUTED)
    out = BytesIO()
    im.save(out, "PNG", optimize=True)
    out.seek(0)
    return out
