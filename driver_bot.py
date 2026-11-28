"""
Driver Number Bot
-----------------
Members claim a driver number (001-999) themselves with $getnumber. Numbers
are given out in the order people ask for them (lowest free number first),
and the number is put in their nickname, e.g. "[007] Alex".
Nothing is assigned automatically. All bot replies are embedded messages
with a banner image and an animated GIF.

Setup:
  1. pip install -U discord.py python-dotenv
  2. Create a .env file next to this script:  DISCORD_TOKEN=your_bot_token
  3. In the Discord Developer Portal -> Bot, enable BOTH
     "Server Members Intent" and "Message Content Intent".
  4. Invite the bot with the "Manage Nicknames", "View Channels",
     "Send Messages", "Embed Links", "Attach Files", "Read Message History"
     and "Manage Roles" permissions and the "bot" + "applications.commands"
     scopes.
  5. Put the bot's role ABOVE the roles of members it should rename
     (Discord never lets bots rename the server owner). It must also be above
     the three roles the welcome buttons hand out.
  6. python driver_bot.py

Welcome message:
  When someone joins, the bot posts a welcome + role-picker message in the
  WELCOME_CHANNEL_ID channel, with three buttons that give out roles, and
  also sends the welcome text to the new member's DMs.
  /testwelcome posts a test copy (admin roles only).

Images: the banner and GIF links below are Discord attachment links, which
expire. For images that never break, save the files next to this script as
banner.png and wolf.gif. The bot uses those local files automatically
whenever they exist, and falls back to the links otherwise.

Member commands:
  $getnumber            claim (or re-check) your own number
  /mynumber             show your number
  /driver @member       look up someone's number
  /whohas 042           find who owns a number

  /drivers              staff run it, everyone sees the result: every driver
                        in numerical order, with nickname, Discord ID, date
                        got, time held and roles

Locked commands (see the PERMISSIONS section near the top of this file):
  $getnumber            only works in one category, and only for members
                        with one of the GETNUMBER_ROLE_IDS roles
  $release 042          only the RELEASE_ROLE_IDS roles can use it
  /release 042         same thing as a slash command
  /wipe                 only the WIPE_ROLE_IDS roles can use it; removes ALL
                        numbers (asks for confirmation first)
  /backfill             only the BACKFILL_ROLE_IDS roles can use it, and it
                        only numbers members with one of the
                        GETNUMBER_ROLE_IDS roles, in ROLE_PRIORITY order
                        (then the main role), each group by join date
"""

import os
import re
import sqlite3
import time

import discord
from discord import app_commands
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

MAX_NUMBER = 999
NICK_FORMAT = "[{num}] {name}"      # change the look of the nickname here
PREFIX_RE = re.compile(r"^\[\d{3}\]\s*")
DB_PATH = "drivers.db"

# ---------- WELCOME MESSAGE ----------
WELCOME_CHANNEL_ID = 1566757691303530516

WELCOME_TITLE = "Welcome to A&T Transport LTD!"
WELCOME_TEXT = """We're really glad to have you here. Whether you're a driver, a dispatcher, a customer, or just curious about what we do, you're in the right place.

**Here's how to get started:**

📜 Read the rules in <#1493669498388222002> so we all keep this a friendly and professional space.

✅ You must go to <#1573800671512236072> to verify.

💬 Say hello in <#1566900514883375217> and tell us a bit about yourself.

**What you'll find here:**

- Company news and announcements
- Route and schedule updates
- Team chat and community discussion
- Support and answers to your questions

Please be respectful, keep things on topic, and help each other out. We're a team, on and off the road. 🚛

Enjoy your stay, and welcome aboard!

*The A&T Transport LTD Management Team*"""

# Only shown on the channel message (where the buttons are), not in DMs.
REACT_TEXT = """React to this message to get your role!
👋 = Visitor
🚛 = Want to Join A&T
📷 = Media Fans"""

# The three clickable buttons: (label, role id, emoji, custom id).
# Don't change the custom ids, or buttons on older messages stop working.
ROLE_BUTTONS = [
    ("Visiting",     1565388942508036136, "👋", "role_visiting"),
    ("Join the VTC", 1565389173727301774, "🚛", "role_join_vtc"),
    ("Media Pings",  1566763046297407549, "📷", "role_media_pings"),
]
# True = clicking a button you already used takes the role away again.
# False = clicking again just tells you that you already have it.
ROLE_BUTTONS_TOGGLE = True
# False = the "Role added" reply is only visible to the person who clicked
# (otherwise the welcome channel fills up with confirmations).
ROLE_REPLY_PUBLIC = False


