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
    if not isinstance(configured_id, str) or not configured_id.isdecimal():
        return False
    return int(configured_id) > 0 and webhook_id == int(configured_id)
