"""Read-only Trucky Discord embed parser. No mileage or role writes.

Discord webhook messages are not a trusted mileage authority: source checks
and independent delivery verification are required before crediting.
"""
import re
from decimal import Decimal

TRACKER_CHANNEL_ID = 1541473951899852800
JOB_RE = re.compile(r"Job Completed\s*-\s*#(\d+)", re.I)
DISTANCE_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(kilomet(?:er|re)s?|miles?|km|mi)(?=\b|\.)", re.I)


def parse_trucky_embed(embed):
    """Extract visible data for staging review; fail closed on missing fields."""
    if not isinstance(embed, dict):
        return None
    title = str(embed.get("title") or "")
    description = str(embed.get("description") or "")
    fields = embed.get("fields") or []
    if not isinstance(fields, list):
        return None
    match = JOB_RE.search(title)
    if not match or len(JOB_RE.findall(title)) != 1:
        return None
    # The embed layout may change; never guess distance from unrelated numbers.
    # Prefer the dedicated distance field when present; otherwise use the
    # description only. Never add numbers from unrelated embed fields.
    distance_fields = [
        str(f.get("value") or "")
        for f in fields if isinstance(f, dict)
        and "distance" in str(f.get("name") or "").lower()
    ]
    if len(distance_fields) > 1:
        return None
    distance_source = distance_fields[0] if distance_fields else description
    distance_matches = DISTANCE_RE.findall(distance_source)
    if len(distance_matches) != 1:
        return None
    distance, unit = distance_matches[0]
    if Decimal(distance) <= 0:
        return None
    stat_fields = [
        str(f.get("value") or "").strip().lower()
        for f in fields if isinstance(f, dict)
        and any(label in str(f.get("name") or "").lower()
                for label in ("stats type", "statistics"))
    ]
    if len(stat_fields) != 1 or stat_fields[0] not in ("real miles", "race miles"):
        return None
    author = embed.get("author") or {}
    name = str(author.get("name") or "").strip() if isinstance(author, dict) else ""
    if not name:
        return None
    return {
        "provider": "trucky",
        "provider_job_id": match.group(1),
        "display_name_unverified": name,
        "distance": str(Decimal(distance)),
        "distance_unit": "km" if unit.lower().startswith("k") else "mi",
        "mileage_type": "real" if stat_fields[0] == "real miles" else "race",
        "trusted": False,
    }


def inspect_trucky_message(message, *, expected_webhook_id):
    """Return untrusted parsed event only from configured Discord source.

    expected_webhook_id must be supplied from protected server configuration,
    not inferred from a user message or a webhook URL pasted in chat.
    """
    if not isinstance(expected_webhook_id, int) or expected_webhook_id <= 0:
        return None
    if getattr(getattr(message, "channel", None), "id", None) != TRACKER_CHANNEL_ID:
        return None
    if getattr(message, "webhook_id", None) != expected_webhook_id:
        return None
    embeds = getattr(message, "embeds", ())
    if len(embeds) != 1:
        return None
    embed = embeds[0]
    data = embed.to_dict() if hasattr(embed, "to_dict") else embed
    return parse_trucky_embed(data)