# ---------- PERMISSIONS (edit these IDs to change who can do what) ----------
# $getnumber only works in channels inside this category.
GETNUMBER_CATEGORY_ID = 1553650713874407504

# Role hierarchy used by /backfill, highest priority first. Members whose
# top role is the FIRST one get the lowest numbers, then the second role, and
# so on. Inside each role, people are numbered by join date (oldest first).
ROLE_PRIORITY = [
    1493662151473369280,
    1494054587055870012,
    1573498775111204954,
    1542938047455432734,
    1542938437374705685,
    1565177299949723749,
    1553651916280561735,
    1542938295510630512,
]

# The main member role. /backfill numbers these members LAST, by join date
# (anyone who also holds a role from ROLE_PRIORITY is placed by that role).
MAIN_ROLE_ID = 1562499224178794546

# Members need at least one of these roles to be given a number at all
# (by $getnumber or /backfill) and to have their nickname renamed.
GETNUMBER_ROLE_IDS = set(ROLE_PRIORITY) | {MAIN_ROLE_ID}

# Roles allowed to run /backfill.
BACKFILL_ROLE_IDS = {
    1493662151473369280,
    1494054587055870012,
    1542938437374705685,
}

# Roles allowed to run /wipe (erases every number). Defaults to the same
# roles as /backfill. Replace with your own set of IDs to change it.
WIPE_ROLE_IDS = set(BACKFILL_ROLE_IDS)

# Roles allowed to run /release and $release.
RELEASE_ROLE_IDS = {
    1493662151473369280,
    1494054587055870012,
    1542938437374705685,
    1553651916280561735,
    1573498775111204954,
    1542938047455432734,
}

# Roles allowed to run /drivers. Defaults to the same roles as /release.
DRIVERS_ROLE_IDS = set(RELEASE_ROLE_IDS)
# /drivers shows which of these roles each driver has.
TRACKED_ROLE_IDS = [
    1566847798714826773,
    1559538231542808648,
    1575105938308857927,
]

# If True, server Administrators can use the locked commands even without
# one of the roles above. False = only the roles listed above can.
ADMINS_BYPASS = False

# ---------- embed images ----------
BANNER_URL = "https://media.discordapp.net/attachments/1562933497310744597/1573495129275375636/2377d219-0033-414c-a8df-15cc2837c55f.png?ex=6b077d08&is=6b062b88&hm=ffa3956a3fa99218bf364d86d76ca1323962374ef8616b64b46740ff536c7661&format=webp&quality=lossless&width=1024&height=320"
GIF_URL = "https://cdn.discordapp.com/attachments/1562933497310744597/1573495210766368778/Continuous_cinematic_shot__the_exact_blue_aurora_wolf_Viking.gif?ex=6b077d1c&is=6b062b9c&hm=66ea8c49ed4c37f7ec8b4a0ca4448460a06978e9722ba0c038effe4c52368f41&"

BANNER_FILE = "banner.png"   # optional local copy (never expires)
GIF_FILE = "wolf.gif"        # optional local copy (never expires)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _pick_asset(filename: str, fallback_url: str):
    """Use a local file if it exists, otherwise the web link."""
    path = os.path.join(BASE_DIR, filename)
    if os.path.isfile(path):
        return f"attachment://{filename}", path
    return fallback_url, None


BANNER_SRC, BANNER_PATH = _pick_asset(BANNER_FILE, BANNER_URL)
GIF_SRC, GIF_PATH = _pick_asset(GIF_FILE, GIF_URL)

# ---------- embed styling (edit these to restyle every message) ----------
COLOR_OK = discord.Color.green()
COLOR_INFO = discord.Color.blurple()
COLOR_ERR = discord.Color.red()
FOOTER_TEXT = "Driver Numbers"


def make_embed(title: str, description: str, color=COLOR_INFO) -> discord.Embed:
    e = discord.Embed(title=title, description=description, color=color)
    e.set_image(url=BANNER_SRC)
    e.set_footer(text=FOOTER_TEXT)
    return e


