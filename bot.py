import os
import discord
from discord.ext import commands

# --------------------------------------------------
# A&T TRANSPORT LTD DISCORD BOT
# --------------------------------------------------

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)


@bot.event
async def on_ready():
    print("--------------------------------")
    print("A&T Transport LTD Bot")
    print(f"Logged in as: {bot.user}")
    print(f"Bot ID: {bot.user.id}")
    print("--------------------------------")

    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash command(s).")
    except Exception as error:
        print(f"Slash command sync failed: {error}")


@bot.tree.command(
    name="ping",
    description="Check whether the A&T Transport LTD bot is online."
)
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(
        "🚛 **A&T Transport LTD Bot is online!**\n"
        "🌌 Driven Beyond Horizons."
    )


if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN has not been configured."
    )

bot.run(TOKEN)
