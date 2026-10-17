"""Read-only A&T Trucky membership verification, based on observed roster JSON."""
import os
import aiohttp

COMPANY_ID = 49041
BASE_URL = f"https://e.truckyapp.com/api/v1/company/{COMPANY_ID}/members"
MAX_PAGES = 100

class MembershipUnavailable(Exception):
    pass

async def verify_member(account_id):
    """Return canonical member name or None. Raise if any page cannot be trusted."""
    if not isinstance(account_id, str) or not account_id.isdecimal() or int(account_id) <= 0:
        raise ValueError("Trucky account ID must be a positive numeric ID")
    token = os.getenv("TRUCKY_API_TOKEN", "").strip()
    if not token:
        raise MembershipUnavailable("TRUCKY_API_TOKEN not configured")
    headers = {"x-access-token": token, "Accept": "application/json",
               "User-Agent": "A&T Transport LTD Command Bot"}
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            last_page = None
            matches = []
            page = 1
            while page <= MAX_PAGES:
                params = {"page": page, "perPage": 100, "inactive": "false"}
                async with session.get(BASE_URL, params=params, headers=headers,
                                       allow_redirects=False) as response:
                    if response.status != 200:
                        raise MembershipUnavailable(f"Trucky API HTTP {response.status}")
                    payload = await response.json(content_type=None)
                current, last, members = validate_roster_page(payload, page, last_page)
                last_page = last
                for member in members:
                    if member["id"] == int(account_id):
                        matches.append(member["name"])
                if page == last:
                    if len(matches) > 1:
                        raise MembershipUnavailable("Duplicate Trucky member ID")
                    return matches[0] if matches else None
                page += 1
    except (aiohttp.ClientError, TimeoutError, ValueError) as error:
        if isinstance(error, ValueError) and "account ID" in str(error):
            raise
        raise MembershipUnavailable("Trucky membership lookup failed") from error
    raise MembershipUnavailable("Trucky roster exceeds supported page limit")


def validate_roster_page(payload, page, previous_last_page=None):
    """Validate the observed Trucky company-members pagination contract."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise MembershipUnavailable("Invalid Trucky roster format")
    current, last = payload.get("current_page"), payload.get("last_page")
    if type(current) is not int or current != page or type(last) is not int or not (page <= last <= MAX_PAGES):
        raise MembershipUnavailable("Invalid Trucky roster pagination")
    if previous_last_page is not None and last != previous_last_page:
        raise MembershipUnavailable("Trucky roster changed during pagination")
    for member in payload["data"]:
        if not isinstance(member, dict) or type(member.get("id")) is not int or type(member.get("company_id")) is not int or not isinstance(member.get("name"), str) or not member["name"].strip():
            raise MembershipUnavailable("Invalid Trucky member record")
        if member["company_id"] != COMPANY_ID:
            raise MembershipUnavailable("Unexpected Trucky company in roster")
    return current, last, payload["data"]


async def inspect_member_identity_fields(account_id):
    """Read-only diagnostic: identify available roster fields, never expose values.

    A Steam field must NOT be trusted for auto-approval until the Trucky API
    contract and its relationship to the authenticated user are validated.
    """
    if not isinstance(account_id, str) or not account_id.isdecimal() or int(account_id) <= 0:
        raise ValueError("Trucky account ID must be numeric")
    token = os.getenv("TRUCKY_API_TOKEN", "").strip()
    if not token:
        raise MembershipUnavailable("Trucky API token not configured")
    timeout = aiohttp.ClientTimeout(total=15)
    headers = {"x-access-token": token, "Accept": "application/json"}
    last_page = None
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for page in range(1, MAX_PAGES + 1):
            async with session.get(
                BASE_URL, params={"page": page, "perPage": 100, "inactive": "false"},
                headers=headers, allow_redirects=False
            ) as response:
                if response.status != 200:
                    raise MembershipUnavailable(f"Trucky roster HTTP {response.status}")
                payload = await response.json(content_type=None)
            _, last, members = validate_roster_page(payload, page, last_page)
            last_page = last
            matching = [m for m in members if m["id"] == int(account_id)]
            if len(matching) > 1:
                raise MembershipUnavailable("Duplicate member ID")
            if matching:
                # Only return field names/types, never the account data.
                return {
                    "found": True,
                    "field_types": {
                        key: type(value).__name__
                        for key, value in matching[0].items()
                    },
                    "steam_id_verified": False,
                }
            if page == last:
                return {"found": False, "field_types": {}, "steam_id_verified": False}
    raise MembershipUnavailable("Trucky roster pagination exceeded limit")