def number_embed(member: discord.abc.User, number: int, title: str, color=COLOR_INFO) -> discord.Embed:
    e = make_embed(title, f"{member.mention}\n# {number:03d}", color)
    e.set_author(name=member.display_name, icon_url=member.display_avatar.url)
    e.set_thumbnail(url=GIF_SRC)
    return e


def kw(e: discord.Embed) -> dict:
    """Send arguments for an embed, attaching local image files if in use."""
    files = []
    if BANNER_PATH and e.image and e.image.url == BANNER_SRC:
        files.append(discord.File(BANNER_PATH, filename=BANNER_FILE))
    if GIF_PATH and e.thumbnail and e.thumbnail.url == GIF_SRC:
        files.append(discord.File(GIF_PATH, filename=GIF_FILE))
    out = {"embed": e}
    if files:
        out["files"] = files
    return out


# ---------- database ----------
db = sqlite3.connect(DB_PATH)
db.execute(
    """CREATE TABLE IF NOT EXISTS drivers (
        guild_id INTEGER NOT NULL,
        user_id  INTEGER NOT NULL,
        number   INTEGER NOT NULL,
        PRIMARY KEY (guild_id, user_id),
        UNIQUE (guild_id, number)
    )"""
)
# Older databases don't record when a number was given out. Add that column.
_cols = [r[1] for r in db.execute("PRAGMA table_info(drivers)")]
if "assigned_at" not in _cols:
    db.execute("ALTER TABLE drivers ADD COLUMN assigned_at INTEGER")
db.commit()


def get_number(guild_id: int, user_id: int):
    row = db.execute(
        "SELECT number FROM drivers WHERE guild_id=? AND user_id=?",
        (guild_id, user_id),
    ).fetchone()
    return row[0] if row else None


def get_owner(guild_id: int, number: int):
    row = db.execute(
        "SELECT user_id FROM drivers WHERE guild_id=? AND number=?",
        (guild_id, number),
    ).fetchone()
    return row[0] if row else None


def assign_number(guild_id: int, user_id: int):
    """Return the member's number, assigning the lowest free one if needed.
    Released numbers are reused first. Returns None if all 999 are taken."""
    existing = get_number(guild_id, user_id)
    if existing is not None:
        return existing

    taken = {
        r[0]
        for r in db.execute("SELECT number FROM drivers WHERE guild_id=?", (guild_id,))
    }
    free = next((n for n in range(1, MAX_NUMBER + 1) if n not in taken), None)
    if free is None:
        return None

    db.execute(
        "INSERT INTO drivers (guild_id, user_id, number, assigned_at) VALUES (?, ?, ?, ?)",
        (guild_id, user_id, free, int(time.time())),
    )
    db.commit()
    return free


def free_number(guild_id: int, number: int):
    """Release a number. Returns the previous owner's user id, or None."""
    owner = get_owner(guild_id, number)
    if owner is None:
        return None
    db.execute(
        "DELETE FROM drivers WHERE guild_id=? AND number=?", (guild_id, number)
    )
    db.commit()
    return owner


def list_drivers(guild_id: int) -> list:
    """All (number, user_id, assigned_at) rows in numerical order."""
    return db.execute(
        "SELECT number, user_id, assigned_at FROM drivers WHERE guild_id=? ORDER BY number",
        (guild_id,),
    ).fetchall()


def count_numbers(guild_id: int) -> int:
    return db.execute(
        "SELECT COUNT(*) FROM drivers WHERE guild_id=?", (guild_id,)
    ).fetchone()[0]


def wipe_numbers(guild_id: int) -> list:
    """Delete every number in the server. Returns the user ids that had one."""
    user_ids = [
        r[0]
        for r in db.execute("SELECT user_id FROM drivers WHERE guild_id=?", (guild_id,))
    ]
    db.execute("DELETE FROM drivers WHERE guild_id=?", (guild_id,))
    db.commit()
    return user_ids


# ---------- permission checks ----------
def has_any_role(member: discord.Member, role_ids: set) -> bool:
    if ADMINS_BYPASS and member.guild_permissions.administrator:
        return True
    return any(role.id in role_ids for role in member.roles)


def can_release(member: discord.Member) -> bool:
    return has_any_role(member, RELEASE_ROLE_IDS)


def can_backfill(member: discord.Member) -> bool:
    return has_any_role(member, BACKFILL_ROLE_IDS)


