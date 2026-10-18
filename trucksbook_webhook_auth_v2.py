"""Fail-closed source check for TrucksBook Discord webhook events."""


def is_authorized_trucksbook_webhook(webhook_id, *, isolated_staging, configured_id):
    """Authorize the existing TrucksBook Discord webhook in isolated staging.

    Discord supplies ``webhook_id`` as message metadata. In staging, only the
    specifically configured TrucksBook webhook is accepted. Outside staging,
    preserve the bot's established webhook behavior.
    """
    if type(webhook_id) is not int or webhook_id <= 0:
        return False
    if not isolated_staging:
        return True
    if (not isinstance(configured_id, str) or not configured_id.isascii()
            or not configured_id.isdecimal() or len(configured_id) > 20):
        return False
    configured = int(configured_id)
    return configured > 0 and webhook_id == configured


def inspect_staging_trucksbook_delivery(
    *, webhook_id, guild_id, channel_id, configured_webhook_id,
    configured_guild_id, configured_channel_id="",
):
    """Classify routing metadata without retaining or logging raw IDs.

    A staging channel restriction is enforced when configured. The webhook ID
    remains the primary source authentication; the guild must always match.
    An unset channel restriction is reported explicitly so operators can tell
    that channel-level validation has not been configured.
    """
    source_match = is_authorized_trucksbook_webhook(
        webhook_id, isolated_staging=True, configured_id=configured_webhook_id
    )
    guild_match = (
        type(guild_id) is int and guild_id > 0
        and type(configured_guild_id) is int and configured_guild_id > 0
        and guild_id == configured_guild_id
    )
    raw_channel = configured_channel_id.strip() if isinstance(configured_channel_id, str) else ""
    if not raw_channel:
        channel_match = None
        channel_status = "not_configured"
    elif (not raw_channel.isascii() or not raw_channel.isdecimal()
          or len(raw_channel) > 20 or int(raw_channel) <= 0):
        channel_match = False
        channel_status = "invalid_configuration"
    else:
        channel_match = type(channel_id) is int and channel_id == int(raw_channel)
        channel_status = "match" if channel_match else "mismatch"

    return {
        "source_match": source_match,
        "guild_match": guild_match,
        "channel_status": channel_status,
        "allowed": source_match and guild_match and channel_match is not False,
    }
