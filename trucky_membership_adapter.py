"""Read-only Trucky membership lookup adapter.

Configure TRUCKY_MEMBERS_ENDPOINT only after verifying the documented roster
endpoint and its JSON response contract. No guessed endpoints or approvals.
"""
import os
from urllib.parse import urlsplit

import aiohttp


class MembershipUnavailable(Exception):
    pass


async def get_roster_json():
    endpoint = os.getenv("TRUCKY_MEMBERS_ENDPOINT", "").strip()
    token = os.getenv("TRUCKY_API_TOKEN", "").strip()
    if not endpoint or not token:
        raise MembershipUnavailable("Trucky membership lookup not configured")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "e.truckyapp.com"
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise MembershipUnavailable("Invalid Trucky API endpoint")
    headers = {
        "x-access-token": token,
        "Accept": "application/json",
        "User-Agent": "A&T Transport LTD Command Bot",
    }
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(endpoint, headers=headers, allow_redirects=False) as response:
            if response.status != 200:
                raise MembershipUnavailable(f"Trucky API returned HTTP {response.status}")
            return await response.json(content_type=None)


def match_membership(roster, account_id):
    """Fail closed until Trucky roster response fields are verified.

    Never approve from username similarity or unvalidated JSON.
    """
    raise MembershipUnavailable("Trucky roster JSON contract not verified")