def can_wipe(member: discord.Member) -> bool:
    return has_any_role(member, WIPE_ROLE_IDS)


def role_tier(member: discord.Member) -> int:
    """0 = holds the first role in ROLE_PRIORITY, 1 = second, ... The last
    tier (len(ROLE_PRIORITY)) is for members with only the main role."""
    ids = {r.id for r in member.roles}
    for i, rid in enumerate(ROLE_PRIORITY):
        if rid in ids:
            return i
    return len(ROLE_PRIORITY)


def can_get_number(member: discord.Member) -> bool:
    return any(role.id in GETNUMBER_ROLE_IDS for role in member.roles)


def in_allowed_category(channel) -> bool:
    """True if the channel (or the parent channel of a thread) is inside
    the category that $getnumber is restricted to."""
    if isinstance(channel, discord.Thread):
        channel = channel.parent
    return channel is not None and getattr(channel, "category_id", None) == GETNUMBER_CATEGORY_ID


# ---------- nickname helpers ----------
async def apply_nickname(member: discord.Member, number: int) -> bool:
    base = PREFIX_RE.sub("", member.nick or member.global_name or member.name)
    tag = NICK_FORMAT.format(num=f"{number:03d}", name="")
    nick = (tag + base)[:32]  # Discord nickname limit is 32 chars
    if member.nick == nick:
        return True
    try:
        await member.edit(nick=nick, reason="Driver number assignment")
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def clear_member_nickname(member: discord.Member):
    """Remove the [000] prefix from one member's nickname.
    Returns True if cleared, False if it failed, None if nothing to do."""
    if not member.nick or not PREFIX_RE.match(member.nick):
        return None
    stripped = PREFIX_RE.sub("", member.nick)
    try:
        await member.edit(nick=stripped or None, reason="Driver number released")
        return True
    except (discord.Forbidden, discord.HTTPException):
        return False


async def clear_nickname(guild: discord.Guild, user_id: int):
    """Same as clear_member_nickname, but looks the member up by user id."""
    member = guild.get_member(user_id)
    if member is None:
        return None
    return await clear_member_nickname(member)


async def do_release(guild: discord.Guild, actor: discord.Member, number: int) -> discord.Embed:
    """Shared logic for $release and /release. Returns the reply embed."""
    if not can_release(actor):
        return make_embed("No permission", "You don't have permission to release driver numbers.", COLOR_ERR)
    if not 1 <= number <= MAX_NUMBER:
        return make_embed("Invalid number", f"Pick a number between 001 and {MAX_NUMBER}.", COLOR_ERR)
    owner = free_number(guild.id, number)
    if owner is None:
        return make_embed("Already available", f"Number **{number:03d}** is already available.", COLOR_INFO)
    await clear_nickname(guild, owner)
    return make_embed(
        "Number released",
        f"Number **{number:03d}** (was <@{owner}>) is now available again.",
        COLOR_OK,
    )


# ---------- bot ----------
intents = discord.Intents.default()
intents.members = True          # privileged intent: enable in the Developer Portal
intents.message_content = True  # privileged intent: needed to read "$getnumber"


class DriverBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.synced = False

    async def setup_hook(self):
        # Keeps the welcome buttons working after the bot restarts.
        self.add_view(RoleButtonsView())


bot = DriverBot()


async def handle_role_click(interaction: discord.Interaction, role_id: int):
    member = interaction.user
    role = interaction.guild.get_role(role_id)
    if role is None:
        e = make_embed("Role not found", "That role no longer exists. Please tell a staff member.", COLOR_ERR)
    else:
        try:
            if role in member.roles and ROLE_BUTTONS_TOGGLE:
                await member.remove_roles(role, reason="Welcome role button")
                e = make_embed("Role removed", f"You no longer have {role.mention}.", COLOR_INFO)
            elif role in member.roles:
                e = make_embed("Already yours", f"You already have {role.mention}.", COLOR_INFO)
            else:
                await member.add_roles(role, reason="Welcome role button")
                e = make_embed("Role added", f"You now have {role.mention}.", COLOR_OK)
        except discord.Forbidden:
            e = make_embed(
                "Couldn't change your role",
                "I don't have permission to give that role. A staff member needs to move my role above it "
                "and give me Manage Roles.",
                COLOR_ERR,
            )
    await interaction.response.send_message(**kw(e), ephemeral=not ROLE_REPLY_PUBLIC)


