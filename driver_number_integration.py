"""Connect the unchanged driver_bot module to the existing command client."""
import os
from pathlib import Path
import sqlite3

async def install(client, guild_id):
    if os.getenv("AT_V2_ISOLATED_STAGING") == "YES":
        return
    if getattr(client, "_driver_numbers_installed", False):
        return
    mount = Path("/driver-number-data")
    if not os.path.ismount(mount):
        raise RuntimeError("Driver number persistent volume is not mounted")
    import driver_bot as source
    commands = source.bot.tree.get_commands()
    for command in commands:
        if client.tree.get_command(command.name):
            raise RuntimeError(f"Driver number command conflict: {command.name}")
    source.db.close()
    source.DB_PATH = str(mount / "drivers.db")
    # Import creates the original schema; initialize the persistent copy using
    # the same schema and migration, without changing the supplied source.
    source.db = sqlite3.connect(source.DB_PATH)
    source.db.execute("""CREATE TABLE IF NOT EXISTS drivers (
        guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
        number INTEGER NOT NULL, PRIMARY KEY (guild_id, user_id),
        UNIQUE (guild_id, number))""")
    if "assigned_at" not in [r[1] for r in source.db.execute("PRAGMA table_info(drivers)")]:
        source.db.execute("ALTER TABLE drivers ADD COLUMN assigned_at INTEGER")
    source.db.commit()
    source.bot = client
    async def on_message(message):
        if message.guild and message.guild.id == guild_id:
            await source.on_message(message)
    async def on_member_join(member):
        if member.guild.id == guild_id:
            await source.on_member_join(member)
    for command in commands:
        client.tree.add_command(command)
    client.add_view(source.RoleButtonsView())
    client.add_listener(on_message, "on_message")
    client.add_listener(on_member_join, "on_member_join")
    client._driver_numbers_installed = True
    print("DRIVER NUMBER INTEGRATION READY", flush=True)