class RoleButtonsView(discord.ui.View):
    """The three role buttons under the welcome message. Persistent."""

    def __init__(self):
        super().__init__(timeout=None)
        for label, role_id, emoji, custom_id in ROLE_BUTTONS:
            button = discord.ui.Button(
                label=label,
                emoji=emoji,
                style=discord.ButtonStyle.primary,
                custom_id=custom_id,
            )
            button.callback = self._make_callback(role_id)
            self.add_item(button)

    @staticmethod
    def _make_callback(role_id: int):
        async def callback(interaction: discord.Interaction):
            await handle_role_click(interaction, role_id)
        return callback


async def send_welcome(member: discord.Member) -> bool:
    """Post the welcome + role-picker message for a member. Returns success."""
    channel = bot.get_channel(WELCOME_CHANNEL_ID)
    if channel is None or channel.guild.id != member.guild.id:
        return False

    # Which member number they are = how many people are in the server now.
    position = member.guild.member_count or len(member.guild.members)
    welcome = make_embed(
        WELCOME_TITLE,
        f"You are member **#{position:,}** in the server!\n\n{WELCOME_TEXT}\n\n{REACT_TEXT}",
        COLOR_OK,
    )

    send_args = {
        "content": f"Welcome {member.mention}!",
        "embeds": [welcome],
        "view": RoleButtonsView(),
        "allowed_mentions": discord.AllowedMentions(users=[member]),
    }
    files = kw(welcome).get("files")
    if files:
        send_args["files"] = files
    try:
        await channel.send(**send_args)
        return True
    except discord.HTTPException as e:
        print(f"Couldn't post the welcome message: {e}")
        return False


async def send_welcome_dm(member: discord.Member) -> bool:
    """Send the welcome message to the member's DMs. Returns success.
    Fails quietly if they have DMs from server members turned off."""
    guild = member.guild
    position = guild.member_count or len(guild.members)

    # Channel mentions don't always show in DMs, so use full links instead.
    text = re.sub(
        r"<#(\d+)>",
        lambda m: f"https://discord.com/channels/{guild.id}/{m.group(1)}",
        WELCOME_TEXT,
    )
    pointer = (
        "To pick your roles, use the buttons on the welcome message here: "
        f"https://discord.com/channels/{guild.id}/{WELCOME_CHANNEL_ID}"
    )
    e = make_embed(
        WELCOME_TITLE,
        f"You are member **#{position:,}** in the server!\n\n{text}\n\n{pointer}",
        COLOR_OK,
    )
    try:
        await member.send(content=f"Welcome to **{guild.name}**!", **kw(e))
        return True
    except (discord.Forbidden, discord.HTTPException):
        print(f"Couldn't DM {member} (their DMs are probably closed).")
        return False


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    await send_welcome(member)
    await send_welcome_dm(member)


@bot.tree.command(name="testwelcome", description="Post a test copy of the welcome message")
@app_commands.guild_only()
async def testwelcome(interaction: discord.Interaction):
    if not can_backfill(interaction.user):
        await interaction.response.send_message(
            **kw(make_embed("No permission", "You don't have permission to use `/testwelcome`.", COLOR_ERR)),
        )
        return
    await interaction.response.defer()
    channel_ok = await send_welcome(interaction.user)
    dm_ok = await send_welcome_dm(interaction.user)

    e = make_embed(
        "Test welcome sent" if (channel_ok or dm_ok) else "Couldn't send it",
        "Here's what happened:",
        COLOR_OK if (channel_ok and dm_ok) else COLOR_ERR,
    )
    e.add_field(
        name="Welcome channel",
        value=f"Posted in <#{WELCOME_CHANNEL_ID}>." if channel_ok
        else "Failed. I can't find the channel, or I'm missing permission to post there.",
        inline=False,
    )
    e.add_field(
        name="Direct message",
        value="Sent. Check your DMs." if dm_ok
        else "Failed. Your DMs from server members are probably turned off.",
        inline=False,
    )
    await interaction.followup.send(**kw(e))


async def sync_guild(guild: discord.Guild):
    """Register the slash commands in one server (shows up instantly)."""
    try:
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        print(f"[{guild.name}] Synced {len(synced)} slash commands.")
    except discord.HTTPException as e:
        print(f"[{guild.name}] Slash command sync FAILED: {e}")


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} ({bot.user.id})")
    print("Banner source:", "local file" if BANNER_PATH else "web link")
    print("GIF source:   ", "local file" if GIF_PATH else "web link")
    if bot.synced:
        return
    bot.synced = True
    for guild in bot.guilds:
        await sync_guild(guild)
    # Remove old global copies so commands don't show up twice.
    try:
        await bot.http.bulk_upsert_global_commands(bot.application_id, [])
    except discord.HTTPException as e:
        print(f"Global cleanup failed: {e}")


@bot.event
async def on_guild_join(guild: discord.Guild):
    await sync_guild(guild)


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    print(f"Command error in /{interaction.command.name if interaction.command else '?'}: {error!r}")
    e = make_embed(
        "Something went wrong",
        "That command failed. Check the bot's terminal for details.",
        COLOR_ERR,
    )
    if interaction.response.is_done():
        await interaction.followup.send(**kw(e))
    else:
        await interaction.response.send_message(**kw(e))


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or message.guild is None:
        return
    parts = message.content.strip().split()
    if not parts:
        return
    cmd = parts[0].lower()

    # ---- $getnumber ----
    if cmd == "$getnumber":
        if not in_allowed_category(message.channel):
            await message.reply(
                **kw(make_embed("Wrong channel", "You can't use `$getnumber` in this channel.", COLOR_ERR)),
                mention_author=False,
                delete_after=10,
            )
            return
        member = message.author
        if not can_get_number(member):
            await message.reply(
                **kw(make_embed(
                    "Not eligible",
                    f"Only members with <@&{MAIN_ROLE_ID}> can get a driver number.",
                    COLOR_ERR,
                )),
                mention_author=False,
            )
            return
        existing = get_number(message.guild.id, member.id)
        if existing is not None:
            await apply_nickname(member, existing)  # re-apply if they changed it
            await message.reply(
                **kw(number_embed(member, existing, "You already have a number")),
                mention_author=False,
            )
            return

        number = assign_number(message.guild.id, member.id)
        if number is None:
            await message.reply(
                **kw(make_embed("No numbers left", f"Sorry, all {MAX_NUMBER} driver numbers are taken.", COLOR_ERR)),
                mention_author=False,
            )
            return

        renamed = await apply_nickname(member, number)
        e = number_embed(member, number, "Driver number assigned", COLOR_OK)
        if not renamed:
            e.add_field(
                name="Heads up",
                value="I couldn't change your nickname, but the number is yours.",
                inline=False,
            )
        await message.reply(**kw(e), mention_author=False)

    # ---- $release 042 ----
    elif cmd == "$release":
        if len(parts) < 2 or not parts[1].isdigit():
            await message.reply(
                **kw(make_embed("Usage", "Type `$release 042` to free up a number.", COLOR_ERR)),
                mention_author=False,
            )
            return
        e = await do_release(message.guild, message.author, int(parts[1]))
        await message.reply(**kw(e), mention_author=False)


# ---------- slash commands ----------
@bot.tree.command(name="release", description="Make a driver number available again")
@app_commands.guild_only()
@app_commands.describe(number="The number to free up (1-999)")
async def release(interaction: discord.Interaction, number: app_commands.Range[int, 1, 999]):
    e = await do_release(interaction.guild, interaction.user, number)
    await interaction.response.send_message(**kw(e))


def human_duration(seconds: int) -> str:
    """Turn a number of seconds into something like '3d 4h'."""
    seconds = max(0, int(seconds))
    units = [("y", 31536000), ("mo", 2592000), ("d", 86400), ("h", 3600), ("m", 60)]
    parts = []
    for label, size in units:
        if seconds >= size:
            value, seconds = divmod(seconds, size)
            parts.append(f"{value}{label}")
        if len(parts) == 2:
            break
    return " ".join(parts) or "under 1m"


def driver_entry(guild: discord.Guild, number: int, user_id: int, assigned_at, now: int) -> str:
    member = guild.get_member(user_id)
    name = discord.utils.escape_markdown(member.display_name) if member else "Left the server"
    line1 = f"**#{number:03d}** \u2022 {name} \u2022 `{user_id}`"
    if assigned_at:
        line2 = f"Got: <t:{assigned_at}:d> \u2022 Held: {human_duration(now - assigned_at)}"
    else:
        line2 = "Got: Unknown \u2022 Held: Unknown"
    if member:
        held = [f"<@&{rid}>" for rid in TRACKED_ROLE_IDS if any(r.id == rid for r in member.roles)]
        line3 = "Roles: " + (" ".join(held) if held else "None")
    else:
        line3 = "Roles: \u2014"
    return f"{line1}\n{line2}\n{line3}"


class DriversView(discord.ui.View):
    """Paged list of drivers. Only the person who ran /drivers can use it."""

    PER_PAGE = 10

    def __init__(self, author_id: int, entries: list, interaction: discord.Interaction):
        super().__init__(timeout=180)
        self.author_id = author_id
        self.entries = entries
        self.original = interaction
        self.page = 0
        self.pages = max(1, -(-len(entries) // self.PER_PAGE))
        self._sync_buttons()

    def embed(self) -> discord.Embed:
        start = self.page * self.PER_PAGE
        chunk = self.entries[start:start + self.PER_PAGE]
        e = make_embed("Drivers", "\n\n".join(chunk), COLOR_INFO)
        e.set_footer(text=f"{FOOTER_TEXT} \u2022 Page {self.page + 1}/{self.pages} \u2022 {len(self.entries)} drivers")
        return e

    def _sync_buttons(self):
        at_start = self.page == 0
        at_end = self.page >= self.pages - 1
        self.first.disabled = self.prev.disabled = at_start
        self.next.disabled = self.last.disabled = at_end

    async def _show(self, interaction: discord.Interaction):
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who ran the command can use these buttons."
            )
            return False
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        try:
            await self.original.edit_original_response(view=self)
        except discord.HTTPException:
            pass

    @discord.ui.button(label="\u23ee", style=discord.ButtonStyle.secondary)
    async def first(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = 0
        await self._show(interaction)

    @discord.ui.button(label="\u25c0", style=discord.ButtonStyle.primary)
    async def prev(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        await self._show(interaction)

    @discord.ui.button(label="\u25b6", style=discord.ButtonStyle.primary)
    async def next(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.pages - 1, self.page + 1)
        await self._show(interaction)

    @discord.ui.button(label="\u23ed", style=discord.ButtonStyle.secondary)
    async def last(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = self.pages - 1
        await self._show(interaction)


@bot.tree.command(name="drivers", description="List all drivers in numerical order")
@app_commands.guild_only()
async def drivers(interaction: discord.Interaction):
    if not has_any_role(interaction.user, DRIVERS_ROLE_IDS):
        await interaction.response.send_message(
            **kw(make_embed("No permission", "You don't have permission to use `/drivers`.", COLOR_ERR)),
        )
        return

    rows = list_drivers(interaction.guild.id)
    if not rows:
        await interaction.response.send_message(
            **kw(make_embed("No drivers yet", "Nobody has a driver number yet.", COLOR_INFO)),
        )
        return

    now = int(time.time())
    entries = [driver_entry(interaction.guild, n, uid, at, now) for n, uid, at in rows]
    view = DriversView(interaction.user.id, entries, interaction)
    kwargs = kw(view.embed())
    if view.pages > 1:
        kwargs["view"] = view
    await interaction.response.send_message(**kwargs)


class WipeConfirmView(discord.ui.View):
    """Confirm / Cancel buttons for /wipe. Only the person who ran it can click."""

    def __init__(self, author_id: int, guild: discord.Guild, interaction: discord.Interaction):
        super().__init__(timeout=30)
        self.author_id = author_id
        self.guild = guild
        self.original = interaction

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "Only the person who ran the command can use these buttons."
            )
            return False
        return True

    async def on_timeout(self):
        try:
            await self.original.edit_original_response(
                embed=make_embed("Wipe cancelled", "No confirmation received, so nothing was changed.", COLOR_INFO),
                view=None,
            )
        except discord.HTTPException:
            pass

    @discord.ui.button(label="Yes, wipe all numbers", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            embed=make_embed("Wiping...", "Removing all numbers and the number tag from every nickname. This can take a while.", COLOR_INFO),
            view=None,
        )
        user_ids = wipe_numbers(self.guild.id)
        cleared = failed = 0
        # Go through EVERY member, so any [000] tag is removed from every
        # nickname, even ones the database didn't know about.
        async for member in self.guild.fetch_members(limit=None):
            result = await clear_member_nickname(member)
            if result is True:
                cleared += 1
            elif result is False:
                failed += 1

        e = make_embed("All numbers wiped", "Every driver number is available again.", COLOR_OK)
        e.add_field(name="Numbers removed", value=str(len(user_ids)))
        e.add_field(name="Nicknames cleaned", value=str(cleared))
        e.add_field(name="Couldn't rename", value=str(failed))
        await interaction.edit_original_response(embed=e)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            embed=make_embed("Wipe cancelled", "Nothing was changed.", COLOR_INFO),
            view=None,
        )


@bot.tree.command(name="wipe", description="Remove ALL driver numbers (asks for confirmation)")
@app_commands.guild_only()
async def wipe(interaction: discord.Interaction):
    if not can_wipe(interaction.user):
        await interaction.response.send_message(
            **kw(make_embed("No permission", "You don't have permission to use `/wipe`.", COLOR_ERR)),
        )
        return

    total = count_numbers(interaction.guild.id)
    if total == 0:
        await interaction.response.send_message(
            **kw(make_embed("Nothing to wipe", "No driver numbers are currently assigned.", COLOR_INFO)),
        )
        return

    e = make_embed(
        "Wipe ALL driver numbers?",
        f"This will remove **{total}** active numbers and strip the `[000]` tag from "
        f"**every** nickname in the server. **This cannot be undone.**",
        COLOR_ERR,
    )
    view = WipeConfirmView(interaction.user.id, interaction.guild, interaction)
    await interaction.response.send_message(**kw(e), view=view)


@bot.tree.command(name="backfill", description="Number all existing members by join date")
@app_commands.guild_only()
async def backfill(interaction: discord.Interaction):
    if not can_backfill(interaction.user):
        await interaction.response.send_message(
            **kw(make_embed("No permission", "You don't have permission to use `/backfill`.", COLOR_ERR)),
        )
        return
    await interaction.response.defer()
    guild = interaction.guild

    members = [
        m async for m in guild.fetch_members(limit=None)
        if not m.bot and can_get_number(m)
    ]
    members.sort(key=lambda m: (role_tier(m), m.joined_at is None, m.joined_at))

    assigned = renamed = failed = 0
    for m in members:
        had = get_number(guild.id, m.id) is not None
        number = assign_number(guild.id, m.id)
        if number is None:
            break
        if not had:
            assigned += 1
        if await apply_nickname(m, number):
            renamed += 1
        else:
            failed += 1

    e = make_embed("Backfill complete", "Eligible members were numbered by role hierarchy, then by join date.", COLOR_OK)
    e.add_field(name="Newly numbered", value=str(assigned))
    e.add_field(name="Nicknames set", value=str(renamed))
    e.add_field(name="Couldn't rename", value=str(failed))
    await interaction.followup.send(**kw(e))


@bot.tree.command(name="mynumber", description="Show your driver number")
@app_commands.guild_only()
async def mynumber(interaction: discord.Interaction):
    n = get_number(interaction.guild.id, interaction.user.id)
    if n:
        e = number_embed(interaction.user, n, "Your driver number")
    else:
        e = make_embed("No number yet", "You don't have a number yet. Type `$getnumber` to claim one.", COLOR_INFO)
    await interaction.response.send_message(**kw(e))


@bot.tree.command(name="driver", description="Look up a member's driver number")
@app_commands.guild_only()
async def driver(interaction: discord.Interaction, member: discord.Member):
    n = get_number(interaction.guild.id, member.id)
    if n:
        e = number_embed(member, n, "Driver number")
    else:
        e = make_embed("No number yet", f"{member.mention} doesn't have a number yet.", COLOR_INFO)
    await interaction.response.send_message(**kw(e))


@bot.tree.command(name="whohas", description="Find out who owns a driver number")
@app_commands.guild_only()
@app_commands.describe(number="A number from 1 to 999")
async def whohas(interaction: discord.Interaction, number: app_commands.Range[int, 1, 999]):
    uid = get_owner(interaction.guild.id, number)
    if uid:
        e = make_embed(f"Number {number:03d}", f"Owned by <@{uid}>.", COLOR_INFO)
    else:
        e = make_embed(f"Number {number:03d}", "This number is free.", COLOR_OK)
    await interaction.response.send_message(**kw(e))


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Missing DISCORD_TOKEN in your .env file.")
    bot.run(TOKEN)
